"""DE03b: schema v1, the migration runner and open-time integrity.

Native tests only: schema versions are pipy's own, so there is no Pi oracle;
Pi's migration tests are ported where they apply. Real child processes with
bounded deadlines and process-group reaping cut creates and migrations with
SIGKILL, and every cut is followed by ``PRAGMA integrity_check``. Files are read
or tampered with only while no store is open in this process (closing any
descriptor of a store file drops this process's SQLite locks on it).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlite_child_support import Child, put, stored

from pipy_harness.native.durable import sqlite_connection, sqlite_schema
from pipy_harness.native.durable.ownership import StorageExistsError
from pipy_harness.native.durable.sqlite_connection import open_owned_database
from pipy_harness.native.durable.sqlite_schema import (
    APPLICATION_ID,
    ForeignStoreError,
    InterruptedCreateError,
    Migration,
    NewerStoreError,
    SqliteRollbackError,
    StorageCorruption,
)

V1 = sqlite_schema.MIGRATIONS
NEWER = "SQLite schema version 2 is newer than supported version 1"


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("child-home")


def sidecar(target: Path, suffix: str) -> Path:
    return Path(str(target) + suffix)


def tree(root: Path) -> dict[str, tuple[int, str]]:
    """Permission bits and content digest of every file below ``root``."""

    found: dict[str, tuple[int, str]] = {}
    for top, _, files in os.walk(root):
        for name in files:
            path = Path(top) / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            found[str(path.relative_to(root))] = (
                stat.S_IMODE(path.stat().st_mode),
                digest,
            )
    return found


def without_lock(found: dict[str, tuple[int, str]]) -> dict[str, tuple[int, str]]:
    """A refusal after the lock was taken may rewrite only its diagnostics."""

    return {name: value for name, value in found.items() if not name.endswith(".lock")}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mark_lock(target: Path) -> None:
    sidecar(target, ".lock").write_bytes(b"untouched by a refusal before the lock\n")


def lock_is_free(target: Path) -> bool:
    fd = os.open(sidecar(target, ".lock"), os.O_RDWR | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def header_identity(target: Path) -> tuple[int, int]:
    """Application ID and user version as the database file's header has them."""

    header = target.read_bytes()[:100]
    return int.from_bytes(header[68:72], "big"), int.from_bytes(header[60:64], "big")


@contextmanager
def raw(target: Path) -> Iterator[sqlite3.Connection]:
    """A plain SQLite connection to a closed store, to inspect or tamper."""

    connection = sqlite3.connect(target, isolation_level=None)
    try:
        yield connection
    finally:
        connection.close()


def make_store(target: Path, *rows: str) -> None:
    db = open_owned_database(str(target), mode="create")
    try:
        for row in rows:
            put(db.connection(), row)
    finally:
        db.close()


def integrity(target: Path) -> list[tuple[Any, ...]]:
    db = open_owned_database(str(target), mode="open")
    try:
        return db.connection().execute("PRAGMA integrity_check").fetchall()
    finally:
        db.close()


def connects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the file URIs pipy connects to (in-memory replays aside)."""

    calls: list[str] = []
    real = sqlite3.connect

    def spy(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if str(database).startswith("file:"):
            calls.append(str(database).rsplit("?", 1)[1])
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    return calls


# A synthetic version 2 in Pi's version 1 to 2 pattern: SQLite cannot change a
# CHECK constraint in place, so the tasks table is rebuilt with a new status.
REBUILD = (
    """CREATE TABLE tasks_v2 (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting',
            'sleeping', 'paused', 'completing', 'terminal')),
        abort_requested INTEGER NOT NULL CHECK (abort_requested IN (0, 1)),
        background INTEGER NOT NULL CHECK (background IN (0, 1)),
        record BLOB NOT NULL
    ) STRICT""",
    """INSERT INTO tasks_v2
        (id, conversation_id, kind, status, abort_requested, background, record)
        SELECT id, conversation_id, kind, status, abort_requested, background, record
        FROM tasks""",
    "DROP TABLE tasks",
    "ALTER TABLE tasks_v2 RENAME TO tasks",
    "CREATE INDEX tasks_by_status ON tasks (status, id)",
    "CREATE INDEX tasks_by_conversation ON tasks (conversation_id, id)",
    "CREATE INDEX tasks_by_kind ON tasks (kind, id)",
    "CREATE INDEX tasks_by_abort_requested ON tasks (abort_requested, id)",
    "CREATE INDEX tasks_by_background ON tasks (background, id)",
)
TASK_COLUMNS = "id, conversation_id, kind, status, abort_requested, background, record"


def with_version_2(
    monkeypatch: pytest.MonkeyPatch, statements: tuple[str, ...] = REBUILD
) -> None:
    monkeypatch.setattr(sqlite_schema, "MIGRATIONS", (*V1, Migration(2, statements)))


def add_tasks(target: Path, count: int, size: int = 64) -> list[tuple[Any, ...]]:
    """Tasks rows written through the owner; returns them as stored."""

    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        connection.execute("BEGIN IMMEDIATE")
        for index in range(count):
            connection.execute(
                f"INSERT INTO tasks ({TASK_COLUMNS}) VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    index + 2,
                    f'"kind-{index % 3}"',
                    ("pending", "waiting", "sleeping", "terminal")[index % 4],
                    index % 2,
                    (index // 2) % 2,
                    os.urandom(size),
                ),
            )
        connection.execute("COMMIT")
        return connection.execute(
            f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        db.close()


# Schema v1 and its mapping to Pi -------------------------------------------------
#
# Pi durable 1.3.0 packages/durable/src/storage/sqlite/migrations.ts at
# 2ec55d808d455b1a64a2bf7888198207a096120a (blob a5ee244ae9e206d8b9a2e911dee5ca0749036d9c),
# rendered with the default identifiers, whitespace collapsed. MIT Copyright (c)
# 2025 Mario Zechner; notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
PI_VERSION_1 = (
    "CREATE TABLE durable_metadata ( singleton INTEGER PRIMARY KEY CHECK (singleton = 1), next_id TEXT NOT NULL, next_seq INTEGER NOT NULL ) STRICT",
    "INSERT INTO durable_metadata (singleton, next_id, next_seq) VALUES (1, '2', 1)",
    "CREATE TABLE record_ids ( id INTEGER PRIMARY KEY, record_type TEXT NOT NULL CHECK (record_type IN ('conversation', 'entry', 'task', 'submission', 'document')) ) STRICT",
    "CREATE TABLE conversations ( id INTEGER PRIMARY KEY, owner_conversation_id INTEGER, owner_task_id INTEGER, record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT",
    "CREATE INDEX conversations_by_owner_conversation ON conversations (owner_conversation_id, id)",
    "CREATE INDEX conversations_by_owner_task ON conversations (owner_task_id, id)",
    "CREATE TABLE entries ( id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, head INTEGER, commit_seq INTEGER NOT NULL, record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT",
    "CREATE INDEX entries_by_conversation ON entries (conversation_id, id DESC)",
    "CREATE INDEX entry_heads_by_conversation ON entries (conversation_id, id DESC) WHERE head IS NOT NULL",
    "CREATE TABLE tasks ( id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting', 'completing', 'terminal')), abort_requested INTEGER NOT NULL CHECK (abort_requested IN (0, 1)), background INTEGER NOT NULL CHECK (background IN (0, 1)), record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT",
    "CREATE INDEX tasks_by_status ON tasks (status, id)",
    "CREATE INDEX tasks_by_conversation ON tasks (conversation_id, id)",
    "CREATE INDEX tasks_by_kind ON tasks (kind, id)",
    "CREATE INDEX tasks_by_abort_requested ON tasks (abort_requested, id)",
    "CREATE INDEX tasks_by_background ON tasks (background, id)",
    "CREATE TABLE submissions ( id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, request_id TEXT, status TEXT NOT NULL CHECK (status IN ('queued', 'placed', 'done', 'unanswered')), record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT",
    "CREATE INDEX submissions_by_request ON submissions (conversation_id, request_id)",
    "CREATE INDEX submissions_by_conversation ON submissions (conversation_id, id)",
    "CREATE INDEX submissions_by_status ON submissions (status, id)",
    "CREATE TABLE documents ( id INTEGER PRIMARY KEY, kind TEXT NOT NULL, family INTEGER NOT NULL CHECK (family IN (0, 1)), key_value TEXT NOT NULL, scope_kind TEXT NOT NULL CHECK (scope_kind IN ('session', 'conversation', 'task')), owner_id INTEGER NOT NULL, created_at INTEGER NOT NULL, retired_at INTEGER, record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT",
    "CREATE INDEX documents_by_address ON documents (kind, scope_kind, owner_id, family, key_value, created_at DESC, retired_at)",
    "CREATE INDEX documents_by_scope ON documents (scope_kind, owner_id, id)",
    "CREATE INDEX documents_by_scope_kind ON documents (scope_kind, owner_id, kind, id)",
    "CREATE TABLE document_revisions ( document_id INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('base', 'delta')), version INTEGER NOT NULL, content TEXT NOT NULL CHECK (json_valid(content)), PRIMARY KEY (document_id, seq) ) STRICT",
    "CREATE INDEX document_revisions_by_kind ON document_revisions (document_id, kind, seq DESC)",
)
PI_VERSION_2_TASKS = "CREATE TABLE tasks_v2 ( id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting', 'sleeping', 'completing', 'terminal')), abort_requested INTEGER NOT NULL CHECK (abort_requested IN (0, 1)), background INTEGER NOT NULL CHECK (background IN (0, 1)), record TEXT NOT NULL CHECK (json_valid(record)) ) STRICT"
# The Python deltas of schema v1 (sqlite_schema docstring; D1, D2, D11).
PYTHON_DELTAS = (
    ("record TEXT NOT NULL CHECK (json_valid(record))", "record BLOB NOT NULL"),
    ("content TEXT NOT NULL CHECK (json_valid(content))", "content BLOB NOT NULL"),
    ("version INTEGER NOT NULL, content", "version BLOB NOT NULL, content"),
    ("next_id TEXT NOT NULL", "next_id INTEGER NOT NULL"),
    ("VALUES (1, '2', 1)", "VALUES (1, 2, 1)"),
)
SUBMISSION_REQUESTS = "CREATE TABLE submission_requests ( conversation_id INTEGER NOT NULL, request_id TEXT NOT NULL, submission_id INTEGER NOT NULL, PRIMARY KEY (conversation_id, request_id) ) STRICT"


def collapse(statement: str) -> str:
    return " ".join(statement.split())


def test_schema_v1_is_pi_version_2_with_exactly_the_python_deltas() -> None:
    expected = []
    for statement in PI_VERSION_1:
        if statement.startswith("CREATE TABLE tasks ("):
            # Pi's version 2 shape, created directly instead of rebuilt.
            statement = PI_VERSION_2_TASKS.replace("tasks_v2", "tasks")
        for old, new in PYTHON_DELTAS:
            statement = statement.replace(old, new)
        expected.append(statement)
        if statement.startswith("CREATE INDEX submissions_by_status"):
            expected.append(SUBMISSION_REQUESTS)
    assert [collapse(s) for s in V1[0].statements] == expected
    assert [m.version for m in V1] == [1]
    joined = " ".join(expected)
    assert "json_valid" not in joined and " TEXT NOT NULL CHECK (json" not in joined


def test_a_created_store_carries_its_identity_schema_and_metadata(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    assert header_identity(target) == (APPLICATION_ID, 1) == (0x50495059, 1)
    with raw(target) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
        ).fetchall()
        indexes = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'index' ORDER BY name"
        ).fetchall()
        assert [name for (name,) in tables] == sorted(sqlite_schema.TABLES)
        assert [name for (name,) in indexes] == sorted(
            [
                *sqlite_schema.INDEXES,
                "sqlite_autoindex_document_revisions_1",
                "sqlite_autoindex_submission_requests_1",
            ]
        )
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 1)]
        assert connection.execute("SELECT * FROM durable_metadata").fetchall() == [
            (1, 2, 1)
        ]
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type IN ('view', 'trigger')"
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_payload_columns_keep_any_json_and_versions_beyond_sqlite_integers(
    tmp_path: Path,
) -> None:
    """The BLOB columns behind the lossless D1 and version deltas."""

    target = tmp_path / "store.sqlite"
    make_store(target)
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        versions = [b"0", b"-1", b"1.5", b"-0.0", b"18446744073709551616"]
        for seq, version in enumerate(versions, 1):
            connection.execute(
                "INSERT INTO document_revisions VALUES (7, ?, 'base', ?, ?)",
                (seq, version, '{"lone":"\ud800"}'.encode("utf-8", "surrogatepass")),
            )
        assert [
            v for (v,) in connection.execute("SELECT version FROM document_revisions")
        ] == versions
        with pytest.raises(sqlite3.IntegrityError, match="cannot store TEXT value"):
            connection.execute("INSERT INTO conversations (record) VALUES ('{}')")
    finally:
        db.close()


# The migration runner (Pi chord migrations.test.ts) -----------------------------


def memory() -> sqlite3.Connection:
    return sqlite3.connect(":memory:", isolation_level=None)


def names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("SELECT name FROM sqlite_schema ORDER BY name")
    return [name for (name,) in rows.fetchall()]


class _StrSubclass(str):
    pass


@pytest.mark.parametrize(
    "versions", [[2], [1, 3], [1, 1], [2, 1]], ids=["2", "1-3", "1-1", "2-1"]
)
def test_the_runner_refuses_non_contiguous_versions_before_any_sql(
    versions: list[int],
) -> None:
    connection = memory()
    try:
        migrations = tuple(Migration(version, ()) for version in versions)
        with pytest.raises(ValueError, match="contiguous versions starting at 1"):
            sqlite_schema.apply_migrations(connection, migrations)
        assert names(connection) == [] and not connection.in_transaction
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: Migration(0, ()), ValueError),
        (lambda: Migration(True, ()), ValueError),
        (lambda: Migration(1, ["CREATE TABLE x (y)"]), TypeError),  # type: ignore[arg-type]
        (lambda: Migration(1, (_StrSubclass("CREATE TABLE x (y)"),)), TypeError),
    ],
    ids=["version-0", "version-bool", "statements-list", "statement-str-subclass"],
)
def test_a_migration_admits_exact_types_only(
    build: Any, error: type[Exception]
) -> None:
    with pytest.raises(error):
        build()


def test_the_runner_admits_only_a_tuple_of_migrations_before_any_sql() -> None:
    connection = memory()
    try:
        for bad in (list(V1), (V1[0], "CREATE TABLE x (y)"), V1[0]):
            with pytest.raises(TypeError, match="tuple of Migration"):
                sqlite_schema.apply_migrations(connection, bad)  # type: ignore[arg-type]
        assert names(connection) == [] and not connection.in_transaction
    finally:
        connection.close()


def test_the_runner_applies_repeatably_and_skips_applied_versions(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        wal = sidecar(target, "-wal").stat().st_size
        for _ in range(2):
            assert sqlite_schema.apply_migrations(connection) == 1
        # Nothing pending commits an empty transaction: no frame was written.
        assert sidecar(target, "-wal").stat().st_size == wal
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 1)]
        assert connection.execute("SELECT * FROM durable_metadata").fetchall() == [
            (1, 2, 1)
        ]
        assert stored(connection) == ["kept"]
    finally:
        db.close()


def test_the_runner_rolls_bootstrap_and_every_pending_migration_back_together() -> None:
    connection = memory()
    try:
        first = Migration(
            1,
            (
                "CREATE TABLE migration_first (value TEXT) STRICT",
                "INSERT INTO migration_first (value) VALUES ('retained')",
            ),
        )
        failing = Migration(
            2, ("CREATE TABLE migration_second (value TEXT) STRICT", "THIS IS NOT SQL")
        )
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            sqlite_schema.apply_migrations(connection, (first, failing))
        assert not connection.in_transaction
        assert names(connection) == []
        assert connection.execute("PRAGMA application_id").fetchone() == (0,)
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        fixed = Migration(2, ("CREATE TABLE migration_second (value TEXT) STRICT",))
        assert sqlite_schema.apply_migrations(connection, (first, fixed)) == 2
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 2)]
        assert connection.execute("SELECT value FROM migration_first").fetchall() == [
            ("retained",)
        ]
        assert connection.execute("PRAGMA application_id").fetchone() == (
            APPLICATION_ID,
        )
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    finally:
        connection.close()


def test_the_runner_names_both_versions_when_the_store_is_newer() -> None:
    connection = memory()
    try:
        first = Migration(1, ("CREATE TABLE notes (body TEXT)",))
        second = Migration(2, ("ALTER TABLE notes ADD COLUMN title TEXT",))
        assert sqlite_schema.apply_migrations(connection, (first, second)) == 2
        with pytest.raises(NewerStoreError, match=NEWER):
            sqlite_schema.apply_migrations(connection, (first,))
        assert not connection.in_transaction
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 2)]
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    finally:
        connection.close()


class _RollbackFails:
    in_transaction = True

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)
        raise sqlite3.OperationalError("injected rollback failure")


def test_a_failed_rollback_raises_a_distinct_error_with_both_errors() -> None:
    """Pi chord's facade: the caller must not mistake it for a rollback."""

    failing = _RollbackFails()
    error = sqlite3.OperationalError("migration failed")
    with pytest.raises(SqliteRollbackError) as raised:
        sqlite_schema.roll_back(failing, error)  # type: ignore[arg-type]
    assert failing.statements == ["ROLLBACK"]
    assert raised.value.error is error
    assert raised.value.__cause__ is raised.value.rollback_error
    assert str(raised.value.rollback_error) == "injected rollback failure"
    failing.in_transaction = False
    sqlite_schema.roll_back(failing, error)  # type: ignore[arg-type]
    assert failing.statements == ["ROLLBACK"]  # nothing to roll back


# Migrations through open (Pi durable sqlite-migrations.test.ts) -----------------


def test_a_failed_migration_rolls_back_version_probe_table_and_data_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    before = without_lock(tree(tmp_path))
    with_version_2(
        monkeypatch,
        (
            "CREATE TABLE migration_probe (value TEXT) STRICT",
            "INSERT INTO migration_probe (value) VALUES ('probe')",
            "UPDATE durable_metadata SET next_seq = next_seq + 7",
            "DELETE FROM conversations",
            "THIS IS NOT SQL",
        ),
    )
    for _ in range(2):  # the lock is freed and the old version kept, so again
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            open_owned_database(str(target), mode="open")
    assert without_lock(tree(tmp_path)) == before  # no byte of the store changed
    assert lock_is_free(target)
    with_version_2(monkeypatch, ("CREATE TABLE migration_probe (value TEXT) STRICT",))
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        assert db.schema_version == 2
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 2)]
        assert connection.execute("SELECT * FROM durable_metadata").fetchall() == [
            (1, 2, 1)
        ]
        assert connection.execute("SELECT * FROM migration_probe").fetchall() == []
        assert stored(connection) == ["kept"]
    finally:
        db.close()
    assert header_identity(target) == (APPLICATION_ID, 2)
    assert integrity(target) == [("ok",)]


def test_a_failed_first_migration_leaves_only_the_lock_and_create_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "new" / "store.sqlite"
    broken = Migration(1, (*V1[0].statements, "THIS IS NOT SQL"))
    monkeypatch.setattr(sqlite_schema, "MIGRATIONS", (broken,))
    with pytest.raises(sqlite3.OperationalError, match="syntax error"):
        open_owned_database(str(target), mode="create")
    assert sorted(p.name for p in target.parent.iterdir()) == ["store.sqlite.lock"]
    monkeypatch.undo()
    make_store(target, "after")
    assert integrity(target) == [("ok",)]


def test_a_table_rebuild_migration_keeps_rows_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    rows = add_tasks(target, 40)
    with raw(target) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"INSERT INTO tasks ({TASK_COLUMNS}) VALUES (99, 1, 'k', 'paused', 0, 0, x'00')"
        )
    with_version_2(monkeypatch)
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        assert db.schema_version == 2
        assert (
            connection.execute(
                f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id"
            ).fetchall()
            == rows
        )
        connection.execute(
            f"INSERT INTO tasks ({TASK_COLUMNS}) VALUES (99, 1, 'k', 'paused', 0, 0, x'00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO tasks ({TASK_COLUMNS}) VALUES (98, 1, 'k', 'lost', 0, 0, x'00')"
            )
        indexes = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'index' AND tbl_name = 'tasks'"
            " ORDER BY name"
        ).fetchall()
        assert [name for (name,) in indexes] == [
            "tasks_by_abort_requested",
            "tasks_by_background",
            "tasks_by_conversation",
            "tasks_by_kind",
            "tasks_by_status",
        ]
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name = 'tasks_v2'"
        ).fetchone() == (0,)
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM tasks WHERE status = 'paused' ORDER BY id"
        ).fetchall()
        assert any("tasks_by_status" in str(step[-1]) for step in plan), plan
    finally:
        db.close()
    assert integrity(target) == [("ok",)]


def test_opening_a_current_store_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    before = digest(target)
    for _ in range(2):
        db = open_owned_database(str(target), mode="open")
        assert db.schema_version == 1
        db.close()
    assert digest(target) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "store.sqlite",
        "store.sqlite.lock",
    ]


# The identity gate ---------------------------------------------------------------


def _foreign(target: Path, kind: str) -> None:
    """A WAL-mode SQLite database that is not a pipy durable store."""

    with raw(target) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        if kind == "geopackage":
            connection.execute("PRAGMA application_id = 1196444487")  # "GPKG"
        elif kind == "user-version":
            connection.execute("PRAGMA user_version = 5")
        elif kind == "pipy-without-version":
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        if kind != "empty-schema-multi-page":
            connection.execute("CREATE TABLE notes (body TEXT)")
            connection.execute("INSERT INTO notes VALUES ('foreign')")
        else:
            connection.execute("CREATE TABLE notes (body TEXT)")
            connection.execute("DROP TABLE notes")  # schema cookie set, no objects
    target.chmod(0o600)


@pytest.mark.parametrize(
    ("kind", "error", "message"),
    [
        ("plain", ForeignStoreError, "application ID 0x00000000, user version 0"),
        ("geopackage", ForeignStoreError, "application ID 0x47504b47"),
        ("user-version", ForeignStoreError, "user version 5"),
        ("empty-schema-multi-page", ForeignStoreError, "not a pipy durable store"),
        ("pipy-without-version", StorageCorruption, "but no schema version"),
    ],
)
def test_a_foreign_file_is_refused_before_sqlite_sees_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    error: type[Exception],
    message: str,
) -> None:
    target = tmp_path / "foreign.sqlite"
    _foreign(target, kind)
    before = tree(tmp_path)
    calls = connects(monkeypatch)
    for _ in range(2):
        with pytest.raises(error, match=message):
            open_owned_database(str(target), mode="open")
    assert calls == []  # never connected
    assert tree(tmp_path) == before  # same bytes and modes, and no lock file


FOREIGN_CRASH = """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("CREATE TABLE notes (body TEXT)")
connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
connection.execute("INSERT INTO notes VALUES ('after the checkpoint')")
say("written", wal=os.path.getsize(sys.argv[1] + "-wal"))
os._exit(0)  # a crashed foreign writer: no close, no checkpoint
"""


def test_a_crashed_foreign_writers_files_are_refused_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    target = tmp_path / "foreign.sqlite"
    with Child(FOREIGN_CRASH, str(target), home=home) as child:
        assert child.expect("written")["wal"] > 0
        assert child.wait() == 0
    for suffix in ("", "-wal", "-shm"):
        sidecar(target, suffix).chmod(0o600)
    before = tree(tmp_path)
    calls = connects(monkeypatch)
    with pytest.raises(ForeignStoreError):
        open_owned_database(str(target), mode="open")
    assert calls == []
    # Even the -shm is unchanged: SQLite never recovered the foreign -wal.
    assert tree(tmp_path) == before


@pytest.mark.parametrize("tamper", ["header", "user-version", "durable-schema"])
def test_a_newer_store_is_refused_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    if tamper == "header":
        with_version_2(monkeypatch)
        open_owned_database(str(target), mode="open").close()
        monkeypatch.undo()
    else:
        with raw(target) as connection:
            if tamper == "user-version":
                connection.execute("PRAGMA user_version = 2")
            else:  # Pi's tamper: only the schema table claims the newer version
                connection.execute("UPDATE durable_schema SET version = 2")
    mark_lock(target)
    before = tree(tmp_path)
    calls = connects(monkeypatch)
    with pytest.raises(NewerStoreError, match=NEWER):
        open_owned_database(str(target), mode="open")
    if tamper == "durable-schema":
        # Seen after connecting; nothing was written and only the lock's
        # diagnostics were rewritten.
        assert calls == ["mode=rw"]
        assert without_lock(tree(tmp_path)) == without_lock(before)
    else:
        assert calls == []
        assert tree(tmp_path) == before
    if tamper == "header":
        with_version_2(monkeypatch)
        db = open_owned_database(str(target), mode="open")
        try:
            assert db.schema_version == 2 and stored(db.connection()) == ["kept"]
        finally:
            db.close()


# Open-time integrity -------------------------------------------------------------

TAMPERS: dict[str, tuple[tuple[str, ...], str]] = {
    "extra-table": (("CREATE TABLE extra (x)",), "unexpected \\['table extra'\\]"),
    "extra-index": (
        ("CREATE INDEX extra_index ON conversations (record)",),
        "unexpected \\['index extra_index'\\]",
    ),
    "view": (("CREATE VIEW extra_view AS SELECT 1",), "view extra_view"),
    "trigger": (
        (
            "CREATE TRIGGER extra_trigger AFTER INSERT ON conversations"
            " BEGIN SELECT 1; END",
        ),
        "trigger extra_trigger",
    ),
    "dropped-index": (
        ("DROP INDEX tasks_by_kind",),
        "missing or changed \\['index tasks_by_kind'\\]",
    ),
    "changed-index": (
        (
            "DROP INDEX entry_heads_by_conversation",
            "CREATE INDEX entry_heads_by_conversation ON entries (conversation_id, id DESC)",
        ),
        "changed \\['index entry_heads_by_conversation'\\]",
    ),
    "schema-row-missing": (
        ("DELETE FROM durable_schema",),
        "SQLite schema metadata is missing",
    ),
    "version-mismatch": (
        ("UPDATE durable_schema SET version = 0",),
        "does not match its header",
    ),
    "metadata-missing": (
        ("DELETE FROM durable_metadata",),
        "Durable SQLite metadata is missing",
    ),
    "next-id-low": (("UPDATE durable_metadata SET next_id = 1",), "out of range"),
    "next-id-high": (
        (f"UPDATE durable_metadata SET next_id = {2**53 + 1}",),
        "out of range",
    ),
    "next-seq-low": (("UPDATE durable_metadata SET next_seq = 0",), "out of range"),
    "next-seq-high": (
        (f"UPDATE durable_metadata SET next_seq = {2**53 + 1}",),
        "out of range",
    ),
    "schema-text-not-utf8": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET sql = sql || ' /*' || CAST(x'ff' AS TEXT)"
            " || '*/' WHERE name = 'tasks_by_kind'",
        ),
        "schema column sql is not UTF-8",
    ),
    # SQLite's own schema table has no STRICT: any class goes in, and SQLite
    # itself still opens the store.
    "schema-sql-blob": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET sql = CAST(sql AS BLOB)"
            " WHERE name = 'tasks_by_kind'",
        ),
        "schema column sql holds blob where the schema allows null or text",
    ),
    "schema-name-blob": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET name = CAST(name AS BLOB)"
            " WHERE name = 'tasks_by_kind'",
        ),
        "schema column name holds blob where the schema allows text",
    ),
    "schema-type-blob": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET type = CAST(type AS BLOB)"
            " WHERE name = 'tasks_by_kind'",
        ),
        "schema column type holds blob where the schema allows text",
    ),
    "schema-table-blob": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET tbl_name = CAST(tbl_name AS BLOB)"
            " WHERE name = 'tasks_by_kind'",
        ),
        "schema column tbl_name holds blob where the schema allows text",
    ),
    # A number there is SQLite's own refusal: it reports a malformed schema.
    "schema-sql-real": (
        (
            "PRAGMA writable_schema = ON",
            "UPDATE sqlite_schema SET sql = 1.5 WHERE name = 'tasks_by_kind'",
        ),
        "SQLite reports the database .* as damaged",
    ),
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_a_store_that_contradicts_its_schema_is_refused_unchanged(
    tmp_path: Path, tamper: str
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    statements, message = TAMPERS[tamper]
    with raw(target) as connection:
        for statement in statements:
            connection.execute(statement)
    before = tree(tmp_path)
    for _ in range(2):
        with pytest.raises(StorageCorruption, match=message):
            open_owned_database(str(target), mode="open")
    assert without_lock(tree(tmp_path)) == without_lock(before)
    assert lock_is_free(target)


def store_in_strict_column(
    target: Path | str, table: str, column: str, value: str, where: str = "1"
) -> list[str]:
    """Store ``value`` (SQL) in ``column`` of a STRICT table, whatever its class.

    SQL alone can: rewrite the table's schema without STRICT, with the column
    untyped and nullable (BLOB affinity, so SQLite converts nothing), write,
    and restore the schema text, which open-time validation then finds
    unchanged. Returns the storage classes the written rows now hold.
    """

    with raw(Path(target)) as connection:
        (sql,) = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = ?", (table,)
        ).fetchone()
        assert sql.endswith(") STRICT"), sql
        loose, found = re.subn(
            rf"\b{column} (INTEGER|TEXT|BLOB)( NOT NULL)?",
            f"{column} BLOB",
            sql.removesuffix(" STRICT"),
        )
        assert found == 1, (column, sql)
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE name = ?", (loose, table)
        )
    with raw(Path(target)) as connection:  # reads the rewritten schema
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(f"UPDATE {table} SET {column} = {value} WHERE {where}")
        classes = [
            stored
            for (stored,) in connection.execute(
                f"SELECT typeof({column}) FROM {table} WHERE {where}"
            )
        ]
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql = ? WHERE name = ?", (sql, table)
        )
    assert classes, (table, column, where)
    return classes


_METADATA_TEXT = "Durable SQLite metadata column"
_VERSION_TEXT = "SQLite schema metadata column version"


@pytest.mark.parametrize(
    "table,column,value,stored,message",
    [
        pytest.param(
            "durable_schema",
            "version",
            "CAST(x'ff' AS TEXT)",
            "text",
            _VERSION_TEXT,
            id="version-not-utf8",
        ),
        pytest.param(
            "durable_schema", "version", "'1'", "text", _VERSION_TEXT, id="version-text"
        ),
        pytest.param(
            "durable_schema", "version", "1.0", "real", _VERSION_TEXT, id="version-real"
        ),
        pytest.param(
            "durable_schema",
            "version",
            "x'01'",
            "blob",
            _VERSION_TEXT,
            id="version-blob",
        ),
        pytest.param(
            "durable_schema",
            "version",
            "NULL",
            "null",
            _VERSION_TEXT,
            id="version-null",
        ),
        pytest.param(
            "durable_metadata",
            "next_id",
            "CAST(x'ff' AS TEXT)",
            "text",
            None,
            id="next-id-not-utf8",
        ),
        pytest.param(
            "durable_metadata", "next_id", "5.0", "real", None, id="next-id-real"
        ),
        pytest.param(
            "durable_metadata", "next_id", "x'05'", "blob", None, id="next-id-blob"
        ),
        pytest.param(
            "durable_metadata", "next_seq", "'1'", "text", None, id="next-seq-text"
        ),
        pytest.param(
            "durable_metadata", "next_seq", "1.0", "real", None, id="next-seq-real"
        ),
    ],
)
def test_a_counter_of_another_storage_class_is_refused_unchanged(
    tmp_path: Path,
    table: str,
    column: str,
    value: str,
    stored: str,
    message: str | None,
) -> None:
    """Exactly ``integer``: a numerically equal REAL or text is refused too, and
    never through sqlite3's own UTF-8 decoding error, which is not SQLite's."""

    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    assert store_in_strict_column(target, table, column, value) == [stored]
    before = tree(tmp_path)
    prefix = message or f"{_METADATA_TEXT} {column}"
    for _ in range(2):
        with pytest.raises(StorageCorruption) as refused:
            open_owned_database(str(target), mode="open")
        assert str(refused.value) == (
            f"{prefix} holds {stored} where the schema allows integer"
        )
    assert without_lock(tree(tmp_path)) == without_lock(before)
    assert lock_is_free(target)


def _corrupt_root(target: Path, name: str) -> None:
    """An invalid b-tree page type on the root page of table or index ``name``."""

    with raw(target) as connection:
        (root,) = connection.execute(
            "SELECT rootpage FROM sqlite_schema WHERE name = ?", (name,)
        ).fetchone()
        (page,) = connection.execute("PRAGMA page_size").fetchone()
    with open(target, "r+b") as stream:
        stream.seek((root - 1) * page)
        stream.write(b"\x01")


def test_damage_sqlite_reports_while_validating_is_storage_corruption(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    _corrupt_root(target, "durable_metadata")  # only validation reads it at open
    before = without_lock(tree(tmp_path))
    with pytest.raises(StorageCorruption, match="reports the database") as raised:
        open_owned_database(str(target), mode="open")
    assert isinstance(raised.value.__cause__, sqlite3.DatabaseError)
    assert without_lock(tree(tmp_path)) == before
    assert lock_is_free(target)


def test_the_largest_counters_are_valid_metadata(tmp_path: Path) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    with raw(target) as connection:
        connection.execute(
            f"UPDATE durable_metadata SET next_id = {2**53}, next_seq = {2**53}"
        )
    db = open_owned_database(str(target), mode="open")
    db.close()


CRASH_WRITER = """
db = open_owned_database(sys.argv[1], mode="open")
connection = db.connection()
for value in sys.argv[2:]:
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("INSERT INTO record_ids VALUES (?, 'task')", (int(value),))
    connection.execute("COMMIT")
say("written", wal=os.stat(sys.argv[1] + "-wal").st_size)
command()  # killed while it owns the store
"""


@pytest.mark.parametrize("shutdown", ["unclean", "clean"])
def test_quick_check_runs_only_after_an_unclean_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, shutdown: str
) -> None:
    """Root decision D6, both sides of the boundary.

    After an unclean shutdown the read-only validation runs ``quick_check`` and
    refuses the store with the database and ``-wal`` bytes unchanged. After a
    clean shutdown the same damage is not looked for at open; ``integrity_check``
    (which tests run after every cut) finds it.
    """

    target = tmp_path / "store.sqlite"
    make_store(target, *(f"row{index}" for index in range(30)))
    _corrupt_root(target, "conversations_by_owner_task")  # never read at open
    if shutdown == "unclean":
        with Child(CRASH_WRITER, str(target), "2", "3", home=home) as child:
            assert child.expect("written")["wal"] > 0
            child.kill_group()
        before = {s: digest(sidecar(target, s)) for s in ("", "-wal")}
        calls = connects(monkeypatch)
        with pytest.raises(StorageCorruption, match="quick_check found"):
            open_owned_database(str(target), mode="open")
        assert calls == ["mode=ro"]
        assert {s: digest(sidecar(target, s)) for s in ("", "-wal")} == before
        assert lock_is_free(target)
    else:
        db = open_owned_database(str(target), mode="open")
        try:
            assert db.wal_size_at_open == 0
            with pytest.raises(sqlite3.DatabaseError, match="malformed"):
                db.connection().execute("PRAGMA integrity_check").fetchall()
        finally:
            db.close()


SIGKILL_WRITER = """
db = open_owned_database(sys.argv[1], mode="open")
connection = db.connection()
for value in sys.argv[2:]:
    connection.execute("BEGIN IMMEDIATE")
    put(connection, value)
    connection.execute("COMMIT")
say("written", wal=os.stat(sys.argv[1] + "-wal").st_size)
command()  # killed while it owns the store
"""


def test_an_unclean_shutdown_is_validated_read_only_then_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "first")
    with Child(SIGKILL_WRITER, str(target), "second", "third", home=home) as child:
        assert child.expect("written")["wal"] > 0
        child.kill_group()
    calls = connects(monkeypatch)
    db = open_owned_database(str(target), mode="open")
    try:
        assert calls == ["mode=ro", "mode=rw"]
        assert db.wal_size_at_open > 0 and db.schema_version == 1
        assert stored(db.connection()) == ["first", "second", "third"]
        assert db.connection().execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        for suffix in ("", "-wal", "-shm", ".lock"):
            assert stat.S_IMODE(os.lstat(sidecar(target, suffix)).st_mode) == 0o600
    finally:
        db.close()


# SQLite corruption errors anywhere in open and close (round-1 review) -----------
#
# The owner's PRAGMA configuration loads the schema before validate_store runs,
# so a damaged schema page used to surface as a raw sqlite3.DatabaseError. Every
# SQLite corruption error raised while a store is opened, migrated or closed is
# StorageCorruption with the SQLite error as its cause; other errors pass.


def _page_size(target: Path) -> int:
    raw_size = int.from_bytes(target.read_bytes()[16:18], "big")
    return 65536 if raw_size == 1 else raw_size


def _schema_leaf(target: Path) -> int:
    """The right-most leaf of the ``sqlite_schema`` b-tree that page 1 roots."""

    header = target.read_bytes()[100:112]
    assert header[0] == 0x05  # an interior table page: the schema spans pages
    return int.from_bytes(header[8:12], "big")


def _corrupt_page(target: Path, number: int) -> None:
    """An invalid b-tree page type on page ``number`` (byte 100 on page 1)."""

    offset = 100 if number == 1 else (number - 1) * _page_size(target)
    with open(target, "r+b") as stream:
        stream.seek(offset)
        stream.write(b"\x01")


def _assert_damage(error: BaseException) -> None:
    cause = error.__cause__
    assert isinstance(cause, sqlite3.DatabaseError)
    assert cause.sqlite_errorcode & 0xFF in (11, 26)  # SQLITE_CORRUPT, _NOTADB


@pytest.mark.parametrize("shutdown", ["clean", "unclean"])
@pytest.mark.parametrize("page", ["page-1", "schema-leaf"])
def test_a_damaged_schema_page_is_storage_corruption_on_both_open_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    page: str,
    shutdown: str,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    number = 1 if page == "page-1" else _schema_leaf(target)
    if shutdown == "unclean":
        # The crashed owner's -wal holds a record_ids frame, not the schema.
        with Child(CRASH_WRITER, str(target), "2", home=home) as child:
            assert child.expect("written")["wal"] > 0
            child.kill_group()
    _corrupt_page(target, number)
    before = without_lock(tree(tmp_path))
    calls = connects(monkeypatch)
    for _ in range(2):
        with pytest.raises(StorageCorruption, match="reports the database") as raised:
            open_owned_database(str(target), mode="open")
        _assert_damage(raised.value)
    after = without_lock(tree(tmp_path))
    if shutdown == "clean":
        # The owner's connection saw the damage while configuring and wrote
        # nothing; no -wal or -shm is left.
        assert calls == ["mode=rw", "mode=rw"]
        assert after == before
    else:
        # The read-only validation refused it; the owner never connected and
        # only SQLite's -shm index was rebuilt.
        assert calls == ["mode=ro", "mode=ro"]
        assert {k: v for k, v in after.items() if not k.endswith("-shm")} == {
            k: v for k, v in before.items() if not k.endswith("-shm")
        }
    assert lock_is_free(target)


def test_damage_a_migration_reads_is_storage_corruption_after_its_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    add_tasks(target, 40)
    _corrupt_root(target, "tasks")  # validation never reads it; the rebuild copies it
    before = without_lock(tree(tmp_path))
    with_version_2(monkeypatch)
    with pytest.raises(StorageCorruption, match="reports the database") as raised:
        open_owned_database(str(target), mode="open")
    _assert_damage(raised.value)
    assert without_lock(tree(tmp_path)) == before  # rolled back, nothing written
    assert header_identity(target) == (APPLICATION_ID, 1)
    assert lock_is_free(target)


def test_the_runner_reports_damage_as_storage_corruption_after_its_rollback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    add_tasks(target, 40)
    _corrupt_root(target, "tasks")
    with raw(target) as connection:
        with pytest.raises(StorageCorruption, match="reports the database") as raised:
            sqlite_schema.apply_migrations(connection, (*V1, Migration(2, REBUILD)))
        _assert_damage(raised.value)
        assert not connection.in_transaction
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 1)]
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)


EXCLUSIVE_HOLDER = """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA locking_mode=EXCLUSIVE")
connection.execute("BEGIN IMMEDIATE")
connection.execute("INSERT INTO record_ids VALUES (9, 'task')")
say("holding")
command()
connection.execute("ROLLBACK")
connection.close()
say("released")
"""


def test_a_non_corruption_error_while_opening_passes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """A real SQLITE_BUSY from a foreign exclusive holder is not damage."""

    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    monkeypatch.setattr(sqlite_connection, "BUSY_TIMEOUT_SECONDS", 0.2)
    with Child(EXCLUSIVE_HOLDER, str(target), home=home) as child:
        child.expect("holding")
        before = without_lock(tree(tmp_path))
        with pytest.raises(sqlite3.OperationalError, match="locked") as raised:
            open_owned_database(str(target), mode="open")
        assert type(raised.value) is sqlite3.OperationalError
        assert raised.value.sqlite_errorcode & 0xFF == 5  # SQLITE_BUSY
        assert without_lock(tree(tmp_path)) == before
        assert lock_is_free(target)
        child.send("release")
        child.expect("released")
        assert child.wait() == 0
    db = open_owned_database(str(target), mode="open")
    try:
        assert stored(db.connection()) == ["kept"]
    finally:
        db.close()


@pytest.mark.parametrize(
    ("code", "damage"),
    [(11, True), (267, True), (26, True), (10, False), (5, False)],
    ids=["corrupt", "corrupt-extended", "notadb", "ioerr", "busy"],
)
def test_close_reports_damage_as_storage_corruption_and_passes_other_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, damage: bool
) -> None:
    """Close-time errors come from SQLite's checkpoint; injected at its seam."""

    target = tmp_path / "store.sqlite"
    db = open_owned_database(str(target), mode="create")
    injected = sqlite3.DatabaseError("injected close failure")
    injected.sqlite_errorcode = code

    def failing(connection: sqlite3.Connection) -> None:
        raise injected

    monkeypatch.setattr(sqlite_connection, "_truncate_checkpoint", failing)
    with pytest.raises(Exception) as first:
        db.close()
    with pytest.raises(Exception) as second:
        db.close()
    assert second.value is first.value  # a repeated close raises the same error
    if damage:
        assert type(first.value) is StorageCorruption
        assert first.value.__cause__ is injected
    else:
        assert first.value is injected
    monkeypatch.undo()
    open_owned_database(str(target), mode="open").close()  # the lock was released


# Interrupted create and the recovery rule ----------------------------------------


def _recover(target: Path) -> None:
    """The documented procedure, while no process owns the store."""

    assert lock_is_free(target)
    for suffix in ("", "-wal", "-shm", "-journal"):
        sidecar(target, suffix).unlink(missing_ok=True)


@pytest.mark.parametrize(
    "content",
    [b"", b"\x00" * 512, b"\x00" * 4096],
    ids=["empty", "zeros-512", "zeros-4096"],
)
def test_a_file_a_create_left_before_its_first_page_is_refused_then_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    target = tmp_path / "store.sqlite"
    target.write_bytes(content)
    target.chmod(0o600)
    before = tree(tmp_path)
    calls = connects(monkeypatch)
    with pytest.raises(InterruptedCreateError, match="interrupted create"):
        open_owned_database(str(target), mode="open")
    with pytest.raises(StorageExistsError):
        open_owned_database(str(target), mode="create")
    assert calls == [] and tree(tmp_path) == before
    sidecar(target, ".lock").write_bytes(b"")  # the crashed create's lock
    sidecar(target, ".lock").chmod(0o600)
    _recover(target)
    make_store(target, "fresh")
    assert integrity(target) == [("ok",)]


CREATE_CUT = """
stop, spill = int(sys.argv[2]), sys.argv[3] == "spill"
if spill:
    real = sqlite_connection._configure

    def configure(connection, owner, durability):
        real(connection, owner, durability)
        connection.execute("PRAGMA cache_size=1")  # spill uncommitted frames

    sqlite_connection._configure = configure

def cut(version, index):
    if version == 1 and index == stop:
        wal = sys.argv[1] + "-wal"
        say("migrating", wal=os.stat(wal).st_size if os.path.exists(wal) else -1)
        command()  # killed inside the create's first transaction

sqlite_schema._before_statement = cut
open_owned_database(sys.argv[1], mode="create")
say("finished")
"""


@pytest.mark.parametrize("spill", ["spill", "no-spill"])
def test_a_create_killed_before_its_schema_commit_is_refused_then_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, spill: str
) -> None:
    target = tmp_path / "state" / "store.sqlite"
    last = len(V1[0].statements) - 1
    with Child(CREATE_CUT, str(target), str(last), spill, home=home) as child:
        wal = child.expect("migrating")["wal"]
        child.kill_group()
    assert (wal > 0) is (spill == "spill")
    assert header_identity(target) == (0, 0)
    before = tree(target.parent)
    calls = connects(monkeypatch)
    for _ in range(2):
        with pytest.raises(InterruptedCreateError, match="interrupted create"):
            open_owned_database(str(target), mode="open")
    after = tree(target.parent)
    if spill == "spill":
        # Spilled frames make the shutdown unclean: only the read-only
        # validation connected, under the lock, and found no committed schema.
        # It rebuilt the -shm index; the database and -wal kept their bytes.
        assert calls == ["mode=ro", "mode=ro"]
        kept = {k: v for k, v in after.items() if not k.endswith(("-shm", ".lock"))}
        assert kept == {
            k: v for k, v in before.items() if not k.endswith(("-shm", ".lock"))
        }
    else:
        assert calls == [] and after == before  # refused before the lock
    with pytest.raises(StorageExistsError):
        open_owned_database(str(target), mode="create")
    _recover(target)
    make_store(target, "fresh")
    assert integrity(target) == [("ok",)]


CREATE_COMMITTED = """
real = sqlite_schema.apply_migrations

def committed(connection, migrations=None, **options):
    version = real(connection, migrations, **options)
    say("committed", version=version, wal=os.stat(sys.argv[1] + "-wal").st_size)
    command()  # killed after the commit, before create returns
    return version

sqlite_schema.apply_migrations = committed
open_owned_database(sys.argv[1], mode="create")
say("finished")
"""


def test_a_create_killed_after_its_schema_commit_opens_as_a_complete_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """The identity sits only in the -wal; open recovers and validates it."""

    target = tmp_path / "store.sqlite"
    with Child(CREATE_COMMITTED, str(target), home=home) as child:
        event = child.expect("committed")
        child.kill_group()
    assert event["version"] == 1 and event["wal"] > 0
    assert header_identity(target) == (0, 0)  # the file header still lags
    calls = connects(monkeypatch)
    db = open_owned_database(str(target), mode="open")
    try:
        assert calls == ["mode=ro", "mode=rw"]
        assert db.schema_version == 1
        connection = db.connection()
        assert connection.execute("SELECT * FROM durable_metadata").fetchall() == [
            (1, 2, 1)
        ]
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        db.close()
    assert header_identity(target) == (APPLICATION_ID, 1)  # checkpointed at close


# Migration crash cuts ------------------------------------------------------------

MIGRATE_CUT = """
extra = sqlite_schema.Migration(2, tuple(json.loads(sys.argv[2])))
sqlite_schema.MIGRATIONS = (*sqlite_schema.MIGRATIONS, extra)
stop = int(sys.argv[3])

def cut(version, index):
    if version == 2 and index == stop:
        say("migrating", wal=os.stat(sys.argv[1] + "-wal").st_size)
        command()  # killed inside the migration transaction

sqlite_schema._before_statement = cut
open_owned_database(sys.argv[1], mode="open")
say("finished")
"""


def test_sigkill_mid_migration_keeps_the_old_version_then_migrates_on_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    # Enough rows that copying them spills past the page cache into the -wal.
    rows = add_tasks(target, 1500, size=2000)
    with Child(MIGRATE_CUT, str(target), json.dumps(REBUILD), "2", home=home) as child:
        assert child.expect("migrating")["wal"] > 0  # uncommitted frames
        child.kill_group()
    assert header_identity(target) == (APPLICATION_ID, 1)
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        assert db.wal_size_at_open > 0 and db.schema_version == 1
        assert connection.execute("SELECT * FROM durable_schema").fetchall() == [(1, 1)]
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name = 'tasks_v2'"
        ).fetchone() == (0,)
        assert (
            connection.execute(
                f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id"
            ).fetchall()
            == rows
        )
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        db.close()
    with_version_2(monkeypatch)
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        assert db.schema_version == 2
        assert (
            connection.execute(
                f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id"
            ).fetchall()
            == rows
        )
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        db.close()


MIGRATE_COMMITTED = """
extra = sqlite_schema.Migration(2, tuple(json.loads(sys.argv[2])))
sqlite_schema.MIGRATIONS = (*sqlite_schema.MIGRATIONS, extra)
real = sqlite_schema.apply_migrations

def committed(connection, migrations=None, **options):
    version = real(connection, migrations, **options)
    say("committed", version=version, wal=os.stat(sys.argv[1] + "-wal").st_size)
    command()  # killed after the migration commit
    return version

sqlite_schema.apply_migrations = committed
open_owned_database(sys.argv[1], mode="open")
say("finished")
"""


def test_a_newer_version_only_in_the_wal_is_refused_with_the_bytes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    with Child(MIGRATE_COMMITTED, str(target), json.dumps(REBUILD), home=home) as child:
        assert child.expect("committed")["version"] == 2
        child.kill_group()
    assert header_identity(target) == (APPLICATION_ID, 1)  # version 2 is in the -wal
    before = {s: digest(sidecar(target, s)) for s in ("", "-wal")}
    calls = connects(monkeypatch)
    with pytest.raises(NewerStoreError, match=NEWER):
        open_owned_database(str(target), mode="open")
    assert calls == ["mode=ro"]  # the read-only connection never checkpoints
    assert {s: digest(sidecar(target, s)) for s in ("", "-wal")} == before
    with_version_2(monkeypatch)
    db = open_owned_database(str(target), mode="open")
    try:
        assert db.schema_version == 2 and stored(db.connection()) == ["kept"]
        assert db.connection().execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        db.close()
