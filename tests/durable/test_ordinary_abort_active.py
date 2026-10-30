"""DE16c B1–B3: active callbacks, COMMITs, physical cuts and lease joins.

Witness real mark publication, held callback cleanup, cold recovery and close
ownership. Keep reservation, callback entry and separately captured SQL facts
distinct; bounded process cuts do not establish exhaustive lifecycle parity.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import pause
from ordinary_abort_active_support import (
    ActiveCalls,
    ActiveMarkCommit,
    active_target,
    evidence,
    physical_active_cut,
    sql_evidence,
    task_sql,
)
from ordinary_abort_support import ABORTED, MISSING, AbortCalls, cold, mark, real_pass
from restart_policy_live_support import idle
from task_migration_support import Rig, opener, sqlite_rows

from pipy_harness.native.durable.session import SessionClosedError, SessionFailed


@pytest.fixture
def rig(tmp_path, monkeypatch):
    for key in (
        "HOME",
        "PIPY_CONFIG_HOME",
        "PIPY_AUTH_DIR",
        "XDG_STATE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig("sqlite", tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_B1_real_mark_COMMIT_precedes_signal_and_late_write_admission(
    rig, tmp_path, monkeypatch
):
    calls = ActiveCalls()
    calls.late_release.clear()
    target, _source, calls, scheduler = active_target(rig, calls)
    before = sqlite_rows(rig.path)
    hook = ActiveMarkCommit(rig, target, "before")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with monkeypatch.context() as patch:
            hook.install(patch)
            job = pool.submit(mark, scheduler, target)
            try:
                assert hook.entered.wait(5)
                assert hook.fired == [True]
                assert not calls.signalled.is_set() and not calls.finalizing.is_set()
                assert not calls.abort_entered.is_set() and not job.done()
                assert sqlite_rows(rig.path) == before
                hook.release.set()
                assert calls.signalled.wait(5)
                before_late_write = sqlite_rows(rig.path)
                calls.late_release.set()
                assert calls.finalizing.wait(5) and calls.late_checked.wait(5)
                assert calls.signalled.is_set() and not calls.finalized.is_set()
                assert not job.done() and not calls.abort_entered.is_set()
                row = rig.record(target)
                assert row["abortRequested"] and row["state"]["status"] == "running"
                assert row["state"]["checkpoint"] == {
                    "phase": "start",
                    "saved": [1, None],
                }
                assert "restarts" not in row
                assert ("late-write-refused", "InvocationEnded") in calls.snapshot()
                assert not any(e[0] == "late-write-admitted" for e in calls.snapshot())
                held = sqlite_rows(rig.path)
                # Baseline taken AFTER intentional mark, BEFORE the attempted late write.
                assert held == before_late_write
                evidence(
                    tmp_path,
                    "mark-publication",
                    calls=calls,
                    SQL_before=sql_evidence(before),
                    SQL_marked=sql_evidence(held),
                    actual_row=row,
                    before_COMMIT_in_transaction=hook.fired,
                )
            finally:
                hook.release.set()
                calls.release_all()
            assert job.result(10) == "marked"
    assert calls.abort_entered.wait(5)
    idle(scheduler)
    pause(scheduler)
    assert rig.record(target)["state"] == ABORTED
    events = calls.snapshot()
    assert next(i for i, e in enumerate(events) if e[0] == "run-finally-exit") < next(
        i for i, e in enumerate(events) if e[0] == "abort"
    )


@pytest.mark.parametrize("stage", ["before", "after"])
def test_B1_real_mark_rollback_or_lost_ACK_preserves_failure_and_cold_state(
    rig, tmp_path, monkeypatch, stage
):
    target, source, calls, scheduler = active_target(rig)
    before = sqlite_rows(rig.path)
    hook = ActiveMarkCommit(rig, target, stage, fail=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with monkeypatch.context() as patch:
            hook.install(patch)
            job = pool.submit(mark, scheduler, target)
            try:
                assert hook.entered.wait(5)
                assert hook.fired == [stage == "before"]
                assert not calls.signalled.is_set() and not calls.abort_entered.is_set()
                visible = task_sql(rig.path, target)
                assert visible["abortRequested"] is (stage == "after")
                if stage == "before":
                    assert sqlite_rows(rig.path) == before
                hook.release.set()
                with pytest.raises((RuntimeError, SessionFailed)) as failure:
                    job.result(10)
                cause = (
                    failure.value.cause
                    if isinstance(failure.value, SessionFailed)
                    else failure.value
                )
                assert str(cause) == "active-mark-" + stage
                # Failure/close may signal the run; it is NOT mark-publication proof.
                assert calls.finalizing.wait(5)
                assert not calls.finalized.is_set() and not rig.storage.closed.is_set()
                assert not calls.abort_entered.is_set()
                opener(rig.path, tmp_path, "failed-mark-held-" + stage, "refused")
            finally:
                hook.release.set()
                calls.release_all()
        rig.close()
    end = rig.session.end
    assert end.reason == "failed" and str(end.error) == "active-mark-" + stage
    opener(rig.path, tmp_path, "failed-mark-retired-" + stage, "opened")
    after_close = sqlite_rows(rig.path)
    evidence(
        tmp_path,
        "mark-" + stage + "-failure",
        calls=calls,
        SQL_before=sql_evidence(before),
        SQL_after_close=sql_evidence(after_close),
        failure_type=type(end.error).__name__,
        failure_message=str(end.error),
    )
    reopened, _owner = cold(rig.path, target, [source])
    try:
        recovered = reopened.record(target)
        assert recovered["state"] == {
            "status": "pending",
            "checkpoint": {"phase": "start", "saved": [1, None]},
        }
        assert recovered["abortRequested"] is (stage == "after")
        assert recovered["restarts"] == 1  # Genuine interrupted RUN, never abort entry.
        assert not calls.abort_entered.is_set()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "cut", ["run-unmarked", "run-marked", "handoff-pending", "abort-entered"]
)
def test_B2_four_positive_SIGKILL_boundaries_have_distinct_cold_authority(
    tmp_path, monkeypatch, cut
):
    path, target, prior, fsynced = physical_active_cut(tmp_path / cut, cut)
    assert prior["state"]["status"] == (
        "pending" if cut == "handoff-pending" else "running"
    )
    assert prior["abortRequested"] is (cut != "run-unmarked")
    assert fsynced == (["run", "abort"] if cut == "abort-entered" else ["run"])
    original_effects = (path.parent / "effects.txt").read_bytes()
    recovered = None
    for _ in range(2):
        opened, _owner = cold(path, target, [], limit=5)
        try:
            row = opened.record(target)
            assert row["state"] == {
                "status": "pending",
                "checkpoint": {"phase": "start", "saved": [1, None]},
            }
            assert row["abortRequested"] is (cut != "run-unmarked")
            assert row.get("restarts") == (None if cut == "handoff-pending" else 1)
            if recovered is not None:
                assert row == recovered  # Recovery charges once, not each open.
            recovered = row
            assert (path.parent / "effects.txt").read_bytes() == original_effects
        finally:
            opened.close()
    calls = AbortCalls("ordinary.abort.active.physical")
    exact = calls.declaration()
    # Four finite discriminating continuations, not a cut×policy Cartesian matrix.
    definitions = [] if cut in {"run-marked", "handoff-pending"} else [exact]
    if cut == "abort-entered":
        definitions = [calls.declaration(2, "newer")]
    opened, scheduler = cold(
        path, target, definitions, limit=1 if cut == "run-marked" else 5
    )
    try:
        if cut == "run-unmarked":
            assert mark(scheduler, target) == "marked"
            assert "restarts" not in opened.record(target)
        if cut == "abort-entered":
            before = opened.record(target)
            real_pass(scheduler, monkeypatch)
            assert opened.record(target) == before and calls.snapshot() == ()
            assert scheduler.install_definition(exact)
        real_pass(scheduler, monkeypatch)
        final = opened.record(target)
        if cut == "run-marked":
            assert (
                final["state"]["outcome"]["error"]["detail"]["reason"]
                == "restart_limit"
            )
            assert final["state"]["outcome"]["status"] == "faulted"
            assert calls.snapshot() == ()  # Cutoff wins even with missing code.
        elif cut == "handoff-pending":
            assert final["state"] == MISSING and calls.snapshot() == ()
        else:
            assert final["state"] == ABORTED
            expected_count = 1 if cut == "abort-entered" else None
            assert calls.snapshot() == (("abort", "A", 1, expected_count),)
        evidence(
            tmp_path,
            "cold-" + cut,
            cut=cut,
            before_death=prior,
            recovered=recovered,
            final=final,
            reopened_calls=calls.snapshot(),
            fsynced_actual_entries=fsynced,
        )
    finally:
        opened.close()


def test_B3_cancelled_mark_and_close_observers_cannot_release_held_run_lease(
    rig, tmp_path
):
    target, _source, calls, scheduler = active_target(rig)
    close_began = threading.Event()
    remove = rig.session.subscribe_close(close_began.set)

    async def scenario():
        waiter = asyncio.create_task(scheduler.aabort_leaf(target))
        close_waiter = None
        try:
            assert await asyncio.to_thread(calls.finalizing.wait, 5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not calls.finalized.is_set() and not rig.storage.closed.is_set()
            close_waiter = asyncio.create_task(rig.session.aclose())
            assert await asyncio.to_thread(close_began.wait, 5)
            assert not close_waiter.done() and rig.session.end is None
            await asyncio.to_thread(
                opener, rig.path, tmp_path, "active-close-held", "refused"
            )
            close_waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close_waiter
            assert not calls.finalized.is_set() and not rig.storage.closed.is_set()
        finally:
            calls.release_all()
            if not waiter.done():
                with pytest.raises(SessionClosedError):
                    await waiter
            await rig.session.await_closed()
            remove()

    asyncio.run(scenario())
    assert calls.finalized.is_set() and rig.storage.closed.is_set()
    assert rig.session.end.reason == "closed"
    assert not calls.abort_entered.is_set()
    opener(rig.path, tmp_path, "active-close-retired", "opened")
    row = next(r.to_tree() for r in rig.storage.final_tasks if r.id == target)
    assert row["state"] == {
        "status": "pending",
        "checkpoint": {"phase": "start", "saved": [1, None]},
    }
    assert row["abortRequested"] and "restarts" not in row
    evidence(
        tmp_path,
        "cancel-close-lifetime",
        calls=calls,
        final_storage_row=row,
        SQL_after_close=sql_evidence(sqlite_rows(rig.path)),
        closed_reason=rig.session.end.reason,
    )


def test_B3_failure_wakes_request_but_not_physical_cleanup_or_lease(rig, tmp_path):
    target, _source, calls, scheduler = active_target(rig)
    failure = RuntimeError("active-run-held-first-failure")
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(mark, scheduler, target)
        try:
            assert calls.finalizing.wait(5)
            rig.session.fail(failure)
            with pytest.raises(SessionFailed) as result:
                request.result(10)
            assert result.value.cause is failure
            assert not calls.finalized.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None and not calls.abort_entered.is_set()
            opener(rig.path, tmp_path, "active-failed-held", "refused")
        finally:
            calls.release_all()
        rig.close()
    assert calls.finalized.is_set() and rig.storage.closed.is_set()
    assert rig.session.end.reason == "failed" and rig.session.end.error is failure
    opener(rig.path, tmp_path, "active-failed-retired", "opened")
    evidence(
        tmp_path,
        "failed-held-lifetime",
        calls=calls,
        SQL_after_close=sql_evidence(sqlite_rows(rig.path)),
        failure_type=type(failure).__name__,
        failure_message=str(failure),
    )
