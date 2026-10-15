"""Extension tools that run other tools (MCP parity plan S37, EXT-11).

A tool the model called can list the callable tools with ``ctx.tools`` and
run them with ``ctx.execute_tool``. The calls pass the session's hooks and
tool budget like a call the model made, and the parent's result keeps a
bounded record of them.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from test_native_coding_session import _CollectingAgentEventSink
from test_native_extension_tool_metadata import (
    _result,
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
from pipy_harness.native.agent.content import ProductContent, ToolPartialResult
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_calls import (
    NestedToolCallOutcome,
    NestedToolCallService,
    PendingNestedCall,
)
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolExecutor,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.coding.tool_parent_runner import ToolParentRunner
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.base import NESTED_CALLS_UNAVAILABLE
from pipy_harness.native.tools.registry import production_tool_registry

_ORCHESTRATOR = """import json
import threading
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolBlock, ToolResult

LOG = Path(__file__).with_name('log.jsonl')
_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}
_TEXT = {'type': 'object', 'properties': {'text': {'type': 'string'}},
         'required': ['text'], 'additionalProperties': False}


def _log(*row):
    with LOG.open('a') as handle:
        handle.write(json.dumps(list(row)) + '\\n')


def activate(api):
    def run_tools(ctx, params):
        _log('thread', threading.current_thread().name)
        _log('tools', [tool.name for tool in ctx.tools])
        helper = ctx.execute_tool('helper', {})
        echo = ctx.executeTool('echo', {'text': 'hi'})
        own = ctx.execute_tool('run_tools', {})
        blocked = ctx.execute_tool('echo', {'text': 'forbidden'})
        invalid = ctx.execute_tool('echo', {'text': [3]})
        _log('flags', [r.is_error for r in (helper, echo, own, blocked, invalid)])
        _log('errors', blocked.result.content, invalid.result.content)
        _log('calls', [[r.tool_call.id, r.tool_call.name, r.tool_call.arguments,
                        r.duration_ms is not None]
                       for r in (helper, echo, own, blocked, invalid)])
        return ToolResult(content=' | '.join(
            r.result.content for r in (helper, echo, own)))

    def nested(ctx, params):
        _log('nested tools', [tool.name for tool in ctx.tools])
        return ToolResult(content=ctx.execute_tool('echo', {'text': 'x'}).result.content)

    api.register_tool(ExtensionTool(
        name='echo', description='Echo text.', input_schema=_TEXT,
        handler=lambda ctx, params: ToolResult(content='echo: ' + params['text'])))
    api.register_tool(ExtensionTool(
        name='helper', description='Only reachable from other tools.',
        input_schema=_NONE, exposure='codemode',
        handler=lambda ctx, params: ToolResult(
            content='helped', structured_content={'ok': True})))
    api.register_tool(ExtensionTool(
        name='inner', description='Tries to run tools as a nested call.',
        input_schema=_NONE, exposure='codemode', handler=nested))
    api.register_tool(ExtensionTool(
        name='outer', description='Runs inner.', input_schema=_NONE,
        handler=lambda ctx, params: ToolResult(
            content=ctx.execute_tool('inner').result.content)))
    api.register_tool(ExtensionTool(
        name='run_tools', description='Runs tools.', input_schema=_NONE,
        handler=run_tools, exposure='model-only'))

    @api.on('tool_call')
    def gate(event, ctx):
        _log('tool_call', event.tool_name)
        if event.input.get('text') == 'forbidden':
            return ToolBlock(reason='forbidden text')
"""


def _write(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "orchestrator.py").write_text(_ORCHESTRATOR, encoding="utf-8")
    return directory / "log.jsonl"


def _log(path: Path) -> list[list[Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _session(workspace: Path, provider, *, abort_event=None):
    sink = _CollectingAgentEventSink()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=20,
        agent_event_sink=sink,
        abort_event=abort_event,
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return result, sink


@pytest.mark.parametrize("interruptible", [False, True])
def test_a_tool_runs_other_tools_through_the_extension_api(
    tmp_path, monkeypatch, interruptible
) -> None:
    # Pi: "supports tools that call other tools under any name through the
    # extension API" (the calls; the loadout hook is EXT-12).
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    log = _write(tmp_path)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "run_tools", "{}"),)),
            _result(final_text="done"),
        ]
    )

    result, sink = _session(
        tmp_path, provider, abort_event=threading.Event() if interruptible else None
    )

    assert result.status is HarnessStatus.SUCCEEDED
    (parent,) = _tool_results(provider.requests[1])
    assert not parent.is_error
    assert parent.content.value == "helped | echo: hi | Tool run_tools not found"
    rows = _log(log)
    thread = next(row[1] for row in rows if row[0] == "thread")
    # The handler runs on a worker thread also in a session that cannot be
    # interrupted, so the session thread can serve the calls it asks for.
    assert thread == "pipy-tool-call"
    # ctx.tools: the active direct tools and the codemode tools, without the
    # model-only parent itself.
    tools = next(row[1] for row in rows if row[0] == "tools")
    assert {"echo", "helper", "inner", "outer", "read"} <= set(tools)
    assert "run_tools" not in tools
    assert ["flags", [False, False, True, True, True]] in rows
    # The outcome names the call: its id, name, arguments and duration;
    # a call to a name that is not callable was refused before it started.
    calls = next(row[1] for row in rows if row[0] == "calls")
    assert calls == [
        ["p1/1", "helper", {}, True],
        ["p1/2", "echo", {"text": "hi"}, True],
        ["p1/3", "run_tools", {}, False],
        ["p1/4", "echo", {"text": "forbidden"}, True],
        ["p1/5", "echo", {"text": [3]}, True],
    ]
    errors = next(row for row in rows if row[0] == "errors")
    assert "forbidden text" in errors[1]
    assert "text" in errors[2]
    # The hooks saw the parent and every nested call that names a tool,
    # except the one whose arguments failed validation (Pi validates first).
    assert [row[1] for row in rows if row[0] == "tool_call"] == [
        "run_tools",
        "helper",
        "echo",
        "echo",
    ]
    # The parent's result keeps the record: no results, and the call to a
    # tool that is not callable as an error.
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    record = completed.details["nestedCalls"]
    assert [(c["id"], c["name"], c["status"]) for c in record["calls"]] == [
        ("p1/1", "helper", "ok"),
        ("p1/2", "echo", "ok"),
        ("p1/3", "run_tools", "error"),
        ("p1/4", "echo", "error"),
        ("p1/5", "echo", "error"),
    ]
    assert record["calls"][2]["error"] == "Tool run_tools not found"
    assert record["complete"] is True
    assert "helped" not in json.dumps(record)
    started = [e for e in sink.events if isinstance(e, NestedToolCallStarted)]
    ended = [e for e in sink.events if isinstance(e, NestedToolCallCompleted)]
    assert {e.parent_correlation_id for e in started + ended} == {"p1"}
    assert [e.call.tool_name for e in started] == ["helper", "echo", "echo", "echo"]


def test_a_result_without_nested_calls_has_no_record(tmp_path, monkeypatch) -> None:
    # Pi: "leaves results without nested calls unchanged".
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "echo", '{"text":"x"}'),)),
            _result(final_text="done"),
        ]
    )

    result, sink = _session(tmp_path, provider)

    assert result.status is HarnessStatus.SUCCEEDED
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.content.value == "echo: x"
    assert completed.details is None
    assert "run_tools" in _tool_names(provider.requests[0])


def test_a_nested_tool_runs_tools_itself(tmp_path, monkeypatch) -> None:
    # Pi: a nested tool gets `executeTool` too, and its calls are recorded
    # on the model's call with the ids `<parent>/<n>/<m>`.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    log = _write(tmp_path)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "outer", "{}"),)),
            _result(final_text="done"),
        ]
    )

    result, sink = _session(tmp_path, provider)

    assert result.status is HarnessStatus.SUCCEEDED
    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "echo: x"
    nested_tools = next(row[1] for row in _log(log) if row[0] == "nested tools")
    assert {"echo", "inner", "outer"} <= set(nested_tools)
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    record = completed.details["nestedCalls"]
    # In the order the calls began, as Pi's recorder keeps them.
    assert [(c["id"], c["name"], c["status"]) for c in record["calls"]] == [
        ("p1/1", "inner", "ok"),
        ("p1/1/1", "echo", "ok"),
    ]
    started = [e for e in sink.events if isinstance(e, NestedToolCallStarted)]
    assert [
        (e.parent_correlation_id, e.call.provider_correlation_id) for e in started
    ] == [
        ("p1", "p1/1"),
        ("p1/1", "p1/1/1"),
    ]


_SELF = """from pipy_harness.extensions import ExtensionTool, ToolResult

_DEPTH = {'type': 'object', 'properties': {'depth': {'type': 'integer'}},
          'required': ['depth'], 'additionalProperties': False}


def activate(api):
    def countdown(ctx, params):
        depth = params['depth']
        if depth == 0:
            return ToolResult(content='bottom')
        outcome = ctx.execute_tool('countdown', {'depth': depth - 1})
        return ToolResult(content=f'{depth} > {outcome.result.content}')

    api.register_tool(ExtensionTool(
        name='countdown', description='Calls itself.', input_schema=_DEPTH,
        handler=countdown))
"""


@pytest.mark.parametrize("interruptible", [False, True])
def test_a_tool_calls_itself(tmp_path, monkeypatch, interruptible) -> None:
    # Pi's callable tools do not leave the calling tool out.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "countdown.py").write_text(_SELF, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "countdown", '{"depth":3}'),)),
            _result(final_text="done"),
        ]
    )

    result, sink = _session(
        tmp_path, provider, abort_event=threading.Event() if interruptible else None
    )

    assert result.status is HarnessStatus.SUCCEEDED
    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "3 > 2 > 1 > bottom"
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    assert [c["id"] for c in completed.details["nestedCalls"]["calls"]] == [
        "p1/1",
        "p1/1/1",
        "p1/1/1/1",
    ]


_ASYNC_TOOLS = """import asyncio
from pipy_harness.extensions import ExtensionTool, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    async def slow(ctx, params):
        await asyncio.sleep(0)
        return ToolResult(content='awaited', structured_content={'n': 1})

    async def broken(ctx, params):
        await asyncio.sleep(0)
        raise RuntimeError('late failure')

    def calls_slow(ctx, params):
        nested = ctx.execute_tool('slow')
        failed = ctx.execute_tool('broken')
        return ToolResult(content=f'{nested.result.content} / {failed.result.content}')

    api.register_tool(ExtensionTool(
        name='slow', description='Async.', input_schema=_NONE, handler=slow))
    api.register_tool(ExtensionTool(
        name='broken', description='Async, fails.', input_schema=_NONE,
        handler=broken))
    api.register_tool(ExtensionTool(
        name='calls_slow', description='Runs slow.', input_schema=_NONE,
        handler=calls_slow))
"""


@pytest.mark.parametrize("interruptible", [False, True])
def test_an_async_tool_handler_is_awaited(tmp_path, monkeypatch, interruptible) -> None:
    # Pi awaits a tool's `execute`, also when another tool called it.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "async_tools.py").write_text(_ASYNC_TOOLS, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("p1", "slow", "{}"),
                    ProviderToolCall("p2", "calls_slow", "{}"),
                )
            ),
            _result(final_text="done"),
        ]
    )

    result, _ = _session(
        tmp_path, provider, abort_event=threading.Event() if interruptible else None
    )

    assert result.status is HarnessStatus.SUCCEEDED
    direct, parent = _tool_results(provider.requests[1])
    assert (direct.content.value, direct.is_error) == ("awaited", False)
    assert parent.content.value == ("awaited / extension tool error: RuntimeError")


_IMAGES = """from pipy_harness.extensions import (
    ExtensionTool, ImageContent, TextContent, ToolResult)

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    def shot(ctx, params):
        return ToolResult(content='ignored', blocks=(
            TextContent('before'), ImageContent('aGk=', 'image/png'),
            TextContent('after')))

    def wrap(ctx, params):
        outcome = ctx.execute_tool('shot')
        return ToolResult(content='', blocks=outcome.result.blocks)

    def too_long(ctx, params):
        return ToolResult(content='', blocks=(
            TextContent('x' * 70000), ImageContent('aGk=', 'image/png'),
            TextContent('dropped')))

    def fits(ctx, params):
        return ToolResult(content='', blocks=(
            TextContent('y' * (60 * 1024 - 2)), ImageContent('aGk=', 'image/png'),
            TextContent('z')))

    def bad(ctx, params):
        return ToolResult(content='', blocks=({'type': 'text', 'text': 'x'},))

    # A tool_result handler that changes nothing: the hooks' own bound must
    # not cost a result its images.
    api.on('tool_result', lambda event, ctx: None)

    api.register_tool(ExtensionTool(
        name='shot', description='Image.', input_schema=_NONE,
        handler=shot, exposure='codemode'))
    api.register_tool(ExtensionTool(
        name='wrap', description='Wraps shot.', input_schema=_NONE, handler=wrap))
    api.register_tool(ExtensionTool(
        name='too_long', description='Long.', input_schema=_NONE,
        handler=too_long))
    api.register_tool(ExtensionTool(
        name='fits', description='Fits.', input_schema=_NONE, handler=fits))
    api.register_tool(ExtensionTool(
        name='bad', description='Bad blocks.', input_schema=_NONE, handler=bad))
"""


def test_a_nested_result_keeps_its_images(tmp_path, monkeypatch) -> None:
    # Pi: `executeTool` returns the result with its content blocks, and a
    # tool can return them as its own result.
    from pipy_harness.native.agent.content import ImageContent, TextContent

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "images.py").write_text(_IMAGES, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("p1", "wrap", "{}"),
                    ProviderToolCall("p2", "too_long", "{}"),
                    ProviderToolCall("p3", "bad", "{}"),
                    ProviderToolCall("p4", "fits", "{}"),
                )
            ),
            _result(final_text="done"),
        ]
    )

    result, _ = _session(tmp_path, provider)

    assert result.status is HarnessStatus.SUCCEEDED
    wrapped, long, bad, fits = _tool_results(provider.requests[1])
    assert wrapped.blocks == (
        TextContent("before"),
        ImageContent("aGk=", "image/png"),
        TextContent("after"),
    )
    assert wrapped.content.value == "before\nafter"
    # The text is bounded; the image is kept whole and in order.
    assert [type(block) for block in long.blocks] == [TextContent, ImageContent]
    assert long.content.value.endswith("[pipy: extension tool output truncated]")
    assert len(long.content.value) <= 60 * 1024
    # Text within the bound is not touched.
    assert [type(block) for block in fits.blocks] == [
        TextContent,
        ImageContent,
        TextContent,
    ]
    assert fits.content.value == "y" * (60 * 1024 - 2) + "\nz"
    assert (bad.content.value, bad.is_error) == (
        "extension tool error: invalid result blocks",
        True,
    )


_SIGNAL = """import threading
from pipy_harness.extensions import ExtensionTool, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    def watch(ctx, params):
        chunks = []
        outcome = ctx.executeTool(
            'bash', {'command': 'printf one; sleep 0.3; printf two'},
            {'onUpdate': chunks.append})
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        stopped = ctx.execute_tool(
            'bash', {'command': 'sleep 20'}, signal=stop)
        return ToolResult(content=' | '.join([
            outcome.result.content.strip(),
            '/'.join(c.content for c in chunks
                     if isinstance(c, ToolResult)),
            stopped.result.content, str(ctx.signal is not None)]))

    api.register_tool(ExtensionTool(
        name='watch', description='Watches bash.', input_schema=_NONE,
        handler=watch))
"""


def test_a_nested_call_has_a_signal_and_updates(tmp_path, monkeypatch) -> None:
    # Pi ExecuteToolOptions: `signal` cancels the nested call alone, and
    # `onUpdate` receives its partial results, which the session also emits
    # as parent-linked update events.
    from pipy_harness.native.agent import NestedToolCallUpdated

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "signal.py").write_text(_SIGNAL, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "watch", "{}"),)),
            _result(final_text="done"),
        ]
    )

    result, sink = _session(tmp_path, provider, abort_event=threading.Event())

    assert result.status is HarnessStatus.SUCCEEDED
    (parent,) = _tool_results(provider.requests[1])
    output, chunks, stopped, has_signal = parent.content.value.split(" | ")
    assert "one" in output and "two" in output
    # Pi's `bash` partial result is the output so far, not the next chunk.
    partials = chunks.split("/")
    assert partials[0] == "one" and partials[-1] == "onetwo"
    assert stopped == "tool cancelled by its caller"
    assert has_signal == "True"
    updates = [e for e in sink.events if isinstance(e, NestedToolCallUpdated)]
    assert updates and {e.call.provider_correlation_id for e in updates} <= {
        "p1/1",
        "p1/2",
    }
    assert {e.parent_correlation_id for e in updates} == {"p1"}
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    statuses = [c["status"] for c in completed.details["nestedCalls"]["calls"]]
    assert statuses == ["ok", "cancelled"]


# -- the bridge between a handler's thread and the session thread -----------


def _message(call: AgentToolCall, text: str, *, error: bool = False):
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-test",
        tool_name=call.tool_name,
        content=ProductContent(text),
        is_error=error,
        provider_correlation_id=call.provider_correlation_id,
    )


def _service(settle, names=("child",)) -> NestedToolCallService:
    parent = AgentToolCall("p", "parent", ProductContent("{}"))
    tools = tuple(
        ToolDefinition(name=name, description=name, input_schema={"type": "object"})
        for name in names
    )
    return NestedToolCallService(
        parent,
        frozenset(names),
        settle,
        lambda call, text: _message(call, text, error=True),
        callable_tools=tools,
    )


class _Parent:
    """A tool whose handler is the test's function of ``nested_tools``."""

    definition = ToolDefinition(
        name="parent", description="parent", input_schema={"type": "object"}
    )

    def __init__(self, handler) -> None:
        self._handler = handler

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=self._handler(context),
            provider_correlation_id=request.provider_correlation_id,
        )


def _run_parent(handler, service, waiter) -> ToolExecutionOutcome:
    executor = ToolExecutor({"parent": _Parent(handler)}, 0.2)

    def execute_parent(
        call, /, *, wait_for_interrupt, nested_tools, arguments, output_sink=None
    ):
        return executor.execute(
            call,
            ToolContext(
                workspace_root=Path.cwd(),
                nested_tools=nested_tools,
                output_sink=output_sink,
            ),
            wait_for_interrupt,
            arguments=arguments,
        )

    return ToolParentRunner(execute_parent, {}).execute(
        AgentToolCall("p", "parent", ProductContent("{}")), service, waiter
    )


def _wait(done_event, cancel_event):
    done_event.wait()
    return ToolExecutionInterruption.SETTLED


def test_nested_calls_of_a_worker_thread_run_on_the_session_thread() -> None:
    threads: list[threading.Thread] = []

    def settle(call):
        threads.append(threading.current_thread())
        return NestedToolCallOutcome(
            call, _message(call, "ran " + call.arguments_json.value), _STATUS
        )

    def handler(context):
        nested = context.nested_tools
        assert [tool.name for tool in nested.tools()] == ["child"]
        first = nested.execute("child", {"n": 1})
        second = nested.execute("child", None)
        bad = nested.execute("child", [1])
        return f"{first.content}; {second.content}; {bad.content}"

    outcome = _run_parent(handler, _service(settle), _wait)

    assert outcome.result.content.value == (
        'ran {"n": 1}; ran {}; Tool child arguments must be an object'
    )
    assert threads == [threading.current_thread()] * 2


_STATUS = NestedCallStatus.SETTLED


def test_an_interrupted_nested_call_ends_the_parent() -> None:
    release = threading.Event()

    def settle(call):
        return NestedToolCallOutcome(
            call,
            _message(call, "tool cancelled by escape", error=True),
            NestedCallStatus.INTERRUPTED,
            ToolExecutionInterruption.OPERATOR_ABORT,
        )

    seen: list[str] = []

    def handler(context):
        seen.append(context.nested_tools.execute("child", {}).content)
        # The parent was cancelled; a handler that goes on gets no more calls.
        release.wait(5)
        seen.append(context.nested_tools.execute("child", {}).content)
        return "late"

    service = _service(settle)
    outcome = _run_parent(handler, service, _wait)
    release.set()

    assert outcome.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert outcome.cancelled and outcome.result.is_error
    assert service.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert seen[0] == "tool cancelled by escape"


def test_an_interruption_of_the_parent_refuses_its_waiting_calls() -> None:
    asked = threading.Event()
    results: list[str] = []

    def handler(context):
        def late() -> None:
            asked.wait(5)
            results.append(context.nested_tools.execute("child", {}).content)

        thread = threading.Thread(target=late)
        thread.start()
        thread.join(5)
        return "done"

    def waiter(done_event, cancel_event):
        # The session is interrupted before any call was asked for.
        return ToolExecutionInterruption.OPERATOR_ABORT

    outcome = _run_parent(handler, _service(lambda call: None), waiter)
    asked.set()

    assert outcome.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    deadline = threading.Event()
    for _ in range(50):
        if results:
            break
        deadline.wait(0.05)
    assert results == [NESTED_CALLS_UNAVAILABLE]


def test_a_failing_pipeline_releases_the_handler_and_is_raised() -> None:
    def settle(call):
        raise RuntimeError("pipeline broke")

    seen: list[str] = []

    def handler(context):
        seen.append(context.nested_tools.execute("child", {}).content)
        return "done"

    service = _service(settle)
    with pytest.raises(RuntimeError, match="pipeline broke"):
        _run_parent(handler, service, _wait)

    assert seen == [NESTED_CALLS_UNAVAILABLE]
    with pytest.raises(RuntimeError, match="pipeline broke"):
        service.raise_pipeline_failure()


def _running_settle(execute):
    """A pipeline whose calls run: ``execute(call)`` is the tool, on the
    thread the service gives it; the outcome is settled on the session
    thread."""
    session = threading.current_thread()

    def settle(call):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            return ToolExecutionOutcome(_message(call, execute(call, nested_tools)))

        def finish(execution):
            assert threading.current_thread() is session
            return NestedToolCallOutcome(call, execution.result, _STATUS)

        return PendingNestedCall(call, run, finish)

    return settle


def test_calls_from_several_threads_run_at_the_same_time() -> None:
    # Pi runs nested calls concurrently unless they must be sequential.
    together = threading.Barrier(4, timeout=5)

    def execute(call, nested_tools):
        together.wait()
        return "ok"

    def handler(context):
        results: list[str] = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    context.nested_tools.execute("child", {}).content
                )
            )
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        return ",".join(results)

    service = _service(_running_settle(execute))
    outcome = _run_parent(handler, service, _wait)

    assert outcome.result.content.value == ",".join(["ok"] * 4)
    assert sorted(r.nested_call.provider_correlation_id for r in service.records()) == [
        "p/1",
        "p/2",
        "p/3",
        "p/4",
    ]


def test_a_running_nested_call_is_cancelled_when_its_parent_ends() -> None:
    entered = threading.Event()
    cancelled: list[bool] = []

    def settle(call):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            done = threading.Event()
            cancel = threading.Event()

            def tool() -> None:
                entered.set()
                cancelled.append(cancel.wait(5))
                done.set()

            threading.Thread(target=tool).start()
            reason = wait_for_interrupt(done, cancel)
            cancel.set()
            done.wait(5)
            return ToolExecutionOutcome(
                _message(call, f"stopped: {reason.value}", error=True),
                cancelled=True,
            )

        def finish(execution):
            return NestedToolCallOutcome(
                call, execution.result, NestedCallStatus.CANCELLED
            )

        return PendingNestedCall(call, run, finish)

    late: list[str] = []

    def handler(context):
        def call() -> None:
            late.append(context.nested_tools.execute("child", {}).content)

        threading.Thread(target=call).start()
        entered.wait(5)
        return "returned"

    service = _service(settle)
    outcome = _run_parent(handler, service, _wait)

    assert outcome.result.content.value == "returned"
    assert cancelled == [True]
    (record,) = service.records()
    assert record.status is NestedCallStatus.CANCELLED
    for _ in range(100):
        if late:
            break
        threading.Event().wait(0.02)
    assert late == ["stopped: caller_stop"]


def test_without_a_waiter_the_handler_runs_on_a_worker_and_is_served() -> None:
    # A session that cannot be interrupted still serves calls from the
    # threads a handler starts.
    def handler(context):
        results: list[str] = []
        thread = threading.Thread(
            target=lambda: results.append(
                context.nested_tools.execute("child", {}).content
            )
        )
        thread.start()
        thread.join(5)
        own = context.nested_tools.execute("child", {}).content
        return f"{threading.current_thread().name}: {results[0]} / {own}"

    service = _service(_running_settle(lambda call, nested_tools: "ok"))
    outcome = _run_parent(handler, service, None)

    assert outcome.result.content.value == "pipy-tool-call: ok / ok"
    assert len(service.records()) == 2


def test_a_nested_call_runs_tools_through_the_same_service() -> None:
    def execute(call, nested_tools):
        if call.tool_name == "child":
            return "child > " + nested_tools.execute("leaf", {}).content
        return "leaf"

    service = _service(_running_settle(execute), names=("child", "leaf"))
    outcome = _run_parent(
        lambda context: context.nested_tools.execute("child", {}).content,
        service,
        _wait,
    )

    assert outcome.result.content.value == "child > leaf"
    assert [r.nested_call.provider_correlation_id for r in service.records()] == [
        "p/1",
        "p/1/1",
    ]


def test_a_call_waiting_for_readiness_ends_with_its_parent() -> None:
    # The readiness wait (MCP servers still connecting) of a requested call
    # ends when the tool the session waits for ended; the call does not run.
    waiting = threading.Event()
    ran: list[str] = []

    def settle(call, waiter=None):
        assert waiter is not None
        waiting.set()
        never = threading.Event()
        reason = waiter(never, threading.Event())

        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            ran.append(call.provider_correlation_id)
            return ToolExecutionOutcome(_message(call, "ran"))

        def finish(execution):
            status = NestedCallStatus.CANCELLED if execution.cancelled else _STATUS
            return NestedToolCallOutcome(call, execution.result, status)

        if reason is not ToolExecutionInterruption.SETTLED:
            return finish(
                ToolExecutionOutcome(
                    _message(call, f"not ready: {reason.value}", error=True),
                    cancelled=True,
                )
            )
        return PendingNestedCall(call, run, finish)

    late: list[str] = []

    def handler(context):
        threading.Thread(
            target=lambda: late.append(
                context.nested_tools.execute("child", {}).content
            )
        ).start()
        waiting.wait(5)
        return "returned"

    parent = AgentToolCall("p", "parent", ProductContent("{}"))
    service = NestedToolCallService(
        parent,
        frozenset({"child"}),
        lambda call: settle(call),
        lambda call, text: _message(call, text, error=True),
        settle_with_waiter=settle,
        callable_tools=(
            ToolDefinition(
                name="child", description="c", input_schema={"type": "object"}
            ),
        ),
    )
    outcome = _run_parent(handler, service, _wait)

    assert outcome.result.content.value == "returned"
    assert ran == []
    (record,) = service.records()
    assert record.status is NestedCallStatus.CANCELLED
    # The helper thread records its answer after the parent returned.
    for _ in range(100):
        if late:
            break
        threading.Event().wait(0.02)
    assert late == ["not ready: caller_stop"]


def test_a_call_waiting_for_readiness_ends_with_its_own_caller() -> None:
    # p -> p/1 -> p/1/1: the nested caller p/1 returns while its call waits
    # for readiness; the wait ends, p/1/1 does not run, and p gets p/1's
    # result.
    waiting = threading.Event()
    ran: list[str] = []
    late: list[str] = []

    def settle(call, waiter=None):
        assert waiter is not None

        def finish(execution):
            status = NestedCallStatus.CANCELLED if execution.cancelled else _STATUS
            return NestedToolCallOutcome(call, execution.result, status)

        if call.tool_name == "leaf":
            waiting.set()
            reason = waiter(threading.Event(), threading.Event())
            if reason is not ToolExecutionInterruption.SETTLED:
                return finish(
                    ToolExecutionOutcome(
                        _message(call, f"not ready: {reason.value}", error=True),
                        cancelled=True,
                    )
                )

        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            ran.append(call.provider_correlation_id)
            if call.tool_name == "child":
                threading.Thread(
                    target=lambda: late.append(nested_tools.execute("leaf", {}).content)
                ).start()
                waiting.wait(5)
            return ToolExecutionOutcome(_message(call, f"{call.tool_name} done"))

        return PendingNestedCall(call, run, finish)

    parent = AgentToolCall("p", "parent", ProductContent("{}"))
    service = NestedToolCallService(
        parent,
        frozenset({"child", "leaf"}),
        lambda call: settle(call),
        lambda call, text: _message(call, text, error=True),
        settle_with_waiter=settle,
        callable_tools=tuple(
            ToolDefinition(name=name, description=name, input_schema={"type": "object"})
            for name in ("child", "leaf")
        ),
    )
    outcome = _run_parent(
        lambda context: context.nested_tools.execute("child", {}).content,
        service,
        _wait,
    )

    assert outcome.result.content.value == "child done"
    assert ran == ["p/1"]
    statuses = {
        r.nested_call.provider_correlation_id: r.status for r in service.records()
    }
    assert statuses == {"p/1/1": NestedCallStatus.CANCELLED, "p/1": _STATUS}
    for _ in range(100):
        if late:
            break
        threading.Event().wait(0.02)
    assert late == ["not ready: caller_stop"]


def _waiting_service(settle):
    """A service whose pipeline has the waiter variant, as the loop's."""
    parent = AgentToolCall("p", "parent", ProductContent("{}"))
    return NestedToolCallService(
        parent,
        frozenset({"child"}),
        lambda call: settle(call),
        lambda call, text: _message(call, text, error=True),
        settle_with_waiter=settle,
        callable_tools=(
            ToolDefinition(
                name="child", description="c", input_schema={"type": "object"}
            ),
        ),
    )


def test_a_signal_ends_the_readiness_wait_of_a_call() -> None:
    ran: list[str] = []
    stop = threading.Event()

    def settle(call, waiter=None):
        def finish(execution):
            status = NestedCallStatus.CANCELLED if execution.cancelled else _STATUS
            return NestedToolCallOutcome(call, execution.result, status)

        assert waiter is not None
        threading.Timer(0.05, stop.set).start()
        reason = waiter(threading.Event(), threading.Event())
        if reason is not ToolExecutionInterruption.SETTLED:
            return finish(
                ToolExecutionOutcome(
                    _message(call, f"not ready: {reason.value}", error=True),
                    cancelled=True,
                )
            )

        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            ran.append(call.provider_correlation_id)
            return ToolExecutionOutcome(_message(call, "ran"))

        return PendingNestedCall(call, run, finish)

    outcome = _waiting_service(settle).call("child", "{}", signal=stop)

    assert outcome.result.content.value == "not ready: caller_stop"
    assert outcome.status is NestedCallStatus.CANCELLED
    assert ran == []


def test_live_output_of_another_thread_is_published_on_the_session_thread() -> None:
    session = threading.current_thread()
    published: list[object] = []
    received: list[ToolPartialResult] = []
    partial = ToolPartialResult("half", {"server": "s", "tool": "t"})

    def settle(call, waiter=None):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            done = threading.Event()

            def tool() -> None:
                assert output_sink is not None
                output_sink("progress")
                output_sink(partial)
                done.set()

            threading.Thread(target=tool).start()
            wait_for_interrupt(done, threading.Event())
            return ToolExecutionOutcome(_message(call, "ran"))

        def update(chunk):
            assert threading.current_thread() is session
            published.append(chunk)

        return PendingNestedCall(
            call,
            run,
            lambda execution: NestedToolCallOutcome(call, execution.result, _STATUS),
            update,
        )

    # No waiter at all: the call still runs under the service's own wait.
    outcome = _waiting_service(settle).call("child", "{}", on_update=received.append)

    assert outcome.result.content.value == "ran"
    # The pipeline publishes what the tool wrote; the caller's on_update gets
    # a partial result for each, the chunk as its text (Pi onUpdate).
    assert published == ["progress", partial]
    assert received == [ToolPartialResult("progress"), partial]


def test_a_failure_to_publish_live_output_is_the_pipelines() -> None:
    class SinkFailure(Exception):
        pass

    def settle(call, waiter=None):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            done = threading.Event()

            def tool() -> None:
                assert output_sink is not None
                output_sink("progress")
                done.set()

            threading.Thread(target=tool).start()
            wait_for_interrupt(done, threading.Event())
            return ToolExecutionOutcome(_message(call, "ran"))

        def update(chunk):
            raise SinkFailure(chunk)

        return PendingNestedCall(
            call,
            run,
            lambda execution: NestedToolCallOutcome(call, execution.result, _STATUS),
            update,
        )

    service = _waiting_service(settle)
    with pytest.raises(SinkFailure):
        service.call("child", "{}")

    # The runner cannot absorb it: the failure is kept, and the call is
    # recorded once as unfinished.
    with pytest.raises(SinkFailure):
        service.raise_pipeline_failure()
    assert [r.status for r in service.records()] == [NestedCallStatus.UNFINISHED]


def test_a_failure_to_publish_queued_output_is_kept() -> None:
    # The chunk of a call running on its own thread is published by serve;
    # when that raises, the runner must not be able to absorb it.
    class SinkFailure(Exception):
        pass

    def settle(call):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            assert output_sink is not None
            output_sink("progress")
            return ToolExecutionOutcome(_message(call, "ran"))

        def update(chunk):
            raise SinkFailure(chunk)

        return PendingNestedCall(
            call,
            run,
            lambda execution: NestedToolCallOutcome(call, execution.result, _STATUS),
            update,
        )

    def handler(context):
        return context.nested_tools.execute("child", {}).content

    service = _service(settle)
    with contextlib.suppress(BaseException):
        _run_parent(handler, service, _wait)

    with pytest.raises(SinkFailure):
        service.raise_pipeline_failure()
    assert [
        (r.nested_call.provider_correlation_id, r.status) for r in service.records()
    ] == [("p/1", NestedCallStatus.UNFINISHED)]


def test_calls_of_an_ended_caller_are_refused() -> None:
    service = _service(lambda call: None)
    tools = service.nested_tools("p")
    service.end_caller("p")

    result = tools.execute("child", {})

    assert (result.content, result.tool_call_id) == (NESTED_CALLS_UNAVAILABLE, "p/0")
    assert service.records() == ()


def test_a_refused_unknown_name_is_recorded_only_when_asked() -> None:
    service = _service(lambda call: None)

    silent = service.call("missing", "{}")
    named = service.call("missing", "{}", unknown_tool_error="Tool missing not found")

    assert silent.status is named.status is NestedCallStatus.REFUSED
    assert silent.result.content.value == "nested call refused"
    calls: Any = service.durable_record()["calls"]
    assert [(c["id"], c["status"], c["error"]) for c in calls] == [
        ("p/2", "error", "Tool missing not found")
    ]
    assert service.has_record()
    assert not _service(lambda call: None).has_record()
