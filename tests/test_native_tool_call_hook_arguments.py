"""What a `tool_call` hook is shown and what it changes (goal check M8 to M10).

Pi ``prepareToolCall`` validates a call's arguments before
``beforeToolCall``; the `tool_call` handlers get the validated arguments as
``event.input``, change them in place, and the tool runs with what they left.
The event carries the call's id and, for a call another tool made, the
calling tool's id.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from test_native_extension_tool_metadata import _result, _StubProvider, _tool_results

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools.registry import production_tool_registry

_EXTENSION = """import json
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolResult

LOG = Path(__file__).with_name('log.jsonl')
_TEXT = {'type': 'object', 'properties': {'text': {'type': 'string'}},
         'required': ['text'], 'additionalProperties': False}


def _log(*row):
    with LOG.open('a') as handle:
        handle.write(json.dumps(list(row)) + '\\n')


def activate(api):
    def echo(ctx, params):
        _log('ran', params['text'])
        return ToolResult(content='echo: ' + params['text'])

    def relay(ctx, params):
        return ToolResult(
            content=ctx.execute_tool('echo', {'text': params['text']}).result.content)

    api.register_tool(ExtensionTool(
        name='echo', description='Echo text.', input_schema=_TEXT, handler=echo))
    api.register_tool(ExtensionTool(
        name='relay', description='Echo through echo.', input_schema=_TEXT,
        handler=relay))

    @api.on('tool_call')
    def change(event, ctx):
        _log('tool_call', event.tool_name, event.tool_call_id,
             event.parent_tool_call_id, dict(event.input))
        if event.input.get('text') == 'shout':
            event.input['text'] = 'SHOUT'

    @api.on('tool_result')
    def seen(event, ctx):
        _log('tool_result', event.tool_name)
"""


def _write(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hooks.py").write_text(_EXTENSION, encoding="utf-8")
    return directory / "log.jsonl"


def _log(path: Path) -> list[list[Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _run(workspace: Path, *calls: ProviderToolCall) -> _StubProvider:
    provider = _StubProvider([_result(tool_calls=calls), _result(final_text="done")])
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=10
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED
    return provider


def test_a_tool_call_hook_changes_the_arguments_the_tool_runs_with(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    log = _write(tmp_path)

    provider = _run(tmp_path, ProviderToolCall("p1", "echo", '{"text": "shout"}'))

    (result,) = _tool_results(provider.requests[1])
    assert result.content.value == "echo: SHOUT"
    assert ["ran", "SHOUT"] in _log(log)
    # The transcript keeps the call the model made.
    assistant = next(
        m
        for m in provider.requests[1].messages
        if isinstance(m, AgentAssistantMessage) and m.tool_calls
    )
    assert json.loads(assistant.tool_calls[0].arguments_json.value) == {"text": "shout"}


def test_a_tool_call_hook_sees_the_call_id_and_the_calling_tools_id(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    log = _write(tmp_path)

    provider = _run(tmp_path, ProviderToolCall("p1", "relay", '{"text": "shout"}'))

    (result,) = _tool_results(provider.requests[1])
    assert result.content.value == "echo: SHOUT"
    assert [row for row in _log(log) if row[0] == "tool_call"] == [
        ["tool_call", "relay", "p1", None, {"text": "shout"}],
        ["tool_call", "echo", "p1/1", "p1", {"text": "SHOUT"}],
    ]


def test_arguments_that_fail_validation_reach_no_hook(tmp_path, monkeypatch) -> None:
    # Pi validates before ``beforeToolCall``, and a call that fails there
    # gets no ``afterToolCall`` either.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    log = _write(tmp_path)

    provider = _run(tmp_path, ProviderToolCall("p1", "echo", '{"text": 3}'))

    (result,) = _tool_results(provider.requests[1])
    assert result.is_error
    assert "text" in result.content.value
    assert _log(log) == []
