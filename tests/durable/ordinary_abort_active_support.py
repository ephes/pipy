"""DE16c Broad helpers for real callbacks, process cuts and lease witnesses.

Reuse real Rig/Child/OPENER/SQLite COMMIT hooks. No private scheduler state is
assigned. Callback ledgers are condition-owned; SQL and counters are separate
captures. A reservation boundary is not callback entry or exactly-once proof.
"""

import asyncio
import json
import signal
import threading

from ordinary_abort_support import ABORTED, MarkCommitCut
from restart_policy_live_support import owner
from sqlite_child_support import Child, group_exists
from task_migration_support import sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import InvocationEnded
from pipy_harness.native.durable.session import SessionClosedError, SessionFailed
from pipy_harness.native.durable.tasks import define_task


class ActiveCalls:
    """Actual phase/signal/finally/abort events, not synthetic invocation flags."""

    def __init__(self, name="ordinary.abort.active.broad", *, effect=None):
        self.name, self.effect = name, effect
        self.condition = threading.Condition()
        self.events = []
        self.entered = threading.Event()
        self.signalled = threading.Event()
        self.finalizing = threading.Event()
        self.release = threading.Event()
        self.finalized = threading.Event()
        self.abort_entered = threading.Event()
        self.abort_release = threading.Event()
        self.abort_release.set()
        self.late_checked = threading.Event()
        self.late_release = threading.Event()
        self.late_release.set()

    def note(self, *event):
        with self.condition:
            self.events.append(event)
            if self.effect is not None and event[0] in {"run", "abort"}:
                self.effect(event[0])
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return tuple(self.events)

    def declaration(self, version=1):
        def initial(_value):
            self.note("initial", version)
            return {"phase": "start", "saved": [1, None]}

        async def run(_row, runtime, context):
            self.note("run", version)
            self.entered.set()
            try:
                await context.signal.wait()
                self.note("signal", version)
                self.signalled.set()
                assert await asyncio.to_thread(self.late_release.wait, 10)

                def forbidden(_tx, _current):
                    self.note("late-write-admitted", version)
                    return {"status": "pending", "checkpoint": {"phase": "illegal"}}

                try:
                    await runtime.commit(forbidden)
                except (InvocationEnded, SessionClosedError, SessionFailed) as error:
                    self.note("late-write-refused", type(error).__name__)
                else:
                    raise AssertionError(
                        "A signalled marked/closing run wrote progress"
                    )
                finally:
                    self.late_checked.set()
            finally:
                self.note("run-finally-enter", version)
                self.finalizing.set()
                # Never block the real scheduler host with threading.Event.wait.
                assert await asyncio.to_thread(self.release.wait, 20)
                self.note("run-finally-exit", version)
                self.finalized.set()

        async def abort(row, runtime, _context):
            self.note("abort", version, row.get("restarts"))
            self.abort_entered.set()
            assert await asyncio.to_thread(self.abort_release.wait, 20)
            await runtime.commit(lambda _tx, _row: ABORTED)
            self.note("abort-committed", version)

        return define_task(self.name, version, initial, {"start": run}, abort)

    def release_all(self):
        self.release.set()
        self.abort_release.set()
        self.late_release.set()


def active_target(rig, calls=None):
    calls = ActiveCalls() if calls is None else calls
    source = calls.declaration()
    target = rig.create(source)
    scheduler = owner(rig, [source])
    scheduler.resume()
    try:
        assert calls.entered.wait(5)
        assert rig.record(target)["state"]["status"] == "running"
    except BaseException:
        calls.release_all()
        raise
    return target, source, calls, scheduler


class ActiveMarkCommit(MarkCommitCut):
    """Narrow real SQL discriminator: mark running, not pending handoff/reserve."""

    def __init__(self, rig, target, stage, *, fail=False):
        super().__init__(rig, target, stage)
        self.fail = fail
        self.entered, self.release = threading.Event(), threading.Event()
        self.retired = threading.Event()
        self.observed = []

    def matches(self, connection):
        if self.fired or connection is not self.connection:
            return False
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        if found is None:
            return False
        row = sqlite._decode(found[0])
        return row["abortRequested"] and row["state"]["status"] == "running"

    def hit(self, connection):
        self.fired.append(connection.in_transaction)
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        assert found is not None
        self.observed.append(sqlite._decode(found[0]))
        self.entered.set()
        try:
            assert self.release.wait(10)
            if self.fail:
                raise RuntimeError("active-mark-" + self.cut)
        finally:
            self.retired.set()

    def before(self, connection):
        if self.cut == "before" and self.matches(connection):
            self.hit(connection)
        return self.before_hook(connection)

    def after(self, connection):
        if self.cut == "after" and self.matches(connection):
            self.hit(connection)
        return self.after_hook(connection)


def task_sql(path, target):
    """Decode the actual task from a separate read-only SQLite transaction."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        value = db.execute("SELECT record FROM tasks WHERE id=?", (target,)).fetchone()
        assert value is not None
        return sqlite._decode(value[0])


def sql_evidence(rows):
    """Exact SQL-cell projection; byte values are typed hex, no lossy fallback."""
    return {
        table: [
            [
                {"bytes_hex": value.hex()} if type(value) is bytes else value
                for value in row
            ]
            for row in records
        ]
        for table, records in rows.items()
    }


def evidence(root, label, *, calls=None, **facts):
    """Retain actual counters separately from committed SQL snapshots."""
    if calls is not None:
        facts["callback_events_separately_captured"] = calls.snapshot()
    facts["capture_qualification"] = (
        "SQL and locked callback ledger are not jointly atomic"
    )
    (root / (label + ".json")).write_text(json.dumps(facts, indent=2) + "\n")


ACTIVE_CUT_CHILD = r"""
import asyncio, hashlib, threading
from pathlib import Path
from ordinary_abort_active_support import ActiveCalls
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
path, cut, effects = sys.argv[1:]
store = open_sqlite_storage(path, mode='create')
session = Session(store)
conversation = session.commit(lambda tx: tx.create_conversation({'kind':'ownerless'})).id

def effect(label):
    with open(effects, 'a') as stream:
        stream.write(label+'\n'); stream.flush(); os.fsync(stream.fileno())

calls = ActiveCalls('ordinary.abort.active.physical', effect=effect)
source = calls.declaration()
target = session.commit(lambda tx: tx.create_task(source, {'input':[1,None]},
    ownership={'kind':'conversation'}, conversation_id=conversation))
scheduler = TaskScheduler(session, [source])
scheduler.open()

def row():
    return session.read_on_line(lambda: store.task(target).to_tree())

def boundary(label):
    say('boundary', label=label, row=row(), calls=calls.snapshot())
    assert threading.Event().wait(60), 'parent did not kill witnessed boundary'

if cut == 'handoff-pending':
    original = scheduler._reserve
    async def before_next_reservation():
        if calls.finalized.is_set():
            # Existing host next-reserve seam, AFTER genuine drain/handoff.
            await asyncio.to_thread(boundary, cut)
        return await original()
    scheduler._reserve = before_next_reservation
    calls.release.set()
if cut == 'abort-entered':
    calls.release.set(); calls.abort_release.clear()
origins = {}
for name, module in tuple(sys.modules.items()):
    if name == 'pipy_harness' or name.startswith('pipy_harness.'):
        file = getattr(module, '__file__', None)
        if file:
            p=Path(file).resolve(); origins[name]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
say('ready', id=target, row=row(), origins=origins)
assert command() == 'go'
scheduler.resume()
assert calls.entered.wait(5)
if cut == 'run-unmarked':
    boundary(cut)

def request():
    result = asyncio.run(scheduler.aabort_leaf(target))
    say('mark-return', result=result)
threading.Thread(target=request, daemon=True).start()
if cut == 'run-marked':
    assert calls.finalizing.wait(5)
    boundary(cut)
if cut == 'abort-entered':
    assert calls.abort_entered.wait(5)
    boundary(cut)
assert threading.Event().wait(60), 'requested boundary never arrived'
"""


def physical_active_cut(root, cut):
    root.mkdir()
    path, effects = root / "active.sqlite", root / "effects.txt"
    with Child(ACTIVE_CUT_CHILD, str(path), cut, str(effects), home=root) as child:
        ready = child.expect("ready")
        child.send("go")
        reached = child.next_event()
        while reached["event"] == "mark-return":
            assert reached["result"] == "marked"
            reached = child.next_event()
        assert reached["event"] == "boundary" and reached["label"] == cut
        before_death = sqlite_rows(path)
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        process = child.evidence()
    actual_entries = effects.read_text().splitlines()
    assert actual_entries == (["run", "abort"] if cut == "abort-entered" else ["run"])
    assert not group_exists(child.group)
    evidence(
        root,
        "physical-cut",
        ready=ready,
        boundary=reached,
        SQL_before_death=sql_evidence(before_death),
        fsynced_actual_entries=actual_entries,
        process=process,
        leader_reaped=True,
        measured_group_absent=True,
        scope="Four explicit cuts, not exactly-once or exhaustive descendant tracing",
    )
    return path, ready["id"], reached["row"], actual_entries
