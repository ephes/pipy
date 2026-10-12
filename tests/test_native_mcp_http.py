"""Tests for the streamable HTTP transport, against the scripted fake server.

Tests that mirror a case of Pi's ``packages/mcp/test/streamable-http.test.ts``
name it in a ``Pi:`` comment. The case about the auth provider's 401 handling
belongs to a later slice.

Transports made by the fixture do not open the server-to-client GET stream
unless a test asks for it with ``open_get_stream=True``, so that tests which
count requests, tokens or threads see only their own.
"""

from __future__ import annotations

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

from pipy_harness.native.mcp import http as http_module
from pipy_harness.native.mcp.client import (
    McpClient,
    McpClientOptions,
    McpRequestOptions,
)
from pipy_harness.native.mcp.http import (
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    StreamableHttpReconnectOptions,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from pipy_harness.native.mcp.protocol import (
    INTERNAL_ERROR,
    LATEST_PROTOCOL_VERSION,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpProtocolError,
    McpTimeoutError,
)
from pipy_harness.native.mcp.transport import McpTransport

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer, RecordedRequest  # noqa: E402

_WAIT_SECONDS = 10.0
_ECHO = {"name": "echo", "inputSchema": {"type": "object"}}
_HELLO = {"content": [{"type": "text", "text": "hello"}]}
# The answer of Pi's test server to `tools/call`: an event stream whose one
# event carries an id.
_SSE_ANSWER = [{"mode": "sse"}, {"sse": "id: tool-result\n"}, {"default": True}]


def _scenario(**overrides: Any) -> dict[str, Any]:
    return {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "serverInfo": {"name": "http-fixture", "version": "1.0.0"},
        "sessionId": "session-1",
        "tools": [_ECHO],
        "toolResults": {"echo": _HELLO},
        **overrides,
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


def _in_thread(
    call: Callable[[], object],
) -> tuple[threading.Thread, list[object], list[BaseException]]:
    """Run ``call`` on a thread; collect what it returns or raises."""

    results: list[object] = []
    raised: list[BaseException] = []

    def run() -> None:
        try:
            results.append(call())
        except Exception as error:  # noqa: BLE001 - cross-thread capture for the assertion
            raised.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, raised


class _Fixture:
    """Starts fake servers and clients, and closes them after the test."""

    def __init__(self) -> None:
        self.servers: list[FakeMcpHttpServer] = []
        self.clients: list[McpClient] = []
        self.transports: list[StreamableHttpTransport] = []

    def server(self, path: str = "/mcp", **scenario: Any) -> FakeMcpHttpServer:
        self.servers.append(FakeMcpHttpServer(_scenario(**scenario), path=path))
        return self.servers[-1]

    def transport(
        self, server: FakeMcpHttpServer, **options: Any
    ) -> StreamableHttpTransport:
        options.setdefault("open_get_stream", False)
        transport = StreamableHttpTransport(
            StreamableHttpTransportOptions(url=server.url, **options)
        )
        self.transports.append(transport)
        return transport

    def client(self, **options: Any) -> McpClient:
        values: dict[str, Any] = {"name": "http-test", "version": "1.0.0", **options}
        self.clients.append(McpClient(McpClientOptions(**values)))
        return self.clients[-1]

    def connect(self, server: FakeMcpHttpServer, **options: Any) -> McpClient:
        client = self.client()
        client.connect(self.transport(server, **options))
        return client

    def close(self) -> None:
        for client in self.clients:
            client.close()
        for transport in self.transports:
            transport.close()
        for server in self.servers:
            server.close()


@pytest.fixture
def fake() -> Iterator[_Fixture]:
    fixture = _Fixture()
    try:
        yield fixture
    finally:
        fixture.close()
    # No test leaves an I/O thread behind.
    _wait_until(lambda: not _io_threads())


def _gets(server: FakeMcpHttpServer) -> list[RecordedRequest]:
    return [request for request in server.requests if request.method == "GET"]


def _post_headers(server: FakeMcpHttpServer, method: str) -> dict[str, str]:
    (request,) = server.messages(method)
    return request.headers


# -- requests and answers ----------------------------------------------------


def test_handles_json_and_sse_responses_with_session_and_protocol_headers(
    fake: _Fixture,
) -> None:
    # Pi: "handles JSON and SSE responses with session and protocol headers"
    # (its GET is checked in the tests of the server-to-client stream)
    server = fake.server(methods={"tools/call": _SSE_ANSWER})
    transport = fake.transport(server)
    client = fake.client()

    client.connect(transport)

    assert transport.session_id == "session-1"
    assert client.list_tools() == [_ECHO]
    assert client.call_tool("echo", {"text": "hello"}) == _HELLO
    client.close()

    initialize = _post_headers(server, "initialize")
    assert "mcp-session-id" not in initialize
    assert "mcp-protocol-version" not in initialize
    for method in ("notifications/initialized", "tools/list", "tools/call"):
        headers = _post_headers(server, method)
        assert headers["mcp-session-id"] == "session-1"
        assert headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION
    deletes = [request for request in server.requests if request.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].path == "/mcp"
    assert deletes[0].headers["mcp-session-id"] == "session-1"
    assert deletes[0].headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION


def test_every_message_is_a_post_with_accept_json_body_and_configured_headers(
    fake: _Fixture,
) -> None:
    server = fake.server()
    client = fake.connect(
        server, headers={"X-Api-Key": "k1", "accept": "text/plain", "User-Agent": "t"}
    )

    client.list_tools()
    client.notify("notifications/roots/list_changed")

    posts = [request for request in server.requests if request.method == "POST"]
    assert [request.message["method"] for request in posts if request.message] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "notifications/roots/list_changed",
    ]
    for request in posts:
        assert request.path == "/mcp"
        # The transport's own headers replace configured ones of the same
        # name, whatever their case.
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["x-api-key"] == "k1"
        assert request.headers["user-agent"] == "t"
        assert "authorization" not in request.headers
    assert posts[2].message == {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


class _Tokens:
    """An auth provider that hands out ``token-1``, ``token-2`` and so on."""

    def __init__(self) -> None:
        self.calls = 0
        self.threads: list[str] = []

    def token(self) -> str | None:
        self.calls += 1
        self.threads.append(threading.current_thread().name)
        return f"token-{self.calls}"


class _NoToken:
    def token(self) -> str | None:
        return None


def test_the_auth_provider_token_replaces_a_configured_authorization_header(
    fake: _Fixture,
) -> None:
    server = fake.server()
    tokens = _Tokens()

    client = fake.connect(
        server, headers={"authorization": "Basic abc"}, auth_provider=tokens
    )
    client.list_tools()

    sent = [
        request.headers["authorization"]
        for request in server.requests
        if request.method == "POST"
    ]
    assert sent == ["Bearer token-1", "Bearer token-2", "Bearer token-3"]
    # The provider may use the network, so it is asked on I/O threads only.
    assert all(name.startswith("mcp-http") for name in tokens.threads)


def test_a_provider_without_a_token_keeps_the_configured_authorization_header(
    fake: _Fixture,
) -> None:
    server = fake.server()

    fake.connect(
        server, headers={"Authorization": "Basic abc"}, auth_provider=_NoToken()
    )

    assert _post_headers(server, "initialize")["authorization"] == "Basic abc"


def test_a_failing_auth_provider_fails_the_request(fake: _Fixture) -> None:
    class Broken:
        def token(self) -> str | None:
            raise RuntimeError("no token store")

    server = fake.server()

    with pytest.raises(RuntimeError, match="no token store"):
        fake.connect(server, auth_provider=Broken())
    assert server.requests == []


def test_a_json_array_delivers_every_message(fake: _Fixture) -> None:
    notification = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    server = fake.server(
        methods={"tools/list": [{"send": notification}, {"default": True}]}
    )
    client = fake.connect(server)
    changes: list[object] = []
    client.on_notification("notifications/tools/list_changed", changes.append)

    assert client.list_tools() == [_ECHO]
    assert changes == [None]


def test_an_event_stream_delivers_every_message_and_skips_what_is_none(
    fake: _Fixture,
) -> None:
    stream = (
        ": comment\n\n"
        "event: ping\ndata: {}\n\n"
        "data:  \n\n"
        "data: not json\n\n"
        'data: {"jsonrpc":"2.0"}\n\n'
        'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/x"}\n\n'
    )
    server = fake.server(
        toolSteps={
            "echo": [
                {"sse": stream},
                {"mode": "sse"},
                {"progress": {"progress": 1}},
                {"default": True},
                # Sent after the answer: the stream is read to its end.
                {"send": {"jsonrpc": "2.0", "method": "notifications/x"}},
            ]
        }
    )
    client = fake.connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    seen: list[object] = []
    progress: list[object] = []
    client.on_notification("notifications/x", seen.append)

    result = client.call_tool(
        "echo", None, McpRequestOptions(on_progress=progress.append)
    )

    assert result == _HELLO
    assert progress == [{"progressToken": 2, "progress": 1}]
    _wait_until(lambda: len(seen) == 2)
    # Event data that is no JSON-RPC message is reported, nothing else is.
    assert [type(error) for error in errors] == [json.JSONDecodeError, McpError]
    assert str(errors[1]) == "Invalid JSON-RPC message"


def test_a_server_request_on_a_response_stream_is_answered_with_a_post(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={
            "echo": [
                {"mode": "sse"},
                {"send": {"jsonrpc": "2.0", "id": "s1", "method": "ping"}},
                {"wait": "answer"},
                {"default": True},
            ]
        }
    )
    client = fake.connect(server)

    thread, results, raised = _in_thread(lambda: client.call_tool("echo"))
    answer = {"jsonrpc": "2.0", "id": "s1", "result": {}}
    _wait_until(lambda: any(r.message == answer for r in server.requests))
    server.release("answer")
    thread.join(_WAIT_SECONDS)

    assert (results, raised) == ([_HELLO], [])


def test_a_stream_that_stays_open_after_the_answer_does_not_hold_the_call(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={"echo": [{"mode": "sse"}, {"default": True}, {"hold": True}]}
    )
    client = fake.connect(server)

    assert client.call_tool("echo") == _HELLO

    # The stream is still being read, until the transport closes.
    assert _io_threads() == ["mcp-http-request"]
    client.close()
    _wait_until(lambda: not _io_threads())


# -- failures of one request -------------------------------------------------


def test_fails_only_the_request_whose_sse_stream_breaks(fake: _Fixture) -> None:
    # Pi: "fails only the request whose SSE stream breaks"
    server = fake.server(
        toolSteps={
            "slow": [{"wait": "slow"}, *_SSE_ANSWER],
            "broken": [{"sse": "data: not json\n\n"}],
        },
        tools=[_ECHO, {**_ECHO, "name": "slow"}, {**_ECHO, "name": "broken"}],
        toolResults={"slow": _HELLO},
    )
    client = fake.connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)

    thread, results, raised = _in_thread(lambda: client.call_tool("slow"))
    _wait_until(lambda: server.events == ["wait:slow"])
    with pytest.raises(McpError, match="MCP response stream failed") as broken:
        client.call_tool("broken")
    server.release("slow")
    thread.join(_WAIT_SECONDS)

    assert broken.value.code == INTERNAL_ERROR
    assert (results, raised) == ([_HELLO], [])
    assert len(errors) == 1


def test_fails_a_request_whose_response_stream_ends_without_an_answer(
    fake: _Fixture,
) -> None:
    # Pi: "fails a request whose response stream ends without an answer"
    server = fake.server(toolSteps={"echo": [{"sse": ": nothing here\n\n"}]})
    client = fake.connect(server)

    with pytest.raises(McpError) as failure:
        client.call_tool("echo", {}, McpRequestOptions(timeout_ms=5_000))

    assert str(failure.value) == (
        "MCP response stream failed: stream ended without a response"
    )
    # Without event ids there is nothing to resume from.
    assert _gets(server) == []
    assert client.list_tools() == [_ECHO]


def test_fails_a_request_whose_response_stream_is_cut(fake: _Fixture) -> None:
    server = fake.server(toolSteps={"echo": [{"sse": ": open\n\n"}, {"drop": True}]})
    client = fake.connect(server)

    with pytest.raises(McpError, match="^MCP response stream failed: ") as failure:
        client.call_tool("echo")

    assert "stream ended without a response" not in str(failure.value)
    assert _gets(server) == []
    assert client.list_tools() == [_ECHO]


def test_an_event_over_the_message_limit_fails_its_request(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": "data: " + "x" * 600 + "\n"}, {"hold": True}]}
    )
    client = fake.connect(server, max_message_bytes=512)

    with pytest.raises(McpError) as failure:
        client.call_tool("echo")

    assert str(failure.value) == (
        "MCP response stream failed: MCP SSE event exceeds 512 bytes"
    )


@pytest.mark.parametrize("status", [202, 204])
def test_rejects_a_request_the_server_accepts_without_a_response(
    fake: _Fixture, status: int
) -> None:
    # Pi: "rejects a request the server accepts without a response"
    server = fake.server(toolSteps={"echo": [{"status": status}]})
    client = fake.connect(server)

    with pytest.raises(McpHttpError) as failure:
        client.call_tool("echo")

    assert failure.value.status == status
    assert str(failure.value) == (
        "MCP server accepted request tools/call without a response"
    )
    # No cancellation follows a request that failed this way.
    client.ping()
    assert server.messages("notifications/cancelled") == []


def test_notifications_and_responses_ignore_the_response_body(fake: _Fixture) -> None:
    junk = {"status": 200, "headers": {"Content-Type": "text/plain"}, "body": "junk"}
    server = fake.server(
        methods={"notifications/initialized": [junk], "notifications/x": [{"hold": 1}]}
    )

    client = fake.connect(server, send_timeout_ms=200)

    assert client.list_tools() == [_ECHO]
    # A notification is sent when its POST was accepted; one the server does
    # not answer in time fails at its call.
    started = time.monotonic()
    with pytest.raises(McpTimeoutError, match="after 200ms"):
        client.notify("notifications/x")
    assert time.monotonic() - started < 5
    _wait_until(lambda: not _io_threads())


@pytest.mark.parametrize(
    ("headers", "shown"),
    [
        ({"Content-Type": "text/plain; charset=utf-8"}, "text/plain"),
        ({"Content-Type": "TEXT/HTML"}, "text/html"),
        ({}, "missing"),
    ],
)
def test_rejects_another_content_type(
    fake: _Fixture, headers: dict[str, str], shown: str
) -> None:
    server = fake.server(
        toolSteps={"echo": [{"status": 200, "headers": headers, "body": "{}"}]}
    )
    client = fake.connect(server)

    with pytest.raises(McpHttpError) as failure:
        client.call_tool("echo")

    assert failure.value.status == 200
    assert str(failure.value) == f"Unsupported MCP response content type: {shown}"


def test_the_content_type_of_an_answer_may_carry_parameters(fake: _Fixture) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 2, "result": _HELLO})
    headers = {"Content-Type": "Application/JSON; charset=utf-8"}
    server = fake.server(
        toolSteps={"echo": [{"status": 200, "headers": headers, "body": body}]}
    )
    client = fake.connect(server)

    assert client.call_tool("echo") == _HELLO


@pytest.mark.parametrize(
    ("body", "error", "message"),
    [
        ("not json", json.JSONDecodeError, "Expecting value"),
        ('{"jsonrpc":"2.0","id":2,"result":NaN}', ValueError, "Invalid JSON constant"),
        ('{"id":2,"result":{}}', McpError, "Invalid JSON-RPC message"),
        (
            '{"jsonrpc":"2.0","id":2,"result":"' + "x" * 600 + '"}',
            McpProtocolError,
            "MCP HTTP message exceeds 512 bytes",
        ),
    ],
)
def test_a_json_body_that_is_no_message_fails_the_request(
    fake: _Fixture, body: str, error: type[Exception], message: str
) -> None:
    headers = {"Content-Type": "application/json"}
    server = fake.server(
        toolSteps={"echo": [{"status": 200, "headers": headers, "body": body}]}
    )
    client = fake.connect(server, max_message_bytes=512)

    with pytest.raises(error, match=message):
        client.call_tool("echo")
    assert client.list_tools() == [_ECHO]


def test_a_json_body_shorter_than_announced_fails_the_request(fake: _Fixture) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 2, "result": _HELLO})
    step = {
        "status": 200,
        "headers": {"Content-Type": "application/json"},
        "body": body,
        "contentLength": len(body) + 100,
    }
    server = fake.server(toolSteps={"echo": [step]})
    client = fake.connect(server)

    with pytest.raises(http.client.IncompleteRead):
        client.call_tool("echo")
    assert client.list_tools() == [_ECHO]


# -- failure statuses --------------------------------------------------------


def test_classifies_authentication_failures(fake: _Fixture) -> None:
    # Pi: "classifies authentication failures"
    challenge = 'Bearer resource_metadata="https://example.com/meta"'
    server = fake.server(
        methods={
            "initialize": [
                {
                    "status": 401,
                    "headers": {"WWW-Authenticate": challenge},
                    "body": "login required",
                }
            ]
        }
    )
    client = fake.client()

    with pytest.raises(McpAuthRequiredError) as failure:
        client.connect(fake.transport(server))

    assert failure.value.status == 401
    assert failure.value.body == "login required"
    assert failure.value.www_authenticate == challenge
    assert str(failure.value) == "MCP server requires authentication"
    assert client.connection_state == "closed"


def test_an_authentication_failure_without_a_challenge(fake: _Fixture) -> None:
    server = fake.server(toolSteps={"echo": [{"status": 401}]})
    client = fake.connect(server)

    with pytest.raises(McpAuthRequiredError) as failure:
        client.call_tool("echo")

    assert failure.value.www_authenticate is None
    assert failure.value.body == ""


def test_includes_the_response_body_in_http_errors(fake: _Fixture) -> None:
    # Pi: "includes the response body in HTTP errors"
    server = fake.server(
        methods={"initialize": [{"status": 400, "body": "Invalid Accept header"}]}
    )

    with pytest.raises(McpHttpError) as failure:
        fake.connect(server)

    assert str(failure.value) == (
        "MCP HTTP request failed with status 400: Invalid Accept header"
    )
    assert failure.value.status == 400
    assert failure.value.body == "Invalid Accept header"
    assert type(failure.value) is McpHttpError


def test_an_http_error_shows_500_characters_of_at_most_8_kib_of_body(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={
            "long": [{"status": 500, "body": "  " + "é" * 20_000}],
            "edge": [{"status": 500, "body": "y" * 500 + "\n"}],
            "empty": [{"status": 503, "body": " \n"}],
        }
    )
    client = fake.connect(server)

    with pytest.raises(McpHttpError) as long:
        client.call_tool("long")
    with pytest.raises(McpHttpError) as edge:
        client.call_tool("edge")
    with pytest.raises(McpHttpError) as empty:
        client.call_tool("empty")

    # 8 KiB are read: two spaces and 4095 two-byte characters.
    assert long.value.body == "  " + "é" * 4095
    assert str(long.value) == (
        "MCP HTTP request failed with status 500: " + "é" * 497 + "..."
    )
    assert str(edge.value) == "MCP HTTP request failed with status 500: " + "y" * 500
    assert str(empty.value) == "MCP HTTP request failed with status 503"
    assert empty.value.body == " \n"


def test_classifies_an_expired_established_session(fake: _Fixture) -> None:
    # Pi: "classifies an expired established session"
    server = fake.server(methods={"tools/list": [{"status": 404, "body": "gone"}]})
    client = fake.connect(server)

    with pytest.raises(McpSessionExpiredError) as failure:
        client.list_tools()

    assert failure.value.status == 404
    assert failure.value.body == "gone"
    assert str(failure.value) == "MCP session expired"


def test_a_404_without_a_session_is_a_plain_http_error(fake: _Fixture) -> None:
    server = fake.server(
        sessionId=None, methods={"tools/list": [{"status": 404, "body": "gone"}]}
    )
    del server.scenario["sessionId"]
    client = fake.connect(server)

    with pytest.raises(McpHttpError) as failure:
        client.list_tools()

    assert type(failure.value) is McpHttpError
    assert str(failure.value) == "MCP HTTP request failed with status 404: gone"


def test_a_refused_connection_fails_the_request(fake: _Fixture) -> None:
    with socket.socket() as placeholder:
        placeholder.bind(("127.0.0.1", 0))
        port = placeholder.getsockname()[1]
    # Nothing listens on the port any more.
    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url=f"http://127.0.0.1:{port}/mcp")
    )
    client = fake.client()

    with pytest.raises(ConnectionRefusedError):
        client.connect(transport)

    assert client.connection_state == "closed"


# -- redirects ---------------------------------------------------------------


def test_follows_a_redirect_within_the_origin_with_method_and_body(
    fake: _Fixture,
) -> None:
    server = fake.server(
        path="/old",
        paths={"/old": [{"status": 307, "headers": {"Location": "/mcp/"}}]},
    )
    client = fake.connect(server, headers={"X-Api-Key": "k1"})

    assert client.list_tools() == [_ECHO]

    posts = [(r.path, r.message["method"]) for r in server.requests if r.message]
    assert posts[:2] == [("/old", "initialize"), ("/mcp/", "initialize")]
    followed = [r for r in server.requests if r.path == "/mcp/"]
    assert all(r.headers["x-api-key"] == "k1" for r in followed)
    assert followed[-1].headers["mcp-session-id"] == "session-1"


def test_does_not_follow_a_redirect_to_another_origin(fake: _Fixture) -> None:
    other = fake.server()
    # Same server, other host name: another origin.
    location = other.url.replace("127.0.0.1", "localhost")
    server = fake.server(
        path="/old",
        paths={"/old": [{"status": 308, "headers": {"Location": location}}]},
    )

    with pytest.raises(McpHttpError) as failure:
        fake.connect(server, headers={"X-Api-Key": "k1"})

    assert failure.value.status == 308
    assert str(failure.value) == "MCP HTTP request failed with status 308"
    assert other.requests == []


def test_a_301_turns_a_post_into_a_get_as_fetch_does(fake: _Fixture) -> None:
    server = fake.server(
        path="/old",
        paths={"/old": [{"status": 301, "headers": {"Location": "mcp"}}]},
    )

    with pytest.raises(McpHttpError) as failure:
        fake.connect(server)

    # The fake server answers a GET with 405.
    assert failure.value.status == 405
    get = server.requests[-1]
    assert (get.method, get.path) == ("GET", "/mcp")
    assert "content-type" not in get.headers


def test_gives_up_on_a_redirect_loop(fake: _Fixture) -> None:
    server = fake.server(
        path="/loop",
        paths={"/loop": [{"status": 307, "headers": {"Location": "/loop"}}]},
    )

    with pytest.raises(McpHttpError) as failure:
        fake.connect(server)

    assert failure.value.status == 307
    assert len(server.requests) == http_module.MAX_REDIRECTS + 1


# -- timeout, cancel and close ----------------------------------------------


def test_a_response_that_stalls_mid_body_times_out_and_its_thread_ends(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": 'data: {"jsonrpc"'}, {"hold": True}]}
    )
    client = fake.connect(server)

    started = time.monotonic()
    with pytest.raises(McpTimeoutError, match="after 200ms"):
        client.call_tool("echo", None, McpRequestOptions(timeout_ms=200))
    elapsed = time.monotonic() - started

    assert 0.2 <= elapsed < 5
    # The cancellation the client sends ends the request's I/O thread and
    # still reaches the server.
    _wait_until(lambda: len(server.messages("notifications/cancelled")) == 1)
    _wait_until(lambda: not _io_threads())
    (cancelled,) = server.messages("notifications/cancelled")
    assert cancelled.message is not None
    assert cancelled.message["params"] == {
        "requestId": 2,
        "reason": "Request timed out",
    }
    assert client.list_tools() == [_ECHO]


def test_cancel_during_a_stalled_read_returns_at_once_and_leaves_no_thread(
    fake: _Fixture,
) -> None:
    # The server holds the POST without answering its headers.
    server = fake.server(toolSteps={"echo": [{"wait": "never"}]})
    client = fake.connect(server)
    cancel = threading.Event()

    thread, results, raised = _in_thread(
        lambda: client.call_tool("echo", None, McpRequestOptions(cancel_event=cancel))
    )
    _wait_until(lambda: server.events == ["wait:never"])
    assert _io_threads() == ["mcp-http-request"]
    started = time.monotonic()
    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert time.monotonic() - started < 1
    assert results == []
    assert [type(error) for error in raised] == [McpAbortError]
    _wait_until(lambda: not _io_threads())
    _wait_until(lambda: len(server.messages("notifications/cancelled")) == 1)
    assert client.list_tools() == [_ECHO]


def test_cancellation_notices_the_server_does_not_take_do_not_queue(
    fake: _Fixture,
) -> None:
    # Each notice is given one second, and none waits for another: every
    # request's connection is closed as soon as the request was given up.
    server = fake.server(
        toolSteps={"echo": [{"sse": ": open\n\n"}, {"wait": "never"}]},
        methods={"notifications/cancelled": [{"hold": True}]},
    )
    client = fake.connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    options = McpRequestOptions(timeout_ms=100)

    started = time.monotonic()
    calls = [
        _in_thread(lambda: client.call_tool("echo", None, options)) for _ in range(3)
    ]
    for thread, _results, _raised in calls:
        thread.join(_WAIT_SECONDS)
    _wait_until(lambda: "mcp-http-request" not in _io_threads())
    requests_ended = time.monotonic() - started

    assert requests_ended < 0.9
    for _thread, results, raised in calls:
        assert results == []
        assert [type(error) for error in raised] == [McpTimeoutError]
    # The notices are posted on threads of their own.
    _wait_until(lambda: len(server.messages("notifications/cancelled")) == 3)
    # The connection stays usable while the notices are still in flight.
    assert client.list_tools() == [_ECHO]
    _wait_until(lambda: len(errors) == 3)
    assert 0.9 < time.monotonic() - started < 5
    for error in errors:
        assert isinstance(error, McpTimeoutError)
        assert "after 1000ms" in str(error)
    _wait_until(lambda: not _io_threads())


def test_a_rejected_cancellation_notice_is_reported(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"wait": "never"}]},
        methods={"notifications/cancelled": [{"status": 500, "body": "no"}]},
    )
    client = fake.connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)

    with pytest.raises(McpTimeoutError):
        client.call_tool("echo", None, McpRequestOptions(timeout_ms=50))
    _wait_until(lambda: len(errors) == 1)

    assert isinstance(errors[0], McpHttpError)
    assert str(errors[0]) == "MCP HTTP request failed with status 500: no"
    _wait_until(lambda: not _io_threads())


def test_a_cancellation_notice_in_flight_is_not_reported_after_close(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={"echo": [{"wait": "never"}]},
        methods={"notifications/cancelled": [{"hold": True}]},
    )
    transport = fake.transport(server)
    client = fake.client()
    client.connect(transport)
    errors: list[Exception] = []
    transport.on_error(errors.append)

    with pytest.raises(McpTimeoutError):
        client.call_tool("echo", None, McpRequestOptions(timeout_ms=50))
    _wait_until(lambda: len(server.messages("notifications/cancelled")) == 1)
    client.close()
    _wait_until(lambda: not _io_threads())

    assert errors == []


def test_closing_ends_every_io_thread_and_fails_what_is_in_flight(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={
            "silent": [{"wait": "never"}],
            "stalled": [{"sse": ": open\n\n"}, {"hold": True}],
        },
        methods={"notifications/x": [{"hold": True}]},
    )
    client = fake.connect(server)
    calls = [
        _in_thread(lambda: client.call_tool("silent")),
        _in_thread(lambda: client.call_tool("stalled")),
        _in_thread(lambda: client.notify("notifications/x")),
    ]
    _wait_until(lambda: len(_io_threads()) == 3)
    _wait_until(lambda: len(server.requests) == 5)

    client.close()

    for thread, results, raised in calls:
        thread.join(_WAIT_SECONDS)
        assert results == []
        assert [type(error) for error in raised] == [McpConnectionClosedError]
    _wait_until(lambda: not _io_threads())


def test_closes_the_session_with_the_last_requests_token(fake: _Fixture) -> None:
    # Pi: "closes the session with the last request's token, without asking
    # the auth provider"
    server = fake.server()
    tokens = _Tokens()
    client = fake.connect(server, auth_provider=tokens)
    before = tokens.calls

    client.close()

    assert tokens.calls == before
    deletes = [request for request in server.requests if request.method == "DELETE"]
    assert [request.headers["authorization"] for request in deletes] == [
        f"Bearer token-{before}"
    ]
    assert deletes[0].headers["mcp-session-id"] == "session-1"


def test_close_sends_no_delete_without_a_session(fake: _Fixture) -> None:
    server = fake.server()
    del server.scenario["sessionId"]
    transport = fake.transport(server)
    client = fake.client()
    client.connect(transport)
    closes: list[str] = []
    transport.on_close(lambda: closes.append("closed"))

    client.close()
    transport.close()

    assert transport.session_id is None
    assert [request.method for request in server.requests] == ["POST", "POST"]
    assert closes == ["closed"]


def test_close_waits_one_second_for_the_delete_and_ignores_its_failure(
    fake: _Fixture,
) -> None:
    server = fake.server(delete=[{"hold": True}])
    client = fake.connect(server)

    started = time.monotonic()
    client.close()
    elapsed = time.monotonic() - started

    assert 0.9 < elapsed < 5
    assert client.connection_state == "closed"
    _wait_until(lambda: not _io_threads())


def test_close_ignores_a_delete_the_server_rejects(fake: _Fixture) -> None:
    server = fake.server(delete=[{"status": 405}])
    transport = fake.transport(server)
    closes: list[str] = []
    transport.on_close(lambda: closes.append("closed"))
    client = fake.client()
    client.connect(transport)

    client.close()

    assert closes == ["closed"]


# -- the handshake -----------------------------------------------------------


def test_nothing_follows_the_initialized_notification_before_it_is_accepted(
    fake: _Fixture,
) -> None:
    server = fake.server(methods={"notifications/initialized": [{"wait": "init"}]})
    client = fake.client()
    transport = fake.transport(server, open_get_stream=True)

    def connect_and_list() -> object:
        client.connect(transport)
        return client.list_tools()

    thread, results, raised = _in_thread(connect_and_list)
    _wait_until(lambda: server.events == ["wait:init"])
    time.sleep(0.2)

    assert client.connection_state == "connecting"
    assert [
        (request.method, request.message and request.message.get("method"))
        for request in server.requests
    ] == [("POST", "initialize"), ("POST", "notifications/initialized")]
    server.release("init")
    thread.join(_WAIT_SECONDS)
    assert (results, raised) == ([[_ECHO]], [])
    _wait_until(lambda: len(_gets(server)) == 1)


def test_a_rejected_initialized_notification_fails_the_connect(fake: _Fixture) -> None:
    server = fake.server(
        methods={"notifications/initialized": [{"status": 500, "body": "no"}]}
    )
    client = fake.client()

    with pytest.raises(McpHttpError, match="status 500: no"):
        client.connect(fake.transport(server))

    assert client.connection_state == "closed"
    assert server.messages("tools/list") == []


# -- the transport on its own ------------------------------------------------


def test_lifecycle_errors(fake: _Fixture) -> None:
    server = fake.server()
    transport: McpTransport = fake.transport(server)
    message: JsonRpcMessage = {"jsonrpc": "2.0", "method": "notifications/x"}

    with pytest.raises(McpConnectionClosedError):
        transport.send(message)
    transport.start()
    with pytest.raises(RuntimeError, match="already started"):
        transport.start()
    transport.send(message)
    transport.close()
    transport.close()
    with pytest.raises(McpConnectionClosedError):
        transport.send(message)
    with pytest.raises(McpConnectionClosedError):
        transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    closed = fake.transport(server)
    closes: list[str] = []
    closed.on_close(lambda: closes.append("closed"))
    closed.close()
    with pytest.raises(McpConnectionClosedError):
        closed.start()
    assert closes == ["closed"]
    assert [request.method for request in server.requests] == ["POST"]


def test_a_message_that_is_not_json_is_refused_by_send(fake: _Fixture) -> None:
    transport = fake.transport(fake.server())
    transport.start()

    with pytest.raises(ValueError):
        transport.send({"jsonrpc": "2.0", "method": "x", "params": {"n": float("nan")}})
    assert not _io_threads()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/mcp",
        "example.com/mcp",
        "http:///mcp",
        "http://user:secret@example.com/mcp",
        "http://example.com:port/mcp",
        # Port 0 is no port a server listens on, and not the default port.
        "http://example.com:0/mcp",
        "https://example.com:0/mcp",
        "",
    ],
)
def test_rejects_a_url_that_is_not_http(url: str) -> None:
    with pytest.raises(ValueError, match="^Invalid MCP server URL"):
        StreamableHttpTransport(StreamableHttpTransportOptions(url=url))


def test_the_request_target_keeps_the_query_and_escapes_what_a_request_line_cannot_hold(
    fake: _Fixture,
) -> None:
    server = fake.server()
    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(url=server.url + "/a b/ü?key=v 1&x=%20#frag")
    )
    fake.transports.append(transport)
    transport.start()

    transport.send({"jsonrpc": "2.0", "method": "notifications/x"})

    assert server.requests[0].path == "/mcp/a%20b/%C3%BC?key=v%201&x=%20"


# -- the server-to-client stream ---------------------------------------------

_LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
_FAST = StreamableHttpReconnectOptions(initial_delay_ms=1, max_delay_ms=5)
_OPEN_STREAM = [{"sse": ": open\n\n"}, {"hold": True}]


def _event(message: dict[str, Any], event_id: str | None = None) -> str:
    """One event of a stream, as a server writes it."""

    head = "" if event_id is None else f"id: {event_id}\n"
    return f"{head}data: {json.dumps(message)}\n\n"


def _last_event_ids(server: FakeMcpHttpServer) -> list[str | None]:
    return [request.headers.get("last-event-id") for request in _gets(server)]


def _get_thread_ended() -> bool:
    return "mcp-http-get" not in _io_threads()


def _connect_with_stream(
    fake: _Fixture, server: FakeMcpHttpServer, **options: Any
) -> tuple[McpClient, list[Exception]]:
    """Connect with the server-to-client stream; collect the reported errors."""

    client = fake.client()
    errors: list[Exception] = []
    client.on_error(errors.append)
    options.setdefault("reconnect", _FAST)
    client.connect(fake.transport(server, open_get_stream=True, **options))
    return client, errors


def test_opens_the_get_stream_after_initialization_and_sends_last_event_id_only_when_resuming(
    fake: _Fixture,
) -> None:
    # Pi: "opens the GET stream after initialization and sends Last-Event-ID
    # only when resuming"
    server = fake.server(methods={"tools/call": _SSE_ANSWER})
    client, errors = _connect_with_stream(fake, server)

    assert client.call_tool("echo") == _HELLO
    assert client.list_tools() == [_ECHO]
    _wait_until(lambda: len(_gets(server)) == 1)
    # The server answered 405: it has no such stream, and that is no error.
    _wait_until(_get_thread_ended)
    client.close()

    order = [
        request.message["method"] if request.message else request.method
        for request in server.requests
    ]
    assert order.index("GET") > order.index("notifications/initialized")
    assert all("last-event-id" not in request.headers for request in server.requests)
    (get,) = _gets(server)
    assert get.path == "/mcp"
    assert get.headers["accept"] == "text/event-stream"
    assert get.headers["mcp-session-id"] == "session-1"
    assert get.headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION
    assert "content-type" not in get.headers
    assert errors == []


def test_the_get_stream_is_not_opened_when_the_option_is_off(fake: _Fixture) -> None:
    server = fake.server(get=_OPEN_STREAM)
    client = fake.client()
    client.connect(fake.transport(server, open_get_stream=False))

    assert client.list_tools() == [_ECHO]
    client.close()

    assert _gets(server) == []


def test_the_get_stream_is_opened_by_default() -> None:
    options = StreamableHttpTransportOptions(url="http://127.0.0.1:1/mcp")

    assert options.open_get_stream is True
    assert options.reconnect == StreamableHttpReconnectOptions(
        initial_delay_ms=1_000, max_delay_ms=30_000, max_retries=5
    )


@pytest.mark.parametrize("ending", ["ends", "is cut"])
def test_reconnects_the_get_stream_after_it_drops(fake: _Fixture, ending: str) -> None:
    # Pi: "reconnects the GET stream after it drops"
    first: list[dict[str, Any]] = [{"sse": _event(_LIST_CHANGED, "g1")}]
    if ending == "is cut":
        first.append({"drop": True})
    server = fake.server(
        getSequence=[first, [{"sse": _event(_LIST_CHANGED, "g2")}, {"hold": True}]]
    )
    client = fake.client()
    errors: list[Exception] = []
    client.on_error(errors.append)
    changes: list[object] = []
    client.on_notification("notifications/tools/list_changed", changes.append)
    transport = fake.transport(server, open_get_stream=True, reconnect=_FAST)

    client.connect(transport)
    _wait_until(lambda: len(changes) == 2)

    assert _last_event_ids(server) == [None, "g1"]
    assert errors == []
    # The stream's thread does not keep the connections it is done with.
    (exchange,) = transport._exchanges
    assert len(exchange.token._closeables) == 1
    assert _io_threads() == ["mcp-http-get"]


def test_a_server_request_on_the_get_stream_is_answered_with_a_post(
    fake: _Fixture,
) -> None:
    ping = {"jsonrpc": "2.0", "id": "s1", "method": "ping"}
    server = fake.server(get=[{"send": ping}, {"hold": True}])

    _connect_with_stream(fake, server)

    answer = {"jsonrpc": "2.0", "id": "s1", "result": {}}
    _wait_until(lambda: any(r.message == answer for r in server.requests))


def test_the_get_stream_carries_the_auth_providers_token(fake: _Fixture) -> None:
    server = fake.server(get=_OPEN_STREAM)
    tokens = _Tokens()
    client, _ = _connect_with_stream(fake, server, auth_provider=tokens)
    _wait_until(lambda: len(_gets(server)) == 1)

    client.close()

    (get,) = _gets(server)
    assert get.headers["authorization"] == "Bearer token-3"
    assert tokens.threads[-1] == "mcp-http-get"
    # It is the latest request, so its token is the one that ends the session.
    (delete,) = [request for request in server.requests if request.method == "DELETE"]
    assert delete.headers["authorization"] == "Bearer token-3"


def test_the_get_stream_gives_up_after_five_failures_in_a_row(fake: _Fixture) -> None:
    server = fake.server(get=[{"status": 503}])
    client, errors = _connect_with_stream(fake, server)

    _wait_until(_get_thread_ended)

    # The first attempt and five more.
    assert len(_gets(server)) == 6
    assert [type(error) for error in errors] == [McpProtocolError]
    assert str(errors[0]) == (
        "MCP server-to-client stream dropped and could not be reopened"
    )
    assert client.list_tools() == [_ECHO]


def test_a_get_stream_that_ends_without_an_event_counts_as_a_failure(
    fake: _Fixture,
) -> None:
    server = fake.server(get=[{"sse": ": nothing\n\n"}])
    _, errors = _connect_with_stream(
        fake, server, reconnect=StreamableHttpReconnectOptions(1, 5, max_retries=2)
    )

    _wait_until(_get_thread_ended)

    assert len(_gets(server)) == 3
    assert [str(error) for error in errors] == [
        "MCP server-to-client stream dropped and could not be reopened"
    ]


def test_a_get_stream_that_delivered_an_event_starts_the_count_again(
    fake: _Fixture,
) -> None:
    streams = [[{"sse": _event(_LIST_CHANGED, f"g{n}")}] for n in range(8)]
    server = fake.server(getSequence=[*streams, _OPEN_STREAM])

    _, errors = _connect_with_stream(
        fake, server, reconnect=StreamableHttpReconnectOptions(1, 5, max_retries=1)
    )

    _wait_until(lambda: len(_gets(server)) == 9)
    assert _last_event_ids(server) == [None, *(f"g{n}" for n in range(8))]
    assert errors == []
    assert not _get_thread_ended()


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_the_get_stream_is_opened_again_after_a_transient_status(
    fake: _Fixture, status: int
) -> None:
    server = fake.server(getSequence=[[{"status": status}], _OPEN_STREAM])

    _, errors = _connect_with_stream(fake, server)

    _wait_until(lambda: len(_gets(server)) == 2)
    assert errors == []
    assert not _get_thread_ended()


def test_the_get_stream_is_opened_again_after_a_network_failure(
    fake: _Fixture,
) -> None:
    server = fake.server(getSequence=[[{"drop": True}], _OPEN_STREAM])

    _, errors = _connect_with_stream(fake, server)

    _wait_until(lambda: len(_gets(server)) == 2)
    assert errors == []


@pytest.mark.parametrize(
    ("steps", "error_type", "message"),
    [
        ([{"status": 400, "body": "bad"}], McpHttpError, "status 400: bad"),
        ([{"status": 403}], McpHttpError, "status 403"),
        (
            [{"status": 401, "headers": {"WWW-Authenticate": "Bearer"}}],
            McpAuthRequiredError,
            "MCP server requires authentication",
        ),
        ([{"status": 404}], McpSessionExpiredError, "MCP session expired"),
        (
            [{"status": 200, "headers": {"Content-Type": "application/json"}}],
            McpHttpError,
            "Unsupported MCP GET response content type: application/json",
        ),
        (
            [{"status": 200}],
            McpHttpError,
            "Unsupported MCP GET response content type: missing",
        ),
    ],
)
def test_the_get_stream_ends_with_an_error_that_is_not_transient(
    fake: _Fixture,
    steps: list[dict[str, Any]],
    error_type: type[Exception],
    message: str,
) -> None:
    server = fake.server(get=steps)
    client, errors = _connect_with_stream(fake, server)

    _wait_until(_get_thread_ended)

    assert len(_gets(server)) == 1
    assert [type(error) for error in errors] == [error_type]
    assert message in str(errors[0])
    # Only the stream is lost.
    assert client.list_tools() == [_ECHO]


def test_a_protocol_error_on_the_get_stream_ends_it(fake: _Fixture) -> None:
    server = fake.server(
        get=[{"sse": "id: g1\ndata: " + "x" * 600 + "\n"}, {"hold": True}]
    )
    _, errors = _connect_with_stream(fake, server, max_message_bytes=512)

    _wait_until(_get_thread_ended)

    assert len(_gets(server)) == 1
    assert [str(error) for error in errors] == ["MCP SSE event exceeds 512 bytes"]


def test_the_get_stream_waits_as_long_as_the_server_asks_with_retry(
    fake: _Fixture,
) -> None:
    server = fake.server(
        getSequence=[[{"sse": "retry: 300\nid: g1\ndata:\n\n"}], _OPEN_STREAM]
    )

    _connect_with_stream(fake, server)

    _wait_until(lambda: len(_gets(server)) == 2)
    first, second = _gets(server)
    assert second.t - first.t >= 0.3
    assert second.headers["last-event-id"] == "g1"


def test_the_reconnect_delay_doubles_from_one_second_to_thirty(fake: _Fixture) -> None:
    transport = fake.transport(fake.server())

    delays = [transport._reconnect_delay(attempt, None) for attempt in range(7)]

    assert delays == [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000]
    assert transport._reconnect_delay(10**6, None) == 30_000
    # What the server asked for replaces the backoff, whatever its size.
    assert transport._reconnect_delay(4, 0) == 0
    assert transport._reconnect_delay(0, 90_000) == 90_000


def test_a_wait_longer_than_a_thread_can_sleep_does_not_fail(fake: _Fixture) -> None:
    server = fake.server(get=[{"sse": "retry: 999999999999999999\ndata:\n\n"}])
    client, errors = _connect_with_stream(fake, server)
    _wait_until(lambda: len(_gets(server)) == 1)
    time.sleep(0.1)

    assert not _get_thread_ended()
    client.close()

    _wait_until(lambda: not _io_threads())
    assert errors == []


@pytest.mark.parametrize(
    "steps",
    [
        _OPEN_STREAM,
        # The stream ended, and the next attempt is a minute away.
        [{"sse": "retry: 60000\ndata:\n\n"}],
        # The server does not answer the GET.
        [{"wait": "never"}],
    ],
    ids=["open stream", "reconnect wait", "unanswered"],
)
def test_closing_ends_the_get_stream_at_once_and_reports_nothing(
    fake: _Fixture, steps: list[dict[str, Any]]
) -> None:
    server = fake.server(get=steps)
    client, errors = _connect_with_stream(fake, server)
    _wait_until(lambda: len(_gets(server)) == 1)
    time.sleep(0.1)
    assert _io_threads() == ["mcp-http-get"]

    started = time.monotonic()
    client.close()
    _wait_until(lambda: not _io_threads())

    assert time.monotonic() - started < 1
    assert errors == []
    assert len(_gets(server)) == 1


def test_a_transport_closed_before_the_handshake_ends_opens_no_get_stream(
    fake: _Fixture,
) -> None:
    server = fake.server(methods={"notifications/initialized": [{"wait": "init"}]})
    client = fake.client()
    transport = fake.transport(server, open_get_stream=True)
    thread, results, raised = _in_thread(lambda: client.connect(transport))
    _wait_until(lambda: server.events == ["wait:init"])

    transport.close()
    thread.join(_WAIT_SECONDS)
    server.release("init")

    assert results == []
    assert [type(error) for error in raised] == [McpConnectionClosedError]
    _wait_until(lambda: not _io_threads())
    assert _gets(server) == []


# -- resuming a response stream ----------------------------------------------

_RESUMED = {"content": [{"type": "text", "text": "resumed"}]}
# The answer to the first `tools/call` of a client, whose request id is 2.
_RESUMED_ANSWER = _event({"jsonrpc": "2.0", "id": 2, "result": _RESUMED}, "2")
# A priming event (an id, no data) and a retry hint; then the stream ends.
_PRIMED = [{"sse": "id: 1\nretry: 5\ndata:\n\n"}]


def _connect_with_errors(
    fake: _Fixture, server: FakeMcpHttpServer, **options: Any
) -> tuple[McpClient, list[Exception]]:
    client = fake.client()
    errors: list[Exception] = []
    client.on_error(errors.append)
    client.connect(fake.transport(server, **options))
    return client, errors


@pytest.mark.parametrize("ending", ["ends", "is cut"])
def test_resumes_a_response_stream_the_server_closed_before_answering(
    fake: _Fixture, ending: str
) -> None:
    # Pi: "resumes a response stream the server closed before answering"
    steps = _PRIMED if ending == "ends" else [*_PRIMED, {"drop": True}]
    server = fake.server(toolSteps={"echo": steps}, get=[{"sse": _RESUMED_ANSWER}])
    client, errors = _connect_with_errors(fake, server)

    assert client.call_tool("echo") == _RESUMED

    (get,) = _gets(server)
    assert get.headers["last-event-id"] == "1"
    assert get.headers["accept"] == "text/event-stream"
    assert get.headers["mcp-session-id"] == "session-1"
    assert get.headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION
    assert errors == []
    _wait_until(lambda: not _io_threads())


def test_resuming_names_the_latest_event_id_also_of_an_event_with_data(
    fake: _Fixture,
) -> None:
    notification = {"jsonrpc": "2.0", "method": "notifications/x"}
    server = fake.server(
        toolSteps={
            "echo": [{"sse": "id: 1\ndata:\n\n" + _event(notification, "seven")}]
        },
        get=[{"sse": _RESUMED_ANSWER}],
    )
    client, _ = _connect_with_errors(fake, server, reconnect=_FAST)
    seen: list[object] = []
    client.on_notification("notifications/x", seen.append)

    assert client.call_tool("echo") == _RESUMED

    assert _last_event_ids(server) == ["seven"]
    assert seen == [None]


def test_resuming_is_tried_again_after_a_transient_failure(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": _PRIMED},
        getSequence=[
            [{"status": 503}],
            [{"drop": True}],
            [{"sse": _RESUMED_ANSWER}],
        ],
    )
    client, errors = _connect_with_errors(fake, server)

    assert client.call_tool("echo") == _RESUMED

    assert _last_event_ids(server) == ["1", "1", "1"]
    assert errors == []


def test_resuming_waits_as_long_as_the_server_asks_with_retry(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": "id: 1\nretry: 300\ndata:\n\n"}]},
        get=[{"sse": _RESUMED_ANSWER}],
    )
    client, _ = _connect_with_errors(fake, server, reconnect=_FAST)

    assert client.call_tool("echo") == _RESUMED

    (post,) = server.messages("tools/call")
    (get,) = _gets(server)
    assert get.t - post.t >= 0.3


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        (
            [{"status": 400, "body": "bad"}],
            "MCP HTTP request failed with status 400: bad",
        ),
        ([{"status": 401}], "MCP server requires authentication"),
        ([{"status": 404}], "MCP session expired"),
        (
            [{"status": 200, "headers": {"Content-Type": "text/plain"}}],
            "Unsupported MCP GET response content type: text/plain",
        ),
    ],
)
def test_a_resume_that_fails_for_good_fails_only_its_request(
    fake: _Fixture, steps: list[dict[str, Any]], reason: str
) -> None:
    server = fake.server(toolSteps={"echo": _PRIMED}, get=steps)
    client, errors = _connect_with_errors(fake, server)

    with pytest.raises(McpError) as failure:
        client.call_tool("echo")

    assert str(failure.value) == f"MCP response stream failed: {reason}"
    assert failure.value.code == INTERNAL_ERROR
    assert len(_gets(server)) == 1
    assert errors == []
    assert client.list_tools() == [_ECHO]


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ([{"status": 503}], "MCP HTTP request failed with status 503"),
        # The streams open and end, with nothing on them.
        ([{"sse": ": nothing\n\n"}], "stream ended without a response"),
        # The server has no GET streams at all (405).
        ([], "stream ended without a response"),
    ],
    ids=["transient status", "empty streams", "no GET streams"],
)
def test_resuming_gives_up_after_the_retries_and_fails_its_request(
    fake: _Fixture, steps: list[dict[str, Any]], reason: str
) -> None:
    server = fake.server(toolSteps={"echo": _PRIMED}, get=steps)
    client, _ = _connect_with_errors(
        fake, server, reconnect=StreamableHttpReconnectOptions(1, 5, max_retries=3)
    )

    with pytest.raises(McpError) as failure:
        client.call_tool("echo")

    assert str(failure.value) == f"MCP response stream failed: {reason}"
    assert _last_event_ids(server) == ["1", "1", "1"]
    assert client.list_tools() == [_ECHO]


def test_a_resumed_stream_that_delivered_an_event_starts_the_count_again(
    fake: _Fixture,
) -> None:
    progress = {"jsonrpc": "2.0", "method": "notifications/x"}
    server = fake.server(
        toolSteps={"echo": _PRIMED},
        getSequence=[
            [{"sse": _event(progress, "a")}],
            [{"sse": _event(progress, "b")}],
            [{"sse": _event(progress, "c")}],
            [{"sse": _RESUMED_ANSWER}],
        ],
    )
    # Two attempts in a row may fail; four are made here, as each got an event.
    client, _ = _connect_with_errors(
        fake, server, reconnect=StreamableHttpReconnectOptions(1, 5, max_retries=2)
    )

    assert client.call_tool("echo") == _RESUMED

    assert _last_event_ids(server) == ["1", "a", "b", "c"]


def test_a_response_stream_broken_by_a_protocol_error_is_not_resumed(
    fake: _Fixture,
) -> None:
    server = fake.server(
        toolSteps={
            "echo": [{"sse": "id: 1\ndata: " + "x" * 600 + "\n"}, {"hold": True}]
        },
        get=[{"sse": _RESUMED_ANSWER}],
    )
    client, _ = _connect_with_errors(fake, server, max_message_bytes=512)

    with pytest.raises(McpError) as failure:
        client.call_tool("echo")

    assert str(failure.value) == (
        "MCP response stream failed: MCP SSE event exceeds 512 bytes"
    )
    assert _gets(server) == []


def test_an_answered_response_stream_is_not_resumed(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": "id: 1\n"}, {"mode": "sse"}, {"default": True}]},
        get=[{"sse": _RESUMED_ANSWER}],
    )
    client, _ = _connect_with_errors(fake, server, reconnect=_FAST)

    assert client.call_tool("echo") == _HELLO
    _wait_until(lambda: not _io_threads())

    assert _gets(server) == []


@pytest.mark.parametrize("phase", ["waiting", "reading"])
def test_giving_a_request_up_ends_the_resumption_of_its_stream(
    fake: _Fixture, phase: str
) -> None:
    if phase == "waiting":
        # The next attempt is a minute away.
        server = fake.server(
            toolSteps={"echo": [{"sse": "id: 1\nretry: 60000\ndata:\n\n"}]}
        )
    else:
        server = fake.server(toolSteps={"echo": _PRIMED}, get=_OPEN_STREAM)
    client, errors = _connect_with_errors(fake, server)
    cancel = threading.Event()

    thread, results, raised = _in_thread(
        lambda: client.call_tool("echo", None, McpRequestOptions(cancel_event=cancel))
    )
    _wait_until(lambda: len(server.messages("tools/call")) == 1)
    if phase == "reading":
        _wait_until(lambda: len(_gets(server)) == 1)
    time.sleep(0.1)
    assert _io_threads() == ["mcp-http-request"]
    started = time.monotonic()
    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert time.monotonic() - started < 1
    assert results == []
    assert [type(error) for error in raised] == [McpAbortError]
    _wait_until(lambda: not _io_threads())
    assert len(_gets(server)) == (1 if phase == "reading" else 0)
    assert errors == []
    assert client.list_tools() == [_ECHO]


def test_a_request_timeout_ends_the_resumption_of_its_stream(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": "id: 1\nretry: 60000\ndata:\n\n"}]}
    )
    client, _ = _connect_with_errors(fake, server)

    with pytest.raises(McpTimeoutError, match="after 200ms"):
        client.call_tool("echo", None, McpRequestOptions(timeout_ms=200))

    _wait_until(lambda: not _io_threads())
    assert _gets(server) == []


def test_closing_ends_the_resumption_of_a_response_stream(fake: _Fixture) -> None:
    server = fake.server(
        toolSteps={"echo": [{"sse": "id: 1\nretry: 60000\ndata:\n\n"}]}
    )
    client, errors = _connect_with_errors(fake, server)

    thread, results, raised = _in_thread(lambda: client.call_tool("echo"))
    _wait_until(lambda: len(server.messages("tools/call")) == 1)
    time.sleep(0.1)
    client.close()
    thread.join(_WAIT_SECONDS)

    assert results == []
    assert [type(error) for error in raised] == [McpConnectionClosedError]
    _wait_until(lambda: not _io_threads())
    assert _gets(server) == []
    assert errors == []
