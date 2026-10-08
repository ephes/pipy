"""T7a canonical lifecycle through projections and the actual transcript owner."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from test_native_agent_event_adapters import (
    _AutomationCollectingSink,
    _RecordingRenderer,
)
from test_native_agent_loop import _run_input, _tool_result
from test_native_agent_nested_calls import _NestedTools, _Runner, _setup
from test_native_tool_rows import _Live, _plain

from pipy_harness.native.agent import (
    AgentEvent,
    AgentToolCall,
    AgentTranscriptMessage,
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ProductContent,
    ToolCallCompleted,
    ToolCallStarted,
)
from pipy_harness.native.agent.nested_calls import NestedToolCallService
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.agent_adapters import (
    NativeProductSessionActionSink,
    ProductSessionEventProjection,
    SdkAgentEventAdapter,
    SynchronousAgentEventComposite,
    WorkflowArchiveAgentEventAdapter,
)
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.frame_renderer import FrameBlock, block_lines
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tool_rows import NestedToolLine
from pipy_harness.native.ui.rendering import RenderingAgentEventAdapter
from pipy_harness.native.ui.state import RenderNestedToolCall, UiState, reduce


def _child(parent: str = "parent", n: int = 1, args: str = "{}") -> AgentToolCall:
    return AgentToolCall(f"{parent}/{n}", "read", ProductContent(args))


def _end(
    call: AgentToolCall,
    status: NestedCallStatus = NestedCallStatus.SETTLED,
    *,
    error: bool = False,
) -> NestedToolCallCompleted:
    return NestedToolCallCompleted(
        0,
        call.provider_correlation_id.rsplit("/", 1)[0],
        call,
        _tool_result(call, "result\x1b[31m\x07", is_error=error),
        status,
        0.125,
    )


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan"), True, None, "1"])
def test_invalid_duration(duration: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_end(_child()), duration_seconds=duration)


@pytest.mark.parametrize(
    "field,value",
    [
        ("turn_index", -1),
        ("parent_correlation_id", "other"),
        ("status", "settled"),
        ("status", NestedCallStatus.REFUSED),
        ("status", NestedCallStatus.CANCELLED),
        ("result", _tool_result(_child("other"))),
    ],
)
def test_invalid_completion(field: str, value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_end(_child()), **{field: value})


def test_nested_reducer_and_legacy_renderer() -> None:
    renderer = _RecordingRenderer()
    adapter = RenderingAgentEventAdapter(renderer)
    for event in (NestedToolCallStarted(0, "parent", _child()), _end(_child())):
        state = UiState()
        updated, decisions = reduce(state, event)
        assert updated is state and decisions == (RenderNestedToolCall(event),)
        adapter.emit(event)
    assert renderer.actions == []


@pytest.mark.parametrize(
    "interruption",
    [
        None,
        ToolExecutionInterruption.OPERATOR_ABORT,
        ToolExecutionInterruption.LOCAL_COMMAND,
    ],
)
def test_loop_to_live_transcript_and_all_projections(
    tmp_path: Path, interruption: ToolExecutionInterruption | None
) -> None:
    live = _Live(tmp_path)
    adapter = RenderingAgentEventAdapter(live.renderer)
    automation = _AutomationCollectingSink()
    workflow = WorkflowArchiveAgentEventAdapter()
    messages: list[AgentTranscriptMessage] = []
    persistence = ProductSessionEventProjection(
        NativeProductSessionActionSink(messages.append)
    )
    sdk = SdkAgentEventAdapter()
    sink = SynchronousAgentEventComposite(
        (adapter, AutomationAgentEventAdapter(automation), persistence, workflow, sdk)
    )

    def body(
        parent: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        assert live.transcript.pending_tool is not None
        first = service.call("read", "{}")
        assert first.result.content.value.endswith(" transformed")
        assert "✓ read {}" in "\n".join(_plain(live.transcript.pending_tool_lines))
        if interruption is None:
            service.call("write", "{}")  # blocked, ordinary error
            assert "✗ write {}" in "\n".join(_plain(live.transcript.pending_tool_lines))
        else:
            service.call("read", "{}")
            assert "⊘ read {}" in "\n".join(_plain(live.transcript.pending_tool_lines))
        assert live.rows() == []
        return ToolExecutionOutcome(_tool_result(parent))

    tools = _NestedTools(
        [],
        [
            ToolExecutionOutcome(_tool_result(_child("parent0", 1))),
            ToolExecutionOutcome(
                _tool_result(_child("parent0", 2), is_error=True),
                interruption=interruption or ToolExecutionInterruption.SETTLED,
            ),
        ],
    )
    loop, _, events, _ = _setup(_Runner(body), tools=tools, event_sink=sink)
    outcome = loop.run(_run_input())
    lifecycle = [
        e
        for e in events.events
        if isinstance(
            e,
            (
                ToolCallStarted,
                ToolCallCompleted,
                NestedToolCallStarted,
                NestedToolCallCompleted,
            ),
        )
    ]
    assert [type(e) for e in lifecycle] == [
        ToolCallStarted,
        NestedToolCallStarted,
        NestedToolCallCompleted,
        NestedToolCallStarted,
        NestedToolCallCompleted,
        ToolCallCompleted,
    ]
    assert len(live.rows()) == 1 and live.transcript.pending_tool is None
    assert (
        sum(
            isinstance(message, type(_tool_result(_child())))
            for message in outcome.result.messages
        )
        == 1
    )
    assert (
        sum(isinstance(message, type(_tool_result(_child()))) for message in messages)
        == 1
    )
    assert (
        workflow.counts().tool_calls_started
        == workflow.counts().tool_calls_completed
        == 1
    )
    assert (
        workflow.counts().nested_calls_started
        == workflow.counts().nested_calls_completed
        == 2
    )
    assert all(type(v) is int for v in workflow._counts)
    assert "read" not in repr(workflow.counts())
    assert sdk.result is outcome.result
    children = [e for e in automation.events if "parentToolCallId" in e]
    assert [e["type"] for e in children] == [
        "tool_execution_start",
        "tool_execution_end",
    ] * 2
    assert all(e["parentToolCallId"] == "parent0" for e in children)
    assert children[0] == {
        "type": "tool_execution_start",
        "toolCallId": "parent0/1",
        "toolName": "read",
        "args": {},
        "parentToolCallId": "parent0",
    }
    child_text = children[1]["result"]
    assert isinstance(child_text, str)
    assert child_text.endswith(" transformed") and children[1]["isError"] is False
    live.width = 19
    live.transcript.refresh_tool_rows()
    live.transcript.set_tools_expanded(True)
    live.transcript.set_tools_expanded(False)
    assert len(live.rows()) == 1
    assert sum("✓ read" in line for line in live.rows()[0]) == 1


def test_live_status_args_bounds_sanitize_and_parent_mismatch(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    adapter = RenderingAgentEventAdapter(live.renderer)
    parent = AgentToolCall("parent", "codemode", ProductContent("{}"))
    adapter.emit(ToolCallStarted(0, parent))
    adapter.emit(NestedToolCallStarted(0, "other", _child("other")))
    state = live.transcript.pending_tool
    assert state is not None and len(state.nested_lines) == 0
    for n in range(1, 258):
        call = _child(n=n, args="\x1b[31m\x07\x9b2J" + "é" * 100000)
        adapter.emit(NestedToolCallStarted(0, "parent", call))
        if n == 1:
            pending_text = "\n".join(_plain(live.transcript.pending_tool_lines))
            assert "… read " + "é" * 77 + "..." in pending_text
        status = [
            NestedCallStatus.SETTLED,
            NestedCallStatus.SETTLED,
            NestedCallStatus.CANCELLED,
        ][(n - 1) % 3]
        adapter.emit(_end(call, status, error=n % 3 != 1))
    retained: list[NestedToolLine] = list(state.nested_lines)
    assert len(retained) == 256
    assert sum(len(line.arguments.encode()) for line in retained) <= 32768
    assert all(
        len(line.arguments.encode()) <= 8192 and len(line.error) <= 500
        for line in retained
    )
    assert all(
        not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in line.arguments + line.error)
        for line in retained
    )
    assert [line.status for line in retained[:3]] == [
        "ok",
        "error",
        "cancelled",
    ]
    assert all(line.duration_seconds == 0.125 for line in retained)
    live.transcript.set_tools_expanded(True)
    text = "\n".join(_plain(live.transcript.pending_tool_lines))
    assert (
        "✓ read" in text and "✗ read" in text and "⊘ read" in text and "125ms" in text
    )
    live.transcript.set_tools_expanded(False)
    assert "earlier calls" in "\n".join(_plain(live.transcript.pending_tool_lines))
    adapter.emit(ToolCallCompleted(0, _tool_result(parent)))
    assert len(live.rows()) == 1
    settled = live.rows()
    adapter.emit(_end(_child()))  # late completion cannot alter a committed row
    assert live.rows() == settled
    live.call("codemode", {})
    adapter.emit(NestedToolCallStarted(0, "parent", _child(n=300)))
    assert (
        live.transcript.pending_tool is not None
        and live.transcript.pending_tool.nested_lines == ()
    )


def test_automation_unknown_rejected_and_plain_children_indented() -> None:
    import io

    sink = _AutomationCollectingSink()
    adapter = AutomationAgentEventAdapter(sink)
    with pytest.raises(TypeError):
        adapter.emit(cast(AgentEvent, object()))
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    renderer.render_tool_call(AgentToolCall("parent", "codemode", ProductContent("{}")))
    renderer.render_nested_tool_call(NestedToolCallStarted(0, "parent", _child()))
    renderer.render_nested_tool_call(
        _end(_child(), NestedCallStatus.CANCELLED, error=True)
    )
    assert (
        "  … read {}" in stream.getvalue() and "  ⊘ read {} 125ms" in stream.getvalue()
    )
    renderer.render_tool_result(output_text="parent", is_error=True)
    before = stream.getvalue()
    renderer.render_nested_tool_call(_end(_child()))
    assert stream.getvalue() == before


def test_expanded_child_error_paints_on_its_own_row(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    adapter = RenderingAgentEventAdapter(live.renderer)
    parent = AgentToolCall("parent", "codemode", ProductContent("{}"))
    child = _child()
    adapter.emit(ToolCallStarted(0, parent))
    adapter.emit(NestedToolCallStarted(0, "parent", child))
    adapter.emit(
        replace(
            _end(child, error=True),
            result=_tool_result(child, "unique child error", is_error=True),
        )
    )
    for settled in (False, True):
        if settled:
            adapter.emit(ToolCallCompleted(0, _tool_result(parent)))
        live.transcript.set_tools_expanded(True)
        lines = (
            live.transcript.history_blocks[-1][1]
            if settled
            else live.transcript.pending_tool_lines
        )
        painted = [row.text for row in block_lines(FrameBlock("tool_box", lines), 80)]
        index = next(i for i, row in enumerate(painted) if "✗ read {} 125ms" in row)
        assert "unique child error" not in painted[index]
        assert painted[index + 1] == "     unique child error"
        live.transcript.set_tools_expanded(False)
        lines = (
            live.transcript.history_blocks[-1][1]
            if settled
            else live.transcript.pending_tool_lines
        )
        assert all(
            "unique child error" not in row.text
            for row in block_lines(FrameBlock("tool_box", lines), 80)
        )


@pytest.mark.parametrize("fail_on", [NestedToolCallStarted, NestedToolCallCompleted])
def test_nested_sink_failure_preserves_canonical_failure(fail_on: type) -> None:
    class FailingSink:
        def emit(self, event: AgentEvent) -> None:
            if isinstance(event, fail_on):
                raise RuntimeError("canonical sink failed")

    def body(
        parent: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        try:
            service.call("read", "{}")
        except RuntimeError:
            pass  # Runner cannot normalize a canonical pipeline exception.
        return ToolExecutionOutcome(_tool_result(parent))

    runner = _Runner(body)
    loop, _, _, _ = _setup(runner, event_sink=FailingSink())
    with pytest.raises(RuntimeError, match="canonical sink failed"):
        loop.run(_run_input())
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


def test_all_accepted_policy_outcomes_have_ordered_nested_pairs() -> None:
    def body(
        parent: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        for name, args in [
            ("read", "{}"),
            ("read", "{}"),
            ("write", "{}"),
            ("bash", "{}"),
            ("read", "malformed"),
            ("read", "{}"),
        ]:
            service.call(name, args)
        service.call("codemode", "{}")  # refusal has no events or execution
        return ToolExecutionOutcome(_tool_result(parent))

    tools = _NestedTools(
        [],
        [
            ToolExecutionOutcome(_tool_result(_child("parent0", 1))),
            ToolExecutionOutcome(
                _tool_result(_child("parent0", 2), "ordinary failure", is_error=True)
            ),
        ],
    )
    loop, _, events, _ = _setup(_Runner(body), tools=tools)
    loop.run(_run_input(tool_budget=6))
    lifecycle = [
        event
        for event in events.events
        if isinstance(
            event,
            (
                ToolCallStarted,
                ToolCallCompleted,
                NestedToolCallStarted,
                NestedToolCallCompleted,
            ),
        )
    ]
    assert [type(event) for event in lifecycle] == [
        ToolCallStarted,
        *([NestedToolCallStarted, NestedToolCallCompleted] * 6),
        ToolCallCompleted,
    ]
    completed = [
        event for event in lifecycle if isinstance(event, NestedToolCallCompleted)
    ]
    assert [event.status for event in completed] == [
        NestedCallStatus.SETTLED,
        NestedCallStatus.SETTLED,
        NestedCallStatus.BLOCKED,
        NestedCallStatus.UNAUTHORIZED,
        NestedCallStatus.MALFORMED,
        NestedCallStatus.BUDGET_EXHAUSTED,
    ]
    assert not completed[0].result.is_error
    assert all(event.result.is_error for event in completed[1:])
    assert all(
        event.call.provider_correlation_id == f"parent0/{i}"
        for i, event in enumerate(completed, 1)
    )
