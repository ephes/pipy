"""Retired allSettled leaf cancellation through real creation, waits and aborts."""

import asyncio
import dataclasses
from concurrent.futures import ThreadPoolExecutor

import pytest
from ordinary_abort_active_core_support import isolated_rig
from ordinary_abort_support import ABORTED, mark, real_pass
from ordinary_abort_waiting_support import WaitingLeaf
from ordinary_waiting_support import waiting
from owned_completing_support import definition, make_child
from restart_policy_live_support import Tickets, held_line

from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.session import SessionFailed


@pytest.fixture(params=["memory", "sqlite"])
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


def test_real_wait_retirement_mark_preserves_checkpoint_and_on_targets(leaf):
    prior = leaf.park()
    target_before = leaf.rig.record(leaf.t)
    blocker_before = leaf.rig.record(leaf.u)
    target_invocation = leaf.invocation(leaf.t)
    signals = leaf.signal_count(target_invocation)
    assert signals > 0  # Normal retirement already signalled the old context.
    assert prior["memos"] == {"saved": {"n": 1}}
    assert leaf.rig.session.snapshot(
        leaf.document, task_id=leaf.target
    ).value.thaw() == {"n": 2}
    assert mark(leaf.scheduler, leaf.target) == "marked"
    expected = {**prior, "abortRequested": True}
    expected.pop("restarts", None)
    assert leaf.rig.record(leaf.target) == expected
    before = leaf.rig.silence()
    assert mark(leaf.scheduler, leaf.target) == "marked"
    leaf.rig.assert_silent(before)
    assert ("abort", leaf.target) not in leaf.snapshot()
    final = leaf.complete_abort()
    assert final["state"] == ABORTED and "memos" not in final
    assert leaf.rig.session.snapshot(leaf.document, task_id=leaf.target) is None
    assert leaf.rig.record(leaf.t) == target_before
    assert leaf.rig.record(leaf.u) == blocker_before
    assert leaf.signal_count(target_invocation) == signals
    assert leaf.snapshot().count(("abort", leaf.target)) == 1
    assert not any(label == "unexpected-abort" for label, _ in leaf.snapshot())
    leaf.progress_targets()
    before = leaf.rig.silence()
    assert mark(leaf.scheduler, leaf.target) == "terminal"
    leaf.rig.assert_silent(before)


def test_committed_wait_with_held_finally_has_no_active_run_mark_exception(leaf):
    leaf.wait_finally.release.clear()
    leaf.scheduler.resume()
    leaf.states.wait(leaf.target, "waiting")
    assert leaf.wait_finally.entered.wait(10)
    invocation = leaf.invocation(leaf.target)
    signals = leaf.signal_count(invocation)
    assert not invocation.ended and not invocation.retired.done()
    before = leaf.rig.silence()
    with pytest.raises(RuntimeError, match="Pause and retire"):
        mark(leaf.scheduler, leaf.target)
    leaf.rig.assert_silent(before)
    assert leaf.signal_count(invocation) == signals
    assert leaf.scheduler._invocation is invocation
    assert ("abort", leaf.target) not in leaf.snapshot()
    leaf.wait_finally.release.set()
    invocation.retired.result(timeout=10)
    asyncio.run(leaf.scheduler.apause())
    assert mark(leaf.scheduler, leaf.target) == "marked"
    assert leaf.complete_abort()["state"] == ABORTED


def test_enabled_idle_wait_still_requires_pause_ack(leaf):
    leaf.park()
    leaf.scheduler.resume()
    before = leaf.rig.silence()
    with pytest.raises(RuntimeError, match="Pause and retire"):
        mark(leaf.scheduler, leaf.target)
    leaf.rig.assert_silent(before)
    asyncio.run(leaf.scheduler.apause())
    assert leaf.rig.record(leaf.target)["abortRequested"] is False


@pytest.mark.parametrize("first", ["mark", "read"])
def test_wait_mark_and_target_read_take_actual_session_FIFO_order(
    leaf, first, monkeypatch
):
    leaf.park()
    tickets = Tickets(leaf.rig.session, monkeypatch)
    order = []

    def committed(writes):
        if any(
            write.to_tree().get("value", {}).get("id") == leaf.target
            and write.to_tree()["value"].get("abortRequested")
            for write in writes
        ):
            order.append("mark")

    leaf.rig.storage.after_commit = committed

    def read():
        return leaf.rig.session.read_on_line(
            lambda: order.append("read") or leaf.rig.storage.task(leaf.t).to_tree()
        )

    actions = {"mark": lambda: mark(leaf.scheduler, leaf.target), "read": read}
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(leaf.rig.session, pool):
            one = pool.submit(actions[first])
            tickets.wait(1)
            second = "read" if first == "mark" else "mark"
            two = pool.submit(actions[second])
            tickets.wait(2)
        one.result(10)
        two.result(10)
    leaf.rig.storage.after_commit = None
    assert order == [first, second]
    assert leaf.complete_abort()["state"] == ABORTED
    assert ("finish", leaf.target) not in leaf.snapshot()


@pytest.mark.parametrize("edge", ["child", "incoming", "incoming-marked"])
@pytest.mark.parametrize("first", ["mark", "edge"])
def test_waiting_leaf_candidate_rechecks_real_graph(leaf, edge, first, monkeypatch):
    leaf.park()
    if first == "mark":
        assert mark(leaf.scheduler, leaf.target) == "marked"
    neighbor = definition("wait.graph.neighbor", leaf._u)
    other = leaf.rig.create(neighbor)
    if edge == "child":
        if first == "mark":
            with pytest.raises(RuntimeError):
                make_child(leaf.rig, leaf.target, leaf.u_source)
            assert leaf.complete_abort()["state"] == ABORTED
            return
        make_child(leaf.rig, leaf.target, leaf.u_source)
    else:
        # Typed native-kernel graph negative, not WAITING execution proof.
        leaf.rig.change(
            other,
            lambda row: row.update(
                state=waiting(leaf.target),
                abortRequested=edge == "incoming-marked",
            ),
        )
    if edge == "incoming-marked":
        # Missing marked code legitimately settles and removes its wait edge.
        # Keep this topology control stable with the existing version-drift hold.
        drift = dataclasses.replace(neighbor, version=2)
        assert leaf.scheduler.install_definition(drift)
        neighbor_before = leaf.rig.record(other)
        assert neighbor_before["version"] == 1 and drift.version == 2
        assert neighbor_before["state"] == waiting(leaf.target)
        assert neighbor_before["abortRequested"] is True
    before = leaf.rig.silence()
    if first == "edge":
        with pytest.raises(UnsupportedTaskExecution, match="root leaf"):
            mark(leaf.scheduler, leaf.target)
        leaf.rig.assert_silent(before)
    else:
        real_pass(leaf.scheduler, monkeypatch)
        assert leaf.rig.record(leaf.target)["state"] == waiting(leaf.t)
        assert ("abort", leaf.target) not in leaf.snapshot()
        if edge == "incoming-marked":
            assert leaf.rig.record(other) == neighbor_before


@pytest.mark.parametrize("version", [None, 0, 2])
def test_marked_wait_uses_existing_missing_and_exact_version_policy(
    leaf, monkeypatch, version
):
    before = leaf.park()
    assert mark(leaf.scheduler, leaf.target) == "marked"
    assert leaf.scheduler.remove_definition(leaf.source.name)
    if version is not None:
        assert leaf.scheduler.install_definition(
            dataclasses.replace(leaf.source, version=version)
        )
    real_pass(leaf.scheduler, monkeypatch)
    row = leaf.rig.record(leaf.target)
    if version is None:
        assert row["state"] == {
            "status": "terminal",
            "outcome": {"status": "orphaned", "reason": "missing_task"},
        }
        assert ("abort", leaf.target) not in leaf.snapshot()
    else:
        assert row["state"] == before["state"] and "restarts" not in row
        assert ("abort", leaf.target) not in leaf.snapshot()
        assert leaf.scheduler.install_definition(leaf.source)
        assert leaf.complete_abort()["state"] == ABORTED


@pytest.mark.parametrize("outcome", ["throw", "empty", "caught-nonterminal"])
def test_wait_reaches_unchanged_terminal_only_abort_contract(leaf, outcome):
    leaf.park()
    caught = []

    async def abort(_row, runtime, _context):
        if outcome == "throw":
            raise ValueError("waiting abort failed")
        if outcome == "empty":
            return
        before = await asyncio.to_thread(leaf.rig.silence)
        with pytest.raises(UnsupportedTaskExecution, match="terminal outcome"):
            await runtime.commit(lambda _tx, _row: waiting(leaf.t))
        await asyncio.to_thread(leaf.rig.assert_silent, before)
        caught.append("refused")
        await runtime.commit(lambda _tx, _row: ABORTED)

    leaf.abort_action = abort
    assert mark(leaf.scheduler, leaf.target) == "marked"
    row = leaf.complete_abort()
    if outcome == "caught-nonterminal":
        assert row["state"] == ABORTED and caught == ["refused"]
    else:
        result = row["state"]["outcome"]
        assert result["status"] == "faulted"
        assert result["error"]["message"] == (
            "waiting abort failed"
            if outcome == "throw"
            else f"Abort handler of task {leaf.target} returned without a terminal outcome"
        )


def test_failed_owner_preserves_identity_without_new_mark(leaf):
    leaf.park()
    prior = leaf.rig.record(leaf.target)
    commits = len(leaf.rig.storage.commits)
    mints = len(leaf.rig.storage.mints)
    failure = OSError("waiting owner failed")
    leaf.rig.session.fail(failure)
    with pytest.raises(SessionFailed) as caught:
        mark(leaf.scheduler, leaf.target)
    assert caught.value.__cause__ is failure
    assert leaf.rig.session.failure is failure
    assert prior["abortRequested"] is False
    assert ("abort", leaf.target) not in leaf.snapshot()
    leaf.rig.close()
    assert (
        next(
            row.to_tree()
            for row in leaf.rig.storage.final_tasks
            if row.id == leaf.target
        )
        == prior
    )
    assert len(leaf.rig.storage.commits) == commits
    assert len(leaf.rig.storage.mints) == mints
