"""``/mcp`` in a session (MCP parity plan S21).

A coding session with a scripted provider and the fake stdio server as a
real child process, driven through its input stream: rows SLH-01 to SLH-03,
SLH-05, SLH-06 and the command's part of EXP-11. The command itself is
tested in ``test_native_mcp_commands.py``, the terminal in
``test_native_mcp_command_pty.py``.

Not here: the prompt of ``--print`` and ``--mode json`` and an RPC client's
prompt, which run the command too
(``test_native_automation_prompt_dispatch.py``,
``test_native_automation_rpc_prompt_dispatch.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_native_extension_tool_metadata import _result, _tool_names
from test_native_mcp_session import (
    _extension,
    _Provider,
    _recorded_managers,
    _records,
    _run,
    _server,
    _settings,
    _wait_until,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.mcp.commands import MCP_USAGE, NO_OAUTH_SERVER
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.resource_loading import RuntimeResourceOptions


def _mcp_json(workspace: Path, servers: dict[str, Any], **top: Any) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers, **top}))
    return path


def test_mcp_prints_one_line_per_server(tmp_path: Path) -> None:
    # SLH-02. No prompt is sent: the command itself waits for the servers.
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    config = _mcp_json(
        workspace,
        {
            "docs": {**entry, "exposure": "direct"},
            "gone": {"command": str(tmp_path / "no-such-server")},
            "off": {**entry, "enabled": False, "exposure": "deferred"},
        },
        autoEnableCodemode="yes",
    )
    provider = _Provider([])

    status, errors = _run(workspace, provider, "/mcp\n", settings=_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert provider.requests == []
    assert "docs: connected, 2 tools (direct)\ngone: failed (codemode)\n    " in errors
    tail = errors[errors.index("off: disabled (deferred)") :]
    assert tail.split("\n")[:2] == [
        "off: disabled (deferred)",
        f"config error: {config}: autoEnableCodemode must be a boolean",
    ]


def test_mcp_without_servers_says_where_to_add_them(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()

    status, errors = _run(
        workspace, _Provider([]), "/mcp\n", settings=_settings(workspace)
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert (
        f"No MCP servers configured. Add them to {workspace / 'mcp.json'} "
        "or .pipy/mcp.json."
    ) in errors


def test_reconnect_after_the_server_process_ended(tmp_path: Path, monkeypatch) -> None:
    # SLH-03, SLH-05: the server exits during a tool call, `/mcp` shows it as
    # disconnected, and `/mcp reconnect` (one eligible server, no name
    # needed) starts it again.
    workspace = tmp_path / "work"
    entry, record = _server(
        tmp_path / "servers",
        methods={"tools/call": [{"record": "dying"}, {"exit": 3}]},
    )
    _mcp_json(workspace, {"docs": {**entry, "exposure": "direct"}})
    managers = _recorded_managers(monkeypatch)

    def dropped() -> bool:
        view = managers[0].servers()[0].connection
        return view is not None and view.state == "disconnected"

    provider = _Provider(
        [
            _result(tool_calls=(ProviderToolCall("c1", "mcp__docs__wipe", "{}"),)),
            lambda: _wait_until(dropped),
            _result(final_text="one"),
            _result(final_text="two"),
        ]
    )

    status, errors = _run(
        workspace,
        provider,
        "go\n/mcp\n/mcp reconnect\n/mcp\nagain\n",
        settings=_settings(workspace),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    before = errors.index("docs: disconnected, reconnects on next call (direct)")
    assert errors[before:].split("\n")[1] == "    Connection closed"
    reconnected = errors.index(
        'Reconnected to MCP server "docs" (connected · 2 tools).', before
    )
    assert errors.index("docs: connected, 2 tools (direct)", reconnected)
    # Two server processes answered initialize, and the tools are declared
    # to the prompt after the reconnect.
    initialized = [
        line
        for line in _records(record)
        if line.get("received", {}).get("method") == "initialize"
    ]
    assert len(initialized) == 2
    assert "mcp__docs__search" in _tool_names(provider.requests[-1])


def test_mcp_subcommands_report_what_they_cannot_do(tmp_path: Path) -> None:
    # SLH-05, SLH-06.
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry, "more": entry})

    status, errors = _run(
        workspace,
        _Provider([]),
        "/mcp reconnect nope\n/mcp login\n/mcp logout docs\n/mcp reconnect docs x\n"
        "/mcp frobnicate\n/mcp reconnect\n",
        settings=_settings(workspace, "read"),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert 'No MCP server named "nope".' in errors
    assert errors.count(NO_OAUTH_SERVER) == 2
    assert errors.count(MCP_USAGE) == 2
    # Two servers could be meant and nothing can be asked without a
    # terminal, so the last command does nothing.
    assert "Reconnected" not in errors


def test_there_is_no_mcp_command_when_mcp_is_off(tmp_path: Path) -> None:
    # EXP-11.
    workspace = tmp_path / "work"
    entry, record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry})

    status, errors = _run(
        workspace,
        _Provider([]),
        "/mcp\n",
        settings=_settings(workspace),
        resource_options=RuntimeResourceOptions(no_mcp=True),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "pipy: '/mcp' is not handled in tool-loop mode" in errors
    assert "No MCP servers configured" not in errors
    assert not record.exists()


def test_an_extension_command_named_mcp_takes_the_place_of_the_builtin(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    _extension(
        workspace,
        "own_mcp",
        "def activate(api):\n"
        "    api.register_command('mcp', 'Mine', lambda ctx, args: "
        "ctx.ui.notify('own mcp: ' + args))\n",
    )
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry})

    status, errors = _run(
        workspace, _Provider([]), "/mcp status\n", settings=_settings(workspace, "read")
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "own mcp: status" in errors
    assert "Usage: /mcp" not in errors
