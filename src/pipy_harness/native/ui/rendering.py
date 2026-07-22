"""Rendering adapter that drives a renderer from canonical agent events.

The adapter owns no lifecycle booleans of its own: it holds a :class:`UiState`,
delegates each assistant/provider/cancellation event to the pure
:func:`reduce`, and applies the returned decisions to the renderer.  Tool events
remain direct, stateless pass-throughs onto the renderer.
"""

from __future__ import annotations

from typing import Protocol, assert_never, runtime_checkable

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentEvent,
    AgentToolCall,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.ui.state import (
    CancelAssistantMessage,
    CompleteAssistantMessage,
    FailAssistantMessage,
    RenderBufferedAssistantText,
    RenderDecision,
    StartAssistantMessage,
    StreamAssistantReasoning,
    StreamAssistantText,
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
        if isinstance(event, ToolCallStarted):
            self._renderer.render_tool_call(event.call)
            return
        if isinstance(event, ToolCallCompleted):
            self._renderer.render_tool_result(
                output_text=event.result.content.value,
                is_error=event.result.is_error,
                duration_seconds=event.duration_seconds,
            )
            return
        if isinstance(event, ToolCallUpdated):
            self._renderer.tool_output_sink(event.update.value)
            return
        self._state, decisions = reduce(self._state, event)
        for decision in decisions:
            self._apply(decision)

    def _apply(self, decision: RenderDecision) -> None:
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
