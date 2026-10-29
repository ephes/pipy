"""New absent-migrator entry points on genuine Session/Tx/backend paths."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import (
    Passes,
    generation,
    held_real_drain,
    owner,
    resume_retired,
)
from orphan_missing_definition_support import (
    CommitHold,
    GraphScans,
    current_documents,
    populate_large_graph,
    sqlite_document_rows,
    task_documents,
    unrelated_history,
)
from orphan_missing_migration_support import (
    GRAPH_REFUSAL,
    REFUSAL,
    Calls,
    assert_missing_terminal,
    structural_target,
    terminal_replay,
)
from orphan_task_too_old_support import FitCalls, reopen_sqlite
from restart_policy_live_support import Tickets, held_line, pause
from task_migration_support import Rig, definition, opener, sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.task_values import identity


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.storage.before_commit = None
        value.storage.after_commit = None
        value.close()


@pytest.mark.parametrize("initial_missing", [True, False])
@pytest.mark.parametrize("first", ["publication", "orphan"])
def test_actual_fifo_captures_callback_absence_on_the_commit_line(
    rig, monkeypatch, initial_missing, first
):
    calls = Calls("missing-migration.fifo")
    target = rig.create(calls.declaration(1, "stored"))
    initial = calls.declaration(2, "initial", migration=not initial_missing)
    replacement = calls.declaration(2, "replacement", migration=initial_missing)
    scheduler = owner(rig, [initial])
    tickets = Tickets(rig.session, monkeypatch)
    fits = FitCalls(scheduler, target, monkeypatch)
    before, prior, callbacks = rig.silence(), rig.record(target), calls.checkpoint()
    actions = {
        "publication": lambda: scheduler.install_definition(replacement),
        "orphan": lambda: scheduler.orphan_missing_migration(target),
    }
    succeeds = not initial_missing if first == "publication" else initial_missing
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(actions[first])}
            tickets.wait(1)
            second = "orphan" if first == "publication" else "publication"
            jobs[second] = pool.submit(actions[second])
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0] and actual[0][1] != actual[1][1]
        if succeeds:
            assert jobs["orphan"].result(5) == "orphaned"
        else:
            with pytest.raises(UnsupportedTaskExecution, match=f"^{REFUSAL}$"):
                jobs["orphan"].result(5)
        assert jobs["publication"].result(5)
    binding = generation(scheduler).bindings[identity(calls.name)]
    assert binding.source is replacement
    assert (binding.captured.migrate is None) is (not initial_missing)
    assert calls.checkpoint() == callbacks and fits.calls == []
    if succeeds:
        assert_missing_terminal(rig, target, prior)
        assert len(rig.storage.commits) == before[1] + 1
        terminal_replay(rig, scheduler, target)
    else:
        rig.assert_silent(before)
        assert rig.record(target) == prior


def test_real_busy_then_retired_enabled_action_does_not_dispatch_target(
    rig, monkeypatch
):
    target, _, calls, scheduler = structural_target(rig)
    with held_real_drain(scheduler, monkeypatch):
        before, callbacks = rig.silence(), calls.checkpoint()
        with pytest.raises(RuntimeError, match="^Task scheduler is busy$"):
            scheduler.orphan_missing_migration(target)
        rig.assert_silent(before)
        assert calls.checkpoint() == callbacks
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    with scheduler._guard:
        assert scheduler._enabled and not scheduler._draining
        assert scheduler._invocation is None
    prior, callbacks = rig.record(target), calls.checkpoint()
    assert scheduler.orphan_missing_migration(target) == "orphaned"
    with scheduler._guard:
        assert scheduler._enabled
    pause(scheduler)
    assert_missing_terminal(rig, target, prior)
    assert calls.checkpoint() == callbacks == (("initial", "stored"),)
    terminal_replay(rig, scheduler, target)


def test_all_257_loaded_and_unloaded_documents_retire_with_unrelated_history(
    rig, monkeypatch
):
    target, _, calls, scheduler = structural_target(rig)
    document_calls = []
    family, documents = task_documents(rig, target, 257, document_calls)
    loaded = rig.session.snapshot(family, task_id=target, key="0")
    token, unrelated_id = unrelated_history(rig)
    other = rig.create(definition("missing-migration.unrelated"))
    _, other_documents = task_documents(rig, other, 1, [])
    # Labelled ordinary typed Tx data: not a claimed natural restart producer.
    rig.change(target, lambda row: row.update(memos={"saved": [1, None]}, restarts=2))
    history = (
        sqlite_document_rows(rig.path, {unrelated_id})
        if rig.backend == "sqlite"
        else None
    )
    prior, other_row = rig.record(target), rig.record(other)
    callbacks, initializers = calls.checkpoint(), tuple(document_calls)
    fits = FitCalls(scheduler, target, monkeypatch)
    commits = len(rig.storage.commits)
    assert scheduler.orphan_missing_migration(target) == "orphaned"
    assert_missing_terminal(rig, target, prior)
    assert len(rig.storage.commits) == commits + 1
    retired = [
        write.to_tree()["id"]
        for write in rig.storage.commits[-1]
        if write.to_tree()["type"] == "document.retire"
    ]
    ids = {row.id for row in documents}
    assert len(retired) == len(set(retired)) == 257 and set(retired) == ids
    assert current_documents(rig, target) == ()
    assert rig.session.snapshot(family, task_id=target, key="0") is None
    assert loaded.value.thaw() == {"saved": 0}
    assert tuple(document_calls) == initializers
    assert calls.checkpoint() == callbacks and fits.calls == []
    assert rig.record(other) == other_row
    assert current_documents(rig, other) == other_documents
    assert rig.session.snapshot(
        token, conversation_id=rig.conversation
    ).value.thaw() == {"saved": 2}
    if rig.backend == "sqlite":
        rows, revisions = sqlite_document_rows(rig.path, ids)
        assert len(rows) == 257 and all(row[1] is not None for row in rows)
        assert revisions == ()
        assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    terminal_replay(rig, scheduler, target)


@pytest.mark.parametrize("edge", ["terminal-child", "incoming-waiter", "unrelated"])
def test_late_page_edges_refuse_the_new_entry_with_a_valid_neighbor(
    rig, monkeypatch, edge
):
    target, _, calls, scheduler = structural_target(rig)
    filler = definition("missing-migration.graph-unrelated")
    _, tasks = populate_large_graph(rig, filler)
    edge_id = None
    if edge == "terminal-child":
        edge_id = rig.session.commit(
            lambda tx: tx.create_task(
                filler, None, ownership={"kind": "task", "taskId": target}
            )
        )
        rig.change(
            edge_id,
            lambda row: row.update(
                state={
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": None},
                }
            ),
        )
    elif edge == "incoming-waiter":
        edge_id = tasks[-1]
        # Native-only typed WAITING control over genuine Tx-made identities.
        rig.change(
            edge_id,
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
    scans = GraphScans(rig, monkeypatch)
    before, prior, callbacks = rig.silence(), rig.record(target), calls.checkpoint()
    if edge == "unrelated":
        assert scheduler.orphan_missing_migration(target) == "orphaned"
        assert_missing_terminal(rig, target, prior)
    else:
        with pytest.raises(UnsupportedTaskExecution, match=f"^{GRAPH_REFUSAL}$"):
            scheduler.orphan_missing_migration(target)
        rig.assert_silent(before)
        assert any(page[1] is not None and edge_id in page[3] for page in scans.pages)
    scans.assert_complete_multiple_pages()
    assert calls.checkpoint() == callbacks


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cold_pending_requires_no_observation_and_cold_terminal_needs_no_code(rig):
    target, source, calls, _ = structural_target(rig)
    scheduler = reopen_sqlite(rig, [source])
    with scheduler._guard:
        assert scheduler._migration_failures.get(target) is None
    prior, callbacks = rig.record(target), calls.checkpoint()
    assert scheduler.orphan_missing_migration(target) == "orphaned"
    terminal = assert_missing_terminal(rig, target, prior)
    scheduler = reopen_sqlite(rig, [])
    assert rig.record(target) == terminal
    terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == callbacks == (("initial", "stored"),)


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_real_precommit_fault_rolls_back_all_tables_and_revisions(rig, monkeypatch):
    target, _, calls, scheduler = structural_target(rig)
    task_documents(rig, target, 3, [])
    unrelated_history(rig)
    before, callbacks, entered = sqlite_rows(rig.path), calls.checkpoint(), []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("missing-migration-before-commit")

    monkeypatch.setattr(sqlite, "_before_commit", fail)
    with pytest.raises(RuntimeError, match="missing-migration-before-commit"):
        scheduler.orphan_missing_migration(target)
    assert entered == [True]
    assert sqlite_rows(rig.path) == before and calls.checkpoint() == callbacks
    # No memo-retention oracle on a failed owner that may shut down its cache.


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_commit_before_ack_loss_reopens_exactly_one_terminal_winner(rig, monkeypatch):
    target, source, calls, scheduler = structural_target(rig)
    _, documents = task_documents(rig, target, 3, [])
    _, unrelated_id = unrelated_history(rig)
    prior, callbacks, entered = rig.record(target), calls.checkpoint(), []
    history = sqlite_document_rows(rig.path, {unrelated_id})

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("missing-migration-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite, "_after_commit", fail)
        with pytest.raises(RuntimeError, match="missing-migration-ack-lost"):
            scheduler.orphan_missing_migration(target)
    assert entered == [False]
    scheduler = reopen_sqlite(rig, [source])
    assert_missing_terminal(rig, target, prior)
    before = sqlite_rows(rig.path)
    terminal_replay(rig, scheduler, target)
    assert sqlite_rows(rig.path) == before
    assert rig.session.read_on_line(
        lambda: all(rig.storage.document(row.id) is None for row in documents)
    )
    assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    assert calls.checkpoint() == callbacks


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize(
    "shutdown", [False, True], ids=["cancel-close", "loop-shutdown"]
)
def test_cancel_close_and_loop_teardown_join_real_commit_before_lease_release(
    rig, tmp_path, shutdown
):
    target, source, calls, scheduler = structural_target(rig)
    task_documents(rig, target, 3, [])
    hold = CommitHold(rig.storage, "after")
    cancelled, exited = threading.Event(), threading.Event()
    callbacks = calls.checkpoint()

    def caller():
        async def scenario():
            waiter = asyncio.create_task(scheduler.aorphan_missing_migration(target))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            cancelled.set()
            if not shutdown:
                with pytest.raises(asyncio.CancelledError):
                    await waiter

        try:
            asyncio.run(scenario())
        finally:
            exited.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(caller)
        try:
            assert cancelled.wait(5)
            rig.session.request_close()
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None and not exited.is_set()
            label = "shutdown" if shutdown else "cancel"
            opener(rig.path, tmp_path, "missing-" + label + "-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=10)
    assert hold.retired.is_set() and exited.is_set() and len(hold.calls) == 1
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "missing-" + label + "-retired", "opened")
    scheduler = reopen_sqlite(rig, [source])
    assert rig.record(target)["state"]["outcome"] == {
        "status": "orphaned",
        "reason": "migration_failed",
    }
    terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == callbacks
