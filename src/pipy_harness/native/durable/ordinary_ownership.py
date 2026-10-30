"""Private transaction-local ordinary-owned-live graph queries.

Capture committed projections before writes, then join actual staged authority on
that same SyncTx. The delivered query is neither execution admission nor
assembly validation. A private awaited scheduler consumer now shares these pure
algorithms; strict orphan and built-in model authority remain separate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Never, TypeVar, cast, final

from .records import ConversationRecord, Record, TaskRecord
from .session_core import AsyncTx, SyncTx
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


def _page(
    result: dict[int, P],
    page: Page[R],
    expected: type[R],
    project: Callable[[R], P],
    last: int,
    cursor: Cursor | None,
) -> tuple[int, Cursor | None]:
    """The same strict protocol for synchronous and awaited storage reads."""
    if type(page) is not Page:
        _invalid()
    for record in page.items:
        if type(record) is not expected or record.id <= last:
            _invalid()
        last = record.id
        result[last] = project(record)
    following = page.next
    if following is not None and (
        type(following) is not Cursor
        or not page.items
        or following.order not in (None, "ascending")
        or following.after != last
        or (cursor is not None and following.after <= cursor.after)
    ):
        _invalid()
    return last, following


def _scan(
    fetch: Callable[[Cursor | None], Page[R]],
    expected: type[R],
    project: Callable[[R], P],
) -> dict[int, P]:
    result: dict[int, P] = {}
    cursor = None
    last = 0
    while True:
        last, cursor = _page(result, fetch(cursor), expected, project, last, cursor)
        if cursor is None:
            return result


async def _ascan(
    fetch: Callable[[Cursor | None], Awaitable[Page[R]]],
    expected: type[R],
    project: Callable[[R], P],
) -> dict[int, P]:
    result: dict[int, P] = {}
    cursor = None
    last = 0
    while True:
        page = await fetch(cursor)
        last, cursor = _page(result, page, expected, project, last, cursor)
        if cursor is None:
            return result


def _parents(
    tasks: Mapping[int, _Task], conversations: Mapping[int, _Conversation]
) -> dict[Node, Node | None]:
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
    return parents


def _graph(
    tasks: Mapping[int, _Task], conversations: Mapping[int, _Conversation]
) -> dict[Node, list[Node]]:
    parents = _parents(tasks, conversations)
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


def _candidates(
    tx: SyncTx | AsyncTx, committed: Mapping[int, _Task]
) -> dict[int, _Task]:
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
        tasks, conversations = _view(self._tx, self._tasks, self._conversations)
        return _has(tasks, conversations, id)


def _view(
    tx: SyncTx | AsyncTx,
    committed: Mapping[int, _Task],
    conversations: Mapping[int, _Conversation],
) -> tuple[dict[int, _Task], dict[int, _Conversation]]:
    tx._core._enter()
    tasks = {**committed, **_candidates(tx, committed)}
    combined = dict(conversations)
    for record in tx.staged_conversations():
        if type(record) is not ConversationRecord or record.id in combined:
            _invalid()
        combined[record.id] = _conversation(record)
    return tasks, combined


def _has(
    tasks: Mapping[int, _Task], conversations: Mapping[int, _Conversation], id: int
) -> bool:
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


def _cancellation_intent(record: TaskRecord) -> bool:
    # Literal release 1.4 scheduler.ts2399–2407: decided non-completed
    # outcomes and explicit marks are intent; terminal ancestors never cascade.
    row = cast(dict[str, Any], record.to_tree())
    state = row["state"]
    return bool(
        state["status"] != "terminal"
        and (
            row["abortRequested"]
            or (
                state["status"] == "completing"
                and state["outcome"]["status"] != "completed"
            )
        )
    )


def _all_wait_on(record: TaskRecord) -> tuple[int, ...]:
    state = cast(dict[str, Any], record.to_tree())["state"]
    return tuple(state["on"]) if state["status"] == "waiting" else ()


def _wait_on(record: TaskRecord) -> tuple[int, ...]:
    row = cast(dict[str, Any], record.to_tree())
    state = row["state"]
    # A marked task waits only for owned work, even for historical failFast.
    return (
        tuple(state["on"])
        if state["status"] == "waiting" and not row["abortRequested"]
        else ()
    )


def _owned_targets(
    children: Mapping[Node, list[Node]],
    tasks: Mapping[int, _Task],
    owner: int,
    walked: set[Node],
) -> Iterator[int]:
    pending = list(children.get(("task", owner), ()))
    while pending:
        node = pending.pop()
        if node in walked:
            continue
        walked.add(node)
        if node[0] == "task":
            task = tasks[node[1]]
            if task.background:
                continue
            if not task.terminal:
                yield node[1]
        # Terminal ownership intermediates and owned conversations remain edges.
        pending.extend(children.get(node, ()))


def _wait_cycle(
    tasks: Mapping[int, _Task],
    children: Mapping[Node, list[Node]],
    waits: Mapping[int, tuple[int, ...]],
    waiter: int,
    targets: list[int],
) -> bool:
    pending = list(targets)
    seen: set[int] = set()
    walked: set[Node] = set()
    while pending:
        id = pending.pop()
        if id == waiter:
            return True
        if id in seen:
            continue
        seen.add(id)
        task = tasks.get(id)
        if task is None or task.terminal:
            continue
        pending.extend(waits[id])
        pending.extend(_owned_targets(children, tasks, id, walked))
    return False


@dataclass(frozen=True, slots=True)
class _AsyncOwnedLive:
    """Private scheduler consumer of the same graph, tied to one real AsyncTx."""

    tx: AsyncTx
    tasks: Mapping[int, _Task]
    conversations: Mapping[int, _Conversation]
    intentions: Mapping[int, bool]
    waits: Mapping[int, tuple[int, ...]]
    all_waits: Mapping[int, tuple[int, ...]]

    def root_leaf(self, task_id: int) -> bool:
        """Strict topology only, including terminal children and marked waits.

        The abort consumer separately owns real record/mode/version admission.
        This never weakens the existing unmarked orphan-control predicate.
        """
        tasks, conversations = _view(self.tx, self.tasks, self.conversations)
        children = _graph(tasks, conversations)
        id = _id(task_id, "task")
        if id not in tasks:
            raise ValueError("Unknown task")
        task = tasks[id]
        waits = dict(self.all_waits)
        waits.update(
            (record.id, _all_wait_on(record)) for record in self.tx.staged_tasks()
        )
        return (
            task.owner is None
            and conversations[task.conversation].owner is None
            and not children.get(("task", id))
            and not any(other != id and id in on for other, on in waits.items())
        )

    def has(self, task_id: int) -> bool:
        tasks, conversations = _view(self.tx, self.tasks, self.conversations)
        return _has(tasks, conversations, _id(task_id, "task"))

    def _wait_view(
        self,
    ) -> tuple[dict[int, _Task], dict[Node, list[Node]], dict[int, tuple[int, ...]]]:
        tasks, conversations = _view(self.tx, self.tasks, self.conversations)
        children = _graph(tasks, conversations)
        waits = dict(self.waits)
        waits.update((record.id, _wait_on(record)) for record in self.tx.staged_tasks())
        return tasks, children, waits

    def wait_refusal(self, task_id: int, on: tuple[int, ...]) -> str | None:
        tasks, children, waits = self._wait_view()
        id = _id(task_id, "task")
        if id not in tasks:
            raise ValueError("Unknown task")
        if any(target not in tasks for target in on):
            return "Task wait target does not exist"
        # Direct foreground children already hold their owner. Native typed
        # records can also represent owned background tasks; an explicit on
        # edge must not disappear merely because ownership cuts that subtree.
        targets = [
            target
            for target in on
            if not tasks[target].terminal
            and (tasks[target].owner != id or tasks[target].background)
        ]
        if _wait_cycle(tasks, children, waits, id, targets):
            return "Task wait would create a cycle"
        return None

    def waiting_ready(self, task_id: int) -> bool:
        tasks, _, waits = self._wait_view()
        id = _id(task_id, "task")
        if id not in tasks:
            raise ValueError("Unknown task")
        return all(target in tasks and tasks[target].terminal for target in waits[id])

    def refuses(self, task_id: int) -> bool:
        tasks, conversations = _view(self.tx, self.tasks, self.conversations)
        parents = _parents(tasks, conversations)
        id = _id(task_id, "task")
        if id not in tasks:
            raise ValueError("Unknown task")
        # An outside intent does not enter a background root's subtree.
        if tasks[id].background:
            return False
        intentions = dict(self.intentions)
        intentions.update(
            (record.id, _cancellation_intent(record))
            for record in self.tx.staged_tasks()
        )
        node = parents[("task", id)]
        while node is not None:
            if node[0] == "task":
                if intentions[node[1]]:
                    return True
                # Its own intent applies above; only outside intent is cut.
                if tasks[node[1]].background:
                    return False
            node = parents[node]
        return False


async def _capture_async(tx: AsyncTx) -> _AsyncOwnedLive:
    if type(tx) is not AsyncTx:
        raise TypeError("Expected an AsyncTx")
    tx._core._enter()
    intentions: dict[int, bool] = {}
    waits: dict[int, tuple[int, ...]] = {}
    all_waits: dict[int, tuple[int, ...]] = {}

    def project(record: TaskRecord) -> _Task:
        intentions[record.id] = _cancellation_intent(record)
        waits[record.id] = _wait_on(record)
        all_waits[record.id] = _all_wait_on(record)
        return _task(record)

    tasks = await _ascan(
        lambda cursor: tx.scan_tasks(TaskQuery({"order": "ascending"}), 128, cursor),
        TaskRecord,
        project,
    )
    conversations = await _ascan(
        lambda cursor: tx.scan_conversations(
            ConversationQuery({"order": "ascending"}), 128, cursor
        ),
        ConversationRecord,
        _conversation,
    )
    _graph(tasks, conversations)
    return _AsyncOwnedLive(
        tx,
        MappingProxyType(tasks),
        MappingProxyType(conversations),
        MappingProxyType(intentions),
        MappingProxyType(waits),
        MappingProxyType(all_waits),
    )
