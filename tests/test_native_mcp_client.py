"""Tests for the MCP client core, against a scripted in-memory server.

Tests that mirror a case of Pi's ``packages/mcp/test/client.test.ts`` name it
in a ``Pi:`` comment.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pipy_harness.native.mcp.client import (
    DEFAULT_REQUEST_TIMEOUT_MS,
    McpClient,
    McpClientOptions,
    McpRequestContext,
    McpRequestOptions,
)
from pipy_harness.native.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpProtocolError,
    McpTimeoutError,
)
from pipy_harness.native.mcp.transport import create_in_memory_transport_pair

_WAIT_SECONDS = 5.0

_Handler = Callable[[JsonRpcMessage], object]


class _ServerError(Exception):
    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class _NoAnswer(Exception):
    """Raised by a handler that leaves its request unanswered."""


class _Server:
    """The server end of an in-memory pair, answering requests from handlers.

    Each request is answered on its own thread, so a handler may block.
    """

    def __init__(self) -> None:
        self.client_transport, self.transport = create_in_memory_transport_pair()
        self.released = threading.Event()
        self.closed = threading.Event()
        self._lock = threading.Lock()
        self._messages: list[JsonRpcMessage] = []
        self._handlers: dict[str, _Handler] = {}
        self.transport.on_message(self._on_message)
        self.transport.on_close(self.closed.set)
        self.transport.start()
        self.set_handler(
            "initialize",
            lambda request: {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "test-server", "version": "1.0.0"},
                "instructions": "Use test tools.",
            },
        )

    @property
    def messages(self) -> list[JsonRpcMessage]:
        with self._lock:
            return list(self._messages)

    def methods(self) -> list[object]:
        return [message.get("method") for message in self.messages]

    def set_handler(self, method: str, handler: _Handler) -> None:
        with self._lock:
            self._handlers[method] = handler

    def never(self, request: JsonRpcMessage) -> object:
        """A handler that does not answer while the test runs."""

        self.released.wait(_WAIT_SECONDS * 4)
        raise _NoAnswer

    def wait_for(self, predicate: Callable[[list[JsonRpcMessage]], bool]) -> None:
        _wait_until(lambda: predicate(self.messages))

    def wait_for_message(self, expected: JsonRpcMessage) -> None:
        self.wait_for(lambda messages: expected in messages)

    def wait_for_method(self, method: str) -> None:
        self.wait_for(lambda messages: any(m.get("method") == method for m in messages))

    def shutdown(self) -> None:
        self.released.set()
        self.transport.close()

    def _on_message(self, message: JsonRpcMessage) -> None:
        with self._lock:
            self._messages.append(message)
        if "id" in message and "method" in message:
            threading.Thread(target=self._answer, args=(message,), daemon=True).start()

    def _answer(self, request: JsonRpcMessage) -> None:
        with self._lock:
            handler = self._handlers.get(str(request["method"]))
        response: JsonRpcMessage = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            if handler is None:
                raise _ServerError(-32601, f"Method not found: {request['method']}")
            response["result"] = handler(request)
        except _NoAnswer:
            return
        except _ServerError as error:
            response_error: dict[str, object] = {
                "code": error.code,
                "message": error.message,
            }
            if error.data is not None:
                response_error["data"] = error.data
            response["error"] = response_error
        try:
            self.transport.send(response)
        except McpConnectionClosedError:
            pass


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.005)


def _options(**overrides: object) -> McpClientOptions:
    values: dict[str, Any] = {"name": "test-client", "version": "1.0.0", **overrides}
    return McpClientOptions(**values)


def _text_result(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}]}


def _raises_in_thread(
    call: Callable[[], object],
) -> tuple[threading.Thread, list[BaseException]]:
    """Run ``call`` on a thread and collect what it raises."""

    raised: list[BaseException] = []

    def run() -> None:
        try:
            call()
        except Exception as error:  # noqa: BLE001 - cross-thread capture for the assertion
            raised.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, raised


@pytest.fixture
def server() -> Iterator[_Server]:
    fake = _Server()
    yield fake
    fake.shutdown()


@pytest.fixture
def make_server() -> Iterator[Callable[[], _Server]]:
    servers: list[_Server] = []

    def make() -> _Server:
        servers.append(_Server())
        return servers[-1]

    yield make
    for fake in servers:
        fake.shutdown()


def _connect(server: _Server, **overrides: object) -> McpClient:
    client = McpClient(_options(**overrides))
    client.connect(server.client_transport)
    return client


# -- handshake ---------------------------------------------------------------


def test_initializes_the_connection_before_exposing_server_information(
    server: _Server,
) -> None:
    # Pi: "initializes the connection before exposing server information"
    client = McpClient(_options(version="2.0.0"))
    assert client.connection_state == "idle"
    assert client.server_info is None
    assert client.protocol_version is None

    result = client.connect(server.client_transport)

    assert client.connection_state == "connected"
    assert client.protocol_version == LATEST_PROTOCOL_VERSION == "2025-11-25"
    assert client.server_info == {"name": "test-server", "version": "1.0.0"}
    assert client.server_capabilities == {"tools": {"listChanged": True}}
    assert client.instructions == "Use test tools."
    assert result["serverInfo"] == client.server_info
    server.wait_for(lambda messages: len(messages) == 2)
    assert server.messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "2.0.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    client.close()


def test_initialize_announces_roots_title_and_configured_capabilities(
    server: _Server,
) -> None:
    client = _connect(
        server,
        title="Test Client",
        protocol_version="2025-06-18",
        capabilities={"experimental": {"x": {}}},
        roots=[],
    )

    assert server.messages[0]["params"] == {
        "protocolVersion": "2025-06-18",
        "capabilities": {"experimental": {"x": {}}, "roots": {}},
        "clientInfo": {
            "name": "test-client",
            "version": "1.0.0",
            "title": "Test Client",
        },
    }
    client.close()


def test_configured_roots_capability_is_kept(server: _Server) -> None:
    client = _connect(server, capabilities={"roots": {"listChanged": True}}, roots=[])

    params = server.messages[0]["params"]
    assert isinstance(params, dict)
    assert params["capabilities"] == {"roots": {"listChanged": True}}
    client.close()


def test_client_is_not_usable_before_the_handshake_completes(server: _Server) -> None:
    client = McpClient(_options())
    with pytest.raises(McpConnectionClosedError, match="^MCP client is idle$"):
        client.request("ping")
    with pytest.raises(McpConnectionClosedError, match="^MCP client is idle$"):
        client.notify("notifications/roots/list_changed")

    answer = threading.Event()

    def initialize(request: JsonRpcMessage) -> object:
        answer.wait(_WAIT_SECONDS)
        return {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "serverInfo": {"name": "slow", "version": "1"},
        }

    server.set_handler("initialize", initialize)
    thread, raised = _raises_in_thread(lambda: client.connect(server.client_transport))
    server.wait_for_method("initialize")
    assert client.connection_state == "connecting"
    with pytest.raises(McpConnectionClosedError, match="^MCP client is connecting$"):
        client.request("ping")
    with pytest.raises(McpConnectionClosedError, match="^MCP client is connecting$"):
        client.notify("notifications/roots/list_changed")

    answer.set()
    thread.join(_WAIT_SECONDS)
    assert raised == []
    assert client.connection_state == "connected"
    assert client.instructions is None
    server.set_handler("ping", lambda request: {})
    client.ping()
    client.close()


@pytest.mark.parametrize(
    "version", ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
)
def test_accepts_servers_that_answer_with_an_older_protocol_version(
    server: _Server, version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi: "accepts servers that answer with an older protocol version"
    server.set_handler(
        "initialize",
        lambda request: {
            "protocolVersion": version,
            "capabilities": {},
            "serverInfo": {"name": "old-server", "version": "0.1.0"},
        },
    )
    versions: list[str] = []
    monkeypatch.setattr(
        server.client_transport, "set_protocol_version", versions.append
    )

    client = _connect(server)

    assert client.protocol_version == version
    assert versions == [version]
    client.close()


def test_rejects_an_unsupported_protocol_version_and_closes_the_transport(
    server: _Server,
) -> None:
    # Pi: "accepts servers that answer with an older protocol version" (second half)
    server.set_handler(
        "initialize",
        lambda request: {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "serverInfo": {"name": "ancient-server", "version": "0.1.0"},
        },
    )
    client = McpClient(_options())

    with pytest.raises(
        McpProtocolError,
        match="^MCP server selected unsupported protocol version 1999-01-01$",
    ):
        client.connect(server.client_transport)

    assert client.connection_state == "closed"
    assert client.protocol_version is None
    assert server.closed.wait(_WAIT_SECONDS)
    assert "notifications/initialized" not in server.methods()


_VALID_INITIALIZE: dict[str, object] = {
    "protocolVersion": LATEST_PROTOCOL_VERSION,
    "capabilities": {},
    "serverInfo": {"name": "s", "version": "1"},
}


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {**_VALID_INITIALIZE, "protocolVersion": 20251125},
        {
            key: value
            for key, value in _VALID_INITIALIZE.items()
            if key != "protocolVersion"
        },
        {**_VALID_INITIALIZE, "capabilities": []},
        {
            key: value
            for key, value in _VALID_INITIALIZE.items()
            if key != "capabilities"
        },
        {**_VALID_INITIALIZE, "serverInfo": "s"},
        {**_VALID_INITIALIZE, "serverInfo": {"version": "1"}},
        {**_VALID_INITIALIZE, "serverInfo": {"name": "s"}},
        {**_VALID_INITIALIZE, "serverInfo": {"name": "s", "version": 1}},
        {**_VALID_INITIALIZE, "instructions": 7},
        {**_VALID_INITIALIZE, "instructions": None},
    ],
)
def test_rejects_an_invalid_initialize_result(server: _Server, result: object) -> None:
    server.set_handler("initialize", lambda request: result)
    client = McpClient(_options())

    with pytest.raises(McpError, match="^Invalid MCP initialize result$") as raised:
        client.connect(server.client_transport)

    assert raised.value.code == -32600
    assert client.connection_state == "closed"
    assert server.closed.wait(_WAIT_SECONDS)


def test_a_client_connects_once(
    server: _Server, make_server: Callable[[], _Server]
) -> None:
    client = _connect(server)
    with pytest.raises(
        RuntimeError, match="^Cannot connect MCP client in connected state$"
    ):
        client.connect(make_server().client_transport)
    client.close()
    with pytest.raises(
        RuntimeError, match="^Cannot connect MCP client in closed state$"
    ):
        client.connect(make_server().client_transport)


def test_a_transport_that_drops_during_the_handshake_fails_connect(
    server: _Server,
) -> None:
    def initialize(request: JsonRpcMessage) -> object:
        server.transport.close()
        raise _NoAnswer

    server.set_handler("initialize", initialize)
    client = McpClient(_options())

    with pytest.raises(McpConnectionClosedError, match="^MCP connection closed$"):
        client.connect(server.client_transport)
    assert client.connection_state == "closed"


# -- requests -----------------------------------------------------------------


def test_requests_carry_increasing_ids_and_return_the_result(server: _Server) -> None:
    client = _connect(server)
    server.set_handler("tools/call", lambda request: {"echo": request.get("params")})

    assert client.request("tools/call", {"name": "a"}) == {"echo": {"name": "a"}}
    assert client.request("tools/call") == {"echo": None}

    requests = [message for message in server.messages if "id" in message]
    assert [message["id"] for message in requests] == [1, 2, 3]
    assert "params" not in requests[2]
    client.close()


def test_surfaces_json_rpc_errors(server: _Server) -> None:
    # Pi: "returns structured tool content and surfaces JSON-RPC errors" (error half)
    client = _connect(server)

    def fail(request: JsonRpcMessage) -> object:
        raise _ServerError(1234, "tool failed", {"retryable": False})

    server.set_handler("tools/call", fail)

    with pytest.raises(McpError) as raised:
        client.request("tools/call", {"name": "fail"})

    assert raised.value.code == 1234
    assert raised.value.message == "tool failed"
    assert raised.value.data == {"retryable": False}
    client.close()


def test_a_response_for_an_unknown_request_is_reported_and_changes_nothing(
    server: _Server,
) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    answer = threading.Event()

    def call(request: JsonRpcMessage) -> object:
        answer.wait(_WAIT_SECONDS)
        return {"ok": True}

    server.set_handler("tools/call", call)
    results: list[object] = []
    thread = threading.Thread(
        target=lambda: results.append(client.request("tools/call"))
    )
    thread.start()
    server.wait_for_method("tools/call")

    server.transport.send({"jsonrpc": "2.0", "id": 99, "result": {}})
    server.transport.send(
        {"jsonrpc": "2.0", "id": "x", "error": {"code": 1, "message": "no"}}
    )
    _wait_until(lambda: len(errors) == 2)
    answer.set()
    thread.join(_WAIT_SECONDS)

    assert [str(error) for error in errors] == [
        "Received response for unknown MCP request 99",
        "Received response for unknown MCP request x",
    ]
    assert all(isinstance(error, McpProtocolError) for error in errors)
    assert results == [{"ok": True}]
    assert client.connection_state == "connected"
    client.close()


def test_an_invalid_message_is_reported(server: _Server) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)

    server.transport.send({"jsonrpc": "2.0", "id": 5})
    _wait_until(lambda: len(errors) == 1)

    assert isinstance(errors[0], McpError)
    assert str(errors[0]) == "Received invalid JSON-RPC message"
    client.close()


def test_a_failed_send_ends_the_request_with_that_error(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)

    def broken(message: JsonRpcMessage) -> None:
        raise OSError("broken pipe")

    monkeypatch.setattr(server.client_transport, "send", broken)

    with pytest.raises(OSError, match="broken pipe"):
        client.request("tools/call")
    with pytest.raises(OSError, match="broken pipe"):
        client.notify("notifications/roots/list_changed")
    assert client.connection_state == "connected"
    monkeypatch.undo()
    client.close()


def _block_sends(
    server: _Server,
    monkeypatch: pytest.MonkeyPatch,
    blocked: Callable[[JsonRpcMessage], bool],
) -> tuple[threading.Event, threading.Event]:
    """Make the client transport's ``send`` block for the matching messages.

    Returns ``(entered, release)``: ``entered`` is set when a write blocks and
    ``release`` lets blocked writes through.
    """

    entered = threading.Event()
    release = threading.Event()
    send = server.client_transport.send

    def blocking(message: JsonRpcMessage) -> None:
        if blocked(message):
            entered.set()
            release.wait(_WAIT_SECONDS * 4)
        send(message)

    monkeypatch.setattr(server.client_transport, "send", blocking)
    return entered, release


def test_a_blocked_request_write_does_not_defeat_the_timeout_or_the_cancel_event(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)
    server.wait_for(lambda messages: len(messages) == 2)
    server.set_handler("tools/call", server.never)
    entered, release = _block_sends(
        server, monkeypatch, lambda message: message.get("method") == "tools/call"
    )

    started = time.monotonic()
    with pytest.raises(McpTimeoutError):
        client.request("tools/call", {"name": "a"}, McpRequestOptions(timeout_ms=20))
    cancel = threading.Event()
    threading.Timer(0.02, cancel.set).start()
    with pytest.raises(McpAbortError):
        client.request(
            "tools/call", {"name": "b"}, McpRequestOptions(cancel_event=cancel)
        )
    assert time.monotonic() - started < _WAIT_SECONDS
    assert entered.is_set()
    assert len(server.messages) == 2

    # The first request was being written when it timed out, so its
    # cancellation follows it. The second one was still queued and is dropped.
    release.set()
    server.set_handler("ping", lambda request: {})
    client.ping()
    assert server.messages[2:] == [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "a"}},
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 2, "reason": "Request timed out"},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    client.close()


def test_a_blocked_cancellation_write_does_not_hold_the_caller(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)
    server.set_handler("tools/call", server.never)
    entered, release = _block_sends(
        server,
        monkeypatch,
        lambda message: message.get("method") == "notifications/cancelled",
    )

    with pytest.raises(McpTimeoutError):
        client.request("tools/call", None, McpRequestOptions(timeout_ms=20))
    assert entered.wait(_WAIT_SECONDS)
    cancel = threading.Event()
    threading.Timer(0.02, cancel.set).start()
    with pytest.raises(McpAbortError):
        client.request("tools/call", None, McpRequestOptions(cancel_event=cancel))

    assert "notifications/cancelled" not in server.methods()
    release.set()
    server.wait_for_method("notifications/cancelled")
    client.close()


def test_repeated_timeouts_against_a_server_that_stopped_reading_stay_bounded(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)
    server.wait_for(lambda messages: len(messages) == 2)
    entered, release = _block_sends(server, monkeypatch, lambda message: True)

    with pytest.raises(McpTimeoutError):
        client.request("tools/call", None, McpRequestOptions(timeout_ms=5))
    assert entered.wait(_WAIT_SECONDS)
    threads = threading.active_count()
    for _ in range(20):
        with pytest.raises(McpTimeoutError):
            client.request("tools/call", None, McpRequestOptions(timeout_ms=5))

    # One writer thread, and one queued notice for the request it is writing.
    assert threading.active_count() <= threads
    assert len(client._outbox) == 1
    assert client._pending == {}

    # Only the request that was being written, and its cancellation, go out.
    release.set()
    server.wait_for_method("notifications/cancelled")
    assert [message.get("id") for message in server.messages[2:]] == [2, None]
    client.close()
    assert len(client._outbox) == 0


def test_connect_fails_closed_when_the_writer_thread_cannot_start(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = McpClient(_options())

    start = threading.Thread.start
    refused: list[str] = []

    def refuse_writer(thread: threading.Thread) -> None:
        if thread.name == "mcp-client-writer":
            refused.append(thread.name)
            raise RuntimeError("can't start new thread")
        start(thread)

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", refuse_writer)
        with pytest.raises(RuntimeError, match="can't start new thread"):
            client.connect(server.client_transport)

    assert refused == ["mcp-client-writer"]
    assert client._writer is None
    assert client.connection_state == "closed"
    assert client._pending == {}
    assert len(client._outbox) == 0
    assert server.closed.wait(_WAIT_SECONDS)


def test_a_request_answered_before_it_was_written_is_not_written(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)
    server.wait_for(lambda messages: len(messages) == 2)
    entered, release = _block_sends(
        server, monkeypatch, lambda message: message.get("method") == "x/first"
    )
    first, _ = _raises_in_thread(
        lambda: client.request("x/first", None, McpRequestOptions(timeout_ms=0))
    )
    assert entered.wait(_WAIT_SECONDS)
    results: list[object] = []
    second = threading.Thread(target=lambda: results.append(client.request("x/second")))
    second.start()
    _wait_until(lambda: len(client._outbox) == 1)

    server.transport.send({"jsonrpc": "2.0", "id": 3, "result": {"guessed": True}})
    second.join(_WAIT_SECONDS)

    assert results == [{"guessed": True}]
    assert len(client._outbox) == 0
    release.set()
    server.wait_for_method("x/first")
    client.close()
    first.join(_WAIT_SECONDS)
    assert "x/second" not in server.methods()


def test_no_cancellation_is_sent_for_a_request_whose_write_failed(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    release = threading.Event()
    send = server.client_transport.send
    sent: list[object] = []

    def failing(message: JsonRpcMessage) -> None:
        if message.get("method") == "tools/call":
            release.wait(_WAIT_SECONDS * 4)
            raise OSError("broken pipe")
        sent.append(message.get("method"))
        send(message)

    monkeypatch.setattr(server.client_transport, "send", failing)

    with pytest.raises(McpTimeoutError):
        client.request("tools/call", None, McpRequestOptions(timeout_ms=20))
    release.set()
    server.set_handler("ping", lambda request: {})
    client.ping()
    time.sleep(0.1)

    assert sent == ["ping"]
    assert errors == []
    client.close()


def test_notify_sends_a_notification(server: _Server) -> None:
    client = _connect(server)

    client.notify("notifications/roots/list_changed")
    client.notify("notifications/custom", {"value": 1})

    server.wait_for_message(
        {"jsonrpc": "2.0", "method": "notifications/custom", "params": {"value": 1}}
    )
    assert {
        "jsonrpc": "2.0",
        "method": "notifications/roots/list_changed",
    } in server.messages
    client.close()


# -- timeouts, progress and cancellation -----------------------------------


def test_renews_the_timeout_on_progress(server: _Server) -> None:
    # Pi: "renews the timeout on progress"
    client = _connect(server)

    def slow(request: JsonRpcMessage) -> object:
        params = request["params"]
        assert isinstance(params, dict)
        token = params["_meta"]["progressToken"]
        time.sleep(0.5)
        server.transport.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progressToken": token, "progress": 1, "total": 2},
            }
        )
        # The answer comes after the original deadline, within the renewed one.
        time.sleep(0.8)
        return _text_result("done")

    server.set_handler("tools/call", slow)
    progress: list[object] = []

    result = client.request(
        "tools/call",
        {"name": "slow", "arguments": {}, "_meta": {"trace": "t"}},
        McpRequestOptions(timeout_ms=1000, on_progress=progress.append),
    )

    assert result == _text_result("done")
    assert progress == [{"progressToken": 2, "progress": 1, "total": 2}]
    assert server.messages[2]["params"] == {
        "name": "slow",
        "arguments": {},
        "_meta": {"trace": "t", "progressToken": 2},
    }
    client.close()


def test_a_request_without_a_progress_handler_sends_no_token(server: _Server) -> None:
    client = _connect(server)
    server.set_handler("tools/call", lambda request: {})

    client.request("tools/call", {"name": "plain"})

    assert server.messages[2]["params"] == {"name": "plain"}
    client.close()


def test_progress_for_unknown_tokens_and_malformed_progress_is_ignored(
    server: _Server,
) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    notified: list[object] = []
    client.on_notification("notifications/progress", notified.append)
    progress: list[object] = []
    answer = threading.Event()

    def call(request: JsonRpcMessage) -> object:
        answer.wait(_WAIT_SECONDS)
        return {}

    server.set_handler("tools/call", call)
    thread = threading.Thread(
        target=lambda: client.request(
            "tools/call", None, McpRequestOptions(on_progress=progress.append)
        )
    )
    thread.start()
    server.wait_for_method("tools/call")

    ignored: list[object] = [
        {"progressToken": 77, "progress": 1},
        {"progressToken": "2", "progress": 1},
        {"progressToken": 2, "progress": "half"},
        {"progressToken": 2, "progress": True},
        {"progressToken": None, "progress": 1},
        "nonsense",
    ]
    for params in ignored:
        server.transport.send(
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}
        )
    _wait_until(lambda: len(notified) == len(ignored))
    answer.set()
    thread.join(_WAIT_SECONDS)

    assert progress == []
    assert errors == []
    # Progress notifications still reach ordinary listeners.
    assert notified == ignored
    client.close()


def test_a_raising_progress_handler_is_reported_and_the_request_completes(
    server: _Server,
) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    reported = threading.Event()

    def call(request: JsonRpcMessage) -> object:
        server.transport.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progressToken": request["id"], "progress": 0.5},
            }
        )
        reported.wait(_WAIT_SECONDS)
        return {"ok": True}

    def on_progress(params: object) -> None:
        try:
            raise ValueError("progress failed")
        finally:
            reported.set()

    server.set_handler("tools/call", call)

    result = client.request(
        "tools/call", None, McpRequestOptions(on_progress=on_progress)
    )

    assert result == {"ok": True}
    assert [str(error) for error in errors] == ["progress failed"]
    client.close()


def test_cancels_aborted_and_timed_out_requests(server: _Server) -> None:
    # Pi: "cancels aborted and timed-out requests"
    client = _connect(server)
    server.set_handler("tools/call", server.never)
    cancel = threading.Event()
    thread, raised = _raises_in_thread(
        lambda: client.request(
            "tools/call", {"name": "wait"}, McpRequestOptions(cancel_event=cancel)
        )
    )
    server.wait_for_method("tools/call")

    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert len(raised) == 1
    assert isinstance(raised[0], McpAbortError)
    assert str(raised[0]) == "MCP request aborted"
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 2, "reason": "Aborted"},
        }
    )

    with pytest.raises(
        McpTimeoutError, match="^MCP request timed out after 20ms$"
    ) as timeout:
        client.request("tools/call", {"name": "wait"}, McpRequestOptions(timeout_ms=20))
    assert timeout.value.timeout_ms == 20
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 3, "reason": "Request timed out"},
        }
    )
    assert client.connection_state == "connected"
    client.close()


def test_a_request_whose_cancel_event_is_already_set_is_not_sent(
    server: _Server,
) -> None:
    client = _connect(server)
    server.wait_for(lambda messages: len(messages) == 2)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(McpAbortError):
        client.request("tools/call", None, McpRequestOptions(cancel_event=cancel))

    server.set_handler("ping", lambda request: {})
    client.ping()
    # Nothing was sent for the aborted request, and it used no id.
    assert server.messages[2:] == [{"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    client.close()


def test_a_cancellable_request_still_returns_its_response(server: _Server) -> None:
    client = _connect(server)
    server.set_handler("tools/call", lambda request: {"ok": True})

    result = client.request(
        "tools/call", None, McpRequestOptions(cancel_event=threading.Event())
    )

    assert result == {"ok": True}
    assert "notifications/cancelled" not in server.methods()
    client.close()


def test_the_client_default_timeout_applies_and_can_be_overridden(
    server: _Server,
) -> None:
    assert DEFAULT_REQUEST_TIMEOUT_MS == 30_000
    client = _connect(server, request_timeout_ms=20)
    server.set_handler("tools/call", server.never)

    with pytest.raises(McpTimeoutError, match="after 20ms"):
        client.request("tools/call")
    with pytest.raises(McpTimeoutError, match="after 30ms"):
        client.request("tools/call", None, McpRequestOptions(timeout_ms=30))
    client.close()


def test_a_non_positive_timeout_disables_the_timeout(server: _Server) -> None:
    client = _connect(server, request_timeout_ms=20)

    def call(request: JsonRpcMessage) -> object:
        time.sleep(0.15)
        return {"ok": True}

    server.set_handler("tools/call", call)

    assert client.request("tools/call", None, McpRequestOptions(timeout_ms=0)) == {
        "ok": True
    }
    client.close()


def test_does_not_send_cancelled_for_a_timed_out_initialize(server: _Server) -> None:
    # Pi: "does not send notifications/cancelled for a timed-out initialize"
    server.set_handler("initialize", server.never)
    client = McpClient(_options(request_timeout_ms=20))

    with pytest.raises(McpTimeoutError):
        client.connect(server.client_transport)

    assert client.connection_state == "closed"
    assert server.closed.wait(_WAIT_SECONDS)
    assert server.methods() == ["initialize"]


# -- transport errors and closing ------------------------------------------


def test_reports_transport_errors_without_failing_pending_requests(
    server: _Server,
) -> None:
    # Pi: "reports transport errors without failing pending requests"
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    respond = threading.Event()

    def call(request: JsonRpcMessage) -> object:
        respond.wait(_WAIT_SECONDS)
        return {"content": []}

    server.set_handler("tools/call", call)
    results: list[object] = []
    thread = threading.Thread(
        target=lambda: results.append(client.request("tools/call"))
    )
    thread.start()
    server.wait_for_method("tools/call")

    server.client_transport.emit_error(OSError("stray log line"))
    respond.set()
    thread.join(_WAIT_SECONDS)

    assert results == [{"content": []}]
    assert [str(error) for error in errors] == ["stray log line"]
    client.close()


def test_a_raising_error_listener_does_not_affect_the_others(server: _Server) -> None:
    client = _connect(server)
    errors: list[Exception] = []

    def broken(error: Exception) -> None:
        raise ValueError("listener failed")

    client.on_error(broken)
    removed = client.on_error(lambda error: errors.append(ValueError("removed")))
    client.on_error(errors.append)
    removed()

    server.client_transport.emit_error(OSError("stray"))

    assert [str(error) for error in errors] == ["stray"]
    client.close()


def test_notifies_close_listeners_once_when_the_transport_drops(
    server: _Server,
) -> None:
    # Pi: "notifies close listeners once when the transport drops"
    client = _connect(server)
    closed: list[str] = []
    errors: list[Exception] = []
    client.on_error(errors.append)

    def broken() -> None:
        closed.append("broken")
        raise ValueError("close listener failed")

    client.on_close(broken)
    client.on_close(lambda: closed.append("closed"))
    removed = client.on_close(lambda: closed.append("removed"))
    removed()
    server.set_handler("tools/call", server.never)
    thread, raised = _raises_in_thread(lambda: client.request("tools/call"))
    server.wait_for_method("tools/call")

    server.transport.close()
    thread.join(_WAIT_SECONDS)

    assert len(raised) == 1
    assert isinstance(raised[0], McpConnectionClosedError)
    assert str(raised[0]) == "MCP connection closed"
    assert client.connection_state == "closed"
    client.close()
    client.close()
    assert closed == ["broken", "closed"]
    assert [str(error) for error in errors] == ["close listener failed"]
    with pytest.raises(McpConnectionClosedError, match="^MCP client is closed$"):
        client.request("ping")


def test_close_rejects_pending_requests_notifies_once_and_closes_the_transport(
    server: _Server,
) -> None:
    client = _connect(server)
    closed: list[str] = []
    client.on_close(lambda: closed.append("closed"))
    server.set_handler("tools/call", server.never)
    thread, raised = _raises_in_thread(lambda: client.request("tools/call"))
    server.wait_for_method("tools/call")

    client.close()
    client.close()
    thread.join(_WAIT_SECONDS)

    assert [type(error) for error in raised] == [McpConnectionClosedError]
    assert closed == ["closed"]
    assert client.connection_state == "closed"
    assert server.closed.wait(_WAIT_SECONDS)
    # A closed client sends no cancellation for the requests it dropped.
    assert "notifications/cancelled" not in server.methods()


def test_closing_a_client_that_never_connected_is_allowed() -> None:
    client = McpClient(_options())
    closed: list[str] = []
    client.on_close(lambda: closed.append("closed"))

    client.close()
    client.close()

    assert client.connection_state == "closed"
    assert closed == ["closed"]


# -- server requests and notifications -------------------------------------


def test_answers_roots_list_and_dispatches_notifications(server: _Server) -> None:
    # Pi: "answers roots/list and dispatches notifications"
    client = _connect(server, roots=[{"uri": "file:///workspace", "name": "workspace"}])
    changed: list[object] = []
    client.on_notification("notifications/tools/list_changed", changed.append)

    server.transport.send({"jsonrpc": "2.0", "id": "roots", "method": "roots/list"})
    server.transport.send(
        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    )

    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": "roots",
            "result": {"roots": [{"uri": "file:///workspace", "name": "workspace"}]},
        }
    )
    _wait_until(lambda: changed == [None])
    client.close()


def test_roots_may_come_from_a_callable(server: _Server) -> None:
    roots = [{"uri": "file:///a"}]
    client = _connect(server, roots=lambda: roots)

    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "roots/list"})
    server.wait_for_message(
        {"jsonrpc": "2.0", "id": 1, "result": {"roots": [{"uri": "file:///a"}]}}
    )
    roots.append({"uri": "file:///b"})
    server.transport.send({"jsonrpc": "2.0", "id": 2, "method": "roots/list"})

    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"roots": [{"uri": "file:///a"}, {"uri": "file:///b"}]},
        }
    )
    client.close()


def test_answers_ping_and_rejects_unknown_methods(server: _Server) -> None:
    client = _connect(server)

    server.transport.send({"jsonrpc": "2.0", "id": "p", "method": "ping"})
    server.transport.send({"jsonrpc": "2.0", "id": "r", "method": "roots/list"})
    server.transport.send(
        {"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage"}
    )

    server.wait_for_message({"jsonrpc": "2.0", "id": "p", "result": {}})
    # Without configured roots there is no roots/list handler.
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": "r",
            "error": {"code": -32601, "message": "Method not found: roots/list"},
        }
    )
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {
                "code": -32601,
                "message": "Method not found: sampling/createMessage",
            },
        }
    )
    client.close()


def test_request_handlers_answer_with_their_result_or_error(server: _Server) -> None:
    client = _connect(server)
    seen: list[object] = []

    def echo(params: object, context: McpRequestContext) -> object:
        seen.append(params)
        return {"echo": params}

    def nothing(params: object, context: McpRequestContext) -> object:
        return None

    def broken(params: object, context: McpRequestContext) -> object:
        raise ValueError("handler exploded")

    def refused(params: object, context: McpRequestContext) -> object:
        raise McpError(-32602, "bad params", {"field": "x"})

    def plain(params: object, context: McpRequestContext) -> object:
        raise McpError(-32000, "no data")

    client.set_request_handler("x/echo", echo)
    client.set_request_handler("x/nothing", nothing)
    client.set_request_handler("x/broken", broken)
    client.set_request_handler("x/refused", refused)
    client.set_request_handler("x/plain", plain)

    for request_id, method in enumerate(
        ["x/echo", "x/nothing", "x/broken", "x/refused", "x/plain"], start=1
    ):
        message: JsonRpcMessage = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if method == "x/echo":
            message["params"] = {"a": 1}
        server.transport.send(message)

    server.wait_for_message({"jsonrpc": "2.0", "id": 1, "result": {"echo": {"a": 1}}})
    server.wait_for_message({"jsonrpc": "2.0", "id": 2, "result": {}})
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {"code": -32603, "message": "handler exploded"},
        }
    )
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"code": -32602, "message": "bad params", "data": {"field": "x"}},
        }
    )
    server.wait_for_message(
        {"jsonrpc": "2.0", "id": 5, "error": {"code": -32000, "message": "no data"}}
    )
    assert seen == [{"a": 1}]
    client.close()


def test_a_removed_request_handler_no_longer_answers(server: _Server) -> None:
    client = _connect(server)

    def first(params: object, context: McpRequestContext) -> object:
        return {"handler": "first"}

    def second(params: object, context: McpRequestContext) -> object:
        return {"handler": "second"}

    remove_first = client.set_request_handler("x/who", first)
    remove_second = client.set_request_handler("x/who", second)
    # The first registration was replaced; its disposer leaves the second one.
    remove_first()
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "x/who"})
    server.wait_for_message(
        {"jsonrpc": "2.0", "id": 1, "result": {"handler": "second"}}
    )

    remove_second()
    server.transport.send({"jsonrpc": "2.0", "id": 2, "method": "x/who"})
    server.wait_for_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32601, "message": "Method not found: x/who"},
        }
    )
    client.close()


def test_a_server_cancellation_aborts_the_matching_handler(server: _Server) -> None:
    client = _connect(server)
    started: list[str] = []
    events: dict[str, threading.Event] = {}

    def wait(params: object, context: McpRequestContext) -> object:
        assert isinstance(params, dict)
        name = str(params["name"])
        events[name] = context.cancel_event
        started.append(name)
        cancelled = context.cancel_event.wait(_WAIT_SECONDS)
        return {"name": name, "cancelled": cancelled}

    client.set_request_handler("x/wait", wait)
    server.transport.send(
        {"jsonrpc": "2.0", "id": "a", "method": "x/wait", "params": {"name": "a"}}
    )
    server.transport.send(
        {"jsonrpc": "2.0", "id": "b", "method": "x/wait", "params": {"name": "b"}}
    )
    _wait_until(lambda: sorted(started) == ["a", "b"])

    # A cancellation for an unknown or malformed request id changes nothing.
    for params in ({"requestId": "zzz"}, {"requestId": None}, "nonsense"):
        server.transport.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": params}
        )
    server.transport.send(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "a"},
        }
    )

    server.wait_for_message(
        {"jsonrpc": "2.0", "id": "a", "result": {"name": "a", "cancelled": True}}
    )
    assert not events["b"].is_set()

    # Closing the connection aborts the handlers still running.
    client.close()
    assert events["b"].wait(_WAIT_SECONDS)


def test_a_server_request_arriving_after_the_connection_dropped_is_not_served(
    server: _Server,
) -> None:
    client = _connect(server)
    served: list[object] = []

    def handler(params: object, context: McpRequestContext) -> object:
        served.append(params)
        return {}

    client.set_request_handler("x/late", handler)
    server.transport.close()
    assert client.connection_state == "closed"
    threads = threading.active_count()

    # A message the reading thread had already taken off the wire.
    server.client_transport.emit_message(
        {"jsonrpc": "2.0", "id": 1, "method": "x/late"}
    )
    server.client_transport.emit_message(
        {"jsonrpc": "2.0", "id": 2, "method": "x/unknown"}
    )
    time.sleep(0.1)

    assert served == []
    assert threading.active_count() <= threads


def test_a_slow_handler_does_not_hold_up_other_messages(server: _Server) -> None:
    client = _connect(server)
    release = threading.Event()

    def slow(params: object, context: McpRequestContext) -> object:
        release.wait(_WAIT_SECONDS)
        return {"slow": True}

    client.set_request_handler("x/slow", slow)
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "x/slow"})
    server.transport.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})

    server.wait_for_message({"jsonrpc": "2.0", "id": 2, "result": {}})
    assert {"jsonrpc": "2.0", "id": 1, "result": {"slow": True}} not in server.messages
    release.set()
    server.wait_for_message({"jsonrpc": "2.0", "id": 1, "result": {"slow": True}})
    client.close()


def test_notifications_reach_every_listener_and_a_raising_one_is_isolated(
    server: _Server,
) -> None:
    client = _connect(server)
    errors: list[Exception] = []
    client.on_error(errors.append)
    first: list[object] = []
    second: list[object] = []
    other: list[object] = []

    def broken(params: object) -> None:
        raise ValueError("listener failed")

    client.on_notification("notifications/message", first.append)
    client.on_notification("notifications/message", broken)
    remove_second = client.on_notification("notifications/message", second.append)
    client.on_notification("notifications/other", other.append)

    server.transport.send(
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "one"}}
    )
    _wait_until(lambda: len(second) == 1)
    remove_second()
    remove_second()
    server.transport.send(
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "two"}}
    )
    _wait_until(lambda: len(first) == 2)

    assert first == [{"data": "one"}, {"data": "two"}]
    assert second == [{"data": "one"}]
    assert other == []
    assert [str(error) for error in errors] == ["listener failed", "listener failed"]
    # The connection is unaffected.
    server.set_handler("ping", lambda request: {})
    client.ping()
    client.close()
