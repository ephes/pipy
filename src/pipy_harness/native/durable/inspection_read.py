"""Pure committed capture helpers, only inside the existing Session line.

The private full-content captures never cross the operator DTO boundary.
Document lookup uses Storage directly and cannot seed/migrate/load a live port.
"""

from __future__ import annotations

from typing import Any, cast

from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical
from .checkpoint_codec import decode_checkpoint
from .documents import Token, check_scope, resolve
from .generation import ROUND_DOC
from .generation_tasks import GENERATION_NAME, GENERATION_VERSION
from .records import DocumentId, EntryRecord
from .storage import ConversationQuery, EntryQuery, Storage, TaskQuery
from .tool_graph import _round_calls, child_result_id, owned_child, result_value
from .tool_values import refuse

Tree = dict[str, Any]


def peek_document(
    storage: Storage,
    token: Token,
    cid: int,
    task_id: int | None = None,
    *,
    key: object = None,
    opaque: bool = False,
) -> Tree | None:
    from .records import OMITTED

    _, address = resolve(
        token,
        conversation_id=cid if task_id is None else None,
        task_id=task_id,
        key=OMITTED if key is None else key,
    )
    record = storage.find_document(address)
    if record is None:
        return None
    check_scope(token, record)
    identity, actual = address.to_tree(), record.to_tree()
    if any(actual.get(k) != v for k, v in identity.items()) or (
        "key" in actual and "key" not in identity
    ):
        refuse()
    found = storage.document(DocumentId(record.id))
    if found is None:
        refuse()
    value = cast(Tree, found.to_tree())
    if value["record"] != actual or not opaque and value["version"] != token.version:
        refuse()
    return value


def document_exists(storage: Storage, token: Token, cid: int, task_id: int) -> bool:
    """Existence is sufficient for a receipt hold, even for unknown contents."""
    _, address = resolve(token, task_id=task_id)
    record = storage.find_document(address)
    if record is None:
        return False
    check_scope(token, record)
    expected = address.to_tree()
    actual = record.to_tree()
    if any(actual.get(k) != v for k, v in expected.items()):
        refuse()
    return True


def tasks(storage: Storage, cid: int | None = None) -> list[Tree]:
    result: list[Tree] = []
    cursor = None
    while True:
        page = storage.scan_tasks(
            TaskQuery({} if cid is None else {"conversationId": cid}), 128, cursor
        )
        result.extend(cast(Tree, r.to_tree()) for r in page.items)
        cursor = page.next
        if cursor is None:
            return result


def conversations(storage: Storage) -> list[Tree]:
    result: list[Tree] = []
    cursor = None
    while True:
        page = storage.scan_conversations(ConversationQuery({}), 128, cursor)
        result.extend(cast(Tree, r.to_tree()) for r in page.items)
        cursor = page.next
        if cursor is None:
            return result


def entries(storage: Storage, cid: int) -> dict[int, EntryRecord]:
    result = {}
    cursor = None
    while True:
        page = storage.scan_entries(EntryQuery({"conversationId": cid}), 128, cursor)
        result.update({r.id: r for r in page.items})
        cursor = page.next
        if cursor is None:
            return result


def round_facts(
    storage: Storage,
    parent: Tree,
    rows: list[Tree],
) -> tuple[
    Tree, AgentAssistantMessage, AgentProviderRequestSnapshot, dict[int, EntryRecord]
]:
    if (parent["kind"], parent["version"]) != (GENERATION_NAME, GENERATION_VERSION):
        refuse()
    state = parent["state"]
    if state["status"] not in ("waiting", "pending", "running"):
        refuse()
    phase = state["checkpoint"].get("phase")
    if phase not in ("tools", "blocked"):
        refuse()
    frame = decode_checkpoint(
        state["checkpoint"],
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase=phase,
        role="provider_outcome",
    ).to_tree()
    doc = peek_document(storage, ROUND_DOC, parent["conversationId"], parent["id"])
    if doc is None:
        refuse()
    value = doc["value"]
    if type(value) is not dict or set(value) != {"assistant", "tools", "slots"}:
        refuse()
    if state["status"] == "waiting" and (
        state["policy"] != "allSettled" or state["on"] != value["tools"]
    ):
        refuse()
    by_id = entries(storage, parent["conversationId"])
    assistant, snapshot = _assistant(parent, frame, phase, value, by_id)
    _round_calls(value, assistant, snapshot)
    # Validate every owned edge independently of progress. No foreign embedded
    # entry ID is ever followed; result facts come from the chosen CID scan.
    for call, slot in zip(assistant.tool_calls, value["slots"], strict=True):
        if "taskId" in slot:
            owned_child(parent, value["assistant"], call, slot, rows)
    return doc, assistant, snapshot, by_id


def _assistant(
    parent: Tree, frame: Tree, phase: str, value: Tree, by_id: dict[int, EntryRecord]
) -> tuple[AgentAssistantMessage, AgentProviderRequestSnapshot]:
    found = by_id.get(value["assistant"])
    if found is None:
        refuse()
    tree = cast(Tree, found.to_tree())
    if tree.get("byTaskId") != parent["id"] or tree["kind"] != "pi.assistant":
        refuse()
    assistant = decode_canonical(tree["model"][0])
    snapshot = decode_canonical(tree["data"]["request"])
    if (
        type(assistant) is not AgentAssistantMessage
        or type(snapshot) is not AgentProviderRequestSnapshot
    ):
        refuse()
    from .codec import OwnedJson

    saved = tree["data"]["receipt"]
    saved["phase"] = phase
    if OwnedJson(saved) != OwnedJson(frame):
        refuse()
    return assistant, snapshot


def terminate_index(
    parent: Tree,
    value: Tree,
    assistant: AgentAssistantMessage,
    rows: list[Tree],
    by_id: dict[int, EntryRecord],
) -> int | None:
    for index, (call, slot) in enumerate(
        zip(assistant.tool_calls, value["slots"], strict=True)
    ):
        if "taskId" in slot:
            result_id = child_result_id(
                parent, value["assistant"], call, slot, rows, by_id
            )
            if result_id is None:
                continue
        else:
            result_id = slot["entryId"]
        found = by_id.get(result_id)
        if found is None:
            refuse()
        if result_value(found, parent, value["assistant"], call, slot).terminate:
            return index
    return None
