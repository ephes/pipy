"""DE16a ordinary-leaf abort execution and recovery tests.

Uses real Native Session/Tx/process witnesses; no Pi or cascade claim.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import Passes, pause
from ordinary_abort_support import (
    ABORTED,
    MISSING,
    AbortCalls,
    MarkCommitCut,
    MissingCommitCut,
    assert_missing_retirement,
    cold,
    mark,
    missing_target,
    paused_target,
    physical_cut,
    real_pass,
    target_batches,
)
from orphan_missing_definition_support import (
    CommitHold,
    current_documents,
    sqlite_document_rows,
    task_documents,
)
from restart_policy_live_support import Tickets, completing, held_line, idle
from task_migration_support import Rig, opener, sqlite_rows

from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.storage import ConversationQuery, TaskQuery


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in (
        "HOME",
        "PIPY_CONFIG_HOME",
        "PIPY_AUTH_DIR",
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
        value.storage.before_commit = value.storage.after_commit = None
        value.close()


def test_mark_ACK_is_real_intent_without_dispatch_and_repeat_is_silent(
    rig, monkeypatch
):
    target, _, calls, scheduler = paused_target(rig)
    prior, before = rig.record(target), rig.silence()
    assert mark(scheduler, target) == "marked"
    marked = rig.record(target)
    assert marked["abortRequested"] is True
    assert marked["state"] == prior["state"] and marked["version"] == prior["version"]
    assert len(rig.storage.commits) == before[1] + 1
    assert calls.snapshot() == (("initial", "A"),)
    with scheduler._guard:
        assert not scheduler._enabled and not scheduler._draining
        assert scheduler._invocation is None
    quiet = rig.silence()
    assert mark(scheduler, target) == "marked"
    rig.assert_silent(quiet)
    real_pass(scheduler, monkeypatch)
    assert rig.record(target)["state"] == ABORTED
    assert calls.snapshot() == (("initial", "A"), ("abort", "A", 1, None))
    quiet = rig.silence()
    assert mark(scheduler, target) == "terminal"
    rig.assert_silent(quiet)


@pytest.mark.parametrize("first", ["mark", "definition"])
def test_definition_and_request_take_actual_FIFO_order(rig, monkeypatch, first):
    target, _, calls, scheduler = paused_target(rig)
    replacement = calls.declaration(2, "new-version")
    tickets = Tickets(rig.session, monkeypatch)
    actions = {
        "mark": lambda: mark(scheduler, target),
        "definition": lambda: scheduler.install_definition(replacement),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(actions[first])}
            tickets.wait(1)
            second = "definition" if first == "mark" else "mark"
            jobs[second] = pool.submit(actions[second])
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0]
        assert jobs["definition"].result(5)
        if first == "mark":
            assert jobs["mark"].result(5) == "marked"
        else:
            with pytest.raises(UnsupportedTaskExecution):
                jobs["mark"].result(5)
    row = rig.record(target)
    assert row["abortRequested"] is (first == "mark")
    quiet = rig.silence()
    with pytest.raises(UnsupportedTaskExecution):
        mark(scheduler, target)
    rig.assert_silent(quiet)
    assert calls.snapshot() == (("initial", "A"),)


def _graph(rig):
    def capture():
        tasks = rig.storage.scan_tasks(TaskQuery({}), 100)
        conversations = rig.storage.scan_conversations(ConversationQuery({}), 100)
        assert tasks.next is None and conversations.next is None
        return (
            tuple(row.to_tree() for row in tasks.items),
            tuple(row.to_tree() for row in conversations.items),
        )

    return rig.session.read_on_line(capture)


@pytest.mark.parametrize("edge", ["child", "conversation", "incoming-waiter"])
@pytest.mark.parametrize("first", ["mark", "edge"])
def test_late_complete_graph_is_on_the_same_request_line(rig, monkeypatch, edge, first):
    target, source, calls, scheduler = paused_target(rig)
    other = rig.create(source)

    def add_edge():
        def change(tx):
            if edge == "child":
                return tx.create_task(
                    source, None, ownership={"kind": "task", "taskId": target}
                )
            if edge == "conversation":
                return tx.create_conversation({"kind": "task", "taskId": target})
            current = tx.task(other).to_tree()
            current["state"] = {
                "status": "waiting",
                "checkpoint": {"phase": "start"},
                "on": [target],
                "policy": "allSettled",
            }
            tx.set_task(current)

        return rig.session.commit(change)

    before_tasks, before_conversations = _graph(rig)
    rejected_creation = first == "mark" and edge in ("child", "conversation")
    tickets = Tickets(rig.session, monkeypatch)
    actions = {"mark": lambda: mark(scheduler, target), "edge": add_edge}
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(actions[first])}
            tickets.wait(1)
            second = "edge" if first == "mark" else "mark"
            jobs[second] = pool.submit(actions[second])
            tickets.wait(2)
        if rejected_creation:
            assert jobs["mark"].result(5) == "marked"
            owner_label = "Task owner" if edge == "child" else "Conversation owner task"
            with pytest.raises(
                RuntimeError, match=rf"^{owner_label} {target} is abort-marked$"
            ):
                jobs["edge"].result(5)
            tasks, conversations = _graph(rig)
            assert tasks == tuple(
                {**row, "abortRequested": True} if row["id"] == target else row
                for row in before_tasks
            )
            assert conversations == before_conversations
            # Only published graph rows are compared; no mint/initial rollback claim.
        else:
            jobs["edge"].result(5)
            if first == "edge":
                with pytest.raises(UnsupportedTaskExecution):
                    jobs["mark"].result(5)
            else:
                assert jobs["mark"].result(5) == "marked"
    quiet = rig.silence()
    if rejected_creation:
        assert mark(scheduler, target) == "marked"
    else:
        with pytest.raises(UnsupportedTaskExecution):
            mark(scheduler, target)
    rig.assert_silent(quiet)
    assert rig.record(target)["state"]["status"] == "pending"
    initial = (("initial", "A"),) * (3 if edge == "child" else 2)
    assert calls.snapshot() == initial
    if rejected_creation:
        # Both existing leaves take the genuine abort path; no ordinary phase runs.
        assert mark(scheduler, other) == "marked"
        real_pass(scheduler, monkeypatch)
        assert rig.record(target)["state"] == ABORTED
        assert rig.record(other)["state"] == ABORTED
        assert calls.snapshot() == initial + (("abort", "A", 1, None),) * 2
    # This is typed transaction topology, explicitly not physical death evidence.


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_actual_mark_COMMIT_failure_or_ACK_loss_has_cold_authority(
    rig, monkeypatch, cut
):
    target, source, calls, scheduler = paused_target(rig)
    before = sqlite_rows(rig.path)
    hook = MarkCommitCut(rig, target, cut)
    with monkeypatch.context() as patch:
        hook.install(patch)
        with pytest.raises(RuntimeError):
            mark(scheduler, target)
    assert hook.fired == [cut == "before"]
    assert calls.snapshot() == (("initial", "A"),)
    if cut == "before":
        assert sqlite_rows(rig.path) == before
    rig.close()
    reopened, owner = cold(rig.path, target, [source])
    try:
        row = reopened.record(target)
        assert row["abortRequested"] is (cut == "after")
        assert row["state"]["status"] == "pending" and "restarts" not in row
        assert calls.snapshot() == (("initial", "A"),)
    finally:
        reopened.close()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancelled_mark_waiter_and_close_join_worker_before_lease_release(
    rig, tmp_path
):
    target, _, calls, scheduler = paused_target(rig)
    hold = CommitHold(rig.storage, "after")
    cancelled, exited = threading.Event(), threading.Event()

    def caller():
        async def scenario():
            waiter = asyncio.create_task(scheduler.aabort_leaf(target))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            cancelled.set()
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
            opener(rig.path, tmp_path, "abort-mark-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=10)
    assert hold.retired.is_set() and exited.is_set() and len(hold.calls) == 1
    opener(rig.path, tmp_path, "abort-mark-retired", "opened")
    assert calls.snapshot() == (("initial", "A"),)


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_close_joins_real_abort_finally_before_backend_and_opener(rig, tmp_path):
    calls = AbortCalls(stop=True)
    calls.finalizer_release.clear()
    target, _, _, scheduler = paused_target(rig, calls)
    assert mark(scheduler, target) == "marked"
    scheduler.resume()
    assert calls.entered.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        closed = pool.submit(rig.session.close, timeout=10)
        try:
            assert calls.finalizing.wait(5)
            assert not calls.finalized.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None
            opener(rig.path, tmp_path, "abort-finally-held", "refused")
        finally:
            calls.finalizer_release.set()
        closed.result(10)
    assert calls.finalized.is_set() and rig.storage.closed.is_set()
    opener(rig.path, tmp_path, "abort-finally-retired", "opened")
    final = next(row.to_tree() for row in rig.storage.final_tasks if row.id == target)
    assert final["state"]["status"] == "pending" and final["abortRequested"]
    assert "restarts" not in final


@pytest.mark.parametrize("cut", ["marked", "reserved", "entered"])
def test_distinct_real_cold_boundaries_never_claim_reserved_as_entry(
    tmp_path, monkeypatch, cut
):
    path, target, prior, _rows, entries = physical_cut(tmp_path / cut, cut)
    assert prior["abortRequested"]
    assert prior["state"]["status"] == ("pending" if cut == "marked" else "running")
    assert entries == (["abort"] if cut == "entered" else [])
    calls = AbortCalls("ordinary.abort.physical")
    source = calls.declaration()
    for _ in range(2):
        rig, scheduler = cold(path, target, [source])
        try:
            row = rig.record(target)
            assert row["state"]["status"] == "pending" and row["abortRequested"]
            assert row.get("restarts") == (None if cut == "marked" else 1)
            assert row["state"]["checkpoint"] == {"phase": "start", "saved": [1, None]}
            assert calls.snapshot() == ()
        finally:
            rig.close()
    rig, scheduler = cold(path, target, [source])
    try:
        quiet = rig.silence()
        assert mark(scheduler, target) == "marked"
        rig.assert_silent(quiet)
        real_pass(scheduler, monkeypatch)
        assert rig.record(target)["state"] == ABORTED
        assert calls.snapshot() == (("abort", "A", 1, None if cut == "marked" else 1),)
        quiet = rig.silence()
        assert mark(scheduler, target) == "terminal"
        rig.assert_silent(quiet)
    finally:
        rig.close()


@pytest.mark.parametrize("version", [0, 2])
def test_interrupted_abort_below_limit_holds_until_exact_version_restored(
    tmp_path, monkeypatch, version
):
    path, target, _, _, _ = physical_cut(tmp_path / "interrupted", "entered")
    calls = AbortCalls("ordinary.abort.physical")
    exact = calls.declaration(1, "restored")
    definitions = [calls.declaration(version, "drifted")]
    rig, scheduler = cold(path, target, definitions, limit=3)
    try:
        before = rig.record(target)
        assert before["restarts"] == 1
        neighbor_events = []
        neighbor = completing(neighbor_events, "ordinary.abort.neighbor")
        neighbor_id = rig.create(neighbor)
        assert scheduler.install_definition(neighbor)
        for _ in range(2):
            real_pass(scheduler, monkeypatch)
            assert rig.record(target) == before and calls.snapshot() == ()
            assert neighbor_events == [(neighbor_id, None)]
            assert rig.record(neighbor_id)["state"]["status"] == "terminal"
            quiet = rig.silence()
            with pytest.raises(UnsupportedTaskExecution):
                mark(scheduler, target)
            rig.assert_silent(quiet)
        assert scheduler.install_definition(exact)
        real_pass(scheduler, monkeypatch)
        assert rig.record(target)["state"] == ABORTED
        assert calls.snapshot() == (("abort", "restored", 1, 1),)
    finally:
        rig.close()


def test_real_run_interruption_counter_is_removed_only_by_first_abort_mark(tmp_path):
    path, target, _, _, entries = physical_cut(tmp_path / "run", "run-entered")
    assert entries == ["run"]
    calls = AbortCalls("ordinary.abort.physical")
    rig, scheduler = cold(path, target, [calls.declaration()])
    try:
        assert rig.record(target)["restarts"] == 1
        assert not rig.record(target)["abortRequested"]
        assert mark(scheduler, target) == "marked"
        assert "restarts" not in rig.record(target)
        assert calls.snapshot() == ()
    finally:
        rig.close()


def test_same_version_future_capture_preserves_busy_publication_fence(rig, monkeypatch):
    calls = AbortCalls()
    calls.release.clear()
    target, _, _, scheduler = paused_target(rig, calls)
    replacement = calls.declaration(1, "B")
    assert mark(scheduler, target) == "marked"
    scheduler.resume()
    try:
        assert calls.entered.wait(5)
        with pytest.raises(RuntimeError, match="busy"):
            scheduler.install_definition(replacement)
        assert calls.snapshot() == (("initial", "A"), ("abort", "A", 1, None))
    finally:
        calls.release.set()
    idle(scheduler)
    pause(scheduler)
    assert rig.record(target)["state"] == ABORTED
    second = rig.create(replacement)
    assert scheduler.install_definition(replacement)
    assert mark(scheduler, second) == "marked"
    real_pass(scheduler, monkeypatch)
    assert rig.record(second)["state"] == ABORTED
    assert [event[:2] for event in calls.snapshot() if event[0] == "abort"] == [
        ("abort", "A"),
        ("abort", "B"),
    ]


@pytest.mark.parametrize("limit", [1, float("inf")], ids=["finite-cutoff", "unbounded"])
def test_genuine_interrupted_attempt_cutoff_precedes_missing_code(
    tmp_path, monkeypatch, limit
):
    path, target, _, _, entries = physical_cut(tmp_path / "cutoff", "entered")
    assert entries == ["abort"]
    calls = AbortCalls("ordinary.abort.physical")
    # Missing definition in the exhausted finite case is deliberate. Infinity
    # has no fabricated cutoff; exact code can perform a real second attempt.
    definitions = [] if limit == 1 else [calls.declaration()]
    rig, scheduler = cold(path, target, definitions, limit=limit)
    try:
        assert rig.record(target)["restarts"] == 1
        real_pass(scheduler, monkeypatch)
        row = rig.record(target)
        if limit == 1:
            outcome = row["state"]["outcome"]
            assert row["state"]["status"] == "terminal"
            assert outcome["status"] == "faulted"
            assert outcome["error"]["detail"]["reason"] == "restart_limit"
            assert calls.snapshot() == ()
        else:
            assert row["state"] == ABORTED
            assert calls.snapshot() == (("abort", "A", 1, 1),)
    finally:
        rig.close()


@pytest.mark.parametrize("cut", ["marked", "reserved", "entered"])
def test_missing_code_after_real_cut_waits_for_enable_then_retires(
    tmp_path, monkeypatch, cut
):
    path, target, prior, _rows, entries = physical_cut(tmp_path / cut, cut)
    assert prior["state"]["status"] == ("pending" if cut == "marked" else "running")
    assert prior["abortRequested"] is True
    assert entries == (["abort"] if cut == "entered" else [])
    effects = path.parent / "effects.txt"
    original_effects = effects.read_bytes() if effects.exists() else None
    recovered = None
    for _ in range(2):
        rig, scheduler = cold(path, target, [], limit=3)
        try:
            row = rig.record(target)
            assert row["state"] == {
                "status": "pending",
                "checkpoint": {"phase": "start", "saved": [1, None]},
            }
            assert row["abortRequested"] is True
            assert row.get("restarts") == (None if cut == "marked" else 1)
            if recovered is not None:
                assert row == recovered
            recovered = row
            quiet = rig.silence()
            with pytest.raises(UnsupportedTaskExecution):
                mark(scheduler, target)
            rig.assert_silent(quiet)
        finally:
            rig.close()
    rig, scheduler = cold(path, target, [], limit=3)
    try:
        assert rig.record(target) == recovered
        doc_calls = []
        _, documents = task_documents(rig, target, 2, doc_calls)
        rig.change(target, lambda row: row.update(memos={"saved": {"retained": True}}))
        before = rig.record(target)
        assert before["state"] == recovered["state"]
        assert before.get("restarts") == recovered.get("restarts")
        ids = {document.id for document in documents}
        document_rows, revisions = sqlite_document_rows(path, ids)
        assert all(row[1] is None for row in document_rows)
        assert {row[0] for row in revisions} == ids
        first_commit = len(rig.storage.commits)
        real_pass(scheduler, monkeypatch)
        terminal = assert_missing_retirement(
            rig, target, before, documents, first_commit
        )
        retired, after_revisions = sqlite_document_rows(path, ids)
        assert {row[0] for row in retired} == ids
        assert all(row[1] is not None for row in retired)
        # Task documents are current-only: retirement deletes their revisions.
        assert after_revisions == ()
        assert doc_calls == [("initial", 0), ("initial", 1)]
        calls = AbortCalls("ordinary.abort.physical")
        assert scheduler.install_definition(calls.declaration())
        quiet = rig.silence()
        assert mark(scheduler, target) == "terminal"
        real_pass(scheduler, monkeypatch)
        assert rig.record(target) == terminal and calls.snapshot() == ()
        rig.assert_silent(quiet)
        assert (effects.read_bytes() if effects.exists() else None) == original_effects
    finally:
        rig.close()


@pytest.mark.parametrize("first", ["restoration", "decision"])
def test_missing_decision_and_idle_restoration_have_ordered_winners(
    rig, monkeypatch, first
):
    target, source, calls, scheduler, documents = missing_target(rig)
    before = rig.record(target)
    first_commit = len(rig.storage.commits)
    tickets = Tickets(rig.session, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        if first == "decision":
            # This is the actual service's queued reservation transaction.
            passes = Passes(scheduler, monkeypatch)
            checkpoint = passes.checkpoint()
            with held_line(rig.session, pool):
                scheduler.resume()
                tickets.wait(1)
            passes.retired_after(checkpoint)
            pause(scheduler)
            assert_missing_retirement(rig, target, before, documents, first_commit)
        with held_line(rig.session, pool):
            publication = pool.submit(scheduler.install_definition, source)
            tickets.wait(2 if first == "decision" else 1)
        assert publication.result(5)
    # No publication was asserted admissible while the scheduler was draining.
    real_pass(scheduler, monkeypatch)
    if first == "restoration":
        assert rig.record(target)["state"] == ABORTED
        assert calls.snapshot() == (("initial", "A"), ("abort", "A", 1, None))
    else:
        assert rig.record(target)["state"] == MISSING
        assert calls.snapshot() == (("initial", "A"),)
    assert current_documents(rig, target) == ()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_missing_decision_COMMIT_failure_has_cold_durable_authority(
    rig, monkeypatch, cut
):
    target, _source, calls, scheduler, documents = missing_target(rig)
    before = rig.record(target)
    durable_before = sqlite_rows(rig.path)
    first_commit = len(rig.storage.commits)
    hook = MissingCommitCut(rig, target, cut)
    with monkeypatch.context() as patch:
        hook.install(patch)
        scheduler.resume()
        rig.session.wait_closed(timeout=10)
    assert hook.fired == [cut == "before"]
    assert type(rig.session.failure) is RuntimeError
    assert str(rig.session.failure) == (
        "ordinary-abort-before-COMMIT" if cut == "before" else "ordinary-abort-ACK-lost"
    )
    assert calls.snapshot() == (("initial", "A"),)
    batches = target_batches(rig, target, first_commit)
    assert len(batches) == 1
    assert [
        write["value"]["state"]
        for write in batches[0]
        if write["type"] == "task" and write["value"]["id"] == target
    ] == [MISSING]
    for document in documents:
        assert {"type": "document.retire", "id": document.id} in batches[0]
    if cut == "before":
        assert sqlite_rows(rig.path) == durable_before
    rig.close()
    reopened, owner = cold(rig.path, target, [])
    try:
        row = reopened.record(target)
        if cut == "before":
            assert row == before
            assert {doc.id for doc in current_documents(reopened, target)} == {
                doc.id for doc in documents
            }
            first_commit = len(reopened.storage.commits)
            real_pass(owner, monkeypatch)
            assert_missing_retirement(reopened, target, before, documents, first_commit)
        else:
            assert row["state"] == MISSING and row["abortRequested"]
            assert "memos" not in row and "restarts" not in row
            assert current_documents(reopened, target) == ()
            quiet = reopened.silence()
            real_pass(owner, monkeypatch)
            reopened.assert_silent(quiet)
        assert calls.snapshot() == (("initial", "A"),)
    finally:
        reopened.close()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancelled_reader_does_not_cancel_missing_decision_or_close_join(
    rig, monkeypatch, tmp_path
):
    target, _source, calls, scheduler, documents = missing_target(rig)
    before = rig.record(target)
    first_commit = len(rig.storage.commits)
    hold = CommitHold(rig.storage, "after")
    tickets = Tickets(rig.session, monkeypatch)
    read_entered, cancelled = threading.Event(), threading.Event()

    def reader():
        async def scenario():
            waiting = asyncio.create_task(rig.session.aread_on_line(read_entered.set))
            await asyncio.to_thread(tickets.wait, 1)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            cancelled.set()

        asyncio.run(scenario())

    scheduler.resume()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert hold.entered.wait(5)
            work = pool.submit(reader)
            work.result(5)
            assert cancelled.is_set() and not read_entered.is_set()
            assert len(hold.calls) == 1
            batches = target_batches(rig, target, first_commit)
            assert len(batches) == 1
            assert [
                write["value"]["state"]
                for write in batches[0]
                if write["type"] == "task" and write["value"]["id"] == target
            ] == [MISSING]
            rig.session.request_close()
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None
            opener(rig.path, tmp_path, "missing-decision-held", "refused")
        finally:
            hold.release.set()
    rig.session.wait_closed(timeout=10)
    assert hold.retired.is_set() and rig.storage.closed.is_set()
    assert not scheduler._thread.is_alive() and rig.session.failure is None
    opener(rig.path, tmp_path, "missing-decision-retired", "opened")
    assert calls.snapshot() == (("initial", "A"),)
    reopened, owner = cold(rig.path, target, [])
    try:
        final = reopened.record(target)
        assert final["state"] == MISSING and final["abortRequested"]
        assert final["state"] != before["state"]
        assert "memos" not in final and "restarts" not in final
        assert current_documents(reopened, target) == ()
        ids = {doc.id for doc in documents}
        rows, _revisions = sqlite_document_rows(rig.path, ids)
        assert {row[0] for row in rows} == ids
        assert all(row[1] is not None for row in rows)
        quiet = reopened.silence()
        real_pass(owner, monkeypatch)
        reopened.assert_silent(quiet)
    finally:
        reopened.close()
