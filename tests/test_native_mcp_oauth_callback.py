"""Tests for the OAuth callback server and the sign-in pages.

Tests that mirror a case of Pi's ``packages/mcp/test/oauth.test.ts`` name it
in a ``Pi:`` comment.
"""

from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.oauth import callback as callback_module
from pipy_harness.native.mcp.oauth.callback import (
    DEFAULT_CALLBACK_TIMEOUT_MS,
    OAuthCallback,
    OAuthCallbackPage,
    OAuthCallbackServer,
    OAuthCallbackServerOptions,
)
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.oauth.flow import OAuthFlowOptions, authorize_mcp
from pipy_harness.native.mcp.oauth.page import (
    SIGNED_IN_MESSAGE,
    oauth_error_html,
    oauth_success_html,
    render_sign_in_page,
)
from pipy_harness.native.mcp.oauth.provider import (
    McpOAuthProvider,
    McpOAuthProviderOptions,
    MemoryOAuthStateStore,
)
from pipy_harness.native.mcp.protocol import McpAbortError

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import (  # noqa: E402
    BrowserPage,
    FakeAuthorizationServer,
    FakeBrowser,
)

_WAIT_SECONDS = 10.0
_THREAD_NAMES = ("mcp-oauth-callback", "mcp-oauth-callback-timeout")


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name in _THREAD_NAMES]


Listen = Callable[..., OAuthCallbackServer]


@pytest.fixture
def listen() -> Iterator[Listen]:
    servers: list[OAuthCallbackServer] = []

    def start(**options: Any) -> OAuthCallbackServer:
        servers.append(OAuthCallbackServer(OAuthCallbackServerOptions(**options)))
        return servers[-1]

    yield start
    for server in servers:
        server.close()
    # Closing leaves neither the serving thread nor a timer behind.
    _wait_until(lambda: not _threads())


def _get(url: str, method: str = "GET") -> BrowserPage:
    return FakeBrowser().get(url, method)


def _failure(pending: Any) -> str:
    with pytest.raises(OAuthFlowError) as raised:
        pending.result()
    return str(raised.value)


# -- listening ----------------------------------------------------------------------


def test_listens_on_a_free_loopback_port_at_callback(listen: Listen) -> None:
    server = listen()
    assert server.port > 0
    assert server.redirect_url == f"http://127.0.0.1:{server.port}/callback"
    assert DEFAULT_CALLBACK_TIMEOUT_MS == 300_000
    # It is reachable on 127.0.0.1.
    assert _get(f"{server.redirect_url}?state=unknown").status == 400


def test_listens_on_a_configured_port_and_fails_when_it_is_taken(
    listen: Listen,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = listen(port=port)
    assert server.redirect_url == f"http://127.0.0.1:{port}/callback"
    with pytest.raises(OSError):
        OAuthCallbackServer(OAuthCallbackServerOptions(port=port))


def test_the_redirect_url_names_the_redirect_host_and_path(listen: Listen) -> None:
    server = listen(redirect_host="localhost", path="/oauth/done")
    assert server.redirect_url == f"http://localhost:{server.port}/oauth/done"
    pending = server.wait_for_callback("s1")
    page = _get(f"http://127.0.0.1:{server.port}/oauth/done?code=abc&state=s1")
    assert page.status == 200
    assert pending.result().code == "abc"
    # The default path is not served then.
    assert _get(f"http://127.0.0.1:{server.port}/callback?state=s1").status == 404


def test_an_ipv6_redirect_host_is_put_in_brackets(listen: Listen) -> None:
    server = listen(redirect_host="::1")
    assert server.redirect_url == f"http://[::1]:{server.port}/callback"


# -- responses ----------------------------------------------------------------------


def test_renders_plain_text_by_default(listen: Listen) -> None:
    # Pi: "renders plain text by default"
    server = listen()
    pending = server.wait_for_callback("s1")
    assert not pending.done.is_set()
    page = _get(f"{server.redirect_url}?code=abc&state=s1")
    assert page.status == 200
    assert page.headers["content-type"] == "text/plain; charset=utf-8"
    assert "cache-control" not in page.headers
    assert page.text == "Authorization complete. You may close this window."
    assert pending.done.is_set()
    assert pending.result() == OAuthCallback(code="abc", state="s1", iss=None)


def test_the_response_carries_the_issuer_when_sent(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s 1")
    query = urllib.parse.urlencode(
        {"code": "a+b/c", "state": "s 1", "iss": "https://as.example"}
    )
    assert _get(f"{server.redirect_url}?{query}").status == 200
    assert pending.result() == OAuthCallback("a+b/c", "s 1", "https://as.example")

    empty = server.wait_for_callback("s2")
    assert _get(f"{server.redirect_url}?code=abc&state=s2&iss=").status == 200
    assert empty.result().iss is None


def test_another_path_is_not_found(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    origin = f"http://127.0.0.1:{server.port}"
    for path in ("/", "/other", "/callback/", "/callback/x", "/CALLBACK"):
        page = _get(f"{origin}{path}?code=abc&state=s1")
        assert (page.status, page.text) == (404, "Not found"), path
    # The state is still waited for.
    assert not pending.done.is_set()
    assert _get(f"{origin}/x/../callback?code=abc&state=s1").status == 200
    assert pending.result().code == "abc"


def test_a_target_that_is_not_a_url_is_not_found(listen: Listen) -> None:
    server = listen()
    for target in ("http://[bad/callback?state=s1", "//[bad/callback", "\\callback"):
        with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
            raw.sendall(f"GET {target} HTTP/1.0\r\n\r\n".encode())
            answer = b""
            while chunk := raw.recv(4096):
                answer += chunk
        assert answer.startswith(b"HTTP/1.0 404 "), target
        assert answer.endswith(b"\r\n\r\nNot found"), target


@pytest.mark.parametrize("query", ["", "?code=abc", "?code=abc&state=", "?state=other"])
def test_an_unknown_or_missing_state_is_refused(listen: Listen, query: str) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    page = _get(f"{server.redirect_url}{query}")
    assert (page.status, page.text) == (400, "Invalid or expired OAuth state")
    assert not pending.done.is_set()


def test_a_state_is_accepted_once(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    assert _get(f"{server.redirect_url}?code=first&state=s1").status == 200
    again = _get(f"{server.redirect_url}?code=second&state=s1")
    assert (again.status, again.text) == (400, "Invalid or expired OAuth state")
    assert pending.result().code == "first"
    # The value can be registered again afterwards.
    server.wait_for_callback("s1")


def test_a_state_can_be_pending_only_once(listen: Listen) -> None:
    server = listen()
    server.wait_for_callback("s1")
    with pytest.raises(OAuthFlowError, match="^OAuth state is already pending$"):
        server.wait_for_callback("s1")
    assert _threads().count("mcp-oauth-callback-timeout") == 1


def test_an_error_response_fails_the_wait_with_its_description(
    listen: Listen,
) -> None:
    server = listen()
    described = server.wait_for_callback("s1")
    page = _get(
        f"{server.redirect_url}?error=access_denied"
        "&error_description=The+user+said+no&state=s1&code=ignored"
    )
    assert page.status == 200
    assert page.text == (
        "Authorization failed. You may close this window.\n\nThe user said no"
    )
    assert _failure(described) == "The user said no"

    bare = server.wait_for_callback("s2")
    page = _get(f"{server.redirect_url}?error=access_denied&state=s2")
    assert page.text.endswith("\n\naccess_denied")
    assert _failure(bare) == "access_denied"

    # An empty description is used as it is, so the page shows no details.
    empty = server.wait_for_callback("s3")
    page = _get(f"{server.redirect_url}?error=denied&error_description=&state=s3")
    assert page.text == "Authorization failed. You may close this window."
    assert _failure(empty) == ""


@pytest.mark.parametrize("query", ["state=s1", "state=s1&code=", "state=s1&error="])
def test_a_missing_code_fails_the_wait(listen: Listen, query: str) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    page = _get(f"{server.redirect_url}?{query}")
    assert (page.status, page.text) == (400, "Missing authorization code")
    assert _failure(pending) == "OAuth callback did not include an authorization code"


def test_rejects_a_response_on_another_path_than_the_expected_one(
    listen: Listen,
) -> None:
    # Pi: "rejects a response on another path than the expected one" (#10302)
    server = listen(extra_paths=("/callback/server-id",))
    origin = f"http://127.0.0.1:{server.port}"
    mixed_up = server.wait_for_callback("s1", "/callback/server-id")
    wrong = _get(f"{origin}/callback?code=abc&state=s1")
    assert (wrong.status, wrong.text) == (400, "Unexpected redirect URI")
    assert _failure(mixed_up) == (
        "The authorization response arrived on another redirect URI"
    )

    pending = server.wait_for_callback("s2", "/callback/server-id")
    right = _get(f"{origin}/callback/server-id?code=abc&state=s2")
    assert right.status == 200
    assert pending.result().code == "abc"

    # Without an expected path, every served path is accepted.
    either = server.wait_for_callback("s3")
    assert _get(f"{origin}/callback/server-id?code=abc&state=s3").status == 200
    assert either.result().code == "abc"


@pytest.mark.parametrize("method", ["POST", "HEAD", "PUT", "DELETE"])
def test_the_request_method_does_not_matter(listen: Listen, method: str) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    page = _get(f"{server.redirect_url}?code=abc&state=s1", method)
    assert page.status == 200
    assert page.text == (
        "" if method == "HEAD" else "Authorization complete. You may close this window."
    )
    assert pending.result().code == "abc"


def test_two_sign_ins_wait_on_one_server(listen: Listen) -> None:
    server = listen()
    first = server.wait_for_callback("s1")
    second = server.wait_for_callback("s2")
    assert _get(f"{server.redirect_url}?code=two&state=s2").status == 200
    assert second.result().code == "two"
    assert not first.done.is_set()
    assert _get(f"{server.redirect_url}?code=one&state=s1").status == 200
    assert first.result().code == "one"


def test_an_idle_connection_does_not_block_the_callback(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as idle:
        assert _get(f"{server.redirect_url}?code=abc&state=s1").status == 200
        assert pending.result().code == "abc"
        # Closing the server drops the connection.
        server.close()
        assert idle.recv(1) == b""


# -- waiting ------------------------------------------------------------------------


def test_result_blocks_until_the_response_arrives(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    results: list[OAuthCallback] = []
    waiter = threading.Thread(target=lambda: results.append(pending.result()))
    waiter.start()
    try:
        time.sleep(0.05)
        assert not results
        _get(f"{server.redirect_url}?code=abc&state=s1")
    finally:
        waiter.join(_WAIT_SECONDS)
    assert results == [OAuthCallback("abc", "s1")]


def test_a_wait_gives_up_after_the_timeout(listen: Listen) -> None:
    server = listen(timeout_ms=50)
    pending = server.wait_for_callback("s1")
    assert pending.done.wait(_WAIT_SECONDS)
    assert _failure(pending) == "OAuth callback timed out"
    late = _get(f"{server.redirect_url}?code=abc&state=s1")
    assert (late.status, late.text) == (400, "Invalid or expired OAuth state")


def test_a_response_ends_the_timeout(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    assert _threads().count("mcp-oauth-callback-timeout") == 1
    _get(f"{server.redirect_url}?code=abc&state=s1")
    assert pending.result().code == "abc"
    _wait_until(lambda: "mcp-oauth-callback-timeout" not in _threads())


def test_a_huge_timeout_is_accepted(listen: Listen) -> None:
    server = listen(timeout_ms=1e300)
    pending = server.wait_for_callback("s1")
    _get(f"{server.redirect_url}?code=abc&state=s1")
    assert pending.result().code == "abc"


def test_a_cancelled_wait_raises_and_keeps_the_state(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    with pytest.raises(McpAbortError):
        pending.result(cancel)
    assert not pending.done.is_set()
    # An outcome that is already there wins over the cancel event.
    _get(f"{server.redirect_url}?code=abc&state=s1")
    assert pending.result(cancel).code == "abc"


def test_close_fails_every_wait_and_stops_listening(listen: Listen) -> None:
    server = listen()
    first = server.wait_for_callback("s1")
    second = server.wait_for_callback("s2")
    server.close()
    assert _failure(first) == "OAuth callback server closed"
    assert _failure(second) == "OAuth callback server closed"
    with pytest.raises(OSError):
        _get(f"{server.redirect_url}?code=abc&state=s1")
    with pytest.raises(OAuthFlowError, match="^OAuth callback server closed$"):
        server.wait_for_callback("s3")
    _wait_until(lambda: not _threads())
    # A second close does nothing.
    server.close()


def test_the_port_is_free_again_after_close(listen: Listen) -> None:
    first = listen()
    first.close()
    second = listen(port=first.port)
    assert second.port == first.port


# -- pages --------------------------------------------------------------------------


def test_renders_pages_through_render_page(listen: Listen) -> None:
    # Pi: "renders pages through renderPage"
    pages: list[OAuthCallbackPage] = []

    def render(page: OAuthCallbackPage) -> str:
        pages.append(page)
        return "<p>ok</p>" if page.ok else f"<p>{page.message}</p>"

    server = listen(render_page=render)
    denied = server.wait_for_callback("s1")
    failure = _get(
        f"{server.redirect_url}?error=access_denied&error_description=Denied&state=s1"
    )
    assert failure.headers["content-type"] == "text/html; charset=utf-8"
    assert failure.headers["cache-control"] == "no-store"
    assert _failure(denied) == "Denied"
    assert pages[-1] == OAuthCallbackPage(
        ok=False,
        message="Authorization failed. You may close this window.",
        details="Denied",
    )

    pending = server.wait_for_callback("s2")
    success = _get(f"{server.redirect_url}?code=abc&state=s2")
    assert success.text == "<p>ok</p>"
    assert pages[-1] == OAuthCallbackPage(ok=True)
    assert pending.result().code == "abc"

    assert _get(f"http://127.0.0.1:{server.port}/other").text == "<p>Not found</p>"


class _SlowPage:
    """A ``render_page`` whose success page waits for ``go_on``."""

    def __init__(self) -> None:
        self.rendering = threading.Event()
        self.go_on = threading.Event()

    def __call__(self, page: OAuthCallbackPage) -> str:
        if not page.ok:
            return "<p>refused</p>"
        self.rendering.set()
        assert self.go_on.wait(_WAIT_SECONDS)
        return "<p>done</p>"


def test_close_lets_a_page_that_is_being_sent_finish(listen: Listen) -> None:
    render = _SlowPage()
    pages: list[BrowserPage] = []
    server = listen(render_page=render)
    pending = server.wait_for_callback("s1")
    url = f"{server.redirect_url}?code=abc&state=s1"
    browser = threading.Thread(target=lambda: pages.append(_get(url)))
    browser.start()
    # A caller that closes the server as soon as it has the code.
    closer = threading.Thread(target=server.close)
    try:
        assert render.rendering.wait(_WAIT_SECONDS)
        # The wait ends before the page is made.
        assert pending.result().code == "abc"
        closer.start()
        _wait_until(lambda: server._closed)
        time.sleep(0.05)
        assert closer.is_alive()
    finally:
        render.go_on.set()
        browser.join(_WAIT_SECONDS)
    closer.join(_WAIT_SECONDS)
    assert not closer.is_alive()
    assert [(page.status, page.text) for page in pages] == [(200, "<p>done</p>")]


def test_close_does_not_wait_long_for_a_page(
    listen: Listen, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(callback_module, "_CLOSE_GRACE_SECONDS", 0.1)
    render = _SlowPage()
    failures: list[Exception] = []

    def browse() -> None:
        try:
            _get(f"{server.redirect_url}?code=abc&state=s1")
        except (OSError, http.client.HTTPException) as error:
            failures.append(error)

    server = listen(render_page=render)
    pending = server.wait_for_callback("s1")
    browser = threading.Thread(target=browse)
    browser.start()
    try:
        assert render.rendering.wait(_WAIT_SECONDS)
        assert pending.result().code == "abc"
        server.close()
        # The connection was dropped while the page was still being made.
        browser.join(_WAIT_SECONDS)
        assert not browser.is_alive()
        assert len(failures) == 1
    finally:
        render.go_on.set()
        browser.join(_WAIT_SECONDS)


def test_a_browser_that_went_away_still_ends_the_wait(listen: Listen) -> None:
    server = listen()
    pending = server.wait_for_callback("s1")
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
        raw.sendall(b"GET /callback?code=abc&state=s1 HTTP/1.0\r\n\r\n")
    assert pending.result().code == "abc"


def test_a_failing_render_page_still_answers(listen: Listen) -> None:
    def render(page: OAuthCallbackPage) -> str:
        raise RuntimeError("no page")

    server = listen(render_page=render)
    pending = server.wait_for_callback("s1")
    page = _get(f"{server.redirect_url}?code=abc&state=s1")
    assert page.status == 200
    assert page.headers["content-type"] == "text/plain; charset=utf-8"
    assert page.text == "Authorization complete. You may close this window."
    assert pending.result().code == "abc"


def test_the_sign_in_pages_are_html(listen: Listen) -> None:
    # OAU-17
    assert SIGNED_IN_MESSAGE == (
        "Signed in to the MCP server. You may now close this page."
    )
    server = listen(render_page=render_sign_in_page)
    pending = server.wait_for_callback("s1")
    success = _get(f"{server.redirect_url}?code=abc&state=s1")
    assert pending.result().code == "abc"
    assert success.headers["content-type"] == "text/html; charset=utf-8"
    assert success.text.startswith("<!doctype html>")
    assert "<title>Authentication successful</title>" in success.text
    assert "<h1>Authentication successful</h1>" in success.text
    assert f"<p>{SIGNED_IN_MESSAGE}</p>" in success.text
    assert 'class="details"' not in success.text

    denied = server.wait_for_callback("s2")
    description = urllib.parse.quote("<script>alert('no' & \"no\")</script>")
    failure = _get(
        f"{server.redirect_url}?error=access_denied"
        f"&error_description={description}&state=s2"
    )
    assert _failure(denied) == "<script>alert('no' & \"no\")</script>"
    assert "<title>Authentication failed</title>" in failure.text
    assert "<p>Authorization failed. You may close this window.</p>" in failure.text
    assert (
        '<div class="details">&lt;script&gt;alert(&#x27;no&#x27; &amp; '
        "&quot;no&quot;)&lt;/script&gt;</div>"
    ) in failure.text
    assert "<script" not in failure.text

    unknown = _get(f"{server.redirect_url}?state=unknown")
    assert unknown.status == 400
    assert "<p>Invalid or expired OAuth state</p>" in unknown.text
    assert 'class="details"' not in unknown.text


def test_the_page_helpers_escape_their_text() -> None:
    assert "<p>a &lt; b</p>" in oauth_success_html("a < b")
    page = oauth_error_html("x & y", "line 1\nline 2")
    assert "<p>x &amp; y</p>" in page
    assert '<div class="details">line 1\nline 2</div>' in page
    assert 'class="details"' not in oauth_error_html("x", "")


# -- a whole sign-in ----------------------------------------------------------------


def test_a_browser_sign_in_ends_on_the_callback_server(listen: Listen) -> None:
    # Pi: the browser part of "discovers, registers, authorizes with PKCE, and
    # refreshes on 401"
    with FakeAuthorizationServer() as authorization_server:
        authorization_server.send_iss = True
        callback = listen(render_page=render_sign_in_page)
        urls: list[str] = []
        server_url = f"{authorization_server.origin}/mcp"
        store = MemoryOAuthStateStore()
        provider = McpOAuthProvider(
            McpOAuthProviderOptions(
                server_url=server_url,
                redirect_url=callback.redirect_url,
                client_metadata={"client_name": "pipy"},
                on_redirect=urls.append,
                store=store,
            )
        )
        options = OAuthFlowOptions(server_url=server_url)
        assert authorize_mcp(provider, options) == "REDIRECT"

        pending = callback.wait_for_callback(provider.state())
        browser = FakeBrowser()
        page = browser.open(urls[0])
        assert page.status == 200
        assert SIGNED_IN_MESSAGE in page.text
        assert len(browser.visited) == 2
        assert browser.visited[1].startswith(f"{callback.redirect_url}?")

        response = pending.result()
        assert response.state == provider.state()
        assert response.iss == authorization_server.origin
        with_code = OAuthFlowOptions(
            server_url=server_url,
            authorization_code=response.code,
            iss=response.iss,
        )
        assert authorize_mcp(provider, with_code) == "AUTHORIZED"
        state = store.load()
        assert state is not None
        assert state["tokens"]["access_token"].startswith("access-")
        assert [f["code"] for f in authorization_server.token_forms] == [response.code]
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]
    )
