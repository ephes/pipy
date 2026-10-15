"""Tests for the sign-in to an OAuth MCP server (MCP parity plan S31).

Tests that mirror a case of Pi's
``packages/coding-agent/test/mcp-oauth-refresh.test.ts`` ("MCP OAuth
sign-in") name it in a ``Pi:`` comment. The browser is a test double that
asks the authorization URL and follows the redirect to the loopback port.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthServerStore,
    McpOAuthSettings,
    mcp_auth_path,
)
from pipy_harness.native.mcp.oauth.errors import (
    OAuthFlowError,
    OAuthIssuerMismatchError,
)
from pipy_harness.native.mcp.oauth.flow import step_up_scope
from pipy_harness.native.mcp.oauth.types import OAuthChallenge
from pipy_harness.native.mcp.sign_in import (
    CallbackSettings,
    McpSignInCancelledError,
    browser_command,
    callback_settings,
    merge_scopes,
    open_browser,
    response_from_redirect_url,
    sign_in_mcp_server,
    wait_for_cancel,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer, Reply  # noqa: E402

_WAIT_SECONDS = 10.0
_SIGN_IN_THREADS = ("mcp-oauth-fetch", "mcp-sign-in-prompt", "mcp-oauth-callback")


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    with FakeOAuthMcpServer() as fake:
        yield fake
    # No sign-in leaves a thread behind.
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name in _SIGN_IN_THREADS]
    )


@pytest.fixture
def store(server: FakeOAuthMcpServer, tmp_path: Path) -> McpOAuthServerStore:
    credentials = McpOAuthCredentialStore(mcp_auth_path(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return credentials.for_server("test", server.url)


class _Prompt:
    """A user with a browser.

    ``follow``: the browser reaches the loopback port. ``paste``: it does
    not, and the user pastes the URL it was redirected to. ``ignore``: the
    user never approves. ``answer`` replaces what is pasted.
    """

    def __init__(self, browser: str = "follow", answer: str | None = None) -> None:
        self._browser = browser
        self._answer = answer
        self.opened: list[str] = []
        self.redirected_to = ""
        self.shown = threading.Event()
        # The cancel event of the prompt for the redirect URL.
        self.prompt_cancel: threading.Event | None = None
        self.prompt_thread = ""

    def show_authorization_url(self, url: str) -> None:
        self.opened.append(url)
        if self._browser == "follow":
            FakeBrowser().open(url)
        elif self._browser == "paste":
            self.redirected_to = FakeBrowser().get(url).headers["location"]
        self.shown.set()

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        self.prompt_cancel = cancel_event
        self.prompt_thread = threading.current_thread().name
        if self._answer is not None:
            return self._answer
        if self._browser == "paste":
            return self.redirected_to
        wait_for_cancel(cancel_event)
        return None

    def query(self, index: int = -1) -> dict[str, str]:
        """The parameters of an authorization URL that was shown."""

        query = urllib.parse.urlsplit(self.opened[index]).query
        return dict(urllib.parse.parse_qsl(query, True))


def _sign_in(
    server: FakeOAuthMcpServer,
    store: McpOAuthServerStore,
    prompt: _Prompt | None = None,
    settings: McpOAuthSettings | None = None,
    challenge: OAuthChallenge | None = None,
    cancel_event: threading.Event | None = None,
) -> _Prompt:
    prompt = prompt or _Prompt()
    sign_in_mcp_server(
        server_url=server.url,
        store=store,
        settings=settings or McpOAuthSettings(),
        prompt=prompt,
        challenge=challenge,
        cancel_event=cancel_event,
    )
    return prompt


def _stored(store: McpOAuthServerStore) -> dict:
    state = store.load()
    assert state is not None
    return state


def _forget_tokens(store: McpOAuthServerStore) -> None:
    state = _stored(store)
    del state["tokens"]
    store.save(state)


# -- the pieces -----------------------------------------------------------------


def test_merges_scopes_keeping_each_once_in_order() -> None:
    assert merge_scopes("a b", None, " b  c ") == "a b c"
    assert merge_scopes(None, "") is None
    assert merge_scopes() is None


def test_a_step_up_asks_for_the_granted_and_the_challenged_scopes() -> None:
    assert step_up_scope("read", "write") == "read write"
    assert step_up_scope("read write", "write admin") == "read write admin"
    assert step_up_scope(None, "write") == "write"
    # Without challenged scopes the flow chooses.
    assert step_up_scope("read", None) is None
    assert step_up_scope("read", "") is None


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (
            McpOAuthSettings(),
            CallbackSettings("127.0.0.1", "127.0.0.1", None, "/callback", None),
        ),
        (
            McpOAuthSettings(callback_port=4567),
            CallbackSettings(
                "127.0.0.1",
                "127.0.0.1",
                4567,
                "/callback",
                "http://127.0.0.1:4567/callback",
            ),
        ),
        # `localhost` is served on 127.0.0.1 and stays in the redirect URI.
        (
            McpOAuthSettings(callback_url="http://localhost:8123/done"),
            CallbackSettings(
                "127.0.0.1", "localhost", 8123, "/done", "http://localhost:8123/done"
            ),
        ),
        # A URL without a port gets the configured port, or a free one.
        (
            McpOAuthSettings(
                callback_url="http://127.0.0.1/oauth/done", callback_port=9
            ),
            CallbackSettings(
                "127.0.0.1",
                "127.0.0.1",
                9,
                "/oauth/done",
                "http://127.0.0.1:9/oauth/done",
            ),
        ),
        (
            McpOAuthSettings(callback_url="http://[::1]/callback"),
            CallbackSettings("::1", "::1", None, "/callback", None),
        ),
    ],
)
def test_callback_settings(
    settings: McpOAuthSettings, expected: CallbackSettings
) -> None:
    assert callback_settings(settings) == expected


_REDIRECT = "http://127.0.0.1:4000/callback"


def test_reads_the_authorization_response_from_a_pasted_redirect_url() -> None:
    response = response_from_redirect_url(
        f"  {_REDIRECT}?code=abc&state=s1&iss=https%3A%2F%2Fas.example \n",
        "s1",
        _REDIRECT,
    )
    assert (response.code, response.iss) == ("abc", "https://as.example")
    assert response_from_redirect_url(
        f"{_REDIRECT}?code=abc&state=s1", "s1", _REDIRECT
    ) == type(response)("abc", None)


@pytest.mark.parametrize(
    ("pasted", "message"),
    [
        # OAU-27, in the order of the checks.
        ("abc", "Expected the full redirect URL from the browser address bar"),
        (
            "127.0.0.1:4000/callback?code=abc&state=s1",
            "Expected the full redirect URL from the browser address bar",
        ),
        # A URL, of the scheme `localhost`.
        (
            "localhost:4000/callback?code=abc&state=s1",
            "The redirect URL does not match this sign-in's redirect URI",
        ),
        (
            "http://127.0.0.1:4001/callback?code=abc&state=s1",
            "The redirect URL does not match this sign-in's redirect URI",
        ),
        (
            "http://127.0.0.1:4000/callback/other?code=abc&state=s1",
            "The redirect URL does not match this sign-in's redirect URI",
        ),
        (
            "https://127.0.0.1:4000/callback?code=abc&state=s1",
            "The redirect URL does not match this sign-in's redirect URI",
        ),
        # An error is reported before the state is looked at.
        (
            f"{_REDIRECT}?error=access_denied&error_description=The+user+said+no",
            "The user said no",
        ),
        (f"{_REDIRECT}?error=access_denied&state=s1", "access_denied"),
        (
            f"{_REDIRECT}?code=abc&state=other",
            "The redirect URL belongs to a different sign-in",
        ),
        (f"{_REDIRECT}?code=abc", "The redirect URL belongs to a different sign-in"),
        (
            f"{_REDIRECT}?state=s1",
            "The redirect URL does not contain an authorization code",
        ),
        (
            f"{_REDIRECT}?code=&state=s1",
            "The redirect URL does not contain an authorization code",
        ),
    ],
)
def test_rejects_a_pasted_url_that_is_not_this_sign_ins_response(
    pasted: str, message: str
) -> None:
    with pytest.raises(OAuthFlowError) as raised:
        response_from_redirect_url(pasted, "s1", _REDIRECT)
    assert str(raised.value) == message


# -- the sign-in ----------------------------------------------------------------


def test_signs_in_through_the_browser(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    prompt = _sign_in(server, store)

    assert server.log == ["register", "token code"]
    state = _stored(store)
    assert state["tokens"]["access_token"].startswith("access-")
    # OAU-08: the client is registered as `pipy`, a native application.
    (registration,) = server.registrations
    assert registration["client_name"] == "pipy"
    assert registration["application_type"] == "native"
    query = prompt.query()
    assert registration["redirect_uris"] == [query["redirect_uri"]]
    assert urllib.parse.urlsplit(query["redirect_uri"]).hostname == "127.0.0.1"
    assert urllib.parse.urlsplit(query["redirect_uri"]).path == "/callback"
    # OAU-15: 32 random bytes in hex.
    assert len(query["state"]) == 64 and int(query["state"], 16) >= 0
    # The prompt for the redirect URL ran on its own thread and was told
    # that its answer is no longer wanted.
    assert prompt.prompt_thread == "mcp-sign-in-prompt"
    assert prompt.prompt_cancel is not None and prompt.prompt_cancel.is_set()


def test_accepts_a_pasted_redirect_url_when_the_browser_cannot_reach_the_callback(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    prompt = _sign_in(server, store, _Prompt("paste"))

    assert prompt.redirected_to.startswith("http://127.0.0.1:")
    assert server.log == ["register", "token code"]
    assert "access_token" in _stored(store)["tokens"]


def test_an_empty_paste_cancels_the_sign_in(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    with pytest.raises(McpSignInCancelledError) as raised:
        _sign_in(server, store, _Prompt("ignore", answer="  "))

    assert str(raised.value) == "Sign-in cancelled"
    assert "tokens" not in _stored(store)


def test_a_pasted_url_of_another_sign_in_fails_the_sign_in(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    with pytest.raises(OAuthFlowError, match="does not match this sign-in"):
        _sign_in(
            server,
            store,
            _Prompt("ignore", answer="http://127.0.0.1:1/callback?code=c&state=s"),
        )


def test_rejects_an_authorization_response_from_another_issuer(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # Pi: "rejects an authorization response from another issuer"
    server.response_iss = "https://attacker.example"

    with pytest.raises(OAuthIssuerMismatchError):
        _sign_in(server, store)

    assert "token code" not in server.log


def test_keeps_the_granted_scope_when_the_server_asks_for_more(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # Pi: "keeps the granted scope when the server asks for more"
    first = _sign_in(server, store, challenge=OAuthChallenge(scope="issues:read"))
    # The token response names no scope, so the grant has the requested one.
    assert _stored(store)["tokens"]["scope"] == "issues:read"

    # The step-up challenge lists only the missing scope. Requesting just
    # that would lose issues:read.
    second = _sign_in(
        server,
        store,
        challenge=OAuthChallenge(error="insufficient_scope", scope="issues:write"),
    )

    assert first.query()["scope"] == "issues:read"
    assert second.query()["scope"] == "issues:read issues:write"
    assert _stored(store)["tokens"]["scope"] == "issues:read issues:write"
    # OAU-21, OAU-24: the stored refresh token was not used for the step-up.
    assert server.log == ["register", "token code", "token code"]


def test_asks_for_the_configured_scope_and_the_challenged_one_together(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-07.
    prompt = _sign_in(
        server,
        store,
        settings=McpOAuthSettings(scope="issues:read profile"),
        challenge=OAuthChallenge(scope="profile issues:write"),
    )

    assert prompt.query()["scope"] == "issues:read profile issues:write"


def test_a_step_up_adds_the_configured_scope_too(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-21: configured, granted so far and challenged scopes together.
    _sign_in(server, store, challenge=OAuthChallenge(scope="issues:read"))

    prompt = _sign_in(
        server,
        store,
        settings=McpOAuthSettings(scope="profile"),
        challenge=OAuthChallenge(error="insufficient_scope", scope="issues:write"),
    )

    assert prompt.query()["scope"] == "profile issues:read issues:write"


def test_tries_the_stored_refresh_token_before_the_browser(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-24.
    _sign_in(server, store)
    first = _stored(store)["tokens"]["access_token"]

    again = _sign_in(server, store)

    assert again.opened == []
    assert server.log == ["register", "token code", "token refresh"]
    assert _stored(store)["tokens"]["access_token"] != first


def test_every_sign_in_has_a_new_state_and_reuses_the_registered_client(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-15, OAU-25: the second sign-in listens on the port of the
    # registered redirect URI, so the client stays valid.
    first = _sign_in(server, store)
    _forget_tokens(store)

    second = _sign_in(server, store)

    assert first.query()["state"] != second.query()["state"]
    assert first.query()["redirect_uri"] == second.query()["redirect_uri"]
    assert first.query()["client_id"] == second.query()["client_id"]
    assert server.log == ["register", "token code", "token code"]


def _occupy(redirect_uri: str) -> socket.socket:
    """Listen on the port of ``redirect_uri``, so nobody else can."""

    port = urllib.parse.urlsplit(redirect_uri).port
    assert port is not None
    taken = socket.socket()
    # The callback server that just closed left the port in TIME_WAIT.
    taken.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    taken.bind(("127.0.0.1", port))
    taken.listen()
    return taken


def test_a_taken_port_is_replaced_by_a_free_one_and_the_client_registered_again(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-25: a registered client cannot use another redirect URI, and its
    # tokens belong to it.
    first = _sign_in(server, store)
    taken = _occupy(first.query()["redirect_uri"])
    try:
        second = _sign_in(server, store)
    finally:
        taken.close()

    assert second.query()["redirect_uri"] != first.query()["redirect_uri"]
    assert second.query()["client_id"] != first.query()["client_id"]
    # No refresh with the tokens of the client that was dropped.
    assert server.log == ["register", "token code", "register", "token code"]


def test_a_configured_port_that_is_taken_fails_the_sign_in(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    try:
        with pytest.raises(OSError):
            _sign_in(
                server,
                store,
                settings=McpOAuthSettings(callback_port=taken.getsockname()[1]),
            )
    finally:
        taken.close()
    assert server.requests == []


def test_a_configured_client_keeps_its_tokens_on_another_redirect_uri(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-25: "unless `clientId` is configured".
    server.add_client("configured")
    settings = McpOAuthSettings(client_id="configured")
    first = _sign_in(server, store, settings=settings)
    taken = _occupy(first.query()["redirect_uri"])
    try:
        second = _sign_in(server, store, settings=settings)
    finally:
        taken.close()

    assert second.opened == []
    assert server.log == ["token code", "token refresh"]


def test_cancels_a_sign_in_that_waits_for_the_browser(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # OAU-28.
    prompt = _Prompt("ignore")
    cancel = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _sign_in(server, store, prompt, cancel_event=cancel)
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert prompt.shown.wait(_WAIT_SECONDS)
    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert not thread.is_alive()
    assert [type(error) for error in errors] == [McpSignInCancelledError]
    # The loopback port is closed: the browser would find nobody there.
    port = urllib.parse.urlsplit(prompt.query()["redirect_uri"]).port
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


@pytest.mark.parametrize("path", ["/.well-known/oauth-authorization-server", "/token"])
def test_cancels_a_sign_in_while_the_authorization_server_hangs(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore, path: str
) -> None:
    # Pi: "cancels a sign-in waiting on <path> when the session shuts down"
    # (the part of the sign-in itself). OAU-28.
    server.route(path, Reply(hang=True))
    cancel = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _sign_in(server, store, cancel_event=cancel)
        except BaseException as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    _wait_until(lambda: path in server.paths)
    started = time.monotonic()
    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert not thread.is_alive()
    assert time.monotonic() - started < 2.0
    assert [type(error) for error in errors] == [McpSignInCancelledError]


class SlowLauncher:
    """``open`` and ``xdg-open`` on ``PATH`` that record their pid and
    arguments and then block, holding whatever standard streams they got."""

    def __init__(self, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory.mkdir()
        self.record = directory / "launched"
        for name in ("open", "xdg-open"):
            script = directory / name
            script.write_text(
                f'#!/bin/sh\necho "$$ $*" >> "{self.record}"\nexec sleep 30\n'
            )
            script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")

    def launched(self) -> list[str]:
        """The arguments of each launch, once it got as far as recording them."""

        if not self.record.exists():
            return []
        lines = self.record.read_text().splitlines()
        return [line.split(" ", 1)[1] for line in lines]

    def stop(self) -> None:
        if not self.record.exists():
            return
        for line in self.record.read_text().splitlines():
            try:
                os.kill(int(line.split(" ", 1)[0]), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.fixture
def slow_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SlowLauncher]:
    launcher = SlowLauncher(tmp_path / "bin", monkeypatch)
    yield launcher
    launcher.stop()


def test_browser_command_is_pis() -> None:
    # Pi `openBrowser`: no shell, so the URL cannot run a command.
    command = browser_command("https://a.example/?x=1&y=$(id)")
    assert command[-1] == "https://a.example/?x=1&y=$(id)"
    assert command[0] == {"darwin": "open", "win32": "rundll32"}.get(
        sys.platform, "xdg-open"
    )


def test_opening_the_browser_does_not_wait_for_the_launcher(
    slow_launcher: SlowLauncher,
) -> None:
    # Goal check r5, F1: Pi's openBrowser starts its launcher detached, with
    # no standard streams, and does not wait for it. A launcher that blocks
    # must hold neither the sign-in nor the output of whoever reads pipy's.
    child = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            "from pipy_harness.native.mcp.sign_in import open_browser\n"
            "open_browser('https://auth.example/authorize?x=1')\n"
            "print('returned')\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The pipes close when the child exits, not when the launcher does.
        stdout, stderr = child.communicate(timeout=_WAIT_SECONDS)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert (stdout, stderr, child.returncode) == ("returned\n", "", 0)
    _wait_until(
        lambda: slow_launcher.launched() == ["https://auth.example/authorize?x=1"]
    )


def test_a_launcher_that_cannot_be_started_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user has the URL (Pi ignores the launcher's error event).
    monkeypatch.setenv("PATH", str(tmp_path))
    open_browser("https://auth.example/authorize")


def test_a_sign_in_that_is_cancelled_before_it_starts_asks_nobody(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(McpSignInCancelledError):
        _sign_in(server, store, cancel_event=cancel)

    assert server.requests == []
    assert store.load() is None


def test_reports_what_the_authorization_server_answered(
    server: FakeOAuthMcpServer, store: McpOAuthServerStore
) -> None:
    # A failure that is no cancellation keeps its own error.
    server.route("/register", Reply(500, body=b"no"))

    with pytest.raises(Exception, match="500") as raised:
        _sign_in(server, store)

    assert not isinstance(raised.value, McpSignInCancelledError)
