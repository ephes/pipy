"""Bounded Storage over an owned durable SQLite store (DE03c1); not exported.

``open_sqlite_storage`` opens a store (``sqlite_connection``, DE03a and DE03b)
and wraps it in ``SqliteStorage``, which implements the Storage port with
MemoryStorage's sixteen signatures and its table semantics: commits of table
writes, ``mint_id``, the exact reads, ``entry`` in both forms, the latest head
marker, the keyset-paged scans and ``submission_by_request``. Documents belong
to DE03c2: until then the three document methods, and every batch with a
document write, refuse with ``UnsupportedStorageOperation``, the error
MemoryStorage raises for a capability it lacks. There is no product factory
(DE11).

Where Pi's SQLite backend differs from Pi's Memory, this backend follows
MemoryStorage (Root decision D2): the last writer of a request ID wins without
fallback (the ``submission_requests`` table), descending scans include ID
2**53 - 1, raw page limits keep Memory's page shapes and errors (its own
``_page``), and a dangling parent raises ``KeyError`` at the point where
Memory's traversal reads it. Ingress is MemoryStorage's, before any statement:
its admission helpers ``_query``, ``_start`` and ``_page`` are reused, and its
two inline checks (an exact tuple of owned writes, an exact ``str`` request ID)
are repeated word for word.

Authority. One non-reentrant lock, held around every method including
``close``, serializes the single connection, and nothing caller-supplied runs
under it. A forked child is refused before that lock, as DE03a's owners refuse
it before theirs: every method raises ``StorageForkedError`` and ``close``
returns, so the child never waits for a lock that a thread of its parent held
at ``fork()``. Reads that need several statements (entry ancestry, the head marker,
entry scans) run in one read transaction, so they see one committed state. A
commit takes Pi's order: ``BEGIN IMMEDIATE``, the metadata row (the next
sequence and the persisted ID counter are authoritative in the database),
sequence exhaustion, the global ID claims, the table writes, the metadata
update and ``COMMIT``; the counter of minted IDs is adopted only after
``COMMIT`` returned. An error before ``COMMIT`` rolls back, but only while a
transaction is still open (SQLite rolls some failures back itself), and leaves
the storage usable. An error raised by ``COMMIT`` leaves the outcome unknown,
since WAL recovery can still find a complete commit, so it poisons the instance
(Root decision D4): every later call raises ``StorageClosedError`` until it is
closed and the store opened again. A failing rollback raises
``SqliteRollbackError`` and poisons the instance too, because the connection
may still be inside the transaction, where reads would see its uncommitted rows.
Every statement runs inside ``damage_as_corruption``.

Values. A record is stored as compact JSON text in UTF-8 that keeps lone
surrogates (``surrogatepass``) in a ``BLOB`` column (D1), so every owned value
reads back exactly: signed zero, integers beyond 64 bits, key order and string
spelling included. The text is what ``json.dumps(value, ensure_ascii=False,
allow_nan=False, separators=(",", ":"))`` writes, but it is written and read
without recursion: owned values have no nesting limit, and ``json`` stops at
Python's recursion limit (about 1000 levels on Python 3.11). Indexed strings
(task ``kind``, request IDs) are stored as ASCII JSON, which is equal exactly
when the UTF-16 code units are. Every read of a row makes every check: each
column's storage class must be exactly one its declared type allows (STRICT
keeps them, but SQL alone can store another by rewriting a table's schema and
restoring it) before anything is decoded; then it decodes and validates the
record, compares the row's index columns with it and, for an entry, checks the
commit sequence. A failure is ``StorageCorruption``, never a request error, and
its message never quotes stored data. No stored value is decoded by
``sqlite3`` (whose error would be neither SQLite's nor ``StorageCorruption``):
text is read as bytes and decoded strictly (``typed_columns``,
``typed_values``). The request bindings and ID claims are read the same way.

Source: Pi durable 1.3.0 at 2ec55d808d455b1a64a2bf7888198207a096120a,
``packages/durable/src/storage/sqlite/storage.ts`` (blob
7dc5903828740fe278fdc692ef9f40dbf68982c7, sha256
6778529dada8ac79d54328f7e8924d14a2a25bf33befc186c078482d27e8fe4a) and
``packages/durable/src/storage/scan.ts`` (blob
ed6ec89512ce08a6ca782bac2ed251bc32ae2d08). MIT Copyright (c) 2025 Mario
Zechner; notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import TypeVar, cast

from . import memory as reference
from .ownership import StorageForkedError
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
from .sqlite_connection import OwnedDatabase, open_owned_database
from .sqlite_schema import (
    BLOB,
    INTEGER,
    NULLABLE_INTEGER,
    NULLABLE_TEXT,
    TABLES,
    TEXT,
    SqliteRollbackError,
    StorageCorruption,
    damage_as_corruption,
    read_metadata,
    roll_back,
    typed_columns,
    typed_values,
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

T = TypeVar("T")

# More rows than any table holds; SQLite cannot bind a LIMIT beyond 2**63 - 1.
_MAX_ROWS = 2**53


# Values -------------------------------------------------------------------

_TOKEN = re.compile(
    r"[ \t\n\r]*+(?:"
    r"(?P<mark>[\[\]{},:])"
    r'|(?P<string>"(?:[^"\\\x00-\x1f]++|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*+")'
    r"|(?P<number>-?(?:0|[1-9][0-9]*+)(?P<real>(?:\.[0-9]++)?(?:[eE][-+]?[0-9]++)?))"
    r"|(?P<literal>true|false|null))"
)
_LITERALS: dict[str, object] = {"true": True, "false": False, "null": None}


def _scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _encode(value: object) -> bytes:
    """Compact JSON text of an owned tree, as UTF-8 that keeps lone surrogates."""

    parts: list[str] = []
    work: list[object] = [value]
    while work:
        item = work.pop()
        if isinstance(item, tuple):  # structural text; owned trees hold no tuples
            parts.append(item[0])
        elif isinstance(item, dict):
            parts.append("{")
            work.append(("}",))
            pairs = list(item.items())
            for index in range(len(pairs) - 1, -1, -1):
                key, child = pairs[index]
                work.append(child)
                work.append((("," if index else "") + _scalar(key) + ":",))
        elif isinstance(item, list):
            parts.append("[")
            work.append(("]",))
            for index in range(len(item) - 1, -1, -1):
                work.append(item[index])
                if index:
                    work.append((",",))
        else:
            parts.append(_scalar(item))
    return "".join(parts).encode("utf-8", "surrogatepass")


def _string(token: str) -> str:
    return token[1:-1] if "\\" not in token else cast(str, json.loads(token))


def _token_value(match: re.Match[str]) -> object:
    text = match["string"]
    if text is not None:
        return _string(text)
    number = match["number"]
    if number is not None:
        return float(number) if match["real"] else int(number)
    return _LITERALS[match["literal"]]


class _Reader:
    """JSON text to a plain tree, one token at a time, without recursion."""

    def __init__(self) -> None:
        self.state = "value"
        self.root: object = None
        self._stack: list[list[object] | dict[str, object]] = []
        self._keys: list[str] = []

    def feed(self, match: re.Match[str]) -> None:
        mark = match["mark"]
        if mark is None:
            self._token(match)
        elif mark in "[{":
            self._expect("value", "first")
            self._stack.append([] if mark == "[" else {})
            self.state = "first" if mark == "[" else "first key"
        elif mark in "]}":
            closing = (
                ("first", "array next") if mark == "]" else ("first key", "object next")
            )
            self._expect(*closing)
            self._attach(self._stack.pop())
        elif mark == ",":
            self._expect("array next", "object next")
            self.state = "value" if self.state == "array next" else "key"
        else:
            self._expect("colon")
            self.state = "value"

    def _token(self, match: re.Match[str]) -> None:
        if self.state in ("key", "first key"):
            if match["string"] is None:
                raise ValueError("A JSON object key must be a string")
            self._keys.append(_string(match["string"]))
            self.state = "colon"
            return
        self._expect("value", "first")
        self._attach(_token_value(match))

    def _attach(self, value: object) -> None:
        if not self._stack:
            self.root, self.state = value, "end"
            return
        top = self._stack[-1]
        if isinstance(top, list):
            top.append(value)
            self.state = "array next"
            return
        key = self._keys.pop()
        if key in top:
            raise ValueError("A JSON object repeats a key")
        top[key] = value
        self.state = "object next"

    def _expect(self, *states: str) -> None:
        if self.state not in states:
            raise ValueError("Malformed JSON text")


def _decode(blob: object) -> object:
    """The plain tree of a stored payload; any malformation raises ``ValueError``."""

    if type(blob) is not bytes:
        raise ValueError("A stored payload must be a BLOB")
    text = blob.decode("utf-8", "surrogatepass")
    reader = _Reader()
    position = 0
    while (match := _TOKEN.match(text, position)) is not None:
        position = match.end()
        reader.feed(match)
    if reader.state != "end" or text[position:].strip(" \t\n\r"):
        raise ValueError("Malformed JSON text")
    return reader.root


def _identity(text: str) -> str:
    """ASCII JSON of an indexed string: equal exactly when the UTF-16 units are."""

    return json.dumps(text, ensure_ascii=True)


# Tables -------------------------------------------------------------------

_TABLES = {
    "conversation": TABLES["conversations"],
    "entry": TABLES["entries"],
    "task": TABLES["tasks"],
    "submission": TABLES["submissions"],
}
# Every stored column, in row order, with the storage classes its declared
# type allows; the index columns come first and are recomputed from the
# record on every read to detect damage.
_COLUMNS: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
    "conversation": (
        ("id", INTEGER),
        ("owner_conversation_id", NULLABLE_INTEGER),
        ("owner_task_id", NULLABLE_INTEGER),
        ("record", BLOB),
    ),
    "entry": (
        ("id", INTEGER),
        ("conversation_id", INTEGER),
        ("head", NULLABLE_INTEGER),
        ("commit_seq", INTEGER),
        ("record", BLOB),
    ),
    "task": (
        ("id", INTEGER),
        ("conversation_id", INTEGER),
        ("kind", TEXT),
        ("status", TEXT),
        ("abort_requested", INTEGER),
        ("background", INTEGER),
        ("record", BLOB),
    ),
    "submission": (
        ("id", INTEGER),
        ("conversation_id", INTEGER),
        ("request_id", NULLABLE_TEXT),
        ("status", TEXT),
        ("record", BLOB),
    ),
}
_INDEXED = {
    kind: tuple(name for name, _ in columns if name not in ("commit_seq", "record"))
    for kind, columns in _COLUMNS.items()
}
_FILTERS = {
    "conversation": (
        ("ownerConversationId", "owner_conversation_id"),
        ("ownerTaskId", "owner_task_id"),
    ),
    "task": (
        ("conversationId", "conversation_id"),
        ("kind", "kind"),
        ("status", "status"),
        ("abortRequested", "abort_requested"),
        ("background", "background"),
    ),
    "submission": (("conversationId", "conversation_id"), ("status", "status")),
}


def _stored(kind: str) -> tuple[str, ...]:
    return tuple(name for name, _ in _COLUMNS[kind])


def _insert(kind: str) -> str:
    columns = _stored(kind)
    sql = (
        f"INSERT INTO {_TABLES[kind]} ({', '.join(columns)})"
        f" VALUES ({', '.join('?' * len(columns))})"
    )
    if kind in ("task", "submission"):
        updates = ", ".join(f"{name} = excluded.{name}" for name in columns[1:])
        sql += f" ON CONFLICT (id) DO UPDATE SET {updates}"
    return sql


def _selected(kind: str, prefix: str = "") -> str:
    """Every stored column with its storage class (``typed_columns``)."""

    return typed_columns(*(prefix + name for name in _stored(kind)))


_SELECT = {kind: f"SELECT {_selected(kind)} FROM {_TABLES[kind]}" for kind in _TABLES}
_SELECT_ID = {kind: f"{sql} WHERE id = ?" for kind, sql in _SELECT.items()}
_INSERT = {kind: _insert(kind) for kind in _TABLES}
_CLAIM_TYPE = (("record_type", TEXT),)
_CLAIMED = (
    f"SELECT {typed_columns('record_type')} FROM {TABLES['record_ids']} WHERE id = ?"
)
_CLAIMS = frozenset({"conversation", "entry", "task", "submission", "document"})
_CLAIM = f"INSERT OR IGNORE INTO {TABLES['record_ids']} (id, record_type) VALUES (?, ?)"
_METADATA = (
    f"UPDATE {TABLES['durable_metadata']} SET next_id = ?, next_seq = ?"
    " WHERE singleton = 1"
)
_HEAD = (
    f"{_SELECT['entry']} WHERE conversation_id = ? AND head IS NOT NULL"
    " ORDER BY id DESC LIMIT 1"
)
_HEAD_AT = (
    f"{_SELECT['entry']} WHERE conversation_id = ? AND head IS NOT NULL AND id <= ?"
    " ORDER BY id DESC LIMIT 1"
)
# Memory's last-writer rule: a rewrite drops only the binding it won, the one
# of its previous request ID (no previous row or request ID: nothing matches).
# Both parameters are the submission ID: plain "?" placeholders, because some
# Python 3.12 releases warn about numbered ones bound from a sequence.
_DROP_WINNER = (
    f"DELETE FROM {TABLES['submission_requests']} WHERE submission_id = ?"
    " AND (conversation_id, request_id) = (SELECT conversation_id, request_id"
    f" FROM {TABLES['submissions']} WHERE id = ?)"
)
_SET_WINNER = (
    f"INSERT INTO {TABLES['submission_requests']}"
    " (conversation_id, request_id, submission_id) VALUES (?, ?, ?)"
    " ON CONFLICT (conversation_id, request_id)"
    " DO UPDATE SET submission_id = excluded.submission_id"
)
# A request binding's own columns, then its submission's (all NULL if missing).
_BINDING = (
    ("conversation_id", INTEGER),
    ("request_id", TEXT),
    ("submission_id", INTEGER),
)
_WINNER = (
    f"SELECT {typed_columns(*(f'r.{name}' for name, _ in _BINDING))},"
    f" {_selected('submission', 's.')}"
    f" FROM {TABLES['submission_requests']} AS r"
    f" LEFT JOIN {TABLES['submissions']} AS s ON s.id = r.submission_id"
    " WHERE r.conversation_id = ? AND r.request_id = ?"
)


def _columns(kind: str, value: dict[str, object]) -> tuple[object, ...]:
    """A record's index columns, as the row stores them."""

    if kind == "conversation":
        owner = cast(dict[str, object] | None, value.get("owner"))
        return (
            value["id"],
            None if owner is None else owner["conversationId"],
            None if owner is None else owner["taskId"],
        )
    if kind == "entry":
        return (value["id"], value["conversationId"], value.get("head"))
    if kind == "task":
        return (
            value["id"],
            value["conversationId"],
            _identity(cast(str, value["kind"])),
            cast(dict[str, object], value["state"])["status"],
            int(cast(bool, value["abortRequested"])),
            int(cast(bool, value["background"])),
        )
    request = cast(str | None, value.get("requestId"))
    return (
        value["id"],
        value["conversationId"],
        None if request is None else _identity(request),
        value["status"],
    )


def _checked(
    kind: str, row: Sequence[object], label: str
) -> tuple[Record, tuple[object, ...]]:
    """One selected row (``_SELECT`` shape), checked; its record and values.

    Every check of a stored row is made here, so every read path makes all of
    them: each column's exact storage class before anything is decoded, strict
    text decoding, the record, its index columns and, for an entry, the commit
    sequence.
    """

    columns = _COLUMNS[kind]
    # The ID is an INTEGER PRIMARY KEY, SQLite's rowid: always an integer.
    ident = f" (ID {row[1]})" if row[0] == "integer" else ""
    noun = f"{'an' if kind == 'entry' else 'a'} {kind} record{ident}"

    def refuse(column: int, problem: str) -> StorageCorruption:
        name = columns[column][0]
        return StorageCorruption(
            f"The {label} holds {noun} whose {name} column {problem}"
        )

    values = typed_values(row, [allowed for _, allowed in columns], refuse)
    try:
        record = RECORD_TYPES[kind](_decode(values[-1]))
    except ValueError as error:
        raise StorageCorruption(
            f"The {label} holds an unreadable {kind} record{ident}"
        ) from error
    if _columns(kind, record.to_tree()) != values[: len(_INDEXED[kind])]:
        raise StorageCorruption(
            f"The {label} holds {noun} that its index columns contradict"
        )
    if kind == "entry" and not 1 <= cast(int, values[3]) <= SAFE_INTEGER:
        raise StorageCorruption(
            f"The {label} holds an entry{ident} without a commit sequence"
        )
    return record, values


def _record(kind: str, row: Sequence[object], label: str) -> Record:
    return _checked(kind, row, label)[0]


def _lookup(row: Sequence[object], label: str) -> EntryLookup:
    entry, values = _checked("entry", row, label)
    return EntryLookup(cast(EntryRecord, entry), Seq(cast(int, values[3])))


def _filter(key: str, value: object) -> object:
    if key == "kind":
        return _identity(cast(str, value))
    return int(value) if type(value) is bool else value


def _admit(writes: object) -> list[dict[str, object]]:
    """MemoryStorage's write ingress, then this backend's capability bound."""

    if type(writes) is not tuple or any(type(w) is not StorageWrite for w in writes):
        raise StorageRequestError("Expected owned write tuple")
    trees = [cast(StorageWrite, write).to_tree() for write in writes]
    if any(
        value["type"] == "document.copy"
        or (
            "content" in value
            and cast(dict[str, object], value["content"])["kind"] != "base"
        )
        for value in trees
    ):
        raise UnsupportedStorageOperation("Document delta/copy requires DE04/DE27")
    if any(cast(str, value["type"]).startswith("document.") for value in trees):
        raise _no_documents()
    return trees


def _no_documents() -> UnsupportedStorageOperation:
    return UnsupportedStorageOperation("SqliteStorage documents require DE03c2")


def _claimed(row: Sequence[object], id: int, label: str) -> str:
    """A stored ID claim's record type: text, decoded strictly, a known type."""

    (kind,) = typed_values(
        row,
        [allowed for _, allowed in _CLAIM_TYPE],
        lambda _, problem: StorageCorruption(
            f"The {label} holds an ID claim ({id}) whose record_type column {problem}"
        ),
    )
    if kind not in _CLAIMS:
        raise StorageCorruption(f"The {label} holds an unreadable ID claim ({id})")
    return kind


def _claim(
    connection: sqlite3.Connection, trees: list[dict[str, object]], label: str
) -> None:
    """MemoryStorage's global ID rules, against ``record_ids`` (Pi checkGlobalIds)."""

    claims: dict[int, str] = {}
    for tree in trees:
        kind = cast(str, tree["type"])
        id = cast(int, cast(dict[str, object], tree["value"])["id"])
        row = connection.execute(_CLAIMED, (id,)).fetchone()
        existing = None if row is None else _claimed(row, id, label)
        earlier = claims.get(id)
        if kind in {"conversation", "entry"}:
            if existing is not None:
                raise RuntimeError(f"ID {id} already belongs to {existing}")
            if earlier is not None:
                raise RuntimeError(f"ID {id} is written more than once")
        else:
            if existing not in (None, kind):
                raise RuntimeError(f"ID {id} already belongs to {existing}")
            if earlier not in (None, kind):
                raise RuntimeError(f"ID {id} is written as two record types")
        claims[id] = kind


def _write(connection: sqlite3.Connection, tree: dict[str, object], seq: int) -> None:
    kind = cast(str, tree["type"])
    record = RECORD_TYPES[kind](tree["value"])
    value = record.to_tree()
    connection.execute(_CLAIM, (record.id, kind))
    if kind == "submission":
        connection.execute(_DROP_WINNER, (record.id, record.id))
    commit_seq = (seq,) if kind == "entry" else ()
    connection.execute(
        _INSERT[kind], (*_columns(kind, value), *commit_seq, _encode(value))
    )
    if kind == "submission" and "requestId" in value:
        request = _identity(cast(str, value["requestId"]))
        connection.execute(_SET_WINNER, (value["conversationId"], request, record.id))


def _segment(
    owner: int, minimum: int | None, upper: int | None, order: ScanOrder, rows: int
) -> tuple[str, list[object]]:
    clauses, params = ["conversation_id = ?"], [cast(object, owner)]
    if minimum is not None:
        clauses.append("id >= ?")
        params.append(minimum)
    if upper is not None:
        clauses.append("id <= ?")
        params.append(upper)
    params.append(rows)
    direction = "ASC" if order == "ascending" else "DESC"
    where = " AND ".join(clauses)
    return f"{_SELECT['entry']} WHERE {where} ORDER BY id {direction} LIMIT ?", params


def _scan_sql(
    kind: str, value: dict[str, object], order: ScanOrder, after: int | None, limit: int
) -> tuple[str, list[object]]:
    """A keyset table scan: the cursor's ID, the query's filters, one page plus one."""

    clauses: list[str] = []
    params: list[object] = []
    if after is not None:
        clauses.append("id > ?" if order == "ascending" else "id < ?")
        params.append(after)
    for key, column in _FILTERS[kind]:
        if key in value:
            clauses.append(f"{column} = ?")
            params.append(_filter(key, value[key]))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "ASC" if order == "ascending" else "DESC"
    # Memory checks before it appends: a negative limit reads nothing.
    params.append(min(max(limit + 1, 0), _MAX_ROWS))
    return f"{_SELECT[kind]}{where} ORDER BY id {direction} LIMIT ?", params


def _before_commit(connection: sqlite3.Connection) -> None:
    """Test seam: runs in the commit transaction after every write, before COMMIT."""


class SqliteStorage:
    """MemoryStorage's bounded table semantics over one owned durable store."""

    def __init__(self, database: OwnedDatabase) -> None:
        self._database = database
        self._label = f"database {database.path.database!r}"
        self._lock = threading.Lock()
        self._closed = False
        self._failed: BaseException | None = None
        with damage_as_corruption(self._label):
            self._next_id = read_metadata(database.connection())[0]

    @contextlib.contextmanager
    def _authority(self) -> Iterator[sqlite3.Connection]:
        """The instance lock and the usable connection, in that order.

        A forked child is refused before the lock: another thread may have held
        it at fork(), and the child would wait for it forever (DE03a's owners
        check fork before their locks too).
        """

        if self._database.owner.forked:
            raise StorageForkedError(
                "A durable store owner cannot be used after fork()"
            )
        with self._lock:
            if self._closed:
                raise StorageClosedError("SqliteStorage is closed")
            if self._failed is not None:
                raise StorageClosedError(
                    "SqliteStorage cannot continue after a failed COMMIT or rollback;"
                    " close it and open the store again"
                ) from self._failed
            yield self._database.connection()

    def _roll_back(self, connection: sqlite3.Connection, error: BaseException) -> None:
        try:
            roll_back(connection, error)
        except SqliteRollbackError as failure:
            # The connection may still be inside the transaction, where reads
            # would see its uncommitted rows: nothing may use it any more.
            self._failed = failure
            raise

    def _read(self, connection: sqlite3.Connection, read: Callable[[], T]) -> T:
        """Run a read of several statements in one transaction: one committed state."""

        try:
            connection.execute("BEGIN")
            result = read()
            connection.execute("COMMIT")
        except BaseException as error:
            self._roll_back(connection, error)
            raise
        return result

    def commit(self, writes: tuple[StorageWrite, ...]) -> Seq:
        with self._authority() as connection:
            trees = _admit(writes)
            candidate = max(
                [
                    self._next_id,
                    *(
                        cast(int, cast(dict[str, object], t["value"])["id"]) + 1
                        for t in trees
                    ),
                ]
            )
            with damage_as_corruption(self._label):
                return self._commit(connection, trees, candidate)

    def _commit(
        self,
        connection: sqlite3.Connection,
        trees: list[dict[str, object]],
        candidate: int,
    ) -> Seq:
        try:
            connection.execute("BEGIN IMMEDIATE")
            next_id, seq = read_metadata(connection)
            if seq > SAFE_INTEGER:
                raise RuntimeError(f"Commit sequence {seq} does not strictly increase")
            _claim(connection, trees, self._label)
            for tree in trees:
                _write(connection, tree, seq)
            next_id = max(next_id, candidate)
            connection.execute(_METADATA, (next_id, seq + 1))
            _before_commit(connection)
        except BaseException as error:
            self._roll_back(connection, error)
            raise
        try:
            connection.execute("COMMIT")
        except BaseException as error:
            # D4: WAL recovery may still find this commit; only a reopen can tell.
            self._failed = error
            self._roll_back(connection, error)
            raise
        # Durable from here: adopt the persisted counter before anything else.
        self._next_id = max(self._next_id, next_id)
        return Seq(seq)

    def mint_id(self) -> int:
        with self._authority():
            id = self._next_id
            if id > SAFE_INTEGER:
                raise RuntimeError("ID space is exhausted")
            self._next_id = id + 1
            return id

    def _exact(self, kind: str, id: int) -> Record | None:
        with self._authority() as connection:
            safe_integer(id)
            with damage_as_corruption(self._label):
                row = connection.execute(_SELECT_ID[kind], (id,)).fetchone()
            return None if row is None else _record(kind, row, self._label)

    def conversation(self, id: ConversationId) -> ConversationRecord | None:
        return cast(ConversationRecord | None, self._exact("conversation", id))

    def task(self, id: TaskId) -> TaskRecord | None:
        return cast(TaskRecord | None, self._exact("task", id))

    def submission(self, id: SubmissionId) -> SubmissionRecord | None:
        return cast(SubmissionRecord | None, self._exact("submission", id))

    def submission_by_request(
        self, conversation_id: ConversationId, request_id: str
    ) -> SubmissionRecord | None:
        with self._authority() as connection:
            safe_integer(conversation_id)
            if type(request_id) is not str:
                raise StorageRequestError("Expected request ID string")
            key = (conversation_id, _identity(request_id))
            with damage_as_corruption(self._label):
                row = connection.execute(_WINNER, key).fetchone()
            if row is None:
                return None
            binding = 2 * len(_BINDING)
            typed_values(
                row[:binding],
                [allowed for _, allowed in _BINDING],
                lambda column, problem: StorageCorruption(
                    f"The {self._label} holds a request binding whose"
                    f" {_BINDING[column][0]} column {problem}"
                ),
            )
            if row[binding] == "null":  # no submission joined the binding
                raise StorageCorruption(
                    f"The {self._label} names a missing submission as a request winner"
                )
            record = _record("submission", row[binding:], self._label)
            # The row's columns match its record: compare the record's key.
            if _columns("submission", record.to_tree())[1:3] != key:
                raise StorageCorruption(
                    f"The {self._label} names submission {record.id} as the winner of"
                    " another request"
                )
            return cast(SubmissionRecord, record)

    def _conversation(
        self, connection: sqlite3.Connection, id: int
    ) -> dict[str, object] | None:
        row = connection.execute(_SELECT_ID["conversation"], (id,)).fetchone()
        return (
            None if row is None else _record("conversation", row, self._label).to_tree()
        )

    def _segments(
        self,
        connection: sqlite3.Connection,
        conversation: int,
        maximum: int | None = None,
        minimum: int | None = None,
    ) -> Iterator[tuple[int, int | None]]:
        """MemoryStorage's fork-chain walk, reading each parent only when resumed."""

        value = self._conversation(connection, conversation)
        if value is None:
            raise StorageRequestError(f"Unknown conversation: {conversation}")
        while True:
            # Raw storage need not hold a valid Session graph: yield this
            # segment before reading its record, as Memory does.
            yield conversation, maximum
            if value is None:
                value = self._conversation(connection, conversation)
                if value is None:
                    raise KeyError(conversation)
            parent = cast(dict[str, int] | None, value.get("parent"))
            if parent is None:
                return
            maximum = parent["at"] if maximum is None else min(maximum, parent["at"])
            if minimum is not None and maximum < minimum:
                return
            conversation, value = parent["conversationId"], None

    def _visible_entry(
        self, connection: sqlite3.Connection, conversation: int, id: int
    ) -> EntryLookup | None:
        row = connection.execute(_SELECT_ID["entry"], (id,)).fetchone()
        lookup = None if row is None else _lookup(row, self._label)
        owner = None if lookup is None else lookup.entry.to_tree()["conversationId"]
        for segment, _ in self._segments(connection, conversation, id, id):
            if segment == owner:
                return lookup
        return None

    def entry(
        self, id: EntryId, *, conversation_id: ConversationId | None = None
    ) -> EntryLookup | None:
        with self._authority() as connection:
            safe_integer(id)
            if conversation_id is not None:
                safe_integer(conversation_id)
                with damage_as_corruption(self._label):
                    return self._read(
                        connection,
                        lambda: self._visible_entry(connection, conversation_id, id),
                    )
            with damage_as_corruption(self._label):
                row = connection.execute(_SELECT_ID["entry"], (id,)).fetchone()
            return None if row is None else _lookup(row, self._label)

    def _head(
        self, connection: sqlite3.Connection, conversation: int, bound: int | None
    ) -> EntryRecord | None:
        for owner, upper in self._segments(connection, conversation, bound):
            params = (owner,) if upper is None else (owner, upper)
            row = connection.execute(
                _HEAD if upper is None else _HEAD_AT, params
            ).fetchone()
            if row is not None:
                return cast(EntryRecord, _record("entry", row, self._label))
        return None

    def find_latest_head_marker(
        self,
        conversation_id: ConversationId,
        at_or_before_entry_id: EntryId | None = None,
    ) -> EntryRecord | None:
        with self._authority() as connection:
            safe_integer(conversation_id)
            if at_or_before_entry_id is not None:
                safe_integer(at_or_before_entry_id)
            with damage_as_corruption(self._label):
                return self._read(
                    connection,
                    lambda: self._head(
                        connection, conversation_id, at_or_before_entry_id
                    ),
                )

    def _entries(
        self,
        connection: sqlite3.Connection,
        conversation: int,
        bounds: tuple[int | None, int | None],
        order: ScanOrder,
        wanted: int,
    ) -> list[EntryRecord]:
        minimum, maximum = bounds
        segments: Iterable[tuple[int, int | None]] = self._segments(
            connection, conversation, maximum, minimum
        )
        if order == "ascending":
            # Pi deliberately builds the complete capped segment list first.
            segments = reversed(list(segments))
        records: list[EntryRecord] = []
        for owner, upper in segments:
            sql, params = _segment(owner, minimum, upper, order, wanted - len(records))
            for row in connection.execute(sql, params).fetchall():
                records.append(cast(EntryRecord, _record("entry", row, self._label)))
            # Memory stops right after the record that overfills the page.
            if len(records) >= wanted:
                break
        return records

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[EntryRecord]:
        with self._authority() as connection:
            value = reference._query(query, EntryQuery, limit)
            order, after = reference._start(value, cursor, "descending")
            minimum = cast(int | None, value.get("minEntryId"))
            maximum = cast(int | None, value.get("maxEntryId"))
            if after is not None and order == "ascending":
                minimum = after + 1 if minimum is None else max(minimum, after + 1)
            elif after is not None:
                maximum = after - 1 if maximum is None else min(maximum, after - 1)
            conversation = cast(int, value["conversationId"])
            # Memory appends before it checks, so even a negative limit reads one.
            wanted = min(max(limit + 1, 1), _MAX_ROWS)
            with damage_as_corruption(self._label):
                records = self._read(
                    connection,
                    lambda: self._entries(
                        connection, conversation, (minimum, maximum), order, wanted
                    ),
                )
            return reference._page(records, limit, order)

    def _scan(
        self,
        kind: str,
        query: Query,
        cls: type[Query],
        limit: int,
        cursor: Cursor | None,
    ) -> Page[Record]:
        with self._authority() as connection:
            value = reference._query(query, cls, limit)
            order, after = reference._start(value, cursor, "ascending")
            sql, params = _scan_sql(kind, value, order, after, limit)
            with damage_as_corruption(self._label):
                rows = connection.execute(sql, params).fetchall()
            records = [_record(kind, row, self._label) for row in rows]
            return reference._page(records, limit, order)

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[ConversationRecord]:
        return cast(
            Page[ConversationRecord],
            self._scan("conversation", query, ConversationQuery, limit, cursor),
        )

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[TaskRecord]:
        return cast(
            Page[TaskRecord], self._scan("task", query, TaskQuery, limit, cursor)
        )

    def scan_submissions(
        self, query: SubmissionQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[SubmissionRecord]:
        return cast(
            Page[SubmissionRecord],
            self._scan("submission", query, SubmissionQuery, limit, cursor),
        )

    def document(
        self, id: DocumentId, at: DocumentPoint = "current"
    ) -> StoredDocument | None:
        with self._authority():
            raise _no_documents()

    def find_document(
        self, address: DocumentAddress, at: DocumentPoint = "current"
    ) -> DocumentRecord | None:
        with self._authority():
            raise _no_documents()

    def scan_documents(
        self, query: DocumentQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[DocumentRecord]:
        with self._authority():
            raise _no_documents()

    def close(self) -> None:
        if self._database.owner.forked:
            return  # A forked child owns nothing and never touches the lock.
        with self._lock:
            self._closed = True
            # Idempotent: a repeated close repeats the database's own outcome.
            self._database.close()


def open_sqlite_storage(
    path: object, *, mode: object, durability: object = "full"
) -> SqliteStorage:
    """Own and open (``mode="open"``) or create a store as a ``SqliteStorage``.

    ``open_owned_database`` admits the input, takes the path lock, connects and
    migrates; a failure after that closes the database again, keeping the
    original error.
    """

    database = open_owned_database(path, mode=mode, durability=durability)
    try:
        return SqliteStorage(database)
    except BaseException as error:
        try:
            database.close()
        except Exception as cleanup:  # noqa: BLE001 - kept as a note on the original error
            error.add_note(f"Closing the database also failed: {cleanup!r}")
        raise
