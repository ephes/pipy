"""Declared and callable tools in a session (MCP parity plan S03).

Rows EXP-01 to EXP-04, CMD-01, CMD-24 and CMD-26, with extension tools in the
place of Pi's MCP tools: a tool's exposure decides whether a request declares
it and whether a codemode script reaches it, a script reaches what the live
capability value offers when its parent is admitted, and a nested call to an
extension tool passes the hooks and is recorded like a call to a built-in.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from test_native_codemode_delivery import (
    provider as codemode_provider,
)
from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
    settings,
)
from test_native_coding_session import _CollectingAgentEventSink
from test_native_extension_tool_metadata import (
    _result,
    _run,
    _StubProvider,
    _tool_names,
    _tool_results,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ToolCallCompleted,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall

_TOOLS = """import json
from pathlib import Path
from pipy_harness.extensions import (
    ExtensionTool, ToolBlock, ToolResult, ToolResultTransform,
)

CALLS = Path(__file__).with_name('calls.jsonl')
_KEY = {'type': 'object', 'properties': {'key': {'type': 'string'}},
        'required': ['key'], 'additionalProperties': False}


def _record(name, params):
    with CALLS.open('a') as handle:
        handle.write(json.dumps([name, dict(params)]) + '\\n')


def activate(api):
    def lookup(ctx, params):
        _record('lookup', params)
        if params['key'] == 'narrow':
            # Publishes a capability value while the script runs.
            assert ctx.set_active_tools(['codemode'])
        return ToolResult(content='record:' + params['key'])

    def echo(ctx, params):
        _record('echo', params)
        return ToolResult(content='echo:' + params['key'])

    api.register_tool(ExtensionTool(
        name='lookup', description='Look a record up.', input_schema=_KEY,
        handler=lookup, exposure='codemode'))
    api.register_tool(ExtensionTool(
        name='archive', description='Search the archive.', input_schema=_KEY,
        handler=lambda ctx, params: ToolResult(content='archived'),
        exposure='deferred'))
    api.register_tool(ExtensionTool(
        name='echo', description='Echo a key.', input_schema=_KEY, handler=echo))
    api.register_tool(ExtensionTool(
        name='orchestrate', description='Runs other tools.', input_schema=_KEY,
        handler=lambda ctx, params: ToolResult(content='orchestrated'),
        exposure='model-only'))

    @api.on('tool_call')
    def gate(event, ctx):
        if event.tool_name == 'lookup' and event.input.get('key') == 'forbidden':
            return ToolBlock(reason='lookup of forbidden is blocked')
        if event.tool_name == 'codemode' and 'SELECT' in event.input.get('code', ''):
            assert ctx.set_active_tools(['codemode', 'echo'])

    @api.on('tool_result')
    def redact(event, ctx):
        if event.tool_name == 'lookup' and event.content == 'record:secret':
            return ToolResultTransform(content='redacted')
"""


def _write_tools(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tools.py").write_text(_TOOLS, encoding="utf-8")
    return directory / "calls.jsonl"


def _calls(path: Path) -> list[list[object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _run_script(workspace: Path, code: str, monkeypatch):
    sink = _CollectingAgentEventSink()
    fake = codemode_provider(code)
    requests = []
    complete = FakeNativeProvider.complete

    def capture(self, request, **kwargs):
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    session = CodingSession(
        provider=fake, settings_manager=settings(workspace), agent_event_sink=sink
    )
    session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    parent = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    return parent, sink, requests


def _output(parent) -> str:
    return parent.content.value.split("Output:\n", 1)[1].split("\nTool calls", 1)[0]


def test_direct_model_call_to_a_codemode_only_tool_is_an_unknown_tool(
    tmp_path, monkeypatch
) -> None:
    # Pi: "rejects direct model calls to codemode-only MCP tools" (EXP-03).
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    calls = _write_tools(tmp_path)
    provider = _StubProvider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "lookup", '{"key":"x"}'),
                    ProviderToolCall("c2", "archive", '{"key":"x"}'),
                    ProviderToolCall("c3", "echo", '{"key":"x"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    result = _run(tmp_path, provider, "go\n")

    assert result.status is HarnessStatus.SUCCEEDED
    # EXP-02: registration activated the direct and the model-only tool only.
    assert _tool_names(provider.requests[0])[-2:] == ["echo", "orchestrate"]
    assert not {"lookup", "archive"} & set(_tool_names(provider.requests[0]))
    rejected, deferred, direct = _tool_results(provider.requests[1])
    assert rejected.is_error and rejected.content.value == "unknown tool: lookup"
    assert deferred.is_error and deferred.content.value == "unknown tool: archive"
    assert not direct.is_error and direct.content.value == "echo:x"
    assert _calls(calls) == [["echo", {"key": "x"}]]


def test_script_calls_extension_tools_through_the_hooks(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Pi: "routes nested calls through extension hooks" (CMD-01, CMD-24, CMD-26).
    calls = _write_tools(tmp_path)
    code = """found = tools.lookup(key='alpha')
try:
    tools.lookup(key='forbidden')
except ToolError as error:
    blocked = str(error)
try:
    tools.lookup(key=3)
except ToolError as error:
    invalid = str(error)
text({'found': found, 'blocked': blocked, 'invalid': invalid,
      'redacted': tools.lookup(key='secret'), 'archive': tools.archive(key='a'),
      'echo': tools.echo(key='direct')})
"""

    parent, sink, requests = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error
    output = json.loads(_output(parent))
    assert output["found"] == "record:alpha"
    assert "lookup of forbidden is blocked" in output["blocked"]
    # Arguments are validated against the tool's schema, as for a direct call.
    assert "key" in output["invalid"]
    assert output["redacted"] == "redacted"
    assert output["archive"] == "archived"
    assert output["echo"] == "echo:direct"
    # The blocked and the invalid call never reached the handler.
    assert _calls(calls) == [
        ["lookup", {"key": "alpha"}],
        ["lookup", {"key": "secret"}],
        ["echo", {"key": "direct"}],
    ]
    # No request declared the codemode and deferred tools the script called.
    declared = {tool.name for tool in requests[0].available_tools}
    assert {"codemode", "echo", "orchestrate"} <= declared
    assert not {"lookup", "archive"} & declared
    # CMD-26: the parent keeps name, arguments, status and error, no results.
    record = parent.details["nestedCalls"]
    assert record["complete"] is True
    assert [
        (call["id"], call["name"], call["arguments"], call["status"])
        for call in record["calls"]
    ] == [
        ("parent/1", "lookup", {"key": "alpha"}, "ok"),
        ("parent/2", "lookup", {"key": "forbidden"}, "error"),
        ("parent/3", "lookup", {"key": 3}, "error"),
        ("parent/4", "lookup", {"key": "secret"}, "ok"),
        ("parent/5", "archive", {"key": "a"}, "ok"),
        ("parent/6", "echo", {"key": "direct"}, "ok"),
    ]
    assert "lookup of forbidden is blocked" in record["calls"][1]["error"]
    assert all(call["durationMs"] >= 0 for call in record["calls"])
    assert "record:alpha" not in json.dumps(record)
    # CMD-24: every nested event carries the parent id and the id <parent>/<n>.
    started = [e for e in sink.events if isinstance(e, NestedToolCallStarted)]
    completed = [e for e in sink.events if isinstance(e, NestedToolCallCompleted)]
    assert {e.parent_correlation_id for e in started} == {"parent"}
    assert {e.parent_correlation_id for e in completed} == {"parent"}
    assert len(completed) == len(started)
    assert [e.call.provider_correlation_id for e in started] == [
        f"parent/{n}" for n in range(1, 7)
    ]


def test_script_reaches_neither_itself_nor_inactive_or_model_only_tools(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Pi: "does not let codemode call itself or inactive direct tools". The
    # tool_call hook narrows the active tools before the script starts; the
    # callable snapshot is taken after the hooks, from the live value.
    _write_tools(tmp_path)
    code = """# SELECT
missing = []
for name in ('codemode', 'orchestrate', 'read', 'bash'):
    try:
        getattr(tools, name)
    except AttributeError as error:
        missing.append(str(error))
text({'missing': missing, 'echo': tools.echo(key='active')})
"""

    parent, _, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error
    output = json.loads(_output(parent))
    assert output["echo"] == "echo:active"
    assert len(output["missing"]) == 4
    assert all(
        message.endswith("Available tools: tools.archive, tools.echo, tools.lookup.")
        for message in output["missing"]
    )


def test_value_published_while_a_script_runs_does_not_change_its_tools(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    (tmp_path / "alpha.txt").write_text("kept")
    _write_tools(tmp_path)
    # `lookup(key='narrow')` deactivates every direct tool mid-script.
    code = """before = tools.read(path='alpha.txt')
tools.lookup(key='narrow')
text({'before': before, 'after': tools.read(path='alpha.txt'),
      'echo': tools.echo(key='still')})
"""

    parent, _, requests = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error
    output = json.loads(_output(parent))
    assert output["before"] == output["after"] == "kept"
    assert output["echo"] == "echo:still"
    assert [call["status"] for call in parent.details["nestedCalls"]["calls"]] == [
        "ok"
    ] * 4
    # The selection applies from the next request on.
    assert {tool.name for tool in requests[1].available_tools} == {"codemode"}
