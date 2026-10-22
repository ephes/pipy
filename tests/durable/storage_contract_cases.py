"""Backend-agnostic Storage contract cases (DE03c1), run over every backend.

The cases are MemoryStorage's own acceptance tests, ``test_memory_storage.py``
(unchanged; it still runs on its own). ``test_memory_storage_body`` runs every
original body that uses only the public Storage API on Memory, SQLite and SQLite
reopened after every commit (Pi's ``ReopeningStorage``,
``sqlite-storage.test.ts`` lines 47-111): the backend's store factory replaces
the module's ``MemoryStorage`` name, through which each body builds its stores.
Four table bodies that observe Memory internals have variants here that make
the same observations through backend hooks, and one mixed body has its table
and close halves split out. A few cases are added for the places where Pi's
SQLite backend differs from Memory (D2) and for exact-type ingress. The runner
``test_sqlite_storage.py`` collects everything through ``cases()`` and checks
``ORIGINALS`` against the original file.

DE03c2 removes the 54 strict document xfails and ports the seven bodies
that observe Memory internals through the backend views below. Every bounded
raw-base contract case now runs over all three factories; one-instance thread
contention cases exclude only the wrapper that deliberately replaces its store.

The ``backend`` fixture makes stores and closes them after the case; for SQLite
it then checks every closed file: ``integrity_check``, owner-only modes and no
leftover journal. The hooks mean the same on every backend: ``state`` (the
complete stored state, compared for "nothing changed"), ``at_commit`` (a
callback at the last point of a commit, after every write and before anything
is published: Memory's final candidate validation, SQLite's ``_before_commit``
seam inside the transaction), ``set_next_seq``, ``locked`` and
``replace_authority``. ``ONE_INSTANCE`` cases leave out the reopening wrapper,
which replaces its instance between calls, so threads would race the wrapper
instead of one store's authority.
"""

from __future__ import annotations

import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_memory_storage as originals
from test_memory_storage import (
    SESSION,
    WitnessLock,
    _foreign_point,
    address,
    change_document,
    commit,
    create_document,
    document_query,
    entry,
    ids,
    operations,
    submission,
    task,
)

from pipy_harness.native.durable import memory, sqlite
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import SAFE_INTEGER, RecordError
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    Cursor,
    EntryQuery,
    StorageClosedError,
    StorageRequestError,
    StorageWrite,
    SubmissionQuery,
    TaskQuery,
    UnsupportedStorageOperation,
)

BACKENDS = ("memory", "sqlite", "reopen")
ALL = pytest.mark.parametrize("backend", BACKENDS, indirect=True)
ONE_INSTANCE = pytest.mark.parametrize("backend", ["memory", "sqlite"], indirect=True)
_TABLES = (
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


class _MemoryView:
    """A Memory commit candidate, read the way the SQLite view reads its rows."""

    def __init__(self, state: Any) -> None:
        self._state = state

    def entry_ids(self) -> set[int]:
        return set(self._state.tables["entry"])

    def entry(self, id: int) -> dict[str, Any]:
        return self._state.tables["entry"][id].to_tree()

    def task(self, id: int) -> Any:
        return self._state.tables["task"][id].to_tree()

    def owners(self, task: int) -> tuple[int, ...]:
        return self._state.owners.get(("taskId", task), ())

    def task_status(self, status: str) -> tuple[int, ...]:
        return self._state.statuses.get(("task", status), ())

    def entries(self, conversation: int) -> tuple[int, ...]:
        return self._state.entries.get(conversation, ())

    def scope(self) -> tuple[int, ...]:
        return self._state.scopes.get(("session",), ())

    def document(self, id: int) -> Any:
        return self._state.documents[id].to_tree()

    def document_record(self, id: int) -> Any:
        return self.document(id)["record"]

    def winner(self, conversation: int, request: str) -> int | None:
        return self._state.requests.get((conversation, memory._units(request)))


class _SqliteView:
    """The rows of an open SQLite commit transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _ids(self, sql: str, *params: object) -> tuple[int, ...]:
        return tuple(id for (id,) in self._connection.execute(sql, params))

    def entry_ids(self) -> set[int]:
        return set(self._ids("SELECT id FROM entries"))

    def entry(self, id: int) -> Any:
        sql = "SELECT record FROM entries WHERE id = ?"
        return sqlite._decode(self._connection.execute(sql, (id,)).fetchone()[0])

    def task(self, id: int) -> Any:
        row = self._connection.execute(
            "SELECT record FROM tasks WHERE id = ?", (id,)
        ).fetchone()
        return sqlite._decode(row[0])

    def owners(self, task: int) -> tuple[int, ...]:
        return self._ids(
            "SELECT id FROM conversations WHERE owner_task_id = ? ORDER BY id", task
        )

    def task_status(self, status: str) -> tuple[int, ...]:
        return self._ids("SELECT id FROM tasks WHERE status = ? ORDER BY id", status)

    def entries(self, conversation: int) -> tuple[int, ...]:
        return self._ids(
            "SELECT id FROM entries WHERE conversation_id = ? ORDER BY id", conversation
        )

    def scope(self) -> tuple[int, ...]:
        return self._ids(
            "SELECT id FROM documents WHERE scope_kind = 'session' AND owner_id = 0 ORDER BY id"
        )

    def document_record(self, id: int) -> Any:
        row = self._connection.execute(
            "SELECT record FROM documents WHERE id = ?", (id,)
        ).fetchone()
        return sqlite._decode(row[0])

    def document(self, id: int) -> Any:
        record = self.document_record(id)
        row = self._connection.execute(
            "SELECT version, content FROM document_revisions WHERE document_id = ? ORDER BY seq DESC LIMIT 1",
            (id,),
        ).fetchone()
        return {
            "record": record,
            "version": sqlite._decode(row[0]),
            "value": sqlite._decode(row[1]),
            "deltasSinceBase": 0,
        }

    def winner(self, conversation: int, request: str) -> int | None:
        row = self._connection.execute(
            "SELECT submission_id FROM submission_requests"
            " WHERE conversation_id = ? AND request_id = ?",
            (conversation, sqlite._identity(request)),
        ).fetchone()
        return None if row is None else row[0]


class Reopening:
    """Pi's ReopeningStorage: the store is closed and opened after every commit."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._closed = False
        self.current = open_sqlite_storage(path, mode="open")

    def commit(self, writes: Any) -> Any:
        if self._closed:
            return self.current.commit(writes)
        try:
            return self.current.commit(writes)
        finally:
            self.current.close()
            self.current = open_sqlite_storage(self._path, mode="open")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.current.close()

    # Each call reaches the instance that is current when it is made, as in Pi.
    def mint_id(self) -> Any:
        return self.current.mint_id()

    def conversation(self, *args: Any) -> Any:
        return self.current.conversation(*args)

    def scan_conversations(self, *args: Any) -> Any:
        return self.current.scan_conversations(*args)

    def entry(self, *args: Any, **kwargs: Any) -> Any:
        return self.current.entry(*args, **kwargs)

    def find_latest_head_marker(self, *args: Any) -> Any:
        return self.current.find_latest_head_marker(*args)

    def scan_entries(self, *args: Any) -> Any:
        return self.current.scan_entries(*args)

    def task(self, *args: Any) -> Any:
        return self.current.task(*args)

    def scan_tasks(self, *args: Any) -> Any:
        return self.current.scan_tasks(*args)

    def submission(self, *args: Any) -> Any:
        return self.current.submission(*args)

    def scan_submissions(self, *args: Any) -> Any:
        return self.current.scan_submissions(*args)

    def submission_by_request(self, *args: Any) -> Any:
        return self.current.submission_by_request(*args)

    def find_document(self, *args: Any) -> Any:
        return self.current.find_document(*args)

    def document(self, *args: Any) -> Any:
        return self.current.document(*args)

    def scan_documents(self, *args: Any) -> Any:
        return self.current.scan_documents(*args)

    @property
    def _lock(self) -> Any:
        return self.current._lock


class MemoryBackend:
    name = "memory"

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self._validate = memory._validate_document_candidate
        self.made = 0

    def new(self) -> Any:
        self.made += 1
        return MemoryStorage()

    def at_commit(self, callback: Any) -> None:
        original = self._validate

        def hook(candidate: Any, counts: Any) -> Any:
            if callback is not None:
                callback(_MemoryView(candidate))
            return original(candidate, counts)

        self._monkeypatch.setattr(memory, "_validate_document_candidate", hook)

    def view(self, store: Any) -> Any:
        return _MemoryView(store._state)

    def state(self, store: Any) -> Any:
        return store._state

    def set_next_seq(self, store: Any, value: int) -> None:
        store._state = replace(store._state, next_seq=value)

    def locked(self, store: Any) -> bool:
        return store._lock.locked()

    def replace_authority(self, store: Any, lock: Any) -> None:
        store._lock = lock

    def finish(self) -> None:
        pass


class SqliteBackend:
    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reopen: bool
    ) -> None:
        self.name = "reopen" if reopen else "sqlite"
        self._monkeypatch = monkeypatch
        self._reopen = reopen
        self._root = tmp_path / "stores"
        self._paths: list[Path] = []
        self._stores: list[Any] = []

    @property
    def made(self) -> int:
        return len(self._stores)

    def new(self) -> Any:
        path = self._root / f"store-{len(self._paths)}.sqlite"
        self._paths.append(path)
        if self._reopen:
            open_sqlite_storage(str(path), mode="create").close()
            store: Any = Reopening(str(path))
        else:
            store = open_sqlite_storage(str(path), mode="create")
        self._stores.append(store)
        return store

    def _inner(self, store: Any) -> Any:
        return store.current if isinstance(store, Reopening) else store

    def _connection(self, store: Any) -> sqlite3.Connection:
        return self._inner(store)._database.connection()

    def at_commit(self, callback: Any) -> None:
        def hook(connection: sqlite3.Connection) -> None:
            if callback is not None:
                callback(_SqliteView(connection))

        self._monkeypatch.setattr(sqlite, "_before_commit", hook)

    def view(self, store: Any) -> Any:
        return _SqliteView(self._connection(store))

    def state(self, store: Any) -> Any:
        connection = self._connection(store)
        return {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1, 2").fetchall()
            for name in _TABLES
        }

    def set_next_seq(self, store: Any, value: int) -> None:
        sql = "UPDATE durable_metadata SET next_seq = ? WHERE singleton = 1"
        self._connection(store).execute(sql, (value,))

    def locked(self, store: Any) -> bool:
        return self._inner(store)._lock.locked()

    def replace_authority(self, store: Any, lock: Any) -> None:
        store._lock = lock

    def finish(self) -> None:
        """Close every store, then check its closed files (no store open here)."""

        for store in self._stores:
            store.close()
        for path in self._paths:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(f"{path}.lock").stat().st_mode) == 0o600
            for suffix in ("-wal", "-shm", "-journal"):
                assert not Path(f"{path}{suffix}").exists(), suffix
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                found = connection.execute("PRAGMA integrity_check").fetchall()
            finally:
                connection.close()
            assert found == [("ok",)], (path, found)


@pytest.fixture
def backend(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    kind = request.param
    made: Any = (
        MemoryBackend(tmp_path, monkeypatch)
        if kind == "memory"
        else SqliteBackend(tmp_path, monkeypatch, reopen=kind == "reopen")
    )
    yield made
    made.finish()


def root(backend: Any) -> Any:
    store = backend.new()
    assert commit(store, {"type": "conversation", "value": {"id": 1}}) == 1
    return store


# The original bodies over every backend ------------------------------------------

TABLE_BODIES = (
    "test_root_global_namespace_mint_seq_empty_and_exhaustion",
    "test_replacement_and_last_write_request_winner_not_largest_id",
    "test_ancestry_segment_order_caps_marker_and_owner_edges",
    "test_ascending_reverses_segments_not_global_ids",
    "test_table_cursors_keep_order_position_after_new_commit",
    "test_full_retained_and_returned_ownership_and_utf16_index_identity",
    "test_request_winner_removal_has_no_fallback_and_legacy_cursor_uses_table_default",
    "test_all_table_exact_and_page_results_retain_independent_owned_snapshots",
    "test_raw_local_reads_return_before_unneeded_parent_access",
    "test_ancestry_minimum_cap_stops_before_following_unneeded_parent",
    "test_raw_reads_still_access_parent_when_source_requires_it",
    "test_pinned_raw_scan_limit_boundary_preserves_pages_and_python_failures",
    # Closed stores refuse before any document capability is consulted.
    "test_closed_document_ingress_refuses_before_foreign_admission",
)
DOCUMENT_BODIES = (
    # Its table and close half also runs as the variants below.
    "test_unsupported_document_commands_refuse_whole_batch_and_all_method_close",
    "test_document_current_base_replaces_value_version_and_keeps_old_snapshots",
    "test_document_retired_and_global_cross_kind_ids_never_recycle",
    "test_document_scope_address_utf16_and_singleton_empty_family_identity",
    "test_document_cursor_ignores_order_and_always_resumes_ascending",
    "test_document_raw_page_limit_boundary_keeps_source_cut",
    "test_document_live_aliases_inputs_pages_records_materializations_and_old_snapshots",
    "test_document_point_exact_builtin_controls_keep_numeric_history_unsupported",
    "test_raw_document_content_version_is_separate_from_point_and_definition_domain",
)


def _expansions(name: str) -> list[tuple[str, dict[str, Any]]]:
    """An original's parameter sets, expanded from its parametrize marks."""

    found: list[tuple[str, dict[str, Any]]] = [(name, {})]
    for mark in getattr(getattr(originals, name), "pytestmark", []):
        if mark.name != "parametrize":
            continue
        names = [part.strip() for part in mark.args[0].split(",")]
        found = [
            (
                f"{label}-{'-'.join(map(str, values))}",
                {**kwargs, **dict(zip(names, values, strict=True))},
            )
            for label, kwargs in found
            for value in mark.args[1]
            for values in [value if len(names) > 1 else (value,)]
        ]
    return found


_BODIES = [
    pytest.param(
        kind,
        (name, kwargs),
        id=f"{kind}-{label}",
    )
    for documents, names in ((False, TABLE_BODIES), (True, DOCUMENT_BODIES))
    for name in names
    for label, kwargs in _expansions(name)
    for kind in BACKENDS
]


@pytest.mark.parametrize(("backend", "body"), _BODIES, indirect=["backend"])
def test_memory_storage_body(backend, body, monkeypatch):
    name, kwargs = body
    monkeypatch.setattr(originals, "MemoryStorage", backend.new)
    getattr(originals, name)(**kwargs)
    assert backend.made, "the body must build its stores through the backend"


# Variants of the bodies that observe Memory internals ------------------------------


@ALL
def test_supported_last_failure_does_not_leak_any_table_index_or_counter(backend):
    store = root(backend)
    commit(store, entry(2), task(3), submission(4))
    before = backend.state(store)
    with pytest.raises(RuntimeError, match="written more than once"):
        commit(
            store,
            {
                "type": "conversation",
                "value": {"id": 10, "owner": {"conversationId": 1, "taskId": 3}},
            },
            task(3, "running"),
            submission(4, "changed"),
            entry(11),
            entry(11),
        )
    assert backend.state(store) == before
    assert store.conversation(10) is None and store.entry(11) is None
    assert (
        ids(store.scan_conversations(ConversationQuery({"ownerTaskId": 3}), 10)) == []
    )
    assert ids(store.scan_tasks(TaskQuery({"status": "pending"}), 10)) == [3]
    assert store.submission_by_request(1, "changed") is None
    assert store.submission_by_request(1, "x").id == 4
    assert store.mint_id() == 5
    assert store.commit(()) == 3


@ALL
def test_late_candidate_failure_is_reached_after_preparation_and_is_atomic(backend):
    store = root(backend)
    before = backend.state(store)
    observed = []

    def failure(candidate):
        assert candidate.entry(100)["data"] == {"x": [None]}
        assert candidate.winner(1, "x") == 102
        observed.append(candidate)
        raise RuntimeError("injected preparation fault")

    backend.at_commit(failure)
    with pytest.raises(RuntimeError, match="injected preparation fault"):
        commit(store, entry(100, data={"x": [None]}), task(101), submission(102))
    assert len(observed) == 1 and backend.state(store) == before
    assert store.entry(100) is None and store.task(101) is None
    assert store.submission_by_request(1, "x") is None
    backend.at_commit(None)
    assert store.mint_id() == 2 and store.commit(()) == 2


@ALL
def test_unsupported_document_commands_refuse_whole_batch(backend):
    store = root(backend)
    with pytest.raises(UnsupportedStorageOperation):
        commit(
            store,
            entry(2),
            {
                "type": "document.change",
                "id": 3,
                "content": {"kind": "delta", "version": 1, "ops": []},
            },
        )
    assert store.entry(2) is None and store.mint_id() == 2


@ALL
def test_close_is_idempotent_and_seals_every_method(backend):
    store = root(backend)
    store.close()
    store.close()
    for operation in operations(store):
        with pytest.raises(StorageClosedError):
            operation()
    with pytest.raises(StorageClosedError):
        commit(store, entry(100), create_document(101))


def _run_contended(backend, store, writes, check, contender, verify, after=None):
    """The originals' two-executor barrier around one commit's late point.

    ``contender`` gets the store's witnessed authority; ``after`` runs in the
    writer once the barrier is released, still inside its commit.
    """

    ready, release = threading.Event(), threading.Event()
    authority = WitnessLock()
    backend.replace_authority(store, authority)
    unowned: list[bool] = []

    def barrier(candidate):
        if threading.current_thread().name.startswith("writer"):
            check(candidate)
            # No contender runs yet: a free authority here means the writer's
            # late commit point runs outside it, whatever the contender does.
            if authority.lock.acquire(blocking=False):
                authority.lock.release()
                unowned.append(True)
            ready.set()
            assert release.wait(5)
            if after is not None:
                after()

    backend.at_commit(barrier)
    first = ThreadPoolExecutor(1, thread_name_prefix="writer")
    second = ThreadPoolExecutor(1, thread_name_prefix="contender")
    try:
        writer = first.submit(commit, store, *writes)
        writer.add_done_callback(lambda _: ready.set())
        assert ready.wait(5)
        if writer.done():
            writer.result()  # the writer's own failure, if it raised early
            raise AssertionError("the writer finished before the late commit point")
        assert not unowned, "the writer's late commit point is outside the authority"
        follower = second.submit(contender, authority)
        assert authority.attempt.wait(5)
        assert authority.lock.locked()  # the writer still owns the common authority
        release.set()
        verify(writer, follower)
    finally:
        release.set()
        first.shutdown(wait=True)
        second.shutdown(wait=True)
    assert not [
        t for t in threading.enumerate() if t.name.startswith(("writer_", "contender_"))
    ]


@ONE_INSTANCE
@pytest.mark.parametrize("contender", ["write", "read", "mint", "close"])
def test_one_authority_linearizes_real_contended_operations(backend, contender):
    store = root(backend)
    choices = {
        "write": lambda _: commit(store, submission(103)),
        "read": lambda _: ids(
            store.scan_entries(EntryQuery({"conversationId": 1}), 10)
        ),
        "mint": lambda _: store.mint_id(),
        "close": lambda _: store.close(),
    }

    def check(candidate):
        assert candidate.entry_ids() == {100, 101}

    def verify(writer, follower):
        assert writer.result(5) == 2
        result = follower.result(5)
        if contender == "write":
            assert result == 3 and store.submission(103).id == 103 and store.entry(100)
        elif contender == "read":
            assert result == [101, 100]
        elif contender == "mint":
            assert result == 103
        else:
            with pytest.raises(StorageClosedError):
                store.commit(())

    writes = (entry(100), entry(101), task(102))
    _run_contended(backend, store, writes, check, choices[contender], verify)


@ALL
def test_sequence_exhaustion_refuses_without_corrupting_state(backend):
    store = root(backend)
    backend.set_next_seq(store, SAFE_INTEGER)
    assert store.commit(()) == SAFE_INTEGER
    before = backend.state(store)
    with pytest.raises(RuntimeError, match="sequence"):
        store.commit(())
    assert backend.state(store) == before and store.conversation(1).id == 1


# Every original test function: run as a body above, by a variant here, or
# represented by the variants and views below.
VARIANTS = {
    "test_supported_last_failure_does_not_leak_any_table_index_or_counter": (
        "test_supported_last_failure_does_not_leak_any_table_index_or_counter",
    ),
    "test_candidate_index_failure_is_reached_after_preparation_and_is_atomic": (
        "test_late_candidate_failure_is_reached_after_preparation_and_is_atomic",
    ),
    "test_one_authority_linearizes_real_contended_operations": (
        "test_one_authority_linearizes_real_contended_operations",
    ),
    "test_sequence_exhaustion_refuses_without_corrupting_state": (
        "test_sequence_exhaustion_refuses_without_corrupting_state",
    ),
}
# Also run as a document body: these carry its table and close halves everywhere.
SPLIT = {
    "test_unsupported_document_commands_refuse_whole_batch_and_all_method_close": (
        "test_unsupported_document_commands_refuse_whole_batch",
        "test_close_is_idempotent_and_seals_every_method",
    ),
}
VARIANTS.update(
    {
        name: (name,)
        for name in (
            "test_document_per_id_grouping_and_final_address_cardinality",
            "test_document_supported_failures_are_atomic_and_ordinary_errors",
            "test_document_late_candidate_failure_rolls_back_tables_docs_indexes_and_counters",
            "test_mixed_document_candidate_uses_same_contended_authority",
            "test_unsupported_document_batch_does_not_publish_supported_mixed_prefix",
            "test_document_point_ingress_rejects_foreign_without_any_hook",
            "test_document_point_reentry_child_is_bounded_and_never_runs_hook",
        )
    }
)
DE03C2_PORTS: tuple[str, ...] = ()
DE03C2_XFAILS: tuple[str, ...] = ()
ORIGINALS = (*TABLE_BODIES, *DOCUMENT_BODIES, *VARIANTS, *DE03C2_PORTS)


# Added: Memory semantics where Pi's SQLite backend differs (D2), and ingress ------


@ALL
@pytest.mark.parametrize("kind", ["conversation", "entry", "task", "submission"])
def test_keyset_scans_reach_the_largest_safe_id_in_both_orders(backend, kind):
    """D2 (c): Pi SQLite starts descending scans below 2**53 - 1 and skips it."""

    store = root(backend)
    build = {
        "conversation": lambda id: {"type": "conversation", "value": {"id": id}},
        "entry": entry,
        "task": task,
        "submission": lambda id: submission(id, request=str(id)),
    }[kind]
    commit(store, build(2), build(SAFE_INTEGER))
    expected = [SAFE_INTEGER, 2, 1] if kind == "conversation" else [SAFE_INTEGER, 2]
    scan, query = {
        "conversation": (store.scan_conversations, ConversationQuery),
        "entry": (store.scan_entries, EntryQuery),
        "task": (store.scan_tasks, TaskQuery),
        "submission": (store.scan_submissions, SubmissionQuery),
    }[kind]
    base = {"conversationId": 1} if kind == "entry" else {}
    for order, rows in (("descending", expected), ("ascending", expected[::-1])):
        assert ids(scan(query({**base, "order": order}), 10)) == rows
        first = scan(query({**base, "order": order}), 1)
        assert ids(first) == rows[:1]
        assert ids(scan(query(base), 10, first.next)) == rows[1:]


@ALL
@pytest.mark.parametrize("kind", ["conversation", "entry", "task", "submission"])
def test_page_sizes_beyond_sqlite_integers_read_every_row(backend, kind):
    store = root(backend)
    commit(store, entry(2), task(3), submission(4))
    scan, query = {
        "conversation": (store.scan_conversations, ConversationQuery({})),
        "entry": (store.scan_entries, EntryQuery({"conversationId": 1})),
        "task": (store.scan_tasks, TaskQuery({})),
        "submission": (store.scan_submissions, SubmissionQuery({})),
    }[kind]
    page = scan(query, 2**64)
    assert len(page.items) == 1 and page.next is None


@ALL
@pytest.mark.parametrize("limit", [-1, 1])
def test_entry_scan_walks_an_empty_segment_into_a_dangling_parent(backend, limit):
    """D2 (d), (f): Memory appends before it checks, so an empty segment is passed."""

    store = backend.new()
    commit(
        store,
        {"type": "conversation", "value": {"id": 1}},
        {
            "type": "conversation",
            "value": {"id": 5, "parent": {"conversationId": 999, "at": 10}},
        },
    )
    with pytest.raises(KeyError, match="999"):
        store.scan_entries(EntryQuery({"conversationId": 5}), limit)


@ALL
def test_a_dangling_parents_own_entries_are_read_before_its_record(backend):
    """D2 (f): Memory reads a segment's entries before it needs its record."""

    store = backend.new()
    commit(
        store,
        {"type": "conversation", "value": {"id": 1}},
        {
            "type": "conversation",
            "value": {"id": 2, "parent": {"conversationId": 999, "at": 10}},
        },
        entry(3, 2),
        entry(5, 999, head=5),  # raw storage holds entries of any conversation ID
    )
    page = store.scan_entries(EntryQuery({"conversationId": 2}), 1)
    assert ids(page) == [3] and page.next == Cursor(3, "descending")
    assert store.find_latest_head_marker(2).id == 5
    with pytest.raises(KeyError, match="999"):
        store.scan_entries(EntryQuery({"conversationId": 2}), 2)


@ALL
@pytest.mark.parametrize("kind", ["int-subclass", "foreign", "bool", "float"])
def test_table_ids_are_admitted_as_exact_builtins_before_any_hook(backend, kind):
    """Exact-type ingress: MemoryStorage's checks run first, on every backend."""

    store = root(backend)
    commit(store, entry(2, head=2), task(3), submission(4))
    calls: list[str] = []
    value = {
        "int-subclass": lambda: _foreign_point("int-subclass", calls),
        "foreign": lambda: _foreign_point("foreign", calls),
        "bool": lambda: True,
        "float": lambda: 1.0,
    }[kind]()
    calls.clear()
    before = backend.state(store)
    reads = (
        lambda: store.conversation(value),
        lambda: store.task(value),
        lambda: store.submission(value),
        lambda: store.entry(value),
        lambda: store.entry(2, conversation_id=value),
        lambda: store.find_latest_head_marker(value),
        lambda: store.find_latest_head_marker(1, value),
        lambda: store.submission_by_request(value, "x"),
    )
    for call in reads:
        with pytest.raises(RecordError):
            call()
    assert calls == [] and not backend.locked(store)
    assert backend.state(store) == before
    store.close()
    for call in reads:  # the closed refusal comes before any admission
        with pytest.raises(StorageClosedError):
            call()
    assert calls == [] and not backend.locked(store)


@ALL
def test_table_arguments_are_admitted_as_owned_values_before_any_hook(backend):
    store = root(backend)
    commit(store, entry(2), task(3), submission(4))
    calls: list[str] = []
    text = _foreign_point("str-subclass", calls)
    foreign = _foreign_point("foreign", calls)
    write = StorageWrite(entry(5))
    calls.clear()
    before = backend.state(store)
    refusals = {
        "Expected request ID string": [lambda: store.submission_by_request(1, text)],
        "Expected owned write tuple": [
            lambda: store.commit([write]),
            lambda: store.commit((write, foreign)),
            lambda: store.commit(foreign),
        ],
        "Expected typed query and integer page size": [
            lambda: store.scan_tasks(foreign, 1),
            lambda: store.scan_tasks(SubmissionQuery({}), 1),
            lambda: store.scan_entries(TaskQuery({}), 1),
            lambda: store.scan_conversations(ConversationQuery({}), True),
            lambda: store.scan_submissions(SubmissionQuery({}), foreign),
        ],
        "Invalid storage cursor": [
            lambda: store.scan_tasks(TaskQuery({}), 1, foreign),
            lambda: store.scan_entries(EntryQuery({"conversationId": 1}), 1, foreign),
        ],
    }
    for message, refused_calls in refusals.items():
        for call in refused_calls:
            with pytest.raises(StorageRequestError, match=message) as refused:
                call()
            assert type(refused.value) is StorageRequestError
    assert calls == [] and not backend.locked(store)
    assert backend.state(store) == before
    assert store.entry(5) is None and store.mint_id() == 5
    store.close()
    for refused_calls in refusals.values():
        for call in refused_calls:  # the closed refusal comes before any admission
            with pytest.raises(StorageClosedError):
                call()
    assert calls == [] and not backend.locked(store)


def cases() -> dict[str, Any]:
    """Every contract case and the ``backend`` fixture, for a runner module."""

    found = {
        name: value for name, value in globals().items() if name.startswith("test_")
    }
    return {"backend": backend, **found}


@ALL
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "operation", ["empty-new", "empty-occupied", "change-retire", "replace"]
)
def test_document_per_id_grouping_and_final_address_cardinality(
    backend, reverse, operation
):
    store = root(backend)
    if operation != "empty-new":
        commit(store, create_document(2, value={"old": True}))
    id = 3 if operation in {"empty-new", "empty-occupied", "replace"} else 2
    content = (
        change_document(2, {"new": True}, 2)
        if operation == "change-retire"
        else create_document(id)
    )
    retire = {"type": "document.retire", "id": 2 if operation == "replace" else id}
    seq = commit(store, *([retire, content] if reverse else [content, retire]))
    if operation == "replace":
        assert store.document(2) is None
        assert store.find_document(address()).id == 3
        assert store.document(3).to_tree()["record"]["createdAt"] == seq
    elif operation == "empty-occupied":
        assert store.document(3) is None and store.find_document(address()).id == 2
        assert ids(store.scan_documents(document_query(), 10)) == [2]
    else:
        assert store.document(id) is None and store.find_document(address()) is None
    retained = backend.view(store).document_record(2 if operation == "replace" else id)
    assert retained["retiredAt"] == seq
    if operation.startswith("empty"):
        assert retained["createdAt"] == seq
    with pytest.raises(RuntimeError, match="already belongs to document"):
        commit(store, create_document(id))
    assert store.mint_id() == max(3, id + 1)
    store.close()


@ALL
@pytest.mark.parametrize(
    "writes,message",
    [
        (
            [change_document(2, {}), change_document(2, {})],
            "more than one content command",
        ),
        ([create_document(3), change_document(3, {})], "more than one content command"),
        ([change_document(3, {}), create_document(3)], "more than one content command"),
        ([{"type": "document.retire", "id": 2}] * 2, "retired more than once"),
        ([change_document(999, {})], "Unknown document: 999"),
        ([{"type": "document.retire", "id": 999}], "Unknown document: 999"),
        ([create_document(3)], "address already has a current incarnation"),
    ],
)
def test_document_supported_failures_are_atomic_and_ordinary_errors(
    backend, writes, message
):
    store = root(backend)
    commit(store, create_document(2), task(3), submission(4))
    # Conflict recipe must use a fresh global ID, not fail at ID claims first.
    writes = [
        {**w, "record": {**w["record"], "id": 5}}
        if w.get("type") == "document.create" and w["record"]["id"] == 3
        else w
        for w in writes
    ]
    if "content command" in message:
        writes = [{**w, "id": 5} if w.get("id") == 3 else w for w in writes]
    before = backend.state(store)
    with pytest.raises(RuntimeError, match=message) as failed:
        commit(store, entry(100), task(3, "running"), submission(4, "new"), *writes)
    assert type(failed.value) is RuntimeError and backend.state(store) == before
    assert (
        store.entry(100) is None
        and store.task(3).to_tree()["state"]["status"] == "pending"
    )
    assert store.submission_by_request(1, "new") is None
    assert store.document(2) and store.find_document(address()).id == 2
    assert store.mint_id() == 5 and store.commit(()) == 3
    store.close()


@ALL
@pytest.mark.parametrize("unsupported", ["delta", "copy"])
def test_unsupported_document_batch_does_not_publish_supported_mixed_prefix(
    backend,
    unsupported,
):
    store = root(backend)
    commit(store, create_document(2))
    before = backend.state(store)
    final = (
        {
            "type": "document.copy",
            "record": {"id": 101, "kind": "copy", "scope": SESSION},
            "source": {"id": 2, "at": "current"},
        }
        if unsupported == "copy"
        else {
            "type": "document.change",
            "id": 2,
            "content": {"kind": "delta", "version": 1, "ops": []},
        }
    )
    with pytest.raises(UnsupportedStorageOperation, match="DE04/DE27"):
        commit(store, entry(100), create_document(102, key="supported"), final)
    assert (
        backend.state(store) == before
        and store.entry(100) is None
        and store.document(102) is None
    )
    assert store.mint_id() == 3
    store.close()


@ALL
@pytest.mark.parametrize(
    "kind", ["foreign", "eq-only", "repr-only", "str-subclass", "int-subclass"]
)
@pytest.mark.parametrize("method", ["document", "find", "query-point", "raw-query"])
def test_document_point_ingress_rejects_foreign_without_any_hook(backend, kind, method):
    store = root(backend)
    commit(store, create_document(2))
    calls = []
    value = _foreign_point(kind, calls)
    calls.clear()  # Construction is outside the public call's evidence boundary.
    before = backend.state(store)
    try:
        with pytest.raises(ValueError) as failure:
            originals._point_call(store, method, value)
        assert not isinstance(failure.value, UnsupportedStorageOperation)
        assert calls == []
        assert backend.state(store) == before and not backend.locked(store)
    finally:
        store.close()


@ALL
@pytest.mark.parametrize("fault", ["address-conflict", "allocation"])
def test_document_late_candidate_failure_rolls_back_tables_docs_indexes_and_counters(
    backend, fault
):
    store = root(backend)
    commit(store, entry(2, head=2), task(3), submission(4), create_document(5))
    assert store.mint_id() == 6
    before = backend.state(store)
    witnessed = []

    def inspect(candidate):
        assert candidate.entry(100)["conversationId"] == 1
        assert candidate.task(3)["state"]["status"] == "running"
        assert candidate.owners(3) == (99,)
        assert candidate.task_status("running") == (3,)
        assert candidate.entries(1) == (2, 100)
        assert candidate.winner(1, "new") == 4
        assert candidate.document(101)["value"] == {"prepared": True}
        assert 101 in candidate.scope()
        witnessed.append(True)
        if fault == "allocation":
            raise MemoryError("candidate allocation after mixed preparation")

    backend.at_commit(inspect)
    writes = [
        {
            "type": "conversation",
            "value": {"id": 99, "owner": {"conversationId": 1, "taskId": 3}},
        },
        entry(100),
        task(3, "running"),
        submission(4, "new"),
        create_document(101, kind="new", value={"prepared": True}),
    ]
    if fault == "address-conflict":
        writes.append(create_document(102))
    with pytest.raises(
        MemoryError if fault == "allocation" else RuntimeError,
        match="allocation|current incarnation",
    ):
        commit(store, *writes)
    assert witnessed == [True] and backend.state(store) == before
    assert store.conversation(99) is None
    assert (
        store.scan_conversations(ConversationQuery({"ownerTaskId": 3}), 10).items == ()
    )
    assert store.entry(100) is None and store.document(101) is None
    assert store.find_latest_head_marker(1).id == 2
    assert store.entry(2).commit_seq == 2
    assert ids(store.scan_tasks(TaskQuery({"status": "pending"}), 10)) == [3]
    assert ids(store.scan_tasks(TaskQuery({"status": "running"}), 10)) == []
    assert store.submission_by_request(1, "new") is None
    assert ids(store.scan_documents(document_query(), 10)) == [5]
    assert store.find_document(address()).id == 5
    assert store.submission_by_request(1, "x").id == 4
    backend.at_commit(None)
    assert store.mint_id() == (6 if isinstance(store, Reopening) else 7)
    assert store.commit(()) == 3


@ONE_INSTANCE
@pytest.mark.parametrize("contender", ["write", "read", "mint", "close", "failure"])
def test_mixed_document_candidate_uses_same_contended_authority(backend, contender):
    store = root(backend)

    def check(candidate):
        assert candidate.entry(100) and candidate.document(102)

    def fail():
        raise MemoryError("mixed preparation failure")

    def read(_):
        with store._lock:
            view = backend.view(store)
            captured = (100 in view.entry_ids(), view.document_record(102)["id"] == 102)
        return captured, store.entry(100) is not None, store.document(102) is not None

    choices = {
        "write": lambda _: commit(
            store, submission(103), create_document(104, key="next")
        ),
        "read": read,
        "mint": lambda _: store.mint_id(),
        "close": lambda _: store.close(),
        "failure": lambda _: commit(store, create_document(103)),
    }

    def verify(writer, follower):
        if contender == "failure":
            with pytest.raises(MemoryError):
                writer.result(5)
            assert follower.result(5) == 2 and store.document(103)
            assert store.entry(100) is None and store.document(102) is None
            return
        assert writer.result(5) == 2
        result = follower.result(5)
        if contender == "write":
            assert (
                result == 3
                and store.document(102)
                and store.document(104)
                and store.submission(103)
            )
        elif contender == "read":
            assert result == ((True, True), True, True)
        elif contender == "mint":
            assert result == 103
        else:
            for operation in operations(store):
                with pytest.raises(StorageClosedError):
                    operation()

    _run_contended(
        backend,
        store,
        (entry(100), task(101), create_document(102)),
        check,
        choices[contender],
        verify,
        after=fail if contender == "failure" else None,
    )


@ALL
@pytest.mark.parametrize("method", ["document", "find"])
def test_document_point_reentry_child_is_bounded_and_never_runs_hook(
    backend, tmp_path, method
):
    from sqlite_child_support import Child

    code = r"""
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import DocumentAddress
store = MemoryStorage() if sys.argv[1] == "memory" else open_sqlite_storage(sys.argv[2], mode="create")
class Point:
    def __eq__(self, other):
        say("hook-entered")
        store.mint_id()
        return True
say("ready")
assert command() == "go"
try:
    if sys.argv[3] == "document":
        store.document(2, Point())
    else:
        store.find_document(DocumentAddress({"kind":"cache","scope":{"kind":"session"}}), Point())
except ValueError as error:
    say("refused", error=type(error).__name__, next_id=store.mint_id())
else:
    say("accepted")
store.close()
"""
    # The reopening factory has the same ingress, and this child independently opens its store.
    with Child(
        code, backend.name, str(tmp_path / "child.sqlite"), method, home=tmp_path
    ) as child:
        child.expect("ready")
        child.send("go")
        event = child.expect("refused", timeout=2)
        assert event["error"] == "RecordError" and event["next_id"] == 2
        assert child.wait() == 0
        assert child.evidence()["stderr"] == ""
        assert child.process.stderr.read() == b""
    (tmp_path / "point-child-evidence.json").write_text(
        __import__("json").dumps(child.evidence())
    )


@ALL
@pytest.mark.parametrize("version", [0, -1, 1.5, -0.0, 2**100])
def test_raw_document_signed_zero_and_version_follow_memory(backend, version):
    from pipy_harness.native.durable.codec import OwnedJson

    store = root(backend)
    commit(store, create_document(2, value={"zero": -0.0}, version=version))
    expected = OwnedJson({"version": version, "value": {"zero": -0.0}})
    materialized = store.document(2).to_tree()
    assert (
        OwnedJson({key: materialized[key] for key in ("version", "value")}) == expected
    )
    commit(store, change_document(2, {"zero": -0.0}, version))
    materialized = store.document(2).to_tree()
    assert (
        OwnedJson({key: materialized[key] for key in ("version", "value")}) == expected
    )
