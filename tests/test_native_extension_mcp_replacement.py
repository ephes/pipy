"""Extensions that replace built-in tools and MCP (MCP parity plan S37,
rows EXT-13 and EXT-14).

An extension tool named ``codemode`` or ``tool_search`` takes the place of
the built-in one; MCP activates it, and waits for servers before its calls,
only when it declares ``reaches_tools``. An extension that registers the
``/mcp`` command replaces the built-in MCP support of the session.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_native_extension_tool_metadata import _result, _tool_names, _tool_results
from test_native_mcp_reload import _Loads, _Steps
from test_native_mcp_reload import _run as _run_steps
from test_native_mcp_session import (
    _LATE,
    _UNREACHABLE,
    _events,
    _extension,
    _Provider,
    _run,
    _server,
    _settings,
    _wait_until,
    _write_mcp_json,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.codemode import selftest
from pipy_harness.native.mcp.registry import McpServerRegistry
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.repl import wiring
from pipy_harness.native.repl.mcp_session import (
    McpSession,
    mcp_command_owner,
    replaced_mcp_warning,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions

# -- EXT-13: a tool named codemode or tool_search ----------------------------

_OTHER_TOOL = """import json
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolResult


def activate(api):
    def run(ctx, params):
        names = [tool.name for tool in ctx.get_all_tools()
                 if tool.name.startswith('mcp__')]
        Path(__file__).with_name('seen.json').write_text(json.dumps(names))
        return ToolResult(content='ran')

    api.register_tool(ExtensionTool(
        name=NAME, description="Another extension's tool.",
        input_schema={'type': 'object', 'properties': {},
                      'additionalProperties': False},
        handler=run, default_active=ACTIVE, reaches_tools=REACH))
"""


def _other_tool(
    workspace: Path, name: str, *, reaches: bool, active: bool = False
) -> Path:
    body = (
        _OTHER_TOOL.replace("NAME", repr(name))
        .replace("ACTIVE", repr(active))
        .replace("REACH", "(lambda params: 'all')" if reaches else "None")
    )
    return _extension(workspace, "other", body)


def _description(request: Any, name: str) -> str:
    return next(
        tool.description for tool in request.available_tools if tool.name == name
    )


@pytest.mark.parametrize(
    ("name", "exposure"), [("codemode", "codemode"), ("tool_search", "deferred")]
)
def test_another_extensions_tool_is_activated_when_it_reaches_other_tools(
    name: str, exposure: str, tmp_path: Path, monkeypatch
) -> None:
    # Pi: "activates another extension's codemode tool when it reaches other
    # tools".
    probes: list[object] = []
    monkeypatch.setattr(selftest, "availability", lambda: probes.append(1))
    workspace = tmp_path / "work"
    _other_tool(workspace, name, reaches=True)
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(workspace / "mcp.json", {"docs": {**entry, "exposure": exposure}})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider, settings=_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    # Registered inactive, and activated for the server from the config.
    assert name in _tool_names(provider.requests[0])
    assert _description(provider.requests[0], name) == "Another extension's tool."
    assert "MCP tools are only reachable" not in errors
    # The built-in codemode was neither probed nor registered.
    assert probes == []


def test_another_extensions_tool_named_codemode_is_not_activated(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "does not activate another extension's tool named codemode".
    probes: list[object] = []
    monkeypatch.setattr(selftest, "availability", lambda: probes.append(1))
    workspace = tmp_path / "work"
    _other_tool(workspace, "codemode", reaches=False)
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(workspace / "mcp.json", {"docs": entry})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider, settings=_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "codemode" not in _tool_names(provider.requests[0])
    assert errors.count(_UNREACHABLE.format(reason="")) == 1, errors
    assert probes == []


@pytest.mark.parametrize("reaches", [True, False])
def test_a_replacement_waits_for_servers_only_when_it_declares_a_reach(
    reaches: bool, tmp_path: Path
) -> None:
    # EXT-13: the wait before a call is for a tool with `reaches_tools`.
    workspace = tmp_path / "work"
    directory = _other_tool(workspace, "codemode", reaches=reaches, active=True)
    entry, _record = _server(tmp_path / "servers", methods=_LATE)
    _write_mcp_json(workspace / "mcp.json", {"docs": entry})
    provider = _Provider(
        [
            _result(tool_calls=(ProviderToolCall("c1", "codemode", "{}"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider, settings=_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _tool_results(provider.requests[1])[-1].content.value == "ran"
    seen = json.loads((directory / "seen.json").read_text())
    # The server answers `initialize` after half a second.
    assert seen == (["mcp__docs__search", "mcp__docs__wipe"] if reaches else [])


# -- EXT-14: the /mcp command -------------------------------------------------

_OWN_MCP = """import json
from pathlib import Path


def activate(api):
    seen = Path(__file__).with_name('own-mcp.jsonl')

    def note(*row):
        with seen.open('a') as handle:
            handle.write(json.dumps(list(row)) + '\\n')

    api.register_command('mcp', 'Mine', lambda ctx, args: note('command', args))
SERVER
HOOK
"""
_OWN_SERVER = """    api.register_mcp_server('plugin', {'command': 'never-started'})
"""
_OWN_HOOK = """
    @api.on('mcp_servers_change')
    def changed(event, ctx):
        note('servers', [server.name for server in event.servers])

    @api.on('session_start')
    def started(event, ctx):
        api.register_mcp_server('later', {'command': 'never-started'})
"""
_OWN = ".pipy/extensions/own_mcp.py"


def _own_mcp(workspace: Path, *, server: bool = False, hook: bool = False) -> Path:
    body = _OWN_MCP.replace("SERVER", _OWN_SERVER if server else "").replace(
        "HOOK", _OWN_HOOK if hook else ""
    )
    return _extension(workspace, "own_mcp", body) / "own-mcp.jsonl"


def _rows(path: Path) -> list[list[Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _refuse_mcp_json(monkeypatch) -> None:
    def refuse(**_kwargs: object) -> None:
        raise AssertionError("mcp.json was read")

    monkeypatch.setattr(wiring, "load_mcp_config", refuse)


def test_an_extension_with_the_mcp_command_replaces_builtin_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-14; Pi "should leave out replaceable extensions whose names another
    # extension registers".
    _refuse_mcp_json(monkeypatch)
    workspace = tmp_path / "work"
    seen = _own_mcp(workspace)
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(workspace / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(
        workspace, provider, "/mcp status\ngo\n", settings=_settings(workspace)
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    # Nothing connected and nothing of MCP was turned on.
    assert not record.exists()
    names = _tool_names(provider.requests[0])
    assert not [name for name in names if "mcp__" in name]
    assert "codemode" not in names and "tool_search" not in names
    # `/mcp` is the extension's.
    assert _rows(seen) == [["command", "status"]]
    assert "Usage: /mcp" not in errors
    assert errors.count(replaced_mcp_warning("own_mcp")) == 1, errors


def test_no_warning_when_mcp_is_off_anyway(tmp_path: Path, monkeypatch) -> None:
    _refuse_mcp_json(monkeypatch)
    workspace = tmp_path / "work"
    _own_mcp(workspace)
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(
        workspace,
        provider,
        settings=_settings(workspace),
        resource_options=RuntimeResourceOptions(no_mcp=True),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "was not loaded" not in errors


@pytest.mark.parametrize("hook", [False, True])
def test_registered_servers_are_for_the_replacing_extension(
    hook: bool, tmp_path: Path, monkeypatch
) -> None:
    # EXT-14 with EXT-10: the built-in support connects nothing; an
    # extension that handles `mcp_servers_change` is what connects servers.
    _refuse_mcp_json(monkeypatch)
    workspace = tmp_path / "work"
    seen = _own_mcp(workspace, server=True, hook=hook)
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider, settings=_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert not [name for name in _tool_names(provider.requests[0]) if "mcp__" in name]
    unhandled = (
        f'Extension "{_OWN}" error: MCP server "plugin" is registered, but no '
        "loaded extension connects MCP servers"
    )
    if hook:
        assert ["servers", ["plugin", "later"]] in _rows(seen)
        assert unhandled not in errors
    else:
        assert errors.count(unhandled) == 1, errors


def test_a_reload_turns_builtin_mcp_off_and_on_with_the_extension(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-14: which extension has `/mcp` is decided again by every reload.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    workspace.mkdir()
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(
        tmp_path / "config" / "mcp.json", {"docs": {**entry, "exposure": "direct"}}
    )
    provider = _Provider([_result(final_text=text) for text in ("1", "2", "3")])
    extension = workspace / _OWN

    def add() -> None:
        _own_mcp(workspace)

    def remove() -> None:
        extension.unlink()

    def closed() -> None:
        _wait_until(lambda: "stdin-closed" in _events(record))

    status, errors = _run_steps(
        workspace,
        provider,
        _Steps("one", add, "/reload", closed, "two", remove, "/reload", "three"),
    )

    assert status is HarnessStatus.SUCCEEDED, errors

    def mcp_names(index: int) -> list[str]:
        names = _tool_names(provider.requests[index])
        return [name for name in names if name.startswith("mcp__")]

    assert mcp_names(0) == ["mcp__docs__search", "mcp__docs__wipe"]
    assert mcp_names(1) == []
    assert mcp_names(2) == ["mcp__docs__search", "mcp__docs__wipe"]
    assert errors.count(replaced_mcp_warning("own_mcp")) == 1, errors


# -- McpSession ---------------------------------------------------------------


def _extensions(owner: str | None) -> Any:
    commands = {} if owner is None else {"mcp": SimpleNamespace(extension=owner)}
    return SimpleNamespace(mcp_servers=McpServerRegistry(), commands=commands)


def test_the_owner_of_the_mcp_command() -> None:
    assert mcp_command_owner({}) is None
    assert mcp_command_owner({"mcp": SimpleNamespace(extension="a.py")}) == "a.py"
    assert mcp_command_owner({"mcp2": SimpleNamespace(extension="a.py")}) is None


def test_the_session_loads_its_servers_for_the_extensions_at_every_start(
    tmp_path: Path,
) -> None:
    loads = _Loads(tmp_path)
    current = [_extensions("a.py")]
    session = McpSession(
        loads, tools=loads.tools, notify=loads.notify, extensions=current[0]
    )
    session.follow_extensions(lambda: current[0])
    try:
        # Loaded once, for the extension that has `/mcp`: nothing to start.
        session.start()
        assert len(loads.loaded) == 1
        assert not session.servers.enabled
        assert session.servers.replaced_by == "a.py"
        assert "/mcp" not in session.commands
        assert loads.opened == []
        assert loads.notices == [("warning", replaced_mcp_warning("a.py"))]

        # A reload without that extension: the candidate was loaded for the
        # extensions before it, and `start` loads again for those after it.
        with session.reloading() as accept:
            current[0] = _extensions(None)
            accept()
        assert len(loads.loaded) == 3
        assert session.servers is loads.loaded[2]
        assert session.servers.enabled and session.servers.replaced_by is None
        assert "/mcp" in session.commands
        _wait_until(lambda: loads.opened == [(2, "docs")])
        assert len(loads.notices) == 1

        # And one that brings another extension with the command.
        with session.reloading() as accept:
            current[0] = _extensions("b.py")
            accept()
        assert len(loads.loaded) == 5
        assert session.servers.replaced_by == "b.py"
        assert loads.notices[1:] == [("warning", replaced_mcp_warning("b.py"))]

        # A reload that changes nothing about it loads once.
        with session.reloading() as accept:
            accept()
        assert len(loads.loaded) == 6
        assert loads.notices[2:] == [("warning", replaced_mcp_warning("b.py"))]
    finally:
        session.close()
        for loaded in loads.loaded:
            loaded.manager.close()
