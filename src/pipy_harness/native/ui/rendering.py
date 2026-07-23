"""Rendering adapter that drives a renderer from canonical agent events.

The adapter owns no display state or event branching of its own: it holds a
:class:`UiState`, delegates every canonical event to the pure :func:`reduce`,
and applies the returned decisions to the renderer.  It is a thin driver — the
reducer is the single owner of the agent-event-to-render-decision mapping,
including the tool-call, tool-update, and tool-result renders.
"""

from __future__ import annotations

from typing import Protocol, assert_never, runtime_checkable

from pipy_harness.native.agent import AgentCancellationReason, AgentEvent, AgentToolCall
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.ui.state import (
    CancelAssistantMessage,
    CompleteAssistantMessage,
    FailAssistantMessage,
    RenderBufferedAssistantText,
    RenderDecision,
    RenderToolCall,
    RenderToolResult,
    StartAssistantMessage,
    StreamAssistantReasoning,
    StreamAssistantText,
    StreamToolOutput,
    UiState,
    reduce,
)


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
        self._state = UiState()

    def emit(self, event: AgentEvent) -> None:
        self._state, decisions = reduce(self._state, event)
        for decision in decisions:
            self._apply(decision)

    def _apply(self, decision: RenderDecision) -> None:
        if isinstance(decision, (RenderToolCall, StreamToolOutput, RenderToolResult)):
            self._apply_tool_decision(decision)
        else:
            self._apply_assistant_decision(decision)

    def _apply_assistant_decision(
        self,
        decision: (
            StartAssistantMessage
            | StreamAssistantText
            | StreamAssistantReasoning
            | RenderBufferedAssistantText
            | CompleteAssistantMessage
            | FailAssistantMessage
            | CancelAssistantMessage
        ),
    ) -> None:
        renderer = self._renderer
        if isinstance(decision, StartAssistantMessage):
            renderer.start_assistant_message()
        elif isinstance(decision, StreamAssistantText):
            renderer.stream_sink(decision.text)
        elif isinstance(decision, StreamAssistantReasoning):
            renderer.reasoning_sink(decision.text)
        elif isinstance(decision, RenderBufferedAssistantText):
            renderer.render_buffered_assistant_text(
                decision.text, has_tool_calls=decision.has_tool_calls
            )
        elif isinstance(decision, CompleteAssistantMessage):
            renderer.complete_assistant_message(has_tool_calls=decision.has_tool_calls)
        elif isinstance(decision, FailAssistantMessage):
            renderer.fail_assistant_message()
        elif isinstance(decision, CancelAssistantMessage):
            renderer.cancel_assistant_message(decision.reason)
        else:
            assert_never(decision)

    def _apply_tool_decision(
        self, decision: RenderToolCall | StreamToolOutput | RenderToolResult
    ) -> None:
        renderer = self._renderer
        if isinstance(decision, RenderToolCall):
            renderer.render_tool_call(decision.call)
        elif isinstance(decision, StreamToolOutput):
            renderer.tool_output_sink(decision.text)
        elif isinstance(decision, RenderToolResult):
            renderer.render_tool_result(
                output_text=decision.output_text,
                is_error=decision.is_error,
                duration_seconds=decision.duration_seconds,
            )
        else:
            assert_never(decision)
