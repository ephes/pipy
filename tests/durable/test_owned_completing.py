"""Ordinary execution, real held outcomes, refusal and physical settlement."""

import asyncio
import threading

import pytest
from ordinary_owned_live_support import assert_rollback, published, reopen
from orphan_missing_definition_support import (
    GraphScans,
    sqlite_document_rows,
    unrelated_history,
)
from owned_completing_support import (
    DONE,
    HELD,
    FinalizationCut,
    States,
    definition,
    end,
    make_child,
    start,
)
from task_migration_support import Rig, opener, sqlite_rows

from pipy_harness.native.durable.documents import define_doc, define_doc_family
from pipy_harness.native.durable.ownership import StorageOwnershipError
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import InvocationEnded
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


def task_doc(name="owned.saved"):
    return define_doc(
        name, 1, "task", lambda: {"saved": 1}, checkpoint_when=lambda *_: False
    )


def test_pi_prefixed_generic_owned_child_memo_commit_and_healthy_hold_without_tool(
    rig, monkeypatch
):
    reached, release = threading.Event(), threading.Event()
    child_ids, calls, winners = [], [], []
    token = task_doc()

    async def child(task, runtime, context):
        calls.append("child")
        winners.append(await runtime.memo("winner", {"first": 1}))
        winners.append(await runtime.memo("winner", {"second": 2}))
        reached.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(lambda tx, row: DONE)

    child_source = definition("pi.user-child", child)

    async def parent_start(task, runtime, context):
        calls.append("parent-start")

        async def create(tx, row):
            child_ids.append(await tx.create_task(child_source, None))
            return {"status": "running", "checkpoint": {"phase": "finish"}}

        await runtime.commit(create)

    async def parent_finish(task, runtime, context):
        calls.append("parent-finish")
        assert await runtime.memo("decided", 7) == 7
        await runtime.commit(lambda tx, row: DONE)

    parent_source = definition(
        "pi.user-parent",
        parent_start,
        phases={"start": parent_start, "finish": parent_finish},
    )
    parent = rig.create(parent_source)
    rig.session.commit(lambda tx: tx.doc(token, task_id=parent))
    states = States(rig)
    scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
    assert calls == []
    scheduler.resume()
    try:
        assert reached.wait(10)
        held = rig.record(parent)
        assert held["state"] == HELD and "memos" not in held and "restarts" not in held
        assert rig.session.snapshot(token, task_id=parent).value.thaw() == {"saved": 1}
        assert winners == [{"first": 1}, {"first": 1}]
    finally:
        release.set()
    terminal = states.wait(parent, "terminal")
    passes.idle()
    assert terminal["state"] == DONE and rig.record(child_ids[0])["state"] == DONE
    assert rig.session.snapshot(token, task_id=parent) is None
    assert calls == ["parent-start", "parent-finish", "child"]
    assert (
        len(
            [
                r
                for r in states.rows
                if r["id"] == parent and r["state"]["status"] == "terminal"
            ]
        )
        == 1
    )
    assert not scheduler._tool_execution_enabled()
    states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_actual_completing_close_cold_resume_retires_all_task_docs_once(
    rig, monkeypatch
):
    entered = threading.Event()
    calls = []
    token = task_doc()
    family = define_doc_family(
        "owned.many", 1, "task", lambda seed: seed, checkpoint_when=lambda *_: False
    )

    async def first(task, runtime, context):
        calls.append("child-start")
        await runtime.commit(
            lambda tx, row: {"status": "running", "checkpoint": {"phase": "finish"}}
        )
        entered.set()
        await context.signal.wait()

    async def finish(task, runtime, context):
        calls.append("child-finish")
        await runtime.commit(lambda tx, row: DONE)

    async def parent_phase(task, runtime, context):
        calls.append("parent")
        await runtime.commit(lambda tx, row: DONE)

    parent_source = definition("owned.cold.parent", parent_phase)
    child_source = definition(
        "owned.cold.child", first, phases={"start": first, "finish": finish}
    )
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)

    def documents(tx):
        tx.doc(token, task_id=parent)
        for index in range(257):
            tx.doc(family, task_id=parent, key=str(index), seed={"index": index})

    rig.session.commit(documents)
    unrelated, unrelated_id = unrelated_history(rig)
    history = sqlite_document_rows(rig.path, {unrelated_id})
    scheduler, _ = start(rig, [parent_source, child_source], monkeypatch)
    scheduler.resume()
    assert entered.wait(10)
    assert rig.record(parent)["state"] == HELD
    assert rig.session.snapshot(token, task_id=parent) is not None
    rig.session.close(timeout=10)
    assert not scheduler._thread.is_alive()
    reopen(rig)
    assert rig.record(parent)["state"] == HELD
    assert rig.record(child)["state"] == {
        "status": "pending",
        "checkpoint": {"phase": "finish"},
    }
    states = States(rig)
    scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
    assert calls == ["parent", "child-start"]
    assert rig.record(parent)["state"] == HELD
    scheduler.resume()
    states.wait(parent, "terminal")
    passes.idle()
    assert calls == ["parent", "child-start", "child-finish"]
    assert rig.session.snapshot(token, task_id=parent) is None
    for index in (0, 255, 256):
        assert rig.session.snapshot(family, task_id=parent, key=str(index)) is None
    assert rig.session.snapshot(unrelated, conversation_id=rig.conversation) is not None
    assert sqlite_document_rows(rig.path, {unrelated_id}) == history
    states.unsubscribe()


@pytest.mark.parametrize("intent", ["faulted", "abort"])
def test_effective_distant_ancestor_refuses_child_but_background_boundary_runs(
    rig, monkeypatch, intent
):
    calls = []

    async def work(task, runtime, context):
        calls.append(task["id"])
        await runtime.commit(lambda tx, row: DONE)

    source = definition("owned.refusal", work)
    owner = rig.create(source)
    intermediate = make_child(rig, owner, source)
    leaf = make_child(rig, intermediate, source)

    def background(tx):
        conversation = tx.create_conversation({"kind": "task", "taskId": owner})
        return tx.create_task(
            source,
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation.id,
            background=True,
        )

    bg = rig.session.commit(background)
    bg_leaf = make_child(rig, bg, source)
    # Genuine existing chain first, then a terminal bridge. No new child is
    # minted below a terminal owner and no private host memo/flag is injected.
    end(rig, intermediate)
    if intent == "faulted":
        rig.change(
            owner,
            lambda row: row.update(
                state={
                    "status": "completing",
                    "outcome": {
                        "status": "faulted",
                        "error": {"message": "saved failure"},
                    },
                }
            ),
        )
    else:
        rig.change(owner, lambda row: row.update(state=HELD, abortRequested=True))
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    passes.idle()
    assert calls == [bg, bg_leaf]
    assert rig.record(leaf)["state"]["status"] == "pending"
    assert rig.record(leaf)["abortRequested"] is False
    assert rig.record(owner)["state"]["status"] == "completing"
    before = rig.silence()
    scheduler.resume()
    passes.idle()
    rig.assert_silent(before)


@pytest.mark.parametrize("ending", ["reraise", "swallow"])
def test_inflight_child_refusal_precedes_fault_and_no_progress(
    rig, monkeypatch, ending
):
    entered, release, retired = threading.Event(), threading.Event(), threading.Event()
    attempted, mutations = [], []

    async def parent_phase(task, runtime, context):
        await runtime.commit(lambda tx, row: DONE)

    async def child_phase(task, runtime, context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        try:
            await runtime.commit(lambda tx, row: mutations.append("forbidden"))
        except InvocationEnded:
            attempted.append("commit refused")
            try:
                await runtime.memo("forbidden", 9)
            except InvocationEnded:
                attempted.append("memo refused")
            if ending == "reraise":
                raise
        finally:
            retired.set()

    parent_source = definition("owned.active.parent", parent_phase)
    child_source = definition("owned.active.child", child_phase)
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)
    rig.change(child, lambda row: row.update(restarts=3, memos={"saved": "same"}))
    scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(10)
        rig.change(
            parent,
            lambda row: row.update(
                state={
                    "status": "completing",
                    "outcome": {
                        "status": "faulted",
                        "error": {"message": "owner intent"},
                    },
                }
            ),
        )
        before = rig.silence()
    finally:
        release.set()
    assert retired.wait(10)
    passes.idle()
    value = rig.record(child)
    assert attempted == ["commit refused", "memo refused"] and mutations == []
    assert value["state"] == {"status": "pending", "checkpoint": {"phase": "start"}}
    assert value["restarts"] == 3 and value["memos"] == {"saved": "same"}
    assert rig.record(parent)["state"]["outcome"]["status"] == "faulted"
    assert "outcome" not in value["state"]
    # Exactly the real running->pending retirement is permitted. An additional
    # refused resume must not add a table/revision/sequence write or mint.
    after = rig.silence()
    assert len(rig.storage.mints) == before[2]
    scheduler.resume()
    passes.idle()
    rig.assert_silent(after)


def test_terminal_bridge_later_page_descendant_holds_parent(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    async def parent_phase(task, runtime, context):
        await runtime.commit(lambda tx, row: DONE)

    async def child_phase(task, runtime, context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(lambda tx, row: DONE)

    source = definition("owned.page.parent", parent_phase)
    child_source = definition("owned.page.leaf", child_phase)
    parent = rig.create(source)
    bridge = make_child(rig, parent, source)
    for _ in range(129):
        end(rig, rig.create(source))
    leaf = make_child(rig, bridge, child_source)
    end(rig, bridge)
    scans = GraphScans(rig, monkeypatch)
    states = States(rig)
    scheduler, passes = start(rig, [source, child_source], monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(10)
        assert rig.record(parent)["state"] == HELD
        assert any(
            name == "scan_tasks" and cursor is not None
            for name, cursor, _, _ in scans.pages
        )
    finally:
        release.set()
    states.wait(parent, "terminal")
    passes.idle()
    assert rig.record(leaf)["state"] == DONE
    states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_final_parent_commit_rollback_vs_real_commit_before_ack_loss(
    rig, monkeypatch, cut
):
    reached, release = threading.Event(), threading.Event()
    calls = []
    token = task_doc()

    async def parent_phase(task, runtime, context):
        calls.append("parent")
        await runtime.commit(lambda tx, row: DONE)

    async def child_phase(task, runtime, context):
        calls.append("child")
        reached.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(lambda tx, row: DONE)

    parent_source = definition("owned.cut.parent", parent_phase)
    child_source = definition("owned.cut.child", child_phase)
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)
    rig.session.commit(lambda tx: tx.doc(token, task_id=parent))
    scheduler, _ = start(rig, [parent_source, child_source], monkeypatch)
    scheduler.resume()
    assert reached.wait(10)
    assert rig.record(parent)["state"] == HELD
    fault = FinalizationCut(rig, parent, cut)

    try:
        with monkeypatch.context() as patch:
            fault.install(patch)
            release.set()
            assert rig.session.wait_closed(timeout=10).reason == "failed"
    finally:
        release.set()
    assert rig.session.failure is fault.error and fault.fired == [cut == "before"]
    assert len(fault.before_rows) == 1 and not scheduler._thread.is_alive()
    actual = sqlite_rows(rig.path)
    if cut == "before":
        assert actual == fault.before_rows[0]
    reopen(rig)
    assert rig.record(child)["state"] == DONE
    assert rig.record(parent)["state"]["status"] == (
        "completing" if cut == "before" else "terminal"
    )
    states = States(rig)
    scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
    if cut == "before":
        # Recovery is metadata reconciliation only, never parent/child effects.
        assert rig.record(parent)["state"] == DONE
    assert rig.session.snapshot(token, task_id=parent) is None
    scheduler.resume()
    passes.idle()
    assert calls == ["parent", "child"]
    states.unsubscribe()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_cancelled_close_waiter_joins_actual_owned_child_finalizer_and_path(
    rig, monkeypatch, tmp_path
):
    entered, finalizer, release, finalized = (threading.Event() for _ in range(4))

    async def parent_phase(task, runtime, context):
        await runtime.commit(lambda tx, row: DONE)

    async def child_phase(task, runtime, context):
        entered.set()
        try:
            await context.signal.wait()
        finally:
            finalizer.set()
            assert await asyncio.to_thread(release.wait, 10)
            finalized.set()

    parent_source = definition("owned.close.parent", parent_phase)
    child_source = definition("owned.close.child", child_phase)
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)
    scheduler, _ = start(rig, [parent_source, child_source], monkeypatch)
    scheduler.resume()
    assert entered.wait(10) and rig.record(parent)["state"] == HELD

    async def close():
        waiter = asyncio.create_task(rig.session.aclose())
        assert await asyncio.to_thread(finalizer.wait, 10)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert rig.session.end is None and not finalized.is_set()
        opener(rig.path, tmp_path, "held-child-finalizer", "refused")
        with pytest.raises(StorageOwnershipError):
            open_sqlite_storage(str(rig.path), mode="open")
        release.set()
        assert (
            await asyncio.wait_for(rig.session.await_closed(), 10)
        ).reason == "closed"

    try:
        asyncio.run(close())
    finally:
        release.set()
    assert finalized.is_set() and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, "owned-closed", "opened")
    cold = open_sqlite_storage(str(rig.path), mode="open")
    try:
        assert cold.task(parent).to_tree()["state"] == HELD
        assert cold.task(child).to_tree()["state"]["status"] == "pending"
    finally:
        cold.close()


def test_pi_prefixed_generic_fault_is_held_and_refuses_child(rig, monkeypatch):
    calls = []

    async def parent_phase(task, runtime, context):
        calls.append("parent")
        raise ValueError("actual parent phase failed")

    async def child_phase(task, runtime, context):
        calls.append("forbidden child")
        await runtime.commit(lambda tx, row: DONE)

    parent_source = definition("pi.user-fault-parent", parent_phase)
    child_source = definition("pi.user-fault-child", child_phase)
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)
    token = task_doc()
    rig.session.commit(lambda tx: tx.doc(token, task_id=parent))
    scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
    scheduler.resume()
    passes.idle()
    assert calls == ["parent"]
    assert rig.record(parent)["state"] == {
        "status": "completing",
        "outcome": {
            "status": "faulted",
            "error": {"message": "actual parent phase failed"},
        },
    }
    assert rig.record(child)["state"]["status"] == "pending"
    assert rig.record(child)["abortRequested"] is False
    assert rig.session.snapshot(token, task_id=parent) is not None
    before = rig.silence()
    scheduler.resume()
    passes.idle()
    rig.assert_silent(before)
    if rig.backend == "sqlite":
        reopen(rig)
        scheduler, passes = start(rig, [parent_source, child_source], monkeypatch)
        assert calls == ["parent"]
        assert rig.record(parent)["state"]["status"] == "completing"
        before = rig.silence()
        scheduler.resume()
        passes.idle()
        rig.assert_silent(before)
        assert calls == ["parent"]
        assert rig.session.snapshot(token, task_id=parent) is not None


def test_held_terminal_intent_keeps_exact_record_validation(rig, monkeypatch):
    entered, attempt, refused, proceed = (threading.Event() for _ in range(4))

    async def phase(task, runtime, context):
        entered.set()
        assert await asyncio.to_thread(attempt.wait, 10)
        with pytest.raises(RecordError, match="^Missing or unknown record fields$"):
            await runtime.commit(lambda tx, row: {**DONE, "checkpoint": None})
        refused.set()
        assert await asyncio.to_thread(proceed.wait, 10)
        await runtime.commit(lambda tx, row: DONE)

    async def unavailable(*args):
        raise AssertionError("Uninstalled child definition cannot run")

    source = definition("owned.validation.parent", phase)
    child_source = definition("owned.validation.child", unavailable)
    parent = rig.create(source)
    child = make_child(rig, parent, child_source)
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(10)
        before = rig.silence()
        attempt.set()
        assert refused.wait(10)
        rig.assert_silent(before)
        assert rig.record(parent)["state"]["status"] == "running"
    finally:
        attempt.set()
        proceed.set()
    assert states.wait(parent, "completing")["state"] == HELD
    passes.idle()
    assert rig.record(child)["state"]["status"] == "pending"
    states.unsubscribe()


def test_same_candidate_mint_and_completing_still_refuses_assembly(rig, monkeypatch):
    entered, release, refused = threading.Event(), threading.Event(), threading.Event()
    minted = []

    async def forbidden(*args):
        raise AssertionError("No child can be published by the refused candidate")

    child_source = definition("owned.illegal.child", forbidden)

    async def phase(task, runtime, context):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)

        async def invalid(tx, row):
            minted.append(await tx.create_task(child_source, None))
            return DONE

        with pytest.raises(RuntimeError, match="Task owner .* is completing"):
            await runtime.commit(invalid)
        refused.set()
        await context.signal.wait()

    source = definition("owned.illegal.parent", phase)
    parent = rig.create(source)
    scheduler, _ = start(rig, [source, child_source], monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(10)
        before, commits, mints = (
            published(rig),
            len(rig.storage.commits),
            len(rig.storage.mints),
        )
        parent_before = rig.record(parent)
    finally:
        release.set()
    assert refused.wait(10)
    assert_rollback(rig, before, commits, mints)
    assert rig.record(parent) == parent_before
    assert len(minted) == 1
    assert rig.session.read_on_line(lambda: rig.storage.task(minted[0])) is None


def test_awaited_graph_refusal_uses_actual_current_replacements_and_creations(rig):
    from ordinary_owned_live_support import QueryWindow

    from pipy_harness.native.durable.ordinary_ownership import _capture_async

    async def never(*args):
        raise AssertionError("This genuine Tx fixture does not dispatch phases")

    source = definition("owned.intent.candidates", never)
    owner = rig.create(source)
    child = make_child(rig, owner, source)
    observations = []

    async def apply(tx):
        original = (await tx.task(owner)).to_tree()
        graph = await _capture_async(tx)
        assert graph.refuses(child) is False
        for fields, expected in [
            ({"abortRequested": True}, True),
            (
                {
                    "state": {
                        "status": "completing",
                        "outcome": {
                            "status": "faulted",
                            "error": {"message": "candidate"},
                        },
                    }
                },
                True,
            ),
            ({}, False),
        ]:
            tx.set_task({**original, **fields})
            window = QueryWindow(rig, tx)
            observations.append(graph.refuses(child))
            assert observations[-1] is expected
            assert graph.has(owner) is True
            window.unchanged(rig, tx)
        fresh = await tx.create_task(
            source, None, ownership={"kind": "task", "taskId": owner}
        )
        fresh_child = await tx.create_task(
            source, None, ownership={"kind": "task", "taskId": fresh}
        )
        created = next(
            record.to_tree() for record in tx.staged_tasks() if record.id == fresh
        )
        tx.set_task({**created, "abortRequested": True})
        window = QueryWindow(rig, tx)
        assert graph.refuses(fresh_child) is True
        window.unchanged(rig, tx)
        # Restore the genuine new owner before assembly. This does not claim
        # that creating work under an abort-marked final owner is legal.
        tx.set_task(created)
        window = QueryWindow(rig, tx)
        assert graph.refuses(fresh_child) is False
        window.unchanged(rig, tx)
        return fresh, fresh_child

    fresh, fresh_child = asyncio.run(rig.session.acommit(apply))
    assert observations == [True, True, False]
    assert rig.record(fresh)["abortRequested"] is False
    assert rig.record(fresh_child)["owner"] == fresh


def test_unknown_ordinary_child_still_holds_until_real_definition_publication(
    rig, monkeypatch
):
    calls = []

    async def parent_phase(task, runtime, context):
        calls.append("parent")
        await runtime.commit(lambda tx, row: DONE)

    async def child_phase(task, runtime, context):
        calls.append("child")
        await runtime.commit(lambda tx, row: DONE)

    parent_source = definition("owned.known.parent", parent_phase)
    child_source = definition("owned.not.installed", child_phase)
    parent = rig.create(parent_source)
    child = make_child(rig, parent, child_source)
    scheduler, passes = start(rig, [parent_source], monkeypatch)
    scheduler.resume()
    passes.idle()
    assert calls == ["parent"]
    assert rig.record(parent)["state"] == HELD
    assert rig.record(child)["state"]["status"] == "pending"
    before = rig.silence()
    assert scheduler.install_definition(child_source) is True
    rig.assert_silent(before)
    states = States(rig)
    scheduler.resume()
    states.wait(parent, "terminal")
    passes.idle()
    assert calls == ["parent", "child"]
    assert rig.record(child)["state"] == DONE
    states.unsubscribe()
