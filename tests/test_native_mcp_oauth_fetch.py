"""Tests for the OAuth library's default fetch, against the fake server."""

from __future__ import annotations

import gzip
import http.client
import socket
import sys
import threading
import time
import zlib
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


@pytest.mark.parametrize("status", [307, 308])
def test_a_request_with_a_body_is_sent_again_after_a_307_or_308(
    server: FakeOAuthServer, status: int
) -> None:
    server.route("/token", _redirect("/moved", status))
    server.route("/moved", Reply(200, {}, b"tokens"))
    response = oauth_fetch(
        f"{server.origin}/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=b"secret=1",
    )
    assert response.text() == "tokens"
    token, moved = server.requests
    for request in (token, moved):
        assert (request.method, request.body) == ("POST", b"secret=1")
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"


@pytest.mark.parametrize(
    ("status", "method", "follows_as"),
    [
        (301, "POST", "GET"),
        (302, "POST", "GET"),
        (303, "POST", "GET"),
        (303, "PUT", "GET"),
        (301, "PUT", "PUT"),
        (302, "DELETE", "DELETE"),
        (303, "HEAD", "HEAD"),
    ],
)
def test_a_redirect_that_changes_the_method_drops_the_body(
    server: FakeOAuthServer, status: int, method: str, follows_as: str
) -> None:
    server.route("/a", _redirect("/b", status))
    server.route("/b", Reply(200, {}, b"done"))
    body = None if method == "HEAD" else b"x=1"
    headers = {
        "Content-Type": "text/plain",
        "Content-Language": "en",
        "Content-Location": "/x",
        "Content-Encoding": "identity",
        "X-Kept": "1",
    }
    oauth_fetch(f"{server.origin}/a", method=method, headers=headers, body=body)
    first, second = server.requests
    assert (first.method, second.method) == (method, follows_as)
    assert second.headers["x-kept"] == "1"
    changed = follows_as != method
    assert second.body == (b"" if changed or body is None else body)
    for name in ("content-type", "content-language", "content-location"):
        assert (name in second.headers) is not changed
    assert ("content-encoding" in second.headers) is not changed


def test_a_body_follows_a_redirect_to_another_origin_without_credential_headers(
    server: FakeOAuthServer,
) -> None:
    with FakeOAuthServer() as other:
        other.route("/token", Reply(200, {}, b"tokens"))
        server.route("/token", _redirect(f"{other.origin}/token", 307))
        response = oauth_fetch(
            f"{server.origin}/token",
            method="POST",
            headers={"Authorization": "Basic x", "Host": "auth.example", "X-A": "1"},
            body=b"refresh_token=r1",
        )
        [there] = other.requests
    assert response.text() == "tokens"
    assert server.requests[0].headers["host"] == "auth.example"
    assert (there.method, there.body) == ("POST", b"refresh_token=r1")
    assert "authorization" not in there.headers
    assert there.headers["host"] == f"127.0.0.1:{other.port}"
    assert there.headers["x-a"] == "1"


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


def test_the_body_of_a_redirect_that_is_followed_is_not_waited_for(
    server: FakeOAuthServer,
) -> None:
    # As fetch, which goes on to the redirect's target at its headers: the
    # body announced here never arrives, and is never read.
    server.route(
        "/a", Reply(302, {"Location": "/b"}, b"", content_length=100, stall=True)
    )
    server.route("/b", Reply(200, {}, b"there"))
    fetch = create_oauth_fetch(timeout_ms=_WAIT_SECONDS * 1000)
    assert fetch(f"{server.origin}/a").text() == "there"
    # Nor is it limited: it is not read.
    fetch = create_oauth_fetch(max_response_bytes=4)
    server.route("/c", Reply(307, {"Location": "/d"}, b"more than four"))
    server.route("/d", Reply(200, {}, b"done"))
    assert fetch(f"{server.origin}/c").text() == "done"


# -- content codings ------------------------------------------------------------


def _deflate_raw(data: bytes) -> bytes:
    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return encoder.compress(data) + encoder.flush()


_DOCUMENT = b'{"issuer": "https://auth.example"}'


def _encoded(encoding: str, body: bytes, status: int = 200) -> Reply:
    return Reply(status, {"Content-Encoding": encoding}, body)


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("gzip", gzip.compress(_DOCUMENT)),
        ("x-gzip", gzip.compress(_DOCUMENT)),
        ("GZip", gzip.compress(_DOCUMENT)),
        ("deflate", zlib.compress(_DOCUMENT)),
        ("deflate", _deflate_raw(_DOCUMENT)),
        # The codings are undone from the last one to the first.
        ("deflate, gzip", gzip.compress(zlib.compress(_DOCUMENT))),
        (" gzip ,deflate", zlib.compress(gzip.compress(_DOCUMENT))),
        # Several gzip members are one body.
        ("gzip", gzip.compress(_DOCUMENT[:9]) + gzip.compress(_DOCUMENT[9:])),
    ],
    ids=[
        "gzip",
        "x-gzip",
        "gzip-mixed-case",
        "deflate-zlib",
        "deflate-raw",
        "deflate-then-gzip",
        "gzip-then-deflate",
        "gzip-multiple-members",
    ],
)
def test_a_compressed_answer_is_decoded(
    server: FakeOAuthServer, encoding: str, body: bytes
) -> None:
    server.route("/doc", _encoded(encoding, body))
    response = oauth_fetch(f"{server.origin}/doc")
    assert response.body == _DOCUMENT
    assert response.json() == {"issuer": "https://auth.example"}
    # The headers are the ones of the answer, as with fetch.
    assert response.header("content-encoding") == encoding.strip()


def test_a_request_asks_for_the_codings_it_can_decode(server: FakeOAuthServer) -> None:
    oauth_fetch(f"{server.origin}/a")
    oauth_fetch(f"{server.origin}/b", method="POST", body=b"x")
    oauth_fetch(f"{server.origin}/c", headers={"accept-encoding": "identity"})
    assert [request.headers["accept-encoding"] for request in server.requests] == [
        "gzip, deflate",
        "gzip, deflate",
        "identity",
    ]


def test_a_compressed_error_answer_is_decoded(server: FakeOAuthServer) -> None:
    server.route("/token", _encoded("gzip", gzip.compress(b'{"error": "x"}'), 400))
    response = oauth_fetch(f"{server.origin}/token", method="POST", body=b"a=1")
    assert (response.status, response.json()) == (400, {"error": "x"})


@pytest.mark.parametrize("encoding", ["br", "zstd", "identity", "gzip, br", "", ","])
def test_an_answer_with_another_coding_is_left_as_it_arrived(
    server: FakeOAuthServer, encoding: str
) -> None:
    body = gzip.compress(_DOCUMENT)
    server.route("/doc", _encoded(encoding, body))
    assert oauth_fetch(f"{server.origin}/doc").body == body


def test_a_compressed_body_that_ends_early_gives_what_it_held(
    server: FakeOAuthServer,
) -> None:
    whole = gzip.compress(b"a" * 50 + bytes(range(256)) * 4)
    server.route("/gzip", _encoded("gzip", whole[: len(whole) // 2]))
    server.route("/deflate", _encoded("deflate", zlib.compress(b"b" * 4000)[:-6]))
    assert oauth_fetch(f"{server.origin}/gzip").body.startswith(b"a" * 50)
    assert set(oauth_fetch(f"{server.origin}/deflate").body) == {ord("b")}
    server.route("/empty", _encoded("gzip", b""))
    assert oauth_fetch(f"{server.origin}/empty").body == b""


@pytest.mark.parametrize(
    "tail",
    [
        b"\x00" * 8,
        b"\x00\x00garbage",
        # The start of another member that ends early.
        b"\x1f",
        b"\x1f\x8b\x08",
    ],
)
def test_padding_and_a_cut_member_after_a_gzip_member_are_accepted(
    server: FakeOAuthServer, tail: bytes
) -> None:
    server.route("/doc", _encoded("gzip", gzip.compress(_DOCUMENT) + tail))
    assert oauth_fetch(f"{server.origin}/doc").body == _DOCUMENT


@pytest.mark.parametrize("tail", [b"garbage", b"\x1f\x8bxxxxxxxxxxxx"])
def test_other_data_after_a_gzip_member_is_a_network_failure(
    server: FakeOAuthServer, tail: bytes
) -> None:
    # As Node's fetch, which fails with "incorrect header check" and
    # "unknown compression method" there.
    server.route("/doc", _encoded("gzip", gzip.compress(_DOCUMENT) + tail))
    with pytest.raises(http.client.HTTPException, match="Cannot decode"):
        oauth_fetch(f"{server.origin}/doc")


@pytest.mark.parametrize("compress", [zlib.compress, _deflate_raw])
def test_data_after_a_deflate_stream_is_ignored(
    server: FakeOAuthServer, compress: Callable[[bytes], bytes]
) -> None:
    server.route("/doc", _encoded("deflate", compress(_DOCUMENT) + b"garbage"))
    assert oauth_fetch(f"{server.origin}/doc").body == _DOCUMENT


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "deflate, gzip"])
def test_data_that_is_not_in_the_coding_is_a_network_failure(
    server: FakeOAuthServer, encoding: str
) -> None:
    server.route("/doc", _encoded(encoding, b"\x08 this is not compressed data"))
    with pytest.raises(http.client.HTTPException, match="Cannot decode") as raised:
        oauth_fetch(f"{server.origin}/doc")
    assert is_network_error(raised.value)


def test_more_than_five_codings_are_a_network_failure(server: FakeOAuthServer) -> None:
    server.route("/five", _encoded(", ".join(["identity"] * 5), b"x"))
    server.route("/six", _encoded(", ".join(["gzip"] * 6), b"x"))
    assert oauth_fetch(f"{server.origin}/five").body == b"x"
    with pytest.raises(http.client.HTTPException, match="Too many content encodings"):
        oauth_fetch(f"{server.origin}/six")


@pytest.mark.parametrize("status", [204, 205, 304])
def test_an_answer_without_a_body_is_not_decoded(
    server: FakeOAuthServer, status: int
) -> None:
    server.route("/doc", _encoded("gzip", b"", status))
    assert oauth_fetch(f"{server.origin}/doc").status == status
    server.route("/head", _encoded("gzip", b"not gzip"))
    assert oauth_fetch(f"{server.origin}/head", method="HEAD").body == b""


def test_the_answer_of_a_redirect_that_is_followed_is_not_decoded(
    server: FakeOAuthServer,
) -> None:
    server.route(
        "/a", Reply(302, {"Location": "/b", "Content-Encoding": "gzip"}, b"not gzip")
    )
    server.route("/b", Reply(200, {}, b"done"))
    assert oauth_fetch(f"{server.origin}/a").text() == "done"
    # Without a `Location` the redirect status is the answer, and is decoded.
    server.route("/c", _encoded("gzip", gzip.compress(b"moved"), 302))
    assert oauth_fetch(f"{server.origin}/c").text() == "moved"


def test_the_size_limit_holds_for_a_decoded_body(server: FakeOAuthServer) -> None:
    fetch = create_oauth_fetch(max_response_bytes=1000)
    server.route("/fits", _encoded("gzip", gzip.compress(b"a" * 1000)))
    server.route("/bomb", _encoded("gzip", gzip.compress(b"a" * 1001)))
    server.route("/members", _encoded("gzip", gzip.compress(b"a" * 600) * 2))
    server.route(
        "/twice", _encoded("gzip, gzip", gzip.compress(gzip.compress(b"a" * 100_000)))
    )
    assert len(fetch(f"{server.origin}/fits").body) == 1000
    for path in ("/bomb", "/members", "/twice"):
        with pytest.raises(McpProtocolError, match="OAuth response exceeds 1000 bytes"):
            fetch(server.origin + path)


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


@pytest.mark.parametrize("status", [302, 404, 500, 502])
def test_with_discard_unless_ok_the_body_of_a_failure_is_not_waited_for(
    server: FakeOAuthServer, status: int
) -> None:
    # Pi's discovery cancels such a body (`discard`) and moves on. A 302
    # without a `Location` is the response, and no 2xx either.
    server.route(
        "/a",
        Reply(status, {"X-Test": "1"}, b"", content_length=100, stall=True),
    )
    fetch = create_oauth_fetch(timeout_ms=_WAIT_SECONDS * 1000)
    response = fetch(f"{server.origin}/a", discard_unless_ok=True)
    assert (response.status, response.header("x-test"), response.body) == (
        status,
        "1",
        b"",
    )
    # Neither decoded nor limited.
    server.route("/b", _encoded("gzip", b"not gzip", 404))
    fetch = create_oauth_fetch(max_response_bytes=2)
    response = fetch(f"{server.origin}/b", discard_unless_ok=True)
    assert (response.status, response.body) == (404, b"")


def test_with_discard_unless_ok_a_2xx_body_is_read(server: FakeOAuthServer) -> None:
    server.route("/a", _encoded("gzip", gzip.compress(b"document"), 203))
    response = oauth_fetch(f"{server.origin}/a", discard_unless_ok=True)
    assert (response.status, response.body) == (203, b"document")
    server.route("/b", Reply(200, {}, b"short", content_length=50))
    with pytest.raises(http.client.IncompleteRead):
        oauth_fetch(f"{server.origin}/b", discard_unless_ok=True)


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
