"""Project canonical agent events onto Pi's automation event vocabulary.

This adapter is intentionally stateful: Pi's ``message_update`` payload carries
the cumulative assistant text even though the canonical agent event carries
only the latest full-content delta.  It otherwise performs a synchronous,
one-event-at-a-time projection onto the existing ``AutomationEventSink``.

Canonical bookkeeping events without an existing Pi automation equivalent are
not emitted here.  In particular, RPC queue ownership and ``agent_settled``
remain at their current serialized mode boundaries.
"""

from __future__ import annotations

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentRunCompleted,
    AgentRunStarted,
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
from pipy_harness.native.automation.events import AutomationEventSink
from pipy_harness.native.automation.serialize import (
    parse_tool_arguments,
    serialize_message,
)


PiAutomationEvent = dict[str, object]


class AutomationAgentEventAdapter:
    """Synchronous canonical-event sink for Pi-shaped automation consumers."""

    def __init__(self, sink: AutomationEventSink) -> None:
        self._sink = sink
        self._partial_text = ""

    def emit(self, event: AgentEvent) -> None:
        """Project one canonical event before accepting the next event."""

        projected = self._project(event)
        if projected is not None:
            self._sink.emit(projected)

    def _project(self, event: AgentEvent) -> PiAutomationEvent | None:
        if isinstance(
            event,
            (
                AgentRunStarted,
                TurnStarted,
                MessageStarted,
                MessageCompleted,
                TurnCompleted,
                AgentRunCompleted,
            ),
        ):
            return self._project_run_turn_message(event)
        if isinstance(event, AssistantTextDelta):
            return self._project_assistant_delta(event)
        if isinstance(event, (ToolCallStarted, ToolCallUpdated, ToolCallCompleted)):
            return self._project_tool_execution(event)
        if isinstance(event, (RetryScheduled, RetryCompleted)):
            return self._project_retry(event)
        if isinstance(
            event,
            (
                AssistantReasoningDelta,
                UsageUpdated,
                SteeringConsumed,
                FollowUpConsumed,
                ProviderFailed,
                RunCancelled,
            ),
        ):
            return None
        raise TypeError(f"unsupported canonical agent event: {type(event)!r}")

    def _project_run_turn_message(self, event: AgentEvent) -> PiAutomationEvent:
        if isinstance(event, AgentRunStarted):
            return {"type": "agent_start"}
        if isinstance(event, TurnStarted):
            return {"type": "turn_start"}
        if isinstance(event, MessageStarted):
            if isinstance(event.message, AgentAssistantMessage):
                self._partial_text = ""
            return {
                "type": "message_start",
                "message": serialize_message(event.message),
            }
        if isinstance(event, MessageCompleted):
            return {
                "type": "message_end",
                "message": serialize_message(event.message),
            }
        if isinstance(event, TurnCompleted):
            return {
                "type": "turn_end",
                "message": serialize_message(event.message),
                "toolResults": [
                    serialize_message(result) for result in event.tool_results
                ],
            }
        assert isinstance(event, AgentRunCompleted)
        return {
            "type": "agent_end",
            "messages": [
                serialize_message(message) for message in event.result.messages
            ],
            "willRetry": event.result.will_retry,
        }

    def _project_assistant_delta(self, event: AssistantTextDelta) -> PiAutomationEvent:
        self._partial_text += event.delta.value
        partial = {
            "role": "assistant",
            "content": [{"type": "text", "text": self._partial_text}],
        }
        return {
            "type": "message_update",
            "message": partial,
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": event.delta.value,
                "partial": partial,
            },
        }

    @staticmethod
    def _project_tool_execution(event: AgentEvent) -> PiAutomationEvent:
        if isinstance(event, ToolCallStarted):
            return {
                "type": "tool_execution_start",
                "toolCallId": event.call.provider_correlation_id,
                "toolName": event.call.tool_name,
                "args": parse_tool_arguments(event.call.arguments_json.value),
            }
        if isinstance(event, ToolCallUpdated):
            return {
                "type": "tool_execution_update",
                "toolCallId": event.call.provider_correlation_id,
                "toolName": event.call.tool_name,
                "args": parse_tool_arguments(event.call.arguments_json.value),
                "partialResult": event.update.value,
            }
        assert isinstance(event, ToolCallCompleted)
        return {
            "type": "tool_execution_end",
            "toolCallId": event.result.provider_correlation_id,
            "toolName": event.result.tool_name,
            "result": event.result.content.value,
            "isError": event.result.is_error,
        }

    @staticmethod
    def _project_retry(event: AgentEvent) -> PiAutomationEvent:
        if isinstance(event, RetryScheduled):
            return {
                "type": "auto_retry_start",
                "attempt": event.attempt,
                "maxAttempts": event.max_attempts,
                "delayMs": event.delay_ms,
                "errorMessage": event.failure.message.value,
            }
        assert isinstance(event, RetryCompleted)
        retry_end: PiAutomationEvent = {
            "type": "auto_retry_end",
            "success": event.succeeded,
            "attempt": event.attempt,
        }
        if event.failure is not None:
            retry_end["finalError"] = event.failure.message.value
        return retry_end
