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
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.messages import AgentContentBlock
from pipy_harness.native.agent.system_messages import system_message_to_json
from pipy_harness.native.agent.usage_json import usage_to_json
from pipy_harness.native.automation.jsonl import loads_strict


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
            "role": "toolResult",
            "toolCallId": message.provider_correlation_id,
            "content": [{"type": "text", "text": message.content.value}],
            "isError": message.is_error,
        }
    raise TypeError(f"unserializable loop message: {type(message)!r}")
