"""MCP servers in a session (MCP parity plan S17).

A coding session with a scripted provider and the fake stdio server as a real
child process: the servers of ``mcp.json`` start with the session, their
tools reach the model and codemode scripts through the normal tool pipeline,
and the session's end closes them. Rows SES-01 to SES-03, SES-17, SES-19,
EXP-05, the MCP part of EXT-03 and CFG-01's trust wiring.

Nothing waits for a server yet (that is S18), so the provider's first turn
calls an extension tool that waits until the server's tools are registered.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_codemode_delivery import settings as codemode_settings
from test_native_extension_tool_metadata import (
    _result,
    _tool_names,
    _tool_results,
)
from test_native_mcp_connection import _FAKE_SERVER, _is_alive

from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.project_trust import has_trust_requiring_project_resources
from pipy_harness.native.settings import SettingsManager

_WAIT_SECONDS = 20.0

_SEARCH = {
    "name": "search",
    "description": "Search the docs.",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "outputSchema": {
        "type": "object",
        "properties": {"hits": {"type": "array", "items": {"type": "string"}}},
        "required": ["hits"],
    },
    "annotations": {"readOnlyHint": True},
}
_WIPE = {
    "name": "wipe",
    "description": "Delete every page.",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"destructiveHint": True},
}
_RESULTS = {
    "search": {
        "content": [{"type": "text", "text": "pipy guide\npipy faq"}],
        "structuredContent": {"hits": ["pipy guide", "pipy faq"]},
    },
    "wipe": {"content": [{"type": "text", "text": "wiped"}]},
}

# Waits until `count` MCP tools are registered and writes what
# `ctx.get_all_tools()` reports about them.
_WAITER = """import json, time
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolResult


def activate(api):
    def wait(ctx, params):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            tools = [t for t in ctx.get_all_tools() if t.name.startswith('mcp__')]
            if len(tools) >= params['count']:
                Path(__file__).with_name('tools.json').write_text(json.dumps([
                    {'name': t.name, 'exposure': t.exposure,
                     'namespace': t.namespace.name,
                     'aliases': list(t.namespace.aliases),
                     'description': t.namespace.description,
                     'instructions': t.namespace.instructions,
                     'read_only': t.annotations.read_only_hint
                     if t.annotations else None,
                     'reaches': t.reaches_tools is not None}
                    for t in tools]))
                return ToolResult(content='ready')
            time.sleep(0.02)
        return ToolResult(content='no MCP tools', is_error=True)

    api.register_tool(ExtensionTool(
        name='wait_for_mcp', description='Wait for the MCP tools.',
        input_schema={'type': 'object',
                      'properties': {'count': {'type': 'integer'}},
                      'required': ['count'], 'additionalProperties': False},
        handler=wait))
"""

# A permission extension as Pi's docs/mcp.md "Permissions" describes it, and a
# result hook.
_HOOKS = """from pipy_harness.extensions import ToolBlock, ToolResultTransform


def activate(api):
    @api.on('tool_call')
    def gate(event, ctx):
        hints = next((t.annotations for t in ctx.get_all_tools()
                      if t.name == event.tool_name), None)
        if hints is not None and hints.destructive_hint is True:
            return ToolBlock(reason=event.tool_name + ' was not approved')

    @api.on('tool_result')
    def redact(event, ctx):
        if event.tool_name == 'mcp__docs__search':
            return ToolResultTransform(
                content=event.content.replace('faq', '[redacted]'),
                structured_content=event.structured_content)
"""

_WAIT_CALL = ProviderToolCall("wait", "wait_for_mcp", '{"count": 2}')


def _server(
    directory: Path, name: str = "docs", **scenario: Any
) -> tuple[dict[str, Any], Path]:
    """An ``mcpServers`` entry for a fake server, and its record file."""

    directory.mkdir(parents=True, exist_ok=True)
    record = directory / f"{name}-record.jsonl"
    path = directory / f"{name}-scenario.json"
    path.write_text(
        json.dumps(
            {
                "record": str(record),
                "tools": [_SEARCH, _WIPE],
                "toolResults": _RESULTS,
                "onStdinClose": [{"record": "stdin-closed"}, {"exit": 0}],
                **scenario,
            }
        )
    )
    entry = {
        "command": sys.executable,
        "args": ["-I", str(_FAKE_SERVER), "--scenario", str(path)],
    }
    return entry, record


def _write_mcp_json(path: Path, servers: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}))


def _extension(workspace: Path, name: str, body: str) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")
    return directory


def _records(record: Path) -> list[dict[str, Any]]:
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines()]


def _tool_calls(record: Path) -> list[tuple[str, Any]]:
    return [
        (line["received"]["params"]["name"], line["received"]["params"]["arguments"])
        for line in _records(record)
        if line.get("received", {}).get("method") == "tools/call"
    ]


def _events(record: Path) -> list[str]:
    return [line["event"] for line in _records(record) if "event" in line]


def _received(record: Path, method: str) -> bool:
    return any(
        line.get("received", {}).get("method") == method for line in _records(record)
    )


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def _mcp_threads(before: set[threading.Thread]) -> list[str]:
    """The MCP threads that started since ``before`` was taken and still run.

    Other test modules leave threads of their in-memory servers behind.
    """

    return [
        thread.name
        for thread in threading.enumerate()
        if thread not in before and thread.name.startswith("mcp")
    ]


class _Provider:
    """Answers with scripted results; a callable runs before its result."""

    name = "stub"
    model_id = "stub-model"
    supports_mid_convo_system_messages = True
    supports_tool_calls = True

    def __init__(self, results: list[ProviderResult | Callable[[], object]]) -> None:
        self._results = list(results)
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        step = self._results.pop(0)
        while not isinstance(step, ProviderResult):
            step()
            step = self._results.pop(0)
        return step


def _run(
    workspace: Path,
    provider: _Provider,
    prompts: str = "go\n",
    *,
    settings: SettingsManager | None = None,
) -> tuple[HarnessStatus, str]:
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        settings_manager=settings,
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result.status, errors.getvalue()


def test_a_direct_mcp_tool_is_declared_and_called_like_a_builtin(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "declares directly exposed MCP tools to the model" (EXP-05).
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    extensions = _extension(workspace, "waiter", _WAITER)
    entry, record = _server(tmp_path / "servers", instructions="Search first.")
    _write_mcp_json(
        config / "mcp.json",
        {"docs": {**entry, "exposure": "direct", "description": "Product docs"}},
    )
    provider = _Provider(
        [
            _result(tool_calls=(_WAIT_CALL,)),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__docs__search", '{"query": "pipy"}'),
                    ProviderToolCall("c2", "mcp__docs__search", '{"query": 3}'),
                )
            ),
            _result(final_text="done"),
        ]
    )
    before = set(threading.enumerate())

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    # The request after the server connected declares both tools.
    assert _tool_names(provider.requests[1])[-2:] == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    declared = provider.requests[1].available_tools[-2]
    assert declared.description == "Search the docs."
    assert declared.input_schema == _SEARCH["inputSchema"]
    found, invalid = _tool_results(provider.requests[2])[-2:]
    assert (found.is_error, found.content.value) == (False, "pipy guide\npipy faq")
    # Arguments are checked against the server's schema before the call.
    assert invalid.is_error and "query" in invalid.content.value
    assert _tool_calls(record) == [("search", {"query": "pipy"})]
    # EXT-03: what an extension reads about the MCP tools.
    assert json.loads((extensions / "tools.json").read_text()) == [
        {
            "name": f"mcp__docs__{tool}",
            "exposure": "direct",
            "namespace": "mcp__docs",
            "aliases": ["docs"],
            "description": "Product docs",
            "instructions": "Search first.",
            "read_only": read_only,
            "reaches": False,
        }
        for tool, read_only in (("search", True), ("wipe", None))
    ]
    # SES-17: the session's end closed the server and waited for it.
    assert "stdin-closed" in _events(record)
    # A transport's own threads end just after it reported its close.
    _wait_until(lambda: _mcp_threads(before) == [])


def test_hooks_block_and_change_direct_mcp_calls(tmp_path: Path, monkeypatch) -> None:
    # SES-19; Pi docs/mcp.md "Permissions".
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    _extension(workspace, "waiter", _WAITER)
    _extension(workspace, "hooks", _HOOKS)
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider(
        [
            _result(tool_calls=(_WAIT_CALL,)),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__docs__wipe", "{}"),
                    ProviderToolCall("c2", "mcp__docs__search", '{"query": "pipy"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    wipe, search = _tool_results(provider.requests[2])[-2:]
    assert wipe.is_error and "mcp__docs__wipe was not approved" in wipe.content.value
    assert (search.is_error, search.content.value) == (
        False,
        "pipy guide\npipy [redacted]",
    )
    assert _tool_calls(record) == [("search", {"query": "pipy"})]


def test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model(
    tmp_path: Path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Pi: "exposes codemode-only MCP tools through codemode and hides them
    # from the model", "applies per-tool exposure overrides", "rejects direct
    # model calls to codemode-only MCP tools"; CMD-05 and SES-19 for nested
    # calls.
    workspace = tmp_path / "work"
    workspace.mkdir()
    _extension(workspace, "waiter", _WAITER)
    _extension(workspace, "hooks", _HOOKS)
    entry, record = _server(
        tmp_path / "servers",
        tools=[_SEARCH, _WIPE, {"name": "fail", "inputSchema": {"type": "object"}}],
        toolResults={
            **_RESULTS,
            "fail": {
                "content": [{"type": "text", "text": "server exploded"}],
                "isError": True,
            },
        },
    )
    # The default exposure is `codemode`; one tool is declared directly.
    _write_mcp_json(
        workspace / "mcp.json",
        {"docs": {**entry, "toolExposure": {"fail": "direct"}}},
    )
    code = """found = tools.mcp__docs__search(query='pipy')
try:
    tools.mcp__docs__wipe()
except ToolError as error:
    blocked = str(error)
failed = tools.mcp__docs__fail()
text({'hits': found['structuredContent']['hits'],
      'text': found['content'][0]['text'], 'blocked': blocked,
      'failed': [failed['isError'], failed['content'][0]['text']],
      'listed': [tool['name'] for tool in search_tools('docs search')]})
"""
    provider = _Provider(
        [
            _result(
                tool_calls=(ProviderToolCall("wait", "wait_for_mcp", '{"count": 3}'),)
            ),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "codemode", json.dumps({"code": code})),
                )
            ),
            _result(
                tool_calls=(
                    ProviderToolCall("c2", "mcp__docs__search", '{"query": "pipy"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider, settings=codemode_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    names = _tool_names(provider.requests[1])
    assert "codemode" in names and "mcp__docs__fail" in names
    assert not {"mcp__docs__search", "mcp__docs__wipe"} & set(names)
    parent = _tool_results(provider.requests[2])[-1]
    assert not parent.is_error, parent.content.value
    output = json.loads(
        parent.content.value.split("Output:\n", 1)[1].split("\nTool calls", 1)[0]
    )
    assert output["hits"] == ["pipy guide", "pipy faq"]
    # The result hook changed the text and passed the structured content on.
    assert output["text"] == "pipy guide\npipy faq"
    assert "mcp__docs__wipe was not approved" in output["blocked"]
    # CMD-05: an error result resolves, with `isError`, instead of raising.
    assert output["failed"] == [True, "server exploded"]
    assert "mcp__docs__search" in output["listed"]
    rejected = _tool_results(provider.requests[3])[-1]
    assert rejected.is_error
    assert rejected.content.value == "unknown tool: mcp__docs__search"
    assert _tool_calls(record) == [("search", {"query": "pipy"}), ("fail", {})]
    assert "stdin-closed" in _events(record)


def test_session_end_closes_a_server_that_is_still_connecting(
    tmp_path: Path, monkeypatch
) -> None:
    # SES-01 and SES-17: the prompt ran while the server had not answered
    # `initialize`, and the session's end did not leave its process behind.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    entry, record = _server(
        tmp_path / "servers",
        methods={"initialize": [{"record": "initialize"}, {"sleep": 120}]},
        onStdinClose=[],
    )
    pid_file = tmp_path / "pid"
    launcher = (
        "import os, sys\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[2:]])\n"
    )
    entry = {
        "command": sys.executable,
        "args": ["-I", "-c", launcher, str(pid_file), *entry["args"]],
    }
    _write_mcp_json(config / "mcp.json", {"slow": entry})
    provider = _Provider(
        [lambda: _wait_until(lambda: "initialize" in _events(record)), _result()]
    )
    before = set(threading.enumerate())

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert not _is_alive(int(pid_file.read_text()))
    # A transport's own threads end just after it reported its close.
    _wait_until(lambda: _mcp_threads(before) == [])


def test_a_session_that_ends_in_a_lifecycle_hook_still_closes_its_servers(
    tmp_path: Path, monkeypatch
) -> None:
    """Closing the servers does not depend on `session_shutdown` being reached."""

    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    _extension(
        workspace,
        "interrupt",
        "def activate(api):\n"
        "    @api.on('agent_settled')\n"
        "    def stop(event, ctx):\n"
        "        raise KeyboardInterrupt\n",
    )
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": entry})
    provider = _Provider(
        [lambda: _wait_until(lambda: _received(record, "tools/list")), _result()]
    )
    before = set(threading.enumerate())

    with pytest.raises(KeyboardInterrupt):
        _run(workspace, provider)

    assert "stdin-closed" in _events(record)
    _wait_until(lambda: _mcp_threads(before) == [])


def _two_servers(tmp_path: Path, config: Path, workspace: Path) -> tuple[Path, Path]:
    user_entry, user_record = _server(tmp_path / "servers", "user")
    project_entry, project_record = _server(tmp_path / "servers", "project")
    _write_mcp_json(config / "mcp.json", {"user": user_entry})
    _write_mcp_json(workspace / ".pipy" / "mcp.json", {"project": project_entry})
    return user_record, project_record


def test_project_mcp_json_is_read_only_for_a_trusted_project(
    tmp_path: Path, monkeypatch
) -> None:
    # CFG-01, at the session: an untrusted project's servers never start.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    user_record, project_record = _two_servers(tmp_path, config, workspace)
    assert has_trust_requiring_project_resources(workspace)
    provider = _Provider(
        [lambda: _wait_until(lambda: _received(user_record, "tools/list")), _result()]
    )

    status, errors = _run(
        workspace,
        provider,
        settings=SettingsManager.for_workspace(workspace, project_trusted=False),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "stdin-closed" in _events(user_record)
    assert not project_record.exists()


def test_a_trusted_project_adds_its_servers(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    user_record, project_record = _two_servers(tmp_path, config, workspace)
    provider = _Provider(
        [
            lambda: _wait_until(
                lambda: (
                    _received(user_record, "tools/list")
                    and _received(project_record, "tools/list")
                )
            ),
            _result(),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "stdin-closed" in _events(user_record)
    assert "stdin-closed" in _events(project_record)


def test_a_notice_reaches_the_user_at_the_next_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    _extension(workspace, "waiter", _WAITER)
    entry, _record = _server(
        tmp_path / "servers",
        tools=[_SEARCH, _WIPE, {"name": "odd", "inputSchema": {"type": "string"}}],
    )
    _write_mcp_json(config / "mcp.json", {"docs": entry})
    provider = _Provider(
        [
            _result(tool_calls=(_WAIT_CALL,)),
            _result(final_text="first"),
            _result(final_text="second"),
        ]
    )

    status, errors = _run(workspace, provider, "go\nagain\n")

    assert status is HarnessStatus.SUCCEEDED, errors
    assert len(provider.requests) == 3
    assert errors.count('MCP tool "docs/odd" is not available: ') == 1, errors


def test_a_session_without_mcp_json_starts_nothing(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    threads: list[list[str]] = []
    before = set(threading.enumerate())
    provider = _Provider([lambda: threads.append(_mcp_threads(before)), _result()])

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert threads == [[]]
    assert not (config / "mcp.log").exists()
    assert not [name for name in _tool_names(provider.requests[0]) if "mcp__" in name]


def test_a_broken_mcp_json_does_not_stop_the_session(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    config.mkdir()
    (config / "mcp.json").write_text("{not json")
    provider = _Provider([_result(final_text="fine")])

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
