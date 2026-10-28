"""Created SQLite tasks and actual private publication; trace-only owned observers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
from contextlib import closing
from importlib import import_module

from restart_reference_support import Recording
from restart_reference_support import origins as origins

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task

values = import_module("durable_reference.definition_publication_values")


def sqlite_snapshot(path):
    """Read all actual tables/revisions in one readonly SQLite transaction."""

    def value(x):
        return {"sqlite_blob_hex": x.hex()} if type(x) is bytes else x

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db, db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result = {}
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            quoted = '"' + name.replace('"', '""') + '"'
            cursor = db.execute("SELECT * FROM " + quoted)
            rows = [[value(x) for x in r] for r in cursor.fetchall()]
            result[name] = {
                "columns": [r[0] for r in cursor.description],
                "rows": sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)),
            }
        return result


class Retirement:
    """Forward this owner's real event/notify; never assign scheduler state."""

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.condition = threading.Condition()
        self._rows = []
        self._notifications = []
        self.original_set = scheduler._quiescent.set
        self.original_notify = scheduler._notify
        scheduler._quiescent.set = self.event_set
        scheduler._notify = self.notify

    def event_set(self):
        self.original_set()
        with self.scheduler._guard:
            row = {
                "draining": self.scheduler._draining,
                "invocation_none": self.scheduler._invocation is None,
            }
        with self.condition:
            self._rows.append(row)
            self.condition.notify_all()

    def notify(self):
        frame = sys._getframe(1)
        row = {"source": frame.f_code.co_filename, "function": frame.f_code.co_name}
        del frame
        with self.condition:
            self._notifications.append(row)
        return self.original_notify()

    def checkpoint(self):
        with self.condition:
            return len(self._rows)

    def notification_snapshot(self):
        with self.condition:
            return [dict(row) for row in self._notifications]

    def snapshot(self):
        with self.condition:
            return {
                "rows": [dict(row) for row in self._rows],
                "notifications": [dict(row) for row in self._notifications],
            }

    def wait(self, previous):
        with self.condition:
            assert self.condition.wait_for(
                lambda: len(self._rows) > previous, timeout=10
            ), "Actual owner drain retirement missing"
            row = dict(self._rows[-1])
        assert row == {"draining": False, "invocation_none": True}
        # A real retirement can be followed by a pending successor wake. Wait on
        # this actual event on its owning loop; do not infer lasting idleness
        # from the historical row, pause, force flags, or retry publication.
        asyncio.run_coroutine_threadsafe(
            self.scheduler._quiescent.wait(), self.scheduler._loop
        ).result(10)
        with self.scheduler._guard:
            current = {
                "draining": self.scheduler._draining,
                "invocation_none": self.scheduler._invocation is None,
            }
        assert not current["draining"] and current["invocation_none"], {
            "historical_retirement": row,
            "current_guarded_state": current,
        }
        return row

    def restore(self):
        self.scheduler._quiescent.set = self.original_set
        self.scheduler._notify = self.original_notify


def generation_on_line(session, scheduler):
    def read():
        with scheduler._guard:
            return scheduler._definition_generation

    return session.read_on_line(read)


def generation_data(generation):
    defs = generation.definitions
    return {
        "order": [
            task.name
            for task in defs.values()
            if task.name.startswith("fixture.definition.")
        ],
        "lookup": {
            name: next(
                (
                    {"version": task.version, "migrate": task.migrate is not None}
                    for task in defs.values()
                    if task.name == name
                ),
                None,
            )
            for name in values.NAMES
        },
        "declarations": [
            {
                "name": task.name,
                "version": task.version,
                "phases": list(task.phases),
                "migrate": task.migrate is not None,
                "support": None if task.support is None else task.support.to_tree(),
            }
            for task in defs.values()
        ],
    }


def exercise(case, folder):
    owners, observers = [], []
    failure = None
    try:
        return _exercise(case, folder, owners, observers)
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        for session in reversed(owners):
            try:
                session.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - join every owned Session
                errors.append(error)
        for observer in observers:
            observer.restore()
        if errors:
            raise BaseExceptionGroup(
                "Definition publication cleanup",
                ([failure] if failure is not None else []) + errors,
            )


def _exercise(case, folder, owners, observers):
    folder = folder.resolve()
    folder.mkdir(parents=True)
    path = (folder / "session.sqlite").resolve()
    counts = {"initial": 0, "migrate": 0, "handler": 0, "abort": 0}
    callback_log = []
    lock = threading.Lock()
    closed = []
    counter_path = folder / "callback-counter.sqlite"
    with closing(sqlite3.connect(counter_path)) as audit, audit:
        audit.execute(
            "CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)"
        )

    def ledger():
        with (
            closing(
                sqlite3.connect(counter_path.as_uri() + "?mode=ro", uri=True)
            ) as audit,
            audit,
        ):
            audit.execute("PRAGMA query_only=ON")
            return [
                {"seq": n, "kind": kind}
                for n, kind in audit.execute("SELECT seq,kind FROM calls ORDER BY seq")
            ]

    task, failed, success = _callbacks(case, counter_path, counts, callback_log, lock)

    main = task()
    source_a = task(version=2, migrate=failed)
    source_b = task(version=2, migrate=failed)
    source_c = task(version=2, migrate=success)
    left, right, left2, unrelated = (
        task(values.NAMES[1]),
        task(values.NAMES[2]),
        task(values.NAMES[1]),
        task(values.NAMES[3]),
    )
    failing = case["id"] in values.IDS[1:3]

    def open_session(mode):
        store = Recording(open_sqlite_storage(str(path), mode=mode))
        session = Session(store, now=lambda: 1000)
        owners.append(session)
        return session, store

    session, store = open_session("create")
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task_id = session.commit(
        lambda tx: tx.create_task(
            main,
            {"fixture": case["id"]},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    store.id = task_id

    def record():
        def read():
            found = store.task(task_id)
            assert found is not None
            return found.to_tree()

        return session.read_on_line(read)

    created = record()
    if failing:
        session.close(timeout=10)
        closed.append(session.end.reason)
        session, store = open_session("open")
        store.id = task_id
    definitions = (
        []
        if case["id"] == values.IDS[0]
        else [source_a]
        if failing
        else [main, left, right]
        if case["id"] == values.IDS[3]
        else [main]
    )
    scheduler = TaskScheduler(session, definitions)
    scheduler.open()
    observer = Retirement(scheduler)
    observers.append(observer)
    old_generation = generation_on_line(session, scheduler)
    old_snapshot = generation_data(old_generation)
    steps = []

    def step(label, before=None, sql=None, side=None):
        gen = generation_on_line(session, scheduler)
        data = generation_data(gen)
        steps.append(
            {
                "label": label,
                "record": record(),
                "before_record": before,
                "counts": dict(counts),
                "callback_rows": ledger(),
                "order": data["order"],
                "lookup": data["lookup"],
                "generation": data,
                "old_snapshot": generation_data(old_generation),
                "sql": sql,
                "native_only": side,
            }
        )

    def resume(label):
        previous = observer.checkpoint()
        scheduler.resume()
        retired = observer.wait(previous)
        step(label, side={"actual_retirement": retired})

    publish = _publisher(scheduler, observer, record, step, path)

    step("open")
    _actions(
        case["id"],
        publish,
        resume,
        main,
        source_a,
        source_b,
        source_c,
        left,
        left2,
        unrelated,
    )
    final_record = record()
    session.close(timeout=10)
    closed.append(session.end.reason)
    trace = observer.snapshot()
    return {
        "id": case["id"],
        "control": {
            "kind": "actual-created-generic-task-and-private-generation-observer",
            "physical_SIGKILL": False,
            "fabricated_task_or_failure_memo": False,
        },
        "created": created,
        "steps": steps,
        "old_snapshot": old_snapshot,
        "final_record": final_record,
        "callback_log": callback_log,
        "callback_rows": ledger(),
        "callback_sqlite": sqlite_snapshot(counter_path),
        "closed": closed,
        "sqlite_final": sqlite_snapshot(path),
        "native_only": {
            "retirement_observations": trace["rows"],
            "notifications": trace["notifications"],
            "private_generation_read": True,
            "reflection_busy_cancellation": "separate native tests; not a paired/public parity claim",
        },
        "mutations": "Actual SQLite task creation, migration and terminal commits; private metadata publications separated from successor passes; no blanket zero-effect claim.",
    }


def _callbacks(case, counter_path, counts, callback_log, lock):
    def called(name):
        with lock:
            with closing(sqlite3.connect(counter_path)) as audit, audit:
                audit.execute("INSERT INTO calls(kind) VALUES (?)", (name,))
            counts[name] += 1
            callback_log.append(name)

    def initial(value):
        called("initial")
        return {"phase": "run"}

    async def phase(task, runtime, context):
        called("handler")
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": {"case": case["id"]}},
            }
        )

    async def abort(*args):
        called("abort")
        raise AssertionError("Abort outside publication profile")

    def failed(value, checkpoint, version):
        called("migrate")
        raise RuntimeError("fixture migration failure")

    def success(value, checkpoint, version):
        called("migrate")
        return {"input": value, "checkpoint": checkpoint}

    def task(name=values.NAMES[0], version=1, migrate=None):
        return define_task(
            name, version, initial, {"run": phase}, abort, migrate=migrate
        )

    return task, failed, success


def _actions(
    case_id, publish, resume, main, source_a, source_b, source_c, left, left2, unrelated
):
    if case_id == values.IDS[0]:
        publish("install", "install", main)
        resume("resume")
    elif case_id == values.IDS[1]:
        resume("first-resume")
        publish("identical", "install", source_a, True, True)
        publish("unrelated", "install", unrelated, True)
        publish("replacement-success", "install", source_c, True, final=True)
    elif case_id == values.IDS[2]:
        resume("first-resume")
        publish("remove", "remove", values.NAMES[0], True)
        publish("readd-same", "install", source_a, True, True)
        publish("replacement-failed", "install", source_b, True)
        publish("restore-A", "install", source_a, True, True)
        publish("replacement-success", "install", source_c, True, final=True)
    elif case_id == values.IDS[3]:
        publish("replace-middle", "install", left2)
        publish("remove-middle", "remove", values.NAMES[1], async_call=True)
        publish("reappend-middle", "install", left)
        resume("resume")
    else:
        publish("absent-remove", "remove", values.NAMES[4], async_call=True)
        publish("identical", "install", main)
        resume("resume")


def _publisher(scheduler, observer, record, step, path):
    def publish(label, kind, incoming, enabled=False, async_call=False, final=False):
        before = None if final else record()
        sql_before = None if enabled else sqlite_snapshot(path)
        previous = observer.checkpoint()
        notices = _publication_notifications(observer)
        method = getattr(scheduler, ("a" if async_call else "") + kind + "_definition")
        ack = asyncio.run(method(incoming)) if async_call else method(incoming)
        assert type(ack) is bool
        delta = _publication_notifications(observer) - notices
        retired = (
            observer.wait(previous) if enabled and (kind == "install" or ack) else None
        )
        if not enabled:
            assert delta == 0
        if enabled:
            assert delta == (1 if kind == "install" or ack else 0)
        step(
            label,
            before,
            None if enabled else {"before": sql_before, "after": sqlite_snapshot(path)},
            {
                "ACK": ack,
                "notify_delta": delta,
                "actual_retirement": retired,
                "async": async_call,
                "no_busy_retry": True,
            },
        )

    return publish


def _publication_notifications(observer):
    return sum(
        row["function"] == "change"
        and row["source"].endswith("/durable/definition_publication.py")
        for row in observer.notification_snapshot()
    )
