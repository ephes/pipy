"""Strict, complete ownership evidence for private orphan controls.

This deliberately does not replace the scheduler's ordinary live-work query.
All records belong to one actual Session transaction; nothing is cached here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from .codec import OwnedJson
from .records import ConversationRecord, Record, TaskRecord
from .session_core import SyncTx
from .storage import ConversationQuery, Cursor, Page, TaskQuery

Tree = dict[str, Any]
R = TypeVar("R", bound=Record)
Node = tuple[str, int]


def _refuse() -> None:
    from .scheduler import UnsupportedTaskExecution

    raise UnsupportedTaskExecution(
        "Task requires a validated unmarked pending root leaf"
    )


def _scan(
    fetch: Callable[[Cursor | None], Page[R]], expected: type[R]
) -> dict[int, Tree]:
    rows: dict[int, Tree] = {}
    cursor = None
    last = 0
    while True:
        page = fetch(cursor)
        for record in page.items:
            if type(record) is not expected or record.id <= last:
                _refuse()
            last = record.id
            rows[record.id] = cast(Tree, record.to_tree())
        following = page.next
        if following is None:
            return rows
        if (
            not page.items
            or following.order not in (None, "ascending")
            or following.after != last
            or (cursor is not None and following.after <= cursor.after)
        ):
            _refuse()
        cursor = following


def _validate_ownership(tasks: dict[int, Tree], conversations: dict[int, Tree]) -> None:
    if tasks.keys() & conversations.keys():
        _refuse()
    parents: dict[Node, Node | None] = {}
    for id, task in tasks.items():
        conversation = task["conversationId"]
        if conversation not in conversations:
            _refuse()
        owner = task.get("owner")
        if owner is not None:
            if owner not in tasks or tasks[owner]["conversationId"] != conversation:
                _refuse()
            parents[("task", id)] = ("task", owner)
        else:
            parents[("task", id)] = ("conversation", conversation)
    for id, conversation in conversations.items():
        owner = conversation.get("owner")
        if owner is None:
            parents[("conversation", id)] = None
        else:
            task_id = owner["taskId"]
            if (
                task_id not in tasks
                or tasks[task_id]["conversationId"] != owner["conversationId"]
            ):
                _refuse()
            parents[("conversation", id)] = ("task", task_id)
    _acyclic(parents)


def _acyclic(parents: dict[Node, Node | None]) -> None:
    # Iterative single-parent traversal handles large graphs without recursion.
    settled: set[Node] = set()
    for node in parents:
        path: set[Node] = set()
        current: Node | None = node
        while current is not None and current not in settled:
            if current in path:
                _refuse()
            path.add(current)
            current = parents[current]
        settled.update(path)


def require_unmarked_root_leaf(tx: SyncTx, record: Tree) -> None:
    """Prove strict graph eligibility; the caller separately checks its code reason."""
    if (
        record["state"]["status"] != "pending"
        or record["abortRequested"]
        or "abortReason" in record
        or record.get("abandonOnRestart", False)
        or "owner" in record
    ):
        _refuse()
    tasks = _scan(
        lambda cursor: tx.scan_tasks(TaskQuery({"order": "ascending"}), 128, cursor),
        TaskRecord,
    )
    conversations = _scan(
        lambda cursor: tx.scan_conversations(
            ConversationQuery({"order": "ascending"}), 128, cursor
        ),
        ConversationRecord,
    )
    if record["id"] not in tasks or OwnedJson(tasks[record["id"]]) != OwnedJson(record):
        _refuse()
    _validate_ownership(tasks, conversations)
    id = record["id"]
    if "owner" in conversations[record["conversationId"]]:
        _refuse()
    if any(task.get("owner") == id for task in tasks.values()):
        _refuse()
    if any(c.get("owner", {}).get("taskId") == id for c in conversations.values()):
        _refuse()
    if any(
        task["id"] != id
        and task["state"]["status"] == "waiting"
        and id in task["state"]["on"]
        for task in tasks.values()
    ):
        _refuse()
