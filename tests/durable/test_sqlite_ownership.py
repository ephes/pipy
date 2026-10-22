"""DE03a: path ownership, owner-only files and the minimal SQLite connection.

Native tests only (no Pi oracle): real child processes with bounded deadlines
and process-group reaping, real SIGKILL, real fork() and real exec. Stores use
the current semantic schema version and the immutable v1 table layout, so these
tests keep scratch rows in ``conversations`` (``put``/``stored``); nothing
reads them as records. No Storage semantics (DE03c).
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from sqlite_child_support import (
    Child,
    group_exists,
    kill_group,
    put,
    stored,
    wait_until_zombie,
)

from pipy_harness.native.durable import ownership, sqlite_connection
from pipy_harness.native.durable.ownership import (
    StorageExistsError,
    StorageNotFoundError,
    StorageOwnershipError,
    StoragePathError,
    UnsupportedStoragePath,
    admit_request,
    classify_directory,
    darwin_verdict,
    linux_mount_type,
    linux_verdict,
)
from pipy_harness.native.durable.sqlite_connection import (
    OwnedDatabase,
    StorageConfigurationError,
    open_owned_database,
)
from pipy_harness.native.durable.sqlite_schema import InterruptedCreateError
from pipy_harness.native.durable.storage import StorageClosedError


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("child-home")


def tree(root: Path) -> dict[str, tuple[str, int, str]]:
    """Kind, permission bits and content digest of every entry below ``root``.

    It reads every file, so it runs only while no store below ``root`` is open
    in this process (closing a read descriptor drops SQLite's POSIX locks);
    ``listing`` serves while one is.
    """

    found: dict[str, tuple[str, int, str]] = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(top) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                found[str(path.relative_to(root))] = ("link", 0, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[str(path.relative_to(root))] = (
                    "dir",
                    stat.S_IMODE(info.st_mode),
                    "",
                )
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                found[str(path.relative_to(root))] = (
                    "file",
                    stat.S_IMODE(info.st_mode),
                    digest,
                )
    return found


def listing(root: Path) -> dict[str, tuple[str, int, int]]:
    """Kind, permission bits and inode of every entry, by ``lstat`` alone.

    Safe while a store is open in this process: reading its database would
    open and close a descriptor, which releases SQLite's POSIX locks.
    """

    found: dict[str, tuple[str, int, int]] = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(top) / name
            info = path.lstat()
            kind = (
                "link"
                if stat.S_ISLNK(info.st_mode)
                else "dir"
                if stat.S_ISDIR(info.st_mode)
                else "file"
            )
            found[str(path.relative_to(root))] = (
                kind,
                stat.S_IMODE(info.st_mode),
                info.st_ino,
            )
    return found


def mode(path: Path | str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def sidecar(target: Path, suffix: str) -> Path:
    return Path(str(target) + suffix)


def make_store(target: Path, *rows: str) -> None:
    db = open_owned_database(str(target), mode="create")
    try:
        connection = db.connection()
        for row in rows:
            put(connection, row)
    finally:
        db.close()


def mark_lock(target: Path) -> None:
    """Give an existing lock distinctive contents, so any acquisition shows.

    A refused open must not take the lock; one that did would rewrite these
    diagnostics, which the same process could otherwise write identically
    within one second.
    """

    sidecar(target, ".lock").write_bytes(b"untouched by a refused open\n")


def rows(db: OwnedDatabase) -> list[str]:
    return stored(db.connection())


def case_insensitive(directory: Path) -> bool:
    return aliased(directory, "CaseProbe", "caseprobe")


def aliased(directory: Path, name: str, other: str) -> bool:
    """Whether this filesystem resolves ``other`` to the file named ``name``."""

    probe = directory / name
    probe.touch()
    try:
        return (directory / other).exists()
    finally:
        probe.unlink()


NFC = unicodedata.normalize("NFC", "caf\u00e9.sqlite")
NFD = unicodedata.normalize("NFD", NFC)


class _Hooked(str):
    """A str subclass whose hooks must never run during admission."""

    calls: list[str] = []

    def __eq__(self, other: object) -> bool:
        _Hooked.calls.append("eq")
        return str.__eq__(self, other)

    __hash__ = str.__hash__

    def __fspath__(self) -> str:
        _Hooked.calls.append("fspath")
        return str(self)

    def __add__(self, other: str) -> str:
        _Hooked.calls.append("add")
        return str.__add__(self, other)


# Ingress and refusals without filesystem effects ---------------------------------


def _bad_inputs(root: Path) -> list[tuple[object, object, object, type[Exception]]]:
    good = str(root / "new" / "store.sqlite")
    return [
        (os.fsencode(good), "create", "full", TypeError),
        (Path(good), "create", "full", TypeError),
        (PurePosixPath(good), "create", "full", TypeError),
        (_Hooked(good), "create", "full", TypeError),
        (42, "create", "full", TypeError),
        ("", "create", "full", StoragePathError),
        (good + "\x00x", "create", "full", StoragePathError),
        (str(root / "new") + "/", "create", "full", StoragePathError),
        (str(root / "new") + "/.", "create", "full", StoragePathError),
        (str(root / "new") + "/..", "create", "full", StoragePathError),
        (str(root / ("n" * 248)), "create", "full", StoragePathError),
        (str(root / "\ud800.sqlite"), "create", "full", StoragePathError),
        (
            str(root / "missing" / ".." / "store.sqlite"),
            "create",
            "full",
            StoragePathError,
        ),
        (good, "Create", "full", ValueError),
        (good, _Hooked("create"), "full", ValueError),
        (good, None, "full", ValueError),
        (good, "create", "FULL", ValueError),
        (good, "create", _Hooked("full"), ValueError),
        (good, "create", None, ValueError),
        (good + "-wal", "create", "full", StoragePathError),
        (good + "-shm", "open", "full", StoragePathError),
        (good + "-journal", "create", "full", StoragePathError),
        (good + ".LOCK", "open", "full", StoragePathError),
    ]


@pytest.mark.parametrize("index", range(len(_bad_inputs(Path("/unused")))))
def test_invalid_input_has_no_filesystem_effect_and_runs_no_caller_hook(
    tmp_path: Path, index: int
) -> None:
    path, open_mode, durability, error = _bad_inputs(tmp_path)[index]
    _Hooked.calls.clear()
    with pytest.raises(error):
        open_owned_database(path, mode=open_mode, durability=durability)
    assert _Hooked.calls == []
    assert tree(tmp_path) == {}


def test_name_length_boundary_leaves_room_for_every_sidecar(tmp_path: Path) -> None:
    longest = "n" * (255 - len("-journal"))
    assert admit_request(str(tmp_path / longest), "create").name == longest
    with pytest.raises(StoragePathError, match="no room"):
        admit_request(str(tmp_path / (longest + "n")), "create")
    target = tmp_path / longest
    make_store(target)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [longest, longest + ".lock"]
    )


@pytest.mark.parametrize("missing", ["store.sqlite", "no/such/dirs/store.sqlite"])
def test_open_of_a_missing_path_creates_nothing(tmp_path: Path, missing: str) -> None:
    with pytest.raises(StorageNotFoundError):
        open_owned_database(str(tmp_path / missing), mode="open")
    assert tree(tmp_path) == {}


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("sqlite_version_info", (3, 36, 9), "older than the required 3.37.0"),
        ("threadsafety", 1, "serialized threading mode"),
    ],
)
def test_sqlite_runtime_is_checked_before_any_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: object,
    message: str,
) -> None:
    monkeypatch.setattr(sqlite3, attribute, value)
    with pytest.raises(StorageConfigurationError, match=message):
        open_owned_database(str(tmp_path / "a" / "store.sqlite"), mode="create")
    assert tree(tmp_path) == {}


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_create_refuses_an_existing_database_or_stale_sidecar_untouched(
    tmp_path: Path, suffix: str
) -> None:
    target = tmp_path / "store.sqlite"
    stale = sidecar(target, suffix)
    stale.write_bytes(b"stale bytes")
    stale.chmod(0o640)
    before = tree(tmp_path)
    with pytest.raises(StorageExistsError):
        open_owned_database(str(target), mode="create")
    # SQLite would delete stale sidecars next to a new empty database; nothing
    # is created, locked, rewritten or chmod-ed instead.
    assert tree(tmp_path) == before


def test_parent_component_that_is_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"")
    before = tree(tmp_path)
    for path, open_mode in (
        ("file/store.sqlite", "create"),
        ("file/sub/store.sqlite", "create"),
        ("file/store.sqlite", "open"),
    ):
        with pytest.raises(StoragePathError, match="not a directory"):
            open_owned_database(str(tmp_path / path), mode=open_mode)
    assert tree(tmp_path) == before


# Lifecycle -----------------------------------------------------------------------


def test_create_then_open_keeps_every_file_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "state" / "nested" / "store.sqlite"
    db = open_owned_database(str(target), mode="create")
    try:
        assert db.created and db.wal_size_at_open == 0
        assert db.path.database == os.path.join(
            os.path.realpath(tmp_path), "state", "nested", "store.sqlite"
        )
        put(db.connection(), "one")
        assert mode(tmp_path / "state") == mode(tmp_path / "state" / "nested") == 0o700
        for suffix in ("", "-wal", "-shm", ".lock"):
            assert mode(sidecar(target, suffix)) == 0o600, suffix
        diagnostic = json.loads(sidecar(target, ".lock").read_text())
        assert diagnostic["pid"] == os.getpid() and diagnostic["schema"] == 1
        assert not os.get_inheritable(db.owner.lock_fd)
    finally:
        db.close()
    assert not sidecar(target, "-wal").exists()
    assert not sidecar(target, "-shm").exists()
    assert mode(target) == mode(sidecar(target, ".lock")) == 0o600
    again = open_owned_database(str(target), mode="open")
    try:
        assert not again.created and again.wal_size_at_open == 0
        assert rows(again) == ["one"]
    finally:
        again.close()
    with pytest.raises(StorageClosedError):
        again.connection()


@pytest.mark.parametrize("durability", ["full", "normal"])
def test_every_connection_setting_is_explicit_and_read_back(
    tmp_path: Path, durability: str
) -> None:
    db = open_owned_database(
        str(tmp_path / "store.sqlite"), mode="create", durability=durability
    )
    try:
        connection = db.connection()

        def value(name: str) -> object:
            return connection.execute(f"PRAGMA {name}").fetchone()[0]

        fullfsync = int(durability == "full" and sys.platform == "darwin")
        names = (
            "journal_mode",
            "secure_delete",
            "synchronous",
            "fullfsync",
            "checkpoint_fullfsync",
            "wal_autocheckpoint",
            "temp_store",
            "trusted_schema",
            "busy_timeout",
        )
        assert {name: value(name) for name in names} == {
            "journal_mode": "wal",
            "secure_delete": 1,
            "synchronous": 2 if durability == "full" else 1,
            "fullfsync": fullfsync,
            "checkpoint_fullfsync": fullfsync,
            "wal_autocheckpoint": 1000,
            "temp_store": 2,
            "trusted_schema": 0,
            "busy_timeout": 5000,
        }
        assert db.durability == durability
        assert connection.isolation_level is None and not connection.in_transaction
    finally:
        db.close()


WAL_SCAN = """
import re
data = open(sys.argv[1], "rb").read()
say("scanned", markers=sorted(set(m.decode() for m in re.findall(rb"pipy-residue-[0-9a-f]{24}", data))))
"""


@pytest.mark.parametrize("secure_delete", ["ON", "OFF"])
def test_a_deleted_row_leaves_no_residue_in_the_database_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, secure_delete: str
) -> None:
    """Root decision: ``secure_delete=ON`` on every platform.

    ``OFF`` is a negative control with the setting monkeypatched off; it must
    leave the deleted bytes behind, so the probe demonstrably sees residue. The
    files are read only while no connection is open, or from another process:
    closing a descriptor of them here would drop SQLite's per-process locks.
    Old WAL frames keep deleted content until the closing checkpoint truncates
    the log.
    """

    target = tmp_path / "store.sqlite"
    first = b"pipy-residue-" + os.urandom(12).hex().encode()
    second = b"pipy-residue-" + os.urandom(12).hex().encode()
    if secure_delete == "OFF":
        real = sqlite_connection._settings

        def without(durability: str) -> tuple[tuple[str, str, int], ...]:
            return tuple(
                ("secure_delete", "OFF", 0) if entry[0] == "secure_delete" else entry
                for entry in real(durability)  # type: ignore[arg-type]
            )

        monkeypatch.setattr(sqlite_connection, "_settings", without)
    db = open_owned_database(str(target), mode="create")
    try:
        connection = db.connection()
        connection.execute("INSERT INTO conversations (record) VALUES (?)", (b"kept",))
        connection.execute("INSERT INTO conversations (record) VALUES (?)", (first,))
    finally:
        db.close()
    assert first in target.read_bytes()  # the checkpoint wrote it into the file
    db = open_owned_database(str(target), mode="open")
    try:
        connection = db.connection()
        connection.execute("INSERT INTO conversations (record) VALUES (?)", (second,))
        connection.execute(
            "DELETE FROM conversations WHERE record IN (?, ?)", (first, second)
        )
        assert connection.execute("SELECT record FROM conversations").fetchall() == [
            (b"kept",)
        ]
        # Earlier frames keep the rows; another process reads the -wal.
        with Child(WAL_SCAN, str(sidecar(target, "-wal")), home=home) as child:
            assert child.expect("scanned")["markers"] == sorted(
                [first.decode(), second.decode()]
            )
            assert child.wait() == 0
    finally:
        db.close()
    assert not sidecar(target, "-wal").exists()
    assert not sidecar(target, "-shm").exists()
    data = target.read_bytes()
    residue = secure_delete == "OFF"
    assert (first in data, second in data) == (residue, residue)


@pytest.mark.parametrize("open_mode", ["create", "open"])
def test_a_setting_that_does_not_read_back_refuses_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, open_mode: str
) -> None:
    target = tmp_path / "store.sqlite"
    if open_mode == "open":
        make_store(target, "kept")
    before = tree(tmp_path)
    real = sqlite_connection._settings

    def drifting(durability: str) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (name, value, 1 if name == "temp_store" else wanted)
            for name, value, wanted in real(durability)  # type: ignore[arg-type]
        )

    monkeypatch.setattr(sqlite_connection, "_settings", drifting)
    with pytest.raises(StorageConfigurationError, match="temp_store reads back 2"):
        open_owned_database(str(target), mode=open_mode)
    monkeypatch.undo()
    if open_mode == "create":
        assert sorted(p.name for p in tmp_path.iterdir()) == ["store.sqlite.lock"]
    else:
        # The refusal came after the lock was taken, so only the lock's
        # diagnostic contents (its acquisition time) may differ.
        after = tree(tmp_path)
        lock = "store.sqlite.lock"
        assert after[lock][:2] == before[lock][:2]
        assert {k: v for k, v in after.items() if k != lock} == {
            k: v for k, v in before.items() if k != lock
        }
    db = open_owned_database(str(target), mode=open_mode)  # the lock was released
    db.close()


def _store_bytes(directory: Path) -> bytes:
    """The bytes of a closed, valid durable store made in ``directory``."""

    source = directory / "source.sqlite"
    make_store(source, "kept")
    return source.read_bytes()


def _write_not_wal(kind: str, target: Path, scratch: Path) -> None:
    if kind == "empty":
        target.write_bytes(b"")
    elif kind == "text":
        target.write_bytes(b'{"not": "a database"}\n' * 100)
    elif kind == "truncated-header":
        target.write_bytes(_store_bytes(scratch)[:60])
    elif kind == "bad-page-size":
        data = bytearray(_store_bytes(scratch))
        data[16:18] = (1000).to_bytes(2, "big")
        target.write_bytes(bytes(data))
    elif kind == "bad-magic":
        data = bytearray(_store_bytes(scratch))
        data[:16] = b"SQLite format 4\x00"  # every other header field is valid
        target.write_bytes(bytes(data))
    else:  # A real rollback-journal (DELETE mode) SQLite database.
        raw = sqlite3.connect(target)
        try:
            raw.execute("CREATE TABLE f(x)")
            raw.commit()
        finally:
            raw.close()
    target.chmod(0o600)


@pytest.mark.parametrize(
    ("kind", "error", "message"),
    [
        # An empty file is what a crashed create leaves (DE03b recovery rule).
        ("empty", InterruptedCreateError, "interrupted create"),
        ("text", StorageConfigurationError, "not an SQLite database"),
        ("truncated-header", StorageConfigurationError, "not an SQLite database"),
        ("bad-page-size", StorageConfigurationError, "not an SQLite database"),
        ("bad-magic", StorageConfigurationError, "not an SQLite database"),
        ("rollback-mode", StorageConfigurationError, "not a WAL database"),
    ],
)
def test_open_refuses_a_non_wal_file_by_its_header_before_sqlite_sees_it(
    tmp_path: Path, kind: str, error: type[Exception], message: str
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    target = work / "foreign.sqlite"
    _write_not_wal(kind, target, scratch)
    before = tree(work)
    for _ in range(2):  # Nothing was locked or changed, so a retry is refused alike.
        with pytest.raises(error, match=message):
            open_owned_database(str(target), mode="open")
    assert tree(work) == before  # same bytes and mode, and no lock file


def test_configuration_still_refuses_a_non_wal_file_the_header_check_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth behind the header check, e.g. against a racing writer."""

    target = tmp_path / "foreign.sqlite"
    _write_not_wal("rollback-mode", target, tmp_path)
    original = target.read_bytes()
    monkeypatch.setattr(
        sqlite_connection, "check_store_header", lambda fd, label, wal_size: None
    )
    with pytest.raises(StorageConfigurationError, match="journal mode 'delete'"):
        open_owned_database(str(target), mode="open")
    assert target.read_bytes() == original


HOT_JOURNAL = """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA journal_mode=DELETE")
connection.execute("PRAGMA cache_size=2")
connection.execute("CREATE TABLE t(v BLOB)")
connection.execute("INSERT INTO t VALUES (x'00')")
connection.execute("BEGIN")
for index in range(250):
    connection.execute("INSERT INTO t VALUES (?)", (os.urandom(4000),))
say(
    "crashing",
    database=os.path.getsize(sys.argv[1]),
    journal=os.path.getsize(sys.argv[1] + "-journal"),
)
os._exit(0)  # A crash mid-transaction: spilled pages and a hot journal.
"""


def test_open_refuses_a_hot_rollback_journal_without_touching_it(
    tmp_path: Path, home: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = work / "foreign.sqlite"
    with Child(HOT_JOURNAL, str(target), home=home) as child:
        crashed = child.expect("crashing")
        assert child.wait() == 0
    assert crashed["journal"] > 0 and crashed["database"] > 8192
    files = {
        suffix: sidecar(target, suffix).read_bytes() for suffix in ("", "-journal")
    }
    # The fixture is really hot: plain SQLite rolls a copy back on first read.
    copy = tmp_path / "copy"
    copy.mkdir()
    for suffix, data in files.items():
        (copy / ("foreign.sqlite" + suffix)).write_bytes(data)
    raw = sqlite3.connect(copy / "foreign.sqlite")
    try:
        assert raw.execute("SELECT count(*) FROM t").fetchone() == (1,)
    finally:
        raw.close()
    assert not (copy / "foreign.sqlite-journal").exists()
    assert (copy / "foreign.sqlite").read_bytes() != files[""]
    before = tree(work)
    with pytest.raises(UnsupportedStoragePath, match="rollback journal"):
        open_owned_database(str(target), mode="open")
    assert tree(work) == before  # database and journal bytes unchanged, no lock


def test_open_refuses_a_stale_journal_next_to_a_wal_store_unchanged(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    journal = sidecar(target, "-journal")
    journal.write_bytes(b"\x01" * 512)  # SQLite would treat it as hot
    journal.chmod(0o600)
    mark_lock(target)
    before = tree(tmp_path)
    with pytest.raises(UnsupportedStoragePath, match="rollback journal"):
        open_owned_database(str(target), mode="open")
    assert tree(tmp_path) == before  # including the lock file's contents


def test_a_failed_flush_after_create_removes_the_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"

    def failing(fd: int) -> None:
        raise OSError(errno.EIO, "injected flush failure")

    monkeypatch.setattr(ownership, "_sync_directory", failing)
    with pytest.raises(OSError, match="injected flush failure") as raised:
        open_owned_database(str(target), mode="create")
    assert raised.value.errno == errno.EIO
    monkeypatch.undo()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["store.sqlite.lock"]
    make_store(target, "after")  # the lock is free and create works again
    db = open_owned_database(str(target), mode="open")
    try:
        assert rows(db) == ["after"]
    finally:
        db.close()


def test_failed_create_removes_only_its_own_files_and_frees_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "new" / "store.sqlite"
    real = sqlite_connection._configure

    def failing(connection: sqlite3.Connection, owner: object, durability: str) -> None:
        real(connection, owner, durability)  # type: ignore[arg-type]
        connection.execute("CREATE TABLE t(x)")
        assert sidecar(target, "-wal").exists()
        raise OSError("injected configuration failure")

    monkeypatch.setattr(sqlite_connection, "_configure", failing)
    with pytest.raises(OSError, match="injected configuration failure"):
        open_owned_database(str(target), mode="create")
    assert sorted(p.name for p in target.parent.iterdir()) == ["store.sqlite.lock"]
    monkeypatch.undo()
    make_store(target, "after")


def test_cleanup_failure_after_a_failed_open_never_masks_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    real = sqlite_connection._configure

    def failing(connection: sqlite3.Connection, owner: object, durability: str) -> None:
        real(connection, owner, durability)  # type: ignore[arg-type]
        raise OSError("injected configuration failure")

    def discard_fails(self: object) -> None:
        raise OSError("injected discard failure")

    monkeypatch.setattr(sqlite_connection, "_configure", failing)
    monkeypatch.setattr(ownership.PathOwner, "discard_created_files", discard_fails)
    with pytest.raises(OSError, match="injected configuration failure") as raised:
        open_owned_database(str(target), mode="create")
    notes = getattr(raised.value, "__notes__", [])
    assert len(notes) == 1 and "injected discard failure" in notes[0]
    monkeypatch.undo()
    owner = ownership.acquire_path(str(target), "open")  # the lock was released
    owner.close()


def test_every_new_directory_entry_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[tuple[int, int]] = []
    real = ownership._sync_directory

    def recording(fd: int) -> None:
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
        real(fd)

    monkeypatch.setattr(ownership, "_sync_directory", recording)
    make_store(tmp_path / "a" / "b" / "store.sqlite")
    expected = [tmp_path, tmp_path / "a", tmp_path / "a" / "b"]
    assert synced == [(os.stat(d).st_dev, os.stat(d).st_ino) for d in expected]


def test_close_frees_the_lock_and_repeats_its_outcome_when_the_checkpoint_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    db = open_owned_database(str(target), mode="create")
    put(db.connection(), "kept")
    injected = OSError("injected checkpoint failure")

    def failing(connection: sqlite3.Connection) -> None:
        raise injected

    monkeypatch.setattr(sqlite_connection, "_truncate_checkpoint", failing)
    with pytest.raises(OSError) as first:
        db.close()
    with pytest.raises(OSError) as second:
        db.close()
    assert first.value is injected and second.value is injected
    with pytest.raises(StorageClosedError):
        db.connection()
    monkeypatch.undo()
    again = open_owned_database(str(target), mode="open")
    again.close()


def test_close_discards_an_abandoned_transaction(tmp_path: Path) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    db = open_owned_database(str(target), mode="open")
    db.connection().execute("BEGIN IMMEDIATE")
    put(db.connection(), "abandoned")
    db.close()
    again = open_owned_database(str(target), mode="open")
    try:
        assert rows(again) == ["kept"]
    finally:
        again.close()


# Aliases, links and foreign files ---------------------------------------------


@pytest.mark.parametrize(
    "alias", ["same", "relative", "dot-dot", "dot", "symlinked-directory"]
)
def test_second_in_process_open_meets_the_same_owner_through_every_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, alias: str
) -> None:
    """A refused second open keeps the owner's SQLite locks and data.

    Closing any descriptor of a file drops this process's POSIX locks on it,
    SQLite's included. A refused open that opened the owned database, even
    read-only, would let an external writer in and lose committed rows (round-2
    review), so the refusal must come before any descriptor touches it.
    """

    real = tmp_path / "real"
    target = real / "store.sqlite"
    db = owned_with_a_row(target)
    try:
        assert external_write(target, home) == "database is locked"
        if alias == "same":
            other = str(target)
        elif alias == "relative":
            monkeypatch.chdir(real)
            other = "store.sqlite"
        elif alias == "dot-dot":
            other = str(tmp_path / "real" / ".." / "real" / "store.sqlite")
        elif alias == "dot":
            other = str(real / "." / "store.sqlite")
        else:
            (tmp_path / "link").symlink_to(real, target_is_directory=True)
            other = str(tmp_path / "link" / "store.sqlite")
        before = listing(tmp_path)
        with pytest.raises(StorageOwnershipError, match="already open in this process"):
            open_owned_database(other, mode="open")
        with pytest.raises(StorageExistsError):
            open_owned_database(other, mode="create")
        assert listing(tmp_path) == before
        assert external_write(target, home) == "database is locked"
        assert db.owner._held.parked == []  # refused unread: nothing was opened
        put(db.connection(), "second")
    finally:
        db.close()
    assert_rows_after_close(target, ["first", "second"])


@pytest.mark.parametrize(
    ("name", "spelling"), [("Store.sqlite", "STORE.SQLITE"), (NFC, NFD)]
)
def test_case_or_normalization_variant_meets_the_same_lock_or_names_another_file(
    tmp_path: Path, home: Path, name: str, spelling: str
) -> None:
    # Runtime detection picks the branch this filesystem needs; never skipped.
    insensitive = aliased(tmp_path, name, spelling)
    target = tmp_path / name
    variant = str(tmp_path / spelling)
    db = owned_with_a_row(target)
    try:
        if insensitive:
            with pytest.raises(
                StorageOwnershipError, match="already open in this process"
            ):
                open_owned_database(variant, mode="open")
            with pytest.raises(StorageExistsError):
                open_owned_database(variant, mode="create")
            # The refusals never opened the owned file (its locks survive).
            assert external_write(target, home) == "database is locked"
        else:
            with pytest.raises(StorageNotFoundError):
                open_owned_database(variant, mode="open")
            other = open_owned_database(variant, mode="create")
            try:
                assert os.stat(variant).st_ino != os.stat(target).st_ino
            finally:
                other.close()
    finally:
        db.close()


def test_symlinked_database_name_is_refused_even_unowned(tmp_path: Path) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    link = tmp_path / "alias.sqlite"
    link.symlink_to(target)
    mark_lock(target)
    before = tree(tmp_path)
    with pytest.raises(UnsupportedStoragePath, match="symbolic link"):
        open_owned_database(str(link), mode="open")
    with pytest.raises(StorageExistsError):
        open_owned_database(str(link), mode="create")
    assert tree(tmp_path) == before


def test_hardlinked_database_is_refused_even_unowned(tmp_path: Path) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    second = tmp_path / "second.sqlite"
    os.link(target, second)
    mark_lock(target)
    before = tree(tmp_path)
    for path in (target, second):
        with pytest.raises(UnsupportedStoragePath, match="2 hard links"):
            open_owned_database(str(path), mode="open")
    assert tree(tmp_path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal", ".lock"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_sidecar_or_lock_file_is_refused(
    tmp_path: Path, suffix: str, kind: str
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"")
    elsewhere.chmod(0o600)
    linked = sidecar(target, suffix)
    if linked.exists():
        linked.unlink()
    if kind == "symlink":
        linked.symlink_to(elsewhere)
    else:
        os.link(elsewhere, linked)
    if suffix != ".lock":
        mark_lock(target)
    before = tree(tmp_path)
    with pytest.raises(UnsupportedStoragePath, match="symbolic link|2 hard links"):
        open_owned_database(str(target), mode="open")
    assert tree(tmp_path) == before


def test_files_owned_by_another_user_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    mark_lock(target)
    before = tree(tmp_path)
    # chown needs privileges; another effective user sees every file as foreign.
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(UnsupportedStoragePath, match="belongs to another user"):
        open_owned_database(str(target), mode="open")
    monkeypatch.undo()
    assert tree(tmp_path) == before


def test_existing_group_and_other_bits_are_reduced_to_owner_only(
    tmp_path: Path,
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    # No -journal: an existing rollback journal refuses the store instead.
    widened = {"": 0o644, ".lock": 0o664, "-wal": 0o640, "-shm": 0o604}
    for suffix, bits in widened.items():
        path = sidecar(target, suffix)
        if not path.exists():
            path.write_bytes(b"")
        path.chmod(bits)
    db = open_owned_database(str(target), mode="open")
    try:
        for suffix in widened:
            assert mode(sidecar(target, suffix)) == 0o600, suffix
        assert rows(db) == ["kept"]
    finally:
        db.close()


@pytest.mark.parametrize("bits", [0o000, 0o200, 0o400, 0o644 & ~0o200])
def test_file_without_owner_read_and_write_is_refused(
    tmp_path: Path, bits: int
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    mark_lock(target)
    target.chmod(bits)
    before = tree(tmp_path) if bits & 0o400 else None
    try:
        with pytest.raises(UnsupportedStoragePath, match="readable and writable"):
            open_owned_database(str(target), mode="open")
        assert mode(target) == bits
        if before is not None:
            assert tree(tmp_path) == before
    finally:
        target.chmod(0o600)


@pytest.mark.parametrize("open_mode", ["open", "create"])
def test_concurrent_in_process_opens_admit_exactly_one_owner(
    tmp_path: Path, home: Path, open_mode: str
) -> None:
    """Exactly one thread wins; the refused ones never release its locks.

    A refused thread either finds the winner registered and opens nothing, or
    reads the header before the winner has connected.
    """

    target = tmp_path / "store.sqlite"
    if open_mode == "open":
        make_store(target, "first")
    barrier = threading.Barrier(8)
    results: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait(timeout=30)
        try:
            outcome: object = open_owned_database(str(target), mode=open_mode)
        except (StorageOwnershipError, StorageExistsError) as error:
            outcome = error
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    owners = [item for item in results if isinstance(item, OwnedDatabase)]
    assert len(results) == 8 and len(owners) == 1
    key = owners[0].owner._held.key
    assert key in ownership._REGISTRY
    try:
        connection = owners[0].connection()
        if open_mode == "create":
            put(connection, "first")
        assert external_write(target, home) == "database is locked"
        put(connection, "second")
    finally:
        owners[0].close()
    assert key not in ownership._REGISTRY
    assert_rows_after_close(target, ["first", "second"])


def test_fork_hooks_hold_the_registry_lock_across_fork() -> None:
    assert not ownership._REGISTRY_LOCK.locked()
    ownership._fork_prepare()
    try:
        assert ownership._REGISTRY_LOCK.locked()
    finally:
        ownership._fork_parent()
    assert not ownership._REGISTRY_LOCK.locked()


# Filesystem classification ----------------------------------------------------

MOUNTINFO = """\
22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
23 22 0:21 / /proc rw,nosuid shared:2 - proc proc rw
30 22 0:40 / /home/user/My\\040Mount rw,relatime - nfs4 server:/export rw,vers=4.2
31 22 0:41 / /mnt/share rw - cifs //host/share rw
32 22 0:42 / /mnt/ssh rw,nosuid shared:7 master:3 - fuse.sshfs user@host:/ rw
33 22 0:43 / /mnt/host rw - 9p host0 rw
34 22 0:44 / /mnt/vfs rw - virtiofs share rw
35 22 0:45 / /var/lib/docker/x rw - overlay overlay rw
36 22 8:2 / /data rw - xfs /dev/sda2 rw
37 22 0:46 / /tmp rw - tmpfs tmpfs rw
38 22 8:3 / /boot/efi rw - vfat /dev/sda3 rw
39 36 8:2 /sub /data rw - btrfs /dev/sda4 rw
"""


@pytest.mark.parametrize(
    ("mount", "fstype", "supported"),
    [
        ("22", "ext4", True),
        ("36", "xfs", True),
        ("37", "tmpfs", True),
        ("39", "btrfs", True),
        ("23", "proc", False),
        ("30", "nfs4", False),
        ("31", "cifs", False),
        ("32", "fuse.sshfs", False),
        ("33", "9p", False),
        ("34", "virtiofs", False),
        ("35", "overlay", False),
        ("38", "vfat", False),
    ],
)
def test_linux_classifier_reads_the_descriptors_own_mount(
    mount: str, fstype: str, supported: bool
) -> None:
    fdinfo = f"pos:\t0\nflags:\t02200000\nmnt_id:\t{mount}\nino:\t42\n"
    assert linux_mount_type(fdinfo, MOUNTINFO) == fstype
    assert (linux_verdict(fstype) is None) is supported


@pytest.mark.parametrize("fdinfo", ["pos:\t0\n", "mnt_id:\t99\n", "mnt_id:\n"])
def test_linux_classifier_fails_closed_without_an_exact_mount(fdinfo: str) -> None:
    with pytest.raises(UnsupportedStoragePath):
        linux_mount_type(fdinfo, MOUNTINFO)


@pytest.mark.parametrize(
    ("fstype", "flags", "supported"),
    [
        ("apfs", 0x1000, True),
        ("hfs", 0x1000 | 0x1, True),
        ("apfs", 0x0, False),
        ("smbfs", 0x0, False),
        ("nfs", 0x0, False),
        ("afpfs", 0x0, False),
        ("webdav", 0x0, False),
        ("msdos", 0x1000, False),
        ("exfat", 0x1000, False),
        ("", 0x1000, False),
    ],
)
def test_darwin_classifier_requires_a_local_apfs_or_hfs_mount(
    fstype: str, flags: int, supported: bool
) -> None:
    assert (darwin_verdict(fstype, flags) is None) is supported


def test_this_hosts_test_directory_is_classified_as_local(tmp_path: Path) -> None:
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        classify_directory(fd)
        if sys.platform == "darwin":
            fstype, flags = ownership._darwin_filesystem(fd)
            assert fstype in {"apfs", "hfs"} and flags & 0x1000
        else:
            assert sys.platform.startswith("linux")
            assert linux_verdict(ownership._linux_filesystem(fd)) is None
    finally:
        os.close(fd)


def test_unsupported_filesystem_is_refused_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "darwin":
        monkeypatch.setattr(ownership, "_darwin_filesystem", lambda fd: ("nfs", 0))
        reason = "nfs is not a local filesystem"
    else:
        monkeypatch.setattr(ownership, "_linux_filesystem", lambda fd: "nfs4")
        reason = "nfs4 is not a supported local filesystem"
    with pytest.raises(UnsupportedStoragePath, match=reason):
        open_owned_database(str(tmp_path / "a" / "b" / "store.sqlite"), mode="create")
    assert tree(tmp_path) == {}


# Real processes ------------------------------------------------------------------


def test_child_helper_tolerates_a_zombie_only_process_group() -> None:
    """macOS refuses ``killpg`` (EPERM) while a group holds only zombies.

    A forked grandchild that exited but is not yet reaped by init left such a
    group behind; the helper must neither raise nor report the group gone early.
    """

    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "pass"],
        env={"PATH": os.defpath},
        start_new_session=True,
    )
    try:
        wait_until_zombie(child.pid)  # exited, not yet reaped
        assert group_exists(child.pid)
        kill_group(child.pid)
        assert group_exists(child.pid)
    finally:
        assert child.wait(timeout=30) == 0
    assert not group_exists(child.pid)


EXCLUSIVE_WRITER = """
import sqlite3
connection = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    connection.execute("PRAGMA locking_mode=EXCLUSIVE")
    connection.execute("BEGIN IMMEDIATE")
    put(connection, "intruder")
    connection.execute("COMMIT")
    outcome = "wrote"
except sqlite3.OperationalError as error:
    outcome = str(error)
finally:
    connection.close()
say("tried", outcome=outcome)
"""


def external_write(target: Path, home: Path) -> str:
    """An external EXCLUSIVE-mode writer; the owner's SHARED lock must stop it."""

    with Child(EXCLUSIVE_WRITER, str(target), home=home) as child:
        outcome = str(child.expect("tried")["outcome"])
        assert child.wait() == 0
    return outcome


def owned_with_a_row(target: Path) -> OwnedDatabase:
    db = open_owned_database(str(target), mode="create")
    put(db.connection(), "first")
    return db


def assert_rows_after_close(target: Path, expected: list[str]) -> None:
    db = open_owned_database(str(target), mode="open")
    try:
        assert rows(db) == expected
        assert db.connection().execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        db.close()


def test_the_lock_refuses_a_second_in_process_owner_before_flock(
    tmp_path: Path, home: Path
) -> None:
    """Without a format gate the lock's own registry check refuses first.

    ``flock`` would also refuse a second description in this process, but only
    the registry names the in-process owner, and it runs before ``flock``.
    """

    target = tmp_path / "store.sqlite"
    db = owned_with_a_row(target)
    try:
        with pytest.raises(StorageOwnershipError, match="already open in this process"):
            ownership.acquire_path(str(target), "open")
        assert external_write(target, home) == "database is locked"
    finally:
        db.close()
    assert_rows_after_close(target, ["first"])


OWNER = """
db = open_owned_database(sys.argv[1], mode=sys.argv[2])
say("owned")
if command() == "close":
    db.close()
    say("closed")
"""


@pytest.mark.parametrize("spelling", ["same", "case-variant"])
def test_a_second_process_is_refused_until_the_owner_closes(
    tmp_path: Path, home: Path, spelling: str
) -> None:
    target = tmp_path / "Store.sqlite"
    insensitive = case_insensitive(tmp_path)
    with Child(OWNER, str(target), "create", home=home) as child:
        child.expect("owned")
        other = str(target) if spelling == "same" else str(tmp_path / "STORE.SQLITE")
        if spelling == "same" or insensitive:
            with pytest.raises(StorageOwnershipError, match=f"pid {child.pid}"):
                open_owned_database(other, mode="open")
        else:
            with pytest.raises(StorageNotFoundError):
                open_owned_database(other, mode="open")
        child.send("close")
        child.expect("closed")
        assert child.wait() == 0
    db = open_owned_database(str(target), mode="open")
    db.close()


SIGKILL_OWNER = """
db = open_owned_database(sys.argv[1], mode="create")
connection = db.connection()
for index in range(5):
    connection.execute("BEGIN IMMEDIATE")
    put(connection, "row%d" % index)
    connection.execute("COMMIT")
say("committed", wal=os.stat(sys.argv[1] + "-wal").st_size)
command()
"""


def test_sigkill_at_idle_with_committed_wal_then_reopen(
    tmp_path: Path, home: Path
) -> None:
    target = tmp_path / "state" / "store.sqlite"
    with Child(SIGKILL_OWNER, str(target), home=home) as child:
        assert child.expect("committed")["wal"] > 0
        child.kill_group()
        assert child.process.returncode == -signal.SIGKILL
    files = {
        suffix: sidecar(target, suffix) for suffix in ("", "-wal", "-shm", ".lock")
    }
    assert all(mode(path) == 0o600 for path in files.values())
    assert files["-wal"].stat().st_size > 0
    stale = json.loads(files[".lock"].read_text())
    assert stale["pid"] == child.pid  # the dead owner's diagnostic decides nothing
    db = open_owned_database(str(target), mode="open")
    try:
        assert db.wal_size_at_open > 0
        assert rows(db) == [f"row{index}" for index in range(5)]
        connection = db.connection()
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert all(mode(path) == 0o600 for path in files.values())
        assert json.loads(files[".lock"].read_text())["pid"] == os.getpid()
    finally:
        db.close()
    assert not files["-wal"].exists() and not files["-shm"].exists()


UMASK_OWNER = """
os.umask(int(sys.argv[2], 8))
db = open_owned_database(sys.argv[1], mode="create")
connection = db.connection()
put(connection, "1")
wal = modes(sys.argv[3])
connection.execute("PRAGMA journal_mode=DELETE")
connection.execute("BEGIN IMMEDIATE")
put(connection, "2")
journal = modes(sys.argv[3])
connection.execute("ROLLBACK")
connection.execute("PRAGMA journal_mode=WAL")
db.close()
say("modes", wal=wal, journal=journal, closed=modes(sys.argv[3]), umask=oct(os.umask(0)))
"""


@pytest.mark.parametrize("umask", ["000", "002", "022", "077", "777"])
def test_umask_never_changes_owner_only_modes(
    tmp_path: Path, home: Path, umask: str
) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    target = root / "a" / "b" / "store.sqlite"
    with Child(UMASK_OWNER, str(target), umask, str(root), home=home) as child:
        report = child.expect("modes")
        assert child.wait() == 0
    assert report["umask"] == oct(int(umask, 8))
    directories = {"a": "0o700", "a/b": "0o700"}
    lock_and_db = {"a/b/store.sqlite": "0o600", "a/b/store.sqlite.lock": "0o600"}
    assert report["wal"] == {
        **directories,
        **lock_and_db,
        "a/b/store.sqlite-wal": "0o600",
        "a/b/store.sqlite-shm": "0o600",
    }
    assert report["journal"] == {
        **directories,
        **lock_and_db,
        "a/b/store.sqlite-journal": "0o600",
    }
    assert report["closed"] == {**directories, **lock_and_db}


EXEC_OWNER = """
import subprocess
db = open_owned_database(sys.argv[1], mode="create")
put(db.connection(), "1")
lister = (
    "import json, os\\n"
    "found = []\\n"
    "for fd in range(3, 1024):\\n"
    "    try:\\n"
    "        info = os.fstat(fd)\\n"
    "    except OSError:\\n"
    "        continue\\n"
    "    found.append([fd, info.st_dev, info.st_ino])\\n"
    "print(json.dumps(found))\\n"
)
listed = subprocess.run(
    [sys.executable, "-I", "-c", lister],
    capture_output=True, text=True, timeout=60, close_fds=False, check=True,
)
store = {}
for suffix in ("", "-wal", "-shm", ".lock"):
    info = os.stat(sys.argv[1] + suffix)
    store[suffix] = [info.st_dev, info.st_ino]
say(
    "listed",
    inherited=json.loads(listed.stdout),
    store=store,
    inheritable=os.get_inheritable(db.owner.lock_fd),
)
db.close()
say("closed")
"""


def test_exec_children_inherit_no_store_descriptor(tmp_path: Path, home: Path) -> None:
    target = tmp_path / "store.sqlite"
    with Child(EXEC_OWNER, str(target), home=home) as child:
        listed = child.expect("listed")
        child.expect("closed")
        assert child.wait() == 0
    inherited = {(dev, ino) for _, dev, ino in listed["inherited"]}
    store = {tuple(identity) for identity in listed["store"].values()}
    assert len(store) == 4 and not inherited & store
    assert listed["inheritable"] is False


FORK_OWNER = """
import errno
db = open_owned_database(sys.argv[1], mode="create")
put(db.connection(), "first")
lock_fd = db.owner.lock_fd
pid = os.fork()
if pid == 0:
    try:
        os.fstat(lock_fd)
        lock_state = "open"
    except OSError as error:
        lock_state = errno.errorcode[error.errno]
    try:
        db.connection()
        connection_state = "usable"
    except ownership.StorageForkedError:
        connection_state = "forked"
    say(
        "grandchild",
        lock_fd=lock_state,
        connection=connection_state,
        close=repr(db.close()),
        forked=db.owner.forked,
        registry=len(ownership._REGISTRY),
    )
    command()
    sys.exit(0)
say("forked", grandchild=pid)
command()
"""


def test_forked_child_keeps_no_lock_copy_and_is_poisoned(
    tmp_path: Path, home: Path
) -> None:
    target = tmp_path / "store.sqlite"
    with Child(FORK_OWNER, str(target), home=home) as child:
        events = {}
        for _ in range(2):
            event = child.next_event()
            events[event["event"]] = event
        grandchild = events["grandchild"]
        assert events["forked"]["grandchild"] == grandchild["pid"]
        assert {key: grandchild[key] for key in grandchild if key != "pid"} == {
            "event": "grandchild",
            "lock_fd": "EBADF",
            "connection": "forked",
            "close": "None",
            "forked": True,
            "registry": 0,
        }
        child.kill_child_only()
        assert group_exists(child.group)  # the forked grandchild is still alive
        db = open_owned_database(str(target), mode="open")  # it holds no lock copy
        db.close()
        child.close_stdin()
        child.wait_stdout_eof()


FIRST_OWNER = """
import signal
if sys.argv[2] == "unpinned":
    ownership._pin_forever = lambda values: None  # negative control only
db = open_owned_database(sys.argv[1], mode="create")
put(db.connection(), "first")
signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
pid = os.fork()
if pid == 0:
    say("waiting")
    signal.sigwait({signal.SIGUSR1})
    say("finalizing")
    sys.exit(0)  # ordinary interpreter finalization
db.close()
say("closed", grandchild=pid)
"""

SECOND_OWNER = """
db = open_owned_database(sys.argv[1], mode="open")
connection = db.connection()
for value in ("second-a", "second-b"):
    connection.execute("BEGIN IMMEDIATE")
    put(connection, value)
    connection.execute("COMMIT")
say("committed", wal=os.stat(sys.argv[1] + "-wal").st_size)
command()
"""


@pytest.mark.parametrize("variant", ["pinned", "unpinned"])
def test_forked_child_never_finalizes_an_inherited_connection(
    tmp_path: Path, home: Path, variant: str
) -> None:
    """A forked child outlives its owner's clean close and a later crashed owner.

    Finalizing the inherited connection would delete the later owner's WAL by
    name. ``unpinned`` disables the pin in the child as a negative control and
    must lose the crashed owner's committed rows, so the guard is reached.
    """

    target = tmp_path / "store.sqlite"
    with Child(FIRST_OWNER, str(target), variant, home=home) as first:
        events = {}
        for _ in range(2):
            event = first.next_event()
            events[event["event"]] = event
        grandchild = events["closed"]["grandchild"]
        assert events["waiting"]["pid"] == grandchild
        assert first.wait() == 0
        with Child(SECOND_OWNER, str(target), home=home) as second:
            assert second.expect("committed")["wal"] > 0
            second.kill_group()
        assert sidecar(target, "-wal").stat().st_size > 0
        os.kill(grandchild, signal.SIGUSR1)
        first.expect("finalizing")
        first.wait_stdout_eof()
    db = open_owned_database(str(target), mode="open")
    try:
        found = rows(db)
        assert db.connection().execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        db.close()
    if variant == "pinned":
        assert found == ["first", "second-a", "second-b"]
    else:
        assert found == ["first"]


def test_stale_or_garbage_lock_contents_never_decide(
    tmp_path: Path, home: Path
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    lock = sidecar(target, ".lock")
    contents = [
        b"",
        b"garbage\x00\xff",
        json.dumps({"pid": os.getppid()}).encode(),
        json.dumps({"pid": os.getpid()}).encode(),
        b"[" * 3000,
        b"x" * 100_000,
    ]
    for content in contents:
        lock.write_bytes(content)
        db = open_owned_database(str(target), mode="open")
        try:
            assert json.loads(lock.read_text())["pid"] == os.getpid()
        finally:
            db.close()
    with Child(OWNER, str(target), "open", home=home) as child:
        child.expect("owned")
        for garbage in (b"\xff not json", b"[" * 3000, b'{"pid": true}'):
            lock.write_bytes(garbage)
            with pytest.raises(StorageOwnershipError) as refused:
                open_owned_database(str(target), mode="open")
            assert "lock diagnostic" not in str(refused.value)
        lock.write_bytes(json.dumps({"pid": 4242}).encode())
        with pytest.raises(StorageOwnershipError, match="pid 4242"):
            open_owned_database(str(target), mode="open")
        child.send("close")
        child.expect("closed")
        assert child.wait() == 0


# The contract boundary ---------------------------------------------------------

BOUNDARY_FILES = (
    "src/pipy_harness/native/durable/sqlite_connection.py",
    "docs/harness-spec.md",
    "docs/session-storage.md",
    "docs/specs/2026-10-19-durable-execution-evidence.md",
    "CHANGELOG.md",
)


def _prose(text: str) -> str:
    """Words only: no Markdown or reST markup, line breaks or indentation."""

    return " ".join(text.replace("`", "").replace("*", "").replace(">", " ").split())


def test_the_contract_boundary_reads_the_same_everywhere_it_is_stated() -> None:
    """The boundary in ``ownership``'s docstring appears word for word."""

    doc = _prose(ownership.__doc__ or "")
    start = doc.index("Contract boundary.")
    end = doc.index("than the reference.", start) + len("than the reference.")
    boundary = doc[start:end]
    assert "While any owner holds a store" in boundary
    root = Path(__file__).resolve().parents[2]
    for name in BOUNDARY_FILES:
        assert boundary in _prose((root / name).read_text(encoding="utf-8")), name


# Best-effort detection (outside the contract) ----------------------------------
#
# Renaming, replacing, hard-linking or deleting store files while any owner
# holds the store, from the start of its open or create until its close has
# completed, is outside the contract (see the contract boundary in
# ownership.py). These tests pin the best-effort checks the code keeps for such
# changes: the in-process registry, the identity checks under the lock, after
# connecting and after configuration, parking, claims that never move, and the
# re-check before each unlink of a failed create. They describe detection, not
# guarantees.


def test_an_owned_database_under_another_name_is_refused_unread(
    tmp_path: Path, home: Path
) -> None:
    """Renaming a held store's database is outside the contract (round 5).

    Renamed while owned, the database keeps its inode and one link but gets a
    new lock name. Within this process the registry still knows its inode, so
    this open is refused before any descriptor opens it and the owner's locks
    survive. Across processes nothing holds: another process takes the new
    lock name and owns the same file.
    """

    first, second = tmp_path / "first.sqlite", tmp_path / "second.sqlite"
    db = owned_with_a_row(first)
    try:
        os.rename(first, second)
        assert external_write(second, home) == "database is locked"
        with pytest.raises(StorageOwnershipError, match="already open in this process"):
            open_owned_database(str(second), mode="open")
        assert not sidecar(second, ".lock").exists()
        assert external_write(second, home) == "database is locked"
        assert db.owner._held.parked == []  # refused unread
        put(db.connection(), "second")
    finally:
        db.close()
    os.rename(second, first)
    assert_rows_after_close(first, ["first", "second"])


def _three_stores(tmp_path: Path) -> tuple[Path, Path, Path]:
    """X under ``a.sqlite``, Y under ``y.sqlite`` and an unrelated other store."""

    a_path, y_path, other = (tmp_path / n for n in ("a.sqlite", "y.sqlite", "o.sqlite"))
    make_store(a_path, "x1")
    make_store(y_path, "y1")
    make_store(other, "o1")
    return a_path, y_path, other


def _park_x_on_a(
    a_path: Path, other: Path, monkeypatch: pytest.MonkeyPatch, parked: list[int]
) -> None:
    """A racing open of ``other`` reaches X, renamed there as it opens: parked on A."""

    real_os_open = os.open

    def rename_then_open(name: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if name == other.name:
            os.rename(a_path, other)  # X now sits under the other store's name
            monkeypatch.setattr(os, "open", real_os_open)
        return real_os_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", rename_then_open)
    with pytest.raises(StorageOwnershipError, match="already open in this process"):
        open_owned_database(str(other), mode="open")
    assert os.open is real_os_open
    parked.append(1)


@pytest.mark.parametrize("change", ["journal", "garbage", "replaced"])
def test_a_change_after_the_precheck_is_refused_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A journal, garbage written in place, or a valid store under the name.

    The replacement is refused although it is a valid WAL store: the lock
    claimed the database inode it found, and a claim never moves to another.
    """

    target = tmp_path / "store.sqlite"
    make_store(target, "kept")
    journal = sidecar(target, "-journal")
    garbage = b"garbage!" * 64
    replacement = tmp_path / "replacement.sqlite"
    make_store(replacement, "other")
    replaced = replacement.read_bytes()

    def change_now() -> None:
        if change == "journal":
            journal.write_bytes(b"\x01" * 512)
            journal.chmod(0o600)
        elif change == "garbage":
            target.write_bytes(garbage)  # the same inode, rewritten
        else:
            os.replace(replacement, target)

    monkeypatch.setattr(ownership, "_after_lock", change_now)
    error: type[Exception]
    if change == "journal":
        error, message = UnsupportedStoragePath, "rollback journal"
    elif change == "garbage":
        error, message = StorageConfigurationError, "not an SQLite database"
    else:
        error, message = UnsupportedStoragePath, "changed while opening"
    with pytest.raises(error, match=message):
        open_owned_database(str(target), mode="open")
    monkeypatch.undo()
    if change == "journal":
        assert journal.read_bytes() == b"\x01" * 512
    else:
        assert target.read_bytes() == (garbage if change == "garbage" else replaced)
    fd = os.open(sidecar(target, ".lock"), os.O_RDWR | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the lock was freed
    finally:
        os.close(fd)


def test_lock_file_replaced_while_locking_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target)
    lock = sidecar(target, ".lock")
    old = tmp_path / "old.lock"

    def replace_lock() -> None:
        os.rename(lock, old)
        lock.write_bytes(b"")
        lock.chmod(0o600)

    monkeypatch.setattr(ownership, "_after_lock", replace_lock)
    with pytest.raises(
        StorageOwnershipError, match="replaced while it was being locked"
    ):
        open_owned_database(str(target), mode="open")
    monkeypatch.undo()
    fd = os.open(old, os.O_RDWR | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # nobody holds the old inode
    finally:
        os.close(fd)
    db = open_owned_database(str(target), mode="open")
    db.close()


@pytest.mark.parametrize("open_mode", ["create", "open"])
def test_database_swapped_before_connect_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, open_mode: str
) -> None:
    target = tmp_path / "store.sqlite"
    if open_mode == "open":
        make_store(target, "original")
    impostor = tmp_path / "impostor.sqlite"
    raw = sqlite3.connect(impostor)
    try:
        raw.execute("CREATE TABLE f(x)")
        raw.commit()
    finally:
        raw.close()
    impostor.chmod(0o600)
    original = impostor.read_bytes()
    real_connect = sqlite3.connect

    def swapping(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        os.replace(impostor, target)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", swapping)
    with pytest.raises(UnsupportedStoragePath, match="changed while opening"):
        open_owned_database(str(target), mode=open_mode)
    monkeypatch.undo()
    # The swapped-in file was never written (no WAL header, no sidecars), and a
    # failed create removes only the database it made, not the impostor.
    assert target.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "store.sqlite",
        "store.sqlite.lock",
    ]


def test_database_replaced_while_opening_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "store.sqlite"
    make_store(target, "original")
    impostor = tmp_path / "impostor.sqlite"
    impostor.write_bytes(target.read_bytes())
    impostor.chmod(0o600)
    real = sqlite_connection._configure

    def swapping(
        connection: sqlite3.Connection, owner: object, durability: str
    ) -> None:
        real(connection, owner, durability)  # type: ignore[arg-type]
        os.replace(impostor, target)

    monkeypatch.setattr(sqlite_connection, "_configure", swapping)
    with pytest.raises(UnsupportedStoragePath, match="changed while opening"):
        open_owned_database(str(target), mode="open")
    monkeypatch.undo()
    db = open_owned_database(str(target), mode="open")
    db.close()


@pytest.mark.parametrize("opener", ["open_owned_database", "acquire_path"])
def test_an_owned_database_moved_in_after_the_lock_is_refused_unread(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, opener: str
) -> None:
    """The under-lock path claims the database inode before opening it.

    ``acquire_path`` has no format check, so no descriptor would ever show the
    inode: only the claim refuses it.
    """

    owned, other = tmp_path / "owned.sqlite", tmp_path / "other.sqlite"
    make_store(other, "other")
    db = owned_with_a_row(owned)
    try:

        def move_owned_in() -> None:
            os.rename(owned, other)

        monkeypatch.setattr(ownership, "_after_lock", move_owned_in)
        with pytest.raises(StorageOwnershipError, match="already open in this process"):
            if opener == "acquire_path":
                ownership.acquire_path(str(other), "open")
            else:
                open_owned_database(str(other), mode="open")
        monkeypatch.undo()
        assert external_write(other, home) == "database is locked"
        assert db.owner._held.parked == []  # refused unread
        put(db.connection(), "second")
    finally:
        db.close()
    os.rename(other, owned)
    assert_rows_after_close(owned, ["first", "second"])


@pytest.mark.parametrize("moment", ["gate", "under-lock", "normalize", "lock-file"])
def test_an_owned_database_renamed_under_a_name_being_opened_keeps_its_locks(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, moment: str
) -> None:
    """A rename racing an open can put an owned database under the opened name.

    Every descriptor of a store file is identified before anything else; one
    that reached an owned database is parked on its owner, and closed only
    after the owner's connection, instead of dropping the owner's locks.
    """

    owned, other = tmp_path / "owned.sqlite", tmp_path / "other.sqlite"
    make_store(other, "other")
    if moment == "normalize":
        other.chmod(0o644)  # so the owner-only normalization opens it
    name = other.name + (".lock" if moment == "lock-file" else "")
    racing = tmp_path / name
    # The gate, the re-check under the lock and the normalization open the
    # database in that order; the lock file is opened once.
    wanted = {"gate": 1, "under-lock": 2, "normalize": 3, "lock-file": 1}[moment]
    calls = 0
    real_open = os.open

    def rename_then_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal calls
        if path == name:
            calls += 1
            if calls == wanted:
                os.rename(owned, racing)
        return real_open(path, flags, *args, **kwargs)

    db = owned_with_a_row(owned)
    try:
        monkeypatch.setattr(os, "open", rename_then_open)
        with pytest.raises(StorageOwnershipError, match="already open in this process"):
            open_owned_database(str(other), mode="open")
        monkeypatch.undo()
        assert calls == wanted
        assert external_write(racing, home) == "database is locked"
        parked = db.owner._held.parked
        assert len(parked) == 1
        put(db.connection(), "second")
    finally:
        monkeypatch.undo()
        db.close()
    assert parked == []  # closed once the owner's connection was closed
    os.rename(racing, owned)
    assert_rows_after_close(owned, ["first", "second"])


def test_a_claim_never_moves_so_its_parked_descriptor_cannot_drop_later_locks(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round-3 sequence: A claims X, a racing open parks an X descriptor on
    A, A's name is replaced by Y, a second owner opens X, and A closes.

    A claim never moves to another inode, so A's open is refused and its
    release closes the parked descriptor before X can be claimed again: the
    second owner keeps its locks and rows.
    """

    a_path, y_path, other = _three_stores(tmp_path)
    real_open_database = ownership._open_database
    parked: list[int] = []

    def racing(dir_fd: int, path: Any, check: Any, held: Any) -> Any:
        _park_x_on_a(a_path, other, monkeypatch, parked)
        assert len(held.parked) == 1
        os.rename(y_path, a_path)  # A's name now names Y
        return real_open_database(dir_fd, path, check, held)

    monkeypatch.setattr(ownership, "_open_database", racing)
    opened: list[OwnedDatabase] = []
    refusals: list[Exception] = []
    try:
        opened.append(open_owned_database(str(a_path), mode="open"))
    except UnsupportedStoragePath as error:
        refusals.append(error)
    monkeypatch.undo()
    second = open_owned_database(str(other), mode="open")  # X's next owner
    try:
        put(second.connection(), "x2")
        assert external_write(other, home) == "database is locked"
        for db in opened:
            db.close()  # an owner kept open on Y would close the X descriptor now
        assert external_write(other, home) == "database is locked"
        put(second.connection(), "x3")
    finally:
        second.close()
    assert_rows_after_close(other, ["x1", "x2", "x3"])
    assert_rows_after_close(a_path, ["y1"])
    assert parked == [1] and not opened
    assert "changed while opening" in str(refusals[0])


def test_a_replacement_seen_at_connect_releases_the_claim_after_its_parked_descriptor(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race once the claim is final: after the checks under the lock,
    a racing open parks an X descriptor on A and A's name is replaced by Y
    before SQLite opens it. The connection lands on Y and is refused right
    after connecting, before any SQL; A's release closes the parked descriptor
    in the registry lock hold that drops the claim, so X's next owner keeps
    its locks.
    """

    a_path, y_path, other = _three_stores(tmp_path)
    y_bytes = y_path.read_bytes()
    real_acquire = ownership.acquire
    parked: list[int] = []

    def racing(request: Any, *, check: Any = None) -> Any:
        owner = real_acquire(request, check=check)
        if request.name == a_path.name:
            _park_x_on_a(a_path, other, monkeypatch, parked)
            assert len(owner._held.parked) == 1
            os.rename(y_path, a_path)
        return owner

    monkeypatch.setattr(ownership, "acquire", racing)
    with pytest.raises(UnsupportedStoragePath, match="changed while opening"):
        open_owned_database(str(a_path), mode="open")
    monkeypatch.undo()
    assert parked == [1]
    assert a_path.read_bytes() == y_bytes  # Y was never written
    second = open_owned_database(str(other), mode="open")
    try:
        put(second.connection(), "x2")
        assert external_write(other, home) == "database is locked"
    finally:
        second.close()
    assert_rows_after_close(other, ["x1", "x2"])


def test_a_failed_create_whose_name_was_replaced_removes_nothing_by_that_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup after a failed create unlinks by name only while the name still
    names the database this create made; a foreign file now under a sidecar
    name survives, and the skipped cleanup is a note on the original error.
    """

    target = tmp_path / "store.sqlite"
    moved = tmp_path / "moved.sqlite"
    foreign = sidecar(target, "-journal")
    real = sqlite_connection._configure

    def configure_then_fail(
        connection: sqlite3.Connection, owner: object, durability: str
    ) -> None:
        real(connection, owner, durability)  # type: ignore[arg-type]
        os.rename(target, moved)
        foreign.write_bytes(b"foreign")
        raise RuntimeError("injected")

    monkeypatch.setattr(sqlite_connection, "_configure", configure_then_fail)
    with pytest.raises(RuntimeError, match="injected") as caught:
        open_owned_database(str(target), mode="create")
    monkeypatch.undo()
    assert foreign.read_bytes() == b"foreign"
    assert moved.exists()
    notes = getattr(caught.value, "__notes__", [])
    assert any("changed while open" in note for note in notes), notes


def test_a_failed_create_keeps_a_replacement_that_arrives_during_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort detection: the name is re-checked right before each unlink.

    A replacement that arrives while the cleanup of a failed create runs is
    kept and the skipped cleanup is a note (round-4 review: the database
    unlink after the sidecar loop was unconditional).
    """

    target = tmp_path / "store.sqlite"
    foreign = tmp_path / "foreign.sqlite"
    make_store(foreign, "foreign")
    foreign_bytes = foreign.read_bytes()
    real_configure, real_lstat = sqlite_connection._configure, ownership._lstat
    armed: list[bool] = []

    def lstat_then_replace(path: str, dir_fd: int | None = None) -> Any:
        if armed and path == target.name + "-wal":
            armed.clear()
            os.replace(foreign, target)  # arrives at the first sidecar lstat
        return real_lstat(path, dir_fd)

    def configure_then_fail(
        connection: sqlite3.Connection, owner: object, durability: str
    ) -> None:
        real_configure(connection, owner, durability)  # type: ignore[arg-type]
        armed.append(True)
        raise RuntimeError("injected")

    monkeypatch.setattr(ownership, "_lstat", lstat_then_replace)
    monkeypatch.setattr(sqlite_connection, "_configure", configure_then_fail)
    with pytest.raises(RuntimeError, match="injected") as caught:
        open_owned_database(str(target), mode="create")
    monkeypatch.undo()
    assert not armed  # the replacement did arrive during the cleanup
    assert target.read_bytes() == foreign_bytes
    notes = getattr(caught.value, "__notes__", [])
    assert any("changed while open" in note for note in notes), notes
