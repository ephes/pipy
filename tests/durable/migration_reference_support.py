"""Actual cold Session/Scheduler fixtures; seeds are controls, not crash evidence."""

from __future__ import annotations

import json
import threading
import time
from functools import partial
from importlib import import_module

from restart_reference_support import Recording
from restart_reference_support import origins as origins

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task


def _copy(value):
    return json.loads(json.dumps(value))


def _wait(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "Migration fixture discriminator timed out"
        time.sleep(0.005)


def exercise(recipe, folder):
    owned = []
    try:
        return _exercise(recipe, folder, owned)
    finally:
        errors = []
        for session in reversed(owned):
            try:
                session.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - join every acquired owner
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("Migration fixture Session cleanup failed", errors)


def _definition(initial, phase, abort, lock, calls, version, mode, owner):
    def migrate(value, checkpoint, from_version):
        with lock:
            calls.append(
                {
                    "input": _copy(value),
                    "checkpoint": _copy(checkpoint),
                    "from_version": from_version,
                    "owner": owner,
                }
            )
        if mode == "throw":
            raise ValueError("synthetic-migration-private-marker")
        if mode == "invalid":
            return None  # Deliberate invalid return; both runtimes actually refuse it.
        return {
            "input": {**value, "migrated": True} if mode == "change" else value,
            "checkpoint": {"phase": "run", "n": 3} if mode == "change" else checkpoint,
        }

    return define_task(
        "fixture.migration",
        version,
        initial,
        {"run": phase},
        abort,
        **({} if mode == "none" else {"migrate": migrate}),
    )


def _owner(path, task_id, following, owned, definition, version, mode, tag):
    store = Recording(open_sqlite_storage(str(path), mode="open"))
    store.id = task_id
    session = Session(store, now=lambda: 1000)
    owned.append(session)
    definitions = [following]
    if version is not None:
        definitions.append(definition(version, mode, tag))
    scheduler = TaskScheduler(session, definitions)
    scheduler.open()
    return session, scheduler, store


def _row(task_id, session):
    return session.commit(lambda tx: tx.task(task_id)).to_tree()


def _info(task_id, scheduler):
    result = scheduler.inspect_task(task_id)
    assert result is not None
    return result.to_tree()


def _close(closed, lifetimes, session, scheduler, store):
    session.close(timeout=10)
    closed.append(session.end.reason)
    lifetimes.append({"host_thread_alive_after_close": scheduler._thread.is_alive()})
    assert not scheduler._thread.is_alive()
    assert store.last is not None
    return _copy(store.last)


def _exercise(recipe, folder, owned):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    lock = threading.Lock()
    calls, handlers, second, closed, lifetimes = [], [], [], [], []
    initializers = 0

    def initial(value):
        nonlocal initializers
        initializers += 1
        return {"phase": "run", "n": 0}

    async def abort(*args):
        raise AssertionError("Abort is outside cold migration common profile")

    async def phase(task, runtime, context):
        with lock:
            handlers.append(_copy(task))
        await runtime.commit(
            lambda tx, row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": {"ok": True}},
            }
        )

    async def second_phase(task, runtime, context):
        with lock:
            second.append(_copy(task))
        await runtime.commit(
            lambda tx, row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": {"second": True}},
            }
        )

    definition = partial(_definition, initial, phase, abort, lock, calls)

    old = definition(recipe["stored"], "none", "create")
    following = define_task(
        "fixture.migration.following",
        1,
        lambda value: {"phase": "run"},
        {"run": second_phase},
        abort,
    )
    create = Session(open_sqlite_storage(str(path), mode="create"), now=lambda: 1000)
    owned.append(create)
    cid = create.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task_id = create.commit(
        lambda tx: tx.create_task(
            old,
            {"fixture": recipe["id"], "n": 1},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    created = create.commit(lambda tx: tx.task(task_id)).to_tree()
    # Explicit typed seed of checkpoint/memos/streak, never an asserted crash.
    create.commit(
        lambda tx: tx.set_task(
            {
                **tx.task(task_id).to_tree(),
                "state": {"status": "pending", "checkpoint": {"phase": "run", "n": 2}},
                "memos": {"fixture.memo": {"kept": True}},
                "restarts": 5 if recipe["parked"] else 2,
            }
        )
    )
    second_id = None
    if recipe["mode"] in {"throw", "invalid"}:
        second_id = create.commit(
            lambda tx: tx.create_task(
                following, None, ownership={"kind": "conversation"}, conversation_id=cid
            )
        )
    seeded = create.commit(lambda tx: tx.task(task_id)).to_tree()
    create.close()

    owner = partial(_owner, path, task_id, following, owned, definition)

    row = partial(_row, task_id)

    info = partial(_info, task_id)

    close = partial(_close, closed, lifetimes)

    session, scheduler, store = owner(recipe["current"], recipe["mode"], "first")
    before = row(session)
    inspection_open = info(scheduler)
    with lock:
        calls_at_open = len(calls) + len(handlers) + len(second)
    if recipe["parked"]:
        assert inspection_open["restart_parked"]
        private_retry_return = scheduler.retry(task_id)
    else:
        private_retry_return = None
        scheduler.resume()
    blocked = recipe["blocked"] is not None
    if blocked:
        _wait(lambda: info(scheduler)["reason"] == recipe["blocked"])
        if second_id is not None:
            _wait(
                lambda: (
                    session.commit(lambda tx: tx.task(second_id)).to_tree()["state"][
                        "status"
                    ]
                    == "terminal"
                )
            )
        after_attempt = row(session)
        inspection_attempt = info(scheduler)
        with lock:
            before_restore_calls = len(calls)
        for _ in range(3):
            scheduler.resume()
            assert info(scheduler)["reason"] == recipe["blocked"]
        with lock:
            repeated_calls = len(calls)
        blocked_unchanged = after_attempt == before
        close(session, scheduler, store)
        current = (
            recipe["current"] if recipe["current"] is not None else recipe["stored"]
        )
        version = max(recipe["stored"], current) + 1
        session, scheduler, store = owner(version, "change", "restore")
        scheduler.resume()
    else:
        _wait(lambda: row(session)["state"]["status"] == "terminal")
        after_attempt = row(session)
        inspection_attempt = info(scheduler)
        with lock:
            before_restore_calls = repeated_calls = len(calls)
        blocked_unchanged = True  # No blocked interval in this case.
        version = recipe["current"]
    _wait(lambda: row(session)["state"]["status"] == "terminal")
    final = row(session)
    after_close = close(session, scheduler, store)
    session, scheduler, store = owner(version, "change", "restore")
    cold = row(session)
    scheduler.resume()
    cold_inspection = info(scheduler)
    close(session, scheduler, store)
    handler = import_module("durable_reference.migration_values").handler

    with lock:
        actual_calls, actual_handlers, actual_second = (
            _copy(calls),
            _copy(handlers),
            _copy(second),
        )
    assert len(actual_handlers) == 1
    return {
        "id": recipe["id"],
        "control": {
            "kind": "explicit-temporary-task-row-seed",
            "created": created,
            "seeded": seeded,
            "physical_SIGKILL": False,
        },
        "before_open": seeded,
        "after_open": before,
        "after_attempt": after_attempt,
        "after_close": after_close,
        "after_cold_open": cold,
        "final": final,
        "inspection_open": inspection_open,
        "inspection_attempt": inspection_attempt,
        "inspection_cold": cold_inspection,
        "calls_at_open": calls_at_open,
        "initializers": initializers,
        "parked_at_open": inspection_open["restart_parked"],
        "migration_calls": actual_calls,
        "before_restore_calls": before_restore_calls,
        "repeated_calls": repeated_calls,
        "first_handler": handler(actual_handlers[0]),
        "handler_records": actual_handlers,
        "second_handlers": len(actual_second),
        "second_handler_records": actual_second,
        "blocked_reason": inspection_attempt["reason"] if blocked else None,
        "blocked_unchanged": blocked_unchanged,
        "identity_preserved": all(
            (k in final) == (k in seeded) and final.get(k) == seeded.get(k)
            for k in (
                "id",
                "kind",
                "conversationId",
                "background",
                "owner",
                "links",
                "trace",
                "abortRequested",
                "abortReason",
                "abandonOnRestart",
            )
        ),
        "closed": closed,
        "cold_equal_calls": len(actual_calls),
        "private_retry_return": private_retry_return,
        "native_lifetimes": lifetimes,
        "mutations": "Actual task creation, explicit typed seeds, scheduler migration/reservation/terminal/reset/close commits in private SQLite; not zero-effect",
    }
