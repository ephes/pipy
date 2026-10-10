"""Pure UI state reducer for the assistant message lifecycle.

This module owns the deterministic decision machine that maps canonical agent
events onto ordered rendering decisions.  It is intentionally free of terminal
I/O: :func:`reduce` is a pure function of ``(state, event)`` and imports only
canonical ``agent`` value types.  The outer rendering adapter holds a
:class:`UiState`, calls :func:`reduce`, and applies the returned
:data:`RenderDecision` tuple to a concrete renderer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pipy_harness.native.agent.content import display_segments
from pipy_harness.native.agent.events import (
    AgentEvent,
    AssistantReasoningDelta,
    AssistantTextDelta,
    MessageCompleted,
    MessageStarted,
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ProviderFailed,
    RetryCompleted,
    RetryScheduled,
    RunCancelled,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage, AgentToolCall
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.ui.stopped_turn import stopped_assistant_marker


@dataclass(frozen=True, slots=True)
class UiState:
    """Immutable assistant message-lifecycle state for the rendering adapter.

    ``assistant_active`` tracks whether an assistant message has started but not
    yet completed.  ``assistant_streamed`` records that at least one non-empty
    text delta streamed, so a buffered fallback is not re-rendered on
    completion; ``reasoning_streamed`` does the same for reasoning deltas.
    ``assistant_completion_suppressed`` records that a failure or
    cancellation already produced a terminal decision, so ``MessageCompleted``
    stays silent for synthetic unstopped messages. Canonical stopped messages
    still commit their partial content and terminal marker.
    """

    assistant_active: bool = False
    assistant_streamed: bool = False
    assistant_completion_suppressed: bool = False
    reasoning_streamed: bool = False


@dataclass(frozen=True, slots=True)
class StartAssistantMessage:
    """Begin a new assistant message on the renderer."""


@dataclass(frozen=True, slots=True)
class StreamAssistantText:
    """Forward one full-content assistant text delta to the stream sink."""

    text: str


@dataclass(frozen=True, slots=True)
class StreamAssistantReasoning:
    """Forward one full-content assistant reasoning delta to the reasoning sink."""

    text: str


@dataclass(frozen=True, slots=True)
class RenderBufferedAssistantText:
    """Render a non-streamed assistant body accumulated for buffered display."""

    text: str
    has_tool_calls: bool


@dataclass(frozen=True, slots=True)
class RenderBufferedAssistantThinking:
    """Render one thinking run of a non-streamed answer, in its place."""

    text: str


@dataclass(frozen=True, slots=True)
class CompleteAssistantMessage:
    """Finalize the active assistant message exactly once."""

    has_tool_calls: bool


@dataclass(frozen=True, slots=True)
class FailAssistantMessage:
    """Render the active assistant message as a provider failure."""


@dataclass(frozen=True, slots=True)
class CancelAssistantMessage:
    """Render the active assistant message as cancelled with its reason."""

    reason: AgentCancellationReason


@dataclass(frozen=True, slots=True)
class RenderStoppedAssistant:
    """Commit a stopped message marker after its admitted partial content."""

    marker: str


@dataclass(frozen=True, slots=True)
class ScheduleRetry:
    """Show a failed attempt and the countdown to its automatic retry.

    Pi renders the failed attempt's ``Error: <message>`` and swaps the working
    loader for ``Retrying (attempt/max_attempts) in Ns...``.
    """

    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str


@dataclass(frozen=True, slots=True)
class FinishRetry:
    """End the retry sequence; a failure shows Pi's final retry line."""

    succeeded: bool
    attempt: int
    final_error: str | None


@dataclass(frozen=True, slots=True)
class RenderNestedToolCall:
    event: NestedToolCallStarted | NestedToolCallCompleted


@dataclass(frozen=True, slots=True)
class RenderToolCall:
    """Render a model-requested tool call entering execution."""

    call: AgentToolCall


@dataclass(frozen=True, slots=True)
class StreamToolOutput:
    """Forward one live full-content update chunk from a running tool."""

    text: str


@dataclass(frozen=True, slots=True)
class RenderToolResult:
    """Render a completed tool call's provider-visible result."""

    output_text: str
    is_error: bool
    duration_seconds: float | None
    # Pi ``ToolResultMessage.details`` for the tool's result renderer.
    details: Mapping[str, Any] | None = field(default=None, compare=False)


RenderDecision = (
    StartAssistantMessage
    | StreamAssistantText
    | StreamAssistantReasoning
    | RenderBufferedAssistantText
    | RenderBufferedAssistantThinking
    | CompleteAssistantMessage
    | FailAssistantMessage
    | CancelAssistantMessage
    | RenderStoppedAssistant
    | ScheduleRetry
    | FinishRetry
    | RenderNestedToolCall
    | RenderToolCall
    | StreamToolOutput
    | RenderToolResult
)
"""Closed union of ordered rendering decisions produced by :func:`reduce`."""


_Reduction = tuple[UiState, tuple[RenderDecision, ...]]


def reduce(state: UiState, event: AgentEvent) -> _Reduction:
    """Map ``event`` onto the next :class:`UiState` and ordered decisions.

    The function is pure: it never touches a terminal or renderer.  It owns the
    full agent-event-to-render-decision mapping: the assistant message lifecycle
    and the three stateless tool-event renders (call start, live update, and
    result).  Tool events carry no display state, so they leave ``state``
    untouched; every other canonical event returns the unchanged state with no
    decisions.
    """

    assistant_reduction = _reduce_assistant_event(state, event)
    if assistant_reduction is not None:
        return assistant_reduction
    return _reduce_tool_event(state, event)


def _reduce_assistant_event(state: UiState, event: AgentEvent) -> _Reduction | None:
    if isinstance(event, MessageStarted):
        return _reduce_message_started(state, event)
    if isinstance(event, AssistantTextDelta):
        streamed = state.assistant_streamed or bool(event.delta.value)
        return (
            replace(state, assistant_streamed=streamed),
            (StreamAssistantText(event.delta.value),),
        )
    if isinstance(event, AssistantReasoningDelta):
        streamed = state.reasoning_streamed or bool(event.delta.value)
        return (
            replace(state, reasoning_streamed=streamed),
            (StreamAssistantReasoning(event.delta.value),),
        )
    if isinstance(event, ProviderFailed):
        suppressed = replace(state, assistant_completion_suppressed=True)
        if state.assistant_active:
            return (suppressed, (FailAssistantMessage(),))
        return (suppressed, ())
    if isinstance(event, RunCancelled):
        suppressed = replace(state, assistant_completion_suppressed=True)
        if state.assistant_active:
            return (suppressed, (CancelAssistantMessage(event.reason),))
        return (suppressed, ())
    if isinstance(event, MessageCompleted):
        return _reduce_message_completed(state, event)
    return _reduce_retry_event(state, event)


def _reduce_retry_event(state: UiState, event: AgentEvent) -> _Reduction | None:
    if isinstance(event, RetryScheduled):
        # The retried attempt starts over, so its text renders as fresh output
        # (a buffered completion is no longer covered by the failed stream).
        return (
            replace(state, assistant_streamed=False, reasoning_streamed=False),
            (
                ScheduleRetry(
                    event.attempt,
                    event.max_attempts,
                    event.delay_ms,
                    event.failure.message.value,
                ),
            ),
        )
    if isinstance(event, RetryCompleted):
        final_error = event.failure.message.value if event.failure is not None else None
        return (state, (FinishRetry(event.succeeded, event.attempt, final_error),))
    return None


def _reduce_message_started(state: UiState, event: MessageStarted) -> _Reduction:
    if not isinstance(event.message, AgentAssistantMessage):
        return (state, ())
    return (
        UiState(
            assistant_active=True,
            assistant_streamed=False,
            assistant_completion_suppressed=False,
        ),
        (StartAssistantMessage(),),
    )


def _reduce_message_completed(state: UiState, event: MessageCompleted) -> _Reduction:
    if not isinstance(event.message, AgentAssistantMessage):
        return (state, ())
    if not state.assistant_active:
        return (state, ())
    completed = replace(state, assistant_active=False)
    marker = stopped_assistant_marker(event.message)
    if state.assistant_completion_suppressed and marker is None:
        return (completed, ())
    has_tool_calls = bool(event.message.tool_calls)
    # Stopped partial calls will never execute. Show their partial answer rather
    # than treating it as a successful tool preamble (hidden by the plain UI).
    render_tool_preamble = has_tool_calls and marker is None
    decisions: list[RenderDecision] = []
    if not state.assistant_streamed and not state.reasoning_streamed:
        # Nothing streamed: draw the answer's thinking runs and text in order.
        for thinking, text in display_segments(event.message.ordered_content()):
            decisions.append(
                RenderBufferedAssistantThinking(text)
                if thinking
                else RenderBufferedAssistantText(
                    text, has_tool_calls=render_tool_preamble
                )
            )
    elif event.message.content.value and not state.assistant_streamed:
        decisions.append(
            RenderBufferedAssistantText(
                event.message.content.value,
                has_tool_calls=render_tool_preamble,
            )
        )
    if marker is not None:
        decisions.append(RenderStoppedAssistant(marker))
    elif not state.assistant_completion_suppressed:
        decisions.append(CompleteAssistantMessage(has_tool_calls=has_tool_calls))
    return (completed, tuple(decisions))


def _reduce_tool_event(state: UiState, event: AgentEvent) -> _Reduction:
    if isinstance(event, (NestedToolCallStarted, NestedToolCallCompleted)):
        return (state, (RenderNestedToolCall(event),))
    if isinstance(event, ToolCallStarted):
        return (state, (RenderToolCall(event.call),))
    if isinstance(event, ToolCallUpdated):
        return (state, (StreamToolOutput(event.update.value),))
    if isinstance(event, ToolCallCompleted):
        return (
            state,
            (
                RenderToolResult(
                    output_text=event.result.content.value,
                    is_error=event.result.is_error,
                    duration_seconds=event.duration_seconds,
                    details=event.result.details,
                ),
            ),
        )
    return (state, ())
