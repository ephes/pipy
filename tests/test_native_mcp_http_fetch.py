"""The fetch of the streamable HTTP transport: the option and the default one."""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.cancellation import CancelToken
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


def test_the_default_fetch_refuses_a_url_it_cannot_request() -> None:
    import http.client

    fetch: McpFetch = create_default_fetch()

    with pytest.raises(http.client.InvalidURL, match="ftp://example.com/mcp"):
        fetch(
            McpHttpRequest("GET", "ftp://example.com/mcp", {}, None, CancelToken(), 0)
        )
