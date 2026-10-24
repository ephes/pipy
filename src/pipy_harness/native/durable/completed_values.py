"""Owned completed facts read from the existing guarded Session authority.

This is a replacement view, not an event log or a second usage accumulator.
All functions below run in one synchronous Session read-line job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn, cast

from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical
from .checkpoint_codec import decode_checkpoint
from .codec import OwnedJson
from .documents import Token, check_scope, resolve
from .generation import ROUND_DOC, USAGE_DOC
from .generation_tasks import GENERATION_NAME, GENERATION_VERSION
from .records import ConversationId, DocumentId, EntryId, EntryRecord, safe_integer
from .storage import EntryQuery, Storage, TaskQuery
from .tool_graph import _child_result_id, _round_calls, owned_child, result_value
from .tool_history import ORDER_DOC, ordered_entries

Tree = dict[str, Any]


class ProjectionError(ValueError):
    """Payload-free refusal; no material reconstruction or migration on read."""


def refuse() -> NoReturn:
    raise ProjectionError("Committed projection is incompatible") from None


@dataclass(frozen=True, slots=True, repr=False)
class CompletedMessage:
    conversation_id: int
    entry_id: int
    commit_seq: int
    by_task_id: int | None
    canonical: OwnedJson
    provider_outcome: OwnedJson | None = None
    tool_output: OwnedJson | None = None

    def decode(self) -> object:
        """Each consumer owns fresh decoded descendants."""
        return decode_canonical(self.canonical.thaw())


@dataclass(frozen=True, slots=True)
class ClosedRound:
    assistant_id: int
    result_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True, repr=False)
class TerminalOutcome:
    task_id: int
    kind: str
    version: int | float
    outcome: OwnedJson


@dataclass(frozen=True, slots=True, repr=False)
class CompletedSnapshot:
    conversation_id: int
    revision: int
    publication_seq: int | None
    messages: tuple[CompletedMessage, ...]
    rounds: tuple[ClosedRound, ...]
    outcomes: tuple[TerminalOutcome, ...]
    usage: OwnedJson | None


def document(
    storage: Storage, token: Token, cid: int, task_id: int | None = None
) -> Tree | None:
    _, address = resolve(
        token, conversation_id=cid if task_id is None else None, task_id=task_id
    )
    record = storage.find_document(address)
    if record is None:
        return None
    check_scope(token, record)
    identity = address.to_tree()
    actual = record.to_tree()
    if any(actual.get(key) != value for key, value in identity.items()) or (
        "key" in actual and "key" not in identity
    ):
        refuse()
    found = storage.document(DocumentId(record.id))
    if found is None:
        refuse()
    assert found is not None
    value = cast(Tree, found.to_tree())
    if value["record"] != record.to_tree() or value["version"] != token.version:
        refuse()
    return cast(Tree, value["value"])


def _rows(storage: Storage, cid: int) -> tuple[tuple[EntryRecord, ...], list[Tree]]:
    entries: list[EntryRecord] = []
    rows: list[Tree] = []
    cursor = None
    while True:
        page = storage.scan_entries(EntryQuery({"conversationId": cid}), 128, cursor)
        entries.extend(page.items)
        cursor = page.next
        if cursor is None:
            break
    cursor = None
    while True:
        tasks = storage.scan_tasks(TaskQuery({"conversationId": cid}), 128, cursor)
        rows.extend(cast(Tree, row.to_tree()) for row in tasks.items)
        cursor = tasks.next
        if cursor is None:
            break
    if any(e.to_tree()["conversationId"] != cid for e in entries) or any(
        row["conversationId"] != cid for row in rows
    ):
        refuse()
    return tuple(entries), rows


def _open_round(
    storage: Storage, cid: int, parent: Tree, by_id: dict[int, EntryRecord]
) -> tuple[int, list[Tree]] | None:
    value = document(storage, ROUND_DOC, cid, parent["id"])
    if value is None:
        return None
    if set(value) != {"assistant", "tools", "slots"}:
        refuse()
    found = by_id.get(value["assistant"])
    if found is None:
        refuse()
    assert found is not None
    tree = cast(Tree, found.to_tree())
    if tree.get("byTaskId") != parent["id"] or tree["kind"] != "pi.assistant":
        refuse()
    assistant = decode_canonical(tree["model"][0])
    seed = decode_canonical(tree["data"]["request"])
    if (
        type(assistant) is not AgentAssistantMessage
        or type(seed) is not AgentProviderRequestSnapshot
    ):
        refuse()
    assert type(assistant) is AgentAssistantMessage
    assert type(seed) is AgentProviderRequestSnapshot
    _round_calls(value, assistant, seed)
    return value["assistant"], value["slots"]


def _prefix(
    parent: Tree,
    assistant_id: int,
    slots: list[Tree],
    rows: list[Tree],
    by_id: dict[int, EntryRecord],
) -> tuple[list[int], set[int]]:
    assistant = decode_canonical(cast(Tree, by_id[assistant_id].to_tree())["model"][0])
    assert type(assistant) is AgentAssistantMessage
    visible: list[int] = []
    linked: set[int] = set()
    held = False
    for call, slot in zip(assistant.tool_calls, slots, strict=True):
        if "taskId" in slot:
            child = owned_child(parent, assistant_id, call, slot, rows)
            state = child["state"]
            if (
                state["status"] != "terminal"
                or state["outcome"]["status"] != "completed"
            ):
                held = True
                continue
            id = _child_result_id(parent, assistant_id, call, slot, rows)
        else:
            id = slot["entryId"]
        found = by_id.get(id)
        if found is None or id in linked:
            refuse()
        assert found is not None
        result_value(found, parent, assistant_id, call, slot)
        linked.add(id)
        if not held:
            visible.append(id)
    return visible, linked


def _ordered(
    storage: Storage,
    cid: int,
    entries: tuple[EntryRecord, ...],
    rows: list[Tree],
    index: Tree,
) -> tuple[EntryRecord, ...]:
    ordered = ordered_entries(entries, index)
    by_id = {entry.id: entry for entry in entries}
    _entry_owners(entries, rows)
    groups: dict[int, list[int]] = {}
    hidden: set[int] = set()
    linked_closed = _closed_links(by_id, rows, index)
    for parent in rows:
        if (parent["kind"], parent["version"]) != (GENERATION_NAME, GENERATION_VERSION):
            continue
        found = _open_round(storage, cid, parent, by_id)
        if found is None or str(found[0]) in index["rounds"]:
            continue
        assistant, slots = found
        prefix, linked = _prefix(parent, assistant, slots, rows, by_id)
        if hidden & linked or assistant in groups:
            refuse()
        hidden.update(linked)
        groups[assistant] = prefix
    if any(
        entry.to_tree()["kind"] == "pi.tool-result"
        and entry.id not in hidden | linked_closed
        for entry in entries
    ):
        refuse()
    result = []
    for entry in ordered:
        if entry.id not in hidden:
            result.append(entry)
            result.extend(by_id[id] for id in groups.get(entry.id, ()))
    return tuple(result)


def _entry_owners(entries: tuple[EntryRecord, ...], rows: list[Tree]) -> None:
    by_task = {row["id"]: row for row in rows}
    for entry in entries:
        tree = cast(Tree, entry.to_tree())
        if "byTaskId" not in tree:
            continue
        owner = by_task.get(tree["byTaskId"])
        if owner is None:
            refuse()
        if tree["kind"] == "pi.assistant" and (
            (owner["kind"], owner["version"]) != (GENERATION_NAME, GENERATION_VERSION)
            or type(tree.get("data")) is not dict
            or set(tree["data"]) != {"receipt", "request"}
        ):
            refuse()


def _closed_links(
    by_id: dict[int, EntryRecord], rows: list[Tree], index: Tree
) -> set[int]:
    linked: set[int] = set()
    for key, ids in index["rounds"].items():
        assistant_id = int(key)
        tree = cast(Tree, by_id[assistant_id].to_tree())
        parent = next((r for r in rows if r["id"] == tree.get("byTaskId")), None)
        if parent is None or (parent["kind"], parent["version"]) != (
            GENERATION_NAME,
            GENERATION_VERSION,
        ):
            refuse()
        assistant = decode_canonical(tree["model"][0])
        assert type(assistant) is AgentAssistantMessage
        seed = decode_canonical(tree["data"]["request"])
        if type(seed) is not AgentProviderRequestSnapshot:
            refuse()
        slots = []
        for call, id in zip(assistant.tool_calls, ids, strict=True):
            child_tree = cast(Tree, by_id[id].to_tree())
            slot: Tree = {"callId": call.provider_correlation_id}
            if seed.authorizes(call.tool_name):
                slot["taskId"] = child_tree.get("byTaskId")
                if _child_result_id(parent, assistant_id, call, slot, rows) != id:
                    refuse()
            else:
                slot["entryId"] = id
            result_value(by_id[id], parent, assistant_id, call, slot)
            slots.append(slot)
            linked.add(id)
        _round_calls(
            {"slots": slots, "tools": [s["taskId"] for s in slots if "taskId" in s]},
            assistant,
            seed,
        )
    return linked


def _message(storage: Storage, cid: int, entry: EntryRecord) -> CompletedMessage:
    tree = cast(Tree, entry.to_tree())
    kinds = {
        "pi.user": AgentUserMessage,
        "pi.assistant": AgentAssistantMessage,
        "pi.tool-result": AgentToolResultMessage,
    }
    model = tree.get("model")
    if tree["kind"] not in kinds or type(model) is not list or len(model) != 1:
        refuse()
    if (
        "head" in tree
        or "edits" in tree
        or type(decode_canonical(model[0])) is not kinds[tree["kind"]]
    ):
        refuse()
    lookup = storage.entry(EntryId(entry.id), conversation_id=ConversationId(cid))
    if lookup is None or lookup.entry != entry:
        refuse()
    assert lookup is not None
    data = tree.get("data", {})
    provider = None
    tool = None
    if tree["kind"] == "pi.assistant" and "receipt" in data:
        frame = decode_checkpoint(
            data["receipt"],
            name=GENERATION_NAME,
            version=GENERATION_VERSION,
            phase="settle",
            role="provider_outcome",
        )
        provider = OwnedJson(frame.to_tree()["payload"])
    if tree["kind"] == "pi.tool-result" and "nativeToolOutput" in data:
        from .tool_values import read_native_tool_output

        tool = OwnedJson(read_native_tool_output(data["nativeToolOutput"])[0])
    return CompletedMessage(
        cid,
        entry.id,
        lookup.commit_seq,
        tree.get("byTaskId"),
        OwnedJson(model[0]),
        provider,
        tool,
    )


def capture(storage: Storage, cid: int) -> CompletedSnapshot:
    """No initialization, migrations, pricing, credentials or effects on this path."""
    try:
        safe_integer(cid)
        conversation = storage.conversation(ConversationId(cid))
        if conversation is None or "parent" in conversation.to_tree():
            refuse()
        entries, rows = _rows(storage, cid)
        index = document(storage, ORDER_DOC, cid) or {"rounds": {}}
        ordered = _ordered(storage, cid, entries, rows, index)
        usage = document(storage, USAGE_DOC, cid)
        return CompletedSnapshot(
            cid,
            0,
            None,
            tuple(_message(storage, cid, entry) for entry in ordered),
            tuple(
                ClosedRound(int(key), tuple(ids))
                for key, ids in index["rounds"].items()
            ),
            tuple(
                TerminalOutcome(
                    row["id"],
                    row["kind"],
                    row["version"],
                    OwnedJson(row["state"]["outcome"]),
                )
                for row in rows
                if row["state"]["status"] == "terminal"
            ),
            None if usage is None else OwnedJson(usage),
        )
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        refuse()
