"""Tests for the resource tools of a session's MCP manager (MCP parity plan S23).

Rows RES-01 and RES-02 (when the three resource tools exist, for which
servers and with which exposure), EXP-10 (they count as MCP tools for the
tool filters), the read hint of TLS-12 and the resource count of the short
state. The manager runs against in-memory servers and a real capability
value; the last test against a real server process.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Any

from test_native_mcp_connection import _is_alive, _Server, _wait_until
from test_native_mcp_connection_resources import _INTRO, _ResourceServer
from test_native_mcp_manager import (
    _WAIT_SECONDS,
    _entry,
    _Session,
    _status_of,
    _stdio_manager,
    _tool,
    sessions,  # noqa: F401 - the fixture that closes the managers
)
from test_native_tool_capabilities_mcp import _session as _capabilities
from test_native_tool_filter_patterns import (
    _callable,
    _declared,
    _registered,
    _Tool,
)

from pipy_harness.native.mcp.manager import describe_state
from pipy_harness.native.mcp.resources import create_mcp_resource_tools
from pipy_harness.native.tool_capabilities import ToolFilterOptions
from pipy_harness.native.tools import ToolContext, ToolPort, ToolRequest

_RESOURCE_TOOLS = (
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
)


def _invoke(
    port: ToolPort, tmp_path: Path, arguments: dict[str, Any] | None = None
) -> tuple[bool, str]:
    result = port.invoke(
        ToolRequest(
            tool_request_id="pipy-tool-1",
            tool_name=port.definition.name,
            arguments=arguments or {},
        ),
        ToolContext(workspace_root=tmp_path),
    )
    return result.is_error, result.output_text


def _exposures(session: _Session) -> set[str]:
    return {session.port(name).definition.exposure for name in _RESOURCE_TOOLS}


# -- RES-01: when the tools exist ---------------------------------------------


def test_the_resource_tools_exist_once_a_server_with_resources_connected(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    docs = _ResourceServer(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": docs, "plain": _Server(tools=[_tool("x")])})
    sessions.append(session)
    assert session.mcp_names() == ()

    session.start()

    assert set(session.mcp_names()) == {
        "mcp__docs__search",
        "mcp__plain__x",
        *_RESOURCE_TOOLS,
    }
    # The default exposure is `codemode`: callable from scripts, not declared.
    assert _exposures(session) == {"deferred"}
    assert session.declared() == ("read",)
    assert set(_RESOURCE_TOOLS) <= session.callable_names()
    assert session.manager.tool_owner("read_mcp_resource") is None
    listed = json.loads(session.call("list_mcp_resources"))
    assert listed == {"resources": [{"server": "docs", **_INTRO}]}
    assert session.call(
        "read_mcp_resource", {"server": "docs", "uri": "docs://intro"}
    ) == ("# docs://intro")
    # A server without the capability is none of theirs.
    assert session.call(
        "read_mcp_resource", {"server": "plain", "uri": "docs://intro"}
    ) == ('MCP server "plain" has no resources. Servers with resources: docs')


def test_without_a_server_with_resources_there_are_no_resource_tools(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    hidden = _ResourceServer(tools=[_tool("search")])
    session = _Session(
        tmp_path,
        {"plain": _Server(tools=[_tool("x")]), "hidden": hidden},
        # A hidden server's resources are not reached.
        [_entry("plain"), _entry("hidden", exposure="hidden")],
    )
    sessions.append(session)

    session.start()

    assert set(session.mcp_names()) == {"mcp__plain__x", "mcp__hidden__search"}


def test_a_server_with_resources_only_gets_the_resource_tools(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(
        tmp_path, {"docs": _ResourceServer(capabilities={"resources": {}})}
    )
    sessions.append(session)

    session.start()

    assert session.mcp_names() == _RESOURCE_TOOLS
    assert json.loads(
        session.call("list_mcp_resource_templates", {"server": "docs"})
    ) == {
        "server": "docs",
        "resourceTemplates": [
            {"server": "docs", "uriTemplate": "docs://pages/{slug}", "name": "page"}
        ],
    }


def test_every_server_is_listed_in_name_order_with_the_ones_that_fail(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    zeta = _ResourceServer(resources=[{"uri": "z://1", "name": "z1"}])
    alpha = _ResourceServer(resources=[{"uri": "a://1", "name": "a1"}])
    session = _Session(tmp_path, {"zeta": zeta, "alpha": alpha})
    sessions.append(session)
    session.start()

    listed = json.loads(session.call("list_mcp_resources"))

    assert listed == {
        "resources": [
            {"server": "alpha", "uri": "a://1", "name": "a1"},
            {"server": "zeta", "uri": "z://1", "name": "z1"},
        ]
    }


def test_a_dropped_server_keeps_the_resource_tools_and_a_read_reconnects_it(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    first = _ResourceServer()
    second = _ResourceServer()
    transports = [first, second]
    session = _Session(tmp_path, {"docs": first})
    sessions.append(session)
    session.start()

    session.servers = {"docs": second}
    first.end.close()
    _wait_until(lambda: _status_of(session, "docs").connection.state == "disconnected")  # type: ignore[union-attr]

    assert session.mcp_names() == _RESOURCE_TOOLS
    assert set(_RESOURCE_TOOLS) <= session.callable_names()
    assert session.call(
        "read_mcp_resource", {"server": "docs", "uri": "docs://intro"}
    ) == ("# docs://intro")
    assert session.opened == ["docs", "docs"]
    assert (transports[0].reads, transports[1].reads) == ([], ["docs://intro"])
    _wait_until(lambda: _status_of(session, "docs").connection.state == "connected")  # type: ignore[union-attr]


def test_disabling_the_only_server_with_resources_makes_the_tools_unreachable(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    docs = _ResourceServer(tools=[_tool("search")])
    again = _ResourceServer(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": docs})
    sessions.append(session)
    session.start()
    prepared = session.port("read_mcp_resource")
    assert set(_RESOURCE_TOOLS) <= session.callable_names()

    assert session.manager.set_enabled("docs", False) is None

    # As in Pi the tools stay registered, hidden.
    assert session.mcp_names() == _RESOURCE_TOOLS
    assert _exposures(session) == {"hidden"}
    assert not set(_RESOURCE_TOOLS) & session.callable_names()
    arguments = {"server": "docs", "uri": "docs://intro"}
    assert _invoke(prepared, tmp_path, arguments) == (
        True,
        'MCP server "docs" has no resources',
    )

    session.servers = {"docs": again}
    assert session.manager.set_enabled("docs", True) is None

    assert _exposures(session) == {"deferred"}
    assert set(_RESOURCE_TOOLS) <= session.callable_names()
    # A call prepared before uses the servers as they are now.
    assert _invoke(prepared, tmp_path, arguments) == (False, "# docs://intro")


def test_the_other_server_keeps_the_tools_when_one_is_disabled(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(tmp_path, {"docs": _ResourceServer(), "wiki": _ResourceServer()})
    sessions.append(session)
    session.start()
    published = len(session.published)

    assert session.manager.set_enabled("docs", False) is None

    assert _exposures(session) == {"deferred"}
    # Nothing changed for the session's tools, so nothing was published.
    assert len(session.published) == published
    assert session.call(
        "read_mcp_resource", {"server": "docs", "uri": "docs://intro"}
    ) == ('MCP server "docs" has no resources. Servers with resources: wiki')


# -- RES-02: exposure -----------------------------------------------------------


def test_the_tools_have_the_widest_exposure_of_the_servers_they_reach(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    servers: dict[str, _Server] = {
        name: _ResourceServer() for name in ("a", "b", "c", "d")
    }
    session = _Session(
        tmp_path,
        servers,
        [
            _entry("a", exposure="deferred"),
            _entry("b"),
            _entry("c", exposure="direct"),
            # A direct server without resources does not count.
            _entry("d", exposure="direct", enabled=False),
        ],
    )
    sessions.append(session)

    session.start()

    assert _exposures(session) == {"direct"}
    assert session.declared() == ("read", *_RESOURCE_TOOLS)

    assert session.manager.set_enabled("c", False) is None

    # `codemode` is wider than `deferred`; both are deferred tools.
    assert session.manager._resource_exposure == "codemode"
    assert _exposures(session) == {"deferred"}
    assert session.declared() == ("read",)
    assert set(_RESOURCE_TOOLS) <= session.callable_names()

    assert session.manager.set_enabled("b", False) is None

    assert session.manager._resource_exposure == "deferred"
    assert set(_RESOURCE_TOOLS) <= session.callable_names()

    assert session.manager.set_exposure("a", "hidden") is None

    assert _exposures(session) == {"hidden"}
    assert not set(_RESOURCE_TOOLS) & session.callable_names()


def test_the_tools_leave_the_declared_set_when_they_stop_being_direct(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(
        tmp_path,
        {"docs": _ResourceServer(tools=[_tool("search")])},
        [_entry("docs", exposure="direct")],
    )
    sessions.append(session)
    session.start()
    # A selection, where registration alone would keep the names.
    session.tools.set_active_tools(["read", "mcp__docs__search", *_RESOURCE_TOOLS])
    assert session.declared() == ("read", "mcp__docs__search", *_RESOURCE_TOOLS)

    assert session.manager.set_exposure("docs", "codemode") is None

    assert session.declared() == ("read",)
    assert set(_RESOURCE_TOOLS) <= session.callable_names()

    assert session.manager.set_exposure("docs", "direct") is None

    assert session.declared() == ("read", "mcp__docs__search", *_RESOURCE_TOOLS)


def test_direct_resource_tools_leave_a_selection_when_their_server_is_disabled(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(
        tmp_path,
        {"docs": _ResourceServer(), "wiki": _ResourceServer()},
        [_entry("docs", exposure="direct"), _entry("wiki")],
    )
    sessions.append(session)
    session.start()
    session.tools.set_active_tools(["read", *_RESOURCE_TOOLS])

    assert session.manager.set_enabled("docs", False) is None

    assert session.declared() == ("read",)
    assert set(_RESOURCE_TOOLS) <= session.callable_names()


def test_a_server_tool_exposure_does_not_widen_the_resource_tools(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    # Pi `syncResourceTools` reads the server's exposure, not `toolExposure`.
    session = _Session(
        tmp_path,
        {"docs": _ResourceServer(tools=[_tool("search")])},
        [_entry("docs", toolExposure={"search": "direct"})],
    )
    sessions.append(session)

    session.start()

    assert session.declared() == ("read", "mcp__docs__search")
    assert _exposures(session) == {"deferred"}


def test_another_tool_of_a_resource_tools_name_keeps_it(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    own = _Tool("read_mcp_resource")
    session = _Session(
        tmp_path,
        {"docs": _ResourceServer()},
        builtins={"read": _Tool("read"), "read_mcp_resource": own},
    )
    sessions.append(session)

    session.start()

    assert session.tools.state.registry["read_mcp_resource"] is own
    assert session.notices() == [
        (
            "warning",
            'MCP tool "read_mcp_resource" is not available: another tool has that name',
        )
    ]


# -- EXP-10: the tool filters ---------------------------------------------------


def _resource_ports(exposure: str) -> dict[str, ToolPort]:
    return {
        port.definition.name: port
        for port in create_mcp_resource_tools(exposure=exposure, servers=lambda: [])
    }


def test_an_allowlist_of_plain_names_keeps_the_resource_tools_for_scripts(
    tmp_path: Path,
) -> None:
    # Pi: "keeps MCP tools when --tools names no MCP tool".
    for exposure in ("codemode", "deferred"):
        tools = _capabilities(tmp_path, ToolFilterOptions(allow=("read",)))

        tools.publish_mcp_tools(_resource_ports(exposure))

        assert _declared(tools) == ("read",)
        assert _callable(tools) == {"read", *_RESOURCE_TOOLS}


def test_an_allowlist_with_mcp_entries_filters_the_resource_tools_too(
    tmp_path: Path,
) -> None:
    # Pi: "filters MCP tools by the mcp__ entries of --tools".
    tools = _capabilities(tmp_path, ToolFilterOptions(allow=("read", "mcp__docs__s*")))

    tools.publish_mcp_tools(_resource_ports("codemode"))

    assert _registered(tools) == ("read",)

    named = _capabilities(
        tmp_path,
        ToolFilterOptions(allow=("read", "mcp__docs__s*", "read_mcp_resource")),
    )
    named.publish_mcp_tools(_resource_ports("direct"))

    assert _declared(named) == ("read", "read_mcp_resource")


def test_the_resource_tools_are_excluded_by_name(tmp_path: Path) -> None:
    tools = _capabilities(
        tmp_path, ToolFilterOptions(exclude=("mcp__*", "list_mcp_resource*"))
    )

    tools.publish_mcp_tools(_resource_ports("direct"))

    assert _declared(tools) == ("read", "bash", "read_mcp_resource")


# -- TLS-12: the read hint of a resource link -----------------------------------

_LINK = {
    "content": [
        {
            "type": "resource_link",
            "uri": "docs://guide",
            "name": "guide",
            "title": "The Guide",
        }
    ]
}


def test_a_resource_link_names_the_read_tool_only_when_it_reaches_the_server(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    handlers: dict[str, Any] = {"tools/call": lambda message: _LINK}
    with_resources = _ResourceServer(tools=[_tool("link")], handlers=handlers)
    without = _Server(tools=[_tool("link")], handlers=handlers)
    unreached = _ResourceServer(tools=[_tool("link")], handlers=handlers)
    session = _Session(
        tmp_path,
        {"docs": with_resources, "plain": without, "quiet": unreached},
        [
            _entry("docs"),
            _entry("plain"),
            # The tool is callable, the server's resources are not reached.
            _entry("quiet", exposure="hidden", toolExposure={"link": "codemode"}),
        ],
    )
    sessions.append(session)
    session.start()

    assert session.call("mcp__docs__link") == (
        '[Resource docs://guide "The Guide". '
        'Read it with read_mcp_resource (server "docs")]'
    )
    assert session.call("mcp__plain__link") == '[Resource docs://guide "The Guide"]'
    assert session.call("mcp__quiet__link") == '[Resource docs://guide "The Guide"]'

    # The answer is the one of the moment of the call.
    assert session.manager.set_exposure("quiet", "codemode") is None

    assert session.call("mcp__quiet__link") == (
        '[Resource docs://guide "The Guide". '
        'Read it with read_mcp_resource (server "quiet")]'
    )


# -- RES-10: the counts ---------------------------------------------------------


def test_the_state_of_a_connected_server_counts_its_resources(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    docs = _ResourceServer(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": docs, "plain": _Server(tools=[_tool("x")])})
    sessions.append(session)
    session.start()

    assert describe_state(_status_of(session, "docs")) == (
        "connected · 1 tool · 1 resource"
    )
    assert describe_state(_status_of(session, "plain")) == "connected · 1 tool"
    published = len(session.published)

    docs.resources = [_INTRO, {"uri": "docs://faq", "name": "faq"}]
    docs.notify("notifications/resources/list_changed")

    _wait_until(
        lambda: (
            describe_state(_status_of(session, "docs"))
            == "connected · 1 tool · 2 resources"
        )
    )
    # A changed count changes no tool.
    assert len(session.published) == published


# -- a real server process -------------------------------------------------------


def test_a_read_reconnects_a_server_whose_process_was_killed(tmp_path: Path) -> None:
    registries: list[dict[str, ToolPort]] = []
    manager, transports, record = _stdio_manager(
        tmp_path,
        {
            "capabilities": {"resources": {}},
            "resources": [_INTRO],
            "resourceContents": {
                "docs://intro": [{"uri": "docs://intro", "text": "# Intro"}]
            },
        },
    )

    def publish(registry: Any) -> tuple[str, ...]:
        registries.append(dict(registry))
        return ()

    manager._publish_tools = publish
    manager.start()
    assert manager.wait_until_settled(_WAIT_SECONDS)
    assert tuple(registries[-1]) == _RESOURCE_TOOLS
    pid = transports[0].pid
    assert pid is not None

    os.kill(pid, signal.SIGKILL)
    _wait_until(lambda: manager.servers()[0].connection.state == "disconnected")  # type: ignore[union-attr]

    # The only server with resources is gone, the tools are still the same.
    assert len(registries) == 1
    assert registries[-1]["read_mcp_resource"].definition.exposure == "deferred"
    arguments = {"server": "docs", "uri": "docs://intro"}
    assert _invoke(registries[-1]["read_mcp_resource"], tmp_path, arguments) == (
        False,
        "# Intro",
    )
    second = transports[1].pid
    assert second is not None and second != pid and _is_alive(second)
    assert describe_state(manager.servers()[0]) == "connected · 0 tools · 1 resource"
    manager.close()
    assert not _is_alive(second)
    reads = [
        line["received"]["params"]["uri"]
        for line in map(json.loads, record.read_text().splitlines())
        if line.get("received", {}).get("method") == "resources/read"
    ]
    assert reads == ["docs://intro"]
