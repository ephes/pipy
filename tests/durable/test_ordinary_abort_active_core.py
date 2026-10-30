"""DE16c source-authored Core witnesses; physical process proof is separate."""

import asyncio
import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from ordinary_abort_active_core_support import (
    CHECKPOINT,
    Active,
    Gate,
    isolated_rig,
)
from owned_completing_support import DONE, make_child

from pipy_harness.native.durable import ordinary_abort
from pipy_harness.native.durable.scheduler import (
    InvocationEnded,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session import SessionClosedError, SessionFailed
from pipy_harness.native.durable.session_core import ReentrantSessionCall


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    value = isolated_rig(request, tmp_path, monkeypatch)
    yield value
    value.storage.before_commit = value.storage.after_commit = None
    value.close()


@pytest.fixture
def active(rig, monkeypatch):
    value = Active(rig, monkeypatch)
    try:
        yield value
    finally:
        value.release()


def test_mark_publication_signal_finally_join_and_silent_repeat(active):
    rig = active.rig
    rig.change(active.target, lambda row: row.update(restarts=2, memos={"saved": 3}))
    invocation = active.start()
    future = invocation.retired
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(active.request)
        assert active.marked.wait(10)
        assert active.finally_gate.entered.wait(10)
        assert active.context.signal.cancelled
        assert not future.done() and not first.done()
        before = rig.silence()
        second = pool.submit(active.request)
        active.marked_requests(2)
        assert not second.done()
        assert invocation.retired is future
        rig.assert_silent(before)
        row = rig.record(active.target)
        assert row["state"]["status"] == "running" and row["abortRequested"]
        assert row["memos"] == {"saved": 3} and "restarts" not in row
        active.finally_gate.release.set()
        assert first.result(10) == second.result(10) == "marked"
    active.done()
    assert future.done() and not future.cancelled()
    assert active.calls == ["run-finally", "abort"]
    assert active.scheduler._invocation is None


@pytest.mark.parametrize("stage", ["logical-end", "pending-adopted"])
def test_repeat_joins_same_physical_slot_after_logical_or_durable_handoff(
    active, monkeypatch, stage
):
    gate = Gate()
    original = ordinary_abort.handoff

    async def held(scheduler, invocation):
        if stage == "logical-end":
            assert invocation.ended
            await gate.wait()
        await original(scheduler, invocation)
        if stage == "pending-adopted":
            await gate.wait()

    monkeypatch.setattr(ordinary_abort, "handoff", held)
    invocation = active.start()
    active.finally_gate.release.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(active.request)
        try:
            assert gate.entered.wait(10)
            assert invocation.ended and not invocation.retired.done()
            assert active.scheduler._invocation is invocation
            expected = "pending" if stage == "pending-adopted" else "running"
            assert active.rig.record(active.target)["state"]["status"] == expected
            before = active.rig.silence()
            second = pool.submit(active.request)
            active.marked_requests(2)
            assert not first.done() and not second.done()
            active.rig.assert_silent(before)
        finally:
            gate.release.set()
        assert first.result(10) == second.result(10) == "marked"
    active.done()


def test_cancelled_request_observers_leave_mark_and_other_join_owned(active):
    invocation = active.start()

    async def observers():
        first = asyncio.create_task(active.scheduler.aabort_leaf(active.target))
        second = asyncio.create_task(active.scheduler.aabort_leaf(active.target))
        assert await asyncio.to_thread(active.finally_gate.entered.wait, 10)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not invocation.retired.done() and not second.done()
        active.finally_gate.release.set()
        assert await second == "marked"

    asyncio.run(observers())
    active.done()
    assert not invocation.retired.cancelled()


def test_active_abort_mode_refuses_without_signalling_cleanup(active):
    active.abort_gate.release.clear()
    active.start()
    with ThreadPoolExecutor() as pool:
        request = pool.submit(active.request)
        active.finally_gate.release.set()
        try:
            assert request.result(10) == "marked"
            assert active.abort_entered.wait(10)
            with active.scheduler._guard:
                invocation = active.scheduler._invocation
            assert invocation.execution.mode == "abort"
            assert not invocation.context.signal.cancelled
            before = active.rig.silence()
            with pytest.raises(RuntimeError, match="Pause"):
                active.request()
            active.rig.assert_silent(before)
            assert not invocation.context.signal.cancelled
        finally:
            active.abort_gate.release.set()
    active.done()


@pytest.mark.parametrize("after", ["return", "throw"])
def test_winning_mark_fences_all_runtime_writes_and_late_fault(rig, monkeypatch, after):
    calls = []

    async def body(_row, runtime, context):
        await context.signal.wait()
        for operation in (
            lambda: runtime.memo("late", True),
            lambda: runtime.commit(lambda _tx, _row: calls.append("admitted")),
        ):
            with pytest.raises(InvocationEnded):
                await operation()
        if after == "throw":
            raise ValueError("must-not-overwrite-mark")

    value = Active(rig, monkeypatch, body=body)
    try:
        value.start()
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            assert value.finally_gate.entered.wait(10)
            assert calls == []
            value.finally_gate.release.set()
            assert request.result(10) == "marked"
        value.done()
        assert all(
            row["state"].get("outcome", {}).get("status") != "faulted"
            for row in value.writes
        )
    finally:
        value.release()


@pytest.mark.parametrize("winner", ["terminal", "child"])
def test_real_runtime_transaction_wins_fifo_before_mark(rig, monkeypatch, winner):
    commit = Gate()
    committed = threading.Event()
    calls = []

    async def body(_row, runtime, _context):
        async def change(tx, _current):
            await commit.wait()
            if winner == "terminal":
                return DONE
            child = await tx.create_task(
                dataclasses.replace(value.task, name="unregistered.child"), {}
            )
            calls.append(child)
            return None

        await runtime.commit(change)
        committed.set()

    value = Active(rig, monkeypatch, body=body)
    neighbor = rig.create(dataclasses.replace(value.task, name="unregistered.neighbor"))
    neighbor_before = rig.record(neighbor)
    try:
        invocation = value.start()
        assert commit.entered.wait(10)
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            commit.release.set()
            assert committed.wait(10)
            if winner == "terminal":
                assert request.result(10) == "terminal"
                assert not invocation.context.signal.cancelled
            else:
                with pytest.raises(UnsupportedTaskExecution, match="root leaf"):
                    request.result(10)
                assert not rig.record(value.target)["abortRequested"]
                assert len(calls) == 1
            value.finally_gate.release.set()
        assert value.finished.wait(10)
        value.passes.idle()
        assert rig.record(neighbor) == neighbor_before
    finally:
        commit.release.set()
        value.release()


def test_mark_wins_before_next_step_and_no_later_phase(rig, monkeypatch):
    calls = []

    async def body(_row, runtime, context):
        await runtime.commit(
            lambda _tx, _current: {"status": "running", "checkpoint": {"phase": "next"}}
        )
        await context.signal.wait()

    value = Active(rig, monkeypatch, body=body)

    # Constructor capture of a second genuine phase, before any reservation.
    async def next_phase(*_args):
        calls.append("next")

    value.scheduler.install_definition(
        dataclasses.replace(
            value.task, phases={"start": value.task.phases["start"], "next": next_phase}
        )
    )
    try:
        value.start()
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            assert value.finally_gate.entered.wait(10)
            value.finally_gate.release.set()
            assert request.result(10) == "marked"
        value.done()
        assert calls == []
    finally:
        value.release()


def test_pause_after_reservation_does_not_enable_abort(active):
    active.start()
    with ThreadPoolExecutor(max_workers=2) as pool:
        paused = threading.Event()

        async def pausing():
            barrier = asyncio.create_task(active.scheduler.apause())
            await asyncio.sleep(0)
            paused.set()
            await barrier

        pause = pool.submit(lambda: asyncio.run(pausing()))
        assert paused.wait(10)
        assert not active.scheduler._enabled
        request = pool.submit(active.request)
        assert active.finally_gate.entered.wait(10)
        active.finally_gate.release.set()
        assert request.result(10) == "marked"
        pause.result(10)
    assert active.calls == ["run-finally"]
    assert active.rig.record(active.target)["state"]["status"] == "pending"
    replacement_calls = []

    async def replacement(_row, runtime, _context):
        replacement_calls.append("fresh-same-version-abort")
        await runtime.commit(lambda _tx, _row: DONE)

    active.scheduler.install_definition(
        dataclasses.replace(active.task, abort=replacement)
    )
    active.scheduler.resume()
    active.done()
    assert replacement_calls == ["fresh-same-version-abort"]


def test_busy_definition_publication_and_host_self_join_refuse(active):
    active.start()
    with pytest.raises(RuntimeError, match="busy"):
        active.scheduler.install_definition(active.task)

    async def own_host():
        with pytest.raises(RuntimeError, match="phase host"):
            await active.scheduler.aabort_leaf(active.target)

    asyncio.run_coroutine_threadsafe(own_host(), active.scheduler._loop).result(5)
    with pytest.raises(ReentrantSessionCall):
        active.rig.session.commit(
            lambda _tx: asyncio.run(active.scheduler.aabort_leaf(active.target))
        )
    with ThreadPoolExecutor() as pool:
        request = pool.submit(active.request)
        active.finally_gate.release.set()
        assert request.result(10) == "marked"
    active.done()


@pytest.mark.parametrize("fault", ["cancelled", "base-exception"])
def test_escaping_run_error_after_real_held_finally_keeps_first_failure(
    rig, monkeypatch, fault
):
    failure = (
        asyncio.CancelledError("actual-run-cancelled")
        if fault == "cancelled"
        else BaseException("actual-run-failure")
    )

    async def body(_row, _runtime, context):
        await context.signal.wait()
        raise failure

    value = Active(rig, monkeypatch, body=body)
    try:
        invocation = value.start()
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            assert value.finally_gate.entered.wait(10)
            assert not invocation.retired.done() and not request.done()
            value.finally_gate.release.set()
            with pytest.raises(SessionFailed) as caught:
                request.result(10)
            assert caught.value.cause is failure
        assert invocation.retired.done() and not invocation.retired.cancelled()
        assert invocation.retired.exception() is failure
        assert rig.session.failure is failure
        assert value.scheduler._invocation is None
        assert value.calls == ["run-finally"]
        assert not value.abort_entered.is_set()
    finally:
        value.release()


def test_step_catch_fail_return_none_cannot_authorize_healthy_handoff(rig, monkeypatch):
    async def returned(*_args):
        return

    value = Active(rig, monkeypatch, body=returned)
    failure = OSError("actual-step-read-failure")
    failed = threading.Event()
    unsubscribe = rig.session.subscribe_close(failed.set)
    try:
        invocation = value.start()
        assert value.finally_gate.entered.wait(10)
        original = rig.storage.inner.task

        def task(id):
            if value.finished.is_set() and id == value.target:
                raise failure
            return original(id)

        monkeypatch.setattr(rig.storage.inner, "task", task)
        value.finally_gate.release.set()
        assert failed.wait(10)
        # Failure notification is not retirement; join the physical owner.
        with pytest.raises(OSError) as retired_failure:
            invocation.retired.result(timeout=10)
        assert retired_failure.value is failure
        assert rig.session.failure is failure
        assert invocation.retired.done() and invocation.retired.exception() is failure
        assert value.scheduler._invocation is None
        assert not value.abort_entered.is_set()
    finally:
        monkeypatch.setattr(rig.storage.inner, "task", original)
        unsubscribe()
        value.release()


@pytest.mark.parametrize("cut", ["before", "after"])
def test_real_handoff_storage_failure_never_returns_healthy_mark(rig, monkeypatch, cut):
    value = Active(rig, monkeypatch)
    failure = OSError("handoff-" + cut)
    fired = threading.Event()

    def fail_handoff(writes):
        for write in writes:
            tree = write.to_tree()
            if tree["type"] != "task":
                continue
            row = tree["value"]
            if (
                row["id"] == value.target
                and row["abortRequested"]
                and row["state"]["status"] == "pending"
            ):
                fired.set()
                raise failure

    setattr(rig.storage, cut + "_commit", fail_handoff)
    try:
        invocation = value.start()
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            assert value.finally_gate.entered.wait(10)
            assert not invocation.retired.done()
            value.finally_gate.release.set()
            with pytest.raises(SessionFailed) as caught:
                request.result(10)
            assert caught.value.cause is failure
        assert fired.is_set() and rig.session.failure is failure
        assert not value.abort_entered.is_set()
        assert value.calls == ["run-finally"]
    finally:
        rig.storage.before_commit = rig.storage.after_commit = None
        value.release()


def test_healthy_close_wakes_request_without_finishing_physical_future(active):
    invocation = active.start()
    closed = threading.Event()
    unsubscribe = active.rig.session.subscribe_close(closed.set)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(active.request)
        assert active.finally_gate.entered.wait(10)
        close = pool.submit(active.rig.session.close)
        try:
            assert closed.wait(10)
            with pytest.raises(SessionClosedError):
                request.result(10)
            assert not invocation.retired.done() and not close.done()
            assert not active.rig.storage.closed.is_set()
        finally:
            active.finally_gate.release.set()
        close.result(10)
    assert invocation.retired.done() and invocation.retired.exception() is None
    assert active.rig.storage.closed.is_set()
    assert active.calls == ["run-finally"]
    unsubscribe()


def test_failure_wakes_request_before_cleanup_but_not_lease_close(rig, monkeypatch):
    failure = OSError("earlier-authoritative-failure")
    later = asyncio.CancelledError("later-run-error")

    async def body(_row, _runtime, context):
        await context.signal.wait()
        raise later

    value = Active(rig, monkeypatch, body=body)
    try:
        invocation = value.start()
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            try:
                assert value.finally_gate.entered.wait(10)
                rig.session.fail(failure)
                with pytest.raises(SessionFailed) as caught:
                    request.result(10)
                assert caught.value.cause is failure
                assert not invocation.retired.done()
                assert not rig.storage.closed.is_set()
            finally:
                value.finally_gate.release.set()
        rig.session.close(timeout=10)
        assert invocation.retired.done() and invocation.retired.exception() is failure
        assert rig.storage.closed.is_set()
    finally:
        value.release()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "older",
        "newer",
        "running-no-slot",
        "restart",
        "abandon",
        "waiting",
        "sleeping",
        "completing",
        "child",
    ],
)
def test_existing_strict_negative_boundaries_without_publication(
    rig, monkeypatch, case
):
    value = Active(rig, monkeypatch)
    if case in {"missing", "older", "newer"}:
        value.scheduler.remove_definition(value.task.name)
        if case != "missing":
            version = 0 if case == "older" else 2
            value.scheduler.install_definition(
                dataclasses.replace(value.task, version=version)
            )
    elif case == "restart":
        rig.change(
            value.target,
            lambda row: row.update(abortRequested=True, abortReason="restart"),
        )
    elif case == "abandon":
        rig.change(value.target, lambda row: row.update(abandonOnRestart=True))
    elif case == "child":
        make_child(rig, value.target, value.task)
    else:
        states = {
            "running-no-slot": {"status": "running", "checkpoint": CHECKPOINT},
            "waiting": {
                "status": "waiting",
                "checkpoint": CHECKPOINT,
                "on": [],
                "policy": "allSettled",
            },
            "sleeping": {"status": "sleeping", "checkpoint": CHECKPOINT, "until": 0},
            "completing": {"status": "completing", "outcome": DONE["outcome"]},
        }
        rig.change(value.target, lambda row: row.update(state=states[case]))
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution):
        value.request()
    rig.assert_silent(before)
    assert rig.session.failure is None


def test_admitted_mark_settlement_survives_cancelled_request_waiter(active):
    commit_entered, commit_release = threading.Event(), threading.Event()
    original = active.rig.storage.before_commit

    def hold(writes):
        for write in writes:
            tree = write.to_tree()
            if (
                tree["type"] == "task"
                and tree["value"]["id"] == active.target
                and tree["value"]["abortRequested"]
            ):
                commit_entered.set()
                assert commit_release.wait(10)
                return

    active.start()
    active.rig.storage.before_commit = hold

    async def caller():
        request = asyncio.create_task(active.scheduler.aabort_leaf(active.target))
        try:
            assert await asyncio.to_thread(commit_entered.wait, 10)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not active.runtime._invocation.retired.done()
        finally:
            commit_release.set()
        assert await asyncio.to_thread(active.finally_gate.entered.wait, 10)
        active.finally_gate.release.set()

    try:
        asyncio.run(caller())
        active.done()
        assert active.calls == ["run-finally", "abort"]
    finally:
        commit_release.set()
        active.rig.storage.before_commit = original


@pytest.mark.parametrize("factory", ["default", "eager"])
def test_completion_exists_before_fast_callback_and_has_one_retirement(
    rig, monkeypatch, factory
):
    async def fast(_row, runtime, _context):
        assert not runtime._invocation.retired.done()
        await runtime.commit(lambda _tx, _current: DONE)

    value = Active(rig, monkeypatch, body=fast)
    original = value.scheduler._retire
    retired = []

    def observe(invocation, failure=None):
        assert not invocation.retired.done()
        original(invocation, failure)
        assert value.scheduler._invocation is not invocation
        retired.append(invocation)

    monkeypatch.setattr(value.scheduler, "_retire", observe)

    async def configure():
        if factory == "eager":
            value.scheduler._loop.set_task_factory(asyncio.eager_task_factory)

    asyncio.run_coroutine_threadsafe(configure(), value.scheduler._loop).result(5)
    try:
        value.finally_gate.release.set()
        invocation = value.start()
        value.passes.idle()
        assert retired == [invocation]
        assert invocation.retired.done() and invocation.retired.exception() is None
        assert value.calls == ["run-finally"]
    finally:
        value.release()


def test_tentative_real_reservation_failure_retires_without_callback(rig, monkeypatch):
    value = Active(rig, monkeypatch)
    failure = OSError("reservation-before-commit")
    failed = threading.Event()
    captured = []
    unsubscribe = rig.session.subscribe_close(failed.set)

    def fail(writes):
        for write in writes:
            tree = write.to_tree()
            if (
                tree["type"] == "task"
                and tree["value"]["id"] == value.target
                and tree["value"]["state"]["status"] == "running"
            ):
                captured.append(value.scheduler._invocation)
                raise failure

    rig.storage.before_commit = fail
    try:
        value.scheduler.resume()
        assert failed.wait(10)
        rig.session.close(timeout=10)
        assert len(captured) == 1
        assert captured[0].retired.done()
        assert captured[0].retired.exception() is failure
        assert value.scheduler._invocation is None
        assert not value.entered.is_set() and value.calls == []
    finally:
        unsubscribe()
        rig.storage.before_commit = None
        value.release()


def test_close_between_registration_and_mark_cannot_strand_transport(
    active, monkeypatch
):
    gate = Gate()
    original = ordinary_abort._mark

    async def held(*args):
        await gate.wait()
        return await original(*args)

    monkeypatch.setattr(ordinary_abort, "_mark", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(active.request)
        assert gate.entered.wait(10)
        closing = threading.Event()
        unsubscribe = active.rig.session.subscribe_close(closing.set)
        close = pool.submit(active.rig.session.close)
        try:
            assert closing.wait(10)
            assert not request.done()
        finally:
            gate.release.set()
        with pytest.raises(SessionClosedError):
            request.result(10)
        close.result(10)
        unsubscribe()
    with pytest.raises(SessionClosedError):
        active.request()
    assert not active.entered.is_set() and active.calls == []


def test_cancelled_session_mark_observer_keeps_actual_settlement_owned(
    active, monkeypatch
):
    commit_entered, commit_release = threading.Event(), threading.Event()
    original_mark = ordinary_abort._mark
    observers = []

    async def observer(*args):
        task = asyncio.create_task(original_mark(*args))
        observers.append(task)
        return await task

    def hold(writes):
        if any(
            write.to_tree()["type"] == "task"
            and write.to_tree()["value"]["id"] == active.target
            and write.to_tree()["value"]["abortRequested"]
            for write in writes
        ):
            commit_entered.set()
            assert commit_release.wait(10)

    monkeypatch.setattr(ordinary_abort, "_mark", observer)
    active.start()
    active.rig.storage.before_commit = hold
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(active.request)
            assert commit_entered.wait(10)
            active.scheduler._loop.call_soon_threadsafe(observers[0].cancel)
            commit_release.set()
            with pytest.raises(asyncio.CancelledError):
                request.result(10)
            assert active.finally_gate.entered.wait(10)
            assert active.rig.record(active.target)["abortRequested"]
            assert not active.runtime._invocation.retired.done()
            active.finally_gate.release.set()
        active.done()
    finally:
        commit_release.set()
        active.rig.storage.before_commit = None


def test_step_admitted_handler_can_enter_late_but_has_no_marked_write_authority(
    rig, monkeypatch
):
    calls = []

    async def body(_row, runtime, context):
        assert context.signal.cancelled
        with pytest.raises(InvocationEnded):
            await runtime.commit(lambda _tx, _row: calls.append("forbidden"))
        calls.append("late-entry-fenced")

    value = Active(rig, monkeypatch, body=body)
    admitted = Gate()
    original = value.scheduler._step
    visits = []

    async def step(*args):
        row = await original(*args)
        if row is not None and not visits:
            visits.append(row)
            await admitted.wait()
        return row

    monkeypatch.setattr(value.scheduler, "_step", step)
    try:
        value.scheduler.resume()
        assert admitted.entered.wait(10)
        with ThreadPoolExecutor() as pool:
            request = pool.submit(value.request)
            assert value.marked.wait(10)
            assert not value.entered.is_set()
            admitted.release.set()
            assert value.finally_gate.entered.wait(10)
            assert calls == ["late-entry-fenced"]
            value.finally_gate.release.set()
            assert request.result(10) == "marked"
        value.done()
    finally:
        admitted.release.set()
        value.release()
