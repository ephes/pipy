"""Internal table-only Memory stage. No product factory or document capability.

Source-derived Pi durable 1.3.0 memory.ts/scan.ts, MIT (c)2025 Mario Zechner;
notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt. DE02a2b must add raw-base
state INSIDE this candidate/authority before cumulative backend completion.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import NoReturn, TypeVar, cast

from .records import (
    RECORD_TYPES,
    SAFE_INTEGER,
    ConversationId,
    ConversationRecord,
    DocumentId,
    DocumentRecord,
    EntryId,
    EntryRecord,
    Record,
    Seq,
    SubmissionId,
    SubmissionRecord,
    TaskId,
    TaskRecord,
    safe_integer,
)
from .storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentPoint,
    DocumentQuery,
    EntryLookup,
    EntryQuery,
    Page,
    Query,
    ScanOrder,
    StorageClosedError,
    StorageRequestError,
    StorageWrite,
    StoredDocument,
    SubmissionQuery,
    TaskQuery,
    UnsupportedStorageOperation,
)

T = TypeVar("T", bound=Record)


def _units(value: str) -> bytes:
    return value.encode("utf-16-be", "surrogatepass")


@dataclass(frozen=True)
class _State:
    # Published dictionaries never mutate. A commit owns a complete replacement.
    tables: dict[str, dict[int, Record]] = field(
        default_factory=lambda: {
            k: {} for k in ("conversation", "entry", "task", "submission")
        }
    )
    roles: dict[int, str] = field(default_factory=dict)
    entry_seqs: dict[int, int] = field(default_factory=dict)
    requests: dict[tuple[int, bytes], int] = field(default_factory=dict)
    owners: dict[tuple[str, int], tuple[int, ...]] = field(default_factory=dict)
    statuses: dict[tuple[str, str], tuple[int, ...]] = field(default_factory=dict)
    entries: dict[int, tuple[int, ...]] = field(default_factory=dict)
    heads: dict[int, tuple[int, ...]] = field(default_factory=dict)
    next_id: int = 2
    next_seq: int = 1


def _copy(record: T) -> T:
    return type(record)(record.to_tree())


def _indexes(state: _State) -> _State:
    owners: dict[tuple[str, int], list[int]] = {}
    statuses: dict[tuple[str, str], list[int]] = {}
    entries: dict[int, list[int]] = {}
    heads: dict[int, list[int]] = {}
    for kind, table in state.tables.items():
        for id, record in sorted(table.items()):
            value = record.to_tree()
            if kind == "conversation" and "owner" in value:
                owner = cast(dict[str, int], value["owner"])
                for key in ("conversationId", "taskId"):
                    owners.setdefault((key, owner[key]), []).append(id)
            elif kind == "entry":
                conversation = cast(int, value["conversationId"])
                entries.setdefault(conversation, []).append(id)
                if "head" in value:
                    heads.setdefault(conversation, []).append(id)
            elif kind in {"task", "submission"}:
                status = (
                    value["status"]
                    if kind == "submission"
                    else cast(dict[str, object], value["state"])["status"]
                )
                statuses.setdefault((kind, cast(str, status)), []).append(id)
    return replace(
        state,
        owners={k: tuple(v) for k, v in owners.items()},
        statuses={k: tuple(v) for k, v in statuses.items()},
        entries={k: tuple(v) for k, v in entries.items()},
        heads={k: tuple(v) for k, v in heads.items()},
    )


def _request(value: dict[str, object]) -> tuple[int, bytes] | None:
    return (
        (cast(int, value["conversationId"]), _units(cast(str, value["requestId"])))
        if "requestId" in value
        else None
    )


def _prepare(state: _State, writes: tuple[StorageWrite, ...]) -> _State:
    if type(writes) is not tuple or any(type(w) is not StorageWrite for w in writes):
        raise StorageRequestError("Expected owned write tuple")
    trees = [write.to_tree() for write in writes]
    if any(cast(str, value["type"]).startswith("document.") for value in trees):
        raise UnsupportedStorageOperation("Documents require DE02a2b")
    if state.next_seq > SAFE_INTEGER:
        raise RuntimeError(
            f"Commit sequence {state.next_seq} does not strictly increase"
        )
    _claims(state, trees)
    candidate = replace(
        state,
        tables={k: dict(v) for k, v in state.tables.items()},
        roles=dict(state.roles),
        entry_seqs=dict(state.entry_seqs),
        requests=dict(state.requests),
        next_seq=state.next_seq + 1,
    )
    for write in trees:
        candidate = _write(candidate, write, state.next_seq)
    return _indexes(candidate)


def _claims(state: _State, trees: list[dict[str, object]]) -> None:
    claims: dict[int, str] = {}
    for write in trees:
        kind = cast(str, write["type"])
        id = cast(int, cast(dict[str, object], write["value"])["id"])
        existing, earlier = state.roles.get(id), claims.get(id)
        if kind in {"conversation", "entry"}:
            if existing is not None:
                raise RuntimeError(f"ID {id} already belongs to {existing}")
            if earlier is not None:
                raise RuntimeError(f"ID {id} is written more than once")
        else:
            if existing is not None and existing != kind:
                raise RuntimeError(f"ID {id} already belongs to {existing}")
            if earlier is not None and earlier != kind:
                raise RuntimeError(f"ID {id} is written as two record types")
        claims[id] = kind


def _write(state: _State, write: dict[str, object], seq: int) -> _State:
    kind = cast(str, write["type"])
    record = RECORD_TYPES[kind](write["value"])
    id = record.id
    previous = state.tables[kind].get(id)
    if kind == "submission":
        key = None if previous is None else _request(previous.to_tree())
        if key is not None and state.requests.get(key) == id:
            del state.requests[key]
        key = _request(record.to_tree())
        if key is not None:
            state.requests[key] = id
    state.tables[kind][id] = record
    state.roles[id] = kind
    if kind == "entry":
        state.entry_seqs[id] = seq
    return replace(state, next_id=max(state.next_id, id + 1))


def _start(
    query: dict[str, object], cursor: Cursor | None, fallback: ScanOrder
) -> tuple[ScanOrder, int | None]:
    if cursor is not None and type(cursor) is not Cursor:
        raise StorageRequestError("Invalid storage cursor")
    requested = cast(ScanOrder | None, query.get("order"))
    if cursor is None:
        return requested or fallback, None
    order = cursor.order or fallback
    if requested is not None and requested != order:
        raise StorageRequestError(
            f"The cursor continues a {order} scan; the query asks for {requested}"
        )
    return order, cursor.after


def _page(records: list[T], limit: int, order: ScanOrder) -> Page[T]:
    items = tuple(_copy(value) for value in records[:limit])
    return Page(items, Cursor(items[-1].id, order) if len(records) > limit else None)


def _query(value: Query, cls: type[Query], limit: int) -> dict[str, object]:
    if type(value) is not cls or type(limit) is not int:
        raise StorageRequestError("Expected typed query and integer page size")
    return value.to_tree()


class MemoryStorage:
    """Internal incomplete backend stage: tables only; documents explicitly refuse."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = _State()
        self._closed = False

    def _open(self) -> None:
        if self._closed:
            raise StorageClosedError("MemoryStorage is closed")

    def commit(self, writes: tuple[StorageWrite, ...]) -> Seq:
        with self._lock:
            self._open()
            candidate = _prepare(self._state, writes)
            seq = Seq(self._state.next_seq)
            self._state = candidate
            return seq

    def mint_id(self) -> int:
        with self._lock:
            self._open()
            id = self._state.next_id
            if id > SAFE_INTEGER:
                raise RuntimeError("ID space is exhausted")
            self._state = replace(self._state, next_id=id + 1)
            return id

    def _exact(self, kind: str, id: int) -> Record | None:
        self._open()
        safe_integer(id)
        value = self._state.tables[kind].get(id)
        return None if value is None else _copy(value)

    def conversation(self, id: ConversationId) -> ConversationRecord | None:
        with self._lock:
            return cast(ConversationRecord | None, self._exact("conversation", id))

    def task(self, id: TaskId) -> TaskRecord | None:
        with self._lock:
            return cast(TaskRecord | None, self._exact("task", id))

    def submission(self, id: SubmissionId) -> SubmissionRecord | None:
        with self._lock:
            return cast(SubmissionRecord | None, self._exact("submission", id))

    def submission_by_request(
        self, conversation_id: ConversationId, request_id: str
    ) -> SubmissionRecord | None:
        with self._lock:
            self._open()
            safe_integer(conversation_id)
            if type(request_id) is not str:
                raise StorageRequestError("Expected request ID string")
            id = self._state.requests.get((conversation_id, _units(request_id)))
            return (
                None
                if id is None
                else cast(SubmissionRecord, self._exact("submission", id))
            )

    def _segments(
        self,
        conversation: int,
        maximum: int | None = None,
        minimum: int | None = None,
    ) -> Iterator[tuple[int, int | None]]:
        if conversation not in self._state.tables["conversation"]:
            raise StorageRequestError(f"Unknown conversation: {conversation}")
        while True:
            # Raw Memory need not have a valid Session graph. Yield this segment
            # before touching its parent, exactly as the source traversal does.
            yield conversation, maximum
            value = self._state.tables["conversation"][conversation].to_tree()
            if "parent" not in value:
                return
            parent = cast(dict[str, int], value["parent"])
            maximum = parent["at"] if maximum is None else min(maximum, parent["at"])
            if minimum is not None and maximum < minimum:
                return
            conversation = parent["conversationId"]

    def _visible(
        self,
        conversation: int,
        minimum: int | None,
        maximum: int | None,
        order: ScanOrder,
    ) -> Iterator[int]:
        segments: Iterable[tuple[int, int | None]] = self._segments(
            conversation, maximum, minimum
        )
        if order == "ascending":
            # Pi deliberately builds the complete capped segment list first.
            segments = reversed(list(segments))
        for owner, upper in segments:
            ids = self._state.entries.get(owner, ())
            for id in ids if order == "ascending" else reversed(ids):
                if (minimum is None or id >= minimum) and (
                    upper is None or id <= upper
                ):
                    yield id

    def entry(
        self, id: EntryId, *, conversation_id: ConversationId | None = None
    ) -> EntryLookup | None:
        with self._lock:
            self._open()
            safe_integer(id)
            if conversation_id is not None:
                safe_integer(conversation_id)
                if (
                    next(self._visible(conversation_id, id, id, "descending"), None)
                    is None
                ):
                    return None
            record = self._exact("entry", id)
            return (
                None
                if record is None
                else EntryLookup(
                    cast(EntryRecord, record), Seq(self._state.entry_seqs[id])
                )
            )

    def find_latest_head_marker(
        self,
        conversation_id: ConversationId,
        at_or_before_entry_id: EntryId | None = None,
    ) -> EntryRecord | None:
        with self._lock:
            self._open()
            safe_integer(conversation_id)
            if at_or_before_entry_id is not None:
                safe_integer(at_or_before_entry_id)
            for owner, upper in self._segments(conversation_id, at_or_before_entry_id):
                ids = [
                    id
                    for id in self._state.heads.get(owner, ())
                    if upper is None or id <= upper
                ]
                if ids:
                    return cast(EntryRecord, self._exact("entry", ids[-1]))
            return None

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[EntryRecord]:
        with self._lock:
            self._open()
            value = _query(query, EntryQuery, limit)
            order, after = _start(value, cursor, "descending")
            minimum, maximum = (
                cast(int | None, value.get("minEntryId")),
                cast(int | None, value.get("maxEntryId")),
            )
            if after is not None:
                if order == "ascending":
                    minimum = (
                        max(minimum, after + 1) if minimum is not None else after + 1
                    )
                else:
                    maximum = (
                        min(maximum, after - 1) if maximum is not None else after - 1
                    )
            ids = self._visible(
                cast(int, value["conversationId"]), minimum, maximum, order
            )
            records = []
            for id in ids:
                records.append(cast(EntryRecord, self._state.tables["entry"][id]))
                if len(records) > limit:
                    break
            return _page(records, limit, order)

    def _scan(
        self,
        kind: str,
        query: Query,
        cls: type[Query],
        limit: int,
        cursor: Cursor | None,
    ) -> Page[Record]:
        self._open()
        value = _query(query, cls, limit)
        order, after = _start(value, cursor, "ascending")
        ids = self._scan_ids(kind, value)
        records: list[Record] = []
        for id in ids if order == "ascending" else reversed(ids):
            if len(records) > limit:
                break
            if after is not None and (
                id <= after if order == "ascending" else id >= after
            ):
                continue
            record = self._state.tables[kind][id]
            if _matches(record.to_tree(), value):
                records.append(record)
        return _page(records, limit, order)

    def _scan_ids(self, kind: str, value: dict[str, object]) -> list[int]:
        if kind == "conversation":
            for field in ("ownerTaskId", "ownerConversationId"):
                if field in value:
                    key = "taskId" if field == "ownerTaskId" else "conversationId"
                    return list(
                        self._state.owners.get((key, cast(int, value[field])), ())
                    )
        if "status" in value:
            return list(
                self._state.statuses.get((kind, cast(str, value["status"])), ())
            )
        return sorted(self._state.tables[kind])

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[ConversationRecord]:
        with self._lock:
            return cast(
                Page[ConversationRecord],
                self._scan("conversation", query, ConversationQuery, limit, cursor),
            )

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[TaskRecord]:
        with self._lock:
            return cast(
                Page[TaskRecord], self._scan("task", query, TaskQuery, limit, cursor)
            )

    def scan_submissions(
        self, query: SubmissionQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[SubmissionRecord]:
        with self._lock:
            return cast(
                Page[SubmissionRecord],
                self._scan("submission", query, SubmissionQuery, limit, cursor),
            )

    def _documents(self) -> NoReturn:
        self._open()
        raise UnsupportedStorageOperation("Documents require DE02a2b")

    def document(
        self, id: DocumentId, at: DocumentPoint = "current"
    ) -> StoredDocument | None:
        with self._lock:
            self._documents()

    def find_document(
        self, address: DocumentAddress, at: DocumentPoint = "current"
    ) -> DocumentRecord | None:
        with self._lock:
            self._documents()

    def scan_documents(
        self, query: DocumentQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[DocumentRecord]:
        with self._lock:
            self._documents()

    def close(self) -> None:
        with self._lock:
            self._closed = True


def _matches(record: dict[str, object], query: dict[str, object]) -> bool:
    for key, expected in query.items():
        if key == "order":
            continue
        if key.startswith("owner"):
            owner = cast(dict[str, object], record.get("owner", {}))
            actual = owner.get("taskId" if key == "ownerTaskId" else "conversationId")
        elif key == "status" and "state" in record:
            actual = cast(dict[str, object], record["state"])["status"]
        else:
            actual = record.get(key)
        if key == "kind":
            if type(actual) is not str or _units(actual) != _units(cast(str, expected)):
                return False
        elif actual != expected:
            return False
    return True
