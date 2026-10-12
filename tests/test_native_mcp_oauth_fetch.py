"""Tests for the OAuth library's default fetch, against the fake server."""

from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.mcp.oauth import fetch as fetch_module
from pipy_harness.native.mcp.oauth.fetch import (
    MAX_REDIRECTS,
    OAuthResponse,
    create_oauth_fetch,
    is_network_error,
    oauth_fetch,
)
from pipy_harness.native.mcp.protocol import (
    McpAbortError,
    McpProtocolError,
    McpTimeoutError,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeOAuthServer, OAuthRequest, Reply  # noqa: E402

_WAIT_SECONDS = 10.0


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _fetch_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]


@pytest.fixture
def server() -> Iterator[FakeOAuthServer]:
    with FakeOAuthServer() as fake:
        yield fake
    # No test leaves a request thread behind.
    _wait_until(lambda: not _fetch_threads())


def _redirect(location: str, status: int = 302) -> Reply:
    return Reply(status, {"Location": location})


# -- the response ---------------------------------------------------------------


def test_a_response_has_status_headers_and_body(server: FakeOAuthServer) -> None:
    server.route(
        "/doc",
        Reply(201, {"Content-Type": "application/json", "X-Test": "a"}, b'{"a": 1}'),
    )
    response = oauth_fetch(f"{server.origin}/doc?x=1")
    assert (response.status, response.ok) == (201, True)
    assert response.header("X-Test") == "a"
    assert response.headers["content-type"] == "application/json"
    assert response.header("missing") is None
    assert response.text() == '{"a": 1}'
    assert response.json() == {"a": 1}
    [request] = server.requests
    assert (request.method, request.path, request.query) == ("GET", "/doc", "x=1")
    assert request.body == b""


def test_a_failure_status_is_a_response(server: FakeOAuthServer) -> None:
    response = oauth_fetch(f"{server.origin}/missing")
    assert (response.status, response.ok, response.body) == (404, False, b"")
    assert not OAuthResponse(199, {}).ok
    assert not OAuthResponse(300, {}).ok


def test_method_headers_and_body_are_sent(server: FakeOAuthServer) -> None:
    server.route("/token", Reply(200, {}, b"ok"))
    response = oauth_fetch(
        f"{server.origin}/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-A": "1"},
        body=b"grant_type=refresh_token",
    )
    assert response.text() == "ok"
    [request] = server.requests
    assert request.method == "POST"
    assert request.body == b"grant_type=refresh_token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.headers["x-a"] == "1"
    assert request.headers["host"] == f"127.0.0.1:{server.port}"


def test_the_path_and_query_are_percent_encoded(server: FakeOAuthServer) -> None:
    oauth_fetch(f"{server.origin}/a b/ä%20c?q=x y&r=%41")
    [request] = server.requests
    assert (request.path, request.query) == ("/a%20b/%C3%A4%20c", "q=x%20y&r=%41")


def test_dot_segments_are_resolved_before_the_request(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", _redirect("/x/%2E%2e/./b/"))
    for path in ("/dir/../a", "/dir/%2e%2E/a", "/./a", "/../a"):
        oauth_fetch(server.origin + path)
    assert server.paths == ["/a", "/b/"] * 4
    # A relative redirect starts from the path that was asked for.
    server.route("/d/e", _redirect("f"))
    oauth_fetch(f"{server.origin}/d/x/%2e%2e/e")
    assert server.paths[-2:] == ["/d/e", "/d/f"]


def test_text_is_decoded_leniently_and_json_strictly() -> None:
    assert OAuthResponse(200, {}, b"\xef\xbb\xbf{}").json() == {}
    assert OAuthResponse(200, {}, b"a\xffb").text() == "a�b"
    for body in (b"", b"<html>", b"NaN", b'{"a": Infinity}', b"[" * 100_000):
        with pytest.raises(ValueError):
            OAuthResponse(200, {}, body).json()


# -- redirects ------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_request_without_a_body_follows_redirects(
    server: FakeOAuthServer, status: int
) -> None:
    server.route("/a", _redirect("/b?x=1", status))
    server.route("/b", _redirect(f"{server.origin}/c"))
    server.route("/c", Reply(200, {}, b"done"))
    response = oauth_fetch(f"{server.origin}/a", headers={"Accept": "text/plain"})
    assert response.text() == "done"
    assert server.paths == ["/a", "/b", "/c"]
    assert [request.headers["accept"] for request in server.requests] == [
        "text/plain"
    ] * 3


def test_a_redirect_may_lead_to_another_origin(server: FakeOAuthServer) -> None:
    with FakeOAuthServer() as other:
        other.route("/doc", Reply(200, {}, b"there"))
        server.route("/doc", _redirect(f"{other.origin}/doc"))
        assert oauth_fetch(f"{server.origin}/doc").text() == "there"


def test_a_redirect_to_another_origin_drops_the_credential_headers(
    server: FakeOAuthServer,
) -> None:
    headers = {"Authorization": "Bearer t", "Cookie": "a=1", "Accept": "text/plain"}
    with FakeOAuthServer() as other:
        server.route("/doc", _redirect("/same"))
        server.route("/same", _redirect(f"{other.origin}/doc"))
        other.route("/doc", _redirect(f"{server.origin}/back"))
        oauth_fetch(f"{server.origin}/doc", headers=headers)
        [there] = other.requests
    doc, same, back = server.requests
    for kept in (doc, same):
        assert kept.headers["authorization"] == "Bearer t"
        assert kept.headers["cookie"] == "a=1"
    # They do not come back when a later redirect returns to the first origin.
    for dropped in (there, back):
        assert "authorization" not in dropped.headers
        assert "cookie" not in dropped.headers
        assert dropped.headers["accept"] == "text/plain"


def test_a_request_with_a_body_does_not_follow_a_redirect(
    server: FakeOAuthServer,
) -> None:
    server.route("/token", _redirect("/elsewhere", 307))
    response = oauth_fetch(f"{server.origin}/token", method="POST", body=b"secret=1")
    assert response.status == 307
    assert response.header("location") == "/elsewhere"
    assert server.paths == ["/token"]


def test_a_redirect_status_without_a_location_is_the_response(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", Reply(302))
    assert oauth_fetch(f"{server.origin}/a").status == 302


def test_too_many_redirects_are_a_network_failure(server: FakeOAuthServer) -> None:
    server.route("/loop", _redirect("/loop"))
    with pytest.raises(http.client.HTTPException, match="Too many redirects"):
        oauth_fetch(f"{server.origin}/loop")
    assert len(server.requests) == MAX_REDIRECTS + 1


def test_a_redirect_to_a_url_that_cannot_be_requested_is_a_network_failure(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", _redirect("file:///etc/passwd"))
    with pytest.raises(http.client.InvalidURL):
        oauth_fetch(f"{server.origin}/a")


@pytest.mark.parametrize("location", ["http://[", "http://[::1", "//[x]/a"])
def test_a_redirect_to_a_malformed_url_is_a_network_failure(
    server: FakeOAuthServer, location: str
) -> None:
    server.route("/a", _redirect(location))
    with pytest.raises(http.client.InvalidURL) as raised:
        oauth_fetch(f"{server.origin}/a")
    assert is_network_error(raised.value)
    assert server.paths == ["/a"]


# -- failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "not a url",
        "https://",
        "http://[",
        "http://[::1",
        "http://user:secret@127.0.0.1/",
    ],
)
def test_a_url_that_cannot_be_requested_is_a_network_failure(url: str) -> None:
    with pytest.raises(http.client.InvalidURL) as raised:
        oauth_fetch(url)
    assert is_network_error(raised.value)
    assert str(raised.value) == f"Cannot request URL {url}"


def test_a_refused_connection_is_a_network_failure() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(OSError) as raised:
        oauth_fetch(f"http://127.0.0.1:{port}/")
    assert is_network_error(raised.value)


def test_a_cut_connection_is_a_network_failure(server: FakeOAuthServer) -> None:
    server.route("/a", Reply(drop=True))
    with pytest.raises((OSError, http.client.HTTPException)) as raised:
        oauth_fetch(f"{server.origin}/a")
    assert is_network_error(raised.value)


def test_a_body_shorter_than_announced_is_a_network_failure(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", Reply(200, {}, b"short", content_length=50))
    with pytest.raises(http.client.IncompleteRead):
        oauth_fetch(f"{server.origin}/a")


def test_a_body_over_the_limit_is_refused(server: FakeOAuthServer) -> None:
    fetch = create_oauth_fetch(max_response_bytes=8)
    server.route("/fits", Reply(200, {}, b"12345678"))
    server.route("/large", Reply(200, {}, b"123456789"))
    assert fetch(f"{server.origin}/fits").body == b"12345678"
    with pytest.raises(McpProtocolError) as raised:
        fetch(f"{server.origin}/large")
    assert str(raised.value) == "OAuth response exceeds 8 bytes"
    assert not is_network_error(raised.value)


def test_errors_that_are_no_network_failure() -> None:
    for error in (McpAbortError(), McpTimeoutError(5), ValueError("x")):
        assert not is_network_error(error)


# -- cancelling and the time limit ----------------------------------------------


def test_a_set_cancel_event_sends_no_request(server: FakeOAuthServer) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(McpAbortError):
        oauth_fetch(f"{server.origin}/a", cancel_event=cancel)
    assert server.requests == []


def test_cancelling_ends_a_request_that_gets_no_answer(server: FakeOAuthServer) -> None:
    server.route("/a", Reply(hang=True))
    cancel = threading.Event()
    raised: list[BaseException] = []

    def call() -> None:
        try:
            oauth_fetch(f"{server.origin}/a", cancel_event=cancel)
        except McpAbortError as error:
            raised.append(error)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    _wait_until(lambda: server.paths == ["/a"])
    assert thread.is_alive()
    cancel.set()
    thread.join(_WAIT_SECONDS)
    assert not thread.is_alive()
    assert len(raised) == 1
    # The socket was shut down, so the request's thread ended with it.
    _wait_until(lambda: not _fetch_threads())


def test_the_time_limit_ends_a_request_that_gets_no_answer(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", Reply(hang=True))
    fetch = create_oauth_fetch(timeout_ms=100)
    started = time.monotonic()
    with pytest.raises(McpTimeoutError) as raised:
        fetch(f"{server.origin}/a")
    assert time.monotonic() - started < _WAIT_SECONDS
    assert raised.value.timeout_ms == 100
    assert not is_network_error(raised.value)
    _wait_until(lambda: not _fetch_threads())


@pytest.mark.parametrize("limit", ["cancel", "time"])
def test_a_body_that_stalls_is_ended_by_cancel_and_time_limit(
    server: FakeOAuthServer, limit: str
) -> None:
    # The response says its connection will close, after which http.client
    # no longer knows the socket the body is read from.
    server.route("/a", Reply(200, {}, b"part", content_length=50, stall=True))
    cancel = threading.Event()
    fetch = create_oauth_fetch(timeout_ms=100 if limit == "time" else None)
    raised: list[BaseException] = []

    def call() -> None:
        try:
            fetch(f"{server.origin}/a", cancel_event=cancel)
        except (McpAbortError, McpTimeoutError) as error:
            raised.append(error)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    _wait_until(lambda: server.paths == ["/a"])
    if limit == "cancel":
        time.sleep(0.1)
        cancel.set()
    thread.join(_WAIT_SECONDS)
    assert not thread.is_alive()
    expected = McpAbortError if limit == "cancel" else McpTimeoutError
    assert [type(error) for error in raised] == [expected]
    # The read was ended, although the server still holds the connection.
    _wait_until(lambda: not _fetch_threads())
    assert not server.stopped.is_set()


def test_a_time_limit_shorter_than_the_answer_takes_is_kept(
    server: FakeOAuthServer,
) -> None:
    def slow(request: OAuthRequest) -> Reply:
        time.sleep(0.03)
        return Reply(200, {}, b"late")

    server.route("/a", slow)
    for timeout_ms in (1, 10):
        with pytest.raises(McpTimeoutError):
            create_oauth_fetch(timeout_ms=timeout_ms)(f"{server.origin}/a")


def test_an_answer_seen_after_the_time_limit_does_not_count() -> None:
    # The request is done, but the waiting thread got to look only after the
    # limit passed.
    done = threading.Event()
    done.set()
    token = CancelToken()
    with pytest.raises(McpTimeoutError):
        fetch_module._await(done, token, None, 0)
    assert token.cancelled
    for timeout_ms in (None, _WAIT_SECONDS * 1000):
        token = CancelToken()
        fetch_module._await(done, token, None, timeout_ms)
        assert not token.cancelled


def test_a_cancel_wins_over_an_answer_that_arrived_with_it(
    server: FakeOAuthServer,
) -> None:
    cancel = threading.Event()

    def answer(request: OAuthRequest) -> Reply:
        cancel.set()
        return Reply(200, {}, b"ok")

    server.route("/a", answer)
    with pytest.raises(McpAbortError):
        oauth_fetch(f"{server.origin}/a", cancel_event=cancel)


def test_the_time_limit_covers_a_chain_of_redirects(server: FakeOAuthServer) -> None:
    def slow(request: OAuthRequest) -> Reply:
        time.sleep(0.03)
        return _redirect("/loop")

    server.route("/loop", slow)
    fetch = create_oauth_fetch(timeout_ms=150)
    with pytest.raises(McpTimeoutError):
        fetch(f"{server.origin}/loop")
    assert len(server.requests) <= MAX_REDIRECTS
    _wait_until(lambda: not _fetch_threads())


def test_an_answer_in_time_is_returned_whatever_the_limit(
    server: FakeOAuthServer,
) -> None:
    server.route("/a", Reply(200, {}, b"ok"))
    fetch = create_oauth_fetch(timeout_ms=_WAIT_SECONDS * 1000)
    assert fetch(f"{server.origin}/a", cancel_event=threading.Event()).text() == "ok"
