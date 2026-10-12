"""Tests for the session's MCP manager (MCP parity plan S17).

The manager is tested against in-memory servers and a real capability value;
what depends on a server process against the scripted fake stdio server. The
session level (a provider, hooks, codemode) is in
``test_native_mcp_session.py``.
"""

from __future__ import annotations

import json
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
from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.connection import (
    McpServerConnection,
    create_default_transport,
)
from pipy_harness.native.mcp.http import McpAuthProvider
from pipy_harness.native.mcp.manager import McpManager
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
    ) -> None:
        self.servers = servers
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
                errors=[],
            ),
            cwd=tmp_path,
            client_version="1.2.3",
            publish_tools=self._publish,
            create_transport=self._create,
        )

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
    """The port holds names, not a connection. The states a later slice
    reaches through `/mcp` (disabled, enabled again and not yet started) are
    set on the manager's record here."""

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
