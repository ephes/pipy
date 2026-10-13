"""Tests for the session's MCP manager (MCP parity plan S17, S21).

The manager is tested against in-memory servers and a real capability value;
what depends on a server process against the scripted fake stdio server. The
session level (a provider, hooks, codemode) is in
``test_native_mcp_session.py``.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_connection import (
    _FAKE_SERVER,
    _NO_ANSWER,
    _is_alive,
    _RpcError,
    _Server,
    _wait_until,
)
from test_native_tool_filter_patterns import _Tool

from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.mcp import manager as manager_module
from pipy_harness.native.mcp.config import (
    LoadedMcpConfig,
    McpServerEntry,
    load_mcp_config,
)
from pipy_harness.native.mcp.connection import (
    ConnectionView,
    McpServerConnection,
    create_default_transport,
)
from pipy_harness.native.mcp.http import McpAuthProvider
from pipy_harness.native.mcp.manager import (
    McpManager,
    McpServerStatus,
    describe_state,
)
from pipy_harness.native.mcp.stdio import StdioTransport
from pipy_harness.native.mcp.transport import McpTransport
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolNamespace,
    ToolPort,
    ToolRequest,
)

_WAIT_SECONDS = 10.0


def _tool(name: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "inputSchema": {"type": "object"}, **fields}


def _entry(name: str, **config: Any) -> McpServerEntry:
    return McpServerEntry(
        name=name, config={"command": "unused", **config}, source="test"
    )


class _Session:
    """A manager over in-memory servers, publishing into a capability value."""

    def __init__(
        self,
        tmp_path: Path,
        servers: dict[str, _Server],
        entries: list[McpServerEntry] | None = None,
        *,
        builtins: dict[str, ToolPort] | None = None,
        state_lock: Any = None,
        errors: list[str] | None = None,
        project_config: str | None = None,
    ) -> None:
        self.servers = servers
        # What a session change saved, and the error the next save raises.
        self.saves: list[tuple[McpServerEntry, dict[str, object]]] = []
        self.save_error: Exception | None = None
        self.opened: list[str] = []
        self.published: list[tuple[str, ...]] = []
        self.tools = NativeToolCapabilities(
            builtins or {"read": _Tool("read")},
            {},
            workspace_root=tmp_path,
            filter_options=ToolFilterOptions.empty(),
            cancel_join_timeout_seconds=0.1,
            state_lock=state_lock,
        )
        self.manager = McpManager(
            config=LoadedMcpConfig(
                servers=entries
                if entries is not None
                else [_entry(name) for name in servers],
                errors=errors or [],
                project_config=project_config,
            ),
            cwd=tmp_path,
            client_version="1.2.3",
            publish_tools=self._publish,
            create_transport=self._create,
            update_config=self._save,
            deactivate_tools=self.tools.deactivate_indirect_mcp_tools,
        )

    def _save(self, entry: McpServerEntry, patch: Any) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saves.append((entry, dict(patch)))

    def _create(
        self, entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        self.opened.append(entry.name)
        return self.servers[entry.name].client_end

    def _publish(self, registry: Any) -> tuple[str, ...]:
        self.published.append(tuple(registry))
        return self.tools.publish_mcp_tools(registry)

    def start(self) -> None:
        self.manager.start()
        assert self.manager.wait_until_settled(_WAIT_SECONDS)

    def mcp_names(self) -> tuple[str, ...]:
        return tuple(self.tools.state.mcp_registry)

    def declared(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.tools.definitions())

    def callable_names(self) -> frozenset[str]:
        return self.tools.callable_snapshot().callable_names()

    def port(self, name: str) -> ToolPort:
        return self.tools.state.mcp_registry[name]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        outcome = self.tools.execute(
            AgentToolCall(
                provider_correlation_id="call-1",
                tool_name=name,
                arguments_json=ProductContent(json.dumps(arguments or {})),
            )
        )
        return outcome.result.content.value

    def notices(self) -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        self.manager.deliver_notices(
            lambda level, text: collected.append((level, text))
        )
        return collected


def _invoke(port: ToolPort, tmp_path: Path) -> tuple[bool, str]:
    result = port.invoke(
        ToolRequest(
            tool_request_id="pipy-tool-1",
            tool_name=port.definition.name,
            arguments={},
        ),
        ToolContext(workspace_root=tmp_path),
    )
    return result.is_error, result.output_text


@pytest.fixture
def sessions() -> Iterator[list[_Session]]:
    opened: list[_Session] = []
    yield opened
    for session in opened:
        session.manager.close()


# -- SES-01: start ------------------------------------------------------------


def test_start_connects_enabled_servers_in_the_background(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return slow._default(message)

    slow = _Server(tools=[_tool("wait")], handlers={"initialize": slow_initialize})
    servers = {"docs": _Server(tools=[_tool("search")]), "slow": slow, "off": _Server()}
    session = _Session(
        tmp_path,
        servers,
        [_entry("docs"), _entry("slow"), _entry("off", enabled=False)],
    )
    sessions.append(session)

    session.manager.start()  # returns while `slow` has not answered

    _wait_until(lambda: session.mcp_names() == ("mcp__docs__search",))
    assert not session.manager.wait_until_settled(0.05)
    states = {
        status.entry.name: (
            status.enabled,
            None if status.connection is None else status.connection.state,
        )
        for status in session.manager.servers()
    }
    assert states == {
        "docs": (True, "connected"),
        "slow": (True, "connecting"),
        "off": (False, None),
    }
    assert "off" not in session.opened

    release.set()
    assert session.manager.wait_until_settled(_WAIT_SECONDS)
    assert session.mcp_names() == ("mcp__docs__search", "mcp__slow__wait")


def test_a_second_start_changes_nothing(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()

    session.manager.start()

    assert session.opened == ["docs"]


def test_without_enabled_servers_no_thread_starts(tmp_path: Path) -> None:
    session = _Session(tmp_path, {"off": _Server()}, [_entry("off", enabled=False)])
    before = set(threading.enumerate())

    session.manager.start()

    assert session.manager.wait_until_settled(0)
    started = [thread.name for thread in threading.enumerate() if thread not in before]
    assert not [name for name in started if name.startswith("mcp-")]
    assert session.published == []
    session.manager.close()


def test_a_failed_server_leaves_the_others_working(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    def refuse(message: Any) -> object:
        raise _RpcError(-32000, "boom")

    broken = _Server(handlers={"initialize": refuse})
    session = _Session(
        tmp_path, {"broken": broken, "docs": _Server(tools=[_tool("search")])}
    )
    sessions.append(session)

    session.start()

    assert session.mcp_names() == ("mcp__docs__search",)
    by_name = {status.entry.name: status for status in session.manager.servers()}
    assert by_name["broken"].connection is not None
    assert by_name["broken"].connection.state == "failed"
    assert "boom" in (by_name["broken"].connection.error or "")


# -- SES-02: registration -----------------------------------------------------


def test_tools_register_with_the_exposure_the_config_resolves(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # Pi: "exposes codemode-only MCP tools through codemode and hides them
    # from the model" and "applies per-tool exposure overrides".
    server = _Server(
        instructions="  Search before you fetch.  ",
        tools=[
            _tool("search", description="Search the docs."),
            _tool("fetch"),
            _tool("find_all"),
            _tool("secret"),
        ],
    )
    session = _Session(
        tmp_path,
        {"my-docs": server},
        [
            _entry(
                "my-docs",
                description=" The product docs ",
                toolExposure={
                    "search": "direct",
                    "find_*": "deferred",
                    "secret": "hidden",
                },
            )
        ],
    )
    sessions.append(session)

    session.start()

    exposures = {
        name: port.definition.exposure
        for name, port in session.tools.state.mcp_registry.items()
    }
    assert exposures == {
        "mcp__my_docs__search": "direct",
        "mcp__my_docs__fetch": "deferred",  # the default `codemode`, not listed
        "mcp__my_docs__find_all": "deferred",
        "mcp__my_docs__secret": "hidden",
    }
    assert session.declared() == ("read", "mcp__my_docs__search")
    assert session.callable_names() == {
        "read",
        "mcp__my_docs__search",
        "mcp__my_docs__fetch",
        "mcp__my_docs__find_all",
    }
    definition = session.port("mcp__my_docs__search").definition
    assert definition.description == "Search the docs."
    assert definition.namespace == ToolNamespace(
        name="mcp__my_docs",
        aliases=("my-docs",),
        description="The product docs",
        instructions="Search before you fetch.",
    )
    assert session.published == [tuple(exposures)]


def test_servers_register_in_configuration_order(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_list(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return {"tools": [_tool("a")]}

    first = _Server(handlers={"tools/list": slow_list})
    session = _Session(
        tmp_path, {"first": first, "second": _Server(tools=[_tool("b")])}
    )
    sessions.append(session)
    session.manager.start()
    _wait_until(lambda: session.mcp_names() == ("mcp__second__b",))

    release.set()

    assert session.manager.wait_until_settled(_WAIT_SECONDS)
    assert session.mcp_names() == ("mcp__first__a", "mcp__second__b")


def test_a_call_reaches_the_server_and_returns_its_result(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    calls: list[Any] = []

    def call(message: Any) -> object:
        calls.append(message["params"])
        return {"content": [{"type": "text", "text": "two hits"}]}

    server = _Server(tools=[_tool("search")], handlers={"tools/call": call})
    session = _Session(tmp_path, {"docs": server}, [_entry("docs", exposure="direct")])
    sessions.append(session)
    session.start()

    assert session.call("mcp__docs__search", {"query": "pipy"}) == "two hits"
    assert [(params["name"], params["arguments"]) for params in calls] == [
        ("search", {"query": "pipy"})
    ]


def test_a_tool_pipy_cannot_declare_is_left_out_and_reported(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    server = _Server(
        tools=[
            _tool("search"),
            {"name": "odd", "inputSchema": {"type": "string"}},
        ]
    )
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)

    session.start()

    assert session.mcp_names() == ("mcp__docs__search",)
    notices = session.notices()
    assert len(notices) == 1
    level, text = notices[0]
    assert level == "warning"
    assert text.startswith('MCP tool "docs/odd" is not available: ')
    assert session.notices() == []


def test_a_name_another_tool_has_is_left_out_and_reported_once(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    builtin = _Tool("mcp__docs__search")
    server = _Server(tools=[_tool("search"), _tool("fetch")])
    session = _Session(
        tmp_path, {"docs": server}, builtins={"mcp__docs__search": builtin}
    )
    sessions.append(session)
    session.start()

    assert session.tools.state.registry["mcp__docs__search"] is builtin
    assert session.tools.registered_names == ("mcp__docs__search", "mcp__docs__fetch")
    expected = (
        "warning",
        'MCP tool "mcp__docs__search" is not available: another tool has that name',
    )
    assert session.notices() == [expected]

    server.tools = [_tool("search"), _tool("fetch"), _tool("more")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: "mcp__docs__more" in session.mcp_names())

    assert session.notices() == []


# -- SES-03: list changes -----------------------------------------------------


def test_a_changed_list_adds_withdraws_and_restores_tools(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    server = _Server(tools=[_tool("search"), _tool("fetch")])
    session = _Session(
        tmp_path,
        {"docs": server},
        [_entry("docs", exposure="direct", toolExposure={"fetch": "codemode"})],
    )
    sessions.append(session)
    session.start()
    assert session.declared() == ("read", "mcp__docs__search")

    server.tools = [_tool("fetch"), _tool("list")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__fetch", "mcp__docs__list"))

    assert session.declared() == ("read", "mcp__docs__list")
    assert session.call("mcp__docs__search") == "unknown tool: mcp__docs__search"
    assert "mcp__docs__search" not in session.callable_names()

    server.tools = [_tool("search"), _tool("fetch")]
    server.notify("notifications/tools/list_changed")
    _wait_until(
        lambda: session.mcp_names() == ("mcp__docs__search", "mcp__docs__fetch")
    )

    # Back with its configured exposure.
    assert session.declared() == ("read", "mcp__docs__search")
    assert session.call("mcp__docs__search") == "ok"
    assert session.port("mcp__docs__fetch").definition.exposure == "deferred"


def test_a_colliding_tool_renames_both_and_its_removal_restores_the_name(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # TLS-03 through a session's registrations.
    server = _Server(tools=[_tool("a-b")])
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)
    session.start()
    assert session.mcp_names() == ("mcp__docs__a_b",)

    server.tools = [_tool("a-b"), _tool("a_b")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: len(session.mcp_names()) == 2)

    suffixed = session.mcp_names()
    assert all(name.startswith("mcp__docs__a_b_") for name in suffixed)
    assert len(set(suffixed)) == 2

    server.tools = [_tool("a-b")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__a_b",))


def test_a_state_change_without_new_tools_publishes_nothing(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    server = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)
    session.start()
    port = session.port("mcp__docs__search")

    server.end.close()  # the connection drops; its tools stay registered
    _wait_until(lambda: _state(session, "docs") == "disconnected")
    _drain(session)

    assert session.published == [("mcp__docs__search",)]
    assert session.port("mcp__docs__search") is port


def _state(session: _Session, name: str) -> str | None:
    for status in session.manager.servers():
        if status.entry.name == name and status.connection is not None:
            return status.connection.state
    return None


def _drain(session: _Session) -> None:
    """Wait until the manager has worked off what was announced so far."""

    worker = session.manager._worker
    assert worker is not None
    _wait_until(lambda: session.manager._events.empty())
    with session.manager._publication:
        pass


# -- TLS-19: checks when a call runs ------------------------------------------


def test_a_call_to_a_tool_the_server_withdrew_is_no_longer_available(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    server = _Server(tools=[_tool("search"), _tool("fetch")])
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)
    session.start()
    prepared = session.port("mcp__docs__search")  # as a running turn holds it

    server.tools = [_tool("fetch")]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__fetch",))

    assert _invoke(prepared, tmp_path) == (
        True,
        'MCP tool "docs/search" is no longer available.',
    )
    assert "tools/call" not in server.methods


def test_a_call_to_a_hidden_tool_is_no_longer_available(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    server = _Server(tools=[_tool("search")])
    session = _Session(
        tmp_path, {"docs": server}, [_entry("docs", toolExposure={"search": "hidden"})]
    )
    sessions.append(session)
    session.start()

    assert _invoke(session.port("mcp__docs__search"), tmp_path) == (
        True,
        'MCP tool "docs/search" is no longer available.',
    )
    assert "tools/call" not in server.methods


def test_a_call_after_the_session_closed_says_the_server_is_disabled(
    tmp_path: Path,
) -> None:
    server = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": server})
    session.start()
    prepared = session.port("mcp__docs__search")

    session.manager.close()

    assert _invoke(prepared, tmp_path) == (True, 'MCP server "docs" is disabled.')


def test_a_call_resolves_its_server_when_it_runs(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """The port holds names, not a connection. The states are set on the
    manager's record here; the S21 tests reach them by disabling and
    enabling the server."""

    server = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)
    session.start()
    prepared = session.port("mcp__docs__search")
    record = session.manager._servers[0]
    connection = record.connection

    with session.manager._lock:
        record.connection = None
    assert _invoke(prepared, tmp_path) == (
        True,
        'MCP server "docs" is still starting.',
    )

    with session.manager._lock:
        record.entry = _entry("docs", enabled=False)
    assert _invoke(prepared, tmp_path) == (True, 'MCP server "docs" is disabled.')

    with session.manager._lock:
        record.entry = _entry("docs")
        record.connection = connection
    assert _invoke(prepared, tmp_path) == (False, "ok")


def test_a_call_reconnects_a_server_that_dropped(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.start()
    prepared = session.port("mcp__docs__search")

    first.end.close()
    session.servers["docs"] = second

    assert _invoke(prepared, tmp_path) == (False, "ok")
    assert session.opened == ["docs", "docs"]
    assert "tools/call" in second.methods


# -- SES-17: close --------------------------------------------------------------


def test_close_closes_every_connection_and_publishes_nothing_afterwards(
    tmp_path: Path,
) -> None:
    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    docs = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": docs, "hanging": hanging})
    session.manager.start()
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__search",))
    _wait_until(lambda: "initialize" in hanging.methods)
    connections = [record.connection for record in session.manager._servers]
    published = list(session.published)

    session.manager.close()

    assert [connection.state for connection in connections if connection] == [
        "closed",
        "closed",
    ]
    assert session.manager.servers() == ()
    assert session.manager.wait_until_settled(0)
    assert session.manager._worker is not None
    assert not session.manager._worker.is_alive()
    # A list change that arrives now registers nothing.
    docs.tools = [_tool("other")]
    session.manager._events.put(None)
    assert session.published == published
    session.manager.close()  # a second close is harmless


def test_close_before_start_starts_nothing(tmp_path: Path) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})

    session.manager.close()
    session.manager.start()

    assert session.opened == []
    assert session.manager.servers() == ()


def test_close_while_a_server_process_is_starting_leaves_no_process(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "scenario.json"
    record = tmp_path / "record.jsonl"
    scenario.write_text(
        json.dumps(
            {
                "record": str(record),
                "methods": {"initialize": [{"record": "initialize"}, {"sleep": 60}]},
            }
        )
    )
    transports: list[StdioTransport] = []

    def create(
        entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        transport = create_default_transport(entry, cwd, auth)
        assert isinstance(transport, StdioTransport)
        transports.append(transport)
        return transport

    manager = McpManager(
        config=LoadedMcpConfig(
            servers=[
                McpServerEntry(
                    name="slow",
                    config={
                        "command": sys.executable,
                        "args": ["-I", str(_FAKE_SERVER), "--scenario", str(scenario)],
                    },
                    source="test",
                )
            ],
            errors=[],
        ),
        cwd=tmp_path,
        client_version="1.2.3",
        publish_tools=lambda registry: (),
        create_transport=create,
    )
    manager.start()
    _wait_until(lambda: record.exists() and '"initialize"' in record.read_text())
    pid = transports[0].pid
    assert pid is not None and _is_alive(pid)

    manager.close()

    assert not _is_alive(pid)


# -- 4.5: notices and locks ---------------------------------------------------


def test_background_changes_never_call_the_notice_sink(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """What the user should see waits for the session thread to fetch it."""

    server = _Server(tools=[{"name": "odd", "inputSchema": {"type": "string"}}])
    session = _Session(tmp_path, {"docs": server})
    sessions.append(session)
    threads: list[threading.Thread] = []

    def sink(level: str, message: str) -> None:
        threads.append(threading.current_thread())

    session.start()
    server.tools = [
        {"name": "odd", "inputSchema": {"type": "string"}},
        {"name": "odder", "inputSchema": {"type": "array"}},
    ]
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: len(session.manager._notices) == 2)
    assert threads == []

    session.manager.deliver_notices(sink)

    assert threads == [threading.current_thread()] * 2


class _LockOrder:
    """Records every lock taken out of order (plan 4.5)."""

    RANKS = {"publication": 0, "manager": 1, "connection": 2, "state": 3}

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.taken: set[str] = set()
        self._held = threading.local()

    def lock(self, kind: str, inner: Any) -> Any:
        return _RecordingLock(self, kind, inner)

    def acquired(self, kind: str) -> None:
        held: list[str] = self._held.__dict__.setdefault("kinds", [])
        for other in held:
            if self.RANKS[other] > self.RANKS[kind]:
                self.violations.append(f"{kind} taken while holding {other}")
            elif other == "manager" and kind != "manager":
                # Never held while calling a connection or publishing.
                self.violations.append(f"{kind} taken while holding manager")
        held.append(kind)
        self.taken.add(kind)

    def released(self, kind: str) -> None:
        self._held.kinds.remove(kind)


class _RecordingLock:
    def __init__(self, order: _LockOrder, kind: str, inner: Any) -> None:
        self._order = order
        self._kind = kind
        self._inner = inner

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        acquired: bool = self._inner.acquire(*args, **kwargs)
        if acquired:
            self._order.acquired(self._kind)
        return acquired

    def release(self) -> None:
        self._order.released(self._kind)
        self._inner.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def test_lock_order_recorder_reports_an_inversion() -> None:
    order = _LockOrder()
    state = order.lock("state", threading.RLock())
    manager = order.lock("manager", threading.Lock())
    connection = order.lock("connection", threading.Lock())

    with manager:
        pass
    with state, state:
        pass
    assert order.violations == []

    with state, manager:
        pass
    with manager, connection:
        pass

    assert order.violations == [
        "manager taken while holding state",
        "connection taken while holding manager",
    ]


def test_locks_are_taken_in_the_documented_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _LockOrder()

    def recorded_connection(**kwargs: Any) -> McpServerConnection:
        connection = McpServerConnection(**kwargs)
        connection._lock = order.lock("connection", connection._lock)
        return connection

    monkeypatch.setattr(manager_module, "McpServerConnection", recorded_connection)
    docs = _Server(tools=[_tool("search"), _tool("fetch")])
    odd = _Server(tools=[{"name": "odd", "inputSchema": {"type": "string"}}])
    replacement = _Server(tools=[_tool("search")])
    session = _Session(
        tmp_path,
        {"docs": docs, "odd": odd, "off": _Server()},
        [
            _entry("docs", exposure="direct"),
            _entry("odd"),
            _entry("off", enabled=False),
        ],
        state_lock=order.lock("state", threading.RLock()),
    )
    manager = session.manager
    manager._lock = order.lock("manager", manager._lock)  # type: ignore[assignment]
    manager._publication = order.lock("publication", manager._publication)  # type: ignore[assignment]

    session.start()
    prepared = session.port("mcp__docs__search")
    assert session.call("mcp__docs__search") == "ok"
    docs.tools = [_tool("fetch")]
    docs.notify("notifications/tools/list_changed")
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__fetch",))
    assert _invoke(prepared, tmp_path)[0] is True
    manager.servers()
    session.notices()
    docs.end.close()
    session.servers["docs"] = replacement
    assert session.call("mcp__docs__fetch") != ""
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__search",))
    manager.wait_until_settled(_WAIT_SECONDS)
    manager.close()

    assert order.taken == {"publication", "manager", "connection", "state"}
    assert order.violations == []


# -- SES-04: the startup report (S19) -----------------------------------------


def _refuse(message: Any) -> object:
    raise _RpcError(-32000, "boom\nsecond line")


def test_startup_problems_are_reported_once_in_one_notice(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {
            "docs": _Server(tools=[_tool("search")]),
            "broken": _Server(handlers={"initialize": _refuse}),
            "off": _Server(),
        },
        [_entry("docs"), _entry("broken"), _entry("off", enabled=False)],
        errors=["/cfg/mcp.json: autoEnableCodemode must be a boolean"],
    )
    sessions.append(session)

    session.start()

    by_name = {status.entry.name: status for status in session.manager.servers()}
    error = by_name["broken"].connection.error  # type: ignore[union-attr]
    assert error is not None and "\n" in error
    # The report is there when the wait for the servers returns.
    assert session.notices() == [
        (
            "warning",
            "MCP servers need attention:\n"
            "  config: /cfg/mcp.json: autoEnableCodemode must be a boolean\n"
            f"  broken: failed: {error.split(chr(10))[0]}\n"
            "Run /mcp to fix.",
        )
    ]
    assert session.notices() == []


def test_nothing_is_reported_when_nothing_needs_attention(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)

    session.start()

    assert session.notices() == []


def test_config_errors_are_reported_without_an_enabled_server(tmp_path: Path) -> None:
    session = _Session(
        tmp_path,
        {"off": _Server()},
        [_entry("off", enabled=False)],
        errors=["a.json: bad", "b.json: worse"],
    )
    before = set(threading.enumerate())

    session.manager.start()

    assert session.notices() == [
        (
            "warning",
            "MCP servers need attention:\n  config: a.json: bad\n"
            "  config: b.json: worse\nRun /mcp to fix.",
        )
    ]
    assert not [
        thread.name
        for thread in threading.enumerate()
        if thread not in before and thread.name.startswith("mcp-")
    ]
    session.manager.close()


def test_the_report_waits_for_every_server(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return slow._default(message)

    slow = _Server(handlers={"initialize": slow_initialize})
    session = _Session(
        tmp_path, {"broken": _Server(handlers={"initialize": _refuse}), "slow": slow}
    )
    sessions.append(session)
    session.manager.start()
    broken = lambda entry: entry.name == "broken"  # noqa: E731
    assert session.manager.wait_for_servers(broken, timeout=_WAIT_SECONDS)

    assert session.notices() == []

    release.set()
    assert session.manager.wait_until_settled(_WAIT_SECONDS)
    (notice,) = session.notices()
    assert notice[1].startswith("MCP servers need attention:\n  broken: failed: ")


def test_a_closed_session_reports_nothing(tmp_path: Path) -> None:
    release = threading.Event()

    def slow_refusal(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        raise _RpcError(-32000, "boom")

    session = _Session(
        tmp_path, {"broken": _Server(handlers={"initialize": slow_refusal})}
    )
    session.manager.start()

    session.manager.close()
    release.set()

    assert session.notices() == []


def _status(state: str, **fields: Any) -> McpServerStatus:
    view = ConnectionView(
        name="docs",
        state=state,  # type: ignore[arg-type]
        error=fields.get("error"),
        tools=tuple(fields.get("tools", ())),
        instructions=None,
    )
    return McpServerStatus(_entry("docs"), True, view)


def test_describe_state() -> None:
    # Pi `describeState`.
    assert describe_state(McpServerStatus(_entry("docs"), False, None)) == "disabled"
    assert describe_state(McpServerStatus(_entry("docs"), True, None)) == "starting"
    assert describe_state(_status("needs-auth")) == "needs sign-in"
    assert describe_state(_status("failed", error="a\nb")) == "failed: a"
    assert describe_state(_status("failed")) == "failed: unknown error"
    assert describe_state(_status("failed", error="a"), with_error=False) == "failed"
    assert describe_state(_status("connected")) == "connected · 0 tools"
    assert describe_state(_status("connected", tools=[_tool("a")])) == (
        "connected · 1 tool"
    )
    assert describe_state(_status("connecting")) == "connecting…"
    assert describe_state(_status("disconnected")) == "disconnected"


# -- SES-11: what the configuration says about exposures ----------------------


def test_configured_exposures_come_from_the_enabled_servers(tmp_path: Path) -> None:
    session = _Session(
        tmp_path,
        {},
        [
            _entry("plain"),
            _entry("mixed", exposure="direct", toolExposure={"s*": "deferred"}),
            _entry("off", enabled=False, exposure="hidden"),
        ],
    )

    assert session.manager.configured_exposures() == {"codemode", "direct", "deferred"}
    assert session.manager.auto_enable_codemode
    session.manager.close()
    assert session.manager.configured_exposures() == frozenset()


# -- S21: changes in a session ------------------------------------------------


class _SlowClose:
    """A transport whose ``close`` waits until the test lets it go on."""

    def __init__(self, inner: McpTransport) -> None:
        self._inner = inner
        self.closing = threading.Event()
        self.release = threading.Event()

    def close(self) -> None:
        self.closing.set()
        assert self.release.wait(_WAIT_SECONDS)
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _serve(session: _Session, *transports: Any) -> None:
    """Give the session's next connections these transports, in order."""

    def create(
        entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        session.opened.append(entry.name)
        transport: McpTransport = transports[len(session.opened) - 1]
        return transport

    session.manager._create_transport = create


def _in_thread(function: Any) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(function()), daemon=True)
    thread.start()
    return thread, results


def test_the_state_of_a_connected_server_counts_its_resources() -> None:
    # Pi `describeState`.
    def state(*resources: str) -> str:
        view = ConnectionView(
            name="docs",
            state="connected",
            error=None,
            tools=(_tool("search"),),
            instructions=None,
            has_resources=True,
            resources=tuple({"uri": uri, "name": uri} for uri in resources),
        )
        return describe_state(McpServerStatus(_entry("docs"), True, view))

    assert state() == "connected · 1 tool"
    assert state("docs://a") == "connected · 1 tool · 1 resource"
    assert state("docs://a", "docs://b") == "connected · 1 tool · 2 resources"


def _status_of(session: _Session, name: str) -> McpServerStatus:
    return next(
        status for status in session.manager.servers() if status.entry.name == name
    )


def test_disabling_closes_the_connection_and_takes_the_tools_away(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-14, SES-16.
    docs = _Server(tools=[_tool("search")])
    other = _Server(tools=[_tool("fetch")])
    session = _Session(tmp_path, {"docs": docs, "other": other})
    sessions.append(session)
    session.start()
    prepared = session.port("mcp__docs__search")
    connection = session.manager._servers[0].connection
    assert connection is not None

    assert session.manager.set_enabled("docs", False) is None

    assert connection.state == "closed"
    assert session.mcp_names() == ("mcp__other__fetch",)
    assert _invoke(prepared, tmp_path) == (True, 'MCP server "docs" is disabled.')
    status = _status_of(session, "docs")
    assert (status.enabled, status.connection) == (False, None)
    assert describe_state(status) == "disabled"
    assert [(entry.name, patch) for entry, patch in session.saves] == [
        ("docs", {"enabled": False})
    ]
    assert session.manager.configured_exposures() == frozenset({"codemode"})
    assert session.manager.wait_until_settled(0)
    # Disabling again has nothing to close.
    assert session.manager.set_enabled("docs", False) is None
    assert session.mcp_names() == ("mcp__other__fetch",)


def test_disabling_closes_a_server_that_is_still_connecting(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-14. The startup report still follows the attempt that was cut off.
    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    session = _Session(tmp_path, {"slow": hanging}, errors=["bad file"])
    sessions.append(session)
    session.manager.start()
    _wait_until(lambda: "initialize" in hanging.methods)
    connection = session.manager._servers[0].connection
    assert connection is not None

    assert session.manager.set_enabled("slow", False) is None

    assert connection.state == "closed"
    assert session.manager.wait_for_startup(timeout=_WAIT_SECONDS)
    assert session.notices() == [
        ("warning", "MCP servers need attention:\n  config: bad file\nRun /mcp to fix.")
    ]


def test_enabling_connects_with_a_new_connection(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-14, TLS-19: a call prepared before uses the new connection.
    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("search"), _tool("fetch")])
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.start()
    prepared = session.port("mcp__docs__search")
    session.manager.set_enabled("docs", False)
    session.servers["docs"] = second

    assert session.manager.set_enabled("docs", True) is None

    assert session.opened == ["docs", "docs"]
    assert session.mcp_names() == ("mcp__docs__search", "mcp__docs__fetch")
    assert _invoke(prepared, tmp_path) == (False, "ok")
    assert "tools/call" in second.methods and "tools/call" not in first.methods
    assert describe_state(_status_of(session, "docs")) == "connected · 2 tools"
    assert [patch for _, patch in session.saves] == [
        {"enabled": False},
        {"enabled": True},
    ]
    # Enabling an enabled server saves the change and opens nothing.
    assert session.manager.set_enabled("docs", True) is None
    assert session.opened == ["docs", "docs"]


def test_enabling_a_server_that_was_disabled_at_startup_connects_it(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # No server was enabled at startup, so nothing ran in the background yet.
    session = _Session(
        tmp_path,
        {"off": _Server(tools=[_tool("search")])},
        [_entry("off", enabled=False)],
    )
    sessions.append(session)
    session.start()
    assert session.manager._worker is None

    assert session.manager.set_enabled("off", True) is None

    assert session.mcp_names() == ("mcp__off__search",)
    # A connection made later is not part of the startup report.
    assert session.notices() == []


def test_enabling_a_server_whose_connect_fails_shows_in_its_state(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {"off": _Server(handlers={"initialize": _refuse})},
        [_entry("off", enabled=False)],
    )
    sessions.append(session)
    session.start()

    assert session.manager.set_enabled("off", True) is None

    assert describe_state(_status_of(session, "off")) == "failed: boom"
    assert session.notices() == []


def test_enabling_waits_until_the_previous_connection_has_closed(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-14, SES-10: a wait covers the attempt that is queued meanwhile.
    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    slow = _SlowClose(first.client_end)
    _serve(session, slow, second.client_end)
    session.start()
    prepared = session.port("mcp__docs__search")

    disabling, _ = _in_thread(lambda: session.manager.set_enabled("docs", False))
    assert slow.closing.wait(_WAIT_SECONDS)
    enabling, enabled = _in_thread(lambda: session.manager.set_enabled("docs", True))
    _wait_until(lambda: _status_of(session, "docs").enabled)

    # Enabled, and nothing opened while the old transport is still closing.
    assert session.opened == ["docs"]
    status = _status_of(session, "docs")
    assert status.connection is None and describe_state(status) == "starting"
    assert _invoke(prepared, tmp_path) == (
        True,
        'MCP server "docs" is still starting.',
    )
    assert not session.manager.wait_for_servers(timeout=0.05)
    assert session.manager.reconnect("docs") == 'MCP server "docs" is disabled.'

    slow.release.set()
    disabling.join(_WAIT_SECONDS)
    enabling.join(_WAIT_SECONDS)

    assert enabled == [None]
    assert session.opened == ["docs", "docs"]
    assert session.manager.wait_for_servers(timeout=0)
    assert _invoke(prepared, tmp_path) == (False, "ok")


def test_an_enable_that_was_overtaken_by_a_disable_opens_nothing(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """Attempt currency: the enable finds the server disabled again once the
    old connection has closed."""

    first = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    slow = _SlowClose(first.client_end)
    _serve(session, slow)
    session.start()

    disabling, _ = _in_thread(lambda: session.manager.set_enabled("docs", False))
    assert slow.closing.wait(_WAIT_SECONDS)
    enabling, _ = _in_thread(lambda: session.manager.set_enabled("docs", True))
    _wait_until(lambda: _status_of(session, "docs").enabled)
    again, _ = _in_thread(lambda: session.manager.set_enabled("docs", False))
    _wait_until(lambda: not _status_of(session, "docs").enabled)
    slow.release.set()
    for thread in (disabling, enabling, again):
        thread.join(_WAIT_SECONDS)
        assert not thread.is_alive()

    assert session.opened == ["docs"]
    assert _status_of(session, "docs").connection is None
    assert session.manager.wait_until_settled(0)
    assert session.mcp_names() == ()


def test_close_ends_an_enable_that_waits_for_a_closing_connection(
    tmp_path: Path,
) -> None:
    first = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": first})
    slow = _SlowClose(first.client_end)
    _serve(session, slow)
    session.start()
    disabling, _ = _in_thread(lambda: session.manager.set_enabled("docs", False))
    assert slow.closing.wait(_WAIT_SECONDS)
    enabling, enabled = _in_thread(lambda: session.manager.set_enabled("docs", True))
    _wait_until(lambda: _status_of(session, "docs").enabled)

    closing, _ = _in_thread(session.manager.close)
    enabling.join(_WAIT_SECONDS)

    assert enabled == [None]
    assert session.opened == ["docs"]
    # ``close`` itself still waits for the transport that is closing.
    assert closing.is_alive()
    slow.release.set()
    closing.join(_WAIT_SECONDS)
    disabling.join(_WAIT_SECONDS)
    assert not closing.is_alive() and not disabling.is_alive()


def test_a_failed_save_changes_nothing(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-16.
    session = _Session(
        tmp_path,
        {"docs": _Server(tools=[_tool("search")])},
        [
            McpServerEntry(
                name="docs",
                config={"command": "unused"},
                source="/home/mcp.json",
                scope="global",
            )
        ],
    )
    sessions.append(session)
    session.start()
    published = list(session.published)
    session.save_error = OSError("read-only file system")

    assert session.manager.set_enabled("docs", False) == (
        "Could not update /home/mcp.json: read-only file system"
    )
    assert session.manager.set_exposure("docs", "direct") == (
        "Could not update /home/mcp.json: read-only file system"
    )

    status = _status_of(session, "docs")
    assert status.enabled and status.entry.config == {"command": "unused"}
    assert status.connection is not None and status.connection.state == "connected"
    assert session.published == published
    assert session.declared() == ("read",)


def test_a_change_is_saved_to_the_override_file_when_there_is_one(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-16.
    entries = [
        McpServerEntry(
            name="plain",
            config={"command": "unused", "enabled": False},
            source="/home/mcp.json",
            scope="global",
        ),
        McpServerEntry(
            name="overridden",
            config={"command": "unused", "enabled": False},
            source="/home/mcp.json",
            scope="global",
            override="/work/.pipy/mcp.json",
        ),
    ]
    session = _Session(
        tmp_path,
        {"plain": _Server(), "overridden": _Server()},
        entries,
        project_config="/work/.pipy/mcp.json",
    )
    sessions.append(session)
    assert session.manager.project_config == "/work/.pipy/mcp.json"

    session.manager.set_exposure("plain", "deferred")
    session.manager.set_exposure("overridden", "direct")
    session.save_error = OSError("denied")
    failed = session.manager.set_exposure("overridden", "hidden")
    session.save_error = None
    session.manager.set_enabled("plain", True, in_project=True)

    assert [(entry.name, entry.override, patch) for entry, patch in session.saves] == [
        ("plain", None, {"exposure": "deferred"}),
        ("overridden", "/work/.pipy/mcp.json", {"exposure": "direct"}),
        ("plain", "/work/.pipy/mcp.json", {"enabled": True}),
    ]
    assert failed == "Could not update /work/.pipy/mcp.json: denied"
    plain = _status_of(session, "plain").entry
    assert plain.override == "/work/.pipy/mcp.json"
    assert plain.config == {
        "command": "unused",
        "enabled": True,
        "exposure": "deferred",
    }
    assert _status_of(session, "overridden").entry.config["exposure"] == "direct"
    # The override the first project change made is where the next one goes.
    session.manager.set_enabled("plain", False)
    assert session.saves[-1][0].override == "/work/.pipy/mcp.json"


def test_a_server_an_extension_registered_changes_for_the_session_only(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-16.
    session = _Session(
        tmp_path,
        {"ext": _Server(tools=[_tool("search")])},
        [
            McpServerEntry(
                name="ext",
                config={"command": "unused"},
                source="/ext/plugin.py",
                scope="extension",
            )
        ],
    )
    sessions.append(session)
    session.start()
    session.save_error = OSError("must not be called")

    assert session.manager.set_exposure("ext", "direct") is None
    assert session.manager.set_enabled("ext", False) is None

    assert session.saves == []
    entry = _status_of(session, "ext").entry
    assert entry.config == {"command": "unused", "exposure": "direct", "enabled": False}


def test_session_changes_are_written_to_the_mcp_json_files(tmp_path: Path) -> None:
    # SES-16 with the real files: the default ``update_config``.
    home = tmp_path / "home"
    work = tmp_path / "work"
    (work / ".pipy").mkdir(parents=True)
    home.mkdir()
    user_file = home / "mcp.json"
    project_file = work / ".pipy" / "mcp.json"
    user_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "a": {"command": "unused", "enabled": False},
                    "b": {"command": "unused", "enabled": False},
                    "c": {"command": "unused", "enabled": False},
                }
            }
        )
    )
    project_file.write_text(json.dumps({"mcpServers": {"b": {"enabled": False}}}))
    manager = McpManager(
        config=load_mcp_config(config_home=home, cwd=work, project_trusted=True),
        cwd=work,
        client_version="1.2.3",
        publish_tools=lambda registry: (),
    )

    assert manager.set_exposure("a", "direct") is None
    assert manager.set_exposure("b", "deferred") is None
    assert manager.set_enabled("c", False, in_project=True) is None

    assert json.loads(user_file.read_text())["mcpServers"] == {
        "a": {"command": "unused", "enabled": False, "exposure": "direct"},
        "b": {"command": "unused", "enabled": False},
        "c": {"command": "unused", "enabled": False},
    }
    assert json.loads(project_file.read_text())["mcpServers"] == {
        "b": {"enabled": False, "exposure": "deferred"},
        "c": {"enabled": False},
    }
    # A file that cannot be edited: the change is refused and names the file.
    user_file.write_text("{ not json")
    failed = manager.set_exposure("a", "hidden")
    assert failed is not None
    assert failed.startswith(f"Could not update {user_file}: ")
    entry = next(s.entry for s in manager.servers() if s.entry.name == "a")
    assert entry.config["exposure"] == "direct"
    manager.close()


def test_an_unknown_server_cannot_be_changed(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server()})
    sessions.append(session)
    session.start()

    for change in (
        lambda: session.manager.set_enabled("nope", False),
        lambda: session.manager.set_exposure("nope", "direct"),
        lambda: session.manager.reconnect("nope"),
    ):
        assert change() == 'No MCP server named "nope".'
    assert session.saves == []


def test_an_exposure_change_registers_the_tools_again(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-15: a tool moves between the declared and the callable set.
    session = _Session(
        tmp_path,
        {"docs": _Server(tools=[_tool("search"), _tool("fetch")])},
        [_entry("docs", toolExposure={"fetch": "hidden"})],
    )
    sessions.append(session)
    session.start()
    assert session.declared() == ("read",)
    assert session.callable_names() >= {"mcp__docs__search"}

    assert session.manager.set_exposure("docs", "direct") is None

    assert session.declared() == ("read", "mcp__docs__search")
    assert session.port("mcp__docs__search").definition.exposure == "direct"
    # ``toolExposure`` still decides for its tool.
    assert session.port("mcp__docs__fetch").definition.exposure == "hidden"
    assert session.manager.configured_exposures() == frozenset({"direct", "hidden"})

    assert session.manager.set_exposure("docs", "codemode") is None

    assert session.declared() == ("read",)
    assert "mcp__docs__search" in session.callable_names()
    assert session.call("mcp__docs__search") == "ok"

    assert session.manager.set_exposure("docs", "hidden") is None

    assert "mcp__docs__search" not in session.callable_names()
    assert [patch for _, patch in session.saves] == [
        {"exposure": "direct"},
        {"exposure": "codemode"},
        {"exposure": "hidden"},
    ]


def test_tools_that_are_no_longer_direct_leave_a_selection(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-15 with a selection, where registration alone would keep the names.
    session = _Session(
        tmp_path,
        {
            "docs": _Server(tools=[_tool("search")]),
            "other": _Server(tools=[_tool("fetch")]),
        },
        [_entry("docs", exposure="direct"), _entry("other", exposure="deferred")],
    )
    sessions.append(session)
    session.start()
    # As `tool_search` does: the deferred tool is loaded by name.
    assert session.tools.load_tools(["mcp__other__fetch"]) == ("mcp__other__fetch",)
    assert session.declared() == ("read", "mcp__docs__search", "mcp__other__fetch")

    assert session.manager.set_exposure("docs", "deferred") is None

    # The other server's loaded tool stays; this server's leaves.
    assert session.declared() == ("read", "mcp__other__fetch")
    assert session.tools.state.active_tool_names == frozenset(
        {"read", "mcp__other__fetch"}
    )

    assert session.manager.set_exposure("docs", "direct") is None

    assert session.declared() == ("read", "mcp__docs__search", "mcp__other__fetch")


def test_an_exposure_change_leaves_another_tool_that_has_an_mcp_name(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """A tool that has the name an MCP tool would get is left out of the MCP
    tools (S17), so the server's exposure does not decide whether it is
    active."""

    other = _Tool("mcp__docs__search", "deferred")
    session = _Session(
        tmp_path,
        {"docs": _Server(tools=[_tool("search"), _tool("fetch")])},
        builtins={"read": _Tool("read"), "mcp__docs__search": other},
    )
    sessions.append(session)
    session.start()
    session.tools.activate_tools(["mcp__docs__search", "mcp__docs__fetch"])
    assert session.declared() == ("read", "mcp__docs__search", "mcp__docs__fetch")

    assert session.manager.set_exposure("docs", "deferred") is None

    assert session.declared() == ("read", "mcp__docs__search")
    assert session.tools.state.registry["mcp__docs__search"] is other


def test_an_exposure_change_of_a_disabled_server_only_changes_the_entry(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {"off": _Server(tools=[_tool("search")])},
        [_entry("off", enabled=False)],
    )
    sessions.append(session)
    session.start()

    assert session.manager.set_exposure("off", "direct") is None
    assert session.published == []
    session.manager.set_enabled("off", True)

    assert session.declared() == ("read", "mcp__off__search")


def test_reconnect_replaces_the_connection_of_a_server(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SLH-03 at the manager.
    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("fetch")], instructions="Fetch first.")
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.start()
    closed = threading.Event()
    first.end.on_close(closed.set)
    session.servers["docs"] = second

    assert session.manager.reconnect("docs") is None

    assert closed.is_set()
    assert session.opened == ["docs", "docs"]
    # Registered when the call returns.
    assert session.mcp_names() == ("mcp__docs__fetch",)
    status = _status_of(session, "docs")
    assert describe_state(status) == "connected · 1 tool"
    assert status.connection is not None
    assert status.connection.instructions == "Fetch first."
    assert session.manager.wait_until_settled(0)
    assert session.notices() == []


def test_reconnect_returns_the_error_of_a_failed_connect(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    session.servers["docs"] = _Server(handlers={"initialize": _refuse})

    failure = session.manager.reconnect("docs")

    assert failure == 'MCP server "docs" failed to connect: boom\nsecond line'
    assert describe_state(_status_of(session, "docs")) == "failed: boom"
    # The tools stay registered while the server is not connected, as before.
    assert session.mcp_names() == ("mcp__docs__search",)


def test_reconnect_of_a_disabled_server_says_so(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"off": _Server()}, [_entry("off", enabled=False)])
    sessions.append(session)
    session.start()

    assert session.manager.reconnect("off") == 'MCP server "off" is disabled.'
    assert session.opened == []


def test_a_wait_covers_a_reconnect_in_progress(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-09, SES-10 with a real second attempt.
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return second._default(message)

    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("search")], handlers={"initialize": slow_initialize})
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.start()
    session.servers["docs"] = second

    reconnecting, results = _in_thread(lambda: session.manager.reconnect("docs"))
    _wait_until(lambda: "initialize" in second.methods)
    assert not session.manager.wait_for_servers(timeout=0.05)
    assert not session.manager.wait_for_servers(
        lambda entry: entry.name == "docs", timeout=0.05
    )
    waiting, waited = _in_thread(lambda: session.manager.wait_for_servers())
    release.set()
    reconnecting.join(_WAIT_SECONDS)
    waiting.join(_WAIT_SECONDS)

    assert results == [None] and waited == [True]


def test_a_reconnect_waits_for_the_attempt_that_is_running(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return first._default(message)

    first = _Server(tools=[_tool("search")], handlers={"initialize": slow_initialize})
    second = _Server(tools=[_tool("fetch")])
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.manager.start()
    _wait_until(lambda: "initialize" in first.methods)
    session.servers["docs"] = second

    reconnecting, results = _in_thread(lambda: session.manager.reconnect("docs"))
    reconnecting.join(0.1)
    assert reconnecting.is_alive() and session.opened == ["docs"]
    release.set()
    reconnecting.join(_WAIT_SECONDS)

    assert results == [None]
    assert session.opened == ["docs", "docs"]
    assert session.mcp_names() == ("mcp__docs__fetch",)


def test_close_ends_a_reconnect_that_waits(tmp_path: Path) -> None:
    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    session = _Session(tmp_path, {"docs": hanging})
    session.manager.start()
    _wait_until(lambda: "initialize" in hanging.methods)

    # This wait holds the event of the attempt the reconnect replaces.
    waiting, waited = _in_thread(session.manager.wait_for_servers)
    reconnecting, results = _in_thread(lambda: session.manager.reconnect("docs"))
    reconnecting.join(0.1)
    assert reconnecting.is_alive() and waiting.is_alive()
    session.manager.close()
    reconnecting.join(_WAIT_SECONDS)
    waiting.join(_WAIT_SECONDS)

    assert results == ['MCP server "docs" is shut down']
    assert waited == [True]
    assert session.manager._unsettled == set()


def test_wait_for_startup_counts_only_the_attempts_of_start(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return slow._default(message)

    slow = _Server(handlers={"initialize": slow_initialize})
    session = _Session(tmp_path, {"docs": _Server(), "slow": slow})
    sessions.append(session)
    assert session.manager.wait_for_startup(
        timeout=0
    )  # not started: nothing to wait for

    session.manager.start()
    assert not session.manager.wait_for_startup(timeout=0.05)
    cancel = threading.Event()
    cancel.set()
    assert not session.manager.wait_for_startup(cancel_event=cancel)
    release.set()
    assert session.manager.wait_for_startup(timeout=_WAIT_SECONDS)

    # A later attempt is not startup.
    blocked = threading.Event()
    session.servers["docs"] = _Server(
        handlers={
            "initialize": lambda message: (blocked.wait(_WAIT_SECONDS), _NO_ANSWER)[1]
        }
    )
    reconnecting, _ = _in_thread(lambda: session.manager.reconnect("docs"))
    _wait_until(lambda: len(session.opened) == 3)
    assert session.manager.wait_for_startup(timeout=0)
    session.manager.close()
    blocked.set()
    reconnecting.join(_WAIT_SECONDS)
    assert session.manager.wait_for_startup(timeout=0)


def test_wait_for_startup_without_an_enabled_server(tmp_path: Path) -> None:
    session = _Session(tmp_path, {"off": _Server()}, [_entry("off", enabled=False)])
    session.manager.start()

    assert session.manager.wait_for_startup(timeout=0)
    session.manager.close()


def _stdio_manager(
    tmp_path: Path, scenario: dict[str, Any]
) -> tuple[McpManager, list[StdioTransport], Path]:
    record = tmp_path / "record.jsonl"
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"record": str(record), **scenario}))
    transports: list[StdioTransport] = []

    def create(
        entry: McpServerEntry, cwd: str, auth: McpAuthProvider | None
    ) -> McpTransport:
        transport = create_default_transport(entry, cwd, auth)
        assert isinstance(transport, StdioTransport)
        transports.append(transport)
        return transport

    manager = McpManager(
        config=LoadedMcpConfig(
            servers=[
                McpServerEntry(
                    name="docs",
                    config={
                        "command": sys.executable,
                        "args": ["-I", str(_FAKE_SERVER), "--scenario", str(path)],
                    },
                    source="test",
                    scope="extension",
                )
            ],
            errors=[],
        ),
        cwd=tmp_path,
        client_version="1.2.3",
        publish_tools=lambda registry: (),
        create_transport=create,
    )
    return manager, transports, record


def test_disabling_ends_the_server_process(tmp_path: Path) -> None:
    # SES-14 with a real process.
    manager, transports, _ = _stdio_manager(tmp_path, {"tools": [_tool("search")]})
    manager.start()
    assert manager.wait_until_settled(_WAIT_SECONDS)
    pid = transports[0].pid
    assert pid is not None and _is_alive(pid)

    assert manager.set_enabled("docs", False) is None

    assert not _is_alive(pid)
    assert manager.set_enabled("docs", True) is None
    second = transports[1].pid
    assert second is not None and second != pid and _is_alive(second)
    manager.close()
    assert not _is_alive(second)


def test_reconnect_after_the_server_process_was_killed(tmp_path: Path) -> None:
    # SLH-03 with a real process.
    manager, transports, _ = _stdio_manager(tmp_path, {"tools": [_tool("search")]})
    manager.start()
    assert manager.wait_until_settled(_WAIT_SECONDS)
    pid = transports[0].pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL)
    _wait_until(lambda: manager.servers()[0].connection.state == "disconnected")  # type: ignore[union-attr]
    status = manager.servers()[0]
    assert describe_state(status) == "disconnected"

    assert manager.reconnect("docs") is None

    status = manager.servers()[0]
    assert describe_state(status) == "connected · 1 tool"
    second = transports[1].pid
    assert second is not None and second != pid and _is_alive(second)
    manager.close()
    assert not _is_alive(second)


def test_session_changes_take_the_locks_in_the_documented_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _LockOrder()

    def recorded_connection(**kwargs: Any) -> McpServerConnection:
        connection = McpServerConnection(**kwargs)
        connection._lock = order.lock("connection", connection._lock)
        return connection

    monkeypatch.setattr(manager_module, "McpServerConnection", recorded_connection)
    session = _Session(
        tmp_path,
        {"docs": _Server(tools=[_tool("search")])},
        [_entry("docs", exposure="direct")],
        state_lock=order.lock("state", threading.RLock()),
    )
    manager = session.manager
    manager._lock = order.lock("manager", manager._lock)  # type: ignore[assignment]
    manager._publication = order.lock("publication", manager._publication)  # type: ignore[assignment]
    session.start()
    session.tools.activate_tools(["mcp__docs__search"])

    assert manager.set_exposure("docs", "codemode") is None
    session.servers["docs"] = _Server(tools=[_tool("search")])
    assert manager.reconnect("docs") is None
    assert manager.set_enabled("docs", False) is None
    session.servers["docs"] = _Server(tools=[_tool("search")])
    assert manager.set_enabled("docs", True) is None
    manager.wait_for_startup()
    manager.close()

    assert order.taken == {"publication", "manager", "connection", "state"}
    assert order.violations == []
