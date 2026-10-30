"""Finite ordinary-wait semantics through real runtime commits and Session lines."""

import asyncio
import threading

import pytest
from owned_completing_support import DONE, States, definition, make_child, start
from task_migration_support import Rig

from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution

CHECKPOINT = {"phase": "finish", "saved": [1, None, {"value": -0.0}]}
FAULTED = {
    "status": "terminal",
    "outcome": {"status": "faulted", "error": {"message": "ended"}},
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


async def forbidden(*args):
    raise AssertionError("Graph validation must not invoke a target's phase")


def wait(on, *, policy="allSettled"):
    return {"status": "waiting", "checkpoint": CHECKPOINT, "on": on, "policy": policy}


def test_real_child_progress_then_wait_reenters_exact_checkpoint(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, children = [], []

    async def child_phase(task, runtime, context):
        calls.append("child")
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(lambda tx, row: DONE)

    child = definition("pi.user-child", child_phase)

    async def create(task, runtime, context):
        calls.append("create")

        async def change(tx, row):
            children.append(await tx.create_task(child, None))
            return {"status": "running", "checkpoint": {"phase": "wait"}}

        await runtime.commit(change)

    async def waiting(task, runtime, context):
        calls.append("wait")
        await runtime.commit(lambda tx, row: wait(children))

    async def finish(task, runtime, context):
        calls.append("finish")
        assert task["state"] == {"status": "running", "checkpoint": CHECKPOINT}
        assert (await asyncio.to_thread(rig.record, children[0]))["state"] == DONE
        await runtime.commit(lambda tx, row: DONE)

    parent = definition(
        "pi.user-parent",
        create,
        phases={"start": create, "wait": waiting, "finish": finish},
    )
    root = rig.create(parent)
    states = States(rig)
    scheduler, passes = start(rig, [parent, child], monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(10)
        assert rig.record(root)["state"] == wait(children)
        assert calls == ["create", "wait", "child"]
    finally:
        release.set()
    assert states.wait(root, "terminal")["state"] == DONE
    passes.idle()
    assert calls == ["create", "wait", "child", "finish"]
    states.unsubscribe()


@pytest.mark.parametrize(
    "target_status", ["pending", "running", "waiting", "sleeping", "completing"]
)
def test_every_nonterminal_target_blocks_and_faulted_terminal_releases(
    rig, monkeypatch, target_status
):
    target_source = definition("missing-target", forbidden)
    target = rig.create(target_source)
    if target_status == "completing":
        make_child(rig, target, target_source)
    calls = []

    async def first(task, runtime, context):
        calls.append("start")
        await runtime.commit(lambda tx, row: wait([target]))

    async def finish(task, runtime, context):
        calls.append("finish")
        assert task["state"]["checkpoint"] == CHECKPOINT
        await runtime.commit(lambda tx, row: DONE)

    source = definition("waiter", first, phases={"start": first, "finish": finish})
    root = rig.create(source)
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    # Apply the live state after metadata-only open: running is not a fake cold cut.
    state = {"status": target_status, "checkpoint": {"phase": "start"}}
    if target_status == "waiting":
        state.update(on=[], policy="allSettled")
    elif target_status == "sleeping":
        state["until"] = 0
    elif target_status == "completing":
        state = {"status": "completing", "outcome": DONE["outcome"]}
    rig.change(target, lambda row: row.update(state=state))
    scheduler.resume()
    states.wait(root, "waiting")
    passes.idle()
    assert calls == ["start"] and rig.record(root)["state"] == wait([target])
    assert rig.record(target)["state"] == state
    rig.change(target, lambda row: row.update(state=FAULTED))
    scheduler.resume()
    assert states.wait(root, "terminal")["state"] == DONE
    passes.idle()
    assert calls == ["start", "finish"]
    states.unsubscribe()


@pytest.mark.parametrize("members", ["empty", "duplicate-terminal"])
def test_empty_and_duplicate_terminal_waits_preserve_checkpoint(
    rig, monkeypatch, members
):
    target = rig.create(definition("missing-terminal", forbidden))
    rig.change(target, lambda row: row.update(state=FAULTED))
    on = [] if members == "empty" else [target, target]
    calls = []

    async def first(task, runtime, context):
        calls.append("start")
        await runtime.commit(lambda tx, row: wait(on))

    async def finish(task, runtime, context):
        calls.append(task["state"]["checkpoint"])
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "pi.user-empty", first, phases={"start": first, "finish": finish}
    )
    root = rig.create(source)
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    assert states.wait(root, "terminal")["state"] == DONE
    passes.idle()
    assert calls == ["start", CHECKPOINT]
    assert any(row["id"] == root and row["state"] == wait(on) for row in states.rows)
    states.unsubscribe()


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "failFast-empty",
        "failFast-failed",
        "boolean-id",
        "missing-policy",
        "extra-state-field",
    ],
)
def test_refusal_is_typed_and_durable_no_write_without_poisoning_owner(
    rig, monkeypatch, case
):
    target = rig.create(definition("missing-terminal", forbidden))
    rig.change(target, lambda row: row.update(state=FAULTED))
    state = wait([target])
    expected: type[Exception] = UnsupportedTaskExecution
    if case == "unknown":
        state["on"] = [10**9]
    elif case.startswith("failFast"):
        state["policy"] = "failFast"
        if case == "failFast-empty":
            state["on"] = []
    else:
        expected = RecordError
        if case == "boolean-id":
            state["on"] = [True]
        elif case == "missing-policy":
            del state["policy"]
        else:
            state["unexpected"] = True
    caught = []

    async def phase(task, runtime, context):
        before = await asyncio.to_thread(rig.silence)
        with pytest.raises(expected) as failure:
            await runtime.commit(lambda tx, row: state)
        caught.append(failure.value)
        await asyncio.to_thread(rig.assert_silent, before)
        await runtime.commit(lambda tx, row: DONE)

    source = definition("refusing-waiter", phase)
    root = rig.create(source)
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    assert states.wait(root, "terminal")["state"] == DONE
    passes.idle()
    assert len(caught) == 1 and type(caught[0]) is expected
    assert rig.session.failure is None
    states.unsubscribe()


@pytest.mark.parametrize(
    "case",
    [
        "self",
        "pair",
        "owned-parent",
        "explicit-background",
        "owned-background",
        "direct-child",
        "marked-target",
        "terminal-target",
    ],
)
def test_real_combined_cycle_refusals_and_literal_positive_neighbors(
    rig, monkeypatch, case
):
    rejected = case in {
        "self",
        "pair",
        "owned-parent",
        "explicit-background",
        "owned-background",
    }
    on, caught = [], []

    async def first(task, runtime, context):
        if rejected:
            before = await asyncio.to_thread(rig.silence)
            with pytest.raises(
                UnsupportedTaskExecution, match="wait would create a cycle"
            ) as failure:
                await runtime.commit(lambda tx, row: wait(on))
            caught.append(failure.value)
            await asyncio.to_thread(rig.assert_silent, before)
            await runtime.commit(lambda tx, row: DONE)
        else:
            await runtime.commit(lambda tx, row: wait(on))

    async def finish(task, runtime, context):
        await runtime.commit(lambda tx, row: DONE)

    source = definition(
        "cycle-waiter", first, phases={"start": first, "finish": finish}
    )
    missing = definition("unknown-cycle-node", forbidden)
    if case == "owned-parent":
        target = rig.create(missing)
        root = make_child(rig, target, source)
    else:
        root = rig.create(source)
        if case == "self":
            target = root
        elif case in {"direct-child", "owned-background"}:
            target = make_child(rig, root, missing)
            if case == "owned-background":
                # Genuine native set_task boundary; no Pi construction parity.
                rig.change(target, lambda row: row.update(background=True))
        else:
            target = rig.session.commit(
                lambda tx: tx.create_task(
                    missing,
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=rig.conversation,
                    background=case == "explicit-background",
                )
            )
    on.append(target)
    if case in {"pair", "explicit-background", "owned-background", "marked-target"}:
        rig.change(
            target,
            lambda row: row.update(
                state=wait([root]), abortRequested=case == "marked-target"
            ),
        )
    elif case == "terminal-target":
        descendant = make_child(rig, target, missing)
        rig.change(descendant, lambda row: row.update(state=wait([root])))
        rig.change(target, lambda row: row.update(state=DONE))
    states = States(rig)
    scheduler, passes = start(rig, [source], monkeypatch)
    scheduler.resume()
    expected = "terminal" if rejected or case == "terminal-target" else "waiting"
    states.wait(root, expected)
    passes.idle()
    assert len(caught) == int(rejected)
    assert rig.record(root)["state"] == (DONE if expected == "terminal" else wait(on))
    assert rig.session.failure is None
    states.unsubscribe()
