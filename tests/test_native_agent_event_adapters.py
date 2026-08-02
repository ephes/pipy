"""Focused contracts for the canonical-agent to Pi automation projection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEvent,
    AgentFailure,
    AgentMessage,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentRunResult,
    AgentRunStarted,
    AgentToolCall,
    AgentToolResultMessage,
    AgentTurnOutcome,
    AgentUsage,
    AgentUserMessage,
    AssistantReasoningDelta,
    AssistantTextDelta,
    FollowUpConsumed,
    MessageCompleted,
    MessageStarted,
    ProductContent,
    ProviderFailed,
    RetryCompleted,
    RetryScheduled,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
    TurnCompleted,
    TurnStarted,
    UsageUpdated,
)
from pipy_harness.native.agent_adapters import (
    AppendProductMessage,
    NativeProductSessionActionSink,
    ProductSessionEventProjection,
    SdkAgentEventAdapter,
    SynchronousAgentEventComposite,
    WorkflowAgentEventCounts,
    WorkflowArchiveAgentEventAdapter,
)
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.ui import RenderingAgentEventAdapter

_PRIVATE_ARGUMENTS = "PIPY_PRIVATE_ADAPTER_ARGUMENTS_2835"


class _RecordingRenderer:
    def __init__(self) -> None:
        self.actions: list[tuple[object, ...]] = []

    def start_assistant_message(self) -> None:
        self.actions.append(("assistant-start",))

    @property
    def stream_sink(self) -> StreamChunkSink:
        return lambda chunk: self.actions.append(("text", chunk))

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return lambda chunk: self.actions.append(("reasoning", chunk))

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None:
        self.actions.append(("buffered", text, has_tool_calls))

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None:
        self.actions.append(("assistant-complete", has_tool_calls))

    def fail_assistant_message(self) -> None:
        self.actions.append(("assistant-failed",))

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None:
        self.actions.append(("assistant-cancelled", reason))

    def render_tool_call(self, call: AgentToolCall) -> None:
        self.actions.append(("tool-start", call))

    def tool_output_sink(self, chunk: str) -> None:
        self.actions.append(("tool-update", chunk))

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        self.actions.append(("tool-complete", output_text, is_error, duration_seconds))


class _AutomationCollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


def test_rendering_projection_owns_streamed_buffered_and_tool_output() -> None:
    renderer = _RecordingRenderer()
    adapter = RenderingAgentEventAdapter(renderer)
    call = AgentToolCall("provider-call", "read", ProductContent("{}"))
    result = AgentToolResultMessage(
        "pipy-tool-000001",
        "read",
        ProductContent("result"),
        "provider-call",
        is_error=True,
    )

    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(AssistantReasoningDelta(0, ProductContent("thinking")))
    adapter.emit(AssistantTextDelta(0, ProductContent("streamed")))
    adapter.emit(MessageCompleted(0, AgentAssistantMessage(ProductContent("streamed"))))
    adapter.emit(MessageStarted(1, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(MessageCompleted(1, AgentAssistantMessage(ProductContent("buffered"))))
    adapter.emit(MessageStarted(2, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(
        MessageCompleted(
            2,
            AgentAssistantMessage(ProductContent("tool preamble"), (call,)),
        )
    )
    adapter.emit(ToolCallStarted(1, call))
    adapter.emit(ToolCallUpdated(1, call, ProductContent("progress")))
    adapter.emit(ToolCallCompleted(1, result, duration_seconds=0.25))

    assert renderer.actions == [
        ("assistant-start",),
        ("reasoning", "thinking"),
        ("text", "streamed"),
        ("assistant-complete", False),
        ("assistant-start",),
        ("buffered", "buffered", False),
        ("assistant-complete", False),
        ("assistant-start",),
        ("buffered", "tool preamble", True),
        ("assistant-complete", True),
        ("tool-start", call),
        ("tool-update", "progress"),
        ("tool-complete", "result", True, 0.25),
    ]


def test_rendering_projection_finalizes_each_successful_assistant_once() -> None:
    renderer = _RecordingRenderer()
    adapter = RenderingAgentEventAdapter(renderer)
    message = AgentAssistantMessage(ProductContent("done"))

    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(MessageCompleted(0, message))
    adapter.emit(MessageCompleted(0, message))
    adapter.emit(MessageStarted(1, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(ProviderFailed(AgentFailure("failed", ProductContent("no")), False))
    adapter.emit(MessageCompleted(1, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(MessageStarted(2, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(AssistantTextDelta(2, ProductContent("cancelled partial")))
    adapter.emit(RunCancelled(AgentCancellationReason.OPERATOR_ABORT))
    adapter.emit(MessageCompleted(2, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(MessageStarted(3, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(MessageCompleted(3, AgentAssistantMessage(ProductContent("fresh"))))

    assert renderer.actions == [
        ("assistant-start",),
        ("buffered", "done", False),
        ("assistant-complete", False),
        ("assistant-start",),
        ("assistant-failed",),
        ("assistant-start",),
        ("text", "cancelled partial"),
        ("assistant-cancelled", AgentCancellationReason.OPERATOR_ABORT),
        ("assistant-start",),
        ("buffered", "fresh", False),
        ("assistant-complete", False),
    ]


@pytest.mark.parametrize("reason", tuple(AgentCancellationReason))
def test_rendering_projection_maps_each_cancellation_reason_synchronously(
    reason: AgentCancellationReason,
) -> None:
    renderer = _RecordingRenderer()
    adapter = RenderingAgentEventAdapter(renderer)

    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(RunCancelled(reason))
    adapter.emit(MessageCompleted(0, AgentAssistantMessage(ProductContent(""))))

    assert renderer.actions == [
        ("assistant-start",),
        ("assistant-cancelled", reason),
    ]


def test_rendering_start_happens_before_later_canonical_projections() -> None:
    trace: list[str] = []

    class StartingRenderer(_RecordingRenderer):
        def start_assistant_message(self) -> None:
            trace.append("rendering-start")

    class LaterSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            trace.append("automation")

    composite = SynchronousAgentEventComposite(
        (RenderingAgentEventAdapter(StartingRenderer()), LaterSink())
    )

    composite.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))

    assert trace == ["rendering-start", "automation"]


def test_rendering_start_failure_stops_later_canonical_projections() -> None:
    trace: list[str] = []

    class FailingRenderer(_RecordingRenderer):
        def start_assistant_message(self) -> None:
            trace.append("rendering-start")
            raise RuntimeError("renderer rejected assistant start")

    class LaterSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            trace.append("automation")

    composite = SynchronousAgentEventComposite(
        (RenderingAgentEventAdapter(FailingRenderer()), LaterSink())
    )

    with pytest.raises(RuntimeError, match="renderer rejected assistant start"):
        composite.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))

    assert trace == ["rendering-start"]


def test_rendering_terminal_callback_failure_stops_later_projections() -> None:
    trace: list[str] = []

    class FailingRenderer(_RecordingRenderer):
        def fail_assistant_message(self) -> None:
            trace.append("rendering")
            raise RuntimeError("renderer rejected provider failure")

    class LaterSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            trace.append("automation")

    composite = SynchronousAgentEventComposite(
        (RenderingAgentEventAdapter(FailingRenderer()), LaterSink())
    )
    composite.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    trace.clear()

    with pytest.raises(RuntimeError, match="renderer rejected provider failure"):
        composite.emit(
            ProviderFailed(AgentFailure("failed", ProductContent("no")), False)
        )

    assert trace == ["rendering"]


def test_rendering_failure_stops_later_canonical_projections() -> None:
    trace: list[str] = []

    class FailingRenderer(_RecordingRenderer):
        @property
        def stream_sink(self) -> StreamChunkSink:
            def _fail(_chunk: str) -> None:
                trace.append("rendering")
                raise RuntimeError("renderer rejected delta")

            return _fail

    class LaterSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            trace.append("automation")

    composite = SynchronousAgentEventComposite(
        (RenderingAgentEventAdapter(FailingRenderer()), LaterSink())
    )

    with pytest.raises(RuntimeError, match="renderer rejected delta"):
        composite.emit(AssistantTextDelta(0, ProductContent("text")))

    assert trace == ["rendering"]


def test_automation_projection_preserves_pi_partial_and_tool_shapes() -> None:
    sink = _AutomationCollectingSink()
    adapter = AutomationAgentEventAdapter(sink)
    malformed_call = AgentToolCall(
        "provider-call-malformed",
        "read",
        ProductContent(_PRIVATE_ARGUMENTS),
    )
    result = AgentToolResultMessage(
        "pipy-tool-000042",
        "read",
        ProductContent("tool result"),
        provider_correlation_id="provider-call-malformed",
        is_error=True,
    )

    adapter.emit(MessageStarted(2, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(AssistantTextDelta(2, ProductContent("first")))
    adapter.emit(AssistantTextDelta(2, ProductContent(" second")))
    adapter.emit(ToolCallStarted(2, malformed_call))
    adapter.emit(ToolCallUpdated(2, malformed_call, ProductContent("partial result")))
    adapter.emit(ToolCallCompleted(2, result))

    assert sink.events == [
        {
            "type": "message_start",
            "message": {"role": "assistant", "content": []},
        },
        {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "first"}],
            },
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": "first",
                "partial": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "first"}],
                },
            },
        },
        {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "first second"}],
            },
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": " second",
                "partial": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "first second"}],
                },
            },
        },
        {
            "type": "tool_execution_start",
            "toolCallId": "provider-call-malformed",
            "toolName": "read",
            "args": {"_raw": _PRIVATE_ARGUMENTS},
        },
        {
            "type": "tool_execution_update",
            "toolCallId": "provider-call-malformed",
            "toolName": "read",
            "args": {"_raw": _PRIVATE_ARGUMENTS},
            "partialResult": "partial result",
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "provider-call-malformed",
            "toolName": "read",
            "result": "tool result",
            "isError": True,
        },
    ]
    assert result.tool_request_id == "pipy-tool-000042"
    assert result.provider_correlation_id == sink.events[-1]["toolCallId"]
    assert "toolRequestId" not in sink.events[-1]


def test_automation_projection_silently_ignores_internal_bookkeeping() -> None:
    sink = _AutomationCollectingSink()
    adapter = AutomationAgentEventAdapter(sink)

    adapter.emit(AssistantReasoningDelta(0, ProductContent("private reasoning")))
    adapter.emit(
        UsageUpdated(
            AgentUsage(input_tokens=12, output_tokens=4),
            last_turn_total_tokens=16,
        )
    )
    adapter.emit(SteeringConsumed(ProductContent("private steering")))
    adapter.emit(FollowUpConsumed(ProductContent("private follow-up")))
    failure = AgentFailure("ProviderFailure", ProductContent("private failure"))
    adapter.emit(ProviderFailed(failure, will_retry=False))
    adapter.emit(
        RunCancelled(
            AgentCancellationReason.OPERATOR_ABORT,
            ProductContent("private cancellation"),
        )
    )

    assert sink.events == []


def test_automation_projection_preserves_lifecycle_retry_and_terminal_shapes() -> None:
    sink = _AutomationCollectingSink()
    adapter = AutomationAgentEventAdapter(sink)
    user = AgentUserMessage(ProductContent("prompt"))
    assistant = AgentAssistantMessage(ProductContent("answer"))
    failure = AgentFailure("TransientProviderFailure", ProductContent("retry me"))

    adapter.emit(AgentRunStarted())
    adapter.emit(TurnStarted(0))
    adapter.emit(MessageStarted(0, user))
    adapter.emit(MessageCompleted(0, user))
    adapter.emit(RetryScheduled(1, 3, 250, failure))
    adapter.emit(RetryCompleted(1, False, failure))
    adapter.emit(TurnCompleted(0, AgentTurnOutcome.SUCCEEDED, assistant))
    adapter.emit(
        AgentRunCompleted(
            AgentRunResult(
                AgentRunOutcome.SUCCEEDED,
                (user, assistant),
            )
        )
    )

    assert sink.events == [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "message_start",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "prompt"}],
            },
        },
        {
            "type": "message_end",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "prompt"}],
            },
        },
        {
            "type": "auto_retry_start",
            "attempt": 1,
            "maxAttempts": 3,
            "delayMs": 250,
            "errorMessage": "retry me",
        },
        {
            "type": "auto_retry_end",
            "success": False,
            "attempt": 1,
            "finalError": "retry me",
        },
        {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
            },
            "toolResults": [],
        },
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "prompt"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "answer"}],
                },
            ],
            "willRetry": False,
        },
    ]


def test_automation_projection_is_synchronous_and_propagates_sink_failure() -> None:
    trace: list[str] = []

    class SinkFailure(RuntimeError):
        pass

    class FailingSink:
        def emit(self, event: dict[str, object]) -> None:
            trace.append(str(event["type"]))
            raise SinkFailure("wire sink rejected event")

    adapter = AutomationAgentEventAdapter(FailingSink())

    with pytest.raises(SinkFailure, match="wire sink rejected event"):
        adapter.emit(AgentRunStarted())

    assert trace == ["agent_start"]


def test_synchronous_composite_stops_at_the_first_failed_projection() -> None:
    trace: list[str] = []

    class RecordingSink:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self._name = name
            self._fail = fail

        def emit(self, event: object) -> None:
            del event
            trace.append(self._name)
            if self._fail:
                raise RuntimeError("projection failed")

    composite = SynchronousAgentEventComposite(
        (
            RecordingSink("automation"),
            RecordingSink("persistence", fail=True),
            RecordingSink("workflow"),
        )
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        composite.emit(AgentRunStarted())

    assert trace == ["automation", "persistence"]


class _ProductActionCollector:
    def __init__(self) -> None:
        self.actions: list[AppendProductMessage] = []

    def append(self, action: AppendProductMessage) -> None:
        self.actions.append(action)


def _assert_product_message_identities(
    actual: Sequence[AgentMessage], expected: Sequence[AgentMessage]
) -> None:
    assert all(
        actual_message is expected_message
        for actual_message, expected_message in zip(actual, expected, strict=True)
    )


def test_product_projection_preserves_real_empty_assistant_and_skipped_results() -> (
    None
):
    collector = _ProductActionCollector()
    projection = ProductSessionEventProjection(collector)
    user = AgentUserMessage(ProductContent("prompt"))
    empty_assistant = AgentAssistantMessage(ProductContent(""))
    completed = AgentToolResultMessage(
        "pipy-tool-completed",
        "read",
        ProductContent("completed"),
        "provider-completed",
    )
    skipped = AgentToolResultMessage(
        "pipy-tool-skipped",
        "ls",
        ProductContent("skipped"),
        "provider-skipped",
        is_error=True,
    )

    projection.emit(AgentRunStarted())
    projection.emit(MessageCompleted(0, user))
    projection.emit(MessageCompleted(0, empty_assistant))
    projection.emit(ToolCallCompleted(0, completed))
    projection.emit(
        TurnCompleted(
            0,
            AgentTurnOutcome.CANCELLED,
            empty_assistant,
            (completed, skipped),
        )
    )

    assert [action.message for action in collector.actions] == [
        user,
        empty_assistant,
        completed,
        skipped,
    ]


@pytest.mark.parametrize(
    "terminal_event",
    [
        ProviderFailed(
            AgentFailure("ProviderFailure", ProductContent("private failure")),
            will_retry=False,
        ),
        RunCancelled(AgentCancellationReason.OPERATOR_ABORT),
    ],
)
def test_product_projection_omits_synthetic_balance_only_assistant(
    terminal_event: AgentEvent,
) -> None:
    collector = _ProductActionCollector()
    projection = ProductSessionEventProjection(collector)
    empty_assistant = AgentAssistantMessage(ProductContent(""))

    projection.emit(AgentRunStarted())
    projection.emit(terminal_event)
    projection.emit(MessageCompleted(0, empty_assistant))
    projection.emit(TurnCompleted(0, AgentTurnOutcome.CANCELLED, empty_assistant))

    assert collector.actions == []


@pytest.mark.parametrize("shorter_side", ["actual", "expected"])
def test_product_message_identity_assertion_rejects_unequal_lengths(
    shorter_side: str,
) -> None:
    message = AgentUserMessage(ProductContent("same common-prefix identity"))
    actual: Sequence[AgentMessage] = (message,)
    expected: Sequence[AgentMessage] = (message,)
    if shorter_side == "actual":
        actual = ()
    else:
        expected = ()

    with pytest.raises(ValueError):
        _assert_product_message_identities(actual, expected)


def test_product_projection_pins_exact_append_sequence_for_a_real_assistant_turn() -> (
    None
):
    # Slice 3.3 precondition: pin the exact durable-append sequence the live
    # persistence projection must reproduce for a full real turn — a user
    # message, a real (non-empty, tool-calling) assistant message, the completed
    # tool result, and a tool result recovered as skipped from ``TurnCompleted``
    # — with the already completed result appended exactly once (no duplicate).
    collector = _ProductActionCollector()
    projection = ProductSessionEventProjection(collector)
    user = AgentUserMessage(ProductContent("please read the file"))
    call = AgentToolCall("provider-call", "read", ProductContent('{"path":"x"}'))
    real_assistant = AgentAssistantMessage(
        ProductContent("I will read that file now."), (call,)
    )
    completed = AgentToolResultMessage(
        "pipy-tool-completed",
        "read",
        ProductContent("file contents"),
        "provider-completed",
    )
    skipped = AgentToolResultMessage(
        "pipy-tool-skipped",
        "ls",
        ProductContent("never executed"),
        "provider-skipped",
        is_error=True,
    )

    projection.emit(AgentRunStarted())
    projection.emit(MessageCompleted(0, user))
    projection.emit(MessageCompleted(0, real_assistant))
    projection.emit(ToolCallCompleted(0, completed))
    projection.emit(
        TurnCompleted(
            0,
            AgentTurnOutcome.SUCCEEDED,
            real_assistant,
            (completed, skipped),
        )
    )

    appended = [action.message for action in collector.actions]
    assert appended == [user, real_assistant, completed, skipped]
    _assert_product_message_identities(
        appended, (user, real_assistant, completed, skipped)
    )
    # The already completed tool result is not re-appended from ``TurnCompleted``.
    assert appended.count(completed) == 1


def test_product_projection_synthetic_suppression_is_one_shot_and_reset_scoped() -> (
    None
):
    # The synthetic balance-only assistant suppression armed by a terminal
    # failure/cancellation consumes exactly one assistant completion, so a
    # genuine assistant emitted afterwards in the same turn is still appended;
    # a fresh ``AgentRunStarted`` also clears a stale armed suppression.
    collector = _ProductActionCollector()
    projection = ProductSessionEventProjection(collector)
    synthetic = AgentAssistantMessage(ProductContent(""))
    real_same_turn = AgentAssistantMessage(ProductContent("recovered real answer"))
    real_next_run = AgentAssistantMessage(ProductContent("next run answer"))

    projection.emit(AgentRunStarted())
    projection.emit(
        ProviderFailed(
            AgentFailure("ProviderFailure", ProductContent("private failure")),
            will_retry=True,
        )
    )
    projection.emit(MessageCompleted(0, synthetic))
    projection.emit(MessageCompleted(0, real_same_turn))

    # A second armed suppression that never consumes an assistant is discarded
    # by the reset on the next run start rather than leaking into it.
    projection.emit(RunCancelled(AgentCancellationReason.OPERATOR_ABORT))
    projection.emit(AgentRunStarted())
    projection.emit(MessageCompleted(0, real_next_run))

    assert [action.message for action in collector.actions] == [
        real_same_turn,
        real_next_run,
    ]


def test_product_projection_with_default_sink_stays_inert_across_a_full_stream() -> (
    None
):
    # Production now wires this projection with a live
    # ``NativeProductSessionActionSink`` (Slice 3.3 cutover); the default
    # ``sink=None`` construction remains a safe inert fallback that accepts the
    # full canonical stream without writing or raising.
    projection = ProductSessionEventProjection()
    user = AgentUserMessage(ProductContent("prompt"))
    call = AgentToolCall("provider-call", "read", ProductContent('{"path":"x"}'))
    assistant = AgentAssistantMessage(ProductContent("answer"), (call,))
    completed = AgentToolResultMessage(
        "pipy-tool-completed",
        "read",
        ProductContent("contents"),
        "provider-completed",
    )

    projection.emit(AgentRunStarted())
    projection.emit(MessageCompleted(0, user))
    projection.emit(MessageCompleted(0, assistant))
    projection.emit(ToolCallCompleted(0, completed))
    projection.emit(
        TurnCompleted(0, AgentTurnOutcome.SUCCEEDED, assistant, (completed,))
    )
    projection.emit(
        RunCancelled(AgentCancellationReason.OPERATOR_ABORT, ProductContent("detail"))
    )


def test_native_product_action_sink_forwards_each_append_to_current_callback() -> None:
    first: list[AgentMessage] = []
    second: list[AgentMessage] = []
    current = {"messages": first}
    sink = NativeProductSessionActionSink(
        lambda message: current["messages"].append(message)
    )
    user = AgentUserMessage(ProductContent("first"))
    assistant = AgentAssistantMessage(ProductContent("second"))

    sink.append(AppendProductMessage(user))
    current["messages"] = second
    sink.append(AppendProductMessage(assistant))

    assert first == [user]
    assert second == [assistant]


def test_native_product_action_sink_rejects_non_action_and_propagates_failure() -> None:
    with pytest.raises(TypeError, match="append_message must be callable"):
        NativeProductSessionActionSink(cast(Callable[[AgentMessage], object], None))

    recorded: list[AgentMessage] = []
    sink = NativeProductSessionActionSink(recorded.append)
    with pytest.raises(TypeError, match="must be an AppendProductMessage"):
        sink.append(cast(AppendProductMessage, object()))
    assert recorded == []

    failure = RuntimeError("append failed")

    def fail(message: AgentMessage) -> object:
        del message
        raise failure

    with pytest.raises(RuntimeError, match="append failed") as raised:
        NativeProductSessionActionSink(fail).append(
            AppendProductMessage(AgentUserMessage(ProductContent("x")))
        )
    assert raised.value is failure


def test_workflow_projection_exposes_only_fixed_numeric_counts() -> None:
    adapter = WorkflowArchiveAgentEventAdapter()
    private = ProductContent("PIPY_PRIVATE_WORKFLOW_PROJECTION_SENTINEL")
    call = AgentToolCall("provider-call", "private-tool-name", private)
    result = AgentToolResultMessage(
        "pipy-tool-result",
        "private-tool-name",
        private,
        "provider-call",
    )

    for event in (
        AgentRunStarted(),
        TurnStarted(0),
        MessageCompleted(0, AgentUserMessage(private)),
        ToolCallStarted(0, call),
        ToolCallCompleted(0, result),
        RunCancelled(AgentCancellationReason.PROVIDER_CANCELLED, private),
        TurnCompleted(0, AgentTurnOutcome.CANCELLED, AgentAssistantMessage(private)),
        AgentRunCompleted(
            AgentRunResult(
                AgentRunOutcome.CANCELLED,
                (AgentUserMessage(private),),
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED,
                cancellation_detail=private,
            )
        ),
    ):
        adapter.emit(event)

    counts = adapter.counts()
    assert counts == WorkflowAgentEventCounts(
        run_started=1,
        run_completed=1,
        turns_started=1,
        turns_completed=1,
        tool_calls_started=1,
        tool_calls_completed=1,
        usage_updates=0,
        retries_scheduled=0,
        retries_completed=0,
        cancellations=1,
        provider_failures=0,
        steering_consumed=0,
        follow_ups_consumed=0,
    )
    assert "PIPY_PRIVATE_WORKFLOW_PROJECTION_SENTINEL" not in repr(counts)


def test_sdk_projection_streams_synchronously_and_retains_terminal_result() -> None:
    trace: list[str] = []
    adapter = SdkAgentEventAdapter(trace.append)
    result = AgentRunResult(
        AgentRunOutcome.SUCCEEDED,
        (AgentAssistantMessage(ProductContent("final")),),
    )

    adapter.emit(AssistantTextDelta(0, ProductContent("one")))
    assert trace == ["one"]
    assert adapter.result is None
    adapter.emit(AgentRunCompleted(result))

    assert adapter.result is result
