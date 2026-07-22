"""Pure UI state and rendering-adapter boundary for the native runtime.

This package owns the deterministic assistant message-lifecycle decision
machine (:mod:`pipy_harness.native.ui.state`) and the rendering adapter that
applies its decisions to a concrete renderer
(:mod:`pipy_harness.native.ui.rendering`).  It consumes canonical agent events
and performs no terminal I/O in the reducer; the adapter is the only I/O edge.
"""

from __future__ import annotations

from pipy_harness.native.ui.rendering import (
    AgentEventRenderer,
    RenderingAgentEventAdapter,
)
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

__all__ = [
    "AgentEventRenderer",
    "CancelAssistantMessage",
    "CompleteAssistantMessage",
    "FailAssistantMessage",
    "RenderBufferedAssistantText",
    "RenderDecision",
    "RenderingAgentEventAdapter",
    "StartAssistantMessage",
    "StreamAssistantReasoning",
    "StreamAssistantText",
    "UiState",
    "reduce",
]
