"""Synchronous outer adapters for canonical native agent events.

The canonical package deliberately knows nothing about product persistence or
the metadata-only workflow archive.  This module owns those one-way
projections and the fixed serial composite used by current run modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEvent,
    AgentEventSink,
    AgentRunCompleted,
    AgentRunResult,
    AgentRunStarted,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    AssistantReasoningDelta,
    AssistantTextDelta,
    FollowUpConsumed,
    MessageCompleted,
    MessageStarted,
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
from pipy_harness.native.provider import StreamChunkSink


class SynchronousAgentEventComposite:
    """Deliver each event to a fixed tuple of sinks, stopping on first failure."""

    def __init__(self, sinks: tuple[AgentEventSink, ...]) -> None:
        if not isinstance(sinks, tuple):
            raise TypeError("SynchronousAgentEventComposite.sinks must be a tuple")
        if any(not isinstance(sink, AgentEventSink) for sink in sinks):
            raise TypeError("all composite sinks must implement AgentEventSink")
        self._sinks = sinks

    def emit(self, event: AgentEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)


@runtime_checkable
class AgentEventRenderer(Protocol):
    """Renderer callbacks driven exclusively by canonical agent events."""

    def start_assistant_message(self) -> None: ...

    @property
    def stream_sink(self) -> StreamChunkSink: ...

    @property
    def reasoning_sink(self) -> StreamChunkSink: ...

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None: ...

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None: ...

    def fail_assistant_message(self) -> None: ...

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None: ...

    def render_tool_call(self, call: AgentToolCall) -> None: ...

    def tool_output_sink(self, chunk: str) -> None: ...

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None: ...


class RenderingAgentEventAdapter:
    """Project canonical deltas, messages, and tool events onto a renderer."""

    def __init__(self, renderer: AgentEventRenderer) -> None:
        if not isinstance(renderer, AgentEventRenderer):
            raise TypeError("renderer must implement AgentEventRenderer")
        self._renderer = renderer
        self._assistant_active = False
        self._assistant_streamed = False
        self._assistant_completion_suppressed = False

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, MessageStarted) and isinstance(
            event.message, AgentAssistantMessage
        ):
            self._renderer.start_assistant_message()
            self._assistant_active = True
            self._assistant_streamed = False
            self._assistant_completion_suppressed = False
        elif isinstance(event, AssistantTextDelta):
            self._renderer.stream_sink(event.delta.value)
            self._assistant_streamed = self._assistant_streamed or bool(
                event.delta.value
            )
        elif isinstance(event, AssistantReasoningDelta):
            self._renderer.reasoning_sink(event.delta.value)
        elif isinstance(event, ProviderFailed):
            self._assistant_completion_suppressed = True
            if self._assistant_active:
                self._renderer.fail_assistant_message()
        elif isinstance(event, RunCancelled):
            self._assistant_completion_suppressed = True
            if self._assistant_active:
                self._renderer.cancel_assistant_message(event.reason)
        elif isinstance(event, MessageCompleted) and isinstance(
            event.message, AgentAssistantMessage
        ):
            if not self._assistant_active:
                return
            self._assistant_active = False
            if self._assistant_completion_suppressed:
                return
            if event.message.content.value and not self._assistant_streamed:
                self._renderer.render_buffered_assistant_text(
                    event.message.content.value,
                    has_tool_calls=bool(event.message.tool_calls),
                )
            self._renderer.complete_assistant_message(
                has_tool_calls=bool(event.message.tool_calls),
            )
        elif isinstance(event, ToolCallStarted):
            self._renderer.render_tool_call(event.call)
        elif isinstance(event, ToolCallCompleted):
            self._renderer.render_tool_result(
                output_text=event.result.content.value,
                is_error=event.result.is_error,
                duration_seconds=event.duration_seconds,
            )
        elif isinstance(event, ToolCallUpdated):
            self._renderer.tool_output_sink(event.update.value)


@dataclass(frozen=True, slots=True)
class AppendProductMessage:
    """One current native-session append implied by a canonical event."""

    message: AgentUserMessage | AgentAssistantMessage | AgentToolResultMessage


@runtime_checkable
class ProductSessionActionSink(Protocol):
    """Receives defined persistence actions without owning storage yet."""

    def append(self, action: AppendProductMessage) -> None: ...


class ProductSessionEventProjection:
    """Define today's event-to-session append behavior for Phase 3.3.

    The current loop still performs the actual writes.  This stateful
    projection distinguishes synthetic balance-only assistant completions from
    real provider completions and recovers skipped tool results from
    ``TurnCompleted`` without duplicating already completed tool calls.
    """

    def __init__(self, sink: ProductSessionActionSink | None = None) -> None:
        self._sink = sink
        self._suppress_next_assistant = False
        self._completed_tool_request_ids: set[str] = set()

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, AgentRunStarted):
            self._reset_turn_state()
        elif isinstance(event, (ProviderFailed, RunCancelled)):
            self._suppress_next_assistant = True
        elif isinstance(event, MessageCompleted):
            self._project_message(event)
        elif isinstance(event, ToolCallCompleted):
            self._completed_tool_request_ids.add(event.result.tool_request_id)
            self._append(event.result)
        elif isinstance(event, TurnCompleted):
            self._append_skipped_results(event)
            self._reset_turn_state()

    def _project_message(self, event: MessageCompleted) -> None:
        message = event.message
        if isinstance(message, AgentUserMessage):
            self._append(message)
            return
        if not isinstance(message, AgentAssistantMessage):
            return
        if self._suppress_next_assistant:
            self._suppress_next_assistant = False
            return
        self._append(message)

    def _append_skipped_results(self, event: TurnCompleted) -> None:
        for result in event.tool_results:
            if result.tool_request_id not in self._completed_tool_request_ids:
                self._append(result)

    def _append(
        self,
        message: AgentUserMessage | AgentAssistantMessage | AgentToolResultMessage,
    ) -> None:
        if self._sink is not None:
            self._sink.append(AppendProductMessage(message))

    def _reset_turn_state(self) -> None:
        self._suppress_next_assistant = False
        self._completed_tool_request_ids.clear()


@dataclass(frozen=True, slots=True)
class WorkflowAgentEventCounts:
    """Closed, numeric-only workflow projection with no product content."""

    run_started: int
    run_completed: int
    turns_started: int
    turns_completed: int
    tool_calls_started: int
    tool_calls_completed: int
    usage_updates: int
    retries_scheduled: int
    retries_completed: int
    cancellations: int
    provider_failures: int
    steering_consumed: int
    follow_ups_consumed: int


class WorkflowArchiveAgentEventAdapter:
    """Observe only explicitly safe event categories for workflow metadata.

    The current workflow archive has no per-agent-event records, so this
    adapter intentionally emits nothing.  It is wired into the mode composite
    and exposes only fixed numeric counters; it never dereferences messages,
    deltas, tool names, failure labels, or any ``ProductContent`` value.
    """

    def __init__(self) -> None:
        self._counts = [0] * 13

    def emit(self, event: AgentEvent) -> None:
        safe_types = (
            AgentRunStarted,
            AgentRunCompleted,
            TurnStarted,
            TurnCompleted,
            ToolCallStarted,
            ToolCallCompleted,
            UsageUpdated,
            RetryScheduled,
            RetryCompleted,
            RunCancelled,
            ProviderFailed,
            SteeringConsumed,
            FollowUpConsumed,
        )
        for index, event_type in enumerate(safe_types):
            if isinstance(event, event_type):
                self._counts[index] += 1
                return

    def counts(self) -> WorkflowAgentEventCounts:
        return WorkflowAgentEventCounts(*self._counts)


class SdkAgentEventAdapter:
    """Preserve the synchronous SDK stream callback and terminal result seam."""

    def __init__(self, stream_sink: StreamChunkSink | None = None) -> None:
        self._stream_sink = stream_sink
        self._result: AgentRunResult | None = None

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, AgentRunCompleted):
            self._result = event.result
        elif isinstance(event, AssistantTextDelta) and self._stream_sink is not None:
            self._stream_sink(event.delta.value)

    @property
    def result(self) -> AgentRunResult | None:
        return self._result
