"""Serialize canonical agent messages onto Pi's JSON message shapes.

Pipy projects its provider-neutral agent dataclasses onto the same role/content
discriminators Pi uses (`packages/ai/src/types.ts`). Exact
byte-for-byte field matching with Pi is not the gate (see
``docs/automation-rpc.md`` Verification); matching role/content discriminators
and full-content presence is.

This is a full-content surface: assistant text, tool-call arguments, and tool
results are emitted verbatim. Auth secrets/tokens never reach these dataclasses
(they live in the provider/auth layer), so nothing here can leak them.
"""

from __future__ import annotations

from typing import Any

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentTranscriptMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ProductContent,
    TextContent,
    ThinkingContent,
    ToolPartialResult,
)
from pipy_harness.native.agent.messages import AgentContentBlock
from pipy_harness.native.agent.system_messages import system_message_to_json
from pipy_harness.native.agent.usage_json import usage_to_json
from pipy_harness.native.assistant_blocks_json import tool_result_blocks_to_json
from pipy_harness.native.automation.jsonl import loads_strict


def nested_call_details(message: AgentToolResultMessage) -> dict[str, Any]:
    """The ``details`` of a result that carries a nested-call record.

    Every calling tool's result has the record (``codemode`` and an
    extension tool that ran others with ``ctx.execute_tool``), as Pi's
    ``_handleAgentEvent`` puts ``nestedCalls`` on every tool result message
    its recorder has a summary for. Other results keep their details to
    ``tool_execution_end``.
    """
    details = message.details
    if (
        details is not None
        and type(record := details.get("nestedCalls")) is dict
        and type(record.get("calls")) is list
        and type(record.get("complete")) is bool
    ):
        return {"details": dict(details)}
    return {}


def tool_result_content(message: AgentToolResultMessage) -> list[dict[str, Any]]:
    """Pi's ``content`` of a tool result: one text block, or the ordered
    text and image blocks when the result has an image."""

    return tool_result_blocks_to_json(message.blocks) or [
        {"type": "text", "text": message.content.value}
    ]


def tool_result_json(message: AgentToolResultMessage) -> dict[str, Any]:
    """Pi's ``AgentToolResult`` of a finished call (``tool_execution_end``
    ``result``): ``content``, ``details`` when the result has them, the
    tool's ``structuredContent`` when it gave one, and ``terminate`` when
    the result asks the agent to stop after the batch."""

    result: dict[str, Any] = {"content": tool_result_content(message)}
    if message.details is not None:
        result["details"] = dict(message.details)
    if message.structured_content is not NO_STRUCTURED_CONTENT:
        result["structuredContent"] = message.structured_content
    if message.terminate:
        result["terminate"] = True
    return result


def partial_result_json(update: ProductContent | ToolPartialResult) -> dict[str, Any]:
    """Pi's partial ``AgentToolResult`` (``tool_execution_update``
    ``partialResult``).

    A partial result keeps its ``details`` and replaces the previous one:
    an MCP tool's progress, or ``bash``'s output so far (Pi's
    ``emitOutputUpdate``). A bare chunk of text, which no built-in tool
    sends, is a partial result with that chunk as its text and empty
    ``details``.
    """

    if isinstance(update, ToolPartialResult):
        partial: dict[str, Any] = {
            "content": [{"type": "text", "text": update.content}]
        }
        if update.details is not None:
            partial["details"] = dict(update.details)
        return partial
    return {"content": [{"type": "text", "text": update.value}], "details": {}}


def duration_ms(execution_seconds: float | None) -> dict[str, int]:
    """Pi's ``durationMs`` field: how long the tool ran, in whole
    milliseconds; absent when it never started."""

    if execution_seconds is None:
        return {}
    return {"durationMs": round(execution_seconds * 1000)}


def parse_tool_arguments(arguments_json: str) -> Any:
    """Parse a tool call's raw JSON arguments into an object.

    Pi's ``tool_execution_start.args`` and the assistant ``toolCall.arguments``
    are parsed objects. When the model emitted malformed JSON we surface the
    raw string under ``_raw`` rather than dropping it (full-content surface).
    """

    try:
        # Strict parse: malformed JSON, or non-standard NaN/Infinity, falls back
        # to the raw string so non-finite floats never enter the emitted payload.
        return loads_strict(arguments_json)
    except (ValueError, TypeError):
        return {"_raw": arguments_json}


def tool_call_block(call: AgentToolCall) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "toolCall",
        "id": call.provider_correlation_id,
        "name": call.tool_name,
        "arguments": parse_tool_arguments(call.arguments_json.value),
    }
    if call.thought_signature is not None:
        block["thoughtSignature"] = call.thought_signature
    return block


def content_block(block: AgentContentBlock) -> dict[str, Any]:
    """One Pi content block: text, thinking or a tool call."""

    if isinstance(block, TextContent):
        text: dict[str, Any] = {"type": "text", "text": block.text}
        if block.signature is not None:
            text["textSignature"] = block.signature
        return text
    if isinstance(block, ThinkingContent):
        thinking: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
        if block.signature is not None:
            thinking["thinkingSignature"] = block.signature
        if block.redacted:
            thinking["redacted"] = True
        return thinking
    return tool_call_block(block)


def assistant_content_blocks(message: AgentAssistantMessage) -> list[dict[str, Any]]:
    """Pi's ordered ``AssistantMessage.content``."""

    return [content_block(block) for block in message.ordered_content()]


def assistant_stop_reason(message: AgentAssistantMessage) -> str:
    """Pi ``stopReason``: ``aborted``/``error`` when set, else how it ended."""

    if message.stop_reason is not None:
        return message.stop_reason.value
    return "toolUse" if message.tool_calls else "stop"


def serialize_message(message: AgentTranscriptMessage) -> dict[str, Any]:
    """Map one native loop message to its Pi-shaped JSON object."""

    if isinstance(message, AgentSystemMessage):
        return system_message_to_json(message)
    if isinstance(message, AgentUserMessage):
        return {
            "role": "user",
            "content": [{"type": "text", "text": message.content.value}],
        }
    if isinstance(message, AgentAssistantMessage):
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_content_blocks(message),
        }
        if message.api is not None:
            assistant["api"] = message.api
        if message.provider is not None:
            assistant["provider"] = message.provider
        if message.model is not None:
            assistant["model"] = message.model
        if message.provider_thinking_level is not None:
            assistant["providerThinkingLevel"] = message.provider_thinking_level
        # Every Pi assistant message has ``usage``; one stored before usage
        # was recorded reads as zero.
        assistant["usage"] = usage_to_json(message.usage or AgentMessageUsage())
        assistant["stopReason"] = assistant_stop_reason(message)
        if message.error_message is not None:
            assistant["errorMessage"] = message.error_message
        return assistant
    if isinstance(message, AgentToolResultMessage):
        return {
            **nested_call_details(message),
            "role": "toolResult",
            "toolCallId": message.provider_correlation_id,
            # Pi's ordered text and image blocks when the result has an image.
            "content": tool_result_content(message),
            "isError": message.is_error,
        }
    raise TypeError(f"unserializable loop message: {type(message)!r}")
