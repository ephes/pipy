"""Mistral's ordered, unsigned native thinking content at the Chat endpoint.

Pinned Pi mistral-conversations.ts:643-705,842-871. Routing remains ``mistral``;
canonical replay provenance is ``mistral-conversations``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.messages import AgentContentBlock

MISTRAL_CONVERSATIONS_API = "mistral-conversations"


def _chunk_text(item: Any) -> tuple[bool, Any]:
    if isinstance(item, str):
        return False, item
    if not isinstance(item, Mapping):
        return False, None
    if item.get("type") == "text":
        return False, item.get("text")
    if item.get("type") == "thinking":
        parts = item.get("thinking")
        if isinstance(parts, list):
            return True, "".join(
                part["text"]
                for part in parts
                if isinstance(part, Mapping) and isinstance(part.get("text"), str)
            )
    return False, None


def capture_content(value: Any) -> tuple[TextContent | ThinkingContent, ...]:
    """Capture native chunks in order, ignoring empty/unsupported items.

    Empty chunks do not split adjacent blocks. Native thinking has no signature;
    generic scalar reasoning and opaque reasoning details are outside this wire.
    """
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        return ()
    blocks: list[TextContent | ThinkingContent] = []
    for item in items:
        thinking, text = _chunk_text(item)
        if not isinstance(text, str) or not text:
            continue
        if thinking:
            if blocks and isinstance(blocks[-1], ThinkingContent):
                text = blocks[-1].thinking + text
                blocks.pop()
            blocks.append(ThinkingContent(text))
        else:
            if blocks and isinstance(blocks[-1], TextContent):
                text = blocks[-1].text + text
                blocks.pop()
            blocks.append(TextContent(text))
    return tuple(blocks)


def assistant_fields(blocks: Sequence[AgentContentBlock]) -> dict[str, Any]:
    """Serialize already target-projected native content without opaque signatures."""
    parts: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextContent) and block.text.strip():
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent) and block.thinking.strip():
            parts.append(
                {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": block.thinking}],
                }
            )
    return {"content": parts, "prefix": False}
