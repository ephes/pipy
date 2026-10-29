"""Real backend/Session witnesses for the private ordinary-live read query."""

import dataclasses
import threading

from task_migration_support import ObservedStorage, definition, sqlite_rows

from pipy_harness.native.durable.ordinary_ownership import OwnedLiveQuery
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage

DONE = {"status": "terminal", "outcome": {"status": "completed", "result": None}}
INVALID = "^Invalid ordinary ownership graph$"


class Rollback(Exception):
    """An intentional fixture callback rollback, never a backend error."""


def child(tx, owner, source=None):
    return tx.create_task(
        definition() if source is None else source,
        None,
        ownership={"kind": "task", "taskId": owner},
    )


def done(rig, target):
    rig.change(target, lambda row: row.update(state=DONE))


def query(rig, target):
    before = rig.silence()
    value = rig.session.commit(lambda tx: OwnedLiveQuery.capture(tx).has(target))
    rig.assert_silent(before)
    return value


def reopen(rig):
    """A real close/new SQLite owner and Session, with no scheduler recovery."""
    assert rig.backend == "sqlite"
    previous = rig.storage
    rig.session.close(timeout=5)
    assert previous.closed.is_set()
    rig.storage = ObservedStorage(open_sqlite_storage(str(rig.path), mode="open"))
    rig.session = Session(rig.storage)
    return previous


def published(rig):
    """Called on an already owned callback/line, without another Session ticket."""
    if rig.backend == "sqlite":
        return sqlite_rows(rig.path)
    with rig.storage.inner._lock:
        return rig.storage.inner._state


def staged_identity(tx):
    return (
        tuple(map(id, tx.staged_tasks())),
        tuple(map(id, tx.staged_conversations())),
        tuple(
            (
                key,
                id(entry),
                id(entry.committed),
                None if entry.write is None else entry.write[0],
                None if entry.write is None else id(entry.write[1]),
            )
            for key, entry in tx._core._tasks.items()
        ),
    )


class QueryWindow:
    """Arm AFTER intentional staging/minting, not before an outer mutation."""

    def __init__(self, rig, tx):
        self.state = published(rig)
        self.commits = len(rig.storage.commits)
        self.mints = tuple(rig.storage.mints)
        self.staged = staged_identity(tx)

    def unchanged(self, rig, tx):
        actual = published(rig)
        if rig.backend == "memory":
            assert actual is self.state
        else:
            assert actual == self.state
        assert len(rig.storage.commits) == self.commits
        assert tuple(rig.storage.mints) == self.mints
        assert staged_identity(tx) == self.staged


def assert_rollback(rig, before, commits, mints):
    after = published(rig)
    if rig.backend == "memory":
        # IDs minted by the surrounding callback are consumed, not rolled back.
        assert dataclasses.replace(after, next_id=before.next_id) == before
    else:
        assert after == before
    assert len(rig.storage.commits) == commits
    assert len(rig.storage.mints) > mints


class ReadAudit:
    """Class-forwarded exact-instance reads; no slotted instance patching."""

    def __init__(self, rig, monkeypatch):
        self.calls = []
        inner = rig.storage.inner

        def install(name):
            original = getattr(type(inner), name)

            def forward(actual, *args, **kwargs):
                value = original(actual, *args, **kwargs)
                if actual is inner:
                    self.calls.append(name)
                return value

            monkeypatch.setattr(type(inner), name, forward)

        for name in ("task", "conversation", "scan_tasks", "scan_conversations"):
            install(name)


class ScanHold:
    """Hold one real forwarded scan with explicit entered/release/retired cuts."""

    def __init__(self, rig, monkeypatch):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.retired = threading.Event()
        self.thread = None
        inner = rig.storage.inner
        original = type(inner).scan_tasks
        held = False

        def forward(actual, *args, **kwargs):
            nonlocal held
            value = original(actual, *args, **kwargs)
            if actual is inner and not held:
                held = True
                self.thread = threading.get_ident()
                self.entered.set()
                try:
                    assert self.release.wait(10)
                finally:
                    self.retired.set()
            return value

        monkeypatch.setattr(type(inner), "scan_tasks", forward)


def complete_task_pages(scans):
    pages = [page for page in scans.pages if page[0] == "scan_tasks"]
    assert pages[0][1] is None and pages[-1][2] is None
    assert any(page[1] is not None for page in pages)
    assert sum(len(page[3]) for page in pages) > 128


def page_evidence(pages):
    """Only the known GraphScans Cursor fields; actual scan objects stay intact."""

    def cursor(value):
        return None if value is None else {"after": value.after, "order": value.order}

    return [
        {
            "table": name,
            "cursor": cursor(before),
            "next": cursor(after),
            "ids": list(ids),
        }
        for name, before, after, ids in pages
    ]
