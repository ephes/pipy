"""Verified SQLite connection lifecycle for an owned store (DE03a, DE03b).

Private and not exported; no Storage semantics (DE03c).
``open_owned_database`` admits its input, checks the SQLite runtime, takes path
ownership (``ownership.py``) and only then connects with ``mode=rw``, so SQLite
never creates the database itself and its sidecars inherit the owner-only mode.
Every PRAGMA is set explicitly and read back, because compile-time defaults
differ between builds (Ubuntu's SQLite enables ``secure_delete`` by default, the
macOS development build does not). ``create`` switches the new database to WAL;
``open`` requires an existing WAL database, so opening never rewrites a foreign
database's header. Close runs a TRUNCATE checkpoint, then closes the
connection, then releases the path lock, each step in ``finally``. The header is
read only through ownership's descriptor rule (see ``ownership`` on POSIX
record locks): never while a SQLite connection of this process may hold locks
on the file, since closing that descriptor would release them. The name is
checked against the claimed inode with ``os.stat`` under the lock, right after
connecting and after configuration, as best-effort detection under the
contract boundary below. Once connected, no descriptor of the database is
opened.

Schema and integrity (DE03b, ``sqlite_schema``). The header gate,
``check_store_header``, reads the header once through that descriptor rule and
refuses, before any connection, an interrupted create, a file that is not an
SQLite database in WAL mode, a foreign application ID and a newer schema
version, so SQLite never reads, recovers or checkpoints such a file. After an
unclean shutdown (a non-empty ``-wal`` found under the lock, Root decision D6)
a read-only connection validates the recovered store and runs ``quick_check``
before the owner connects: a read-only connection never checkpoints, so its
refusal leaves the database and ``-wal`` bytes unchanged (SQLite rebuilds only
its ``-shm`` index). It is closed before the owner's connection opens, so its
descriptors close while no other connection of this process holds locks on the
file. The owner's connection then runs ``sqlite_schema.prepare``: ``create``
migrates the new database, ``open`` validates first and applies pending
migrations only to a valid older store. Every corruption error SQLite reports
while a store is opened or closed (``SQLITE_CORRUPT`` with its extended codes,
``SQLITE_NOTADB``) is raised as ``sqlite_schema.StorageCorruption`` with the
SQLite error as its cause, after the same cleanup as any other failure; other
errors pass unchanged.

Contract boundary. The store directory is private to the owning user (owned by
that user, mode ``0700``; missing directories are created that way, and keeping
an existing one private is the caller's part). While any owner holds a store,
from the moment an open or create of it starts until its close has completed,
renaming, replacing, hard-linking or deleting any store file (the database, its
``-wal``, ``-shm`` and ``-journal``, and the lock file), by any actor, is
outside the contract. pipy detects some of these cases on a best-effort basis
(the identity checks under the lock, after connecting and after configuration;
the parking of descriptors that reach a claimed database; the re-check before
each unlink of a failed create) but guarantees nothing about them. SQLite's own
guidance is the same: do not rename, unlink or replace an open database or its
journal. With the store files left in place, the guarantees hold: one owner per
store across processes and within the process, owner-only file modes, no
descriptor of a store file held by another owner in this process is ever closed
by pipy, and invalid input, an ``open`` of a missing path and a store refused
before its lock is taken have no filesystem effect. Pi durable 1.3.0 has no
cross-process locking at all, so this remains strictly stronger than the
reference.

Connection settings follow Pi durable 1.3.0 ``packages/chord/src/sqlite/node.ts``
(blob b156477e8749c5b0315f0444396e82b8ee79a5bf, sha256
87eee26ec2cd23f0ec19fa785c4b985546d85cf791fd6454e75bbe643e6eac68):
``journal_mode=WAL``, ``wal_autocheckpoint`` 1000 pages, a 5000 ms busy timeout
and a ``wal_checkpoint(TRUNCATE)`` before close. Python decisions: synchronous
``FULL`` with ``fullfsync``/``checkpoint_fullfsync`` on macOS for the product,
``NORMAL`` as Pi's class for parity and benchmark runs (Root decision D3);
``temp_store=MEMORY``, ``trusted_schema=OFF`` and, by Root decision,
``secure_delete=ON`` on every platform, so deleted content is overwritten with
zeros in the database's pages (old WAL frames keep earlier page images until a
checkpoint truncates the log); SQLite 3.37 or newer (STRICT tables) in
serialized threading mode. MIT Copyright (c) 2025 Mario Zechner;
notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
import threading
import urllib.parse
from typing import Literal

from . import ownership, sqlite_schema
from .ownership import (
    PathOwner,
    StoragePath,
    StoragePathError,
    UnsupportedStoragePath,
)
from .storage import StorageClosedError

Durability = Literal["full", "normal"]

MIN_SQLITE_VERSION = (3, 37, 0)
_SQLITE_MAGIC = b"SQLite format 3\x00"
_HEADER_SIZE = 100
BUSY_TIMEOUT_SECONDS = 5.0
WAL_AUTOCHECKPOINT_PAGES = 1000
_PLATFORM = sys.platform
# The read-only connection that validates a store after an unclean shutdown.
_VALIDATION_SETTINGS = (("trusted_schema", "OFF", 0), ("temp_store", "MEMORY", 2))


class StorageConfigurationError(RuntimeError):
    """SQLite cannot run the durable store with its required configuration."""


def check_sqlite_runtime() -> None:
    """Fail closed on an old or non-serialized SQLite, before any file effect."""

    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        raise StorageConfigurationError(
            f"SQLite {sqlite3.sqlite_version} is older than the required 3.37.0"
        )
    if sqlite3.threadsafety != 3:
        raise StorageConfigurationError(
            "The durable store requires SQLite in serialized threading mode"
        )


def check_store_header(fd: int, label: str, wal_size: int) -> None:
    """The pre-connect gate: refuse a file the store cannot own, by its header.

    Runs on ownership's read-only descriptor before any connection exists, so
    SQLite never reads, recovers or rewrites a refused file. ``wal_size`` is the
    ``-wal`` size by ``lstat``; ``sqlite_schema.check_identity`` explains the
    one shape it lets through to be decided after WAL recovery.
    """

    size = os.fstat(fd).st_size
    data = os.pread(fd, min(size, sqlite_schema.MAX_PAGE_SIZE), 0)
    sqlite_schema.check_unwritten(data, size, label)
    header = data[:_HEADER_SIZE]
    _check_format(header, label)
    sqlite_schema.check_identity(header, size, label, wal_size)


def _check_format(header: bytes, label: str) -> None:
    """Refuse a file that is not an SQLite database in WAL mode.

    The database file format, sections 1.3.2 and 1.3.3: the magic string, a
    power-of-two page size of 512 to 65536, and write/read versions 2 for WAL.
    """

    size = int.from_bytes(header[16:18], "big")
    size = 65536 if size == 1 else size
    if (
        len(header) < _HEADER_SIZE
        or header[:16] != _SQLITE_MAGIC
        or not 512 <= size <= 65536
        or size & (size - 1)
    ):
        raise StorageConfigurationError(f"The {label} is not an SQLite database")
    if header[18] != 2 or header[19] != 2:
        raise StorageConfigurationError(
            f"The {label} is not a WAL database (file format versions"
            f" {header[18]}/{header[19]})"
        )


def _admit_durability(value: object) -> Durability:
    if type(value) is not str or value not in ("full", "normal"):
        raise ValueError("Durability must be exactly 'full' or 'normal'")
    return "full" if value == "full" else "normal"


def _uri(database: str, mode: Literal["rw", "ro"] = "rw") -> str:
    # An empty authority keeps a path that starts with "//" a path.
    quoted = urllib.parse.quote_from_bytes(os.fsencode(database), safe="/")
    return f"file://{quoted}?mode={mode}"


def _connect(database: str, mode: Literal["rw", "ro"]) -> sqlite3.Connection:
    return sqlite3.connect(
        _uri(database, mode),
        uri=True,
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None,
        check_same_thread=False,
    )


def _read_back(
    connection: sqlite3.Connection, settings: tuple[tuple[str, str, int], ...]
) -> None:
    for name, _, wanted in settings:
        actual = connection.execute(f"PRAGMA {name}").fetchone()[0]
        if type(actual) is not int or actual != wanted:
            raise StorageConfigurationError(
                f"SQLite PRAGMA {name} reads back {actual!r}, expected {wanted!r}"
            )


def _validate_after_crash(owner: PathOwner) -> None:
    """D6: validate a store whose last owner did not close it cleanly.

    The previous owner left a non-empty ``-wal``. A read-only connection lets
    SQLite recover it into the ``-shm`` index only and never checkpoints, so a
    refusal here leaves the database and ``-wal`` bytes unchanged. It is closed
    before the owner connects; no other connection of this process can hold
    locks on the file then (see ``ownership`` on POSIX record locks).
    """

    label = f"database {owner.path.database!r}"
    validation = _connect(owner.path.database, "ro")
    try:
        owner.pin_in_forked_children(validation)
        _verify_identity(owner)
        for name, value, _ in _VALIDATION_SETTINGS:
            validation.execute(f"PRAGMA {name}={value}").fetchall()
        _read_back(validation, _VALIDATION_SETTINGS)
        sqlite_schema.validate_store(validation, label=label)
        sqlite_schema.quick_check(validation, label=label)
    except BaseException as error:
        try:
            validation.close()
        except sqlite3.Error as cleanup:
            error.add_note(
                f"Closing the validation connection also failed: {cleanup!r}"
            )
        raise
    validation.close()


def _settings(durability: Durability) -> tuple[tuple[str, str, int], ...]:
    full = durability == "full"
    fullfsync = full and _PLATFORM == "darwin"
    return (
        ("trusted_schema", "OFF", 0),
        ("secure_delete", "ON", 1),
        ("temp_store", "MEMORY", 2),
        ("synchronous", "FULL" if full else "NORMAL", 2 if full else 1),
        ("fullfsync", "ON" if fullfsync else "OFF", int(fullfsync)),
        ("checkpoint_fullfsync", "ON" if fullfsync else "OFF", int(fullfsync)),
        ("wal_autocheckpoint", str(WAL_AUTOCHECKPOINT_PAGES), WAL_AUTOCHECKPOINT_PAGES),
    )


def _configure(
    connection: sqlite3.Connection, owner: PathOwner, durability: Durability
) -> None:
    """Apply and read back every setting; the journal mode comes last.

    The connection-level settings touch no file. Only ``create`` switches its
    new, empty database to WAL; ``open`` refuses a non-WAL file before anything
    is written to it, so a foreign rollback-journal database keeps its bytes.
    """

    settings = _settings(durability)
    for name, value, _ in settings:
        connection.execute(f"PRAGMA {name}={value}").fetchall()
    journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
    if owner.created:
        journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if journal != "wal":
            raise StorageConfigurationError(
                f"SQLite cannot use WAL for {owner.path.database!r} (got {journal!r})"
            )
    elif journal != "wal":
        raise StorageConfigurationError(
            f"{owner.path.database!r} is not a WAL database (journal mode {journal!r})"
        )
    timeout = ("busy_timeout", "", int(BUSY_TIMEOUT_SECONDS * 1000))
    _read_back(connection, (*settings, timeout))
    if connection.in_transaction:
        raise StorageConfigurationError("SQLite left a transaction open while opening")


def _verify_identity(owner: PathOwner) -> None:
    """The file SQLite opened by name is still the one checked under the lock."""

    path = owner.path
    try:
        database = os.stat(path.database, follow_symlinks=False)
        directory = os.stat(path.directory, follow_symlinks=False)
    except FileNotFoundError:
        raise UnsupportedStoragePath(
            f"The durable store {path.database!r} changed while opening"
        ) from None
    if (
        not stat.S_ISREG(database.st_mode)
        or database.st_nlink != 1
        or (database.st_dev, database.st_ino) != owner.database_identity
        or (directory.st_dev, directory.st_ino) != owner.directory_identity
    ):
        raise UnsupportedStoragePath(
            f"The durable store {path.database!r} changed while opening"
        )


def _truncate_checkpoint(connection: sqlite3.Connection) -> None:
    """Close-time checkpoint (Pi node.ts close); a module seam for failure tests."""

    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()


def _undo_failed_open(
    owner: PathOwner, connection: sqlite3.Connection | None, error: BaseException
) -> None:
    """Close, discard what ``create`` made and free the lock, keeping ``error``.

    A cleanup failure is attached to ``error`` as a note instead of replacing it.
    """

    try:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as cleanup:
                error.add_note(
                    f"Closing the failed connection also failed: {cleanup!r}"
                )
        try:
            owner.discard_created_files()
        except (OSError, StoragePathError) as cleanup:
            error.add_note(f"Removing the files of a failed create failed: {cleanup!r}")
    finally:
        owner.close()


class OwnedDatabase:
    """One owned durable SQLite file and its single connection.

    The raw connection is for the owning Storage (DE03c) and tests: its users
    serialize with ``close()``, and it is never used across ``fork()``. In a
    forked child ``connection()`` raises ``StorageForkedError`` and ``close()``
    does nothing, so the inherited connection is never touched there.
    ``close()`` is idempotent: a failed close raises the same error again.
    ``schema_version`` is the validated schema version after opening.
    Statements on the raw connection report damage as plain SQLite errors; its
    users wrap them in ``sqlite_schema.damage_as_corruption``.
    """

    __slots__ = (
        "_owner",
        "_connection",
        "durability",
        "schema_version",
        "_lock",
        "_closed",
        "_close_error",
    )

    def __init__(
        self,
        owner: PathOwner,
        connection: sqlite3.Connection,
        durability: Durability,
        schema_version: int,
    ) -> None:
        self._owner = owner
        self._connection = connection
        self.durability = durability
        self.schema_version = schema_version
        self._lock = threading.Lock()
        self._closed = False
        self._close_error: Exception | None = None

    @property
    def path(self) -> StoragePath:
        return self._owner.path

    @property
    def created(self) -> bool:
        return self._owner.created

    @property
    def wal_size_at_open(self) -> int:
        return self._owner.wal_size_at_open

    @property
    def owner(self) -> PathOwner:
        return self._owner

    def connection(self) -> sqlite3.Connection:
        self._owner.check_usable()
        with self._lock:
            if self._closed:
                raise StorageClosedError("The durable SQLite database is closed")
            return self._connection

    def close(self) -> None:
        if self._owner.forked:
            return
        with self._lock:
            if self._closed:
                if self._close_error is not None:
                    raise self._close_error
                return
            self._closed = True
            label = f"database {self._owner.path.database!r}"
            try:
                with sqlite_schema.damage_as_corruption(label):
                    try:
                        if self._connection.in_transaction:
                            # Closing discards it anyway; the checkpoint needs none.
                            self._connection.execute("ROLLBACK")
                        _truncate_checkpoint(self._connection)
                    finally:
                        self._connection.close()
            except Exception as error:
                self._close_error = error
                raise
            finally:
                self._owner.close()


def open_owned_database(
    path: object, *, mode: object, durability: object = "full"
) -> OwnedDatabase:
    """Own, connect, configure and migrate a durable SQLite store.

    ``mode="create"`` makes a new database (and missing parent directories),
    refuses an existing one and commits the current schema (immutable v1 DDL,
    semantic version 2) with the store's identity;
    ``mode="open"`` requires an existing pipy durable store in WAL mode without
    a rollback journal, identified by its header before SQLite sees it,
    validates it (after an unclean shutdown first on a read-only connection,
    with ``quick_check``) and applies pending migrations. Input is admitted and
    the runtime checked before any filesystem effect. A failure after
    ``create`` made the database removes it again; the lock is released on
    every failure path.
    """

    admitted = _admit_durability(durability)
    request = ownership.admit_request(path, mode)
    check_sqlite_runtime()
    owner = ownership.acquire(request, check=check_store_header)
    label = f"database {owner.path.database!r}"
    connection: sqlite3.Connection | None = None
    try:
        # Every SQLite step from here on reports damage as StorageCorruption;
        # the cleanup below sees the converted error.
        with sqlite_schema.damage_as_corruption(label):
            if owner.wal_size_at_open > 0:
                _validate_after_crash(owner)
            connection = _connect(owner.path.database, "rw")
            # Pinned before any other reference escapes, so a forked child can
            # never finalize this connection.
            owner.pin_in_forked_children(connection)
            # SQLite opened the file by name: before any PRAGMA can lock or
            # write it, the name must still name the inode claimed under the
            # lock. Like the check after configuration, this is best-effort
            # detection (see the contract boundary in ``ownership``).
            _verify_identity(owner)
            _configure(connection, owner, admitted)
            _verify_identity(owner)
            version = sqlite_schema.prepare(
                connection, created=owner.created, label=label
            )
    except BaseException as error:
        _undo_failed_open(owner, connection, error)
        raise
    return OwnedDatabase(owner, connection, admitted, version)
