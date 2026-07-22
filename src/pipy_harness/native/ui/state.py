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
)
from pipy_harness.native.agent.messages import AgentAssistantMessage
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


RenderDecision = (
    StartAssistantMessage
    | StreamAssistantText
    | StreamAssistantReasoning
    | RenderBufferedAssistantText
    | CompleteAssistantMessage
    | FailAssistantMessage
    | CancelAssistantMessage
)
"""Closed union of ordered rendering decisions produced by :func:`reduce`."""


def reduce(
    state: UiState, event: AgentEvent
) -> tuple[UiState, tuple[RenderDecision, ...]]:
    """Map ``event`` onto the next :class:`UiState` and ordered decisions.

    The function is pure: it never touches a terminal or renderer.  It owns the
    assistant message lifecycle only; tool events and other canonical events
    return the unchanged state with no decisions.
    """

    if isinstance(event, MessageStarted) and isinstance(
        event.message, AgentAssistantMessage
    ):
        return (
            UiState(
                assistant_active=True,
                assistant_streamed=False,
                assistant_completion_suppressed=False,
            ),
            (StartAssistantMessage(),),
        )
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
    if isinstance(event, MessageCompleted) and isinstance(
        event.message, AgentAssistantMessage
    ):
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
    return (state, ())
