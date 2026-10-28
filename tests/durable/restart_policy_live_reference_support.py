"""Actual generic owner-local publication; seeded rows/trace-only barriers are controls."""

from __future__ import annotations

import asyncio
import json
import threading

from restart_reference_support import Recording
from restart_reference_support import origins as origins

from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task


def limit(value):
    return float("inf") if value == "Infinity" else value


def row(session, store, task_id):
    def read():
        record = store.task(task_id)
        assert record is not None
        return record.to_tree()

    return session.read_on_line(read)


def create(recipe, path, definition, owned):
    store = open_sqlite_storage(str(path), mode="create")
    session = Session(store, now=lambda: 1000)
    owned.append(session)
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task_id = session.commit(
        lambda tx: tx.create_task(
            definition,
            {"fixture": recipe["id"]},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    created = row(session, store, task_id)
    session.commit(
        lambda tx: tx.set_task(
            {**tx.task(task_id).to_tree(), "restarts": recipe["count"]}
        )
    )
    seeded = row(session, store, task_id)
    session.close(timeout=10)
    return task_id, created, seeded


def owner(path, definition, initial, task_id, owned):
    store = Recording(open_sqlite_storage(str(path), mode="open"))
    store.id = task_id
    session = Session(store, now=lambda: 1000)
    owned.append(session)
    scheduler = TaskScheduler(session, [definition], restart_limit=limit(initial))
    scheduler.open()
    return session, scheduler, store


def barriers(scheduler):
    """Observe actual empty reservation completion; never replace its decision."""
    empty = threading.Event()
    original = scheduler._reserve

    async def observed():
        invocation = await original()
        if invocation is None:
            empty.set()
        return invocation

    scheduler._reserve = observed
    notifications = []
    notify = scheduler._notify

    def counted_notify():
        notifications.append("notify")
        return notify()

    scheduler._notify = counted_notify
    return empty, notifications


def joined_close(session, store, closed):
    session.close(timeout=10)
    assert store.last is not None
    closed.append(session.end.reason)
    return store.last


def exercise(recipe, folder):
    owned = []
    failure = None
    try:
        return _exercise(recipe, folder, owned)
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        for session in reversed(owned):
            try:
                session.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - close every acquired owner
                errors.append(error)
        if errors:
            raise BaseExceptionGroup(
                "Live-policy fixture Session cleanup failed",
                ([failure] if failure is not None else []) + errors,
            )


def _exercise(recipe, folder, owned):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    calls, closed = [], []
    lock = threading.Lock()

    async def phase(task, runtime, context):
        with lock:
            calls.append({"present": "restarts" in task, "value": task.get("restarts")})
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": {"ok": True}},
            }
        )

    async def abort(*args):
        raise AssertionError("Abort outside live-policy common profile")

    definition = define_task(
        "fixture.restart.live", 1, lambda value: {"phase": "run"}, {"run": phase}, abort
    )
    task_id, created, seeded = create(recipe, path, definition, owned)
    session, scheduler, store = owner(
        path, definition, recipe["initial"], task_id, owned
    )
    empty, notifications = barriers(scheduler)
    after_open = row(session, store, task_id)
    before = scheduler.inspect_task(task_id)
    assert before is not None
    calls_at_open = len(calls)
    before_update = row(session, store, task_id)
    notify_before = len(notifications)
    returned = (
        asyncio.run(scheduler.aset_restart_limit(limit(recipe["update"])))
        if recipe["async_setter"]
        else scheduler.set_restart_limit(limit(recipe["update"]))
    )
    notification_delta = len(notifications) - notify_before
    assert type(returned) is bool
    assert returned is (recipe["initial"] != recipe["update"])
    assert notification_delta == 0
    after_update = row(session, store, task_id)
    calls_at_update = len(calls)
    after = scheduler.inspect_task(task_id)
    standalone = inspect_task(session, task_id)
    assert after is not None and standalone is not None
    scheduler.resume()
    assert empty.wait(10), "Actual scheduler empty reservation pass did not finish"
    after_resume = row(session, store, task_id)
    calls_after_resume = len(calls)
    first_close = joined_close(session, store, closed)
    session, scheduler, store = owner(path, definition, recipe["fresh"], task_id, owned)
    fresh_info = scheduler.inspect_task(task_id)
    assert fresh_info is not None
    empty, _ = barriers(scheduler)
    before_final = row(session, store, task_id)
    calls_before_final = len(calls)
    scheduler.resume()
    assert empty.wait(10), "Fresh owner's actual reservation pass did not finish"
    final = row(session, store, task_id)
    after_close = joined_close(session, store, closed)
    return {
        "id": recipe["id"],
        "control": {
            "kind": "explicit-temporary-task-row-seed",
            "created": created,
            "seeded": seeded,
            "physical_SIGKILL": False,
            "native_observer": "trace-only actual _reserve return; existing _notify forwarded unchanged",
        },
        "after_open": after_open,
        "before_update": before_update,
        "after_update": after_update,
        "calls_at_open": calls_at_open,
        "calls_at_update": calls_at_update,
        "parked_before": before.restart_parked,
        "parked_after": after.restart_parked,
        "after_resume": after_resume,
        "calls_after_resume": calls_after_resume,
        "first_close": first_close,
        "before_final_resume": before_final,
        "calls_before_final_resume": calls_before_final,
        "after_final_resume": final,
        "after_close": after_close,
        "phase_counts": json.loads(json.dumps(calls)),
        "closed": closed,
        "native_only": {
            "setter_return": returned,
            "setter_notify_calls": notification_delta,
            "async_setter": recipe["async_setter"],
            "owner_before": before.to_tree(),
            "owner_after": after.to_tree(),
            "standalone_default_after": standalone.to_tree(),
            "fresh_owner": fresh_info.to_tree(),
        },
        "mutations": "Actual creation/seed, owner recovery, explicit resume/terminal/close commits in temporary SQLite. Setter authority observations are separated from later writes; not a blanket zero-effect claim.",
    }
