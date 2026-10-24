"""Completed round order over real entries; physical completion order is not view order."""

from __future__ import annotations

from typing import Any, cast

from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
)

from .canonical_codec import decode_canonical
from .documents import Doc, define_doc
from .records import EntryRecord
from .tool_values import match_result, refuse

Tree = dict[str, Any]
ORDER_DOC: Doc[Tree] = define_doc(
    "pi.tool.round-order",
    1,
    "conversation",
    lambda: {"rounds": {}},
    "latest",
    "initial",
)
PREPARE_DOC: Doc[Tree] = define_doc("pi.generation.next", 1, "task", dict)


def _round_ids(
    by_id: dict[int, EntryRecord], key: object, ids: object
) -> tuple[int, list[int]]:
    if (
        type(key) is not str
        or not key.isdecimal()
        or str(int(key)) != key
        or type(ids) is not list
    ):
        refuse()
    parent = by_id.get(int(key))
    if parent is None or parent.to_tree()["kind"] != "pi.assistant":
        refuse()
    assistant = decode_canonical(cast(Tree, parent.to_tree())["model"][0])
    if type(assistant) is not AgentAssistantMessage or len(ids) != len(
        assistant.tool_calls
    ):
        refuse()
    for id, call in zip(ids, assistant.tool_calls, strict=True):
        if type(id) is not int or id not in by_id:
            refuse()
        child = cast(Tree, by_id[id].to_tree())
        if child["kind"] != "pi.tool-result":
            refuse()
        message = decode_canonical(child["model"][0])
        if type(message) is not AgentToolResultMessage:
            refuse()
        match_result(message, call)
    return parent.id, ids


def ordered_entries(
    entries: tuple[EntryRecord, ...], index: object
) -> tuple[EntryRecord, ...]:
    if (
        type(index) is not dict
        or set(index) != {"rounds"}
        or type(index["rounds"]) is not dict
    ):
        refuse()
    by_id = {entry.id: entry for entry in entries}
    grouped = {}
    used = set()
    for key, ids in index["rounds"].items():
        parent, ordered = _round_ids(by_id, key, ids)
        if len(set(ordered)) != len(ordered) or any(id in used for id in ordered):
            refuse()
        used.update(ordered)
        grouped[parent] = ordered
    result = []
    for entry in sorted(entries, key=lambda item: item.id):
        if entry.id in used:
            continue
        result.append(entry)
        result.extend(by_id[id] for id in grouped.get(entry.id, ()))
    return tuple(result)
