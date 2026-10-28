"""Real repeated process loss, parked recovery and healthy owner retirement."""

import hashlib
import json
import signal
from pathlib import Path

import pytest
from sqlite_child_support import Child

ACTOR = r"""
import asyncio, threading
from pathlib import Path
from adjudication_sdk_support import native_origins
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery
from pipy_harness.native.durable.tasks import define_task
path, effects, cut, limit = sys.argv[1:]
limit = float(limit)
fresh = not Path(path).exists()
store = open_sqlite_storage(path, mode="create" if fresh else "open")
session = Session(store)

def stop_at(label):
    say("cut", label=label, native_origins=native_origins())
    if not threading.Event().wait(30):
        raise AssertionError("parent did not kill actual cut")

async def abort(*args):
    raise AssertionError("abort is deferred")

async def start(task, runtime, context):
    with open(effects, "a") as file:
        file.write("effect\n")
        file.flush()
        os.fsync(file.fileno())
    if cut == "effect":
        stop_at("effect")
    if cut == "healthy":
        say("phase")
        await context.signal.wait()
        say("finalizer")
    else:
        await runtime.commit(lambda tx, row: {"status":"terminal", "outcome":{"status":"completed", "result":None}})
        say("done")

definition = define_task("restart-physical", 1, lambda _: {"phase":"start"}, {"start":start}, abort)
if fresh:
    cid = session.commit(lambda tx: tx.create_conversation({"kind":"ownerless"})).id
    tid = session.commit(lambda tx: tx.create_task(definition, {"original":True}, ownership={"kind":"conversation"}, conversation_id=cid))
else:
    tid = store.scan_tasks(TaskQuery({}), 10).items[0].id
scheduler = TaskScheduler(session, [definition], restart_limit=limit)
scheduler.open()
row = session.read_on_line(lambda: store.task(tid).to_tree())
say("opened", row=row, inspection=inspect_task(session, tid, restart_limit=limit).to_tree())
if cut == "reservation":
    def after(connection):
        current = sqlite._decode(connection.execute("SELECT record FROM tasks WHERE id=?", (tid,)).fetchone()[0])
        if current["state"]["status"] == "running":
            stop_at("reservation")
    sqlite._after_commit = after
action = command()
if action == "retry":
    say("reset", changed=scheduler.retry(tid))
elif action == "go":
    scheduler.resume()
else:
    assert action == "close"
if cut == "parked" and action == "go":
    asyncio.run(scheduler.apause())
    say("parked", row=session.read_on_line(lambda: store.task(tid).to_tree()))
if action != "close":
    assert command() == "close"
session.close(timeout=10)
say("closed", host_alive=scheduler._thread.is_alive(), native_origins=native_origins())
"""


def effects(path):
    return path.read_text().splitlines() if path.exists() else []


def validate_origins(event):
    root = Path(__file__).resolve().parents[2] / "src"
    rows = event["native_origins"]
    assert rows and len({row["module"] for row in rows}) == len(rows)
    for row in rows:
        path = Path(row["file"])
        assert path.is_relative_to(root) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]


def closed(child):
    child.send("close")
    event = child.expect("closed")
    assert event["host_alive"] is False
    validate_origins(event)
    assert child.wait() == 0
    child.wait_stdout_eof()


@pytest.mark.parametrize("limit", [2, 5])
@pytest.mark.parametrize("cut", ["reservation", "effect"])
def test_real_repeated_sigkill_parks_at_exact_limit_then_fresh_higher_owner(
    tmp_path, limit, cut
):
    path, ledger = tmp_path / "crash.sqlite", tmp_path / "effects.txt"
    evidence = []
    task_id = None
    for count in range(limit):
        with Child(
            ACTOR, str(path), str(ledger), cut, str(limit), home=tmp_path
        ) as child:
            opened = child.expect("opened")
            row = opened["row"]
            task_id = row["id"] if task_id is None else task_id
            assert row["id"] == task_id
            assert row.get("restarts", 0) == count
            assert not opened["inspection"]["restart_parked"]
            assert effects(ledger) == ["effect"] * (count if cut == "effect" else 0)
            child.send("go")
            event = child.expect("cut")
            assert event["label"] == cut
            validate_origins(event)
            child.kill_group()
            assert child.wait() == -signal.SIGKILL
            child.wait_stdout_eof()
            evidence.append(child.evidence())
    expected_effects = ["effect"] * (limit if cut == "effect" else 0)
    for _ in range(2):
        with Child(
            ACTOR, str(path), str(ledger), "parked", str(limit), home=tmp_path
        ) as child:
            opened = child.expect("opened")
            row = opened["row"]
            assert row["id"] == task_id and row["restarts"] == limit
            assert row["state"]["status"] == "pending"
            assert opened["inspection"]["restart_parked"]
            child.send("go")
            assert child.expect("parked")["row"] == row
            assert effects(ledger) == expected_effects
            closed(child)
            evidence.append(child.evidence())
    with Child(
        ACTOR, str(path), str(ledger), "finish", str(limit + 1), home=tmp_path
    ) as child:
        opened = child.expect("opened")
        assert opened["row"]["restarts"] == limit
        assert not opened["inspection"]["restart_parked"]
        child.send("go")
        child.expect("done")
        closed(child)
        evidence.append(child.evidence())
    assert effects(ledger) == expected_effects + ["effect"]
    (tmp_path / "physical-evidence.json").write_text(json.dumps(evidence, indent=2))


def test_healthy_close_preserves_crash_count_and_joins_actual_phase(tmp_path):
    path, ledger = tmp_path / "healthy.sqlite", tmp_path / "effects.txt"
    evidence = []
    with Child(
        ACTOR, str(path), str(ledger), "reservation", "5", home=tmp_path
    ) as child:
        child.expect("opened")
        child.send("go")
        event = child.expect("cut")
        assert event["label"] == "reservation"
        validate_origins(event)
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        evidence.append(child.evidence())
    for _ in range(2):
        with Child(
            ACTOR, str(path), str(ledger), "healthy", "5", home=tmp_path
        ) as child:
            assert child.expect("opened")["row"]["restarts"] == 1
            child.send("go")
            child.expect("phase")
            child.send("close")
            child.expect("finalizer")
            event = child.expect("closed")
            assert event["host_alive"] is False
            validate_origins(event)
            assert child.wait() == 0
            child.wait_stdout_eof()
            evidence.append(child.evidence())
    assert effects(ledger) == ["effect", "effect"]
    (tmp_path / "physical-evidence.json").write_text(json.dumps(evidence, indent=2))
