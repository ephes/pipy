"""DE16a real FIFO, storage, process and lifecycle witnesses.

Physical evidence distinguishes durable marking, reservation and observed
callback entry; no seeded crash or exactly-once claim.
"""

import asyncio
import json
import signal
import threading

from definition_publication_live_support import Passes, pause
from restart_policy_live_support import owner
from sqlite_child_support import Child, group_exists
from task_migration_support import ObservedStorage, Rig, sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task

ABORTED = {"status": "terminal", "outcome": {"status": "aborted", "result": "cleaned"}}


def mark(scheduler, target):
    return asyncio.run(scheduler.aabort_leaf(target))


class AbortCalls:
    """All ledger readers and writers share one observer condition."""

    def __init__(self, name="ordinary.abort.broad", *, stop=False):
        self.name, self.stop = name, stop
        self.condition = threading.Condition()
        self.events = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.finalizing, self.finalizer_release = threading.Event(), threading.Event()
        self.finalized = threading.Event()
        self.release.set()
        self.finalizer_release.set()

    def note(self, *event):
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return tuple(self.events)

    def declaration(self, version=1, label="A"):
        def initial(_value):
            self.note("initial", label)
            return {"phase": "start", "saved": [1, None]}

        async def phase(*_args):
            self.note("run", label)
            raise AssertionError("An abort-marked target must not run its phase")

        async def abort(row, runtime, context):
            self.note("abort", label, row["version"], row.get("restarts"))
            self.entered.set()
            try:
                if self.stop:
                    await context.signal.wait()
                else:
                    assert await owned_call(lambda: self.release.wait(10))
                    await runtime.commit(lambda _tx, _row: ABORTED)
            finally:
                self.finalizing.set()
                assert await owned_call(lambda: self.finalizer_release.wait(10))
                self.finalized.set()

        return define_task(self.name, version, initial, {"start": phase}, abort)


def paused_target(rig, calls=None, *, limit=5):
    calls = AbortCalls() if calls is None else calls
    source = calls.declaration()
    target = rig.create(source)
    scheduler = owner(rig, [source], limit=limit)
    return target, source, calls, scheduler


def real_pass(scheduler, monkeypatch):
    passes = Passes(scheduler, monkeypatch)
    before = passes.checkpoint()
    scheduler.resume()
    passes.retired_after(before)
    pause(scheduler)


def cold(path, target, definitions, *, limit=5):
    """Open existing committed data without a second fixture-creation commit."""
    rig = Rig.__new__(Rig)
    rig.backend, rig.path = "sqlite", path
    rig.storage = ObservedStorage(open_sqlite_storage(str(path), mode="open"))
    rig.session, rig.scheduler = Session(rig.storage), None
    rig.conversation = rig.record(target)["conversationId"]
    return rig, owner(rig, definitions, limit=limit)


class MarkCommitCut:
    """Forward real SQLite hooks; raise only at this target's actual mark."""

    def __init__(self, rig, target, cut):
        self.rig, self.target, self.cut = rig, target, cut
        self.connection = rig.storage.inner._database.connection()
        self.before_hook, self.after_hook = sqlite._before_commit, sqlite._after_commit
        self.fired = []

    def matches(self, connection):
        if connection is not self.connection:
            return False
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        if found is None:
            return False
        row = sqlite._decode(found[0])
        return row["abortRequested"] and row["state"]["status"] == "pending"

    def before(self, connection):
        if self.cut == "before" and self.matches(connection):
            self.fired.append(connection.in_transaction)
            raise RuntimeError("ordinary-abort-before-COMMIT")
        return self.before_hook(connection)

    def after(self, connection):
        if self.cut == "after" and self.matches(connection):
            self.fired.append(connection.in_transaction)
            raise RuntimeError("ordinary-abort-ACK-lost")
        return self.after_hook(connection)

    def install(self, patch):
        patch.setattr(sqlite, "_before_commit", self.before)
        patch.setattr(sqlite, "_after_commit", self.after)


ABORT_CUT_CHILD = r"""
import asyncio, threading
from pipy_harness.native.durable import ordinary_abort
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task
path, cut, effects = sys.argv[1:]
store = open_sqlite_storage(path, mode="create")
session = Session(store)
conversation = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

def effect(label):
    with open(effects, "a") as file:
        file.write(label + "\n"); file.flush(); os.fsync(file.fileno())

def row():
    return session.read_on_line(lambda: store.task(target).to_tree())

def boundary(label):
    say("boundary", label=label, row=row())
    if not threading.Event().wait(60):
        raise AssertionError("parent did not kill an observed boundary")

async def phase(*args):
    effect("run")
    if cut == "run-entered":
        await asyncio.to_thread(boundary, cut)
    raise AssertionError("run mode is outside this abort case")

async def abort(*args):
    effect("abort")
    await asyncio.to_thread(boundary, "entered")

source = define_task("ordinary.abort.physical", 1,
    lambda _: {"phase": "start", "saved": [1, None]}, {"start": phase}, abort)
target = session.commit(lambda tx: tx.create_task(source, {"input": [1, None]},
    ownership={"kind": "conversation"}, conversation_id=conversation))
scheduler = TaskScheduler(session, [source])
scheduler.open()
if cut != "run-entered":
    assert asyncio.run(scheduler.aabort_leaf(target)) == "marked"
if cut == "reserved":
    original = ordinary_abort.run
    async def held(actual_scheduler, invocation):
        await asyncio.to_thread(boundary, "reserved")
        return await original(actual_scheduler, invocation)
    ordinary_abort.run = held
say("ready", id=target, row=row())
assert command() == "go"
if cut == "marked":
    boundary(cut)
scheduler.resume()
if not threading.Event().wait(60):
    raise AssertionError("requested process boundary never arrived")
"""


def physical_cut(root, cut):
    """Return observed reserved/entered facts, not an exactly-once claim."""
    root.mkdir()
    path, effects = root / "abort.sqlite", root / "effects.txt"
    with Child(ABORT_CUT_CHILD, str(path), cut, str(effects), home=root) as child:
        ready = child.expect("ready")
        child.send("go")
        reached = child.expect("boundary")
        assert reached["label"] == cut
        before_death = sqlite_rows(path)
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        process = child.evidence()
    entries = effects.read_text().splitlines() if effects.exists() else []
    expected = (
        ["abort"] if cut == "entered" else ["run"] if cut == "run-entered" else []
    )
    assert entries == expected
    assert not group_exists(child.group)
    evidence = {
        "cut": cut,
        "ready": ready,
        "boundary": reached,
        "actual_effect_entries": entries,
        "actual_process": process,
        "leader_reaped": child.process.returncode is not None,
        "measured_group_absent": True,
        "physical_lifecycle_limit": "Reserved-before-entry proves durable reservation, not callback execution. Callback evidence is only the fsynced entry above; no exactly-once/exhaustive-descendant claim.",
    }
    (root / "process.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return path, ready["id"], reached["row"], before_death, entries


MISSING = {
    "status": "terminal",
    "outcome": {"status": "orphaned", "reason": "missing_task"},
}


class MissingCommitCut(MarkCommitCut):
    """Fail only the genuine missing-decision COMMIT, never the earlier mark."""

    def matches(self, connection):
        if connection is not self.connection:
            return False
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        if found is None:
            return False
        row = sqlite._decode(found[0])
        return row["abortRequested"] and row["state"] == MISSING


def target_batches(rig, target, first_commit):
    batches = [
        [write.to_tree() for write in batch]
        for batch in rig.storage.commits[first_commit:]
    ]
    return [
        batch
        for batch in batches
        if any(
            write["type"] == "task" and write["value"]["id"] == target
            for write in batch
        )
    ]


def assert_missing_retirement(rig, target, before, documents, first_commit):
    """Assert real terminal replacement and same-batch task-document retirement."""
    from orphan_missing_definition_support import current_documents

    after = rig.record(target)
    assert after["state"] == MISSING
    retained = {
        key: value
        for key, value in before.items()
        if key not in {"state", "memos", "restarts", "endedAt"}
    }
    assert {key: after[key] for key in retained} == retained
    assert after["abortRequested"] is True
    assert "memos" not in after and "restarts" not in after
    assert type(after["endedAt"]) in {int, float}
    batches = target_batches(rig, target, first_commit)
    assert len(batches) == 1
    writes = [
        write["value"]
        for write in batches[0]
        if write["type"] == "task" and write["value"]["id"] == target
    ]
    assert writes == [after]
    for document in documents:
        assert {"type": "document.retire", "id": document.id} in batches[0]
    assert current_documents(rig, target) == ()
    with rig.scheduler._guard:
        assert rig.scheduler._invocation is None
    return after


def missing_target(rig):
    """Create documents/memos, genuinely mark, then remove idle definition."""
    from orphan_missing_definition_support import task_documents

    target, source, calls, scheduler = paused_target(rig)
    doc_calls = []
    _, documents = task_documents(rig, target, 2, doc_calls)
    rig.change(target, lambda row: row.update(memos={"saved": {"retained": True}}))
    assert mark(scheduler, target) == "marked"
    assert scheduler.remove_definition(source.name)
    assert doc_calls == [("initial", 0), ("initial", 1)]
    return target, source, calls, scheduler, documents
