"""Broad explicit orphan acceptance; typed controls are not historical producers."""

import asyncio
import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import (
    CallbackProbe,
    Passes,
    Wakes,
    generation,
    held_real_drain,
    owner,
    resume_retired,
)
from orphan_missing_definition_support import (
    CommitHold,
    GraphScans,
    assert_orphan,
    current_documents,
    populate_large_graph,
    sqlite_document_rows,
    task_documents,
    unrelated_history,
)
from restart_policy_live_support import Tickets, held_line, idle, pause
from task_migration_support import Rig, definition, opener, sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.storage.before_commit = None
        value.storage.after_commit = None
        value.close()


@pytest.mark.parametrize("loaded", [False, True], ids=["cold-docs", "loaded-docs"])
def test_all_257_real_task_documents_retire_in_one_commit_without_history_copies(
    rig, loaded
):
    calls = []
    source = definition("orphan.large.documents")
    target = rig.create(source)
    family, documents = task_documents(rig, target, 257, calls)
    token, unrelated_id = unrelated_history(rig)
    assert len(documents) == 257
    ids = {row.id for row in documents}
    if loaded:
        saved = rig.session.snapshot(family, task_id=target, key="0")
        assert saved.value.thaw() == {"saved": 0}
    else:
        saved = None
    scheduler = owner(rig, [])
    prior = rig.record(target)
    old_inspection = scheduler.inspect_task(target)
    old_tree = old_inspection.to_tree()
    initial_calls = tuple(calls)
    commits = len(rig.storage.commits)
    history = (
        sqlite_document_rows(rig.path, {unrelated_id})
        if rig.backend == "sqlite"
        else None
    )
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    assert_orphan(rig, target, prior)
    assert len(rig.storage.commits) == commits + 1
    batch = rig.storage.commits[-1]
    retired = [
        write.to_tree()["id"]
        for write in batch
        if write.to_tree()["type"] == "document.retire"
    ]
    assert len(retired) == len(set(retired)) == 257 and set(retired) == ids
    assert current_documents(rig, target) == ()
    assert rig.session.read_on_line(
        lambda: all(rig.storage.document(id) is None for id in ids)
    )
    assert rig.session.snapshot(family, task_id=target, key="0") is None
    assert tuple(calls) == initial_calls
    if saved is not None:
        assert saved.value.thaw() == {"saved": 0}
    assert rig.session.snapshot(
        token, conversation_id=rig.conversation
    ).value.thaw() == {"saved": 2}
    assert old_inspection.to_tree() == old_tree
    assert scheduler.inspect_task(target).status == "terminal"
    if rig.backend == "sqlite":
        rows, revisions = sqlite_document_rows(rig.path, ids)
        assert len(rows) == 257 and all(row[1] is not None for row in rows)
        assert revisions == ()
        assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    before = rig.silence()
    assert asyncio.run(scheduler.aorphan_missing_definition(target)) == "terminal"
    rig.assert_silent(before)


@pytest.mark.parametrize(
    "edge", ["terminal-child", "owned-conversation", "incoming-waiter"]
)
def test_disqualifying_edges_beyond_page_one_refuse_without_any_write(
    rig, monkeypatch, edge
):
    source = definition("orphan.large.graph")
    target = rig.create(source)
    _, others = populate_large_graph(rig, source)
    if edge == "terminal-child":
        child = rig.session.commit(
            lambda tx: tx.create_task(
                source, None, ownership={"kind": "task", "taskId": target}
            )
        )
        # A real child is terminalized through Tx, not dropped from the graph.
        rig.change(
            child,
            lambda row: row.update(
                state={
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": None},
                }
            ),
        )
    elif edge == "owned-conversation":
        rig.session.commit(
            lambda tx: tx.create_conversation({"kind": "task", "taskId": target})
        )
    else:
        # Labelled typed waiting control: creation and ownership are genuine Tx.
        rig.change(
            others[-1],
            lambda row: row.update(
                background=True,
                state={
                    "status": "waiting",
                    "checkpoint": row["state"]["checkpoint"],
                    "on": [target],
                    "policy": "allSettled",
                },
            ),
        )
    scheduler = owner(rig, [])
    scans = GraphScans(rig, monkeypatch)
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution):
        scheduler.orphan_missing_definition(target)
    scans.assert_complete_multiple_pages()
    rig.assert_silent(before)


@pytest.mark.parametrize("count", [None, 5, 7.5, 2**53, 1e300])
def test_explicit_action_does_not_apply_restart_ceiling_or_c1_counter_domain(
    rig, count
):
    target = rig.create(definition("orphan.numeric"))
    if count is not None:
        rig.change(
            target,
            lambda row: row.update(restarts=count, memos={"retained": [1, None]}),
        )
    scheduler = owner(rig, [])
    prior = rig.record(target)
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    assert_orphan(rig, target, prior)


@pytest.mark.parametrize("first", ["publication", "orphan"])
def test_definition_publication_and_orphan_use_actual_fifo_commit_tickets(
    rig, monkeypatch, first
):
    source = definition("orphan.publication.order")
    target = rig.create(source)
    scheduler = owner(rig, [])
    tickets = Tickets(rig.session, monkeypatch)
    callbacks = {
        "publication": lambda: scheduler.install_definition(source),
        "orphan": lambda: scheduler.orphan_missing_definition(target),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            first_job = pool.submit(callbacks[first])
            tickets.wait(1)
            second_job = pool.submit(
                callbacks["orphan" if first == "publication" else "publication"]
            )
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0]
        if first == "publication":
            assert first_job.result(5) is True
            before = rig.silence()
            with pytest.raises(UnsupportedTaskExecution):
                second_job.result(5)
            rig.assert_silent(before)
            assert rig.record(target)["state"]["status"] == "pending"
        else:
            assert first_job.result(5) == "orphaned"
            assert second_job.result(5) is True
            assert rig.record(target)["state"]["outcome"]["reason"] == "missing_task"
    assert generation(scheduler).support.definition(source.name) is not None


def test_actual_drain_with_free_line_is_busy_and_does_not_write(rig, monkeypatch):
    target = rig.create(definition("orphan.busy.drain"))
    scheduler = owner(rig, [])
    with held_real_drain(scheduler, monkeypatch):
        before = rig.silence()
        with pytest.raises(RuntimeError, match="Task scheduler is busy"):
            scheduler.orphan_missing_definition(target)
        rig.assert_silent(before)


def test_service_admitted_during_real_scan_wins_final_activity_gate(rig, monkeypatch):
    target = rig.create(definition("orphan.scan.race"))
    scheduler = owner(rig, [])
    entered, release = threading.Event(), threading.Event()
    original = rig.storage.scan_tasks

    def scan(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(rig.storage, "scan_tasks", scan, raising=False)
    before = rig.silence()
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(scheduler.orphan_missing_definition, target)
        try:
            assert entered.wait(5)
            scheduler.resume()
            with scheduler._guard:
                assert scheduler._guard.wait_for(lambda: scheduler._draining, 5)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="Task scheduler is busy"):
            work.result(10)
    idle(scheduler)
    pause(scheduler)
    rig.assert_silent(before)


def test_failed_migration_binding_is_finalized_outside_guard_by_real_terminal_commit(
    rig, monkeypatch
):
    old = definition("orphan.failure.release", version=1)
    target = rig.create(old)
    scheduler = owner(rig, [])
    calls, drops = [], []
    probe = CallbackProbe(
        calls, failure=True, drops=drops, session=rig.session, guard=scheduler._guard
    )
    weak = weakref.ref(probe)
    current = definition(old.name, version=2, migrate=probe)
    assert scheduler.install_definition(current) is True
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    assert (
        len(calls) == 1 and scheduler.inspect_task(target).reason == "migration_failed"
    )
    assert scheduler.remove_definition(current.name) is True
    del current, probe
    gc.collect()
    assert weak() is not None and drops == []
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    gc.collect()
    assert weak() is None
    assert len(drops) == 1 and drops[0][0] is False
    assert drops[0][1] in {"ReentrantSessionCall", "read_returned"}
    assert len(calls) == 1


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_no_write_oracle_detects_revision_only_change(rig):
    token, id = unrelated_history(rig)
    before = sqlite_rows(rig.path)

    def edit(tx, value):
        tx.doc(token, conversation_id=rig.conversation)["saved"] = value

    rig.session.commit(lambda tx: edit(tx, 99))
    rig.session.commit(lambda tx: edit(tx, 2))
    after = sqlite_rows(rig.path)
    assert before["documents"] == after["documents"]
    assert before["document_revisions"] != after["document_revisions"]
    assert sqlite_document_rows(rig.path, {id})[1]


def test_reentry_is_refused_before_hostile_id_without_coercion(rig):
    scheduler = owner(rig, [])

    class Hostile(int):
        def __int__(self):
            pytest.fail("reentry/ID validation must never coerce caller input")

    before = rig.silence()
    with pytest.raises(ReentrantSessionCall):
        rig.session.read_on_line(
            lambda: scheduler.orphan_missing_definition(Hostile(1))
        )
    rig.assert_silent(before)


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_real_sqlite_before_commit_failure_rolls_back_task_and_all_documents(
    rig, monkeypatch
):
    target = rig.create(definition("orphan.rollback"))
    task_documents(rig, target, 3, [])
    scheduler = owner(rig, [])
    before = sqlite_rows(rig.path)
    entered = []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("orphan-before-durable-commit")

    monkeypatch.setattr(sqlite, "_before_commit", fail)
    with pytest.raises(RuntimeError, match="orphan-before-durable-commit"):
        scheduler.orphan_missing_definition(target)
    assert entered == [True]
    assert sqlite_rows(rig.path) == before


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_real_sqlite_commit_then_lost_ack_reopens_terminal_without_second_settlement(
    rig, monkeypatch
):
    target = rig.create(definition("orphan.lost.ack"))
    _, docs = task_documents(rig, target, 3, [])
    scheduler = owner(rig, [])
    entered = []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("orphan-durable-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite, "_after_commit", fail)
        with pytest.raises(RuntimeError, match="orphan-durable-ack-lost"):
            scheduler.orphan_missing_definition(target)
    assert entered == [False]
    rig.close()
    stored = open_sqlite_storage(str(rig.path), mode="open")
    reopened = Session(stored)
    try:
        actual = TaskScheduler(reopened, [])
        actual.open()
        before = sqlite_rows(rig.path)
        assert actual.orphan_missing_definition(target) == "terminal"
        assert sqlite_rows(rig.path) == before
        assert reopened.read_on_line(
            lambda: all(stored.document(row.id) is None for row in docs)
        )
        assert not before["document_revisions"]
    finally:
        reopened.close(timeout=10)
    assert not actual._thread.is_alive()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancel_and_close_join_actual_committed_sqlite_worker_before_lease_release(
    rig, tmp_path
):
    target = rig.create(definition("orphan.cancel.close"))
    _, docs = task_documents(rig, target, 3, [])
    scheduler = owner(rig, [])
    hold = CommitHold(rig.storage, "after")

    async def scenario():
        waiter = asyncio.create_task(scheduler.aorphan_missing_definition(target))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            rig.session.request_close()
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None
            opener(rig.path, tmp_path, "orphan-committed-held", "refused")
        finally:
            hold.release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await asyncio.to_thread(hold.retired.wait, 5)

    asyncio.run(scenario())
    rig.session.wait_closed(timeout=10)
    assert len(hold.calls) == 1 and rig.storage.closed.is_set()
    assert not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "orphan-committed-retired", "opened")
    stored = open_sqlite_storage(str(rig.path), mode="open")
    reopened = Session(stored)
    try:
        actual = reopened.read_on_line(lambda: stored.task(target).to_tree())
        assert actual["state"]["outcome"] == {
            "status": "orphaned",
            "reason": "missing_task",
        }
        assert reopened.read_on_line(
            lambda: all(stored.document(row.id) is None for row in docs)
        )
    finally:
        reopened.close(timeout=10)


@pytest.mark.parametrize("enabled", [False, True], ids=["paused", "enabled-retired"])
def test_actual_quiescent_success_preserves_enable_and_only_commit_wake(
    rig, monkeypatch, enabled
):
    calls = []

    async def forbidden(*_args):
        calls.append("target")
        raise AssertionError("Missing-definition cleanup invoked a target callback")

    target = rig.create(definition("orphan.quiescent", phase=forbidden))
    scheduler = owner(rig, [])
    passes = Passes(scheduler, monkeypatch)
    if enabled:
        resume_retired(scheduler, passes)
    idle(scheduler)
    wakes = Wakes(scheduler, monkeypatch)
    before = wakes.checkpoint()
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    with scheduler._guard:
        assert scheduler._enabled is enabled
    assert wakes.checkpoint() == before + 1
    idle(scheduler)
    assert calls == []
    pause(scheduler)
    before = wakes.checkpoint()
    quiet = rig.silence()
    assert scheduler.orphan_missing_definition(target) == "terminal"
    assert wakes.checkpoint() == before
    rig.assert_silent(quiet)


def test_actual_held_invocation_is_busy_without_target_write(rig):
    entered, release = threading.Event(), threading.Event()

    async def hold(row, runtime, _context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    active = definition("orphan.other.active", phase=hold)
    rig.create(active)
    target = rig.create(definition("orphan.busy.invocation"))
    scheduler = owner(rig, [active])
    scheduler.resume()
    try:
        assert entered.wait(5)
        with scheduler._guard:
            assert scheduler._invocation is not None
        before = rig.silence()
        with pytest.raises(RuntimeError, match="Task scheduler is busy"):
            scheduler.orphan_missing_definition(target)
        rig.assert_silent(before)
    finally:
        release.set()
        pause(scheduler)
    assert rig.record(target)["state"]["status"] == "pending"


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_loop_teardown_still_joins_committed_worker_and_keeps_sqlite_lease(
    rig, tmp_path
):
    target = rig.create(definition("orphan.loop.close"))
    scheduler = owner(rig, [])
    hold = CommitHold(rig.storage, "after")
    cancelled, exited = threading.Event(), threading.Event()

    def caller():
        async def scenario():
            task = asyncio.create_task(scheduler.aorphan_missing_definition(target))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            task.cancel()
            cancelled.set()

        try:
            asyncio.run(scenario())
        finally:
            exited.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(caller)
        try:
            assert cancelled.wait(5)
            rig.session.request_close()
            assert not exited.is_set() and not hold.retired.is_set()
            assert not rig.storage.closed.is_set()
            opener(rig.path, tmp_path, "orphan-loop-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=10)
    assert exited.is_set() and hold.retired.is_set()
    assert len(hold.calls) == 1 and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "orphan-loop-retired", "opened")


def test_background_root_with_large_unrelated_graph_is_a_real_positive(
    rig, monkeypatch
):
    source = definition("orphan.background.root")
    target = rig.session.commit(
        lambda tx: tx.create_task(
            source,
            None,
            ownership={"kind": "conversation"},
            conversation_id=rig.conversation,
            background=True,
        )
    )
    populate_large_graph(rig, source)
    scheduler = owner(rig, [])
    scans = GraphScans(rig, monkeypatch)
    before = rig.record(target)
    assert before["background"] is True
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    scans.assert_complete_multiple_pages()
    assert_orphan(rig, target, before)


@pytest.mark.parametrize("value", [True, 1.0, 0, 2**53])
def test_invalid_exact_id_precedes_unopened_lifecycle_without_submission(rig, value):
    from pipy_harness.native.durable.records import RecordError

    scheduler = TaskScheduler(rig.session, [])
    rig.scheduler = scheduler
    before = rig.silence()
    with pytest.raises(RecordError):
        scheduler.orphan_missing_definition(value)
    rig.assert_silent(before)
    with pytest.raises(RuntimeError, match="[Oo]pen"):
        scheduler.orphan_missing_definition(1)
    rig.assert_silent(before)


def test_sync_on_loop_precedes_hostile_id_and_async_rejects_without_coercion(rig):
    from pipy_harness.native.durable.records import RecordError

    scheduler = owner(rig, [])

    class Hostile(int):
        def __int__(self):
            pytest.fail("No caller coercion is permitted")

        def __index__(self):
            pytest.fail("No caller index coercion is permitted")

    async def scenario():
        with pytest.raises(RuntimeError, match="aorphan_missing_definition"):
            scheduler.orphan_missing_definition(Hostile(1))
        with pytest.raises(RecordError):
            await scheduler.aorphan_missing_definition(Hostile(1))

    before = rig.silence()
    asyncio.run(scenario())
    rig.assert_silent(before)


@pytest.mark.parametrize("state", ["failed", "closed"])
def test_failed_and_closed_valid_id_preserve_existing_lifecycle_authority(rig, state):
    from pipy_harness.native.durable.session import SessionClosedError, SessionFailed

    target = rig.create(definition("orphan.lifecycle"))
    scheduler = owner(rig, [])
    before = rig.silence()
    cause = RuntimeError("orphan-private-existing-failure")
    if state == "failed":
        rig.session.fail(cause)
        with pytest.raises(SessionFailed) as refused:
            scheduler.orphan_missing_definition(target)
        assert refused.value.cause is cause
    else:
        rig.close()
        with pytest.raises(SessionClosedError):
            scheduler.orphan_missing_definition(target)
    assert len(rig.storage.commits) == before[1]
    assert len(rig.storage.mints) == before[2]
    if rig.backend == "sqlite":
        assert sqlite_rows(rig.path) == before[0]
    else:
        with rig.storage.inner._lock:
            assert rig.storage.inner._state is before[0]
