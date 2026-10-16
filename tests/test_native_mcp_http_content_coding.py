"""Compressed answers on the streamable HTTP transport's default fetch.

Pi's transport sends its requests with Node's ``fetch``, which asks for
``gzip`` and ``deflate`` and decodes them, whole bodies and event streams.
"""

from __future__ import annotations

import gzip
import http.client
import http.server
import json
import threading
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.mcp.client import McpClient, McpClientOptions
from pipy_harness.native.mcp.content_coding import PIECE_BYTES, ContentDecoder
from pipy_harness.native.mcp.http import (
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from pipy_harness.native.mcp.http_fetch import McpHttpRequest, create_default_fetch
from pipy_harness.native.mcp.protocol import LATEST_PROTOCOL_VERSION

_WAIT_SECONDS = 10.0
_ECHO = {"name": "echo", "inputSchema": {"type": "object"}}
_HELLO = {"content": [{"type": "text", "text": "hello"}]}

# The parts of an answer: bytes are written as they are, an event is waited for.
Part = bytes | threading.Event


@dataclass
class _Answer:
    status: int
    headers: dict[str, str]
    parts: list[Part]


@dataclass
class _Server:
    """Answers each request with what ``answer`` makes of it."""

    answer: Callable[[str, dict[str, str], bytes], _Answer]
    requests: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                headers = {name.lower(): value for name, value in self.headers.items()}
                owner.requests.append(headers)
                answer = owner.answer(self.command, headers, body)
                self.send_response(answer.status)
                for name, value in answer.headers.items():
                    self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                for part in answer.parts:
                    if isinstance(part, threading.Event):
                        part.wait(_WAIT_SECONDS)
                    else:
                        self.wfile.write(part)
                        self.wfile.flush()
                self.close_connection = True

            do_GET = do_POST = do_DELETE = _handle  # noqa: N815 - names BaseHTTPRequestHandler calls

        self._http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}/mcp"

    def close(self) -> None:
        self._http.shutdown()
        self._http.server_close()


@pytest.fixture
def serve() -> Iterator[Callable[[Callable[..., _Answer]], _Server]]:
    servers: list[_Server] = []

    def start(answer: Callable[..., _Answer]) -> _Server:
        servers.append(_Server(answer))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def _fixed(
    body: bytes, encoding: str | None, status: int = 200, **headers: str
) -> Callable[..., _Answer]:
    sent = {"Content-Length": str(len(body)), **headers}
    if encoding is not None:
        sent["Content-Encoding"] = encoding
    return lambda *_request: _Answer(status, sent, [body])


def _get(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> Any:
    request = McpHttpRequest(method, url, headers or {}, None, CancelToken(), 5_000)
    return create_default_fetch()(request)


def _deflate_raw(data: bytes) -> bytes:
    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return encoder.compress(data) + encoder.flush()


_DOCUMENT = b'{"jsonrpc": "2.0", "id": 1, "result": {}}'


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("gzip", gzip.compress(_DOCUMENT)),
        ("X-Gzip", gzip.compress(_DOCUMENT)),
        ("deflate", zlib.compress(_DOCUMENT)),
        ("deflate", _deflate_raw(_DOCUMENT)),
        ("deflate, gzip", gzip.compress(zlib.compress(_DOCUMENT))),
        ("gzip", gzip.compress(_DOCUMENT[:9]) + gzip.compress(_DOCUMENT[9:])),
        ("gzip", gzip.compress(_DOCUMENT) + b"\x00garbage"),
        ("deflate", zlib.compress(_DOCUMENT) + b"garbage"),
    ],
)
def test_a_compressed_body_is_decoded(
    serve: Callable[..., _Server], encoding: str, body: bytes
) -> None:
    server = serve(_fixed(body, encoding))
    response = _get(server.url)
    try:
        assert response.read() == _DOCUMENT
        # The headers are the ones of the answer, as with fetch.
        assert response.header("content-encoding") == encoding
    finally:
        response.close()


def test_a_request_asks_for_the_codings_the_fetch_decodes(
    serve: Callable[..., _Server],
) -> None:
    server = serve(_fixed(b"", None))
    for headers in ({}, {"accept-encoding": "identity"}):
        _get(server.url, headers=headers).close()
    assert [request["accept-encoding"] for request in server.requests] == [
        "gzip, deflate",
        "identity",
    ]


def test_a_body_is_read_in_parts_after_decoding(serve: Callable[..., _Server]) -> None:
    whole = bytes(range(256)) * 1000
    server = serve(_fixed(gzip.compress(whole), "gzip"))
    response = _get(server.url)
    try:
        assert response.read(10) == whole[:10]
        assert response.read(300_000) == whole[10:300_000]
        assert b"".join(response.chunks()) == whole[300_000:]
        assert response.read() == b""
    finally:
        response.close()


def test_a_large_decoded_body_comes_in_bounded_pieces(
    serve: Callable[..., _Server],
) -> None:
    server = serve(_fixed(gzip.compress(b"a" * 5_000_000), "gzip"))
    response = _get(server.url)
    try:
        pieces = list(response.chunks())
    finally:
        response.close()
    assert sum(map(len, pieces)) == 5_000_000
    assert max(map(len, pieces)) <= PIECE_BYTES


@pytest.mark.parametrize("encoding", ["br", "zstd", "identity", "gzip, br"])
def test_another_coding_is_left_as_it_arrived(
    serve: Callable[..., _Server], encoding: str
) -> None:
    body = gzip.compress(_DOCUMENT)
    server = serve(_fixed(body, encoding))
    response = _get(server.url)
    try:
        assert response.read() == body
    finally:
        response.close()


@pytest.mark.parametrize("read", ["read", "chunks"])
def test_data_not_in_the_coding_is_a_network_failure(
    serve: Callable[..., _Server], read: str
) -> None:
    server = serve(_fixed(b"\x08 this is not compressed", "gzip"))
    response = _get(server.url)
    try:
        with pytest.raises(http.client.HTTPException, match="Cannot decode the gzip"):
            response.read() if read == "read" else list(response.chunks())
    finally:
        response.close()


def test_more_than_five_codings_are_a_network_failure(
    serve: Callable[..., _Server],
) -> None:
    server = serve(_fixed(b"x", ", ".join(["gzip"] * 6)))
    with pytest.raises(http.client.HTTPException, match="Too many content encodings"):
        _get(server.url)


@pytest.mark.parametrize("status", [204, 304])
def test_an_answer_without_a_body_is_not_decoded(
    serve: Callable[..., _Server], status: int
) -> None:
    server = serve(lambda *_request: _Answer(status, {"Content-Encoding": "gzip"}, []))
    response = _get(server.url)
    try:
        assert (response.status, response.read()) == (status, b"")
    finally:
        response.close()


@pytest.mark.parametrize("encoding", ["gzip", None])
@pytest.mark.parametrize("read", ["read", "chunks"])
def test_a_cut_body_with_a_length_is_a_network_failure(
    serve: Callable[..., _Server], encoding: str | None, read: str
) -> None:
    # Node's fetch fails a body that ends before its `Content-Length` with a
    # `TypeError`, also a body read as a stream; `read1` of `http.client`
    # only closes the connection.
    body = _DOCUMENT if encoding is None else gzip.compress(_DOCUMENT)
    headers = {"Content-Length": str(len(body) + 10)}
    if encoding is not None:
        headers["Content-Encoding"] = encoding
    server = serve(lambda *_request: _Answer(200, headers, [body]))
    response = _get(server.url)
    received = bytearray()
    try:
        with pytest.raises(http.client.IncompleteRead):
            if read == "read":
                response.read()
            for piece in response.chunks():
                received += piece
    finally:
        response.close()
    # What arrived is handed on first.
    assert bytes(received) == (b"" if read == "read" else _DOCUMENT)


def test_a_compressed_event_stream_is_decoded_as_it_arrives(
    serve: Callable[..., _Server],
) -> None:
    encoder = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
    first = encoder.compress(b"data: one\n\n") + encoder.flush(zlib.Z_SYNC_FLUSH)
    rest = encoder.compress(b"data: two\n\n") + encoder.flush()
    later = threading.Event()
    server = serve(
        lambda *_request: _Answer(
            200,
            {"Content-Type": "text/event-stream", "Content-Encoding": "gzip"},
            [first, later, rest],
        )
    )
    response = _get(server.url)
    try:
        pieces = response.chunks()
        # The first event is decoded before the rest of the stream is sent.
        assert next(pieces) == b"data: one\n\n"
        later.set()
        assert b"".join(pieces) == b"data: two\n\n"
    finally:
        later.set()
        response.close()


def test_the_decoder_hands_on_a_gzip_member_split_anywhere() -> None:
    body = gzip.compress(_DOCUMENT) + gzip.compress(b"!") + b"\x00pad"
    for cut in range(len(body) + 1):
        decoder = ContentDecoder(["gzip"])
        decoded = b"".join(decoder.feed(body[:cut])) + b"".join(
            decoder.feed(body[cut:])
        )
        assert decoded == _DOCUMENT + b"!", cut


# -- through the transport ------------------------------------------------------


def _mcp_answer(method: str, headers: dict[str, str], body: bytes) -> _Answer:
    if method != "POST":
        return _Answer(405, {"Content-Length": "0"}, [])
    message = json.loads(body)
    if "id" not in message:
        return _Answer(202, {"Content-Length": "0"}, [])
    results: dict[str, Any] = {
        "initialize": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gzip", "version": "1"},
        },
        "tools/list": {"tools": [_ECHO]},
        "tools/call": _HELLO,
    }
    answer = {
        "jsonrpc": "2.0",
        "id": message["id"],
        "result": results[message["method"]],
    }
    if message["method"] == "tools/call":
        event = f"data: {json.dumps(answer)}\n\n".encode()
        return _Answer(
            200,
            {"Content-Type": "text/event-stream", "Content-Encoding": "gzip"},
            [gzip.compress(event)],
        )
    encoded = zlib.compress(json.dumps(answer).encode())
    return _Answer(
        200,
        {
            "Content-Type": "application/json",
            "Content-Encoding": "deflate",
            "Content-Length": str(len(encoded)),
        },
        [encoded],
    )


def test_the_transport_reads_compressed_json_and_event_streams(
    serve: Callable[..., _Server],
) -> None:
    server = serve(_mcp_answer)
    client = McpClient(McpClientOptions(name="gzip-test", version="1.0.0"))
    client.connect(
        StreamableHttpTransport(StreamableHttpTransportOptions(url=server.url))
    )
    try:
        assert client.list_tools() == [_ECHO]
        assert client.call_tool("echo") == _HELLO
    finally:
        client.close()
    assert {request["accept-encoding"] for request in server.requests} == {
        "gzip, deflate"
    }
