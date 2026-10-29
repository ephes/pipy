"""Private older-code settlement, using genuine Session records and captured code."""

import asyncio

import pytest
from test_task_runtime import create
from test_task_runtime import rig as rig

from pipy_harness.native.durable import orphan_control
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session_core import ReentrantSessionCall
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.tasks import define_task


def declaration(version, calls, name="older-code"):
    def initial(value):
        calls.append("initial")
        return {"phase": "work"}

    async def phase(*args):
        calls.append("phase")
        raise AssertionError("Orphan control must not invoke task code")

    async def abort(*args):
        calls.append("abort")
        raise AssertionError("Orphan control must not invoke abort")

    def migrate(*args):
        calls.append("migrate")
        raise AssertionError("Orphan control must not invoke migration")

    return define_task(name, version, initial, {"work": phase}, abort, migrate=migrate)


def unexpected(*args):
    raise AssertionError("Explicit settlement must bypass fitting and terminal lookups")


def test_older_settlement_uses_terminal_abi_and_retires_documents(rig, monkeypatch):
    session, store = rig
    calls = []
    task_id = create(session, declaration(2, calls))
    available = declaration(1, calls)
    doc = define_doc("older-doc", 1, "task", lambda: {"value": 1})

    def prepare(tx):
        tx.doc(doc, task_id=task_id)["value"] = 2
        record = tx.task(task_id).to_tree()
        tx.set_task({**record, "memos": {"held": {"value": 3}}, "restarts": 7.5})

    session.commit(prepare)
    scheduler = TaskScheduler(session, [available])
    scheduler.open()
    old = scheduler.inspect()[0]
    address = DocumentAddress(
        {"kind": "older-doc", "scope": {"kind": "task", "taskId": task_id}}
    )
    document_id = session.read_on_line(lambda: store.find_document(address).id)
    count = len(store.commits)
    assert calls == ["initial"]
    monkeypatch.setattr(TaskScheduler, "_fit", unexpected)
    monkeypatch.setattr(TaskScheduler, "_fitting_definition", unexpected)
    assert asyncio.run(scheduler.aorphan_task_too_old(task_id)) == "orphaned"
    current = scheduler.inspect()[0].to_tree()
    assert current["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "task_too_old"},
    }
    assert "memos" not in current and "restarts" not in current
    assert current["abortRequested"] is False and "endedAt" in current
    for key, value in old.to_tree().items():
        if key not in {"state", "memos", "restarts", "endedAt"}:
            assert current[key] == value
    assert old.to_tree()["state"]["status"] == "pending"
    assert session.read_on_line(lambda: store.document(document_id)) is None
    assert session.read_on_line(lambda: store.find_document(address)) is None
    assert scheduler.orphan_task_too_old(task_id) == "terminal"
    assert len(store.commits) == count + 1 and calls == ["initial"]
    with scheduler._guard:
        assert scheduler._enabled is False


@pytest.mark.parametrize("version", [None, 2, 3])
def test_present_strictly_older_is_required_without_fitting(rig, monkeypatch, version):
    session, store = rig
    calls = []
    task_id = create(session, declaration(2, calls))
    scheduler = TaskScheduler(
        session, [] if version is None else [declaration(version, calls)]
    )
    scheduler.open()
    before = scheduler.inspect()
    count = len(store.commits)
    monkeypatch.setattr(TaskScheduler, "_fit", unexpected)
    monkeypatch.setattr(TaskScheduler, "_fitting_definition", unexpected)
    with pytest.raises(
        UnsupportedTaskExecution,
        match="^Task definition is not older than the stored task$",
    ):
        scheduler.orphan_task_too_old(task_id)
    assert scheduler.inspect() == before and len(store.commits) == count
    assert calls == ["initial"] and session.failure is None


def test_missing_and_older_apis_remain_distinct(rig):
    session, store = rig
    calls = []
    older = create(session, declaration(2, calls))
    missing = create(session, declaration(2, calls, "missing"))
    scheduler = TaskScheduler(session, [declaration(1, calls)])
    scheduler.open()
    count = len(store.commits)
    with pytest.raises(UnsupportedTaskExecution, match="^Task definition is present$"):
        scheduler.orphan_missing_definition(older)
    with pytest.raises(UnsupportedTaskExecution, match="not older"):
        scheduler.orphan_task_too_old(missing)
    assert len(store.commits) == count
    assert scheduler.orphan_missing_definition(missing) == "orphaned"
    assert scheduler.orphan_task_too_old(older) == "orphaned"
    rows = {r.id: r.to_tree() for r in scheduler.inspect()}
    assert rows[missing]["state"]["outcome"]["reason"] == "missing_task"
    assert rows[older]["state"]["outcome"]["reason"] == "task_too_old"


@pytest.mark.parametrize(
    ("stored", "available", "allowed"),
    [
        (0.0, -0.0, False),
        (-0.0, 0, False),
        (-1, -1.5, True),
        (-1.5, -1, False),
        (1.5, 1.25, True),
    ],
)
def test_numeric_order_preserves_existing_version_domain(
    rig, stored, available, allowed
):
    session, store = rig
    calls = []
    task_id = create(session, declaration(stored, calls))
    scheduler = TaskScheduler(session, [declaration(available, calls)])
    scheduler.open()
    before = scheduler.inspect()
    count = len(store.commits)
    if allowed:
        assert scheduler.orphan_task_too_old(task_id) == "orphaned"
        assert len(store.commits) == count + 1
    else:
        with pytest.raises(UnsupportedTaskExecution, match="not older"):
            scheduler.orphan_task_too_old(task_id)
        assert scheduler.inspect() == before and len(store.commits) == count
    assert calls == ["initial"]


@pytest.mark.parametrize(
    ("captured", "reflected", "allowed"), [(1, 3, True), (3, 1, False)]
)
def test_captured_version_not_mutable_source_is_authority(
    rig, captured, reflected, allowed
):
    session, store = rig
    calls = []
    task_id = create(session, declaration(2, calls))
    source = declaration(captured, calls)
    scheduler = TaskScheduler(session, [source])
    scheduler.open()
    count = len(store.commits)
    object.__setattr__(source, "version", reflected)
    if allowed:
        assert scheduler.orphan_task_too_old(task_id) == "orphaned"
        assert len(store.commits) == count + 1
    else:
        with pytest.raises(UnsupportedTaskExecution, match="not older"):
            scheduler.orphan_task_too_old(task_id)
        assert len(store.commits) == count
    assert calls == ["initial"]


@pytest.mark.parametrize("available", [None, 1, 2, 3])
@pytest.mark.parametrize("name", ["older-code", "pi.tool"])
def test_terminal_precedes_graph_and_generation_but_not_reserved_gate(
    rig, monkeypatch, available, name
):
    session, store = rig
    calls = []
    task_id = create(session, declaration(2, calls, name))

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
    scheduler = TaskScheduler(
        session, [] if available is None else [declaration(available, calls, name)]
    )
    scheduler.open()
    before = scheduler.inspect()
    count = len(store.commits)
    monkeypatch.setattr(orphan_control, "require_unmarked_root_leaf", unexpected)
    monkeypatch.setattr(orphan_control, "identity", unexpected)
    if name.startswith("pi."):
        with pytest.raises(UnsupportedTaskExecution, match="Built-in tasks"):
            scheduler.orphan_task_too_old(task_id)
    else:
        assert scheduler.orphan_task_too_old(task_id) == "terminal"
    assert scheduler.inspect() == before and len(store.commits) == count


@pytest.mark.parametrize("value", [True, 1.0, 0, 2**53])
def test_exact_id_validation_precedes_unopened_lifecycle(rig, value):
    session, store = rig
    scheduler = TaskScheduler(session, [])
    count = len(store.commits)
    with pytest.raises(RecordError):
        scheduler.orphan_task_too_old(value)
    with pytest.raises(RecordError):
        asyncio.run(scheduler.aorphan_task_too_old(value))
    with pytest.raises(RuntimeError, match="Open the scheduler"):
        scheduler.orphan_task_too_old(1)
    assert len(store.commits) == count


def test_reentry_offloop_and_unknown_id_preserve_existing_precedence(rig):
    session, store = rig
    scheduler = TaskScheduler(session, [])
    scheduler.open()

    class Hostile(int):
        def __int__(self):
            pytest.fail("Caller ID must not be coerced")

    count = len(store.commits)
    with pytest.raises(ReentrantSessionCall):
        session.read_on_line(lambda: scheduler.orphan_task_too_old(Hostile(1)))

    async def scenario():
        with pytest.raises(RuntimeError, match="aorphan_task_too_old"):
            scheduler.orphan_task_too_old(Hostile(1))
        with pytest.raises(RecordError):
            await scheduler.aorphan_task_too_old(Hostile(1))

    asyncio.run(scenario())
    with pytest.raises(ValueError, match="^Unknown task$"):
        scheduler.orphan_task_too_old(999999)
    assert len(store.commits) == count and session.failure is None
