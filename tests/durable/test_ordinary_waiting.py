"""Broad real-backend waits/candidate/FIFO/cold/fault/owned-lifetime witnesses.

Semantic wait refusals use the established UnsupportedTaskExecution family.
Private TaskTx._tx use below is explicitly a native-kernel candidate fixture,
not an added public task mutation API or historical producer claim.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from definition_publication_live_support import (
    Passes as PublicationPasses,
)
from definition_publication_live_support import (
    Wakes,
    idle,
    successful_waking_install,
)
from ordinary_owned_live_support import complete_task_pages, reopen
from ordinary_waiting_support import (
    DONE,
    PhaseGate,
    States,
    WaitingCommitCut,
    definition,
    start,
    waiting,
)
from orphan_missing_definition_support import (
    GraphScans,
    sqlite_document_rows,
    unrelated_history,
)
from restart_policy_live_support import Tickets, held_line
from task_migration_support import Rig, opener, sqlite_rows

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.ownership import StorageOwnershipError
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.sqlite import open_sqlite_storage


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


def task_doc(name="waiting.saved"):
    return define_doc(
        name, 1, "task", lambda: {"n": 1}, checkpoint_when=lambda *_: False
    )


async def forbidden(*args):
    raise AssertionError("An uninstalled target must not invoke user code")


@pytest.mark.parametrize("background", [False, True])
def test_deep_terminal_owned_conversation_cycle_and_background_neighbor(
    rig, monkeypatch, background
):
    gate = PhaseGate()
    targets = {}
    calls = []

    async def first(task, runtime, context):
        gate.entered.set()
        assert await asyncio.to_thread(gate.release.wait, 10)
        bad = targets["background"] if background else targets["owner"]
        # The candidate introduces the only cycle; the stored graph is valid.
        # Candidate cycles are refused with UnsupportedTaskExecution.
        with pytest.raises(UnsupportedTaskExecution):
            await runtime.commit(lambda tx, row: waiting(bad))
        gate.refused.set()
        assert await asyncio.to_thread(gate.continue_.wait, 10)
        await runtime.commit(lambda tx, row: waiting(targets["owner"]))

    async def finish(task, runtime, context):
        calls.append(task["state"]["checkpoint"])
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "pi.user-wait.deep", first, phases={"start": first, "finish": finish}
    )
    unavailable = definition("waiting.deep.unavailable", forbidden)
    waiter = rig.create(source)

    def build(tx):
        owner = tx.create_task(
            unavailable,
            None,
            ownership={"kind": "conversation"},
            conversation_id=rig.conversation,
        )
        conversation = tx.create_conversation({"kind": "task", "taskId": owner})
        root = tx.create_task(
            unavailable,
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation.id,
            background=background,
        )
        bridges = [root]
        previous = root
        for _ in range(139):
            previous = tx.create_task(
                unavailable, None, ownership={"kind": "task", "taskId": previous}
            )
            bridges.append(previous)
        leaf = tx.create_task(
            unavailable, None, ownership={"kind": "task", "taskId": previous}
        )
        return owner, root, bridges, leaf

    owner, root, bridges, leaf = rig.session.commit(build)

    # Create the genuine chain while live, then terminalize existing bridges.
    # No newly created work is assembled under a terminal owner.
    def mark(tx):
        ending = bridges[1:] if background else bridges
        # Read every required actual record before the first staged write.
        originals = {
            target: tx.task(target).to_tree() for target in (*ending, leaf, root)
        }
        for target in ending:
            tx.set_task({**originals[target], "state": DONE})
        tx.set_task({**originals[leaf], "state": waiting(waiter)})
        if background:
            tx.set_task({**originals[root], "state": waiting(waiter)})

    rig.session.commit(mark)
    targets.update(owner=owner, background=root)
    scans = GraphScans(rig, monkeypatch)
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    try:
        assert gate.entered.wait(10)
        before = rig.silence()
        scans.pages.clear()
        gate.release.set()
        assert gate.refused.wait(10)
        rig.assert_silent(before)
        complete_task_pages(scans)
        assert calls == [] and rig.record(waiter)["state"]["status"] == "running"
        if not background:
            rig.change(leaf, lambda row: row.update(state=DONE))
        gate.continue_.set()
        assert states.wait(waiter, "waiting")["state"] == waiting(owner)
        passes.idle()
        assert rig.record(owner)["state"]["status"] == "pending"
        # A background ownership cut does not erase an explicit on dependency:
        # waiting directly on root refused above; waiting on owner is legal.
        rig.change(owner, lambda row: row.update(state=DONE))
        scheduler.resume()
        states.wait(waiter, "terminal")
        passes.idle()
        assert calls == [{"phase": "finish", "receipt": "saved"}]
        if background:
            assert rig.record(root)["state"] == waiting(waiter)
    finally:
        gate.release.set()
        gate.continue_.set()
        states.unsubscribe()


@pytest.mark.parametrize("candidate", ["cycle", "settled"])
def test_native_kernel_current_replacement_overrides_captured_wait_facts(
    rig, monkeypatch, candidate
):
    gate = PhaseGate()
    target = []
    callback_reached = []
    calls = []
    token = task_doc("waiting.candidate.saved")

    async def first(task, runtime, context):
        gate.entered.set()
        assert await asyncio.to_thread(gate.release.wait, 10)

        async def change(scope, row):
            callback_reached.append("candidate")
            # Explicit native-kernel fixture through the actual admitted
            # TaskTx/AsyncTx, not a public raw replacement method.
            tx = scope._tx
            existing = (await tx.task(target[0])).to_tree()
            draft = await scope.doc(token)
            tx.set_task(
                {
                    **existing,
                    "state": waiting(task["id"]) if candidate == "cycle" else DONE,
                }
            )
            draft["n"] = 2
            return waiting(target[0])

        if candidate == "cycle":
            with pytest.raises(UnsupportedTaskExecution):
                await runtime.commit(change)
            gate.refused.set()
            await context.signal.wait()
        else:
            await runtime.commit(change)

    async def finish(task, runtime, context):
        calls.append(task["state"]["checkpoint"])
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "waiting.candidate", first, phases={"start": first, "finish": finish}
    )
    waiter = rig.create(source)
    other = rig.create(definition("waiting.candidate.unavailable", forbidden))
    target.append(other)
    if candidate == "settled":
        # Captured B->A is replaced with a terminal candidate before A waits B.
        rig.change(other, lambda row: row.update(state=waiting(waiter)))
    rig.session.commit(lambda tx: tx.doc(token, task_id=waiter))
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    try:
        assert gate.entered.wait(10)
        before = rig.silence()
        target_before = rig.record(other)
        gate.release.set()
        if candidate == "cycle":
            assert gate.refused.wait(10)
            rig.assert_silent(before)
            assert rig.record(other) == target_before
            assert rig.session.snapshot(token, task_id=waiter).value.thaw() == {"n": 1}
            assert calls == []
        else:
            states.wait(waiter, "terminal")
            passes.idle()
            assert rig.record(other)["state"] == DONE
            assert rig.session.snapshot(token, task_id=waiter) is None
            assert calls == [{"phase": "finish", "receipt": "saved"}]
        assert callback_reached == ["candidate"]
    finally:
        gate.release.set()
        states.unsubscribe()


@pytest.mark.parametrize("order", ["wait-first", "terminal-first"])
def test_real_session_fifo_wait_and_target_settlement_have_no_lost_wake(
    rig, monkeypatch, order
):
    gate = PhaseGate()
    target = []
    line_order = []
    phases = []

    async def first(task, runtime, context):
        gate.entered.set()
        assert await asyncio.to_thread(gate.release.wait, 10)

        def commit(tx, row):
            line_order.append("wait")
            return waiting(target[0], receipt="fifo")

        await runtime.commit(commit)

    async def finish(task, runtime, context):
        phases.append(task["state"]["checkpoint"])
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "waiting.fifo", first, phases={"start": first, "finish": finish}
    )
    waiter = rig.create(source)
    target.append(rig.create(definition("waiting.fifo.target", forbidden)))
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    assert gate.entered.wait(10)
    tickets = Tickets(rig.session, monkeypatch)

    def settle():
        def change(tx):
            line_order.append("target")
            tx.set_task({**tx.task(target[0]).to_tree(), "state": DONE})

        return rig.session.commit(change)

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            with held_line(rig.session, pool):
                if order == "wait-first":
                    gate.release.set()
                    tickets.wait(1)
                    settlement = pool.submit(settle)
                else:
                    settlement = pool.submit(settle)
                    tickets.wait(1)
                    gate.release.set()
                actual = tickets.wait(2)
                assert actual[0][0] != actual[1][0]
            settlement.result(10)
        states.wait(waiter, "terminal")
        passes.idle()
        assert line_order == (
            ["wait", "target"] if order == "wait-first" else ["target", "wait"]
        )
        assert phases == [{"phase": "finish", "receipt": "fifo"}]
        assert rig.record(target[0])["state"] == DONE
    finally:
        gate.release.set()
        states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_public_new_child_wait_is_cold_and_keeps_documents_and_checkpoint(
    rig, monkeypatch
):
    entered = threading.Event()
    children = []
    calls = []
    token = task_doc("waiting.cold.saved")

    async def child_start(task, runtime, context):
        calls.append("child-start")
        await runtime.commit(
            lambda tx, row: {"status": "running", "checkpoint": {"phase": "finish"}}
        )
        entered.set()
        await context.signal.wait()

    async def child_finish(task, runtime, context):
        calls.append("child-finish")
        await runtime.commit(lambda tx, row: DONE)

    child_source = definition(
        "waiting.cold.child",
        child_start,
        phases={"start": child_start, "finish": child_finish},
    )

    async def first(task, runtime, context):
        calls.append("parent-start")
        assert await runtime.memo("saved", {"v": 1}) == {"v": 1}

        async def create(tx, row):
            children.append(await tx.create_task(child_source, None))
            (await tx.doc(token))["n"] = 2
            return waiting(children[-1], receipt="cold")

        await runtime.commit(create)

    async def finish(task, runtime, context):
        calls.append(("parent-finish", task["state"]["checkpoint"]))
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "waiting.cold.parent", first, phases={"start": first, "finish": finish}
    )
    waiter = rig.create(source)
    unrelated, unrelated_id = unrelated_history(rig)
    history = sqlite_document_rows(rig.path, {unrelated_id})
    scheduler, _ = start(rig, [source, child_source], monkeypatch)
    scheduler.resume()
    assert entered.wait(10)
    saved = rig.record(waiter)
    assert saved["state"] == waiting(children[0], receipt="cold")
    assert saved["memos"] == {"saved": {"v": 1}} and "restarts" not in saved
    assert rig.session.snapshot(token, task_id=waiter).value.thaw() == {"n": 2}
    rig.session.close(timeout=10)
    reopen(rig)
    assert rig.record(waiter) == saved
    states = States(rig)
    scheduler, passes = start(rig, [source, child_source], monkeypatch)
    assert calls == ["parent-start", "child-start"]
    assert rig.record(waiter) == saved
    scheduler.resume()
    states.wait(waiter, "terminal")
    passes.idle()
    assert calls == [
        "parent-start",
        "child-start",
        "child-finish",
        ("parent-finish", {"phase": "finish", "receipt": "cold"}),
    ]
    assert rig.session.snapshot(token, task_id=waiter) is None
    assert rig.session.snapshot(unrelated, conversation_id=rig.conversation) is not None
    assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_waiting_real_sqlite_rollback_versus_commit_before_ack_loss(
    rig, monkeypatch, cut
):
    gate = PhaseGate()
    target = []
    calls = []
    token = task_doc("waiting.cut.saved")

    async def first(task, runtime, context):
        calls.append("start")
        gate.entered.set()
        assert await asyncio.to_thread(gate.release.wait, 10)

        async def change(tx, row):
            (await tx.doc(token))["n"] = 2
            return waiting(target[0], receipt="cut")

        await runtime.commit(change)

    async def finish(task, runtime, context):
        calls.append("finish")
        await runtime.commit(lambda tx, row: DONE)

    source = definition("waiting.cut", first, phases={"start": first, "finish": finish})
    waiter = rig.create(source)
    target.append(rig.create(definition("waiting.cut.target", forbidden)))
    rig.session.commit(lambda tx: tx.doc(token, task_id=waiter))
    scheduler, _ = start(rig, [source], monkeypatch)
    scheduler.resume()
    assert gate.entered.wait(10)
    fault = WaitingCommitCut(rig, waiter, cut)
    try:
        with monkeypatch.context() as patch:
            fault.install(patch)
            gate.release.set()
            assert rig.session.wait_closed(timeout=10).reason == "failed"
    finally:
        gate.release.set()
    assert rig.session.failure is fault.error and fault.fired == [cut == "before"]
    assert len(fault.before_rows) == 1 and not scheduler._thread.is_alive()
    if cut == "before":
        assert sqlite_rows(rig.path) == fault.before_rows[0]
    reopen(rig)
    persisted = rig.record(waiter)
    assert persisted["state"] == (
        waiting(target[0], receipt="cut")
        if cut == "after"
        else {"status": "running", "checkpoint": {"phase": "start"}}
    )
    assert rig.session.snapshot(token, task_id=waiter).value.thaw() == {
        "n": 2 if cut == "after" else 1
    }
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    assert calls == ["start"]
    scheduler.resume()
    passes.idle()
    # After real ACK loss the wait already exists: readiness observation must
    # not require a fabricated second waiting publication.
    assert rig.record(waiter)["state"] == waiting(target[0], receipt="cut")
    assert calls == (["start", "start"] if cut == "before" else ["start"])
    rig.change(target[0], lambda row: row.update(state=DONE))
    scheduler.resume()
    states.wait(waiter, "terminal")
    passes.idle()
    assert calls[-1] == "finish" and calls.count("finish") == 1
    assert rig.session.snapshot(token, task_id=waiter) is None
    states.unsubscribe()


def test_unknown_live_target_waits_for_actual_enabled_definition_publication(
    rig, monkeypatch
):
    targets = []
    calls = []

    async def first(task, runtime, context):
        calls.append("wait")
        await runtime.commit(lambda tx, row: waiting(targets[0], receipt="publication"))

    async def finish(task, runtime, context):
        calls.append("finish")
        await runtime.commit(lambda tx, row: DONE)

    async def target_phase(task, runtime, context):
        calls.append("target")
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "waiting.publication", first, phases={"start": first, "finish": finish}
    )
    target_source = definition("waiting.publication.target", target_phase)
    waiter = rig.create(source)
    targets.append(rig.create(target_source))
    states = States(rig)
    scheduler, _ = start(rig, [source], monkeypatch)
    passes = PublicationPasses(scheduler, monkeypatch)
    wakes = Wakes(scheduler, monkeypatch)
    before = passes.checkpoint()
    scheduler.resume()
    passes.retired_after(before)
    assert rig.record(waiter)["state"] == waiting(targets[0], receipt="publication")
    assert calls == ["wait"]
    idle(scheduler)
    silent = rig.silence()
    with scheduler._guard:
        assert scheduler._enabled is True
    rig.assert_silent(silent)
    successful_waking_install(scheduler, target_source, True, passes, wakes)
    states.wait(waiter, "terminal")
    idle(scheduler)
    assert calls == ["wait", "target", "finish"]
    states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancelled_close_waiter_retains_wait_and_real_finalizer_path_lease(
    rig, monkeypatch, tmp_path
):
    entered, finalizing, release, finalized = (threading.Event() for _ in range(4))
    target = []

    async def child_phase(task, runtime, context):
        await runtime.commit(
            lambda tx, row: {"status": "running", "checkpoint": {"phase": "finish"}}
        )
        entered.set()
        try:
            await context.signal.wait()
        finally:
            finalizing.set()
            assert await asyncio.to_thread(release.wait, 10)
            finalized.set()

    child_source = definition("waiting.close.child", child_phase)

    async def first(task, runtime, context):
        async def create(tx, row):
            target.append(await tx.create_task(child_source, None))
            return waiting(target[0], receipt="close")

        await runtime.commit(create)

    source = definition(
        "waiting.close.parent", first, phases={"start": first, "finish": forbidden}
    )
    waiter = rig.create(source)
    scheduler, _ = start(rig, [source, child_source], monkeypatch)
    scheduler.resume()
    assert entered.wait(10)
    saved = rig.record(waiter)
    assert saved["state"] == waiting(target[0], receipt="close")

    async def close():
        first_waiter = asyncio.create_task(rig.session.aclose())
        assert await asyncio.to_thread(finalizing.wait, 10)
        first_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_waiter
        second_waiter = asyncio.create_task(rig.session.aclose())
        assert rig.session.end is None and not finalized.is_set()
        opener(rig.path, tmp_path, "ordinary-wait-finalizer", "refused")
        with pytest.raises(StorageOwnershipError):
            open_sqlite_storage(str(rig.path), mode="open")
        release.set()
        await asyncio.wait_for(second_waiter, 10)
        assert (await rig.session.await_closed()).reason == "closed"

    try:
        asyncio.run(close())
    finally:
        release.set()
    assert finalized.is_set() and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "ordinary-wait-closed", "opened")
    cold = open_sqlite_storage(str(rig.path), mode="open")
    try:
        assert cold.task(waiter).to_tree() == saved
        assert cold.task(target[0]).to_tree()["state"] == {
            "status": "pending",
            "checkpoint": {"phase": "finish"},
        }
    finally:
        cold.close()
