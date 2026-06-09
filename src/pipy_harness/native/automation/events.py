"""Pi-shaped session-event vocabulary emitted by the real tool loop.

``AutomationEmitter`` is the seam between pipy's native tool loop and the
Pi-compatible ``AgentSessionEvent`` JSON stream (`docs/automation-rpc.md` §a).
The loop calls semantic methods at its real lifecycle points; the emitter
builds the Pi-shaped event dict and forwards it to a sink. When no sink is
attached every method is a cheap no-op, so the interactive/TUI path is
unchanged.

The event vocabulary mirrors Pi's ``AgentEvent`` (`packages/agent/src/
types.ts`) plus the session-extension events of ``AgentSessionEvent``
(`packages/coding-agent/src/core/agent-session.ts`):

base lifecycle
    ``agent_start``/``agent_end``, ``turn_start``/``turn_end``,
    ``message_start``/``message_update``/``message_end``,
    ``tool_execution_start``/``tool_execution_update``/``tool_execution_end``
session extension
    ``queue_update``, ``compaction_start``/``compaction_end``,
    ``session_info_changed``, ``thinking_level_changed``,
    ``auto_retry_start``/``auto_retry_end``
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.automation.serialize import (
    parse_tool_arguments,
    serialize_message,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
)


@runtime_checkable
class AutomationEventSink(Protocol):
    """Receives one Pi-shaped session event at a time.

    Implementations serialize to JSONL stdout (``--mode json``/``--mode rpc``)
    or collect events in tests. ``emit`` must be safe to call from the loop
    thread; a JSONL sink serializes writes through a single writer.
    """

    def emit(self, event: dict[str, Any]) -> None: ...


class AutomationEmitter:
    """Builds Pi-shaped events from the real loop's lifecycle calls."""

    def __init__(self, sink: AutomationEventSink | None) -> None:
        self._sink = sink
        self._partial_text = ""

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def _emit(self, event: dict[str, Any]) -> None:
        if self._sink is not None:
            self._sink.emit(event)

    # -- agent / turn lifecycle ------------------------------------------
    def agent_start(self) -> None:
        self._emit({"type": "agent_start"})

    def agent_end(
        self, messages: Sequence[LoopMessage], *, will_retry: bool = False
    ) -> None:
        self._emit(
            {
                "type": "agent_end",
                "messages": [serialize_message(message) for message in messages],
                "willRetry": will_retry,
            }
        )

    def turn_start(self) -> None:
        self._emit({"type": "turn_start"})

    def non_streamed_message(self, message: LoopMessage) -> None:
        """Emit ``message_start`` then ``message_end`` for a non-streamed message.

        Used for the user message (and could carry tool-result messages): Pi
        emits a ``message_start``/``message_end`` pair for the user message after
        ``turn_start`` and before the assistant message begins. Non-streamed
        messages carry their full content in both events.
        """

        serialized = serialize_message(message)
        self._emit({"type": "message_start", "message": serialized})
        self._emit({"type": "message_end", "message": serialized})

    def turn_end(
        self, message: AssistantMessage, tool_results: Sequence[ToolResultMessage]
    ) -> None:
        self._emit(
            {
                "type": "turn_end",
                "message": serialize_message(message),
                "toolResults": [serialize_message(result) for result in tool_results],
            }
        )

    # -- assistant message streaming -------------------------------------
    def assistant_message_start(self) -> None:
        self._partial_text = ""
        self._emit({"type": "message_start", "message": {"role": "assistant", "content": []}})

    def assistant_text_delta(self, delta: str) -> None:
        if self._sink is None:
            return
        self._partial_text += delta
        partial = {
            "role": "assistant",
            "content": [{"type": "text", "text": self._partial_text}],
        }
        self._emit(
            {
                "type": "message_update",
                "message": partial,
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "contentIndex": 0,
                    "delta": delta,
                    "partial": partial,
                },
            }
        )

    def assistant_message_end(self, message: AssistantMessage) -> None:
        self._emit({"type": "message_end", "message": serialize_message(message)})

    # -- tool execution --------------------------------------------------
    def tool_execution_start(self, call: ProviderToolCall) -> None:
        self._emit(
            {
                "type": "tool_execution_start",
                "toolCallId": call.provider_correlation_id,
                "toolName": call.tool_name,
                "args": parse_tool_arguments(call.arguments_json),
            }
        )

    def tool_execution_update(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        partial_result: str,
    ) -> None:
        self._emit(
            {
                "type": "tool_execution_update",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "args": args,
                "partialResult": partial_result,
            }
        )

    def tool_execution_end(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        result: str,
        is_error: bool,
    ) -> None:
        self._emit(
            {
                "type": "tool_execution_end",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "result": result,
                "isError": is_error,
            }
        )

    # -- session-extension events ----------------------------------------
    def queue_update(
        self, steering: Sequence[str], follow_up: Sequence[str]
    ) -> None:
        self._emit(
            {
                "type": "queue_update",
                "steering": list(steering),
                "followUp": list(follow_up),
            }
        )

    def compaction_start(self, reason: str) -> None:
        self._emit({"type": "compaction_start", "reason": reason})

    def compaction_end(
        self,
        *,
        reason: str,
        result: dict[str, Any] | None,
        aborted: bool = False,
        will_retry: bool = False,
        error_message: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "compaction_end",
            "reason": reason,
            "result": result,
            "aborted": aborted,
            "willRetry": will_retry,
        }
        if error_message is not None:
            event["errorMessage"] = error_message
        self._emit(event)

    def session_info_changed(self, name: str | None) -> None:
        self._emit({"type": "session_info_changed", "name": name})

    def thinking_level_changed(self, level: str) -> None:
        self._emit({"type": "thinking_level_changed", "level": level})

    def auto_retry_start(
        self, *, attempt: int, max_attempts: int, delay_ms: int, error_message: str
    ) -> None:
        self._emit(
            {
                "type": "auto_retry_start",
                "attempt": attempt,
                "maxAttempts": max_attempts,
                "delayMs": delay_ms,
                "errorMessage": error_message,
            }
        )

    def auto_retry_end(
        self, *, success: bool, attempt: int, final_error: str | None = None
    ) -> None:
        event: dict[str, Any] = {
            "type": "auto_retry_end",
            "success": success,
            "attempt": attempt,
        }
        if final_error is not None:
            event["finalError"] = final_error
        self._emit(event)
