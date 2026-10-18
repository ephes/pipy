"""Buffered Chat reasoning capture/replay (Pi openai-completions, c268154ca).

Completed reasoning_details keep their original order and JSON fields. Pi's
stream-only detail merging is deliberately not used here. New capture stores
replay data on ThinkingContent; tool thought signatures are import-only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.messages import AgentContentBlock, AgentToolCall

REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")


def is_reasoning_detail(value: object) -> TypeGuard[dict[str, Any]]:
    """Pi's finite reasoning detail types, including its common fields."""
    if not isinstance(value, dict):
        return False
    if value.get("id") is not None and not isinstance(value["id"], str):
        return False
    if "format" in value and not isinstance(value["format"], str):
        return False
    if "index" in value and type(value["index"]) not in (int, float):
        return False
    kind = value.get("type")
    if not isinstance(kind, str):
        return False
    if kind == "reasoning.text":
        return isinstance(value.get("text"), str) and (
            value.get("signature") is None or isinstance(value["signature"], str)
        )
    field = {"reasoning.summary": "summary", "reasoning.encrypted": "data"}.get(kind)
    return field is not None and isinstance(value.get(field), str)


def capture_thinking(
    message: Mapping[str, Any], provider: str
) -> tuple[ThinkingContent, ...]:
    """First nonempty scalar is display text; valid details are signature only."""
    text, signature = "", None
    for field in REASONING_FIELDS:
        value = message.get(field)
        if isinstance(value, str) and value:
            text = value
            signature = (
                "reasoning_content"
                if provider == "opencode-go" and field == "reasoning"
                else field
            )
            break
    raw = message.get("reasoning_details")
    details = (
        [item for item in raw if is_reasoning_detail(item)]
        if isinstance(raw, list)
        else []
    )
    if details:
        signature = json.dumps(details, ensure_ascii=False)
    return (ThinkingContent(text, signature),) if signature is not None else ()


def _signature_value(signature: str | None) -> Any:
    if not signature:
        return None
    try:
        return json.loads(signature)
    except ValueError:
        return None


def preserved_details(blocks: Sequence[AgentContentBlock]) -> list[dict[str, Any]]:
    """First wholly valid thinking array, else ordered legacy encrypted tools."""
    for block in blocks:
        if isinstance(block, ThinkingContent):
            value = _signature_value(block.signature)
            if (
                isinstance(value, list)
                and value
                and all(is_reasoning_detail(v) for v in value)
            ):
                return value
    legacy = []
    for block in blocks:
        if not isinstance(block, AgentToolCall):
            continue
        value = _signature_value(block.thought_signature)
        if (
            is_reasoning_detail(value)
            and value["type"] == "reasoning.encrypted"
            and isinstance(value.get("id"), str)
            and value["id"]
            and value["data"]
        ):
            legacy.append(value)
    return legacy


def assistant_fields(
    blocks: Sequence[AgentContentBlock],
    *,
    provider: str | None,
    thinking_as_text: bool,
    required_reasoning_content: bool,
) -> dict[str, Any]:
    """Reasoning projection after the caller's exact-target transform."""
    texts = [b.text for b in blocks if isinstance(b, TextContent) and b.text.strip()]
    thinking = [
        b for b in blocks if isinstance(b, ThinkingContent) and b.thinking.strip()
    ]
    details = preserved_details(blocks)
    fields: dict[str, Any] = {"content": "".join(texts)}
    if thinking and thinking_as_text:
        fields["content"] = [
            {"type": "text", "text": "\n\n".join(b.thinking for b in thinking)},
            *({"type": "text", "text": text} for text in texts),
        ]
    elif thinking and not details:
        signature = thinking[0].signature
        if provider == "opencode-go" and signature == "reasoning":
            signature = "reasoning_content"
        if isinstance(signature, str) and signature in REASONING_FIELDS:
            fields[signature] = "\n".join(b.thinking for b in thinking)
    if details:
        fields["reasoning_details"] = details
    if required_reasoning_content:
        fields.setdefault("reasoning_content", "")
    return fields
