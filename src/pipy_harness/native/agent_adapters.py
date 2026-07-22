"""Synchronous outer adapters for canonical native agent events.

The canonical package deliberately knows nothing about product persistence or
the metadata-only workflow archive.  This module owns those one-way
projections and the fixed serial composite used by current run modes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentEventSink,
    AgentMessage,
    AgentRunCompleted,
    AgentRunResult,
    AgentRunStarted,
    AgentToolResultMessage,
    AgentUserMessage,
    AssistantTextDelta,
    FollowUpConsumed,
    MessageCompleted,
    ProviderFailed,
    RetryCompleted,
    RetryScheduled,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    ToolCallStarted,
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


@dataclass(frozen=True, slots=True)
class AppendProductMessage:
    """One current native-session append implied by a canonical event."""

    message: AgentUserMessage | AgentAssistantMessage | AgentToolResultMessage


@runtime_checkable
class ProductSessionActionSink(Protocol):
    """Receives defined persistence actions without owning storage yet."""

    def append(self, action: AppendProductMessage) -> None: ...


class ProductSessionEventProjection:
    """Own event-to-session durable append behavior.

    Since Phase 3.3 this projection is the live durable writer: each canonical
    completion event it accepts is forwarded through a
    ``ProductSessionActionSink`` to ``product_session.append_message``.  This
    stateful projection distinguishes synthetic balance-only assistant
    completions from real provider completions and recovers skipped tool
    results from ``TurnCompleted`` without duplicating already completed tool
    calls.
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


class NativeProductSessionActionSink:
    """Persist projected product-session appends through current callbacks.

    :class:`ProductSessionEventProjection` turns each canonical completion
    event into an :class:`AppendProductMessage`; this concrete sink forwards
    the carried message to the product session's synchronous
    ``append_message`` callback, which maintains the live coding-state mirror
    and the durable native session tree.  It is the live projection wiring that
    replaced the reusable loop's run-effect append path.
    """

    __slots__ = ("_append_message",)

    def __init__(self, append_message: Callable[[AgentMessage], object]) -> None:
        if not callable(append_message):
            raise TypeError("append_message must be callable")
        self._append_message = append_message

    def append(self, action: AppendProductMessage) -> None:
        if not isinstance(action, AppendProductMessage):
            raise TypeError("action must be an AppendProductMessage")
        self._append_message(action.message)


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
