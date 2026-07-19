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
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
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
    return {
        "type": "toolCall",
        "id": call.provider_correlation_id,
        "name": call.tool_name,
        "arguments": parse_tool_arguments(call.arguments_json.value),
    }


def assistant_content_blocks(
    content: str, tool_calls: tuple[AgentToolCall, ...]
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if content:
        blocks.append({"type": "text", "text": content})
    blocks.extend(tool_call_block(call) for call in tool_calls)
    return blocks


def serialize_message(message: AgentMessage) -> dict[str, Any]:
    """Map one native loop message to its Pi-shaped JSON object."""

    if isinstance(message, AgentUserMessage):
        return {
            "role": "user",
            "content": [{"type": "text", "text": message.content.value}],
        }
    if isinstance(message, AgentAssistantMessage):
        return {
            "role": "assistant",
            "content": assistant_content_blocks(
                message.content.value, message.tool_calls
            ),
        }
    if isinstance(message, AgentToolResultMessage):
        return {
            "role": "toolResult",
            "toolCallId": message.provider_correlation_id,
            "content": [{"type": "text", "text": message.content.value}],
            "isError": message.is_error,
        }
    raise TypeError(f"unserializable loop message: {type(message)!r}")
