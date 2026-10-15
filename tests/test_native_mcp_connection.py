"""Tests for one MCP server connection.

The state machine is tested against an in-memory server, as in Pi's
``mcp-extension.test.ts``; what depends on a server process (stderr, the
working directory, the environment, a server that is still starting) against
the scripted fake stdio server.
"""

from __future__ import annotations

import dataclasses
import json
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.client import McpClient, McpRequestOptions
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.connection import (
    DEFAULT_TIMEOUT_SECONDS,
    STDERR_TAIL_CHARS,
    ConnectionView,
    McpServerConnection,
    McpServerConnectionError,
    create_default_transport,
    expand_home,
)
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore
from pipy_harness.native.mcp.http import McpAuthProvider
from pipy_harness.native.mcp.log import McpServerLog
from pipy_harness.native.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpTimeoutError,
)
from pipy_harness.native.mcp.stdio import StdioTransport, StdioTransportOptions
from pipy_harness.native.mcp.transport import (
    McpTransport,
    create_in_memory_transport_pair,
)

_FAKE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fake_server.py"
_WAIT_SECONDS = 10.0
_OK = {"content": [{"type": "text", "text": "ok"}]}
_NO_ANSWER = object()

Handler = Callable[[JsonRpcMessage], object]


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "inputSchema": {"type": "object"}}


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Server:
    """An in-memory server that answers initialize, tools/list and tools/call."""

    def __init__(
        self,
        *,
        capabilities: dict[str, Any] | None = None,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self.client_end, self.end = create_in_memory_transport_pair()
        self.capabilities = {"tools": {}} if capabilities is None else capabilities
        self.instructions = instructions
        self.tools = tools or []
        self.handlers = handlers or {}
        # Every request received, and every answer to a request of ours.
        self.requests: list[JsonRpcMessage] = []
        self.responses: queue.Queue[JsonRpcMessage] = queue.Queue()
        self.end.on_message(self._on_message)
        self.end.start()

    @property
    def methods(self) -> list[str]:
        return [str(request["method"]) for request in self.requests]

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: JsonRpcMessage = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.end.send(message)

    def _on_message(self, message: JsonRpcMessage) -> None:
        if "method" not in message:
            self.responses.put(message)
            return
        if "id" not in message:
            return
        self.requests.append(message)
        handler = self.handlers.get(str(message["method"]), self._default)
        try:
            result = handler(message)
        except _RpcError as error:
            self.end.send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": error.code, "message": str(error)},
                }
            )
            return
        if result is not _NO_ANSWER:
            self.end.send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _default(self, message: JsonRpcMessage) -> object:
        method = message["method"]
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": self.capabilities,
                "serverInfo": {"name": "fake", "version": "1.0.0"},
            }
            if self.instructions is not None:
                result["instructions"] = self.instructions
            return result
        if method == "tools/list":
            if "tools" not in self.capabilities:
                raise _RpcError(-32601, "Method not found")
            return {"tools": list(self.tools)}
        return _OK


class _Harness:
    """A connection whose transports come from a list of factories, in order."""

    def __init__(
        self,
        transports: list[Callable[[], McpTransport]],
        *,
        config: dict[str, Any] | None = None,
        log: McpServerLog | None = None,
        on_change: Callable[[McpServerConnection], None] | None = None,
        cwd: Path | str | None = None,
        credentials: McpOAuthCredentialStore | None = None,
    ) -> None:
        self.opened: list[McpTransport] = []
        # The auth provider each transport was created with.
        self.auth_providers: list[McpAuthProvider | None] = []
        self.tool_reports: list[tuple[str, ...]] = []
        self.states: list[str] = []
        self._transports = transports
        self._on_change = on_change
        self.connection = McpServerConnection(
            entry=McpServerEntry(
                name="fake", config=config or {"command": "unused"}, source="test"
            ),
            cwd=cwd or os.getcwd(),
            client_version="1.2.3",
            create_transport=self._create,
            log=log,
            on_tools=self._tools_changed,
            on_change=self._changed,
            credentials=credentials,
        )

    def _create(
        self, entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        self.auth_providers.append(auth)
        transport = self._transports[len(self.opened)]()
        self.opened.append(transport)
        return transport

    def _tools_changed(self, connection: McpServerConnection) -> None:
        self.tool_reports.append(
            tuple(str(tool["name"]) for tool in connection.view().tools)
        )

    def _changed(self, connection: McpServerConnection) -> None:
        self.states.append(connection.state)
        if self._on_change is not None:
            self._on_change(connection)


def _tool_names(connection: McpServerConnection) -> list[str]:
    return [str(tool["name"]) for tool in connection.tools]


# -- connecting -------------------------------------------------------------


def test_a_new_connection_is_connecting_and_connects_on_the_first_call() -> None:
    server = _Server(tools=[_tool("echo")])
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    assert connection.view() == ConnectionView(
        name="fake", state="connecting", error=None, tools=(), instructions=None
    )
    assert harness.opened == []
    client = connection.get_client()
    assert isinstance(client, McpClient)
    assert connection.get_client() is client
    assert connection.name == "fake"
    assert connection.state == "connected"
    assert connection.error is None
    assert _tool_names(connection) == ["echo"]
    assert harness.tool_reports == [("echo",)]
    assert server.methods == ["initialize", "tools/list"]
    assert len(harness.opened) == 1
    connection.close()


def test_connects_to_servers_without_the_tools_capability_without_listing_tools() -> (
    None
):
    # Pi: connects to servers without the tools capability without listing tools
    server = _Server(capabilities={"prompts": {}})
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    assert harness.connection.state == "connected"
    assert harness.connection.tools == ()
    assert server.methods == ["initialize"]
    harness.connection.close()


@pytest.mark.parametrize(
    ("declared", "lists"),
    [({}, True), ({"listChanged": True}, True), (None, False), (False, False)],
)
def test_tools_are_listed_when_the_capability_is_declared(
    declared: object, lists: bool
) -> None:
    server = _Server(capabilities={"tools": declared}, tools=[_tool("echo")])
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    assert ("tools/list" in server.methods) is lists
    assert _tool_names(harness.connection) == (["echo"] if lists else [])
    harness.connection.close()


def test_the_client_announces_pipy_and_the_session_directory_as_its_one_root(
    tmp_path: Path,
) -> None:
    session = tmp_path / "my project"
    session.mkdir()
    server = _Server()
    harness = _Harness([lambda: server.client_end], cwd=session)
    harness.connection.get_client()
    params = server.requests[0]["params"]
    assert isinstance(params, dict)
    assert params["clientInfo"] == {"name": "pipy", "version": "1.2.3"}
    assert params["capabilities"] == {"roots": {}}
    server.end.send({"jsonrpc": "2.0", "id": "r1", "method": "roots/list"})
    answer = server.responses.get(timeout=_WAIT_SECONDS)
    assert answer["result"] == {
        "roots": [{"uri": session.resolve().as_uri(), "name": "my project"}]
    }
    assert session.resolve().as_uri().endswith("/my%20project")
    harness.connection.close()


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("  Use search first.\n\n", "Use search first."),
        ("﻿x ", "x"),
        (" \n\t ", None),
        ("", None),
        (None, None),
    ],
)
def test_instructions_are_kept_trimmed_and_empty_ones_count_as_none(
    sent: str | None, kept: str | None
) -> None:
    server = _Server(instructions=sent)
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    assert harness.connection.instructions == kept
    assert harness.connection.view().instructions == kept
    harness.connection.close()


def test_concurrent_callers_share_one_connection_attempt() -> None:
    release = threading.Event()
    arrived = threading.Event()

    def initialize(message: JsonRpcMessage) -> object:
        arrived.set()
        assert release.wait(_WAIT_SECONDS)
        return server._default(message)

    server = _Server(handlers={"initialize": initialize})
    harness = _Harness([lambda: server.client_end, lambda: _Server().client_end])
    clients: queue.Queue[McpClient] = queue.Queue()
    callers = [
        threading.Thread(target=lambda: clients.put(harness.connection.get_client()))
        for _ in range(5)
    ]
    for caller in callers[:2]:
        caller.start()
    assert arrived.wait(_WAIT_SECONDS)
    for caller in callers[2:]:
        caller.start()
    time.sleep(0.05)
    assert harness.connection.state == "connecting"
    release.set()
    for caller in callers:
        caller.join(_WAIT_SECONDS)
    found = [clients.get_nowait() for _ in callers]
    assert all(client is found[0] for client in found)
    assert len(harness.opened) == 1
    assert server.methods == ["initialize", "tools/list"]
    harness.connection.close()


def test_a_caller_that_is_cancelled_stops_waiting_and_the_attempt_goes_on() -> None:
    release = threading.Event()

    def initialize(message: JsonRpcMessage) -> object:
        assert release.wait(_WAIT_SECONDS)
        return server._default(message)

    server = _Server(handlers={"initialize": initialize})
    harness = _Harness([lambda: server.client_end])
    cancel = threading.Event()
    failures: queue.Queue[BaseException] = queue.Queue()

    def call() -> None:
        try:
            harness.connection.call_tool(
                "echo", {}, McpRequestOptions(cancel_event=cancel)
            )
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            failures.put(error)

    caller = threading.Thread(target=call)
    caller.start()
    _wait_until(lambda: server.methods == ["initialize"])
    cancel.set()
    assert isinstance(failures.get(timeout=_WAIT_SECONDS), McpAbortError)
    assert harness.connection.state == "connecting"
    release.set()
    assert harness.connection.call_tool("echo", {}) == _OK
    assert len(harness.opened) == 1
    assert server.methods == ["initialize", "tools/list", "tools/call"]
    harness.connection.close()


# -- timeouts ---------------------------------------------------------------


def test_requests_time_out_after_the_configured_seconds_default_sixty() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 60
    default = _Harness([])
    assert default.connection.timeout_ms == 60_000
    server = _Server(handlers={"tools/call": lambda message: _NO_ANSWER})
    harness = _Harness(
        [lambda: server.client_end], config={"command": "unused", "timeout": 0.05}
    )
    assert harness.connection.timeout_ms == 50
    client = harness.connection.get_client()
    assert client.options.request_timeout_ms == 50
    started = time.monotonic()
    with pytest.raises(McpTimeoutError):
        harness.connection.call_tool("slow", {})
    assert time.monotonic() - started < 5
    harness.connection.close()


def test_progress_notifications_reset_the_timeout() -> None:
    def call(message: JsonRpcMessage) -> object:
        params = message["params"]
        assert isinstance(params, dict)
        token = params["_meta"]["progressToken"]

        def work() -> None:
            # Three steps of 0.6 s against a timeout of 1 s.
            for progress in (1, 2):
                time.sleep(0.6)
                server.notify(
                    "notifications/progress",
                    {"progressToken": token, "progress": progress},
                )
            time.sleep(0.6)
            server.end.send({"jsonrpc": "2.0", "id": message["id"], "result": _OK})

        threading.Thread(target=work, daemon=True).start()
        return _NO_ANSWER

    server = _Server(handlers={"tools/call": call})
    harness = _Harness(
        [lambda: server.client_end], config={"command": "unused", "timeout": 1}
    )
    seen: list[object] = []
    options = McpRequestOptions(on_progress=lambda params: seen.append(params))
    assert harness.connection.call_tool("slow", {}, options) == _OK
    assert [params["progress"] for params in seen if isinstance(params, dict)] == [1, 2]
    harness.connection.close()


# -- tool list changes ------------------------------------------------------


def test_a_tool_list_change_lists_the_tools_again() -> None:
    server = _Server(tools=[_tool("one")])
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    server.tools = [_tool("one"), _tool("two")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: _tool_names(harness.connection) == ["one", "two"])
    _wait_until(lambda: harness.tool_reports == [("one",), ("one", "two")])
    assert harness.connection.state == "connected"
    assert harness.connection.error is None
    server.tools = []
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: harness.tool_reports[-1] == ())
    harness.connection.close()


def test_a_failed_refresh_is_recorded_and_keeps_the_tools() -> None:
    lists = 0

    def list_tools(message: JsonRpcMessage) -> object:
        nonlocal lists
        lists += 1
        if lists == 2:
            raise _RpcError(-32000, "boom")
        return {"tools": [_tool(f"tool{lists}")]}

    server = _Server(handlers={"tools/list": list_tools})
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: harness.connection.error is not None)
    assert harness.connection.error == "Failed to refresh tools: boom"
    assert harness.connection.state == "connected"
    assert _tool_names(harness.connection) == ["tool1"]
    assert harness.tool_reports == [("tool1",)]
    # The next change lists them again; the error stays, as in Pi.
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: _tool_names(harness.connection) == ["tool3"])
    assert harness.connection.call_tool("echo", {}) == _OK
    harness.connection.close()


def test_a_change_announced_while_the_tools_are_first_listed_is_not_lost() -> None:
    lists = 0

    def list_tools(message: JsonRpcMessage) -> object:
        nonlocal lists
        lists += 1
        if lists == 1:
            # The list changes after this answer was put together.
            server.notify("notifications/tools/list_changed")
            return {"tools": [_tool("old")]}
        return {"tools": [_tool("new")]}

    server = _Server(handlers={"tools/list": list_tools})
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    _wait_until(lambda: _tool_names(harness.connection) == ["new"])
    assert harness.tool_reports == [("old",), ("new",)]
    assert lists == 2
    harness.connection.close()


def test_changes_announced_in_a_burst_end_with_the_last_list() -> None:
    lists = 0

    def list_tools(message: JsonRpcMessage) -> object:
        nonlocal lists
        lists += 1
        return {"tools": [_tool(f"v{len(announced)}")]}

    announced: list[int] = []
    server = _Server(handlers={"tools/list": list_tools})
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    for number in range(20):
        announced.append(number)
        server.notify("notifications/tools/list_changed")
    _wait_until(lambda: _tool_names(harness.connection) == ["v20"])
    time.sleep(0.1)
    assert _tool_names(harness.connection) == ["v20"]
    # Notifications that arrive while a refresh runs are answered by one more.
    assert 2 <= lists <= 21
    harness.connection.close()


# -- dropped and failed connections -----------------------------------------


def test_marks_a_dropped_connection_and_reconnects_on_the_next_call() -> None:
    # Pi: marks a dropped connection and reconnects on the next call
    servers = [_Server(tools=[_tool("echo")]), _Server(tools=[_tool("echo2")])]
    harness = _Harness([lambda: servers[0].client_end, lambda: servers[1].client_end])
    connection = harness.connection
    connection.get_client()
    servers[0].end.close()
    _wait_until(lambda: connection.state == "disconnected")
    assert connection.error == "Connection closed"
    # The tools of the dropped server stay known until the next connect.
    assert _tool_names(connection) == ["echo"]
    assert connection.call_tool("echo", {}) == _OK
    assert connection.state == "connected"
    assert connection.error is None
    assert _tool_names(connection) == ["echo2"]
    assert len(harness.opened) == 2
    assert {"connecting", "connected", "disconnected"} <= set(harness.states)
    connection.close()
    assert harness.states[-1] == "closed"


def test_reconnect_drops_the_client_and_connects_again() -> None:
    # Pi `reconnect`: the dropped client is not reported as disconnected.
    servers = [_Server(tools=[_tool("echo")]), _Server(tools=[_tool("echo2")])]
    closed = threading.Event()
    servers[0].client_end.on_close(closed.set)
    harness = _Harness([lambda: servers[0].client_end, lambda: servers[1].client_end])
    connection = harness.connection
    first = connection.get_client()

    connection.reconnect()

    # The old transport had closed before the new one was opened.
    assert closed.is_set()
    assert first.connection_state != "connected"
    assert connection.state == "connected"
    assert _tool_names(connection) == ["echo2"]
    assert len(harness.opened) == 2
    assert harness.states == ["connecting", "connected", "connecting", "connected"]
    assert harness.tool_reports == [("echo",), ("echo2",)]
    connection.close()


def test_reconnect_waits_for_a_connect_in_progress() -> None:
    release = threading.Event()

    def slow_initialize(message: JsonRpcMessage) -> object:
        release.wait(_WAIT_SECONDS)
        return servers[0]._default(message)

    servers = [_Server(handlers={"initialize": slow_initialize}), _Server()]
    harness = _Harness([lambda: servers[0].client_end, lambda: servers[1].client_end])
    connection = harness.connection
    connecting = threading.Thread(target=connection.get_client, daemon=True)
    connecting.start()
    _wait_until(lambda: "initialize" in servers[0].methods)
    reconnecting = threading.Thread(target=connection.reconnect, daemon=True)
    reconnecting.start()
    reconnecting.join(0.1)

    assert reconnecting.is_alive() and len(harness.opened) == 1
    release.set()
    reconnecting.join(_WAIT_SECONDS)
    connecting.join(_WAIT_SECONDS)

    assert not reconnecting.is_alive()
    assert len(harness.opened) == 2
    assert connection.state == "connected"
    connection.close()


def test_reconnect_connects_a_server_that_failed_or_dropped() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "not today")

    servers = [_Server(handlers={"initialize": refuse}), _Server(), _Server()]
    harness = _Harness([lambda server=server: server.client_end for server in servers])  # type: ignore[misc]
    connection = harness.connection
    with pytest.raises(McpServerConnectionError):
        connection.get_client()

    connection.reconnect()
    assert connection.state == "connected"
    servers[1].end.close()
    _wait_until(lambda: connection.state == "disconnected")
    connection.reconnect()

    assert connection.state == "connected" and connection.error is None
    assert len(harness.opened) == 3
    connection.close()


def test_reconnect_raises_the_failure_of_the_new_connect() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "not today")

    servers = [_Server(), _Server(handlers={"initialize": refuse})]
    harness = _Harness([lambda: servers[0].client_end, lambda: servers[1].client_end])
    connection = harness.connection
    connection.get_client()

    with pytest.raises(McpServerConnectionError) as raised:
        connection.reconnect()

    assert str(raised.value) == 'MCP server "fake" failed to connect: not today'
    assert connection.state == "failed"
    connection.close()


def test_reconnect_of_a_closed_connection_is_refused() -> None:
    server = _Server()
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    harness.connection.close()

    with pytest.raises(McpServerConnectionError, match="is shut down"):
        harness.connection.reconnect()

    assert len(harness.opened) == 1 and harness.connection.state == "closed"


def test_a_failed_connect_is_recorded_and_the_next_call_tries_again() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "not today")

    broken = _Server(handlers={"initialize": refuse})
    working = _Server()
    closed = threading.Event()
    broken.client_end.on_close(closed.set)
    harness = _Harness([lambda: broken.client_end, lambda: working.client_end])
    connection = harness.connection
    with pytest.raises(McpServerConnectionError) as raised:
        connection.get_client()
    assert str(raised.value) == 'MCP server "fake" failed to connect: not today'
    assert connection.state == "failed"
    assert connection.error == "not today"
    assert closed.is_set()
    assert harness.tool_reports == []
    assert connection.call_tool("echo", {}) == _OK
    assert connection.state == "connected"
    assert connection.error is None
    connection.close()


def test_a_transport_that_cannot_be_created_fails_the_connect() -> None:
    def create() -> McpTransport:
        raise ValueError("no transport")

    harness = _Harness([create])
    with pytest.raises(McpServerConnectionError) as raised:
        harness.connection.call_tool("echo", {})
    assert str(raised.value) == 'MCP server "fake" failed to connect: no transport'
    assert isinstance(raised.value.__cause__, ValueError)
    assert harness.connection.state == "failed"
    harness.connection.close()
    assert harness.connection.state == "closed"


def test_a_connection_that_closes_while_the_tools_are_listed_fails_the_connect() -> (
    None
):
    def list_tools(message: JsonRpcMessage) -> object:
        server.end.close()
        return _NO_ANSWER

    server = _Server(handlers={"tools/list": list_tools})
    harness = _Harness([lambda: server.client_end])
    with pytest.raises(McpServerConnectionError, match="failed to connect"):
        harness.connection.get_client()
    assert harness.connection.state == "failed"
    assert harness.connection.error == "MCP connection closed"
    harness.connection.close()


def test_a_callback_that_raises_does_not_break_the_connection() -> None:
    def explode(connection: McpServerConnection) -> None:
        raise RuntimeError("callback failed")

    server = _Server(tools=[_tool("one")])
    harness = _Harness([lambda: server.client_end], on_change=explode)
    assert harness.connection.call_tool("echo", {}) == _OK
    server.tools = [_tool("two")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: _tool_names(harness.connection) == ["two"])
    harness.connection.close()
    assert harness.connection.state == "closed"


# -- closing ----------------------------------------------------------------


def test_closes_a_connection_that_is_still_initializing_before_close_returns() -> None:
    # Pi: closes a connection that is still initializing before close returns
    client_end, server_end = create_in_memory_transport_pair()
    initializing = threading.Event()
    # The server receives initialize but never answers it.
    server_end.on_message(lambda message: initializing.set())
    server_end.start()
    transport_closed = threading.Event()
    client_end.on_close(transport_closed.set)
    harness = _Harness([lambda: client_end])
    failures: queue.Queue[BaseException] = queue.Queue()

    def connect() -> None:
        try:
            harness.connection.get_client()
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            failures.put(error)

    caller = threading.Thread(target=connect)
    caller.start()
    assert initializing.wait(_WAIT_SECONDS)
    harness.connection.close()
    assert transport_closed.is_set()
    assert not any(thread.name == "mcp-connect" for thread in threading.enumerate())
    failure = failures.get(timeout=_WAIT_SECONDS)
    assert isinstance(failure, McpServerConnectionError)
    assert "failed to connect" in str(failure)
    assert harness.connection.state == "closed"
    caller.join(_WAIT_SECONDS)


def test_a_stdio_start_that_cannot_start_a_thread_fails_and_close_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Goal check r5, P1: the failed connect's cleanup and close() waited
    # without end for a stdio watcher thread that never started.
    start = threading.Thread.start

    def failing_start(thread: threading.Thread) -> None:
        if thread.name == "mcp-stdio-wait":
            raise RuntimeError("can't start new thread")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", failing_start)
    # A server that would run for a minute: only the start's SIGKILL ends it.
    harness = _Harness(
        [
            lambda: StdioTransport(
                StdioTransportOptions(
                    command=sys.executable, args=("-c", "import time; time.sleep(60)")
                )
            )
        ]
    )
    failures: list[BaseException] = []

    def connect_and_close() -> None:
        try:
            harness.connection.get_client()
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            failures.append(error)
        harness.connection.close()

    caller = threading.Thread(target=connect_and_close, daemon=True)
    caller.start()
    caller.join(_WAIT_SECONDS)

    assert not caller.is_alive()
    assert [type(error) for error in failures] == [McpServerConnectionError]
    assert "can't start new thread" in str(failures[0])
    assert harness.connection.state == "closed"
    [transport] = harness.opened
    assert isinstance(transport, StdioTransport)
    assert transport.pid is None


def test_calls_after_close_fail_as_shut_down() -> None:
    server = _Server()
    closed = threading.Event()
    server.client_end.on_close(closed.set)
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    harness.connection.close()
    assert closed.is_set()
    assert harness.connection.state == "closed"
    for call in (
        harness.connection.get_client,
        lambda: harness.connection.call_tool("echo", {}),
    ):
        with pytest.raises(McpServerConnectionError) as raised:
            call()
        assert str(raised.value) == 'MCP server "fake" is shut down'
    harness.connection.close()
    assert harness.connection.state == "closed"
    assert len(harness.opened) == 1


def test_a_connection_that_never_connected_closes_without_a_transport() -> None:
    harness = _Harness([])
    harness.connection.close()
    assert harness.connection.state == "closed"
    assert harness.states == ["closed"]
    with pytest.raises(McpServerConnectionError, match="is shut down"):
        harness.connection.get_client()


def test_a_drop_after_close_does_not_change_the_closed_state() -> None:
    server = _Server()
    harness = _Harness([lambda: server.client_end])
    harness.connection.get_client()
    harness.connection.close()
    server.end.close()
    time.sleep(0.05)
    assert harness.connection.view().state == "closed"
    assert harness.connection.error is None


def test_a_callback_may_close_the_connection_it_reports() -> None:
    def close_when_connected(connection: McpServerConnection) -> None:
        if connection.state == "connected":
            connection.close()

    server = _Server()
    closed = threading.Event()
    server.client_end.on_close(closed.set)
    harness = _Harness([lambda: server.client_end], on_change=close_when_connected)
    done = threading.Event()

    def connect() -> None:
        try:
            harness.connection.get_client()
        finally:
            done.set()

    threading.Thread(target=connect, daemon=True).start()
    assert done.wait(_WAIT_SECONDS), "close() from a callback deadlocked"
    assert closed.wait(_WAIT_SECONDS)
    assert harness.connection.state == "closed"
    # A later close from another thread returns too.
    harness.connection.close()


def test_two_threads_closing_both_return_after_the_transport_closed() -> None:
    client_end, server_end = create_in_memory_transport_pair()
    initializing = threading.Event()
    server_end.on_message(lambda message: initializing.set())
    server_end.start()
    transport_closed = threading.Event()
    client_end.on_close(transport_closed.set)
    harness = _Harness([lambda: client_end])
    threading.Thread(
        target=lambda: pytest.raises(
            McpServerConnectionError, harness.connection.get_client
        ),
        daemon=True,
    ).start()
    assert initializing.wait(_WAIT_SECONDS)
    seen: queue.Queue[bool] = queue.Queue()

    def close() -> None:
        harness.connection.close()
        seen.put(transport_closed.is_set())

    closers = [threading.Thread(target=close) for _ in range(3)]
    for closer in closers:
        closer.start()
    for closer in closers:
        closer.join(_WAIT_SECONDS)
    assert [seen.get_nowait() for _ in closers] == [True, True, True]


def test_close_waits_for_a_transport_that_somebody_else_is_closing(
    tmp_path: Path,
) -> None:
    # The server ignores the end of stdin, so closing it takes at least the
    # 0.5 s until SIGTERM.
    process = _Process(tmp_path, {"onStdinClose": []})
    connection = process.connection
    client = connection.get_client()
    pid = process.transports[0].pid
    assert pid is not None
    closer = threading.Thread(target=client.close)
    closer.start()
    _wait_until(lambda: connection.state == "disconnected")
    assert _is_alive(pid)
    connection.close()
    assert not _is_alive(pid)
    assert process.transports[0].pid is None
    closer.join(_WAIT_SECONDS)
    assert connection.state == "closed"


def test_close_waits_for_a_client_another_thread_started_to_close(
    tmp_path: Path,
) -> None:
    process = _Process(tmp_path, {"onStdinClose": []})
    connection = process.connection
    client = connection.get_client()
    pid = process.transports[0].pid
    assert pid is not None
    entered = threading.Event()
    proceed = threading.Event()

    def hold() -> None:
        entered.set()
        assert proceed.wait(_WAIT_SECONDS)

    # A close listener that keeps the other thread inside ``client.close()``
    # after the connection heard of the drop and before the transport is
    # closed.
    client.on_close(hold)
    closer = threading.Thread(target=client.close)
    closer.start()
    assert entered.wait(_WAIT_SECONDS)
    threading.Timer(0.2, proceed.set).start()
    connection.close()
    assert not _is_alive(pid)
    closer.join(_WAIT_SECONDS)


def test_a_callback_may_close_the_connection_when_it_hears_of_a_drop() -> None:
    def close_when_dropped(connection: McpServerConnection) -> None:
        if connection.state == "disconnected":
            connection.close()

    server = _Server()
    harness = _Harness([lambda: server.client_end], on_change=close_when_dropped)
    client = harness.connection.get_client()
    done = threading.Event()

    def close_client() -> None:
        client.close()
        done.set()

    threading.Thread(target=close_client, daemon=True).start()
    assert done.wait(_WAIT_SECONDS), "close() from a drop callback deadlocked"
    assert harness.connection.state == "closed"
    harness.connection.close()


def test_a_drop_callback_closing_after_another_thread_started_to_close() -> None:
    in_callback = threading.Event()
    go = threading.Event()

    def close_when_dropped(connection: McpServerConnection) -> None:
        if connection.state == "disconnected":
            in_callback.set()
            assert go.wait(_WAIT_SECONDS)
            connection.close()

    server = _Server()
    transport_closed = threading.Event()
    server.client_end.on_close(transport_closed.set)
    harness = _Harness([lambda: server.client_end], on_change=close_when_dropped)
    client = harness.connection.get_client()
    dropper = threading.Thread(target=client.close, daemon=True)
    dropper.start()
    assert in_callback.wait(_WAIT_SECONDS)
    seen: list[bool] = []

    def close() -> None:
        harness.connection.close()
        seen.append(transport_closed.is_set())

    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    _wait_until(lambda: harness.connection.state == "closed")
    time.sleep(0.05)
    # The first closer waits for the transport the callback's thread holds.
    assert closer.is_alive() and not transport_closed.is_set()
    go.set()
    dropper.join(_WAIT_SECONDS)
    closer.join(_WAIT_SECONDS)
    assert not dropper.is_alive() and not closer.is_alive(), "close() deadlocked"
    assert seen == [True]


def test_close_waits_for_the_transport_after_a_drop_callback_closed_first() -> None:
    closed_in_callback = threading.Event()
    go = threading.Event()

    def close_when_dropped(connection: McpServerConnection) -> None:
        if connection.state == "disconnected":
            connection.close()
            closed_in_callback.set()
            # Keeps the thread that is closing the client, so its transport
            # stays open.
            assert go.wait(_WAIT_SECONDS)

    server = _Server()
    transport_closed = threading.Event()
    server.client_end.on_close(transport_closed.set)
    harness = _Harness([lambda: server.client_end], on_change=close_when_dropped)
    client = harness.connection.get_client()
    dropper = threading.Thread(target=client.close, daemon=True)
    dropper.start()
    assert closed_in_callback.wait(_WAIT_SECONDS)
    assert harness.connection.state == "closed"
    seen: list[bool] = []

    def close() -> None:
        harness.connection.close()
        seen.append(transport_closed.is_set())

    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    time.sleep(0.1)
    assert closer.is_alive() and not transport_closed.is_set()
    go.set()
    closer.join(_WAIT_SECONDS)
    dropper.join(_WAIT_SECONDS)
    assert seen == [True]


def test_close_returns_after_a_transport_that_was_handed_over_closed() -> None:
    def create() -> McpTransport:
        server = _Server()
        # Closed before the connection sees it: it never reports a close.
        server.end.close()
        return server.client_end

    harness = _Harness([create, create])
    with pytest.raises(McpServerConnectionError, match="failed to connect"):
        harness.connection.get_client()
    assert harness.connection.state == "failed"
    done = threading.Event()

    def close() -> None:
        harness.connection.close()
        done.set()

    threading.Thread(target=close, daemon=True).start()
    assert done.wait(_WAIT_SECONDS), "close() waited for a close that never comes"
    assert harness.connection.state == "closed"


def test_a_callback_asking_for_the_client_of_a_failed_attempt_is_refused() -> None:
    asked: list[str] = []

    def ask(connection: McpServerConnection) -> None:
        if connection.state == "failed":
            try:
                connection.get_client()
            except McpServerConnectionError as error:
                asked.append(str(error))

    def create() -> McpTransport:
        raise ValueError("no transport")

    harness = _Harness([create], on_change=ask)
    with pytest.raises(McpServerConnectionError, match="no transport"):
        harness.connection.get_client()
    assert asked == ['MCP server "fake" is still connecting']
    harness.connection.close()


# -- server log -------------------------------------------------------------


def test_appends_server_log_messages_to_the_log_file(tmp_path: Path) -> None:
    # Pi: appends server log messages to the log file
    path = tmp_path / "mcp.log"
    server = _Server()
    harness = _Harness([lambda: server.client_end], log=McpServerLog(path))
    harness.connection.get_client()
    server.notify(
        "notifications/message",
        {"level": "warning", "logger": "db", "data": "slow\nquery"},
    )
    server.notify("notifications/message", {"level": "error", "data": {"code": 7}})
    expected = '[fake] warning db: slow\n    query\n[fake] error {"code":7}\n'

    def lines() -> str:
        if not path.exists():
            return ""
        return re.sub(r"(?m)^\S+ ", "", path.read_text(encoding="utf-8"))

    _wait_until(lambda: lines() == expected)
    harness.connection.close()


# -- the default transport ----------------------------------------------------


def _entry(config: dict[str, Any], name: str = "files") -> McpServerEntry:
    return McpServerEntry(name=name, config=config, source="test")


def _stdio(entry: McpServerEntry, cwd: Path | str) -> StdioTransport:
    transport = create_default_transport(entry, str(cwd), None)
    assert isinstance(transport, StdioTransport)
    return transport


def test_expand_home_names_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    home = str(tmp_path)
    assert expand_home("~") == home
    assert expand_home("~/") == home
    assert expand_home("~/bin/server") == f"{home}/bin/server"
    assert expand_home("~/data/") == f"{home}/data/"
    assert expand_home("~/a/../b") == f"{home}/b"
    # A second slash does not make the rest an absolute path.
    assert expand_home("~//bin/server") == f"{home}/bin/server"
    assert expand_home("~//") == f"{home}/"
    assert expand_home("~/./") == f"{home}/"
    # Only a leading ``~`` or ``~/`` is the home directory.
    for value in ("~other/x", "a/~/b", "/abs/~", "relative", "", "~~", "--dir=~/x"):
        assert expand_home(value) == value


def test_home_is_expanded_in_command_arguments_and_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    session = tmp_path / "session"
    monkeypatch.setenv("HOME", str(home))
    config = {
        "command": "~/bin/server",
        "args": ["~", "~/data", "--root=~/x", "plain"],
        "cwd": "~/work",
    }
    options = _stdio(_entry(config), session).options
    assert options.command == f"{home}/bin/server"
    assert options.args == (str(home), f"{home}/data", "--root=~/x", "plain")
    assert options.cwd == f"{home}/work"
    assert options.env == {}
    assert options.inherit_env is True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "{session}"),
        (".", "{session}"),
        ("", "{session}"),
        ("sub/dir", "{session}/sub/dir"),
        ("./sub/", "{session}/sub"),
        ("../sibling", "{parent}/sibling"),
        ("/abs/elsewhere", "/abs/elsewhere"),
    ],
)
def test_a_relative_cwd_resolves_against_the_session_directory(
    tmp_path: Path, configured: str | None, expected: str
) -> None:
    session = tmp_path / "session"
    config: dict[str, Any] = {"command": "server"}
    if configured is not None:
        config["cwd"] = configured
    options = _stdio(_entry(config), session).options
    assert options.cwd == expected.format(session=session, parent=tmp_path)
    assert options.command == "server"
    assert options.args == ()


def test_env_values_are_resolved_and_an_unresolved_one_names_server_and_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIPY_TEST_MCP_TOKEN", "t0k")
    monkeypatch.delenv("PIPY_TEST_MCP_UNSET", raising=False)
    config = {
        "command": "server",
        "env": {
            "API_KEY": "Bearer ${PIPY_TEST_MCP_TOKEN}",
            "FROM_COMMAND": "!printf from-command",
            "LITERAL": "a$$b",
        },
    }
    assert _stdio(_entry(config), tmp_path).options.env == {
        "API_KEY": "Bearer t0k",
        "FROM_COMMAND": "from-command",
        "LITERAL": "a$b",
    }
    config["env"] = {"API_KEY": "${PIPY_TEST_MCP_UNSET}"}
    message = (
        'Failed to resolve MCP server "files" env "API_KEY" '
        "from environment variable: PIPY_TEST_MCP_UNSET"
    )
    with pytest.raises(Exception, match=re.escape(message)):
        create_default_transport(_entry(config), str(tmp_path), None)
    # Through a connection, that fails the connect and starts no process.
    connection = McpServerConnection(
        entry=_entry(config),
        cwd=tmp_path,
        client_version="1.2.3",
        on_tools=lambda connection: None,
    )
    with pytest.raises(McpServerConnectionError) as raised:
        connection.get_client()
    assert str(raised.value) == f'MCP server "files" failed to connect: {message}'
    assert connection.state == "failed"
    assert connection.error == message
    connection.close()


# -- against a server process -------------------------------------------------


class _Process:
    """A connection to the fake stdio server, made by the default transport."""

    def __init__(
        self,
        tmp_path: Path,
        scenario: dict[str, Any],
        *,
        config: dict[str, Any] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.record_path = tmp_path / "record.jsonl"
        scenario_path = tmp_path / "scenario.json"
        scenario_path.write_text(
            json.dumps({"record": str(self.record_path), **scenario}),
            encoding="utf-8",
        )
        self.transports: list[StdioTransport] = []
        self.pids: list[int] = []
        self.connection = McpServerConnection(
            entry=McpServerEntry(
                name="fake",
                config={
                    "command": sys.executable,
                    "args": ["-I", str(_FAKE_SERVER), "--scenario", str(scenario_path)],
                    **(config or {}),
                },
                source="test",
            ),
            cwd=cwd or tmp_path,
            client_version="1.2.3",
            create_transport=self._create,
            on_tools=lambda connection: None,
        )

    def _create(
        self, entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        transport = create_default_transport(entry, cwd, auth)
        assert isinstance(transport, StdioTransport)
        self.transports.append(transport)
        return transport

    def received(self) -> list[str]:
        if not self.record_path.exists():
            return []
        lines = self.record_path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        return [r["received"]["method"] for r in records if "received" in r]


def test_a_stdio_server_runs_in_the_configured_directory_and_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIPY_TEST_MCP_TOKEN", "t0k")
    session = tmp_path / "session"
    (session / "sub").mkdir(parents=True)
    process = _Process(
        tmp_path,
        {"tools": [_tool("echo")]},
        config={"cwd": "sub", "env": {"API_KEY": "key-${PIPY_TEST_MCP_TOKEN}"}},
        cwd=session,
    )
    connection = process.connection
    client = connection.get_client()
    info = client.request("fixture/process")
    assert isinstance(info, dict)
    assert Path(info["cwd"]) == (session / "sub").resolve()
    assert info["env"]["API_KEY"] == "key-t0k"
    assert info["env"]["PIPY_TEST_MCP_TOKEN"] == "t0k"
    assert _tool_names(connection) == ["echo"]
    assert connection.call_tool("echo", {"text": "hi"}) == {
        "content": [{"type": "text", "text": "hi"}]
    }
    pid = process.transports[0].pid
    assert pid is not None and _is_alive(pid)
    connection.close()
    assert not _is_alive(pid)


def test_a_dropped_stdio_server_is_reported_with_its_stderr_and_restarted(
    tmp_path: Path,
) -> None:
    scenario = {
        "tools": [_tool("echo"), _tool("crash")],
        "methods": {
            "tools/call": [{"stderr": "  fatal: disk gone\n"}, {"exit": 3}],
        },
    }
    process = _Process(tmp_path, scenario)
    connection = process.connection
    connection.get_client()
    with pytest.raises(McpConnectionClosedError):
        connection.call_tool("crash", {})
    _wait_until(lambda: connection.state == "disconnected")
    assert connection.error == "Connection closed\nfatal: disk gone"
    # The next call starts the server again.
    client = connection.get_client()
    assert connection.state == "connected"
    assert connection.error is None
    assert len(process.transports) == 2
    assert client.request("ping") == {}
    connection.close()


def test_a_dropped_server_reports_only_the_end_of_a_long_stderr(
    tmp_path: Path,
) -> None:
    scenario = {
        "tools": [_tool("crash")],
        "methods": {
            "tools/call": [
                {"stderr": "x" * 3000},
                {"stderr": "THE END\n"},
                {"exit": 1},
            ],
        },
    }
    process = _Process(tmp_path, scenario)
    connection = process.connection
    connection.get_client()
    with pytest.raises(McpConnectionClosedError):
        connection.call_tool("crash", {})
    _wait_until(lambda: connection.state == "disconnected")
    error = connection.error
    assert error is not None
    head, tail = error.split("\n", 1)
    assert head == "Connection closed"
    assert len(tail) == STDERR_TAIL_CHARS == 2000
    assert tail == "x" * 1993 + "THE END"
    connection.close()


def test_a_stdio_server_that_fails_to_start_is_reported_with_its_stderr(
    tmp_path: Path,
) -> None:
    scenario = {"startup": [{"stderr": "cannot open database\n"}, {"exit": 2}]}
    process = _Process(tmp_path, scenario)
    connection = process.connection
    with pytest.raises(McpServerConnectionError) as raised:
        connection.get_client()
    assert connection.state == "failed"
    assert connection.error == "MCP connection closed\ncannot open database"
    assert str(raised.value) == (
        'MCP server "fake" failed to connect: MCP connection closed\n'
        "cannot open database"
    )
    # A stdio connect is not retried.
    assert len(process.transports) == 1
    connection.close()
    assert connection.state == "closed"


def test_close_waits_for_an_unconnected_transport_somebody_else_is_closing(
    tmp_path: Path,
) -> None:
    # The server never answers initialize and ignores stdin and SIGTERM, so
    # closing its transport takes about a second, longer than the request
    # timeout that ends the connect.
    scenario = {
        "methods": {"initialize": [{"sleep": 60}]},
        "onStdinClose": [],
        "onSigterm": [],
    }
    process = _Process(tmp_path, scenario, config={"timeout": 0.3})

    def create(
        entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        made = create_default_transport(entry, cwd, auth)
        assert isinstance(made, StdioTransport)
        transport = StdioTransport(
            dataclasses.replace(made.options, close_timeout_ms=600)
        )
        process.transports.append(transport)
        return transport

    connection = McpServerConnection(
        entry=process.connection.entry,
        cwd=tmp_path,
        client_version="1.2.3",
        create_transport=create,
        on_tools=lambda connection: None,
    )
    caller = threading.Thread(
        target=lambda: pytest.raises(McpServerConnectionError, connection.get_client)
    )
    caller.start()
    _wait_until(lambda: process.received() == ["initialize"])
    transport = process.transports[0]
    pid = transport.pid
    assert pid is not None
    outsider = threading.Thread(target=transport.close)
    outsider.start()
    # The connect ends while the outsider is still closing the transport.
    _wait_until(lambda: connection.state == "failed")
    connection.close()
    assert not _is_alive(pid)
    assert transport.pid is None
    outsider.join(_WAIT_SECONDS)
    caller.join(_WAIT_SECONDS)


def test_a_missing_executable_fails_the_connect(tmp_path: Path) -> None:
    connection = McpServerConnection(
        entry=_entry({"command": str(tmp_path / "no-such-server")}),
        cwd=tmp_path,
        client_version="1.2.3",
        on_tools=lambda connection: None,
    )
    with pytest.raises(McpServerConnectionError) as raised:
        connection.get_client()
    assert str(raised.value).startswith('MCP server "files" failed to connect: ')
    assert "no-such-server" in str(raised.value)
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert connection.state == "failed"
    connection.close()


def test_closing_ends_a_stdio_server_that_has_not_answered_initialize(
    tmp_path: Path,
) -> None:
    scenario = {"methods": {"initialize": [{"sleep": 60}, {"default": True}]}}
    process = _Process(tmp_path, scenario)
    connection = process.connection
    failures: queue.Queue[BaseException] = queue.Queue()

    def connect() -> None:
        try:
            connection.get_client()
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            failures.put(error)

    caller = threading.Thread(target=connect)
    caller.start()
    _wait_until(lambda: process.received() == ["initialize"])
    pid = process.transports[0].pid
    assert pid is not None and _is_alive(pid)
    started = time.monotonic()
    connection.close()
    # No transport of the server is open when close returns.
    assert not _is_alive(pid)
    assert process.transports[0].pid is None
    assert time.monotonic() - started < 8
    failure = failures.get(timeout=_WAIT_SECONDS)
    assert isinstance(failure, McpServerConnectionError)
    assert "failed to connect" in str(failure)
    assert connection.state == "closed"
    caller.join(_WAIT_SECONDS)
    with pytest.raises(McpServerConnectionError, match="is shut down"):
        connection.get_client()
    assert len(process.transports) == 1
