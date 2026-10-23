"""Actual SQLite process-death boundaries for DE05b, with bounded child groups."""

import json
import signal
import sqlite3
import threading

import pytest
from sqlite_child_support import Child
from test_task_runtime import abort

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task

CUT_CHILD = r"""
import threading
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.tasks import define_task
path, cut, effects = sys.argv[1:]
store = open_sqlite_storage(path, mode="create")
session = Session(store)
conversation = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

def barrier(label):
    say("cut", label=label)
    if not threading.Event().wait(60):
        raise RuntimeError("parent did not kill reached child")

async def abort(*args):
    raise AssertionError("abort must not dispatch")

async def phase(task, runtime, context):
    with open(effects, "a") as file:
        file.write("phase\n")
        file.flush()
        os.fsync(file.fileno())
    if cut == "before-checkpoint":
        barrier(cut)
    await runtime.commit(lambda tx, current: {"status": "running", "checkpoint": {"phase": "start", "step": 1}})
    if cut.startswith("close-"):
        session.request_close()
    await context.signal.wait()

definition = define_task("work", 1, lambda value: {"phase": "start", "step": 0}, {"start": phase}, abort)
id = session.commit(lambda tx: tx.create_task(definition, {"preserve": [1, None]}, ownership={"kind": "conversation"}, conversation_id=conversation))
def seed(tx):
    row = tx.task(id).to_tree()
    tx.set_task({**row, "memos": {"effect-key": "stable-key", "null": None}})
session.commit(seed)
scheduler = TaskScheduler(session, [definition])
scheduler.open()

def at(connection, point):
    row = sqlite._decode(connection.execute("SELECT record FROM tasks WHERE id = ?", (id,)).fetchone()[0])
    state = row["state"]
    stage = "close" if state["status"] == "pending" else "reservation" if state["checkpoint"]["step"] == 0 else "checkpoint"
    if cut == stage + "-" + point:
        barrier(cut)
sqlite._before_commit = lambda connection: at(connection, "before")
sqlite._after_commit = lambda connection: at(connection, "after")
say("ready", id=id)
assert command() == "go"
if cut == "before-reservation":
    barrier(cut)
scheduler.resume()
scheduler.resume()
if not threading.Event().wait(60):
    raise RuntimeError("cut never reached")
"""


@pytest.mark.parametrize(
    "cut",
    [
        "before-reservation",
        "reservation-before",
        "reservation-after",
        "before-checkpoint",
        "checkpoint-before",
        "checkpoint-after",
        "close-before",
        "close-after",
    ],
)
def test_real_sigkill_recovery_requires_explicit_resume_and_counts_once(tmp_path, cut):
    path, effects = tmp_path / "cut.sqlite", tmp_path / "phases.txt"
    with Child(CUT_CHILD, str(path), cut, str(effects), home=tmp_path) as child:
        id = child.expect("ready")["id"]
        child.send("go")
        assert child.expect("cut")["label"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "process.json").write_text(json.dumps(child.evidence(), indent=2))
    phase_calls = effects.read_text().splitlines() if effects.exists() else []
    assert phase_calls == (
        []
        if cut in ("before-reservation", "reservation-before", "reservation-after")
        else ["phase"]
    )
    with sqlite3.connect(path) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    persisted_running = cut not in (
        "before-reservation",
        "reservation-before",
        "close-after",
    )
    expected_step = (
        1 if cut in ("checkpoint-after", "close-before", "close-after") else 0
    )
    reopened_rows = []
    resumed = []
    done = threading.Event()

    async def phase(task, runtime, context):
        resumed.append(task)
        assert await runtime.memo("effect-key", "wrong-new-key") == "stable-key"
        assert await runtime.memo("null", 2) is None
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {
                    "status": "completed",
                    "result": current["state"]["checkpoint"],
                },
            }
        )
        done.set()

    definition = define_task(
        "work", 1, lambda _: pytest.fail("initializer reran"), {"start": phase}, abort
    )
    for attempt in range(2):
        store = open_sqlite_storage(str(path), mode="open")
        session = Session(store)
        scheduler = TaskScheduler(session, [definition])
        scheduler.open()
        row = store.task(id).to_tree()
        reopened_rows.append(row)
        assert resumed == []
        assert row["state"] == {
            "status": "pending",
            "checkpoint": {"phase": "start", "step": expected_step},
        }
        assert row.get("restarts", 0) == int(persisted_running)
        assert row["input"] == {"preserve": [1, None]}
        assert row["memos"] == {"effect-key": "stable-key", "null": None}
        assert row["abortRequested"] is False
        if attempt:
            scheduler.resume()
            scheduler.resume()
            assert done.wait(5)
        session.close(timeout=5)
    assert len(resumed) == 1
    store = open_sqlite_storage(str(path), mode="open")
    try:
        row = store.task(id).to_tree()
        assert row["state"]["status"] == "terminal"
        assert "memos" not in row and "restarts" not in row
    finally:
        store.close()
    with sqlite3.connect(path) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    (tmp_path / "recovery.json").write_text(
        json.dumps(
            {
                "cut": cut,
                "phase_calls": phase_calls,
                "reopened": reopened_rows,
                "final": row,
                "resumed_calls": len(resumed),
                "integrity": "ok",
                "lock_reacquired": True,
            },
            indent=2,
        )
    )


@pytest.mark.parametrize(
    "failure",
    [
        "readonly",
        "reservation-ack",
        "checkpoint-ack",
        "fault-commit",
        "close-commit",
        "adoption",
    ],
)
def test_real_storage_fault_seals_owner_and_reopen_uses_actual_state(
    tmp_path, monkeypatch, failure
):
    from pipy_harness.native.durable import sqlite
    from pipy_harness.native.durable import tx as tx_module

    path = tmp_path / "fault.sqlite"
    store = open_sqlite_storage(str(path), mode="create")
    session = Session(store)
    conversation = session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    phase_calls = []

    async def phase(task, runtime, context):
        phase_calls.append("phase")
        if failure == "fault-commit":
            raise ValueError("phase failure")
        await runtime.commit(
            lambda tx, current: {"status": "running", "checkpoint": {"phase": "next"}}
        )
        session.request_close()
        await context.signal.wait()

    async def next_phase(*args):
        phase_calls.append("forbidden next")

    definition = define_task(
        "work",
        1,
        lambda _: {"phase": "start"},
        {"start": phase, "next": next_phase},
        abort,
    )
    id = session.commit(
        lambda tx: tx.create_task(
            definition,
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()

    def fault(connection, point):
        row = sqlite._decode(
            connection.execute("SELECT record FROM tasks WHERE id=?", (id,)).fetchone()[
                0
            ]
        )
        state = row["state"]
        target = (
            failure == "reservation-ack"
            and state == {"status": "running", "checkpoint": {"phase": "start"}}
            or failure == "checkpoint-ack"
            and state == {"status": "running", "checkpoint": {"phase": "next"}}
            or failure == "fault-commit"
            and state["status"] == "terminal"
            or failure == "close-commit"
            and state["status"] == "pending"
        )
        if target:
            raise RuntimeError("injected " + failure)

    if failure == "readonly":
        store._database.connection().execute("PRAGMA query_only = ON")
    elif failure == "adoption":

        def fail_adopt(self, seq):
            raise RuntimeError("adoption failed")

        monkeypatch.setattr(tx_module._TxCore, "adopt", fail_adopt)
    else:
        seam = "_after_commit" if failure.endswith("ack") else "_before_commit"
        monkeypatch.setattr(sqlite, seam, lambda connection: fault(connection, seam))
    scheduler.resume()
    end = session.wait_closed(timeout=5)
    assert end.reason == "failed"
    with session._owner._cond:
        assert not session._internal_listeners.copy()
        assert not session._commit_listeners.copy()
    assert phase_calls == (
        [] if failure in ("readonly", "reservation-ack", "adoption") else ["phase"]
    )
    assert not scheduler._thread.is_alive()
    scheduler.resume()
    monkeypatch.undo()
    reopened = open_sqlite_storage(str(path), mode="open")
    try:
        row = reopened.task(id).to_tree()
        expected = "pending" if failure == "readonly" else "running"
        assert row["state"]["status"] == expected
        assert row["state"]["checkpoint"]["phase"] == (
            "next" if failure in ("checkpoint-ack", "close-commit") else "start"
        )
    finally:
        reopened.close()


EFFECT_CHILD = r"""
import sqlite3, threading
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.tasks import define_task
path, external = sys.argv[1:]
store = open_sqlite_storage(path, mode="create")
session = Session(store)
conversation = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
async def abort(*args):
    raise AssertionError()
async def phase(task, runtime, context):
    key = await runtime.memo("key", "committed-key")
    await runtime.commit(lambda tx, current: {"status": "running", "checkpoint": {"phase": "effect", "intent": key}})
    with sqlite3.connect(external) as effect:
        effect.execute("INSERT INTO calls VALUES (?)", (key,))
        effect.execute("INSERT OR IGNORE INTO applications VALUES (?)", (key,))
    say("applied", key=key)
    if not threading.Event().wait(60):
        raise RuntimeError("missing parent kill")
definition = define_task("effect", 1, lambda _: {"phase": "effect"}, {"effect": phase}, abort)
id = session.commit(lambda tx: tx.create_task(definition, None, ownership={"kind": "conversation"}, conversation_id=conversation))
scheduler = TaskScheduler(session, [definition])
scheduler.open()
scheduler.resume()
if not threading.Event().wait(60):
    raise RuntimeError("effect did not run")
"""


@pytest.mark.parametrize(
    "safe", [False, True], ids=["unsafe-uncertainty", "keyed-safe-retry"]
)
def test_actual_effect_after_intent_before_receipt_has_no_generic_exactly_once_claim(
    tmp_path, safe
):
    path, external = tmp_path / "effect.sqlite", tmp_path / "external.sqlite"
    with sqlite3.connect(external) as effect:
        effect.execute("CREATE TABLE calls (key TEXT NOT NULL)")
        effect.execute("CREATE TABLE applications (key TEXT PRIMARY KEY)")
    with Child(EFFECT_CHILD, str(path), str(external), home=tmp_path) as child:
        assert child.expect("applied")["key"] == "committed-key"
        child.kill_group()
        child.wait_stdout_eof()
        (tmp_path / "process.json").write_text(json.dumps(child.evidence(), indent=2))
    done = threading.Event()

    async def phase(task, runtime, context):
        key = await runtime.memo("key", "must-not-replace")
        assert key == task["state"]["checkpoint"]["intent"] == "committed-key"
        if safe:
            with sqlite3.connect(external) as effect:
                effect.execute("INSERT INTO calls VALUES (?)", (key,))
                effect.execute("INSERT OR IGNORE INTO applications VALUES (?)", (key,))
            outcome = {"status": "completed", "result": {"receipt": key}}
        else:
            outcome = {
                "status": "failed",
                "error": {"message": "interrupted effect; receipt unknown"},
            }
        await runtime.commit(
            lambda tx, current: {"status": "terminal", "outcome": outcome}
        )
        done.set()

    definition = define_task(
        "effect",
        1,
        lambda _: pytest.fail("initializer on resume"),
        {"effect": phase},
        abort,
    )
    store = open_sqlite_storage(str(path), mode="open")
    session = Session(store)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    with sqlite3.connect(external) as effect:
        assert effect.execute("SELECT key FROM calls").fetchall() == [
            ("committed-key",)
        ]
    scheduler.resume()
    scheduler.resume()
    assert done.wait(5)
    session.close(timeout=5)
    with sqlite3.connect(external) as effect:
        assert effect.execute("SELECT key FROM calls").fetchall() == [
            ("committed-key",)
        ] * (2 if safe else 1)
        assert effect.execute("SELECT key FROM applications").fetchall() == [
            ("committed-key",)
        ]
        assert effect.execute("PRAGMA integrity_check").fetchone() == ("ok",)

        (tmp_path / "effects.json").write_text(
            json.dumps(
                {
                    "safe": safe,
                    "calls": effect.execute("SELECT key FROM calls").fetchall(),
                    "applications": effect.execute(
                        "SELECT key FROM applications"
                    ).fetchall(),
                    "integrity": "ok",
                },
                indent=2,
            )
        )
