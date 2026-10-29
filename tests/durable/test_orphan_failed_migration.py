"""Observed-failure orphan control on real Memory/SQLite Session lines."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import pause, resume_retired
from orphan_failed_migration_support import (
    GRAPH_REFUSAL,
    REFUSAL,
    assert_failed_terminal,
    authority,
    observed_failure,
    terminal_replay,
    unrelated_definition,
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
from orphan_task_too_old_support import FitCalls, reopen_sqlite
from restart_policy_live_support import Tickets, held_line
from task_migration_support import Rig, opener, sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution


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


@pytest.mark.parametrize("restore_matching", [False, True])
@pytest.mark.parametrize("first", ["publication", "orphan"])
def test_actual_fifo_chooses_current_binding_and_genuine_memo(
    rig, monkeypatch, restore_matching, first
):
    target, source, calls, scheduler, _, binding = observed_failure(rig, monkeypatch)
    replacement = calls.declaration(2, "B")
    if restore_matching:
        assert scheduler.install_definition(replacement)
        successor = source
    else:
        successor = replacement
    tickets = Tickets(rig.session, monkeypatch)
    fits = FitCalls(scheduler, target, monkeypatch)
    before, prior, call_before = rig.silence(), rig.record(target), calls.checkpoint()
    callbacks = {
        "publication": lambda: scheduler.install_definition(successor),
        "orphan": lambda: scheduler.orphan_failed_migration(target),
    }
    succeeds = restore_matching if first == "publication" else not restore_matching
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(callbacks[first])}
            tickets.wait(1)
            second = "orphan" if first == "publication" else "publication"
            jobs[second] = pool.submit(callbacks[second])
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0] and actual[0][1] != actual[1][1]
        if succeeds:
            assert jobs["orphan"].result(5) == "orphaned"
        else:
            with pytest.raises(UnsupportedTaskExecution, match=f"^{REFUSAL}$"):
                jobs["orphan"].result(5)
        assert jobs["publication"].result(5)
    current, memo = authority(scheduler, target)
    assert current.source is successor
    assert calls.checkpoint() == call_before and fits.calls == []
    if succeeds:
        assert_failed_terminal(rig, target, prior)
        assert memo is None and len(rig.storage.commits) == before[1] + 1
        terminal_replay(rig, scheduler, target)
    else:
        rig.assert_silent(before)
        assert memo is binding and rig.record(target) == prior


def test_genuine_second_failure_overwrites_old_binding_authority(rig, monkeypatch):
    target, source, calls, scheduler, passes, original = observed_failure(
        rig, monkeypatch
    )
    replacement = calls.declaration(3, "B")
    assert scheduler.install_definition(replacement)
    resume_retired(scheduler, passes)
    pause(scheduler)
    current, memo = authority(scheduler, target)
    assert current is memo and current is not original
    assert current.source is replacement
    assert scheduler.install_definition(source)
    restored, memo = authority(scheduler, target)
    assert restored.source is source and restored is not original
    assert memo is current
    before, call_before = rig.silence(), calls.checkpoint()
    with pytest.raises(UnsupportedTaskExecution, match=f"^{REFUSAL}$"):
        scheduler.orphan_failed_migration(target)
    rig.assert_silent(before)
    assert calls.checkpoint() == call_before
    resume_retired(scheduler, passes)
    pause(scheduler)
    assert authority(scheduler, target) == (restored, restored)
    prior, call_before = rig.record(target), calls.checkpoint()
    assert scheduler.orphan_failed_migration(target) == "orphaned"
    assert_failed_terminal(rig, target, prior)
    assert calls.checkpoint() == call_before
    assert [event for event in calls.events if event[0] == "migrate"] == [
        ("migrate", "A", 1),
        ("migrate", "B", 1),
        ("migrate", "A", 1),
    ]


def test_invalid_migration_result_is_an_actual_observed_failure(rig, monkeypatch):
    target, _, calls, scheduler, _, binding = observed_failure(
        rig, monkeypatch, malformed=True
    )
    before, call_before = rig.record(target), calls.checkpoint()
    assert authority(scheduler, target) == (binding, binding)
    assert scheduler.orphan_failed_migration(target) == "orphaned"
    assert_failed_terminal(rig, target, before)
    assert calls.checkpoint() == call_before


def test_257_task_documents_retire_without_touching_unrelated_history(rig, monkeypatch):
    target, _, calls, scheduler, _, _ = observed_failure(rig, monkeypatch)
    document_calls = []
    family, documents = task_documents(rig, target, 257, document_calls)
    loaded = rig.session.snapshot(family, task_id=target, key="0")
    token, unrelated_id = unrelated_history(rig)
    other = rig.create(unrelated_definition())
    _, other_documents = task_documents(rig, other, 1, [])
    rig.change(target, lambda row: row.update(memos={"saved": [1, None]}, restarts=2))
    history = (
        sqlite_document_rows(rig.path, {unrelated_id})
        if rig.backend == "sqlite"
        else None
    )
    prior, other_row = rig.record(target), rig.record(other)
    callbacks, init_before = calls.checkpoint(), tuple(document_calls)
    assert scheduler.orphan_failed_migration(target) == "orphaned"
    assert_failed_terminal(rig, target, prior)
    retired = [
        w.to_tree()["id"]
        for w in rig.storage.commits[-1]
        if w.to_tree()["type"] == "document.retire"
    ]
    ids = {row.id for row in documents}
    assert len(retired) == len(set(retired)) == 257 and set(retired) == ids
    assert current_documents(rig, target) == ()
    assert rig.session.snapshot(family, task_id=target, key="0") is None
    assert loaded.value.thaw() == {"saved": 0}
    assert tuple(document_calls) == init_before and calls.checkpoint() == callbacks
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


@pytest.mark.parametrize(
    "edge", ["terminal-child", "owned-conversation", "incoming-waiter", "unrelated"]
)
def test_actual_late_page_graph_edges_are_checked_after_failure(rig, monkeypatch, edge):
    target, _, calls, scheduler, _, _ = observed_failure(rig, monkeypatch)
    filler = unrelated_definition()
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
    elif edge == "owned-conversation":
        edge_id = rig.session.commit(
            lambda tx: tx.create_conversation({"kind": "task", "taskId": target})
        ).id
    elif edge == "incoming-waiter":
        edge_id = tasks[-1]
        # Typed native WAITING control; genuine Tx-made identities and ownership.
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
        assert scheduler.orphan_failed_migration(target) == "orphaned"
        assert_failed_terminal(rig, target, prior)
    else:
        with pytest.raises(UnsupportedTaskExecution, match=f"^{GRAPH_REFUSAL}$"):
            scheduler.orphan_failed_migration(target)
        rig.assert_silent(before)
        assert any(page[1] is not None and edge_id in page[3] for page in scans.pages)
    scans.assert_complete_multiple_pages()
    assert calls.checkpoint() == callbacks


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_new_owner_must_observe_its_own_failure_then_terminal_replay_is_cold(
    rig, monkeypatch
):
    target, source, calls, _, _, _ = observed_failure(rig, monkeypatch)
    scheduler = reopen_sqlite(rig, [source])
    before, callbacks = rig.silence(), calls.checkpoint()
    assert authority(scheduler, target)[1] is None
    with pytest.raises(UnsupportedTaskExecution, match=f"^{REFUSAL}$"):
        scheduler.orphan_failed_migration(target)
    rig.assert_silent(before)
    assert calls.checkpoint() == callbacks
    from definition_publication_live_support import Passes

    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    current, memo = authority(scheduler, target)
    assert current is memo and current.source is source
    prior, callbacks = rig.record(target), calls.checkpoint()
    assert scheduler.orphan_failed_migration(target) == "orphaned"
    terminal = assert_failed_terminal(rig, target, prior)
    scheduler = reopen_sqlite(rig, [])
    assert rig.record(target) == terminal
    terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == callbacks


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_real_precommit_failure_keeps_all_logical_tables_and_revisions(
    rig, monkeypatch
):
    target, _, calls, scheduler, _, _ = observed_failure(rig, monkeypatch)
    task_documents(rig, target, 3, [])
    unrelated_history(rig)
    before, callbacks, entered = sqlite_rows(rig.path), calls.checkpoint(), []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("failed-orphan-before-commit")

    monkeypatch.setattr(sqlite, "_before_commit", fail)
    with pytest.raises(RuntimeError, match="failed-orphan-before-commit"):
        scheduler.orphan_failed_migration(target)
    assert entered == [True]
    assert sqlite_rows(rig.path) == before and calls.checkpoint() == callbacks


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_durable_commit_before_lost_ack_reopens_one_terminal_winner(rig, monkeypatch):
    target, source, calls, scheduler, _, _ = observed_failure(rig, monkeypatch)
    _, documents = task_documents(rig, target, 3, [])
    _, unrelated_id = unrelated_history(rig)
    prior, callbacks, entered = rig.record(target), calls.checkpoint(), []
    history = sqlite_document_rows(rig.path, {unrelated_id})

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("failed-orphan-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite, "_after_commit", fail)
        with pytest.raises(RuntimeError, match="failed-orphan-ack-lost"):
            scheduler.orphan_failed_migration(target)
    assert entered == [False]
    scheduler = reopen_sqlite(rig, [source])
    assert_failed_terminal(rig, target, prior)
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
def test_cancel_and_close_join_actual_commit_worker_before_lease_release(
    rig, monkeypatch, tmp_path, shutdown
):
    target, source, calls, scheduler, _, _ = observed_failure(rig, monkeypatch)
    task_documents(rig, target, 3, [])
    hold = CommitHold(rig.storage, "after")
    cancelled, exited = threading.Event(), threading.Event()
    callbacks = calls.checkpoint()

    def caller():
        async def scenario():
            waiter = asyncio.create_task(scheduler.aorphan_failed_migration(target))
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
            opener(rig.path, tmp_path, "failed-" + label + "-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=10)
    assert hold.retired.is_set() and exited.is_set() and len(hold.calls) == 1
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "failed-" + label + "-retired", "opened")
    scheduler = reopen_sqlite(rig, [source])
    assert rig.record(target)["state"]["outcome"] == {
        "status": "orphaned",
        "reason": "migration_failed",
    }
    terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == callbacks
