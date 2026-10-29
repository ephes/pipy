"""Private transaction-local ordinary-owned-live graph queries.

Capture committed projections before writes, then join actual staged authority on
that same SyncTx. This is neither execution admission nor assembly validation.
The scheduler's conservative ownership and strict orphan fences remain separate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Never, TypeVar, cast, final

from .records import ConversationRecord, Record, TaskRecord
from .session_core import SyncTx
from .storage import ConversationQuery, Cursor, Page, TaskQuery
from .tx import _id

Node = tuple[str, int]
R = TypeVar("R", bound=Record)
P = TypeVar("P")


def _invalid() -> Never:
    raise ValueError("Invalid ordinary ownership graph")


@dataclass(frozen=True, slots=True)
class _Task:
    conversation: int
    owner: int | None
    background: bool
    terminal: bool


@dataclass(frozen=True, slots=True)
class _Conversation:
    owner: int | None
    owner_conversation: int | None


def _task(record: TaskRecord) -> _Task:
    row = cast(dict[str, Any], record.to_tree())
    return _Task(
        row["conversationId"],
        row.get("owner"),
        row["background"],
        row["state"]["status"] == "terminal",
    )


def _conversation(record: ConversationRecord) -> _Conversation:
    row = cast(dict[str, Any], record.to_tree())
    owner = row.get("owner")
    return _Conversation(
        None if owner is None else owner["taskId"],
        None if owner is None else owner["conversationId"],
    )


def _scan(
    fetch: Callable[[Cursor | None], Page[R]],
    expected: type[R],
    project: Callable[[R], P],
) -> dict[int, P]:
    result: dict[int, P] = {}
    cursor = None
    last = 0
    while True:
        page = fetch(cursor)
        if type(page) is not Page:
            _invalid()
        for record in page.items:
            if type(record) is not expected or record.id <= last:
                _invalid()
            last = record.id
            result[last] = project(record)
        following = page.next
        if following is None:
            return result
        if (
            type(following) is not Cursor
            or not page.items
            or following.order not in (None, "ascending")
            or following.after != last
            or (cursor is not None and following.after <= cursor.after)
        ):
            _invalid()
        cursor = following


def _graph(
    tasks: Mapping[int, _Task], conversations: Mapping[int, _Conversation]
) -> dict[Node, list[Node]]:
    if tasks.keys() & conversations.keys():
        _invalid()
    parents: dict[Node, Node | None] = {}
    for id, task in tasks.items():
        if task.conversation not in conversations:
            _invalid()
        if task.owner is None:
            parents[("task", id)] = ("conversation", task.conversation)
        else:
            owner = tasks.get(task.owner)
            if owner is None or owner.conversation != task.conversation:
                _invalid()
            parents[("task", id)] = ("task", task.owner)
    for id, conversation in conversations.items():
        parents[("conversation", id)] = _conversation_parent(conversation, tasks)
    _acyclic(parents)
    children: dict[Node, list[Node]] = {}
    for node, parent in parents.items():
        if parent is not None:
            children.setdefault(parent, []).append(node)
    return children


def _conversation_parent(
    conversation: _Conversation, tasks: Mapping[int, _Task]
) -> Node | None:
    if conversation.owner is None:
        return None
    owner = tasks.get(conversation.owner)
    if owner is None or owner.conversation != conversation.owner_conversation:
        _invalid()
    return ("task", conversation.owner)


def _acyclic(parents: Mapping[Node, Node | None]) -> None:
    settled: set[Node] = set()
    for node in parents:
        path: set[Node] = set()
        current: Node | None = node
        while current is not None and current not in settled:
            if current in path:
                _invalid()
            path.add(current)
            current = parents[current]
        settled.update(path)


def _candidates(tx: SyncTx, committed: Mapping[int, _Task]) -> dict[int, _Task]:
    staged = tx.staged_tasks()
    if any(type(record) is not TaskRecord for record in staged):
        _invalid()
    records = {record.id: record for record in staged}
    if len(records) != len(staged):
        _invalid()
    result: dict[int, _Task] = {}
    for id, entry in tx._core._tasks.items():
        if entry.write is None:
            continue
        kind, record = entry.write
        if kind not in ("create", "replace") or records.get(id) is not record:
            _invalid()
        projection = _task(record)
        previous = committed.get(id)
        if kind == "create":
            if previous is not None:
                _invalid()
        elif (
            previous is None
            or previous.terminal
            or (previous.conversation, previous.owner, previous.background)
            != (projection.conversation, projection.owner, projection.background)
        ):
            _invalid()
        result[id] = projection
    if result.keys() != records.keys():
        _invalid()
    return result


@final
class OwnedLiveQuery:
    """Read a complete graph in one genuine synchronous Session transaction."""

    __slots__ = ("_tx", "_tasks", "_conversations")
    _tx: SyncTx
    _tasks: Mapping[int, _Task]
    _conversations: Mapping[int, _Conversation]

    def __init__(self) -> None:
        raise TypeError("Use OwnedLiveQuery.capture(tx)")

    @classmethod
    def capture(cls, tx: SyncTx) -> OwnedLiveQuery:
        if type(tx) is not SyncTx:
            raise TypeError("Expected a SyncTx")
        tx._core._enter()
        tasks = _scan(
            lambda cursor: tx.scan_tasks(
                TaskQuery({"order": "ascending"}), 128, cursor
            ),
            TaskRecord,
            _task,
        )
        conversations = _scan(
            lambda cursor: tx.scan_conversations(
                ConversationQuery({"order": "ascending"}), 128, cursor
            ),
            ConversationRecord,
            _conversation,
        )
        _graph(tasks, conversations)
        query = object.__new__(cls)
        query._tx = tx
        query._tasks = MappingProxyType(tasks)
        query._conversations = MappingProxyType(conversations)
        return query

    def has(self, task_id: int) -> bool:
        self._tx._core._enter()
        id = _id(task_id, "task")
        tasks = {**self._tasks, **_candidates(self._tx, self._tasks)}
        conversations = dict(self._conversations)
        for record in self._tx.staged_conversations():
            if type(record) is not ConversationRecord or record.id in conversations:
                _invalid()
            conversations[record.id] = _conversation(record)
        children = _graph(tasks, conversations)
        if id not in tasks:
            raise ValueError("Unknown task")
        pending = list(children.get(("task", id), ()))
        while pending:
            node = pending.pop()
            if node[0] == "task":
                task = tasks[node[1]]
                if task.background:
                    continue
                if not task.terminal:
                    return True
            pending.extend(children.get(node, ()))
        return False
