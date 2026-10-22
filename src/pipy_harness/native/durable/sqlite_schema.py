"""Schema v2, immutable v1 migrations and durable store open-time integrity.

Private and not exported. ``sqlite_connection.open_owned_database`` runs this
module on every ``create`` and ``open``; Storage semantics follow in DE03c.

Schema v1 has the shape of Pi durable 1.3.0's SQLite schema version 2 (tasks
with the ``sleeping`` status), Pi's table, index and column names and no table
prefix (Root decisions D7 and D11), plus these Python deltas (D1, D2):

- The payload columns, ``record`` in every record table and
  ``document_revisions.content``, are ``BLOB`` instead of ``TEXT CHECK
  (json_valid(...))``. DE03c stores UTF-8 JSON that keeps lone surrogates
  (``surrogatepass``) there: lossless for every owned value, and no JSON1
  functions (SQLite before 3.45 rejects BLOB arguments to them).
- ``document_revisions.version`` is a ``BLOB`` holding the version's JSON
  number instead of an ``INTEGER``, so a raw Storage version keeps ``0``,
  ``-1``, ``1.5``, ``-0.0`` and integers beyond 64 bits, as MemoryStorage does.
- ``durable_metadata.next_id`` is an ``INTEGER``; Pi's ``TEXT`` works around
  ``node:sqlite`` refusing integers beyond JavaScript's safe range.
- ``submission_requests`` maps a conversation's request ID to the submission
  that wrote it last, for MemoryStorage's last-writer rule without fallback
  (D2); Pi's SQLite backend returns the lowest matching ID instead.
- Tasks are created in their final shape, without Pi's version 1 to 2 rebuild.
- The database header names the store: ``application_id`` is ``0x50495059``
  ("PIPY") and ``user_version`` is the schema version, so the gate can refuse
  foreign and newer files before SQLite opens them.

The identity columns (task ``kind``, ``request_id``, document ``kind`` and
``key_value``) stay ``TEXT``; DE03c fills them with ASCII JSON (D1). Names come
from ``TABLES`` and ``INDEXES``, Pi's immutable identifier history, so a prefix
could still be added later.

Migrations, a port of Pi chord's ``applySqliteMigrations``. ``MIGRATIONS`` is
append-only: a released migration is never edited or removed, and versions are
contiguous from 1, which is checked before the database is touched. One
``BEGIN IMMEDIATE`` transaction creates ``durable_schema`` when it is missing,
reads its version, refuses a newer one, and applies every pending migration
with its version bump (``durable_schema`` and ``user_version``; version 1 also
sets ``application_id``). Any error rolls all of it back and is raised
unchanged; a failed rollback raises ``SqliteRollbackError`` with both errors.
A crash leaves the old version, because SQLite discards an uncommitted
transaction, and the next ``open`` migrates again. Migrations run only under
the path lock; a read-only inspection (DE10) validates without migrating. A
change to what stored bytes mean needs a new version even without new DDL.
DE04b1b2 appends semantic version 2 with no statements: v1 already admits delta
rows, and the version prevents older pipy from opening a store requiring replay. Full histories/as-of/fork/migrations remain
DE27.

Open-time integrity. Before any connection the gate in ``sqlite_connection``
reads the header through ownership's descriptor rule and ``check_identity``
refuses a foreign application ID, a newer ``user_version`` and an interrupted
create, leaving every byte unchanged. The database file's header lags the
``-wal``: the identity and ``user_version`` reach the file only at a checkpoint.
So after an unclean shutdown (a non-empty ``-wal`` found under the lock before
connecting) the caller validates on a read-only connection after WAL recovery,
which also catches a newer version committed only in the ``-wal``, and the gate
lets one shape through to that check: a one-page database without identity or
schema next to a non-empty ``-wal``. ``validate_store`` requires, on a
connection, the identity again, the exact ``sqlite_schema`` objects of the
store's version (no extra table, index, view or trigger; the SQL text of each
object as replaying the migrations in memory produces it), a ``durable_schema``
version equal to ``user_version``, and a metadata singleton with ``2 <= next_id
<= 2**53`` and ``1 <= next_seq <= 2**53``. ``quick_check`` runs only after an
unclean shutdown (Root decision D6). Records are decoded and checked when they
are read (DE03c), not here. ``damage_as_corruption`` raises SQLite's corruption
errors (``SQLITE_CORRUPT`` with its extended codes, ``SQLITE_NOTADB``) as
``StorageCorruption`` wherever a store is opened, validated, migrated or closed;
other errors pass unchanged, and DE03c wraps its own statements the same way.
Stored values are read through ``typed_columns`` and ``typed_values``: each
column's storage class is read with it and must be exactly the class its
declared type allows (``integer``, ``text`` or ``blob``, and ``null`` only
where the column is nullable) before anything is decoded, and no value goes
through ``sqlite3``'s own text decoding, whose error is not SQLite's: text is
read as bytes and decoded strictly. STRICT tables keep the classes, but SQL
alone can store another one by rewriting a table's schema and restoring it, and
SQLite's own ``sqlite_schema`` has no STRICT at all; any other class, or text
that is not UTF-8, in the schema, the version or the counters is
``StorageCorruption``.

Recovery rule for an interrupted create. ``create`` makes an empty file, SQLite
writes a one-page WAL header into it, and one transaction then commits the
identity, the schema and the metadata. A crash before that commit leaves an
empty or zero-filled file, or a one-page database without application ID,
user version or schema. ``open`` refuses such a file (``InterruptedCreateError``)
and ``create`` refuses any existing file; nothing is adopted silently, because
nothing proves that this process's create made it and an empty foreign
database looks the same. The documented procedure: while no process owns the
store, remove the database and any ``-wal``, ``-shm`` and ``-journal`` next to
it (the lock file may stay), then create again; no committed data can be lost.
A crash after the commit leaves a complete store whose identity may still sit
only in the ``-wal``; ``open`` recovers and validates it and opens it normally.
Pi opens or creates and would adopt an empty or even a foreign file.

Sources: Pi durable 1.3.0 at 2ec55d808d455b1a64a2bf7888198207a096120a,
``packages/durable/src/storage/sqlite/migrations.ts`` (blob
a5ee244ae9e206d8b9a2e911dee5ca0749036d9c, sha256
dd0f6445e308133d86e7f9fac5945b0907b78f6a9012800ec53787f526d0dea9),
``packages/durable/src/storage/sqlite/schema.ts`` (blob
b807ba52910cb370426dc21d7dc40c9b2495e85e),
``packages/chord/src/sqlite/migrations.ts`` (blob
d4dc7efe88c2142af3ff281bebf56df72c87e1fb, sha256
bd21356d008b67c1c9c2da00bb49fcf439822d0fe8c2afaa17fac075474bbc40) and the
metadata check of ``packages/durable/src/storage/sqlite/storage.ts`` (blob
7dc5903828740fe278fdc692ef9f40dbf68982c7). MIT Copyright (c) 2025 Mario
Zechner; notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
"""

from __future__ import annotations

import contextlib
import functools
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

# "PIPY" in ASCII; positive as a signed 32-bit integer, as PRAGMA reads it.
APPLICATION_ID = 0x50495059
MAX_PAGE_SIZE = 65536
# Highest persisted counter: one past the largest safe integer ID or sequence.
_COUNTER_LIMIT = 2**53
# SQLITE_CORRUPT and SQLITE_NOTADB, as primary result codes.
_DAMAGED = frozenset({11, 26})

TABLES: Mapping[str, str] = MappingProxyType(
    {
        name: name
        for name in (
            "durable_schema",
            "durable_metadata",
            "record_ids",
            "conversations",
            "entries",
            "tasks",
            "submissions",
            "submission_requests",
            "documents",
            "document_revisions",
        )
    }
)
INDEXES: Mapping[str, str] = MappingProxyType(
    {
        name: name
        for name in (
            "conversations_by_owner_conversation",
            "conversations_by_owner_task",
            "entries_by_conversation",
            "entry_heads_by_conversation",
            "tasks_by_status",
            "tasks_by_conversation",
            "tasks_by_kind",
            "tasks_by_abort_requested",
            "tasks_by_background",
            "submissions_by_request",
            "submissions_by_conversation",
            "submissions_by_status",
            "documents_by_address",
            "documents_by_scope",
            "documents_by_scope_kind",
            "document_revisions_by_kind",
        )
    }
)


class StoreIdentityError(RuntimeError):
    """The file is not a durable store this pipy can open (bytes unchanged)."""


class ForeignStoreError(StoreIdentityError):
    """Another application's, or no application's, SQLite database."""


class NewerStoreError(StoreIdentityError):
    """A durable store with a schema version newer than this pipy supports."""


class InterruptedCreateError(StoreIdentityError):
    """A file a crashed ``create`` left before its schema was committed."""


class StorageCorruption(RuntimeError):
    """The store contradicts its own schema or format; it is never repaired."""


class SqliteRollbackError(RuntimeError):
    """A transaction failed and so did its rollback; ``error`` is the first."""

    def __init__(self, error: BaseException, rollback_error: BaseException) -> None:
        super().__init__("SQLite transaction failed and rollback failed")
        self.error = error
        self.rollback_error = rollback_error


@dataclass(frozen=True, slots=True)
class Migration:
    """One schema version: SQL statements run in order, one per entry."""

    version: int
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("A migration version must be an exact int of at least 1")
        if type(self.statements) is not tuple or any(
            type(statement) is not str for statement in self.statements
        ):
            raise TypeError("Migration statements must be a tuple of exact str")


def _schema_table() -> str:
    return f"""CREATE TABLE IF NOT EXISTS {TABLES["durable_schema"]} (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL CHECK (version >= 0)
    ) STRICT"""


def _version_1(t: Mapping[str, str], i: Mapping[str, str]) -> tuple[str, ...]:
    """Pi's schema version 2 with the Python deltas (module docstring)."""

    return (
        f"""CREATE TABLE {t["durable_metadata"]} (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            next_id INTEGER NOT NULL,
            next_seq INTEGER NOT NULL
        ) STRICT""",
        f"""INSERT INTO {t["durable_metadata"]} (singleton, next_id, next_seq)
            VALUES (1, 2, 1)""",
        f"""CREATE TABLE {t["record_ids"]} (
            id INTEGER PRIMARY KEY,
            record_type TEXT NOT NULL CHECK (record_type IN
                ('conversation', 'entry', 'task', 'submission', 'document'))
        ) STRICT""",
        f"""CREATE TABLE {t["conversations"]} (
            id INTEGER PRIMARY KEY,
            owner_conversation_id INTEGER,
            owner_task_id INTEGER,
            record BLOB NOT NULL
        ) STRICT""",
        f"""CREATE INDEX {i["conversations_by_owner_conversation"]}
            ON {t["conversations"]} (owner_conversation_id, id)""",
        f"""CREATE INDEX {i["conversations_by_owner_task"]}
            ON {t["conversations"]} (owner_task_id, id)""",
        f"""CREATE TABLE {t["entries"]} (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            head INTEGER,
            commit_seq INTEGER NOT NULL,
            record BLOB NOT NULL
        ) STRICT""",
        f"""CREATE INDEX {i["entries_by_conversation"]}
            ON {t["entries"]} (conversation_id, id DESC)""",
        f"""CREATE INDEX {i["entry_heads_by_conversation"]}
            ON {t["entries"]} (conversation_id, id DESC) WHERE head IS NOT NULL""",
        f"""CREATE TABLE {t["tasks"]} (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting',
                'sleeping', 'completing', 'terminal')),
            abort_requested INTEGER NOT NULL CHECK (abort_requested IN (0, 1)),
            background INTEGER NOT NULL CHECK (background IN (0, 1)),
            record BLOB NOT NULL
        ) STRICT""",
        f"CREATE INDEX {i['tasks_by_status']} ON {t['tasks']} (status, id)",
        f"""CREATE INDEX {i["tasks_by_conversation"]}
            ON {t["tasks"]} (conversation_id, id)""",
        f"CREATE INDEX {i['tasks_by_kind']} ON {t['tasks']} (kind, id)",
        f"""CREATE INDEX {i["tasks_by_abort_requested"]}
            ON {t["tasks"]} (abort_requested, id)""",
        f"""CREATE INDEX {i["tasks_by_background"]}
            ON {t["tasks"]} (background, id)""",
        f"""CREATE TABLE {t["submissions"]} (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            request_id TEXT,
            status TEXT NOT NULL CHECK (status IN
                ('queued', 'placed', 'done', 'unanswered')),
            record BLOB NOT NULL
        ) STRICT""",
        f"""CREATE INDEX {i["submissions_by_request"]}
            ON {t["submissions"]} (conversation_id, request_id)""",
        f"""CREATE INDEX {i["submissions_by_conversation"]}
            ON {t["submissions"]} (conversation_id, id)""",
        f"""CREATE INDEX {i["submissions_by_status"]}
            ON {t["submissions"]} (status, id)""",
        f"""CREATE TABLE {t["submission_requests"]} (
            conversation_id INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            submission_id INTEGER NOT NULL,
            PRIMARY KEY (conversation_id, request_id)
        ) STRICT""",
        f"""CREATE TABLE {t["documents"]} (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            family INTEGER NOT NULL CHECK (family IN (0, 1)),
            key_value TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN
                ('session', 'conversation', 'task')),
            owner_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            retired_at INTEGER,
            record BLOB NOT NULL
        ) STRICT""",
        f"""CREATE INDEX {i["documents_by_address"]} ON {t["documents"]}
            (kind, scope_kind, owner_id, family, key_value, created_at DESC,
            retired_at)""",
        f"""CREATE INDEX {i["documents_by_scope"]}
            ON {t["documents"]} (scope_kind, owner_id, id)""",
        f"""CREATE INDEX {i["documents_by_scope_kind"]}
            ON {t["documents"]} (scope_kind, owner_id, kind, id)""",
        f"""CREATE TABLE {t["document_revisions"]} (
            document_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('base', 'delta')),
            version BLOB NOT NULL,
            content BLOB NOT NULL,
            PRIMARY KEY (document_id, seq)
        ) STRICT""",
        f"""CREATE INDEX {i["document_revisions_by_kind"]}
            ON {t["document_revisions"]} (document_id, kind, seq DESC)""",
    )


# Append-only. Tests and children replace it to exercise later versions.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, _version_1(TABLES, INDEXES)),
    # Semantic format change: current documents may now require delta replay.
    # V1 already admits the rows; the version prevents older readers opening them.
    Migration(2, ()),
)


def _active(migrations: object = None) -> tuple[Migration, ...]:
    """Admit a migration list (``MIGRATIONS`` when ``None``) before any SQL."""

    if migrations is None:
        migrations = MIGRATIONS
    if type(migrations) is not tuple or any(
        type(migration) is not Migration for migration in migrations
    ):
        raise TypeError("Migrations must be a tuple of Migration")
    for index, migration in enumerate(migrations):
        if migration.version != index + 1:
            raise ValueError(
                "SQLite migrations must have contiguous versions starting at 1"
            )
    return migrations


def _target(migrations: tuple[Migration, ...]) -> int:
    return migrations[-1].version if migrations else 0


def _newer(version: int, supported: int) -> str:
    # Pi chord's message, word for word.
    return (
        f"SQLite schema version {version} is newer than supported version {supported}"
    )


def _interrupted(label: str) -> str:
    return (
        f"The {label} is an interrupted create that never committed a schema;"
        " while no process owns the store, remove it and any -wal, -shm and"
        " -journal next to it, then create the store again"
    )


def _foreign(label: str, application: int, version: int) -> str:
    return (
        f"The {label} is not a pipy durable store (application ID"
        f" {application & 0xFFFFFFFF:#010x}, user version {version})"
    )


def _one_empty_page(header: bytes, size: int) -> bool:
    """Exactly one page and no schema: what the WAL switch of a create writes."""

    raw = int.from_bytes(header[16:18], "big")
    page = MAX_PAGE_SIZE if raw == 1 else raw
    return size == page and int.from_bytes(header[40:44], "big") == 0


def check_unwritten(data: bytes, size: int, label: str) -> None:
    """Refuse a file a create left before SQLite wrote its first page.

    ``data`` holds the first ``min(size, MAX_PAGE_SIZE)`` bytes: an empty file,
    or one page at most of zeros (a torn first write), is no database at all.
    """

    if size <= MAX_PAGE_SIZE and not data.strip(b"\x00"):
        raise InterruptedCreateError(_interrupted(label))


def check_identity(header: bytes, size: int, label: str, wal_size: int) -> None:
    """The pre-connect identity gate on a valid WAL header (bytes 60-63, 68-71).

    A one-page database without identity or schema and with a non-empty
    ``-wal`` passes: the create's first migration may have committed into the
    ``-wal`` only, so the caller decides after WAL recovery, on a read-only
    connection. Every other refusal happens here, before SQLite sees the file.
    """

    application = int.from_bytes(header[68:72], "big", signed=True)
    version = int.from_bytes(header[60:64], "big", signed=True)
    supported = _target(_active())
    if application == APPLICATION_ID:
        if version > supported:
            raise NewerStoreError(_newer(version, supported))
        if version < 1:
            raise StorageCorruption(
                f"The {label} has the durable store's application ID but no"
                " schema version"
            )
        return
    if application == 0 and version == 0 and _one_empty_page(header, size):
        if wal_size > 0:
            return
        raise InterruptedCreateError(_interrupted(label))
    raise ForeignStoreError(_foreign(label, application, version))


Objects = frozenset[tuple[str, str, str, str | None]]


# The storage classes a column may hold, as ``typeof`` names them.
INTEGER = frozenset({"integer"})
NULLABLE_INTEGER = frozenset({"integer", "null"})
TEXT = frozenset({"text"})
NULLABLE_TEXT = frozenset({"text", "null"})
BLOB = frozenset({"blob"})


def typed_columns(*names: str) -> str:
    """Result columns for ``names``: each column's storage class, then its value.

    ``sqlite3`` never decodes a value (its error is not SQLite's, so
    ``damage_as_corruption`` would pass it on, and it quotes the stored text):
    text is read as bytes, for ``typed_values`` to decode once the class holds.
    """

    return ", ".join(
        f"typeof({name}), CASE typeof({name}) WHEN 'text'"
        f" THEN CAST({name} AS BLOB) ELSE {name} END"
        for name in names
    )


def typed_values(
    row: Sequence[object],
    classes: Sequence[frozenset[str]],
    refuse: Callable[[int, str], StorageCorruption],
) -> tuple[object, ...]:
    """The values of a ``typed_columns`` row, each of exactly its allowed classes.

    Every storage class is checked before anything is decoded; then text is
    decoded strictly. ``refuse(column, problem)`` makes the error, whose problem
    names storage classes only, never stored data.
    """

    stored, values = row[0::2], row[1::2]
    for index, (found, allowed) in enumerate(zip(stored, classes, strict=True)):
        if found not in allowed:
            permitted = " or ".join(sorted(allowed))
            raise refuse(index, f"holds {found} where the schema allows {permitted}")
    decoded: list[object] = []
    for index, (found, value) in enumerate(zip(stored, values, strict=True)):
        if found == "text":
            try:
                value = cast(bytes, value).decode("utf-8")
            except UnicodeDecodeError as error:
                raise refuse(index, "is not UTF-8") from error
        decoded.append(value)
    return tuple(decoded)


# SQLite's own schema table: text, and no SQL for automatic indexes.
SCHEMA_COLUMNS = (
    ("type", TEXT),
    ("name", TEXT),
    ("tbl_name", TEXT),
    ("sql", NULLABLE_TEXT),
)
_SCHEMA_ROWS = (
    f"SELECT {typed_columns(*(name for name, _ in SCHEMA_COLUMNS))} FROM sqlite_schema"
)


def _schema_refusal(column: int, problem: str) -> StorageCorruption:
    return StorageCorruption(
        f"The durable store's schema column {SCHEMA_COLUMNS[column][0]} {problem}"
    )


def _objects(connection: sqlite3.Connection) -> Objects:
    rows = connection.execute(_SCHEMA_ROWS).fetchall()
    classes = [allowed for _, allowed in SCHEMA_COLUMNS]
    return frozenset(
        cast(
            tuple[str, str, str, str | None],
            typed_values(row, classes, _schema_refusal),
        )
        for row in rows
    )


@functools.cache
def _expected(migrations: tuple[Migration, ...]) -> Objects:
    """The objects of a store at the last of ``migrations``, replayed in memory.

    Replaying through the same SQLite reproduces its normalized SQL text,
    including the quoted names that ``ALTER TABLE ... RENAME`` writes.
    """

    memory = sqlite3.connect(":memory:", isolation_level=None)
    try:
        memory.execute(_schema_table())
        for migration in migrations:
            for statement in migration.statements:
                memory.execute(statement)
        return _objects(memory)
    finally:
        memory.close()


def _damaged(error: sqlite3.DatabaseError) -> bool:
    """SQLite's own corruption codes; errors the module raises carry no code."""

    code = getattr(error, "sqlite_errorcode", None)
    return type(code) is int and code & 0xFF in _DAMAGED


@contextlib.contextmanager
def damage_as_corruption(label: str) -> Iterator[None]:
    """Raise SQLite's corruption errors as ``StorageCorruption``, nothing else.

    ``SQLITE_CORRUPT`` with its extended codes and ``SQLITE_NOTADB`` become
    ``StorageCorruption`` with the SQLite error as the cause. Every other error
    passes unchanged, and so does one without an SQLite code (Python 3.11 and
    later set ``sqlite_errorcode`` on errors that come from SQLite; errors the
    ``sqlite3`` module raises itself have none), so nothing else is
    reclassified as damage.
    """

    try:
        yield
    except sqlite3.DatabaseError as error:
        if not _damaged(error):
            raise
        raise StorageCorruption(f"SQLite reports the {label} as damaged") from error


def _pragma(connection: sqlite3.Connection, name: str) -> int:
    value = connection.execute(f"PRAGMA {name}").fetchone()[0]
    if type(value) is not int:
        raise StorageCorruption(f"SQLite PRAGMA {name} is not an integer")
    return value


def _difference(found: Objects, expected: Objects, version: int) -> str:
    extra = sorted(f"{kind} {name}" for kind, name, _, _ in found - expected)
    missing = sorted(f"{kind} {name}" for kind, name, _, _ in expected - found)
    return (
        f"The durable store's schema does not match version {version}:"
        f" unexpected {extra[:5]}, missing or changed {missing[:5]}"
    )


# The columns read from the two singletons, with their storage classes.
VERSION_COLUMNS = (("version", INTEGER),)
METADATA_COLUMNS = (("next_id", INTEGER), ("next_seq", INTEGER))
_VERSION = (
    f"SELECT {typed_columns(*(name for name, _ in VERSION_COLUMNS))}"
    f" FROM {TABLES['durable_schema']} WHERE singleton = 1"
)
_METADATA = (
    f"SELECT {typed_columns(*(name for name, _ in METADATA_COLUMNS))}"
    f" FROM {TABLES['durable_metadata']} WHERE singleton = 1"
)


def _version(row: tuple[object, ...] | None) -> int:
    if row is None:
        raise StorageCorruption("SQLite schema metadata is missing")
    (version,) = typed_values(
        row,
        [allowed for _, allowed in VERSION_COLUMNS],
        lambda _, problem: StorageCorruption(
            f"SQLite schema metadata column version {problem}"
        ),
    )
    return cast(int, version)


def _stored_version(connection: sqlite3.Connection) -> int:
    return _version(connection.execute(_VERSION).fetchone())


def read_metadata(connection: sqlite3.Connection) -> tuple[int, int]:
    """The metadata singleton's ``(next_id, next_seq)``, refused unless in range."""

    row = connection.execute(_METADATA).fetchone()
    if row is None:
        # Pi durable storage.ts open(), word for word.
        raise StorageCorruption("Durable SQLite metadata is missing")
    next_id, next_seq = cast(
        tuple[int, int],
        typed_values(
            row,
            [allowed for _, allowed in METADATA_COLUMNS],
            lambda column, problem: StorageCorruption(
                f"Durable SQLite metadata column {METADATA_COLUMNS[column][0]}"
                f" {problem}"
            ),
        ),
    )
    if not (2 <= next_id <= _COUNTER_LIMIT and 1 <= next_seq <= _COUNTER_LIMIT):
        raise StorageCorruption("Durable SQLite metadata is out of range")
    return next_id, next_seq


def _validate(
    connection: sqlite3.Connection, migrations: tuple[Migration, ...], label: str
) -> int:
    supported = _target(migrations)
    application = _pragma(connection, "application_id")
    version = _pragma(connection, "user_version")
    objects = _objects(connection)
    if application != APPLICATION_ID:
        if application == 0 and version == 0 and not objects:
            raise InterruptedCreateError(_interrupted(label))
        raise ForeignStoreError(_foreign(label, application, version))
    if version > supported:
        raise NewerStoreError(_newer(version, supported))
    if version < 1:
        raise StorageCorruption(
            f"The {label} has the durable store's application ID but no schema version"
        )
    expected = _expected(migrations[:version])
    if objects != expected:
        raise StorageCorruption(_difference(objects, expected, version))
    stored = _stored_version(connection)
    if stored > supported:
        raise NewerStoreError(_newer(stored, supported))
    if stored != version:
        raise StorageCorruption(
            f"The durable store's schema version {stored} does not match its"
            f" header's user version {version}"
        )
    read_metadata(connection)
    return version


def validate_store(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...] | None = None,
    *,
    label: str = "database",
) -> int:
    """Check a store's identity, exact schema and metadata; return its version.

    Reads only, so it serves read-only connections too. Damage that SQLite
    reports while reading becomes ``StorageCorruption``.
    """

    active = _active(migrations)
    with damage_as_corruption(label):
        return _validate(connection, active, label)


def quick_check(connection: sqlite3.Connection, *, label: str = "database") -> None:
    """``PRAGMA quick_check``; any finding refuses the store (D6)."""

    with damage_as_corruption(label):
        rows = connection.execute("PRAGMA quick_check").fetchall()
    if rows != [("ok",)]:
        raise StorageCorruption(
            f"quick_check found {len(rows)} problem(s) in the {label}, first:"
            f" {str(rows[0][0])[:200]!r}"
        )


def _before_statement(version: int, index: int) -> None:
    """Test seam: runs before each migration statement, inside the transaction."""


def roll_back(connection: sqlite3.Connection, error: BaseException) -> None:
    """Roll back after ``error``, only while a transaction is open.

    SQLite may already have rolled the whole transaction back (``SQLITE_FULL``,
    for example). Pi chord's rule: a failed rollback raises a different error,
    ``SqliteRollbackError`` carrying both.
    """

    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except Exception as failure:
        raise SqliteRollbackError(error, failure) from failure


def _bootstrap(connection: sqlite3.Connection, target: int) -> int:
    schema = TABLES["durable_schema"]
    connection.execute(_schema_table())
    connection.execute(
        f"INSERT OR IGNORE INTO {schema} (singleton, version) VALUES (1, 0)"
    )
    version = _version(connection.execute(_VERSION).fetchone())
    if version > target:
        raise NewerStoreError(_newer(version, target))
    return version


def _apply(connection: sqlite3.Connection, migration: Migration) -> None:
    if migration.version == 1:
        # The store's identity commits with its first schema, never apart.
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    for index, statement in enumerate(migration.statements):
        _before_statement(migration.version, index)
        connection.execute(statement)
    connection.execute(
        f"UPDATE {TABLES['durable_schema']} SET version = ? WHERE singleton = 1",
        (migration.version,),
    )
    connection.execute(f"PRAGMA user_version = {migration.version}")


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...] | None = None,
    *,
    label: str = "database",
) -> int:
    """Apply every pending migration in one transaction; return the version.

    The list is checked before any SQL. Nothing pending commits an empty
    transaction, which writes nothing. Damage SQLite reports is raised as
    ``StorageCorruption`` after the rollback.
    """

    active = _active(migrations)
    target = _target(active)
    with damage_as_corruption(label):
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = _bootstrap(connection, target)
            for migration in active[version:]:
                _apply(connection, migration)
            connection.execute("COMMIT")
        except BaseException as error:
            roll_back(connection, error)
            raise
    return target


def prepare(
    connection: sqlite3.Connection, *, created: bool, label: str = "database"
) -> int:
    """Bring a newly connected store to ``MIGRATIONS``; return its version.

    ``create`` migrates the new, empty database. ``open`` validates first, so
    a foreign, newer, interrupted or corrupt store is refused before anything
    is written, then applies pending migrations. Either way the result is
    validated again.
    """

    migrations = _active()
    if not created:
        version = validate_store(connection, migrations, label=label)
        if version == _target(migrations):
            return version
    apply_migrations(connection, migrations, label=label)
    return validate_store(connection, migrations, label=label)
