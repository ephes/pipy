"""Pure UI state reducer for the assistant message lifecycle.

This module owns the deterministic decision machine that maps canonical agent
events onto ordered rendering decisions.  It is intentionally free of terminal
I/O: :func:`reduce` is a pure function of ``(state, event)`` and imports only
canonical ``agent`` value types.  The outer rendering adapter holds a
:class:`UiState`, calls :func:`reduce`, and applies the returned
:data:`RenderDecision` tuple to a concrete renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pipy_harness.native.agent.events import (
    AgentEvent,
    AssistantReasoningDelta,
    AssistantTextDelta,
    MessageCompleted,
    MessageStarted,
    ProviderFailed,
    RunCancelled,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage, AgentToolCall
from pipy_harness.native.agent.results import AgentCancellationReason


@dataclass(frozen=True, slots=True)
class UiState:
    """Immutable assistant message-lifecycle state for the rendering adapter.

    ``assistant_active`` tracks whether an assistant message has started but not
    yet completed.  ``assistant_streamed`` records that at least one non-empty
    text delta streamed, so a buffered fallback is not re-rendered on
    completion.  ``assistant_completion_suppressed`` records that a failure or
    cancellation already produced a terminal decision, so ``MessageCompleted``
    stays silent.
    """

    assistant_active: bool = False
    assistant_streamed: bool = False
    assistant_completion_suppressed: bool = False


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


RenderDecision = (
    StartAssistantMessage
    | StreamAssistantText
    | StreamAssistantReasoning
    | RenderBufferedAssistantText
    | CompleteAssistantMessage
    | FailAssistantMessage
    | CancelAssistantMessage
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
        return (state, (StreamAssistantReasoning(event.delta.value),))
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


def _reduce_message_completed(
    state: UiState, event: MessageCompleted
) -> _Reduction:
    if not isinstance(event.message, AgentAssistantMessage):
        return (state, ())
    if not state.assistant_active:
        return (state, ())
    completed = replace(state, assistant_active=False)
    if state.assistant_completion_suppressed:
        return (completed, ())
    has_tool_calls = bool(event.message.tool_calls)
    decisions: list[RenderDecision] = []
    if event.message.content.value and not state.assistant_streamed:
        decisions.append(
            RenderBufferedAssistantText(
                event.message.content.value,
                has_tool_calls=has_tool_calls,
            )
        )
    decisions.append(CompleteAssistantMessage(has_tool_calls=has_tool_calls))
    return (completed, tuple(decisions))


def _reduce_tool_event(state: UiState, event: AgentEvent) -> _Reduction:
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
                ),
            ),
        )
    return (state, ())
