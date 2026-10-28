"""Generic actual Session/TaskScheduler fixtures. Seeds are controls, not crash producers."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task

NOTE = define_doc("fixture.restart.note", 1, "task", lambda: {"writes": 0})


def origins(root):
    import hashlib

    result = {}
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename is not None:
            p = Path(filename).resolve()
            if p.is_relative_to(root) and p.suffix == ".py":
                result[str(p.relative_to(root))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
    return result


class Recording:
    def __init__(self, inner):
        self.inner, self.id, self.last = inner, None, None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        if self.id is not None:
            row = self.inner.task(self.id)
            self.last = None if row is None else row.to_tree()
        return self.inner.close()


def exercise(recipe, folder):
    """Every acquired Session remains owned even when a discriminator fails."""
    owned = []
    try:
        return _exercise(recipe, folder, owned)
    finally:
        errors = []
        for session in reversed(owned):
            try:
                session.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - join every owned Session
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("Restart fixture Session cleanup failed", errors)


def _phase(lock, calls, mode, reached, done):
    async def phase(task, runtime, context):
        frame = task["state"]["checkpoint"]
        with lock:
            calls.append(
                {
                    "phase": frame["phase"],
                    "count": {
                        "present": "restarts" in task,
                        "value": task.get("restarts"),
                    },
                }
            )
        with lock:
            action = mode["value"]
        if action == "progress" and frame["phase"] == "run":
            await runtime.commit(
                lambda tx, row: {
                    "status": "running",
                    "checkpoint": {"phase": "next", "n": 1},
                }
            )
            return
        if action == "chatter":
            await runtime.memo("fixture.memo", {"kept": True})

            async def write(tx, row):
                note = await tx.doc(NOTE)
                note["writes"] += 1

            await runtime.commit(write)
            await runtime.commit(
                lambda tx, row: {
                    "status": "running",
                    "checkpoint": dict(
                        reversed(list(row["state"]["checkpoint"].items()))
                    ),
                }
            )
        if action in {"progress", "chatter", "hold"}:
            reached.set()
            await context.signal.wait()
            return
        await runtime.commit(
            lambda tx, row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": {"ok": True}},
            }
        )
        done.set()

    return phase


def _owner(path, task_id, definition, limit, owned):
    store = Recording(open_sqlite_storage(str(path), mode="open"))
    store.id = task_id
    session = Session(store, now=lambda: 1000)
    owned.append(session)
    scheduler = (
        TaskScheduler(session, [definition])
        if limit == "default"
        else TaskScheduler(
            session,
            [definition],
            restart_limit=float("inf") if limit == "Infinity" else limit,
        )
    )
    scheduler.open()
    info = inspect_task(
        session,
        task_id,
        restart_limit=5
        if limit == "default"
        else float("inf")
        if limit == "Infinity"
        else limit,
    )
    return session, scheduler, store, info


def _create(recipe, path, definition, owned):
    create_store = open_sqlite_storage(str(path), mode="create")
    create = Session(create_store, now=lambda: 1000)
    owned.append(create)
    cid = create.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task_id = create.commit(
        lambda tx: tx.create_task(
            definition,
            {"fixture": recipe["id"]},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    created = create.commit(lambda tx: tx.task(task_id)).to_tree()

    def seed(tx):
        row = tx.task(task_id).to_tree()
        if recipe["count"] is not None:
            row["restarts"] = recipe["count"]
        if recipe["running"]:
            row["state"]["status"] = "running"
        tx.set_task(row)

    create.commit(seed)
    seeded = create.commit(lambda tx: tx.task(task_id)).to_tree()
    create.close()

    return task_id, created, seeded


def _exercise(recipe, folder, owned):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    lock = threading.Lock()
    calls = []
    closed = []
    limits = []
    reached, done = threading.Event(), threading.Event()
    mode = {"value": "complete"}

    def trace():
        with lock:
            return json.loads(json.dumps(calls))

    phase = _phase(lock, calls, mode, reached, done)

    async def abort(*args):
        raise AssertionError("Abort is outside restart common profile")

    definition = define_task(
        "fixture.restart",
        1,
        lambda x: {"phase": "run", "n": 0},
        {"run": phase, "next": phase},
        abort,
    )
    task_id, created, seeded = _create(recipe, path, definition, owned)

    def owner(limit):
        return _owner(path, task_id, definition, limit, owned)

    def row(session):
        return session.commit(lambda tx: tx.task(task_id)).to_tree()

    def set_mode(value):
        with lock:
            mode["value"] = value

    def close(session, store):
        session.close(timeout=10)
        closed.append(session.end.reason)
        assert store.last is not None
        return store.last

    session, scheduler, store, info = owner(recipe["limit"])
    after_open = row(session)
    calls_at_open = len(trace())
    parked = info.restart_parked
    limits.append(info.restart_limit)
    action = recipe["action"]
    retry_return = None
    first_close = None
    higher = None
    unlimited_control = None
    if action == "fresh":
        first_close = close(session, store)
        session, scheduler, store, info = owner(6)
        limits.append(info.restart_limit)
        assert len(trace()) == 0
        set_mode("hold")
        reached.clear()
        scheduler.resume()
        assert reached.wait(10)
        higher = row(session)
        close(session, store)
        control_store = open_sqlite_storage(str(path), mode="open")
        control_session = Session(control_store, now=lambda: 1000)
        owned.append(control_session)
        control_before = row(control_session)
        control_session.commit(
            lambda tx: tx.set_task({**tx.task(task_id).to_tree(), "restarts": 1e100})
        )
        unlimited_control = {"before": control_before, "after": row(control_session)}
        control_session.close()
        session, scheduler, store, info = owner("Infinity")
        limits.append(info.restart_limit)
        set_mode("complete")
        done.clear()
        scheduler.resume()
        assert done.wait(10)
    elif action in {"retry", "noop"}:
        set_mode("complete")
        done.clear()
        retry_return = scheduler.retry(task_id)
        assert done.wait(10)
    else:
        set_mode(action)
        reached.clear()
        scheduler.resume()
        assert reached.wait(10)
    after_action = row(session)
    chatter_evidence = None
    if action == "chatter":
        note = session.commit(lambda tx: dict(tx.doc(NOTE, task_id=task_id)))
        chatter_evidence = {
            "document": note["writes"],
            "memo": after_action.get("memos", {}).get("fixture.memo"),
        }
    after_close = close(session, store)
    session, scheduler, store, info = owner(
        "Infinity" if action == "fresh" else recipe["limit"]
    )
    after_reopen = row(session)
    calls_before_cold_close = len(trace())
    close(session, store)
    assert len(trace()) == calls_before_cold_close
    return {
        "id": recipe["id"],
        "control": {
            "kind": "explicit-temporary-task-row-seed",
            "created": created,
            "seeded": seeded,
            "physical_SIGKILL": False,
        },
        "before_open": seeded,
        "after_open": after_open,
        "calls_at_open": calls_at_open,
        "parked": parked,
        "after_action": after_action,
        "after_close": after_close,
        "after_reopen": after_reopen,
        "phases": trace(),
        "chatter_evidence": chatter_evidence,
        "limits": limits,
        "closed": closed,
        "private_retry_return": retry_return,
        "unlimited_control": unlimited_control,
        "first_policy_close": first_close,
        "higher_policy_record": higher,
        "mutations": "Task creation, explicit seed commit, real scheduler/progress/memo/document/reset/terminal/close commits in private SQLite store; not zero-effect",
    }
