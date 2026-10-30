"""Terminal-only ordinary aborts through actual marking, callbacks and transactions.

Counter boundary rows below are explicitly typed kernel controls, not physical
process-death evidence. Real interruption/cold-cut ownership belongs to Broad.
"""

import asyncio
import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from owned_completing_support import DONE, Passes, States, make_child
from task_migration_support import ObservedStorage, Rig

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session import SessionClosedError, SessionFailed
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.tasks import define_task

CHECKPOINT = {"phase": "start", "saved": [1, None, {"negative": -0.0}]}
MISSING = {
    "status": "terminal",
    "outcome": {"status": "orphaned", "reason": "missing_task"},
}


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for key in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("PIPY_THEME", "pi")
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.storage.before_commit = value.storage.after_commit = None
        value.close()


async def forbidden(*_args):
    raise AssertionError("No run phase or excluded handler may execute")


async def complete(_row, runtime, _context):
    await runtime.commit(lambda _tx, _current: DONE)


def definition(abort=complete, *, name="ordinary.abort", version=1):
    return define_task(name, version, lambda _: CHECKPOINT, {"start": forbidden}, abort)


def owner(rig, definitions, monkeypatch, *, limit=5):
    scheduler = TaskScheduler(rig.session, definitions, restart_limit=limit)
    rig.scheduler = scheduler
    passes = Passes(scheduler, monkeypatch)
    scheduler.open()
    return scheduler, passes


def mark(scheduler, id):
    return asyncio.run(scheduler.aabort_leaf(id))


def test_real_mark_cleanup_terminal_and_frozen_capture(rig, monkeypatch):
    calls = []
    token = define_doc(
        "abort-cleanup",
        1,
        "task",
        lambda: {"value": 1},
        checkpoint_when=lambda _v, _ops, _info: False,
    )

    async def abort(row, runtime, _context):
        calls.append((row["state"]["checkpoint"], runtime))
        assert row["abortRequested"] and row["state"]["status"] == "running"
        assert runtime._invocation.execution.mode == "abort"
        with pytest.raises(dataclasses.FrozenInstanceError):
            runtime._invocation.execution.mode = "run"
        assert await runtime.memo("saved") == {"retained": True}
        assert await runtime.memo("cleanup", "actual") == "actual"

        async def clean(tx, _current):
            document = await tx.doc(token)
            document["value"] = 3

        await runtime.commit(clean)
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)

    def seed(tx):
        tx.doc(token, task_id=target)["value"] = 2
        row = tx.task(target).to_tree()
        tx.set_task({**row, "memos": {"saved": {"retained": True}}})

    rig.session.commit(seed)
    address = DocumentAddress(
        {"kind": token.kind, "scope": {"kind": "task", "taskId": target}}
    )
    doc_id = rig.session.read_on_line(lambda: rig.storage.find_document(address).id)
    scheduler, passes = owner(rig, [task], monkeypatch)
    states = States(rig)
    assert mark(scheduler, target) == "marked"
    assert calls == [] and not scheduler._enabled
    assert rig.record(target)["state"] == {
        "status": "pending",
        "checkpoint": CHECKPOINT,
    }
    silent = rig.silence()
    assert mark(scheduler, target) == "marked"
    rig.assert_silent(silent)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert len(calls) == 1 and calls[0][0] == CHECKPOINT
    assert "memos" not in rig.record(target)
    assert rig.session.read_on_line(lambda: rig.storage.document(doc_id)) is None
    silent = rig.silence()
    assert mark(scheduler, target) == "terminal"
    rig.assert_silent(silent)
    with pytest.raises(RuntimeError, match="ended"):
        asyncio.run(calls[0][1].memo("escaped", True))
    states.unsubscribe()


@pytest.mark.parametrize(
    "outcome", ["throw", "no-outcome", "no-state-only", "terminal-then-throw"]
)
def test_real_callback_failure_and_terminal_precedence(rig, monkeypatch, outcome):
    calls = []

    async def abort(_row, runtime, _context):
        calls.append(outcome)
        if outcome == "no-state-only":
            await runtime.memo("observed", True)
            await runtime.commit(lambda _tx, _current: None)
        if outcome == "terminal-then-throw":
            await runtime.commit(lambda _tx, _current: DONE)
        if outcome in {"throw", "terminal-then-throw"}:
            raise ValueError("actual-abort-error")

    task = definition(abort)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    states = States(rig)
    mark(scheduler, target)
    scheduler.resume()
    row = states.wait(target, "terminal")
    passes.idle()
    assert calls == [outcome]
    if outcome == "terminal-then-throw":
        assert row["state"] == DONE
    else:
        result = row["state"]["outcome"]
        assert result["status"] == "faulted"
        assert result["error"]["message"] == (
            "actual-abort-error"
            if outcome == "throw"
            else f"Abort handler of task {target} returned without a terminal outcome"
        )
    assert "memos" not in row and rig.session.failure is None
    states.unsubscribe()


@pytest.mark.parametrize(
    "state",
    [
        {"status": "running", "checkpoint": CHECKPOINT},
        {"status": "pending", "checkpoint": CHECKPOINT},
        {
            "status": "waiting",
            "checkpoint": CHECKPOINT,
            "on": [],
            "policy": "allSettled",
        },
        {"status": "waiting", "checkpoint": CHECKPOINT, "on": [], "policy": "failFast"},
        {"status": "sleeping", "checkpoint": CHECKPOINT, "until": 0},
        {"status": "completing", "outcome": DONE["outcome"]},
        pytest.param("terminal", id="scalar-string"),
        pytest.param([], id="array"),
        pytest.param({}, id="missing-status"),
    ],
)
def test_unsupported_next_state_rolls_back_then_caught_callback_can_finish(
    rig, monkeypatch, state
):
    caught = []

    async def abort(_row, runtime, _context):
        before = await asyncio.to_thread(rig.silence)
        with pytest.raises(UnsupportedTaskExecution, match="terminal outcome") as error:
            await runtime.commit(lambda _tx, _current: state)
        caught.append(error.value)
        await asyncio.to_thread(rig.assert_silent, before)
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    states = States(rig)
    mark(scheduler, target)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert len(caught) == 1
    states.unsubscribe()


def test_uncaught_transition_refusal_becomes_actual_fault(rig, monkeypatch):
    async def abort(_row, runtime, _context):
        await runtime.commit(
            lambda _tx, _current: {"status": "running", "checkpoint": CHECKPOINT}
        )

    task = definition(abort)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    states = States(rig)
    mark(scheduler, target)
    scheduler.resume()
    result = states.wait(target, "terminal")["state"]["outcome"]
    passes.idle()
    assert result == {
        "status": "faulted",
        "error": {"message": "Abort cleanup requires a terminal outcome"},
    }
    states.unsubscribe()


@pytest.mark.parametrize("change_kind", ["owned-child", "replace-self"])
def test_real_candidate_cannot_escape_leaf_or_remove_mark(
    rig, monkeypatch, change_kind
):
    child = definition(name="abort.child")
    minted = []
    caught = []

    async def abort(_row, runtime, _context):
        commits = len(rig.storage.commits)

        async def change(tx, current):
            if change_kind == "owned-child":
                minted.append(await tx.create_task(child, None))
            else:
                tx._tx.set_task({**current, "abortRequested": False})
            return None

        with pytest.raises(UnsupportedTaskExecution) as error:
            await runtime.commit(change)
        caught.append(error.value)
        assert len(rig.storage.commits) == commits
        for id in minted:
            assert (
                await asyncio.to_thread(
                    rig.session.read_on_line, lambda: rig.storage.task(id)
                )
                is None
            )
        assert (await asyncio.to_thread(rig.record, runtime.task_id))[
            "abortRequested"
        ] is True
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task, child], monkeypatch)
    states = States(rig)
    mark(scheduler, target)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert len(caught) == 1 and len(minted) == (change_kind == "owned-child")
    states.unsubscribe()


@pytest.mark.parametrize("drift", ["missing", "older", "newer"])
def test_repeat_after_drift_refuses_without_reset_and_hold_needs_exact_version(
    rig, monkeypatch, drift
):
    task = definition()
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    mark(scheduler, target)
    # Typed metadata boundary control, not a claimed prior abort invocation.
    rig.change(target, lambda row: row.update(restarts=2))
    if drift == "missing":
        scheduler.remove_definition(task.name)
    else:
        scheduler.install_definition(definition(version=0 if drift == "older" else 2))
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match="exact definition"):
        mark(scheduler, target)
    rig.assert_silent(before)
    if drift == "missing":
        states = States(rig)
        scheduler.resume()
        assert states.wait(target, "terminal")["state"] == MISSING
        passes.idle()
        assert "restarts" not in rig.record(target)
        scheduler.install_definition(definition(forbidden))
        before = rig.silence()
        assert mark(scheduler, target) == "terminal"
        scheduler.resume()
        passes.idle()
        rig.assert_silent(before)
        states.unsubscribe()
        return
    for _ in range(2):
        scheduler.resume()
        passes.idle()
        assert rig.record(target)["restarts"] == 2
        assert rig.record(target)["state"]["status"] == "pending"
    scheduler.install_definition(definition())
    before = rig.silence()
    assert mark(scheduler, target) == "marked"
    rig.assert_silent(before)
    states = States(rig)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    states.unsubscribe()


def test_first_mark_resets_run_budget_repeat_does_not_reset_abort_budget(
    rig, monkeypatch
):
    task = definition()
    target = rig.create(task)
    # Exact typed counter policy control; physical interruption is Broad-owned.
    rig.change(target, lambda row: row.update(restarts=100))
    scheduler, _passes = owner(rig, [task], monkeypatch)
    assert mark(scheduler, target) == "marked"
    assert "restarts" not in rig.record(target)
    rig.change(target, lambda row: row.update(restarts=3))
    before = rig.silence()
    assert mark(scheduler, target) == "marked"
    rig.assert_silent(before)
    assert rig.record(target)["restarts"] == 3


@pytest.mark.parametrize("limit", [2, float("inf")])
def test_typed_cutoff_precedes_missing_definition_but_infinity_does_not_fault(
    rig, monkeypatch, limit
):
    task = definition()
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch, limit=limit)
    mark(scheduler, target)
    rig.change(target, lambda row: row.update(restarts=2))
    scheduler.remove_definition(task.name)
    states = States(rig)
    scheduler.resume()
    passes.idle()
    row = rig.record(target)
    if limit == 2:
        assert row["state"]["outcome"]["status"] == "faulted"
        assert row["state"]["outcome"]["error"]["detail"] == {"reason": "restart_limit"}
        assert "restarts" not in row
    else:
        assert row["state"] == MISSING and "restarts" not in row
    states.unsubscribe()


def test_marked_missing_definition_retires_without_reservation(rig, monkeypatch):
    calls = []

    def initial(_value):
        calls.append("initial")
        return CHECKPOINT

    async def excluded(*_args):
        calls.append("unexpected")
        raise AssertionError("Missing-code settlement must not invoke a handler")

    task = define_task(
        "ordinary.missing-abort",
        1,
        initial,
        {"start": excluded},
        excluded,
        migrate=excluded,
    )
    target = rig.create(task)
    token = define_doc(
        "missing-abort-doc",
        1,
        "task",
        lambda: {"value": 1},
        checkpoint_when=lambda _v, _ops, _info: False,
    )

    def prepare(tx):
        tx.doc(token, task_id=target)["value"] = 2
        row = tx.task(target).to_tree()
        tx.set_task({**row, "memos": {"saved": {"retained": True}}})

    rig.session.commit(prepare)
    address = DocumentAddress(
        {"kind": token.kind, "scope": {"kind": "task", "taskId": target}}
    )
    doc_id = rig.session.read_on_line(lambda: rig.storage.find_document(address).id)
    scheduler, passes = owner(rig, [task], monkeypatch)
    assert mark(scheduler, target) == "marked"
    scheduler.remove_definition(task.name)
    before = rig.record(target)
    silent = rig.silence()
    assert not scheduler._enabled and scheduler._invocation is None
    assert calls == ["initial"]
    rig.assert_silent(silent)
    states = States(rig)
    first_commit = len(rig.storage.commits)
    scheduler.resume()
    row = states.wait(target, "terminal")
    passes.idle()
    assert row["state"] == MISSING
    assert {
        k: row[k] for k in ("id", "kind", "version", "input", "conversationId")
    } == {k: before[k] for k in ("id", "kind", "version", "input", "conversationId")}
    assert row["abortRequested"] and row.get("startedAt") is None
    assert "memos" not in row and "restarts" not in row
    assert calls == ["initial"] and all(value is None for value in passes.returns)
    assert [r["state"] for r in states.rows if r["id"] == target] == [MISSING]
    assert rig.session.read_on_line(lambda: rig.storage.document(doc_id)) is None
    terminal_batches = [
        [write.to_tree() for write in batch]
        for batch in rig.storage.commits[first_commit:]
        if any(write.to_tree().get("value", {}).get("id") == target for write in batch)
    ]
    assert len(terminal_batches) == 1
    assert {"type": "document.retire", "id": doc_id} in terminal_batches[0]
    states.unsubscribe()
    if rig.backend == "sqlite":
        rig.close()
        rig.storage = ObservedStorage(open_sqlite_storage(str(rig.path), mode="open"))
        rig.session = Session(rig.storage)
        scheduler, passes = owner(rig, [task], monkeypatch)
    scheduler.install_definition(task)
    silent = rig.silence()
    assert mark(scheduler, target) == "terminal"
    scheduler.resume()
    passes.idle()
    assert rig.record(target)["state"] == MISSING and calls == ["initial"]
    assert rig.session.read_on_line(lambda: rig.storage.document(doc_id)) is None
    rig.assert_silent(silent)


def test_exact_restoration_before_missing_decision_runs_real_abort(rig, monkeypatch):
    calls = []

    async def abort(_row, runtime, _context):
        calls.append("restored")
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    assert mark(scheduler, target) == "marked"
    scheduler.remove_definition(task.name)
    assert rig.record(target)["state"]["status"] == "pending" and calls == []
    scheduler.install_definition(task)
    states = States(rig)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert calls == ["restored"]
    states.unsubscribe()


@pytest.mark.parametrize("version", [0, 2])
def test_present_drift_does_not_borrow_missing_or_migration_authority(
    rig, monkeypatch, version
):
    calls = []

    def migrate(_old):
        calls.append("migration")
        raise AssertionError("Marked version drift must not fit or migrate")

    task = definition()
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    assert mark(scheduler, target) == "marked"
    replacement = dataclasses.replace(
        definition(forbidden, version=version), migrate=migrate
    )
    scheduler.install_definition(replacement)
    before = rig.silence()
    scheduler.resume()
    passes.idle()
    rig.assert_silent(before)
    assert calls == [] and rig.record(target)["state"]["status"] == "pending"
    scheduler.install_definition(task)
    states = States(rig)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert calls == []
    states.unsubscribe()


@pytest.mark.parametrize("edge", ["committed", "candidate"])
def test_missing_code_keeps_late_inbound_wait_and_neighbor_progress(
    rig, monkeypatch, edge
):
    task = definition(forbidden)
    target = rig.create(task)
    waiter = rig.create(definition(name="missing.waiter"))
    neighbor_calls = []

    async def finish(_row, runtime, _context):
        neighbor_calls.append("run")
        await runtime.commit(lambda _tx, _current: DONE)

    neighbor_task = define_task(
        "missing.neighbor", 1, lambda _: CHECKPOINT, {"start": finish}, forbidden
    )
    neighbor = rig.create(neighbor_task)
    scheduler, passes = owner(rig, [task, neighbor_task], monkeypatch)
    assert mark(scheduler, target) == "marked"
    scheduler.remove_definition(task.name)
    waiting = {
        "status": "waiting",
        "checkpoint": CHECKPOINT,
        "on": [target],
        "policy": "allSettled",
    }
    staged = []
    original = scheduler._candidate

    async def with_candidate(tx, row, *args):
        if row["id"] == target and not staged:
            # Typed graph control on the real enabled reservation AsyncTx,
            # not a claimed handler or physical process-death trajectory.
            other = await tx.task(waiter)
            tx.set_task({**other.to_tree(), "state": waiting})
            staged.append(id(tx))
        return await original(tx, row, *args)

    if edge == "committed":
        rig.change(waiter, lambda row: row.update(state=waiting))
    else:
        monkeypatch.setattr(scheduler, "_candidate", with_candidate)
    before = rig.record(target)
    states = States(rig)
    scheduler.resume()
    assert states.wait(neighbor, "terminal")["state"] == DONE
    passes.idle()
    assert rig.record(target) == before
    assert rig.record(waiter)["state"] == waiting
    assert not any(row["id"] == target for row in states.rows)
    assert neighbor_calls == ["run"] and len(staged) == (edge == "candidate")
    states.unsubscribe()


@pytest.mark.parametrize(
    "boundary",
    [
        "owner",
        "terminal-child",
        "inbound-marked-wait",
        "restart-reason",
        "abandon",
        "sleeping",
        "reserved",
    ],
)
def test_strict_leaf_authority_refusal_has_no_write(rig, monkeypatch, boundary):
    task = definition(name="pi.tool" if boundary == "reserved" else "ordinary.abort")
    target = rig.create(task)
    scheduler, _passes = owner(rig, [task], monkeypatch)
    if boundary == "owner":
        target = make_child(rig, target, task)
    elif boundary == "terminal-child":
        child = make_child(rig, target, task)
        rig.change(child, lambda row: row.update(state=DONE))
    elif boundary == "inbound-marked-wait":
        other = rig.create(definition(name="other.wait"))
        # Native-only typed graph negative, not fake abort execution evidence.
        rig.change(
            other,
            lambda row: row.update(
                abortRequested=True,
                state={
                    "status": "waiting",
                    "checkpoint": CHECKPOINT,
                    "on": [target],
                    "policy": "allSettled",
                },
            ),
        )
    elif boundary == "restart-reason":
        rig.change(
            target, lambda row: row.update(abortRequested=True, abortReason="restart")
        )
    elif boundary == "abandon":
        rig.change(target, lambda row: row.update(abandonOnRestart=True))
    elif boundary == "sleeping":
        rig.change(
            target,
            lambda row: row.update(
                state={"status": "sleeping", "checkpoint": CHECKPOINT, "until": 0}
            ),
        )
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match="root leaf"):
        mark(scheduler, target)
    rig.assert_silent(before)
    assert rig.session.failure is None


@pytest.mark.parametrize("id", [True, 0, -1, "1", 1.5])
def test_existing_typed_id_gate(rig, monkeypatch, id):
    scheduler, _passes = owner(rig, [], monkeypatch)
    before = rig.silence()
    with pytest.raises((RecordError, TypeError, ValueError)):
        mark(scheduler, id)
    rig.assert_silent(before)


def test_unopened_unknown_and_active_owner_do_not_publish(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    async def abort(_row, runtime, _context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)
    scheduler = rig.scheduler = TaskScheduler(rig.session, [task])
    passes = Passes(scheduler, monkeypatch)
    with pytest.raises(RuntimeError, match="Open"):
        mark(scheduler, target)
    scheduler.open()
    with pytest.raises(ValueError, match="Unknown"):
        mark(scheduler, 10**9)
    mark(scheduler, target)
    states = States(rig)
    scheduler.resume()
    try:
        assert entered.wait(10)
        before = rig.silence()
        with pytest.raises(RuntimeError, match="Pause"):
            mark(scheduler, target)
        rig.assert_silent(before)
        with pytest.raises(RuntimeError, match="busy"):
            scheduler.install_definition(definition())
    finally:
        release.set()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    states.unsubscribe()


def test_same_version_replacement_is_captured_only_by_a_new_reservation(
    rig, monkeypatch
):
    calls = []

    async def first(_row, runtime, _context):
        calls.append("A")
        await runtime.commit(lambda _tx, _current: DONE)

    async def second(_row, runtime, _context):
        calls.append("B")
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(first)
    target = rig.create(task)
    scheduler, passes = owner(rig, [task], monkeypatch)
    mark(scheduler, target)
    scheduler.install_definition(definition(second))
    states = States(rig)
    scheduler.resume()
    assert states.wait(target, "terminal")["state"] == DONE
    passes.idle()
    assert calls == ["B"]
    states.unsubscribe()


def test_sqlite_kernel_running_recovery_differs_from_pending_mark(rig, monkeypatch):
    if rig.backend != "sqlite":
        pytest.skip(
            "Cold reopen requires the persistent backend; no physical death claim"
        )
    task = definition()
    pending = rig.create(task)
    reserved = rig.create(task)
    scheduler, _passes = owner(rig, [task], monkeypatch)
    mark(scheduler, pending)
    mark(scheduler, reserved)
    # Real set_task kernel-state control, not a claimed callback/process cut.
    rig.change(
        reserved,
        lambda row: row.update(state={"status": "running", "checkpoint": CHECKPOINT}),
    )
    rig.close()
    rig.storage = ObservedStorage(open_sqlite_storage(str(rig.path), mode="open"))
    rig.session = Session(rig.storage)
    scheduler, passes = owner(rig, [task], monkeypatch)
    assert rig.record(pending)["state"]["status"] == "pending"
    assert "restarts" not in rig.record(pending)
    assert rig.record(reserved)["state"]["status"] == "pending"
    assert rig.record(reserved)["restarts"] == 1
    assert not scheduler._enabled
    states = States(rig)
    scheduler.resume()
    assert states.wait(pending, "terminal")["state"] == DONE
    assert states.wait(reserved, "terminal")["state"] == DONE
    passes.idle()
    states.unsubscribe()


def test_close_joins_actual_abort_finalizer_without_manufacturing_outcome(
    rig, monkeypatch
):
    entered, finishing, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    async def abort(_row, _runtime, context):
        entered.set()
        try:
            await context.signal.wait()
        finally:
            finishing.set()
            assert await asyncio.to_thread(release.wait, 10)

    task = definition(abort)
    target = rig.create(task)
    scheduler, _passes = owner(rig, [task], monkeypatch)
    mark(scheduler, target)
    rig.change(target, lambda row: row.update(restarts=2))  # Typed accounting control.
    scheduler.resume()

    with ThreadPoolExecutor(max_workers=1) as pool:
        closer = None
        try:
            assert entered.wait(10)
            closer = pool.submit(rig.session.close, timeout=10)
            assert finishing.wait(10)
            assert not rig.storage.closed.is_set()
            assert not closer.done()
        finally:
            release.set()
            if closer is not None:
                closer.result(timeout=10)
        assert closer is not None and closer.done()
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    final = next(row.to_tree() for row in rig.storage.final_tasks if row.id == target)
    assert final["state"] == {"status": "pending", "checkpoint": CHECKPOINT}
    assert final["abortRequested"] is True and final["restarts"] == 2
    with pytest.raises(SessionClosedError):
        mark(scheduler, target)


def test_failed_owner_refuses_mark_and_cannot_manufacture_a_terminal(rig, monkeypatch):
    task = definition()
    target = rig.create(task)
    scheduler, _passes = owner(rig, [task], monkeypatch)
    before = len(rig.storage.commits)
    failure = RuntimeError("actual-owner-failure")
    rig.session.fail(failure)
    with pytest.raises(SessionFailed):
        mark(scheduler, target)
    rig.close()
    assert rig.session.failure is failure
    assert len(rig.storage.commits) == before
    final = next(row.to_tree() for row in rig.storage.final_tasks if row.id == target)
    assert final["state"]["status"] == "pending" and not final["abortRequested"]


def test_real_terminal_storage_failure_retains_failed_owner_authority(rig, monkeypatch):
    failure = RuntimeError("actual-abort-terminal-storage-failure")
    observed = threading.Event()

    def fail_write(_writes):
        observed.set()
        raise failure

    async def abort(_row, runtime, _context):
        # The existing forwarding storage fails the real terminal transaction.
        rig.storage.before_commit = fail_write
        await runtime.commit(lambda _tx, _current: DONE)

    task = definition(abort)
    target = rig.create(task)
    scheduler, _passes = owner(rig, [task], monkeypatch)
    mark(scheduler, target)
    scheduler.resume()
    assert observed.wait(10)
    rig.close()
    assert rig.session.failure is failure
    final = next(row.to_tree() for row in rig.storage.final_tasks if row.id == target)
    assert final["state"]["status"] == "running" and final["abortRequested"]
    with pytest.raises(SessionFailed):
        mark(scheduler, target)
