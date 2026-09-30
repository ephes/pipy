"""Pi's ordered content from OpenAI Responses output items.

Port of the content assembly in ``processResponsesStream``
(``packages/ai/src/api/openai-responses-shared.ts``), shared by the OpenAI
and Azure adapters (one non-streamed body, :func:`output_blocks`) and the
Codex adapter (a stream of events, :class:`ResponsesStreamAssembler`):

- a ``reasoning`` item is a thinking block: its summary texts joined by a
  blank line (else its content texts, else what streamed), with the whole
  item as JSON for its signature, so the same model gets it back verbatim
  (``encrypted_content`` included);
- a ``message`` item is a text block: its output text (or refusal), with
  ``{"v":1,"id",phase?}`` as its signature (Pi ``TextSignatureV1``);
- a ``function_call`` item is a tool call whose id is Pi's compound
  ``call_id|id``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.models import ProviderContentBlock, ProviderToolCall

_PHASES = frozenset({"commentary", "final_answer"})


def encode_text_signature(item_id: str, phase: object = None) -> str:
    """Pi ``encodeTextSignatureV1``."""

    payload: dict[str, object] = {"v": 1, "id": item_id}
    if isinstance(phase, str) and phase:
        payload["phase"] = phase
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def parse_text_signature(signature: str | None) -> tuple[str, str | None] | None:
    """Pi ``parseTextSignature``: ``(id, phase)``; a legacy value is the id."""

    if not signature:
        return None
    if signature.startswith("{"):
        try:
            parsed = json.loads(signature)
        except ValueError:
            parsed = None
        if (
            isinstance(parsed, dict)
            and parsed.get("v") == 1
            and isinstance(parsed.get("id"), str)
        ):
            phase = parsed.get("phase")
            return parsed["id"], phase if phase in _PHASES else None
    return signature, None


def _item_json(item: Mapping[str, Any]) -> str:
    """``JSON.stringify(item)``."""

    return json.dumps(item, separators=(",", ":"), ensure_ascii=False)


def _texts(parts: object, key: str = "text") -> list[str]:
    if not isinstance(parts, list):
        return []
    return [
        part[key]
        for part in parts
        if isinstance(part, Mapping) and isinstance(part.get(key), str)
    ]


def reasoning_block(item: Mapping[str, Any], streamed: str = "") -> ThinkingContent:
    summary = "\n\n".join(_texts(item.get("summary")))
    content = "\n\n".join(_texts(item.get("content")))
    return ThinkingContent(summary or content or streamed, _item_json(item))


def message_block(item: Mapping[str, Any], streamed: str = "") -> TextContent:
    content = item.get("content")
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if not isinstance(part, Mapping):
                continue
            key = "text" if part.get("type") == "output_text" else "refusal"
            value = part.get(key)
            if isinstance(value, str):
                chunks.append(value)
        text = "".join(chunks)
    else:
        text = streamed
    item_id = item.get("id")
    signature = (
        encode_text_signature(item_id, item.get("phase"))
        if isinstance(item_id, str) and item_id
        else None
    )
    return TextContent(text, signature)


def _correlation(call_id: object, item_id: object) -> str | None:
    call = call_id if isinstance(call_id, str) and call_id else None
    item = item_id if isinstance(item_id, str) and item_id else None
    if call and item:
        return f"{call}|{item}"
    return call or item


def _tool_call(
    correlation: str | None, name: object, arguments: object
) -> ProviderToolCall | None:
    if not correlation or not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, Mapping):
        arguments = json.dumps(arguments, sort_keys=True)
    if not isinstance(arguments, str):
        arguments = ""
    try:
        return ProviderToolCall(
            provider_correlation_id=correlation[
                : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
            ],
            tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
            arguments_json=arguments[: ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH],
        )
    except ValueError:
        return None


def output_blocks(
    output: object, *, provider_prefix: str, output_text: object = None
) -> tuple[ProviderContentBlock, ...]:
    """Pi's ordered content of a non-streamed Responses ``output`` list.

    A body whose output holds no message item but a top-level
    ``output_text`` gets that text as a block before its tool calls.
    """

    blocks: list[ProviderContentBlock] = []
    if isinstance(output, list):
        for index, item in enumerate(output):
            if not isinstance(item, Mapping):
                continue
            kind = item.get("type")
            if kind == "reasoning":
                blocks.append(reasoning_block(item))
            elif kind in (None, "message"):
                blocks.append(message_block(item))
            elif kind == "function_call":
                call = _tool_call(
                    _correlation(item.get("call_id"), item.get("id"))
                    or f"{provider_prefix}-tool-{index}",
                    item.get("name"),
                    item.get("arguments"),
                )
                if call is not None:
                    blocks.append(call)
    if isinstance(output_text, str) and output_text:
        if not any(isinstance(block, TextContent) and block.text for block in blocks):
            position = next(
                (
                    i
                    for i, block in enumerate(blocks)
                    if isinstance(block, ProviderToolCall)
                ),
                len(blocks),
            )
            blocks.insert(position, TextContent(output_text))
    return tuple(blocks)


def joined_text(blocks: tuple[ProviderContentBlock, ...]) -> str | None:
    text = "".join(block.text for block in blocks if isinstance(block, TextContent))
    return text or None


@dataclass(slots=True)
class _Slot:
    """One output item being streamed (Pi ``ResponsesOutputSlot``)."""

    kind: str  # "thinking" | "text" | "call"
    item_id: str | None = None
    output_index: int | None = None
    chunks: list[str] = field(default_factory=list)
    call_id: str | None = None
    name: str | None = None
    final_arguments: str | None = None
    done: ThinkingContent | TextContent | None = None
    closed: bool = False

    def block(self) -> ProviderContentBlock | None:
        if self.done is not None:
            return self.done
        streamed = "".join(self.chunks)
        if self.kind == "thinking":
            return ThinkingContent(streamed)
        if self.kind == "text":
            return TextContent(streamed)
        arguments = (
            self.final_arguments if self.final_arguments is not None else streamed
        )
        return _tool_call(
            _correlation(self.call_id, self.item_id), self.name, arguments
        )


_ITEM_KINDS = {"reasoning": "thinking", "message": "text", "function_call": "call"}


class ResponsesStreamAssembler:
    """Pi ``processResponsesStream``'s output slots for a Responses stream.

    Events are correlated like Pi: by ``output_index`` first, then by item
    id; a delta with neither falls back to the one open slot of its kind
    (text and reasoning deltas open an anonymous slot when there is none),
    and an item event with an unknown identity may adopt the one open
    anonymous slot of its kind.
    """

    __slots__ = ("_by_id", "_by_index", "_slots")

    def __init__(self) -> None:
        self._slots: list[_Slot] = []
        self._by_index: dict[int, _Slot] = {}
        self._by_id: dict[str, _Slot] = {}

    def item_added(self, event: Mapping[str, Any]) -> None:
        item = event.get("item")
        if isinstance(item, Mapping):
            slot = self._find(event, item)
            if slot is None:
                slot = self._open(event, item)
            if slot is not None and slot.kind == "call":
                self._merge_call(slot, item)
                arguments = item.get("arguments")
                if isinstance(arguments, str) and arguments and not slot.chunks:
                    slot.chunks.append(arguments)

    def delta(self, event: Mapping[str, Any], kind: str, delta: str) -> None:
        slot = self._find(event, None, kind)
        if slot is None:
            item_id = _str(event.get("item_id"))
            if kind == "call" and item_id is None:
                return
            slot = self._new(kind, item_id, _index(event))
        slot.chunks.append(delta)

    def arguments_done(self, event: Mapping[str, Any]) -> None:
        slot = self._find(event, None, "call")
        item_id = _str(event.get("item_id"))
        if slot is None and item_id is not None:
            # An early done event reserves the call's place, as a delta does.
            slot = self._new("call", item_id, _index(event))
        arguments = event.get("arguments")
        if slot is not None and isinstance(arguments, str):
            slot.final_arguments = arguments

    def item_done(self, event: Mapping[str, Any]) -> None:
        item = event.get("item")
        if not isinstance(item, Mapping):
            return
        slot = self._find(event, item) or self._open(event, item)
        if slot is None:
            return
        streamed = "".join(slot.chunks)
        if slot.kind == "thinking":
            slot.done = reasoning_block(item, streamed)
        elif slot.kind == "text":
            slot.done = message_block(item, streamed)
        else:
            self._merge_call(slot, item)
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                slot.final_arguments = arguments
        slot.closed = True

    def backfill(self, response: Mapping[str, Any] | None) -> None:
        """Pi ``backfillReasoningSignatures``: take ``encrypted_content`` the
        done event omitted from the terminal response's output."""

        output = response.get("output") if response is not None else None
        if not isinstance(output, list):
            return
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "reasoning":
                continue
            encrypted = item.get("encrypted_content")
            slot = self._by_id.get(_str(item.get("id")) or "")
            if (
                not encrypted
                or slot is None
                or not isinstance(slot.done, ThinkingContent)
            ):
                continue
            stored = json.loads(slot.done.signature or "{}")
            if stored.get("encrypted_content"):
                continue
            stored["encrypted_content"] = encrypted
            slot.done = ThinkingContent(slot.done.thinking, _item_json(stored))

    def blocks(self) -> tuple[ProviderContentBlock, ...]:
        """The content so far, in item order (partial items included)."""

        return tuple(
            block for slot in self._slots if (block := slot.block()) is not None
        )

    def _find(
        self,
        event: Mapping[str, Any],
        item: Mapping[str, Any] | None,
        kind: str | None = None,
    ) -> _Slot | None:
        if item is not None:
            kind = _ITEM_KINDS.get(str(item.get("type") or "message"))
            if kind is None:
                return None
        index = _index(event)
        if index is not None and index in self._by_index:
            slot = self._by_index[index]
            return slot if slot.kind == kind else None
        item_id = (
            _str(item.get("id")) if item is not None else _str(event.get("item_id"))
        )
        if item_id is not None and item_id in self._by_id:
            slot = self._by_id[item_id]
            return slot if slot.kind == kind else None
        if item is None and (index is not None or item_id is not None):
            # A delta naming an unknown item belongs to a new slot, never to
            # another item's (only an event with no identity falls back).
            return None
        open_slots = [
            slot
            for slot in self._slots
            if slot.kind == kind
            and not slot.closed
            and (item is None or (slot.item_id is None and slot.output_index is None))
        ]
        if len(open_slots) != 1:
            return None
        slot = open_slots[0]
        if item_id is not None and slot.item_id is None:
            slot.item_id = item_id
            self._by_id[item_id] = slot
        return slot

    def _open(self, event: Mapping[str, Any], item: Mapping[str, Any]) -> _Slot | None:
        kind = _ITEM_KINDS.get(str(item.get("type") or "message"))
        if kind is None:
            return None
        return self._new(kind, _str(item.get("id")), _index(event))

    def _new(self, kind: str, item_id: str | None, index: int | None) -> _Slot:
        slot = _Slot(kind, item_id, index)
        self._slots.append(slot)
        if index is not None:
            self._by_index[index] = slot
        if item_id is not None:
            self._by_id[item_id] = slot
        return slot

    @staticmethod
    def _merge_call(slot: _Slot, item: Mapping[str, Any]) -> None:
        slot.call_id = slot.call_id or _str(item.get("call_id"))
        slot.name = slot.name or _str(item.get("name"))


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _index(event: Mapping[str, Any]) -> int | None:
    value = event.get("output_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
