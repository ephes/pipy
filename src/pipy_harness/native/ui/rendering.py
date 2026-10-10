"""Rendering adapter that drives a renderer from canonical agent events.

The adapter owns no display state or event branching of its own: it holds a
:class:`UiState`, delegates every canonical event to the pure :func:`reduce`,
and applies the returned decisions to the renderer.  It is a thin driver — the
reducer is the single owner of the agent-event-to-render-decision mapping,
including the tool-call, tool-update, and tool-result renders.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, assert_never, runtime_checkable

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentEvent,
    AgentToolCall,
    NestedToolCallCompleted,
    NestedToolCallStarted,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.ui.state import (
    CancelAssistantMessage,
    CompleteAssistantMessage,
    FailAssistantMessage,
    FinishRetry,
    RenderBufferedAssistantText,
    RenderBufferedAssistantThinking,
    RenderDecision,
    RenderNestedToolCall,
    RenderStoppedAssistant,
    RenderToolCall,
    RenderToolResult,
    ScheduleRetry,
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

    def render_buffered_thinking(self, text: str) -> None: ...

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None: ...

    def render_stopped_assistant(self, marker: str) -> None: ...

    def fail_assistant_message(self) -> None: ...

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None: ...

    def render_tool_call(
        self, call: AgentToolCall, *, display_only: bool = False
    ) -> None: ...

    def tool_output_sink(self, chunk: str) -> None: ...

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...


@runtime_checkable
class RetryEventRenderer(Protocol):
    """Optional renderer verbs for Pi's ``auto_retry_start``/``auto_retry_end``.

    Both product renderers implement them; a renderer without them simply does
    not show retries.
    """

    def schedule_retry(
        self, *, attempt: int, max_attempts: int, delay_ms: int, error_message: str
    ) -> None: ...

    def finish_retry(
        self, *, succeeded: bool, attempt: int, final_error: str | None
    ) -> None: ...


@runtime_checkable
class NestedEventRenderer(Protocol):
    """Optional live child verb; legacy mandatory renderers stay compatible."""

    def render_nested_tool_call(
        self, event: NestedToolCallStarted | NestedToolCallCompleted
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
        if isinstance(decision, RenderNestedToolCall):
            if isinstance(self._renderer, NestedEventRenderer):
                self._renderer.render_nested_tool_call(decision.event)
        elif isinstance(decision, (RenderToolCall, StreamToolOutput, RenderToolResult)):
            self._apply_tool_decision(decision)
        elif isinstance(decision, (ScheduleRetry, FinishRetry)):
            self._apply_retry_decision(decision)
        else:
            self._apply_assistant_decision(decision)

    def _apply_assistant_decision(
        self,
        decision: (
            StartAssistantMessage
            | StreamAssistantText
            | StreamAssistantReasoning
            | RenderBufferedAssistantText
            | RenderBufferedAssistantThinking
            | CompleteAssistantMessage
            | FailAssistantMessage
            | CancelAssistantMessage
            | RenderStoppedAssistant
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
        elif isinstance(decision, RenderBufferedAssistantThinking):
            renderer.render_buffered_thinking(decision.text)
        elif isinstance(decision, CompleteAssistantMessage):
            renderer.complete_assistant_message(has_tool_calls=decision.has_tool_calls)
        elif isinstance(decision, RenderStoppedAssistant):
            renderer.render_stopped_assistant(decision.marker)
        elif isinstance(decision, FailAssistantMessage):
            renderer.fail_assistant_message()
        elif isinstance(decision, CancelAssistantMessage):
            renderer.cancel_assistant_message(decision.reason)
        else:
            assert_never(decision)

    def _apply_retry_decision(self, decision: ScheduleRetry | FinishRetry) -> None:
        renderer = self._renderer
        if not isinstance(renderer, RetryEventRenderer):
            return
        if isinstance(decision, ScheduleRetry):
            renderer.schedule_retry(
                attempt=decision.attempt,
                max_attempts=decision.max_attempts,
                delay_ms=decision.delay_ms,
                error_message=decision.error_message,
            )
        else:
            renderer.finish_retry(
                succeeded=decision.succeeded,
                attempt=decision.attempt,
                final_error=decision.final_error,
            )

    def _apply_tool_decision(
        self, decision: RenderToolCall | StreamToolOutput | RenderToolResult
    ) -> None:
        renderer = self._renderer
        if isinstance(decision, RenderToolCall):
            renderer.render_tool_call(decision.call, display_only=decision.display_only)
        elif isinstance(decision, StreamToolOutput):
            renderer.tool_output_sink(decision.text)
        elif isinstance(decision, RenderToolResult):
            renderer.render_tool_result(
                output_text=decision.output_text,
                is_error=decision.is_error,
                duration_seconds=decision.duration_seconds,
                details=decision.details,
            )
        else:
            assert_never(decision)
