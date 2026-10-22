"""Internal detached tables and current base/delta documents; no product factory.

Source-derived Pi durable 1.3.0 memory.ts/scan.ts, MIT (c)2025 Mario Zechner;
notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt. Copy/historical/fork
materialization remain later capabilities; Session authority is not implemented.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import TypeVar, cast

from .codec import OwnedJson
from .delta_ops import apply_operations
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
    documents: dict[int, StoredDocument] = field(default_factory=dict)
    addresses: dict[tuple[object, ...], int] = field(default_factory=dict)
    scopes: dict[tuple[object, ...], tuple[int, ...]] = field(default_factory=dict)
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
    if any(value["type"] == "document.copy" for value in trees):
        raise UnsupportedStorageOperation("Document copy requires DE27")
    if state.next_seq > SAFE_INTEGER:
        raise RuntimeError(
            f"Commit sequence {state.next_seq} does not strictly increase"
        )
    _claims(state, trees)
    actions = _document_actions(trees)
    candidate = replace(
        state,
        tables={k: dict(v) for k, v in state.tables.items()},
        roles=dict(state.roles),
        entry_seqs=dict(state.entry_seqs),
        requests=dict(state.requests),
        documents=dict(state.documents),
        addresses=dict(state.addresses),
        next_seq=state.next_seq + 1,
    )
    for write in trees:
        if not cast(str, write["type"]).startswith("document."):
            candidate = _write(candidate, write, state.next_seq)
    candidate, live_counts = _prepare_documents(
        state, candidate, actions, state.next_seq
    )
    return _validate_document_candidate(_indexes(candidate), live_counts)


def _claims(state: _State, trees: list[dict[str, object]]) -> None:
    claims: dict[int, str] = {}
    for write in trees:
        kind = cast(str, write["type"])
        if kind in {"document.change", "document.retire"}:
            continue
        document = kind == "document.create"
        id = cast(
            int, cast(dict[str, object], write["record" if document else "value"])["id"]
        )
        kind = "document" if document else kind
        existing, earlier = state.roles.get(id), claims.get(id)
        if kind in {"conversation", "entry", "document"}:
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


def _scope_key(value: dict[str, object]) -> tuple[object, ...]:
    kind = cast(str, value["kind"])
    return (kind,) if kind == "session" else (kind, value[kind + "Id"])


def _address_key(value: dict[str, object]) -> tuple[object, ...]:
    return (
        _scope_key(cast(dict[str, object], value["scope"])),
        _units(cast(str, value["kind"])),
        ("family", _units(cast(str, value["key"])))
        if "key" in value
        else ("singleton",),
    )


def _document_actions(trees: list[dict[str, object]]) -> dict[int, dict[str, object]]:
    actions: dict[int, dict[str, object]] = {}
    for write in trees:
        kind = write["type"]
        if kind not in {"document.create", "document.change", "document.retire"}:
            continue
        id = cast(
            int,
            cast(dict[str, object], write["record"])["id"]
            if kind == "document.create"
            else write["id"],
        )
        action = actions.setdefault(id, {})
        if kind == "document.retire":
            if action.get("retire"):
                raise RuntimeError(f"Document {id} is retired more than once")
            action["retire"] = True
        else:
            if "content" in action:
                raise RuntimeError(f"Document {id} has more than one content command")
            action["content"] = write["content"]
            if kind == "document.create":
                action["create"] = write["record"]
    return actions


def _materialize_current(
    stored: StoredDocument | None, id: int, action: dict[str, object], seq: int
) -> StoredDocument:
    existing = None if stored is None else stored.to_tree()
    if "create" not in action and existing is None:
        raise RuntimeError(f"Unknown document: {id}")
    if "create" in action and existing is not None:
        raise RuntimeError(f"Document {id} already exists")
    record = cast(dict[str, object], existing["record"]) if existing is not None else {}
    if "retiredAt" in record:
        raise RuntimeError(f"Document {id} is retired")
    if "create" in action:
        record = {**cast(dict[str, object], action["create"]), "createdAt": seq}
    content = cast(dict[str, object], action.get("content", {}))
    if content:
        if content["kind"] == "delta":
            if existing is None or existing["version"] != content["version"]:
                raise StorageRequestError(
                    "Document delta requires a matching base version"
                )
            # StorageWrite owns only the batch shape. Validate operations during
            # application, so an early path failure precedes later bad grammar.
            value = apply_operations(
                OwnedJson(existing["value"]), OwnedJson(content["ops"])
            ).thaw()
            count = cast(int, existing["deltasSinceBase"]) + 1
            version = existing["version"]
        else:
            value, count = content["value"], 0
            version = content["version"]
        materialized = {
            "record": record,
            "version": version,
            "value": value,
            "deltasSinceBase": count,
        }
    else:
        assert existing is not None
        materialized = existing
    if action.get("retire"):
        materialized = {**materialized, "record": {**record, "retiredAt": seq}}
    return StoredDocument(materialized)


def _prepare_documents(
    previous: _State, candidate: _State, actions: dict[int, dict[str, object]], seq: int
) -> tuple[_State, dict[tuple[object, ...], int]]:
    live_counts: dict[tuple[object, ...], int] = {}
    for id, action in actions.items():
        document = _materialize_current(previous.documents.get(id), id, action, seq)
        record = cast(dict[str, object], document.to_tree()["record"])
        key = _address_key(record)
        current = previous.addresses.get(key)
        count = live_counts.get(key, 0 if current is None else 1)
        if action.get("retire") and current == id:
            count -= 1
        if "create" in action and not action.get("retire"):
            count += 1
        live_counts[key] = count
        candidate.documents[id] = document
        if "create" in action:
            candidate.roles[id] = "document"
            candidate = replace(candidate, next_id=max(candidate.next_id, id + 1))
        # Source order can create a replacement before the old retirement.
        if action.get("retire") and candidate.addresses.get(key) == id:
            del candidate.addresses[key]
        if "create" in action and not action.get("retire"):
            candidate.addresses[key] = id
    scopes: dict[tuple[object, ...], list[int]] = {}
    for id, stored in sorted(candidate.documents.items()):
        record = cast(dict[str, object], stored.to_tree()["record"])
        scopes.setdefault(
            _scope_key(cast(dict[str, object], record["scope"])), []
        ).append(id)
    candidate = replace(candidate, scopes={k: tuple(v) for k, v in scopes.items()})
    return candidate, live_counts


def _validate_document_candidate(
    candidate: _State, live_counts: dict[tuple[object, ...], int]
) -> _State:
    # Final cardinality, after all supported table/document preparation. Never
    # publish a partial candidate, including on a late conflict/allocation fault.
    if any(count > 1 for count in live_counts.values()):
        raise RuntimeError("Document address already has a current incarnation")
    return candidate


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
    """Internal bounded Storage: tables/current base/delta, one candidate authority."""

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

    def document(
        self, id: DocumentId, at: DocumentPoint = "current"
    ) -> StoredDocument | None:
        with self._lock:
            self._open()
            safe_integer(id)
            _current(at)
            stored = self._state.documents.get(id)
            if stored is None or "retiredAt" in cast(
                dict[str, object], stored.to_tree()["record"]
            ):
                return None
            return StoredDocument(stored.to_tree())

    def find_document(
        self, address: DocumentAddress, at: DocumentPoint = "current"
    ) -> DocumentRecord | None:
        with self._lock:
            self._open()
            _current(at)
            if type(address) is not DocumentAddress:
                raise StorageRequestError("Expected owned document address")
            id = self._state.addresses.get(_address_key(address.to_tree()))
            return (
                None
                if id is None
                else DocumentRecord(self._state.documents[id].to_tree()["record"])
            )

    def scan_documents(
        self, query: DocumentQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[DocumentRecord]:
        with self._lock:
            self._open()
            value = _query(query, DocumentQuery, limit)
            _current(cast(DocumentPoint, value["at"]))
            if cursor is not None and type(cursor) is not Cursor:
                raise StorageRequestError("Invalid storage cursor")
            after = None if cursor is None else cursor.after
            records: list[DocumentRecord] = []
            for id in self._state.scopes.get(
                _scope_key(cast(dict[str, object], value["scope"])), ()
            ):
                if len(records) > limit:
                    break
                if after is not None and id <= after:
                    continue
                record = cast(
                    dict[str, object], self._state.documents[id].to_tree()["record"]
                )
                if "retiredAt" in record or (
                    "kind" in value
                    and _units(cast(str, record["kind"]))
                    != _units(cast(str, value["kind"]))
                ):
                    continue
                records.append(DocumentRecord(record))
            # Unlike table scans, source cursorId ignores order entirely.
            return _page(records, limit, "ascending")

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


def _current(at: DocumentPoint) -> None:
    # Admit exact built-ins before comparison/coercion/repr: invalid Python
    # objects must never execute caller hooks under the non-reentrant authority.
    if type(at) is str and at == "current":
        return
    safe_integer(at)
    raise UnsupportedStorageOperation("Numeric document history requires DE04/DE27")
