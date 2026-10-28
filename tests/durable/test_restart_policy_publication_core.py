"""Private policy publication, including the real recovery publication gap."""

import asyncio
import hashlib
import json
import math
import os
import signal
import threading
from contextlib import closing
from pathlib import Path

import pytest
import test_tool_sqlite as sqlite_cut
from sqlite_child_support import group_exists
from test_restart_policy_runtime import definition, set_count
from test_task_runtime import create
from test_task_runtime import rig as rig
from tool_support import counts, definitions

from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.restart_policy import RestartPolicy
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery, TaskQuery
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC

RECEIPT_CHILD_SHA256 = (
    "2c1b30ac1dcdd8048afe193c4b613e0678106abd3e793bb5d619807ad183c49f"
)
RECEIPT_CUT_ANCHOR = "say('cut',label=label)"
RECEIPT_CUT_EVENT = (
    "from adjudication_sdk_support import native_origins; "
    "say('cut',label=label,native_origins=native_origins(),"
    "orig_argv=sys.orig_argv,cwd=os.getcwd(),executable=sys.executable,"
    "pgid=os.getpgid(0))"
)


def receipt_cut_with_evidence(root, monkeypatch):
    """Add observations only to this exact existing receipt-after actor."""
    base = sqlite_cut.CHILD
    assert hashlib.sha256(base.encode()).hexdigest() == RECEIPT_CHILD_SHA256
    assert base.count(RECEIPT_CUT_ANCHOR) == 1
    program = base.replace(RECEIPT_CUT_ANCHOR, RECEIPT_CUT_EVENT, 1)
    original = sqlite_cut.Child
    captures = []
    expected_args = (
        str(Path(sqlite_cut.__file__).parent),
        str(root),
        "receipt-after",
        "unsafe",
        "none",
    )

    class Capture:
        def __init__(self, child):
            self.child = child
            self.pgid = os.getpgid(child.pid)  # Observe while actually alive.
            assert self.pgid == child.group

        def __enter__(self):
            return self.child.__enter__()

        def __exit__(self, kind, error, trace):
            self.child.__exit__(kind, error, trace)
            result = self.child.wait(5)
            evidence = self.child.evidence()
            evidence.update(
                argv=self.child.process.args,
                actual_pgid=self.pgid,
                reaped=True,
                group_absent=not group_exists(self.pgid),
                base_program_sha256=RECEIPT_CHILD_SHA256,
                observed_program_sha256=hashlib.sha256(program.encode()).hexdigest(),
                scope="this recorded child/group after actual Child.__exit__ and wait",
            )
            (root / "receipt-cut-postexit.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
            assert result == -signal.SIGKILL and evidence["group_absent"]
            captures.append(evidence)

    def capture(code, *args, **kwargs):
        child = original(code, *args, **kwargs)
        if code != program or args != expected_args or kwargs != {"home": root}:
            return child
        try:
            return Capture(child)
        except BaseException:
            child.reap()
            raise

    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite_cut, "CHILD", program)
        scoped.setattr(sqlite_cut, "Child", capture)
        ready = sqlite_cut.cut_store(root, "receipt-after", "unsafe")
    assert len(captures) == 1
    evidence = captures[0]
    events = [event for event in evidence["events"] if event["event"] == "cut"]
    assert len(events) == 1
    event = events[0]
    assert event["label"] == "receipt-after"
    assert event["pid"] == evidence["pid"]
    assert event["pgid"] == evidence["actual_pgid"]
    assert event["orig_argv"] == evidence["argv"]
    assert event["executable"] == evidence["argv"][0]
    assert event["cwd"] == str(Path.cwd())
    source = Path(__file__).resolve().parents[2] / "src"
    origins = event["native_origins"]
    assert origins and len({row["module"] for row in origins}) == len(origins)
    for row in origins:
        path = Path(row["file"])
        assert path.is_relative_to(source) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    return ready


def test_publication_is_not_a_commit_or_wake(rig, monkeypatch):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    notifications = []
    original = scheduler._notify
    monkeypatch.setattr(scheduler, "_notify", lambda: notifications.append(True))
    before = session.read_on_line(lambda: storage.task(task_id).to_tree())
    commits = len(storage.commits)
    assert scheduler.set_restart_limit(6) is True
    assert asyncio.run(scheduler.aset_restart_limit(6.9)) is False
    assert scheduler.inspect_task(task_id).restart_limit == 6
    assert not scheduler.inspect_task(task_id).restart_parked
    assert inspect_task(session, task_id).restart_parked
    assert session.read_on_line(lambda: storage.task(task_id).to_tree()) == before
    assert len(storage.commits) == commits
    assert notifications == []
    with scheduler._guard:
        assert scheduler._opening and scheduler._opened
        assert not scheduler._enabled
    assert not called.is_set()
    monkeypatch.setattr(scheduler, "_notify", original)
    scheduler.resume()
    assert called.wait(5)


def test_exact_policy_and_hostile_inputs_use_existing_constructor(rig):
    class Hostile:
        def __float__(self):
            raise AssertionError("Do not invoke conversion")

    class ForeignPolicy(RestartPolicy):
        pass

    session, storage = rig
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    before = len(storage.commits)
    assert scheduler.set_restart_limit(RestartPolicy(math.inf)) is True
    assert scheduler.set_restart_limit(math.inf) is False
    assert scheduler.set_restart_limit(ForeignPolicy(9)) is True
    assert scheduler.set_restart_limit(Hostile()) is False
    assert scheduler.set_restart_limit(2**53 + 1) is False
    assert scheduler.set_restart_limit(float(2**53 + 1)) is True
    assert len(storage.commits) == before


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_recovery_releases_line_before_open_publication(
    rig, monkeypatch, cancel_waiter
):
    session, storage = rig
    scheduler = TaskScheduler(session, [])
    committed = threading.Event()
    release = threading.Event()
    published = threading.Event()
    original_commit = type(session).acommit
    original_recover = scheduler._recover

    async def held_commit(owner, callback):
        result = await original_commit(owner, callback)
        if owner is session:
            committed.set()
            assert await asyncio.to_thread(release.wait, 5)
        return result

    async def observed_recover():
        await original_recover()
        published.set()

    monkeypatch.setattr(type(session), "acommit", held_commit)
    monkeypatch.setattr(scheduler, "_recover", observed_recover)

    async def run():
        opening = asyncio.create_task(scheduler.aopen())
        try:
            assert await asyncio.to_thread(committed.wait, 5)
            commits = len(storage.commits)
            with scheduler._guard:
                assert scheduler._opening and not scheduler._opened
            with pytest.raises(RuntimeError, match="Open the scheduler"):
                await scheduler.aset_restart_limit(9)
            assert len(storage.commits) == commits
            with scheduler._guard:
                assert scheduler._restart_policy.limit == 5
            if cancel_waiter:
                opening.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await opening
            release.set()
            assert await asyncio.to_thread(published.wait, 5)
            if not cancel_waiter:
                await opening
            assert await scheduler.aset_restart_limit(9) is True
            with scheduler._guard:
                assert scheduler._opening and scheduler._opened
                assert scheduler._restart_policy.limit == 9
        finally:
            release.set()
            if not opening.done():
                await opening

    asyncio.run(run())


def test_live_policy_preserves_actual_receipt_and_never_repeats_old_call(
    tmp_path, monkeypatch
):
    ready = receipt_cut_with_evidence(tmp_path, monkeypatch)
    generation, tool, _ = definitions(tmp_path, replay="unsafe")
    store = open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    with closing(Session(store)) as session:
        scheduler = TaskScheduler(session, [generation, tool])

        def seed(tx):
            first = next(
                row.to_tree()
                for row in tx.scan_tasks(TaskQuery({}), 100).items
                if row.to_tree()["kind"] == "pi.tool"
                and row.to_tree()["input"]["callId"] == "call:0"
            )
            assert first["state"]["status"] == "running"
            assert first["state"]["checkpoint"]["phase"] == "settle"
            tx.set_task({**first, "restarts": 4})
            return first["id"]

        task_id = session.commit(seed)
        scheduler.open()  # Actual recovery acquires the fifth restart.
        idle = threading.Event()
        reserve = scheduler._reserve

        async def observed():
            result = await reserve()
            if result is None:
                idle.set()
            return result

        scheduler._reserve = observed

        def captured():
            return (
                store.task(task_id).to_tree(),
                peek_document(store, RECEIPT_DOC, ready["conversation"], task_id),
            )

        saved = session.read_on_line(captured)
        assert saved[0]["restarts"] == 5
        assert saved[1]["value"]["version"] == 1
        assert saved[1]["value"]["intent"] is not None
        before = counts(tmp_path)
        scheduler.resume()
        assert idle.wait(5)
        asyncio.run_coroutine_threadsafe(
            scheduler._quiescent.wait(), scheduler._loop
        ).result(5)
        assert scheduler.inspect_task(task_id).restart_parked
        assert session.read_on_line(captured) == saved
        assert counts(tmp_path) == before
        assert scheduler.set_restart_limit(6) is True
        assert session.read_on_line(captured) == saved
        assert counts(tmp_path) == before
        assert not scheduler.inspect_task(task_id).restart_parked
        idle.clear()
        scheduler.resume()
        assert idle.wait(5)
        rows = {row.id: row.to_tree() for row in scheduler.inspect()}
        assert rows[task_id]["state"]["outcome"]["status"] == "completed"
        assert rows[ready["parent"]]["state"]["outcome"]["status"] == "completed"
        entries = session.read_on_line(
            lambda: (
                store.scan_entries(
                    EntryQuery({"conversationId": ready["conversation"]}), 100
                ).items
            )
        )
        winner = next(
            entry.to_tree()["data"]
            for entry in entries
            if entry.to_tree().get("byTaskId") == task_id
        )
        # Normal receipt1 retains its existing output/call/key carrier; do not
        # invent the interrupted receipt2 envelope or rewrite normal bytes.
        assert winner == {
            key: saved[1]["value"][key] for key in ("nativeToolOutput", "call", "key")
        }
        assert counts(tmp_path) == {
            "calls": 2,
            "applications": 2,
            "prepares": 2,
            "providers": 2,
        }
