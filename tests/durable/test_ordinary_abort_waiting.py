"""Real SQLite mark cuts, cold waits, callback cleanup and backend lease joins."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from ordinary_abort_active_core_support import isolated_rig
from ordinary_abort_active_support import evidence, sql_evidence
from ordinary_abort_support import ABORTED, AbortCalls, cold, mark, real_pass
from ordinary_abort_waiting_support import (
    WaitingLeaf,
    WaitingMarkCommit,
    observe_close_wait,
    physical_wait_cut,
)
from ordinary_waiting_support import waiting
from task_migration_support import opener, sqlite_rows

from pipy_harness.native.durable.session import SessionClosedError, SessionFailed


@pytest.fixture(params=["sqlite"])
def rig(request, tmp_path, monkeypatch):
    value = isolated_rig(request, tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.storage.before_commit = value.storage.after_commit = None
        value.close()


@pytest.fixture
def leaf(rig, monkeypatch):
    value = WaitingLeaf(rig, monkeypatch)
    try:
        yield value
    finally:
        value.release()


@pytest.mark.parametrize("cut", ["before", "after"])
def test_wait_mark_actual_COMMIT_rollback_and_lost_ACK_cold_authority(
    leaf, monkeypatch, tmp_path, cut
):
    prior = leaf.park()
    before = sqlite_rows(leaf.rig.path)
    hooks = WaitingMarkCommit(leaf.rig, leaf.target, cut)
    with monkeypatch.context() as patch:
        hooks.install(patch)
        with pytest.raises(RuntimeError) as caught:
            mark(leaf.scheduler, leaf.target)
    assert caught.value is hooks.error
    assert hooks.fired == [cut == "before"] and hooks.retired.is_set()
    assert leaf.rig.session.failure is hooks.error
    assert ("abort", leaf.target) not in leaf.snapshot()
    if cut == "before":
        assert sqlite_rows(leaf.rig.path) == before
    leaf.rig.close()
    opened, scheduler = cold(leaf.rig.path, leaf.target, [])
    try:
        row = opened.record(leaf.target)
        expected = {**prior, "abortRequested": cut == "after"}
        expected.pop("restarts", None)
        assert row == expected and row["state"] == waiting(leaf.t)
        assert opened.record(leaf.t)["state"] == waiting(leaf.u)
        assert opened.record(leaf.u)["state"]["status"] == "pending"
        assert not scheduler._enabled
        evidence(
            tmp_path,
            "mark-" + cut,
            original_SQL=sql_evidence(before),
            cold_SQL=sql_evidence(sqlite_rows(opened.path)),
            original=prior,
            recovered=row,
            hook_rows=hooks.rows,
            callback_events=leaf.snapshot(),
            commit_in_transaction=hooks.fired,
        )
    finally:
        opened.close()


@pytest.mark.parametrize("cut", ["wait-unmarked", "wait-marked", "abort-entered"])
def test_genuine_waiting_SIGKILL_and_abort_entry_have_distinct_cold_charges(
    tmp_path, monkeypatch, cut
):
    path, ready, reached, _before = physical_wait_cut(tmp_path / cut, cut)
    prior = reached["row"]
    assert prior["state"]["status"] == (
        "running" if cut == "abort-entered" else "waiting"
    )
    assert prior["abortRequested"] is (cut != "wait-unmarked")
    assert reached["target"]["state"] == waiting(ready["blocker"])
    assert reached["target"]["abortRequested"] is False
    assert reached["blocker"]["state"]["status"] == "pending"
    original_effects = (path.parent / "effects.txt").read_bytes()
    recovered = None
    for _ in range(2):
        opened, _scheduler = cold(path, ready["id"], [])
        try:
            row = opened.record(ready["id"])
            expected = (
                {"status": "pending", "checkpoint": prior["state"]["checkpoint"]}
                if cut == "abort-entered"
                else prior["state"]
            )
            assert row["state"] == expected
            assert row.get("restarts") == (1 if cut == "abort-entered" else None)
            assert row["abortRequested"] is (cut != "wait-unmarked")
            if recovered is not None:
                assert row == recovered
            recovered = row
            assert (path.parent / "effects.txt").read_bytes() == original_effects
        finally:
            opened.close()
    calls = AbortCalls("ordinary.wait.abort.W")
    source = calls.declaration()
    opened, scheduler = cold(path, ready["id"], [source], limit=1)
    try:
        if cut == "wait-unmarked":
            assert mark(scheduler, ready["id"]) == "marked"
        real_pass(scheduler, monkeypatch)
        final = opened.record(ready["id"])
        if cut == "abort-entered":
            assert (
                final["state"]["outcome"]["error"]["detail"]["reason"]
                == "restart_limit"
            )
            assert calls.snapshot() == ()
        else:
            assert final["state"] == ABORTED and "restarts" not in final
            assert calls.snapshot() == (("abort", "A", 1, None),)
        assert opened.record(ready["target"])["abortRequested"] is False
        assert opened.record(ready["target"])["state"] == waiting(ready["blocker"])
        assert opened.record(ready["blocker"])["state"]["status"] == "pending"
        evidence(
            tmp_path,
            "cold-" + cut,
            before_death=prior,
            recovered=recovered,
            final=final,
            SQL=sql_evidence(sqlite_rows(path)),
            callback_events=calls.snapshot(),
            effects=original_effects.decode(),
        )
    finally:
        opened.close()


def test_cancelled_mark_observer_leaves_real_commit_and_close_lease_owned(
    leaf, tmp_path
):
    leaf.park()
    hook = WaitingMarkCommit(leaf.rig, leaf.target, "after", hold=True)
    cancelled = threading.Event()

    def caller():
        async def scenario():
            task = asyncio.create_task(leaf.scheduler.aabort_leaf(leaf.target))
            assert await asyncio.to_thread(hook.entered.wait, 10)
            task.cancel()
            cancelled.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())

    with pytest.MonkeyPatch.context() as patch:
        hook.install(patch)
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(caller)
            try:
                assert cancelled.wait(10)
                leaf.rig.session.request_close()
                assert not hook.retired.is_set() and leaf.rig.session.end is None
                opener(leaf.rig.path, tmp_path, "waiting-mark-held", "refused")
            finally:
                hook.release.set()
            work.result(10)
    assert leaf.rig.session.wait_closed(timeout=10).reason == "closed"
    assert hook.retired.is_set() and hook.fired == [False]
    opener(leaf.rig.path, tmp_path, "waiting-mark-closed", "opened")
    opened, _scheduler = cold(leaf.rig.path, leaf.target, [])
    try:
        assert opened.record(leaf.target)["state"] == waiting(leaf.t)
        assert opened.record(leaf.target)["abortRequested"] is True
        assert ("abort", leaf.target) not in leaf.snapshot()
    finally:
        opened.close()


@pytest.mark.parametrize("failed", [False, True])
def test_close_and_failure_do_not_release_held_actual_abort_finally(
    leaf, tmp_path, failed, monkeypatch
):
    leaf.park()
    leaf.abort_finally.release.clear()
    assert mark(leaf.scheduler, leaf.target) == "marked"
    leaf.scheduler.resume()
    assert leaf.abort_finally.entered.wait(10)
    invocation = leaf.invocation(leaf.target, "abort")
    assert not invocation.retired.done()
    failure = OSError("waiting-abort-held-finally")
    close_wait_entered = observe_close_wait(leaf.rig.session, monkeypatch)

    async def close():
        if failed:
            leaf.rig.session.fail(failure)
        first = asyncio.create_task(leaf.rig.session.aclose())
        assert await asyncio.to_thread(close_wait_entered.wait, 10)
        # A cancelled actual observer never supplies physical completion.
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert leaf.rig.session.end is None and not invocation.retired.done()
        opener(leaf.rig.path, tmp_path, "waiting-finally-held", "refused")
        second = asyncio.create_task(leaf.rig.session.aclose())
        leaf.abort_finally.release.set()
        await asyncio.wait_for(second, 10)

    try:
        asyncio.run(close())
    finally:
        leaf.abort_finally.release.set()
    assert invocation.retired.done() and not invocation.retired.cancelled()
    if failed:
        assert leaf.rig.session.failure is failure
        with pytest.raises(SessionFailed) as caught:
            mark(leaf.scheduler, leaf.target)
        assert caught.value.__cause__ is failure
    else:
        assert leaf.rig.session.failure is None
        with pytest.raises(SessionClosedError):
            mark(leaf.scheduler, leaf.target)
    opener(leaf.rig.path, tmp_path, "waiting-finally-closed", "opened")
