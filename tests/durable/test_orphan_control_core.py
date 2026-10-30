"""Initial private orphan-control behavior on genuine Session transactions."""

import asyncio

import pytest
from test_task_runtime import abort, create
from test_task_runtime import rig as rig

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.tasks import define_task


async def never(*args):
    raise AssertionError("Explicit orphaning must not invoke task code")


def declaration(name="missing"):
    return define_task(name, 1, lambda _: {"phase": "work"}, {"work": never}, abort)


def test_orphan_uses_standard_terminal_and_document_retirement(rig):
    session, store = rig
    task_id = create(session, declaration())
    doc = define_doc(
        "orphan-doc",
        1,
        "task",
        lambda: {"value": 1},
        checkpoint_when=lambda _value, _ops, _info: False,
    )

    def prepare(tx):
        tx.doc(doc, task_id=task_id)["value"] = 2
        record = tx.task(task_id).to_tree()
        tx.set_task({**record, "memos": {"held": {"value": 3}}, "restarts": 7.5})

    session.commit(prepare)
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    old = scheduler.inspect()[0]
    address = DocumentAddress(
        {"kind": "orphan-doc", "scope": {"kind": "task", "taskId": task_id}}
    )
    document_id = session.read_on_line(lambda: store.find_document(address).id)
    count = len(store.commits)
    assert scheduler.orphan_missing_definition(task_id) == "orphaned"
    current = scheduler.inspect()[0].to_tree()
    assert current["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "missing_task"},
    }
    assert "memos" not in current and "restarts" not in current
    assert current["abortRequested"] is False and "endedAt" in current
    for key, value in old.to_tree().items():
        if key not in {"state", "memos", "restarts", "endedAt"}:
            assert current[key] == value
    assert old.to_tree()["state"]["status"] == "pending"
    assert session.read_on_line(lambda: store.document(document_id)) is None
    assert session.read_on_line(lambda: store.find_document(address)) is None
    assert len(store.commits) == count + 1
    assert scheduler.orphan_missing_definition(task_id) == "terminal"
    assert asyncio.run(scheduler.aorphan_missing_definition(task_id)) == "terminal"
    assert len(store.commits) == count + 1
    with scheduler._guard:
        assert scheduler._enabled is False


@pytest.mark.parametrize("name", ["present", "pi.tool", "pi.future-private"])
def test_definition_or_reserved_frame_refuses_without_writes(rig, name):
    session, store = rig
    task = declaration(name)
    task_id = create(session, task)
    scheduler = TaskScheduler(session, [task] if name == "present" else [])
    scheduler.open()
    before = scheduler.inspect()
    count = len(store.commits)
    with pytest.raises(UnsupportedTaskExecution):
        scheduler.orphan_missing_definition(task_id)
    assert scheduler.inspect() == before
    assert len(store.commits) == count and session.failure is None


def test_generic_terminal_replay_does_not_scan_or_rewrite(rig, monkeypatch):
    session, store = rig
    task = declaration()
    task_id = create(session, task)

    def complete(tx):
        record = tx.task(task_id).to_tree()
        tx.set_task(
            {
                **record,
                "state": {
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": 3},
                },
            }
        )

    session.commit(complete)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    before = scheduler.inspect()
    count = len(store.commits)

    def unexpected(*args):
        raise AssertionError("Terminal replay must precede graph scans")

    monkeypatch.setattr(store, "scan_tasks", unexpected)
    monkeypatch.setattr(store, "scan_conversations", unexpected)
    assert scheduler.orphan_missing_definition(task_id) == "terminal"
    assert scheduler.inspect() == before and len(store.commits) == count


def test_terminal_owned_child_is_still_not_a_strict_leaf(rig):
    session, store = rig
    task = declaration()
    task_id = create(session, task)

    def child(tx):
        parent = tx.task(task_id).to_tree()
        return tx.create_task(
            task,
            {},
            ownership={"kind": "task", "taskId": task_id},
            conversation_id=parent["conversationId"],
        )

    child_id = session.commit(child)

    def complete(tx):
        record = tx.task(child_id).to_tree()
        tx.set_task(
            {
                **record,
                "state": {
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": None},
                },
            }
        )

    session.commit(complete)
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    count = len(store.commits)
    with pytest.raises(UnsupportedTaskExecution):
        scheduler.orphan_missing_definition(task_id)
    assert len(store.commits) == count and session.failure is None


def test_incoming_waiter_refuses_but_unrelated_waiting_does_not(rig):
    session, store = rig
    task = declaration()
    target = create(session, task)
    unrelated = create(session, task)
    waiter = create(session, task)

    def waiting(tx, dependency):
        record = tx.task(waiter).to_tree()
        tx.set_task(
            {
                **record,
                "background": True,
                "state": {
                    "status": "waiting",
                    "checkpoint": {"phase": "work"},
                    "on": [dependency],
                    "policy": "allSettled",
                },
            }
        )

    session.commit(lambda tx: waiting(tx, target))
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    count = len(store.commits)
    with pytest.raises(UnsupportedTaskExecution):
        scheduler.orphan_missing_definition(target)
    assert len(store.commits) == count
    session.commit(lambda tx: waiting(tx, unrelated))
    assert scheduler.orphan_missing_definition(target) == "orphaned"
