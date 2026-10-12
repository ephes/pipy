"""Tests for the MCP readiness waits (MCP parity plan S18).

``McpManager.wait_for_servers``, the choice of servers a prompt or a tool
call waits for (``McpReadiness``) and the way the session runs such a wait
(``McpSessionWaits``), against in-memory servers. Rows SES-05 to SES-10 and
the use of EXT-05. The session level is in ``test_native_mcp_session.py``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_connection import _RpcError, _Server, _wait_until
from test_native_mcp_manager import _drain, _entry, _Session, _tool

from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.mcp import readiness as readiness_module
from pipy_harness.native.mcp.readiness import (
    STILL_CONNECTING_NOTICE,
    McpReadiness,
    has_direct_tools,
    reaches_server,
)
from pipy_harness.native.repl.mcp_waits import McpSessionWaits, run_readiness_wait
from pipy_harness.native.tool_search import tool_search_definition
from pipy_harness.native.tools.base import ToolInfo, tool_info
from pipy_harness.native.tools.codemode import CodemodeTool, script_reach

_WAIT_SECONDS = 10.0


class _Gate:
    """A server whose ``initialize`` answers only after ``open()``."""

    def __init__(self, *tools: str) -> None:
        self._release = threading.Event()
        self.server = _Server(
            tools=[_tool(name) for name in tools],
            handlers={"initialize": self._initialize},
        )

    def _initialize(self, message: Any) -> object:
        self._release.wait(_WAIT_SECONDS)
        return self.server._default(message)

    def open(self) -> None:
        self._release.set()


@pytest.fixture
def sessions() -> Iterator[list[_Session]]:
    opened: list[_Session] = []
    yield opened
    for session in opened:
        session.manager.close()


def _named(name: str) -> Callable[[Any], bool]:
    return lambda entry: entry.name == name


def _start(
    tmp_path: Path,
    sessions: list[_Session],
    servers: dict[str, _Server],
    **configs: dict[str, Any],
) -> _Session:
    """Start a manager and return while gated servers are still connecting."""

    session = _Session(
        tmp_path,
        servers,
        [_entry(name, **configs.get(name, {})) for name in servers],
    )
    sessions.append(session)
    session.manager.start()
    return session


def _restart_attempt(
    session: _Session, name: str
) -> tuple[threading.Event, threading.Event]:
    """Give a server a new connection attempt that has not settled.

    The test does what enabling or reconnecting a server does: it assigns a
    new ``ready`` event under the manager lock. Returns the event of the
    attempt that was replaced and the new one. (The real paths are tested in
    ``test_native_mcp_manager.py``.)
    """

    manager = session.manager
    with manager._lock:
        server = next(s for s in manager._servers if s.entry.name == name)
        replaced = server.ready
        assert replaced is not None
        ready = server.ready = threading.Event()
        return replaced, ready


def _in_thread(function: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(function()), daemon=True)
    thread.start()
    return thread, results


# -- McpManager.wait_for_servers ------------------------------------------------


def test_a_wait_covers_only_the_chosen_servers(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    slow = _Gate("wait")
    session = _start(
        tmp_path,
        sessions,
        {"docs": _Server(tools=[_tool("search")]), "slow": slow.server},
    )
    manager = session.manager

    assert manager.wait_for_servers(_named("docs"), timeout=_WAIT_SECONDS)
    # The tools were published before the wait returned.
    assert session.mcp_names() == ("mcp__docs__search",)
    assert not manager.wait_for_servers(_named("slow"), timeout=0)
    assert not manager.wait_for_servers(timeout=0)
    assert manager.wait_for_servers(_named("nobody"), timeout=0)

    slow.open()
    assert manager.wait_for_servers(timeout=_WAIT_SECONDS)
    assert session.mcp_names() == ("mcp__docs__search", "mcp__slow__wait")


def test_a_wait_gives_up_after_its_time_limit(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    started = time.monotonic()

    assert not session.manager.wait_for_servers(timeout=0.1)

    assert 0.1 <= time.monotonic() - started < 5


def test_a_cancel_event_ends_a_wait(tmp_path: Path, sessions: list[_Session]) -> None:
    # SES-10.
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    cancel = threading.Event()
    thread, results = _in_thread(
        lambda: session.manager.wait_for_servers(cancel_event=cancel)
    )
    thread.join(0.2)
    assert thread.is_alive()

    cancel.set()

    thread.join(_WAIT_SECONDS)
    assert results == [False]


def test_a_cancel_event_that_is_already_set_ends_the_wait_at_once(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    cancel = threading.Event()
    cancel.set()

    assert not session.manager.wait_for_servers(cancel_event=cancel)


def test_a_failed_server_ends_the_wait_like_a_connected_one(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    def refuse(message: Any) -> object:
        raise _RpcError(-32000, "boom")

    broken = _Server(handlers={"initialize": refuse})
    session = _start(tmp_path, sessions, {"broken": broken})

    assert session.manager.wait_for_servers(timeout=_WAIT_SECONDS)

    (status,) = session.manager.servers()
    assert status.connection is not None and status.connection.state == "failed"


def test_closing_the_manager_ends_a_wait(tmp_path: Path) -> None:
    session = _Session(tmp_path, {"slow": _Gate().server})
    session.manager.start()
    thread, results = _in_thread(session.manager.wait_for_servers)
    thread.join(0.2)
    assert thread.is_alive()

    session.manager.close()

    thread.join(_WAIT_SECONDS)
    assert results == [True]


def test_a_wait_covers_an_attempt_that_was_queued_while_waiting(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-10: Pi "waitForServers" loops until the attempts it waited for are
    # still the servers' latest ones.
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    manager = session.manager
    thread, results = _in_thread(
        lambda: manager.wait_for_servers(timeout=_WAIT_SECONDS)
    )
    thread.join(0.2)
    assert thread.is_alive()

    first, second = _restart_attempt(session, "slow")
    first.set()  # the attempt the wait began with is over
    thread.join(0.3)
    assert thread.is_alive()

    second.set()
    thread.join(_WAIT_SECONDS)
    assert results == [True]


def test_a_change_of_the_connection_does_not_settle_a_newer_attempt(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # An attempt's event is set for that attempt's outcome only. A tool list
    # change arrives while a reconnect is closing the old transport.
    docs = _Server(
        tools=[_tool("search")], capabilities={"tools": {"listChanged": True}}
    )
    session = _start(tmp_path, sessions, {"docs": docs})
    assert session.manager.wait_for_servers(timeout=_WAIT_SECONDS)
    _first, second = _restart_attempt(session, "docs")

    docs.tools = [_tool("search"), _tool("find")]
    docs.notify("notifications/tools/list_changed")

    _wait_until(lambda: len(session.mcp_names()) == 2)
    _drain(session)
    assert not second.is_set()
    assert not session.manager.wait_for_servers(timeout=0)


def test_a_disabled_server_is_not_waited_for(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"off": _Server()}, [_entry("off", enabled=False)])
    sessions.append(session)
    session.manager.start()

    assert session.manager.wait_for_servers(timeout=0)


def test_a_server_that_was_not_started_is_not_waited_for(tmp_path: Path) -> None:
    session = _Session(tmp_path, {"docs": _Server()})

    assert session.manager.wait_for_servers(timeout=0)


def test_tool_owner_names_the_server_of_a_registered_tool(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"my-docs": _Server(tools=[_tool("search")])})
    assert session.manager.wait_for_servers(timeout=_WAIT_SECONDS)

    assert session.manager.tool_owner("mcp__my_docs__search") == "my-docs"
    assert session.manager.tool_owner("read") is None


# -- which servers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, False),
        ({"exposure": "direct"}, True),
        ({"exposure": "deferred"}, False),
        ({"exposure": "codemode", "toolExposure": {"shot": "direct"}}, True),
        ({"exposure": "hidden", "toolExposure": {"s*": "direct"}}, True),
        ({"exposure": "direct", "toolExposure": {"shot": "codemode"}}, True),
        ({"toolExposure": {"shot": "deferred"}}, False),
    ],
)
def test_has_direct_tools_reads_the_configured_exposures(
    config: dict[str, Any], expected: bool
) -> None:
    # SES-05, SES-06; Pi `hasDirectTools`.
    assert has_direct_tools(_entry("docs", **config)) is expected


def test_reaches_server_matches_the_namespace_inside_a_word() -> None:
    # SES-07; Pi `reachesServer`.
    assert reaches_server("all", "docs")
    assert reaches_server(("tools", "mcp__docs__search"), "docs")
    assert reaches_server(("mcp__docs__",), "docs")
    assert reaches_server(("mcp__slow_docs__search",), "slow-docs")
    assert not reaches_server(("mcp__slow-docs__search",), "slow_docs")
    assert not reaches_server(("docs", "search"), "docs")
    assert not reaches_server((), "docs")
    # A word contains the namespace of a server whose name it starts with.
    assert reaches_server(("mcp__docs2__search",), "docs")


@pytest.mark.parametrize(
    ("code", "waits_for_docs", "waits_for_other"),
    [
        ("text(tools.mcp__docs__search(query='q'))", True, False),
        ("name = 'mcp__docs__' + kind\ntext(tools[name]())", True, False),
        ("text(1 + 1)", False, False),
        ("text(search_tools('pages'))", True, True),
        ("text(describe_namespace('docs'))", True, True),
        ("text(describe_tool('x'))", True, True),
        ("text(len(ALL_TOOLS))", True, True),
    ],
)
def test_a_script_reaches_the_servers_its_source_names(
    code: str, waits_for_docs: bool, waits_for_other: bool
) -> None:
    # SES-08; Pi `getScriptReach`.
    reach = script_reach({"code": code})

    assert reaches_server(reach, "docs") is waits_for_docs
    assert reaches_server(reach, "other") is waits_for_other


# -- McpReadiness -----------------------------------------------------------------


def _info(name: str, reaches_tools: Any = None) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="",
        input_schema={"type": "object"},
        exposure="direct",
        reaches_tools=reaches_tools,
    )


_CODEMODE = tool_info(CodemodeTool().definition)
_TOOL_SEARCH = tool_info(tool_search_definition())


def _readiness(
    session: _Session, *tools: ToolInfo, startup_wait_seconds: float | None = None
) -> McpReadiness:
    return McpReadiness(
        session.manager,
        lambda: (*session.tools.all_tools(), *tools),
        startup_wait_seconds=startup_wait_seconds,
    )


def _script(code: str) -> str:
    return json.dumps({"code": code})


def test_the_first_prompt_waits_for_servers_with_direct_tools(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-05; Pi "holds the first prompt for servers with direct tools".
    slow = _Gate("search")
    session = _start(
        tmp_path, sessions, {"slow": slow.server}, slow={"exposure": "direct"}
    )
    wait = _readiness(session).first_prompt_wait()
    assert wait is not None
    thread, results = _in_thread(lambda: wait(None))
    thread.join(0.2)
    assert thread.is_alive()

    slow.open()

    thread.join(_WAIT_SECONDS)
    assert results == [True]
    assert session.declared() == ("read", "mcp__slow__search")


def test_the_first_prompt_waits_for_a_single_direct_tool(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(
        tmp_path,
        sessions,
        {"slow": _Gate("search").server},
        slow={"toolExposure": {"search": "direct"}},
    )

    assert _readiness(session).first_prompt_wait() is not None


def test_the_first_prompt_does_not_wait_for_servers_without_direct_tools(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-06; Pi "does not hold the first prompt for codemode servers that
    # are still connecting".
    session = _start(
        tmp_path,
        sessions,
        {"code": _Gate().server, "late": _Gate().server},
        late={"exposure": "deferred"},
    )

    assert _readiness(session).first_prompt_wait() is None


def test_the_first_prompt_waits_only_up_to_the_startup_limit(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-05; Pi "holds the first prompt for servers with direct tools only
    # up to startupWaitMs".
    session = _start(
        tmp_path, sessions, {"slow": _Gate().server}, slow={"exposure": "direct"}
    )
    wait = _readiness(session, startup_wait_seconds=0.1).first_prompt_wait()
    assert wait is not None
    started = time.monotonic()

    assert wait(None) is False

    assert 0.1 <= time.monotonic() - started < 5


def test_only_the_first_prompt_waits(tmp_path: Path, sessions: list[_Session]) -> None:
    # SES-05: "Later prompts do not wait", also when the first had nothing
    # to wait for.
    slow = _Gate()
    session = _start(
        tmp_path, sessions, {"slow": slow.server}, slow={"exposure": "direct"}
    )
    readiness = _readiness(session, startup_wait_seconds=0.05)

    assert readiness.first_prompt_wait() is not None
    assert readiness.first_prompt_wait() is None


def test_the_default_startup_limit_is_ten_seconds(
    tmp_path: Path, sessions: list[_Session], monkeypatch
) -> None:
    # Pi `DEFAULT_STARTUP_WAIT_MS`.
    assert readiness_module.STARTUP_WAIT_SECONDS == 10.0
    session = _start(
        tmp_path, sessions, {"slow": _Gate().server}, slow={"exposure": "direct"}
    )
    limits: list[float | None] = []

    def record(
        select: Any = None, *, timeout: float | None = None, cancel_event: Any = None
    ) -> bool:
        limits.append(timeout)
        return False

    monkeypatch.setattr(session.manager, "wait_for_servers", record)

    wait = _readiness(session).first_prompt_wait()
    assert wait is not None
    wait(None)

    assert limits == [0, 10.0]


def test_a_call_waits_for_nothing_when_every_server_settled(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"docs": _Server(tools=[_tool("search")])})
    assert session.manager.wait_for_servers(timeout=_WAIT_SECONDS)
    listed: list[int] = []

    def all_tools() -> tuple[ToolInfo, ...]:
        listed.append(1)
        return ()

    readiness = McpReadiness(session.manager, all_tools)

    assert readiness.call_wait("codemode", _script("search_tools('x')")) is None
    # The usual case does not even list the tools.
    assert listed == []


def test_a_script_waits_for_the_servers_it_names(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-07; Pi "waits for the servers a codemode script names" and "waits
    # for servers whose script identifiers differ from their names".
    slow = _Gate("search")
    session = _start(
        tmp_path,
        sessions,
        {"slow-docs": slow.server, "other": _Gate().server},
    )
    readiness = _readiness(session, _CODEMODE)
    wait = readiness.call_wait(
        "codemode", _script("text(tools.mcp__slow_docs__search())")
    )
    assert wait is not None
    thread, results = _in_thread(lambda: wait(None))
    thread.join(0.2)
    assert thread.is_alive()

    slow.open()  # `other` stays connecting

    thread.join(_WAIT_SECONDS)
    assert results == [True]
    assert "mcp__slow_docs__search" in session.callable_names()


def test_a_script_that_names_no_server_waits_for_none(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-08; Pi "does not wait for servers a codemode script does not name".
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    readiness = _readiness(session, _CODEMODE)

    assert readiness.call_wait("codemode", _script("text(1 + 1)")) is None
    assert readiness.call_wait("codemode", "not json") is None
    assert readiness.call_wait("codemode", '{"code": 3}') is None
    assert readiness.call_wait("codemode", "[]") is None


def test_a_script_that_uses_discovery_waits_for_every_server(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-08.
    first, second = _Gate(), _Gate()
    session = _start(tmp_path, sessions, {"one": first.server, "two": second.server})
    wait = _readiness(session, _CODEMODE).call_wait(
        "codemode", _script("text(search_tools('pages'))")
    )
    assert wait is not None
    thread, results = _in_thread(lambda: wait(None))

    first.open()
    thread.join(0.3)
    assert thread.is_alive()
    second.open()

    thread.join(_WAIT_SECONDS)
    assert results == [True]


@pytest.mark.parametrize(
    "tool",
    [
        _TOOL_SEARCH,
        _info("list_mcp_resources"),
        _info("list_mcp_resource_templates"),
        _info("read_mcp_resource"),
    ],
    ids=lambda tool: tool.name,
)
def test_tool_search_and_the_resource_tools_wait_for_every_server(
    tmp_path: Path, sessions: list[_Session], tool: ToolInfo
) -> None:
    # SES-09; Pi "waits for servers before tool_search searches".
    slow = _Gate("search")
    session = _start(
        tmp_path,
        sessions,
        {"docs": _Server(tools=[_tool("find")]), "slow": slow.server},
    )
    wait = _readiness(session, tool).call_wait(tool.name, '{"query": "docs"}')
    assert wait is not None

    slow.open()

    assert wait(None) is True
    assert session.mcp_names() == ("mcp__docs__find", "mcp__slow__search")


def test_an_mcp_tool_waits_for_its_own_server_only(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-09: "A direct call to an MCP tool waits for its own server only,
    # including a reconnect in progress."
    session = _start(
        tmp_path,
        sessions,
        {"docs": _Server(tools=[_tool("search")]), "slow": _Gate().server},
        docs={"exposure": "direct"},
    )
    manager = session.manager
    assert manager.wait_for_servers(_named("docs"), timeout=_WAIT_SECONDS)
    readiness = _readiness(session)

    # `slow` is still connecting; `docs` is not.
    assert readiness.call_wait("mcp__docs__search", "{}") is None

    _replaced, reconnect = _restart_attempt(session, "docs")
    wait = readiness.call_wait("mcp__docs__search", "{}")
    assert wait is not None
    thread, results = _in_thread(lambda: wait(None))
    thread.join(0.2)
    assert thread.is_alive()
    reconnect.set()
    thread.join(_WAIT_SECONDS)
    assert results == [True]


def test_other_tools_wait_for_no_server(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    readiness = _readiness(session, _info("probe"))

    # A built-in tool, an extension tool without a reach, an unknown tool.
    assert readiness.call_wait("read", '{"path": "mcp__slow__x"}') is None
    assert readiness.call_wait("probe", "{}") is None
    assert readiness.call_wait("missing", "{}") is None


def test_an_extension_tool_waits_for_the_reach_it_declares(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # EXT-05 (use).
    session = _start(
        tmp_path, sessions, {"slow": _Gate().server, "other": _Gate().server}
    )
    seen: list[Any] = []

    def reach(arguments: Any) -> Any:
        seen.append(arguments)
        return arguments.get("reach")

    readiness = _readiness(session, _info("runner", reach))

    def waits(arguments: Any) -> bool:
        return readiness.call_wait("runner", json.dumps(arguments)) is not None

    assert waits({"reach": "all"})
    assert waits({"reach": ["x", "mcp__slow__search"]})
    assert not waits({"reach": ["mcp__gone__search"]})
    assert not waits({"reach": []})
    # No reach for this call: the tool is treated like any other tool.
    assert not waits({"reach": None})
    # Not a reach: every server, the over-match the contract allows.
    assert waits({"reach": "mcp__gone"})
    assert waits({"reach": [1]})
    assert seen[0] == {"reach": "all"}


def test_a_reach_function_that_fails_waits_for_every_server(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})

    def broken(arguments: Any) -> Any:
        raise RuntimeError("boom")

    readiness = _readiness(session, _info("runner", broken))

    assert readiness.call_wait("runner", "{}") is not None


def test_the_built_in_reaching_tools_declare_their_reach() -> None:
    # EXT-05: "`codemode` and `tool_search` declare it."
    assert _CODEMODE.reaches_tools is not None
    assert _CODEMODE.reaches_tools({"code": "ALL_TOOLS"}) == "all"
    assert _TOOL_SEARCH.reaches_tools is not None
    assert _TOOL_SEARCH.reaches_tools({"query": "x"}) == "all"


# -- McpSessionWaits --------------------------------------------------------------


class _Waiter:
    """A tool interrupt waiter: interrupts after ``after`` seconds, if given."""

    def __init__(
        self,
        interruption: ToolExecutionInterruption = ToolExecutionInterruption.SETTLED,
        after: float | None = None,
    ) -> None:
        self._interruption = interruption
        self._after = after
        self.calls = 0

    def __call__(
        self, done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        self.calls += 1
        if done_event.wait(_WAIT_SECONDS if self._after is None else self._after):
            return ToolExecutionInterruption.SETTLED
        assert self._after is not None, "the wait did not end"
        return self._interruption


def _waits(
    session: _Session,
    *tools: ToolInfo,
    prompt_waiter: Any = None,
    startup_wait_seconds: float | None = None,
) -> tuple[McpSessionWaits, list[tuple[str, str]]]:
    notices: list[tuple[str, str]] = []
    waits = McpSessionWaits(
        _readiness(session, *tools, startup_wait_seconds=startup_wait_seconds),
        notify=lambda level, message: notices.append((level, message)),
        prompt_waiter=prompt_waiter,
    )
    return waits, notices


def _call(name: str, arguments: Any) -> AgentToolCall:
    return AgentToolCall("call-1", name, ProductContent(json.dumps(arguments)))


def _wait_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "mcp-wait"]


def test_the_first_prompt_proceeds_once_the_direct_server_connected(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    slow = _Gate("search")
    session = _start(
        tmp_path, sessions, {"slow": slow.server}, slow={"exposure": "direct"}
    )
    waiter = _Waiter()
    waits, notices = _waits(session, prompt_waiter=waiter)
    threading.Timer(0.1, slow.open).start()

    waits.before_first_prompt()

    assert session.declared() == ("read", "mcp__slow__search")
    assert notices == []
    assert waiter.calls == 1
    waits.before_first_prompt()
    assert waiter.calls == 1


@pytest.mark.parametrize("with_waiter", [True, False])
def test_the_first_prompt_reports_servers_that_are_still_connecting(
    tmp_path: Path, sessions: list[_Session], with_waiter: bool
) -> None:
    # SES-05: the notice after the limit, once.
    session = _start(
        tmp_path, sessions, {"slow": _Gate().server}, slow={"exposure": "direct"}
    )
    waits, notices = _waits(
        session,
        prompt_waiter=_Waiter() if with_waiter else None,
        startup_wait_seconds=0.1,
    )

    waits.before_first_prompt()
    waits.before_first_prompt()

    assert notices == [("info", STILL_CONNECTING_NOTICE)]
    assert STILL_CONNECTING_NOTICE == (
        "MCP servers are still connecting; their tools become available once connected."
    )
    assert _wait_threads() == []


def test_an_interruption_ends_the_first_prompts_wait(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # SES-10: Escape ends the wait long before its limit; the prompt goes on.
    session = _start(
        tmp_path, sessions, {"slow": _Gate().server}, slow={"exposure": "direct"}
    )
    waits, notices = _waits(
        session,
        prompt_waiter=_Waiter(ToolExecutionInterruption.OPERATOR_ABORT, after=0.1),
        startup_wait_seconds=60,
    )
    started = time.monotonic()

    waits.before_first_prompt()

    assert time.monotonic() - started < 5
    assert notices == [("info", STILL_CONNECTING_NOTICE)]
    assert _wait_threads() == []


def test_the_first_prompt_does_not_run_a_waiter_without_direct_servers(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    waiter = _Waiter()
    waits, notices = _waits(session, prompt_waiter=waiter)

    waits.before_first_prompt()

    assert (waiter.calls, notices) == (0, [])


def test_a_tool_call_runs_once_its_servers_settled(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    slow = _Gate("search")
    session = _start(tmp_path, sessions, {"slow": slow.server})
    waits, _notices = _waits(session, _CODEMODE)
    call = _call("codemode", {"code": "text(tools.mcp__slow__search())"})
    threading.Timer(0.1, slow.open).start()

    assert waits.before_tool_call(call, _Waiter()) is ToolExecutionInterruption.SETTLED
    assert "mcp__slow__search" in session.callable_names()
    assert _wait_threads() == []


def test_a_tool_call_without_a_waiter_blocks_until_its_servers_settled(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    slow = _Gate("search")
    session = _start(tmp_path, sessions, {"slow": slow.server})
    waits, _notices = _waits(session, _TOOL_SEARCH)
    threading.Timer(0.1, slow.open).start()

    interruption = waits.before_tool_call(_call("tool_search", {"query": "q"}), None)

    assert interruption is ToolExecutionInterruption.SETTLED
    assert session.mcp_names() == ("mcp__slow__search",)


@pytest.mark.parametrize(
    "interruption",
    [
        ToolExecutionInterruption.OPERATOR_ABORT,
        ToolExecutionInterruption.LOCAL_COMMAND,
        ToolExecutionInterruption.SCRIPT_STOP,
    ],
)
def test_an_interruption_ends_a_tool_calls_wait(
    tmp_path: Path, sessions: list[_Session], interruption: ToolExecutionInterruption
) -> None:
    # SES-10: "Every readiness wait ends when the turn is cancelled."
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    waits, _notices = _waits(session, _TOOL_SEARCH)
    started = time.monotonic()

    result = waits.before_tool_call(
        _call("tool_search", {"query": "q"}), _Waiter(interruption, after=0.1)
    )

    assert result is interruption
    assert time.monotonic() - started < 5
    assert _wait_threads() == []


def test_ctrl_c_while_a_tool_call_waits_aborts_the_call(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    waits, _notices = _waits(session, _TOOL_SEARCH)

    def interrupt(done: threading.Event, cancel: threading.Event) -> Any:
        raise KeyboardInterrupt

    result = waits.before_tool_call(_call("tool_search", {"query": "q"}), interrupt)

    assert result is ToolExecutionInterruption.OPERATOR_ABORT
    assert _wait_threads() == []


def test_a_tool_call_that_needs_no_server_does_not_run_the_waiter(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _start(tmp_path, sessions, {"slow": _Gate().server})
    waits, _notices = _waits(session, _CODEMODE)
    waiter = _Waiter()

    result = waits.before_tool_call(_call("codemode", {"code": "text(1)"}), waiter)

    assert (result, waiter.calls) == (ToolExecutionInterruption.SETTLED, 0)


def test_an_error_of_the_wait_is_raised_on_the_calling_thread() -> None:
    def wait(cancel_event: threading.Event | None) -> bool:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_readiness_wait(wait, _Waiter())
    assert _wait_threads() == []
