"""The fetch of the streamable HTTP transport: the option and the default one."""

from __future__ import annotations

import dataclasses
import errno
import http.client
import json
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.mcp import http_fetch
from pipy_harness.native.mcp.client import McpClient, McpClientOptions
from pipy_harness.native.mcp.http import (
    McpHttpError,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from pipy_harness.native.mcp.http_fetch import (
    McpFetch,
    McpHttpRequest,
    McpHttpResponse,
    create_default_fetch,
)
from pipy_harness.native.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    McpConnectionClosedError,
    McpTimeoutError,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer  # noqa: E402

_WAIT_SECONDS = 10.0
_ECHO = {"name": "echo", "inputSchema": {"type": "object"}}
_HELLO = {"content": [{"type": "text", "text": "hello"}]}
_SCENARIO: dict[str, Any] = {
    "protocolVersion": LATEST_PROTOCOL_VERSION,
    "serverInfo": {"name": "http-fixture", "version": "1.0.0"},
    "sessionId": "session-1",
    "tools": [_ECHO],
    "toolResults": {"echo": _HELLO},
}


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _io_threads() -> list[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("mcp-http")
    ]


def _client() -> McpClient:
    return McpClient(McpClientOptions(name="http-test", version="1.0.0"))


@pytest.fixture
def server() -> Iterator[FakeMcpHttpServer]:
    with FakeMcpHttpServer(dict(_SCENARIO)) as fake:
        yield fake
    # No test leaves an I/O thread behind.
    _wait_until(lambda: not _io_threads())


class _Tokens:
    def token(self) -> str | None:
        return "t1"


# -- the fetch option ---------------------------------------------------------


def test_every_request_of_the_transport_goes_through_its_fetch(
    server: FakeMcpHttpServer,
) -> None:
    # Pi: `options.fetch ?? globalThis.fetch` sends every request, the GET
    # of the stream and the DELETE of the session too.
    sent: list[McpHttpRequest] = []
    default = create_default_fetch()

    def proxying(request: McpHttpRequest) -> McpHttpResponse:
        sent.append(request)
        # As a proxy would: the request goes somewhere else than its URL.
        return default(dataclasses.replace(request, url=server.url))

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(
            url="http://mcp.invalid/mcp?x=1",
            headers={"X-Api-Key": "k1"},
            auth_provider=_Tokens(),
            send_timeout_ms=5_000,
            fetch=proxying,
        )
    )
    client = _client()
    client.connect(transport)

    assert client.list_tools() == [_ECHO]
    _wait_until(lambda: [r.method for r in sent].count("GET") == 1)
    client.close()

    assert sorted(request.method for request in sent) == [
        "DELETE",
        "GET",
        "POST",
        "POST",
        "POST",
    ]
    assert {request.url for request in sent} == {"http://mcp.invalid/mcp?x=1"}
    for request in sent:
        assert request.headers["X-Api-Key"] == "k1"
        assert request.headers["Authorization"] == "Bearer t1"
        assert isinstance(request.cancel, CancelToken)
    posts = [request for request in sent if request.method == "POST"]
    assert [json.loads(request.body or b"")["method"] for request in posts] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert all(request.connect_timeout_ms == 5_000 for request in posts)
    (get,) = (request for request in sent if request.method == "GET")
    (delete,) = (request for request in sent if request.method == "DELETE")
    assert get.body is None and delete.body is None
    assert get.headers["Accept"] == "text/event-stream"
    assert delete.headers["Mcp-Session-Id"] == "session-1"
    assert delete.connect_timeout_ms == 1_000
    assert [request.path for request in server.requests] == ["/mcp"] * 5


class _Canned:
    """An answer that never was on a network."""

    def __init__(
        self, status: int, content_type: str | None = None, body: bytes = b""
    ) -> None:
        self.status = status
        self._content_type = content_type
        self._body = body
        self.closed = 0

    def header(self, name: str) -> str | None:
        return self._content_type if name.lower() == "content-type" else None

    def read(self, limit: int | None = None) -> bytes:
        data, self._body = (
            self._body[:limit],
            b"" if limit is None else self._body[limit:],
        )
        return data

    def chunks(self) -> Iterator[bytes]:
        while self._body:
            yield self.read(7)

    def close(self) -> None:
        self.closed += 1


def _answer(request: McpHttpRequest) -> dict[str, Any]:
    message = json.loads(request.body or b"")
    results: dict[str, Any] = {
        "initialize": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "canned", "version": "1"},
        },
        "tools/list": {"tools": [_ECHO]},
        "tools/call": _HELLO,
    }
    return {"jsonrpc": "2.0", "id": message["id"], "result": results[message["method"]]}


def test_a_fetch_needs_no_network() -> None:
    answers: list[_Canned] = []

    def canned(request: McpHttpRequest) -> McpHttpResponse:
        if request.method != "POST":
            answers.append(_Canned(405))
        elif b'"id"' not in (request.body or b""):
            answers.append(_Canned(202))
        elif b"tools/call" in (request.body or b""):
            event = f"data: {json.dumps(_answer(request))}\n\n".encode()
            answers.append(_Canned(200, "text/event-stream", event))
        else:
            body = json.dumps(_answer(request)).encode()
            answers.append(_Canned(200, "Application/JSON; charset=utf-8", body))
        return answers[-1]

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url="https://mcp.invalid/mcp", fetch=canned)
    )
    client = _client()
    client.connect(transport)

    assert client.list_tools() == [_ECHO]
    assert client.call_tool("echo") == _HELLO
    client.close()

    _wait_until(lambda: not _io_threads())
    # Every answer was closed, the 405 to the GET of the stream too.
    assert len(answers) == 5
    assert all(answer.closed >= 1 for answer in answers)


def test_a_fetch_decides_about_redirects() -> None:
    # The transport takes what its fetch returns as the answer.
    def redirecting(request: McpHttpRequest) -> McpHttpResponse:
        return _Canned(307)

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url="http://mcp.invalid/mcp", fetch=redirecting)
    )

    with pytest.raises(McpHttpError) as failure:
        _client().connect(transport)

    assert failure.value.status == 307


def test_what_a_fetch_raises_fails_the_request() -> None:
    def failing(request: McpHttpRequest) -> McpHttpResponse:
        raise ConnectionResetError("proxy went away")

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url="http://mcp.invalid/mcp", fetch=failing)
    )

    with pytest.raises(ConnectionResetError, match="proxy went away"):
        _client().connect(transport)


def test_a_fetch_that_is_falsy_is_still_the_fetch() -> None:
    class Fetch:
        def __len__(self) -> int:
            return 0

        def __call__(self, request: McpHttpRequest) -> McpHttpResponse:
            return _Canned(418)

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url="http://mcp.invalid/mcp", fetch=Fetch())
    )

    with pytest.raises(McpHttpError) as failure:
        _client().connect(transport)

    assert failure.value.status == 418


def test_closing_cancels_the_token_a_fetch_was_handed() -> None:
    waiting = threading.Event()
    ended: list[bool] = []

    def stalled(request: McpHttpRequest) -> McpHttpResponse:
        waiting.set()
        ended.append(request.cancel.event.wait(_WAIT_SECONDS))
        raise OSError("given up")

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url="http://mcp.invalid/mcp", fetch=stalled)
    )
    client = _client()
    raised: list[BaseException] = []

    def connect() -> None:
        try:
            client.connect(transport)
        except Exception as error:  # noqa: BLE001 - cross-thread capture for the assertion
            raised.append(error)

    thread = threading.Thread(target=connect, daemon=True)
    thread.start()
    assert waiting.wait(_WAIT_SECONDS)

    client.close()
    thread.join(_WAIT_SECONDS)

    _wait_until(lambda: not _io_threads())
    assert ended == [True]
    assert [type(error) for error in raised] == [McpConnectionClosedError]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Pi's transport reads its URL with `new URL`, a WHATWG parser.
        ("http://127.1:8080/mcp", ("http://127.0.0.1:8080/mcp", "127.0.0.1", 8080)),
        ("http://0x7f.0.0.1/a/../mcp", ("http://127.0.0.1/mcp", "127.0.0.1", 80)),
        ("https://2130706433/mcp?x#f", ("https://127.0.0.1/mcp?x#f", "127.0.0.1", 443)),
        ("http:\\\\Mcp.Example\\mcp", ("http://mcp.example/mcp", "mcp.example", 80)),
        ("http://@[0::1]:81/", ("http://[::1]:81/", "::1", 81)),
        # The request goes to the host's ASCII form, never fass.de.
        (
            "https://fa\u00df.de/mcp",
            ("https://xn--fa-hia.de/mcp", "xn--fa-hia.de", 443),
        ),
        # A lone surrogate is U+FFFD, as in a USVString.
        ("http://h/\ud800", ("http://h/\ufffd", "h", 80)),
    ],
)
def test_a_url_is_requested_as_a_whatwg_parser_reads_it(
    url: str, expected: tuple[str, str, int]
) -> None:
    target = http_fetch.parse_http_target(url)
    assert target is not None
    assert (target.url, target.host, target.port) == expected


def test_a_lone_surrogate_in_the_path_is_sent_as_a_replacement_character() -> None:
    target = http_fetch.parse_http_target("http://h/a\ud800?q=\udc00")
    assert target is not None
    assert target.path == "/a%EF%BF%BD?q=%EF%BF%BD"


@pytest.mark.parametrize(
    "url",
    [
        "http://user@h/",
        "http://:secret@h/",
        "http://h:0/",
        "http://256.1/",
        "http://a\u200cb.com/",
    ],
)
def test_a_url_no_request_can_go_to_is_no_target(url: str) -> None:
    assert http_fetch.parse_http_target(url) is None


def test_a_shortened_ipv4_url_reaches_its_server(server: FakeMcpHttpServer) -> None:
    port = server.origin.rpartition(":")[2]
    seen: list[str] = []
    default = create_default_fetch()

    def recording(request: McpHttpRequest) -> McpHttpResponse:
        seen.append(request.url)
        return default(request)

    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(
            url=f"http://127.1:{port}/x/../mcp",
            open_get_stream=False,
            fetch=recording,
        )
    )
    client = _client()
    client.connect(transport)

    assert client.list_tools() == [_ECHO]
    client.close()
    # The fetch is handed the URL as `new URL` writes it.
    assert set(seen) == {f"http://127.0.0.1:{port}/mcp"}
    assert server.requests[0].headers["host"] == f"127.0.0.1:{port}"


def test_the_default_fetch_refuses_a_url_it_cannot_request() -> None:
    fetch: McpFetch = create_default_fetch()

    with pytest.raises(http.client.InvalidURL, match="ftp://example.com/mcp"):
        fetch(
            McpHttpRequest("GET", "ftp://example.com/mcp", {}, None, CancelToken(), 0)
        )


# -- the default fetch: a connection attempt ends when the request does -------


class _SilentListener:
    """Accepts TCP connections and never answers on them."""

    def __init__(self) -> None:
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self.port: int = self._socket.getsockname()[1]
        self.accepted: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while True:
            try:
                connection, _address = self._socket.accept()
            except OSError:
                return
            self.accepted.append(connection)

    def peer_closed(self) -> bool:
        """Whether the one accepted connection was closed by the client."""

        (connection,) = self.accepted
        connection.setblocking(False)
        try:
            while connection.recv(65536):
                pass  # The client's hello.
        except BlockingIOError:
            return False
        except OSError:
            return True
        return True

    def close(self) -> None:
        self._socket.close()
        self._thread.join(_WAIT_SECONDS)
        for connection in self.accepted:
            connection.close()


@pytest.fixture
def silent() -> Iterator[_SilentListener]:
    listener = _SilentListener()
    try:
        yield listener
    finally:
        listener.close()
    _wait_until(lambda: not _io_threads())


def _connect_in_thread(
    client: McpClient, transport: StreamableHttpTransport
) -> tuple[threading.Thread, list[BaseException]]:
    raised: list[BaseException] = []

    def connect() -> None:
        try:
            client.connect(transport)
        except Exception as error:  # noqa: BLE001 - cross-thread capture for the assertion
            raised.append(error)

    thread = threading.Thread(target=connect, daemon=True)
    thread.start()
    return thread, raised


def test_closing_ends_a_tls_handshake_in_progress(silent: _SilentListener) -> None:
    # Pi: `close()` aborts the controller whose signal every fetch carries,
    # also a fetch that is still connecting.
    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url=f"https://127.0.0.1:{silent.port}/mcp")
    )
    client = _client()
    thread, raised = _connect_in_thread(client, transport)
    _wait_until(lambda: len(silent.accepted) == 1)
    assert _io_threads() == ["mcp-http-request"]

    started = time.monotonic()
    client.close()
    thread.join(_WAIT_SECONDS)
    _wait_until(lambda: not _io_threads())
    _wait_until(silent.peer_closed)

    # A connection attempt has no time limit of its own, as Pi's fetch has
    # none; the close ends it.
    assert time.monotonic() - started < 5
    assert [type(error) for error in raised] == [McpConnectionClosedError]


def test_a_request_timeout_ends_a_tls_handshake_in_progress(
    silent: _SilentListener,
) -> None:
    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url=f"https://127.0.0.1:{silent.port}/mcp")
    )
    client = McpClient(
        McpClientOptions(name="http-test", version="1.0.0", request_timeout_ms=100)
    )

    started = time.monotonic()
    with pytest.raises(McpTimeoutError):
        client.connect(transport)
    _wait_until(lambda: not _io_threads())
    _wait_until(silent.peer_closed)

    assert time.monotonic() - started < 5


def test_a_tls_handshake_is_given_the_connect_timeout(silent: _SilentListener) -> None:
    fetch = create_default_fetch()
    url = f"https://127.0.0.1:{silent.port}/mcp"

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        fetch(McpHttpRequest("GET", url, {}, None, CancelToken(), 100))

    assert 0.1 <= time.monotonic() - started < 5
    _wait_until(silent.peer_closed)


def test_a_cancelled_request_opens_no_connection(silent: _SilentListener) -> None:
    fetch = create_default_fetch()
    token = CancelToken()
    token.cancel()
    url = f"http://127.0.0.1:{silent.port}/mcp"

    with pytest.raises(ProviderCancelledError):
        fetch(McpHttpRequest("GET", url, {}, None, token, 0))

    time.sleep(0.05)
    assert silent.accepted == []


class _NeverConnected(socket.socket):
    """A socket whose connect never completes: it listens instead, and a
    listening socket is never ready for writing."""

    def connect_ex(self, address: Any) -> int:
        self.bind(("127.0.0.1", 0))
        self.listen()
        return errno.EINPROGRESS


def test_a_tcp_connect_in_progress_ends_when_it_is_woken_or_times_out() -> None:
    wake = http_fetch._ConnectWake()
    token = CancelToken()
    token.register(wake)
    address = ("127.0.0.1", 9)
    try:
        with _NeverConnected() as stalled:
            stalled.setblocking(False)
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                http_fetch._tcp_connect(stalled, address, wake, 0.1)
            assert 0.1 <= time.monotonic() - started < 5

        with _NeverConnected() as stalled:
            stalled.setblocking(False)
            timer = threading.Timer(0.05, token.cancel)
            timer.start()
            started = time.monotonic()
            with pytest.raises(ProviderCancelledError):
                http_fetch._tcp_connect(stalled, address, wake, None)
            assert time.monotonic() - started < 5
            timer.join()
    finally:
        wake.free()
    # A wake after that is harmless.
    wake.close()


def test_a_refused_tcp_connect_raises_the_error_of_the_socket() -> None:
    with socket.socket() as placeholder:
        placeholder.bind(("127.0.0.1", 0))
        address = placeholder.getsockname()
    wake = http_fetch._ConnectWake()
    try:
        with socket.socket() as sock:
            sock.setblocking(False)
            with pytest.raises(ConnectionRefusedError):
                http_fetch._tcp_connect(sock, address, wake, 5)
    finally:
        wake.free()


def test_an_address_family_without_sockets_is_one_more_address_that_failed(
    server: FakeMcpHttpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As `socket.create_connection`: the next address is tried.
    port = int(server.origin.rsplit(":", 1)[1])
    real_socket = socket.socket

    def addresses(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    def create(family: int = -1, *args: Any, **kwargs: Any) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "Address family not supported")
        return real_socket(family, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    monkeypatch.setattr(socket, "socket", create)
    fetch = create_default_fetch()

    response = fetch(McpHttpRequest("GET", server.url, {}, None, CancelToken(), 5_000))
    monkeypatch.undo()

    assert response.status == 405
    response.close()


def test_a_host_no_request_can_name_opens_no_socket(
    server: FakeMcpHttpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[object] = []
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: opened.append(args)
    )
    fetch = create_default_fetch()
    token = CancelToken()
    url = server.origin.replace("127.0.0.1", "127.0.0.1\x00") + "/mcp"

    with pytest.raises(http.client.InvalidURL):
        fetch(McpHttpRequest("GET", url, {}, None, token, 5_000))

    assert opened == []
    assert token._closeables == []
