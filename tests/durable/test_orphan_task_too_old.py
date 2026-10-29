"""B5 broad development tests; typed policy controls are not crash producers."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import (
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
    current_documents,
    populate_large_graph,
    sqlite_document_rows,
    task_documents,
    unrelated_history,
)
from orphan_task_too_old_support import (
    GRAPH_REFUSAL,
    VERSION_REFUSAL,
    FitCalls,
    TargetCalls,
    assert_terminal_replay,
    assert_too_old_terminal,
    created_target,
    reopen_sqlite,
)
from restart_policy_live_support import Tickets, held_line, idle, pause
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


@pytest.mark.parametrize(
    "initial_version", [1, 2], ids=["older-to-fit", "fit-to-older"]
)
@pytest.mark.parametrize("first", ["publication", "orphan"])
def test_actual_fifo_decides_present_older_generation_on_the_commit_line(
    rig, monkeypatch, initial_version, first
):
    calls = TargetCalls("too-old.fifo")
    target = rig.create(calls.declaration(2))
    initial = calls.declaration(initial_version)
    successor = calls.declaration(2 if initial_version == 1 else 1)
    scheduler = owner(rig, [initial])
    old_generation = generation(scheduler)
    old_capture = old_generation.definitions[identity(initial.name)]
    tickets = Tickets(rig.session, monkeypatch)
    fits = FitCalls(scheduler, target, monkeypatch)
    callbacks = {
        "publication": lambda: scheduler.install_definition(successor),
        "orphan": lambda: scheduler.orphan_task_too_old(target),
    }
    before, prior = rig.silence(), rig.record(target)
    call_before = calls.checkpoint()
    expected_version = successor.version if first == "publication" else initial.version
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(callbacks[first])}
            tickets.wait(1)
            second = "orphan" if first == "publication" else "publication"
            jobs[second] = pool.submit(callbacks[second])
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0]
            assert actual[0][1] != actual[1][1]
        if expected_version == 1:
            assert jobs["orphan"].result(5) == "orphaned"
        else:
            with pytest.raises(UnsupportedTaskExecution, match=f"^{VERSION_REFUSAL}$"):
                jobs["orphan"].result(5)
        assert jobs["publication"].result(5) is True
    assert generation(scheduler).bindings[identity(initial.name)].source is successor
    assert old_generation.definitions[identity(initial.name)] is old_capture
    assert old_capture.version == initial_version
    assert calls.checkpoint() == call_before and fits.calls == []
    if expected_version == 1:
        assert_too_old_terminal(rig, target, prior)
        assert len(rig.storage.commits) == before[1] + 1
        assert_terminal_replay(rig, scheduler, target)
    else:
        rig.assert_silent(before)
        assert rig.record(target) == prior


def test_257_loaded_and_unloaded_documents_retire_with_unrelated_history_intact(rig):
    target, older, calls = created_target(rig, "too-old.documents")
    document_calls = []
    family, documents = task_documents(rig, target, 257, document_calls)
    loaded = rig.session.snapshot(family, task_id=target, key="0")
    assert loaded.value.thaw() == {"saved": 0}
    token, history_id = unrelated_history(rig)
    other = rig.create(definition("too-old.unrelated"))
    other_family, other_documents = task_documents(rig, other, 1, [])
    history = (
        sqlite_document_rows(rig.path, {history_id})
        if rig.backend == "sqlite"
        else None
    )
    # Explicit typed restart-policy control, not a claimed crash/recovery origin.
    rig.change(
        target,
        lambda row: row.update(restarts=5, memos={"retained": [1, None]}),
    )
    scheduler = owner(rig, [older])
    before, unrelated = rig.record(target), rig.record(other)
    old_inspection = scheduler.inspect_task(target)
    assert old_inspection.restart_count == old_inspection.restart_limit == 5
    assert old_inspection.restart_parked is True
    assert old_inspection.reason == "stored_version_newer"
    old_tree = old_inspection.to_tree()
    initializer_before = tuple(document_calls)
    call_before, commits = calls.checkpoint(), len(rig.storage.commits)
    assert scheduler.orphan_task_too_old(target) == "orphaned"
    assert_too_old_terminal(rig, target, before)
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
    assert tuple(document_calls) == initializer_before
    assert calls.checkpoint() == call_before
    assert rig.record(other) == unrelated
    assert current_documents(rig, other) == other_documents
    assert rig.session.snapshot(other_family, task_id=other, key="0") is not None
    assert rig.session.snapshot(
        token, conversation_id=rig.conversation
    ).value.thaw() == {"saved": 2}
    assert old_inspection.to_tree() == old_tree
    assert scheduler.inspect_task(target).status == "terminal"
    if rig.backend == "sqlite":
        rows, revisions = sqlite_document_rows(rig.path, ids)
        assert len(rows) == 257 and all(row[1] is not None for row in rows)
        assert revisions == ()
        assert sqlite_document_rows(rig.path, {history_id}) == history
    assert_terminal_replay(rig, scheduler, target)


@pytest.mark.parametrize(
    "edge", ["terminal-child", "owned-conversation", "incoming-background-waiter"]
)
def test_later_page_disqualifier_is_seen_with_present_older_code(
    rig, monkeypatch, edge
):
    target, older, calls = created_target(rig, "too-old.later-page")
    filler = definition("too-old.graph.filler")
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
    scheduler = owner(rig, [older])
    if edge == "incoming-background-waiter":
        edge_id = tasks[-1]
        # Typed native WAITING control AFTER recovery; all identities/owners are Tx-made.
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
    before, call_before = rig.silence(), calls.checkpoint()
    with pytest.raises(UnsupportedTaskExecution, match=f"^{GRAPH_REFUSAL}$"):
        scheduler.orphan_task_too_old(target)
    scans.assert_complete_multiple_pages()
    assert any(page[1] is not None and edge_id in page[3] for page in scans.pages)
    rig.assert_silent(before)
    assert calls.checkpoint() == call_before


def test_large_valid_graph_is_neighbor_positive_for_the_same_older_predicate(
    rig, monkeypatch
):
    calls = TargetCalls("too-old.background")
    stored, older = calls.declaration(2), calls.declaration(1)
    target = rig.session.commit(
        lambda tx: tx.create_task(
            stored,
            None,
            ownership={"kind": "conversation"},
            conversation_id=rig.conversation,
            background=True,
        )
    )
    populate_large_graph(rig, definition("too-old.unrelated.graph"))
    scheduler = owner(rig, [older])
    scans = GraphScans(rig, monkeypatch)
    prior, call_before = rig.record(target), calls.checkpoint()
    assert prior["background"] is True
    assert scheduler.orphan_task_too_old(target) == "orphaned"
    scans.assert_complete_multiple_pages()
    assert_too_old_terminal(rig, target, prior)
    assert calls.checkpoint() == call_before


@pytest.mark.parametrize("enabled", [False, True], ids=["paused", "enabled-retired"])
def test_actual_retirement_then_explicit_action_preserves_enable_and_target_silence(
    rig, monkeypatch, enabled
):
    target, older, calls = created_target(rig, "too-old.quiescent")
    scheduler = owner(rig, [older])
    passes = Passes(scheduler, monkeypatch)
    if enabled:
        resume_retired(scheduler, passes)
    idle(scheduler)
    fits = FitCalls(scheduler, target, monkeypatch)
    wakes = Wakes(scheduler, monkeypatch)
    before, call_before = rig.record(target), calls.checkpoint()
    before_wake = wakes.checkpoint()
    assert scheduler.orphan_task_too_old(target) == "orphaned"
    with scheduler._guard:
        assert scheduler._enabled is enabled
    assert wakes.checkpoint() == before_wake + 1
    idle(scheduler)
    assert fits.calls == [] and calls.checkpoint() == call_before
    assert_too_old_terminal(rig, target, before)
    pause(scheduler)
    before_wake = wakes.checkpoint()
    assert_terminal_replay(rig, scheduler, target)
    assert wakes.checkpoint() == before_wake


def test_real_drain_refuses_even_when_the_session_line_is_available(rig, monkeypatch):
    target, older, calls = created_target(rig, "too-old.draining")
    scheduler = owner(rig, [older])
    with held_real_drain(scheduler, monkeypatch):
        before, call_before = rig.silence(), calls.checkpoint()
        with pytest.raises(RuntimeError, match="Task scheduler is busy"):
            scheduler.orphan_task_too_old(target)
        rig.assert_silent(before)
        assert calls.checkpoint() == call_before


def test_real_service_admission_during_scan_wins_the_final_gate(rig, monkeypatch):
    target, older, calls = created_target(rig, "too-old.final-admission")
    scheduler = owner(rig, [older])
    entered, release = threading.Event(), threading.Event()
    inner = rig.storage.inner
    original = type(inner).scan_tasks

    def observed(actual, *args, **kwargs):
        page = original(actual, *args, **kwargs)
        if actual is inner and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return page

    monkeypatch.setattr(type(inner), "scan_tasks", observed)
    before, call_before = rig.silence(), calls.checkpoint()
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(scheduler.orphan_task_too_old, target)
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
    assert calls.checkpoint() == call_before


def test_actual_unrelated_invocation_blocks_control_without_target_mutation(rig):
    entered, release = threading.Event(), threading.Event()

    async def held(_row, runtime, _context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    active = definition("too-old.actual-active", phase=held)
    rig.create(active)
    target, older, calls = created_target(rig, "too-old.busy-target")
    scheduler = owner(rig, [active, older])
    scheduler.resume()
    try:
        assert entered.wait(5)
        with scheduler._guard:
            assert scheduler._invocation is not None
        before, call_before = rig.silence(), calls.checkpoint()
        with pytest.raises(RuntimeError, match="Task scheduler is busy"):
            scheduler.orphan_task_too_old(target)
        rig.assert_silent(before)
        assert calls.checkpoint() == call_before
    finally:
        release.set()
        pause(scheduler)
    assert rig.record(target)["state"]["status"] == "pending"


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cold_pending_v2_reopens_with_real_v1_without_dispatch(rig):
    target, older, calls = created_target(rig, "too-old.cold")
    task_documents(rig, target, 3, [])
    pending, call_before = rig.record(target), calls.checkpoint()
    scheduler = reopen_sqlite(rig, [older])
    assert rig.record(target) == pending
    assert calls.checkpoint() == call_before
    assert scheduler.orphan_task_too_old(target) == "orphaned"
    terminal = assert_too_old_terminal(rig, target, pending)
    scheduler = reopen_sqlite(rig, [calls.declaration(3)])
    assert rig.record(target) == terminal
    assert_terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == call_before


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_real_precommit_fault_rolls_back_all_documents_and_revisions(rig, monkeypatch):
    target, older, calls = created_target(rig, "too-old.rollback")
    task_documents(rig, target, 3, [])
    unrelated_history(rig)
    scheduler = owner(rig, [older])
    before, call_before = sqlite_rows(rig.path), calls.checkpoint()
    entered = []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("too-old-before-durable-commit")

    monkeypatch.setattr(sqlite, "_before_commit", fail)
    with pytest.raises(RuntimeError, match="too-old-before-durable-commit"):
        scheduler.orphan_task_too_old(target)
    assert entered == [True]
    assert sqlite_rows(rig.path) == before
    assert calls.checkpoint() == call_before


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_actual_commit_before_lost_ack_reopens_one_terminal_winner(rig, monkeypatch):
    target, older, calls = created_target(rig, "too-old.lost-ack")
    _, documents = task_documents(rig, target, 3, [])
    token, unrelated_id = unrelated_history(rig)
    scheduler = owner(rig, [older])
    prior, call_before = rig.record(target), calls.checkpoint()
    history = sqlite_document_rows(rig.path, {unrelated_id})
    entered = []

    def fail(connection):
        entered.append(connection.in_transaction)
        raise RuntimeError("too-old-durable-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite, "_after_commit", fail)
        with pytest.raises(RuntimeError, match="too-old-durable-ack-lost"):
            scheduler.orphan_task_too_old(target)
    assert entered == [False]
    scheduler = reopen_sqlite(rig, [older])
    assert_too_old_terminal(rig, target, prior)
    before = sqlite_rows(rig.path)
    assert_terminal_replay(rig, scheduler, target)
    assert sqlite_rows(rig.path) == before
    assert rig.session.read_on_line(
        lambda: all(rig.storage.document(row.id) is None for row in documents)
    )
    assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    assert rig.session.snapshot(
        token, conversation_id=rig.conversation
    ).value.thaw() == {"saved": 2}
    assert calls.checkpoint() == call_before


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_no_write_oracle_detects_revision_only_change(rig):
    token, _id = unrelated_history(rig)
    before = sqlite_rows(rig.path)

    def set_value(tx, value):
        tx.doc(token, conversation_id=rig.conversation)["saved"] = value

    rig.session.commit(lambda tx: set_value(tx, 99))
    rig.session.commit(lambda tx: set_value(tx, 2))
    after = sqlite_rows(rig.path)
    assert before["documents"] == after["documents"]
    assert before["document_revisions"] != after["document_revisions"]


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancel_close_join_committed_worker_before_actual_lease_release(rig, tmp_path):
    target, older, calls = created_target(rig, "too-old.cancel-close")
    _, documents = task_documents(rig, target, 3, [])
    scheduler = owner(rig, [older])
    call_before = calls.checkpoint()
    hold = CommitHold(rig.storage, "after")

    async def scenario():
        waiter = asyncio.create_task(scheduler.aorphan_task_too_old(target))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            rig.session.request_close()
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None
            opener(rig.path, tmp_path, "too-old-commit-held", "refused")
        finally:
            hold.release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await asyncio.to_thread(hold.retired.wait, 5)

    asyncio.run(scenario())
    rig.session.wait_closed(timeout=10)
    assert len(hold.calls) == 1 and rig.storage.closed.is_set()
    assert not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "too-old-commit-retired", "opened")
    scheduler = reopen_sqlite(rig, [older])
    assert rig.record(target)["state"]["outcome"] == {
        "status": "orphaned",
        "reason": "task_too_old",
    }
    assert rig.session.read_on_line(
        lambda: all(rig.storage.document(row.id) is None for row in documents)
    )
    assert_terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == call_before


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_loop_shutdown_keeps_lease_until_the_accepted_commit_worker_is_joined(
    rig, tmp_path
):
    target, older, calls = created_target(rig, "too-old.loop-shutdown")
    scheduler = owner(rig, [older])
    hold = CommitHold(rig.storage, "after")
    cancelled, exited = threading.Event(), threading.Event()
    call_before = calls.checkpoint()

    def caller():
        async def scenario():
            waiter = asyncio.create_task(scheduler.aorphan_task_too_old(target))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
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
            opener(rig.path, tmp_path, "too-old-loop-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=10)
    assert exited.is_set() and hold.retired.is_set()
    assert len(hold.calls) == 1 and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "too-old-loop-retired", "opened")
    scheduler = reopen_sqlite(rig, [older])
    assert rig.record(target)["state"]["outcome"] == {
        "status": "orphaned",
        "reason": "task_too_old",
    }
    assert_terminal_replay(rig, scheduler, target)
    assert calls.checkpoint() == call_before
