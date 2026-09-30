"""Session JSON of an assistant message's ordered content (Pi ``content``).

A stored assistant message keeps ``content`` (its text) and ``tool_calls``.
When its ordered content holds more than that layout (thinking blocks,
provider signatures, text after a tool call), ``blocks`` lists it in Pi's
shape: ``{"type":"text","text","textSignature"?}``,
``{"type":"thinking","thinking","thinkingSignature"?,"redacted"?}`` and
``{"type":"toolCall","index"}``, a position in ``tool_calls`` so arguments are
stored once.
"""

from __future__ import annotations

from typing import Any

from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentContentBlock,
    AgentToolCall,
)


def assistant_blocks_to_json(message: AgentAssistantMessage) -> list[dict[str, Any]]:
    """The message's ``blocks`` in the session shape."""

    # The message's invariant: its tool-call blocks are ``tool_calls`` in order.
    call_index = 0
    stored: list[dict[str, Any]] = []
    for block in message.blocks:
        if isinstance(block, TextContent):
            entry: dict[str, Any] = {"type": "text", "text": block.text}
            if block.signature is not None:
                entry["textSignature"] = block.signature
        elif isinstance(block, ThinkingContent):
            entry = {"type": "thinking", "thinking": block.thinking}
            if block.signature is not None:
                entry["thinkingSignature"] = block.signature
            if block.redacted:
                entry["redacted"] = True
        else:
            entry = {"type": "toolCall", "index": call_index}
            call_index += 1
        stored.append(entry)
    return stored


def assistant_blocks_from_json(
    raw: object, tool_calls: tuple[AgentToolCall, ...]
) -> tuple[AgentContentBlock, ...]:
    """Read ``blocks`` back; an entry without them is the default layout."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("assistant blocks must be a list")
    blocks: list[AgentContentBlock] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("assistant block must be an object")
        kind = entry.get("type")
        if kind == "text":
            blocks.append(
                TextContent(_string(entry, "text"), _optional(entry, "textSignature"))
            )
        elif kind == "thinking":
            redacted = entry.get("redacted", False)
            if type(redacted) is not bool:
                raise ValueError("assistant thinking redacted must be a bool")
            blocks.append(
                ThinkingContent(
                    _string(entry, "thinking"),
                    _optional(entry, "thinkingSignature"),
                    redacted,
                )
            )
        elif kind == "toolCall":
            index = entry.get("index")
            if type(index) is not int or not 0 <= index < len(tool_calls):
                raise ValueError("assistant toolCall block index is invalid")
            blocks.append(tool_calls[index])
        else:
            raise ValueError(f"unsupported assistant block type: {kind!r}")
    return tuple(blocks)


def _string(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if type(value) is not str:
        raise ValueError(f"assistant block {key} must be a string")
    return value


def _optional(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if value is not None and type(value) is not str:
        raise ValueError(f"assistant block {key} must be a string")
    return value
