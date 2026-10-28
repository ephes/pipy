"""Local reservation smoke tests and the built-in migration authority fence."""

import asyncio
import threading

import pytest
from test_task_runtime import abort, create
from test_task_runtime import rig as rig

from pipy_harness.native.durable.generation_tasks import GENERATION_NAME, TOOL_NAME
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.inspection_values import (
    InspectionError,
    InspectionSupport,
)
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import ReentrantSessionCall
from pipy_harness.native.durable.task_values import identity
from pipy_harness.native.durable.tasks import define_task


def test_migration_and_running_are_one_admitted_record(rig):
    session, store = rig
    observed = []
    done = threading.Event()

    async def phase(row, runtime, context):
        observed.append(row)
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )
        done.set()

    original = define_task("generic", -1.5, lambda _: {"phase": "old"}, {}, abort)
    task_id = create(session, original)
    calls = []

    def migrate(value, checkpoint, version):
        calls.append((value, checkpoint, version))
        return {"input": {"migrated": True}, "checkpoint": {"phase": "start"}}

    current = define_task(
        "generic", 0.5, lambda _: None, {"start": phase}, abort, migrate=migrate
    )
    scheduler = TaskScheduler(session, [current])
    scheduler.open()
    assert scheduler.inspect_task(task_id).reason == "migration_required"
    assert calls == []
    before = len(store.commits)
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    assert calls == [({"input": [1]}, {"phase": "old"}, -1.5)]
    assert len(observed) == 1
    assert observed[0]["version"] == 0.5
    assert observed[0]["input"] == {"migrated": True}
    assert observed[0]["state"] == {
        "status": "running",
        "checkpoint": {"phase": "start"},
    }
    writes = [
        w.to_tree()["value"]
        for batch in store.commits[before:]
        for w in batch
        if w.to_tree()["type"] == "task"
    ]
    assert len(writes) == 2
    assert writes[0] == observed[0]
    assert writes[1]["state"]["status"] == "terminal"
    assert scheduler.inspect_task(task_id).compatibility == "terminal_readonly"


@pytest.mark.parametrize("kind", [GENERATION_NAME, TOOL_NAME])
def test_builtin_version_mismatch_never_calls_generic_migrator(rig, kind, monkeypatch):
    session, store = rig
    calls = []
    graph_reads = []
    from pipy_harness.native.durable import tool_graph

    actual = tool_graph.model_call

    async def observed(*args, **kwargs):
        graph_reads.append(True)
        return await actual(*args, **kwargs)

    monkeypatch.setattr(tool_graph, "model_call", observed)

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("Generic migration grants no model authority")

    original = define_task(kind, 1, lambda _: {"phase": "start"}, {}, abort)
    task_id = create(session, original)
    current = define_task(kind, 3, lambda _: None, {}, abort, migrate=forbidden)
    scheduler = TaskScheduler(session, [current])
    scheduler.open()
    before = len(store.commits)
    scheduler.resume()
    asyncio.run(scheduler.apause())
    assert calls == [] and len(store.commits) == before
    assert graph_reads == []
    for report in (
        scheduler.inspect_task(task_id),
        inspect_task(session, task_id, support=InspectionSupport((current,))),
    ):
        assert report.reason == "definition_version_mismatch"
        assert report.compatibility == "unsupported"


def test_failure_is_owner_metadata_and_does_not_starve_next_task(rig):
    session, store = rig
    calls = []
    done = threading.Event()

    def fail(*args):
        calls.append("failed")
        raise ValueError("PRIVATE-MIGRATION-MARKER")

    async def phase(row, runtime, context):
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )
        done.set()

    old = define_task("a", 1, lambda _: {"phase": "old"}, {}, abort)
    failed_id = create(session, old)
    current = define_task("a", 2, lambda _: None, {}, abort, migrate=fail)
    healthy = define_task("b", 1, lambda _: {"phase": "start"}, {"start": phase}, abort)
    create(session, healthy)
    scheduler = TaskScheduler(session, [current, healthy])
    scheduler.open()
    before = session.read_on_line(lambda: store.task(failed_id).to_tree())
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    assert session.failure is None and calls == ["failed"]
    assert session.read_on_line(lambda: store.task(failed_id).to_tree()) == before
    owner = asyncio.run(scheduler.ainspect_task(failed_id))
    assert owner.reason == "migration_failed"
    assert "PRIVATE-MIGRATION-MARKER" not in str(owner.to_tree())
    assert (
        inspect_task(session, failed_id, support=InspectionSupport((current,))).reason
        == "migration_required"
    )


@pytest.mark.parametrize("operation", ["commit", "read", "close", "inspect"])
def test_synchronous_migrator_reentry_refuses_without_poisoning_owner(rig, operation):
    session, store = rig
    entered = threading.Event()
    caught = []
    old = define_task("reentry", 1, lambda _: {"phase": "old"}, {}, abort)
    task_id = create(session, old)

    def migrate(*args):
        try:
            if operation == "commit":
                session.commit(lambda tx: pytest.fail("Reentrant write executed"))
            elif operation == "read":
                session.read_on_line(lambda: pytest.fail("Reentrant read executed"))
            elif operation == "close":
                session.close()
            else:
                scheduler.inspect_task(task_id)
        except ReentrantSessionCall as error:
            caught.append(type(error))
            raise
        except InspectionError as error:
            assert operation == "inspect" and error.code == "unavailable"
            caught.append(type(error))
            raise
        finally:
            entered.set()

    current = define_task("reentry", 2, lambda _: None, {}, abort, migrate=migrate)
    scheduler = TaskScheduler(session, [current])
    scheduler.open()
    before = session.read_on_line(lambda: store.task(task_id).to_tree())
    commits = len(store.commits)
    scheduler.resume()
    assert entered.wait(5)
    asyncio.run(scheduler.apause())
    assert caught == [
        InspectionError if operation == "inspect" else ReentrantSessionCall
    ]
    assert session.failure is None and session.end is None
    assert len(store.commits) == commits
    assert session.read_on_line(lambda: store.task(task_id).to_tree()) == before
    assert scheduler.inspect_task(task_id).reason == "migration_failed"


@pytest.mark.parametrize("tear_down_loop", [False, True])
def test_pause_owns_held_migration_after_waiter_loop_teardown(rig, tear_down_loop):
    session, store = rig
    entered, release, finished = (threading.Event() for _ in range(3))
    calls, handlers, waiters = [], [], []

    def migrate(value, checkpoint, version):
        calls.append(version)
        entered.set()
        assert release.wait(5)
        return {"input": value, "checkpoint": {"phase": "work"}}

    async def work(row, runtime, context):
        handlers.append(row["version"])
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )
        finished.set()

    old = define_task("paused", 1, lambda _: {"phase": "old"}, {}, abort)
    task_id = create(session, old)
    current = define_task(
        "paused", 2, lambda _: None, {"work": work}, abort, migrate=migrate
    )
    scheduler = TaskScheduler(session, [current])
    scheduler.open()
    before = session.read_on_line(lambda: store.task(task_id).to_tree())
    commits = len(store.commits)
    scheduler.resume()

    async def pause_held():
        from test_scheduler_pause import until_disabled

        waiter = asyncio.create_task(scheduler.apause())
        waiters.append(waiter)
        await until_disabled(scheduler)
        with scheduler._guard:
            assert scheduler._draining and scheduler._invocation is None
        assert not waiter.done() and handlers == []
        if not tear_down_loop:
            release.set()
            await asyncio.wait_for(waiter, 5)

    try:
        assert entered.wait(5)
        asyncio.run(pause_held())
        if tear_down_loop:
            assert waiters[0].cancelled()
            with scheduler._guard:
                assert scheduler._draining and not scheduler._enabled
            release.set()
        asyncio.run(scheduler.apause())
        assert calls == [1] and handlers == [] and session.failure is None
        assert len(store.commits) == commits
        assert session.read_on_line(lambda: store.task(task_id).to_tree()) == before
        scheduler.resume()
        assert finished.wait(5)
        asyncio.run(scheduler.apause())
        assert calls == [1, 1] and handlers == [2]
    finally:
        release.set()


def test_failure_cache_terminal_eviction_and_disposal_leave_metadata_guard(rig):
    session, store = rig
    entered = threading.Event()
    drops, pops = [], []

    def fail(*args):
        entered.set()
        raise ValueError("private failure")

    old = define_task("eviction", 1, lambda _: {"phase": "old"}, {}, abort)
    task_id = create(session, old)
    current = define_task("eviction", 2, lambda _: None, {}, abort, migrate=fail)
    scheduler = TaskScheduler(session, [current])
    scheduler.open()
    scheduler.resume()
    assert entered.wait(5)
    asyncio.run(scheduler.apause())

    class ReleasedReference:
        def __init__(self, value):
            self.value = value

        def __del__(self):
            drops.append(("popped", scheduler._guard._is_owned()))

    class ObservedCache(dict):
        def pop(self, key, default=None):
            value = super().pop(key, default)
            pops.append(
                (
                    key,
                    value
                    is scheduler._definition_generation.bindings[identity("eviction")],
                    scheduler._guard._is_owned(),
                )
            )
            return ReleasedReference(value)

        def __del__(self):
            drops.append(("cache", scheduler._guard._is_owned()))

    # Instrument only reference release, keeping the actual retained definition binding and
    # real publication/terminal eviction path. The observer adds no authority.
    with scheduler._guard:
        assert (
            scheduler._migration_failures[task_id]
            is scheduler._definition_generation.bindings[identity("eviction")]
        )
        scheduler._migration_failures = ObservedCache(scheduler._migration_failures)

    def terminate(tx):
        row = tx.task(task_id).to_tree()
        row["state"] = {
            "status": "terminal",
            "outcome": {"status": "completed", "result": None},
        }
        tx.set_task(row)

    session.commit(terminate)
    assert pops == [(task_id, True, True)]
    assert drops == [("popped", False)]
    with scheduler._guard:
        assert task_id not in scheduler._migration_failures
    assert scheduler.inspect_task(task_id).compatibility == "terminal_readonly"
    session.close(timeout=5)
    assert not scheduler._thread.is_alive()
    assert drops == [("popped", False), ("cache", False)]
