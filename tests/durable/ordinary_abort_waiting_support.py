"""Real retired-wait, Session COMMIT and process witnesses for leaf aborts."""

import asyncio
import json
import signal
import threading

from ordinary_abort_active_core_support import Gate
from ordinary_abort_active_support import sql_evidence
from ordinary_abort_support import ABORTED, MarkCommitCut
from ordinary_waiting_support import waiting
from owned_completing_support import DONE, Passes, States
from sqlite_child_support import Child, group_exists
from task_migration_support import sqlite_rows

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.scheduler import TaskScheduler, TaskSignal
from pipy_harness.native.durable.tasks import define_task


class WaitingLeaf:
    """Create U/T/W, run real waits, and join actual retired invocation Futures."""

    def __init__(self, rig, monkeypatch):
        self.rig = rig
        self.condition = threading.Condition()
        self.events, self.invocations, self.signals = [], [], []
        self.wait_finally, self.abort_gate, self.abort_finally = Gate(), Gate(), Gate()
        self.wait_finally.release.set()
        self.abort_gate.release.set()
        self.abort_finally.release.set()
        self.abort_action = None
        self.document = define_doc(
            "ordinary.wait.abort.saved",
            1,
            "task",
            lambda: {"n": 1},
            checkpoint_when=lambda *_: False,
        )
        self.u_source = self.definition("U", self._u)
        self.u = rig.create(self.u_source)
        self.t_source = self.definition("T", self._t)
        self.t = rig.create(self.t_source)
        self.source = self.definition("W", self._w, abort=self._abort)
        self.target = rig.create(self.source)
        self.scheduler = rig.scheduler = TaskScheduler(
            rig.session, [self.t_source, self.source]
        )
        self._observers(monkeypatch)
        self.states = States(rig)
        self.passes = Passes(self.scheduler, monkeypatch)
        self.scheduler.open()

    def definition(self, name, phase, *, abort=None, version=1):
        return define_task(
            "ordinary.wait.abort." + name,
            version,
            lambda _: {"phase": "start", "saved": [1, None]},
            {"start": phase, "finish": self._finish},
            self._unexpected_abort if abort is None else abort,
        )

    def _observers(self, monkeypatch):
        reserve, signal_task = self.scheduler._reserve, TaskSignal._signal

        async def observed():
            value = await reserve()
            if value is not None:
                with self.condition:
                    self.invocations.append(value)
                    self.condition.notify_all()
            return value

        def signal(actual):
            with self.condition:
                self.signals.append(actual)
                self.condition.notify_all()
            return signal_task(actual)

        monkeypatch.setattr(self.scheduler, "_reserve", observed)
        monkeypatch.setattr(TaskSignal, "_signal", signal)

    def note(self, label, task):
        with self.condition:
            self.events.append((label, task))
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return tuple(self.events)

    def invocation(self, task, mode="run"):
        with self.condition:

            def matching():
                return [
                    v
                    for v in self.invocations
                    if v.task_id == task and v.execution.mode == mode
                ]

            assert self.condition.wait_for(lambda: bool(matching()), 10)
            return matching()[-1]

    def signal_count(self, invocation):
        with self.condition:
            return sum(value is invocation.context.signal for value in self.signals)

    async def _u(self, row, runtime, _context):
        self.note("u-run", row["id"])
        await runtime.commit(lambda _tx, _row: DONE)

    async def _t(self, row, runtime, _context):
        self.note("t-run", row["id"])
        await runtime.commit(lambda _tx, _row: waiting(self.u))

    async def _w(self, row, runtime, _context):
        self.note("w-run", row["id"])
        assert await runtime.memo("saved", {"n": 1}) == {"n": 1}

        async def change(tx, _row):
            (await tx.doc(self.document))["n"] = 2
            return waiting(self.t)

        try:
            await runtime.commit(change)
        finally:
            await self.wait_finally.wait()
            self.note("wait-finally", row["id"])

    async def _finish(self, row, runtime, _context):
        self.note("finish", row["id"])
        await runtime.commit(lambda _tx, _row: DONE)

    async def _unexpected_abort(self, row, _runtime, _context):
        self.note("unexpected-abort", row["id"])
        raise AssertionError("An on target is not owned cancellation work")

    async def _abort(self, row, runtime, context):
        self.note("abort", row["id"])
        try:
            await self.abort_gate.wait()
            if self.abort_action is None:
                await runtime.commit(lambda _tx, _row: ABORTED)
            else:
                await self.abort_action(row, runtime, context)
        finally:
            await self.abort_finally.wait()
            self.note("abort-finally", row["id"])

    def park(self):
        self.scheduler.resume()
        self.states.wait(self.t, "waiting")
        self.states.wait(self.target, "waiting")
        for task in (self.t, self.target):
            self.invocation(task).retired.result(timeout=10)
        asyncio.run(self.scheduler.apause())
        assert self.rig.record(self.u)["state"]["status"] == "pending"
        assert self.rig.record(self.t)["state"] == waiting(self.u)
        assert self.rig.record(self.target)["state"] == waiting(self.t)
        with self.scheduler._guard:
            assert self.scheduler._invocation is None
            assert not self.scheduler._enabled and not self.scheduler._draining
        return self.rig.record(self.target)

    def complete_abort(self):
        self.scheduler.resume()
        row = self.states.wait(self.target, "terminal")
        self.invocation(self.target, "abort").retired.result(timeout=10)
        asyncio.run(self.scheduler.apause())
        return row

    def progress_targets(self):
        assert self.scheduler.install_definition(self.u_source)
        self.scheduler.resume()
        self.states.wait(self.u, "terminal")
        self.states.wait(self.t, "terminal")
        self.passes.idle()
        assert ("u-run", self.u) in self.snapshot()
        assert ("finish", self.t) in self.snapshot()
        assert ("finish", self.target) not in self.snapshot()

    def release(self):
        for gate in (self.wait_finally, self.abort_gate, self.abort_finally):
            gate.release.set()
        self.states.unsubscribe()


class WaitingMarkCommit(MarkCommitCut):
    """Only this row's real marked-WAITING SQL COMMIT, never its reservation."""

    def __init__(self, rig, target, cut, *, hold=False):
        super().__init__(rig, target, cut)
        self.hold = hold
        self.entered, self.release, self.retired = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.rows = []
        self.error = RuntimeError("waiting-mark-" + cut)

    def matches(self, connection):
        if self.fired or connection is not self.connection:
            return False
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        if found is None:
            return False
        row = sqlite._decode(found[0])
        return row["state"]["status"] == "waiting" and row["abortRequested"]

    def hit(self, connection):
        self.fired.append(connection.in_transaction)
        record = connection.execute(
            "SELECT record FROM tasks WHERE id=?",
            (self.target,),
        ).fetchone()
        self.rows.append(sqlite._decode(record[0]))
        self.entered.set()
        try:
            if not self.hold:
                raise self.error
            assert self.release.wait(10)
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


WAITING_CUT_CHILD = r"""
import asyncio, threading
from pathlib import Path
from pytest import MonkeyPatch
from ordinary_abort_waiting_support import WaitingLeaf
from task_migration_support import Rig
root, cut, effects = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
rig = Rig('sqlite', root)
patch = MonkeyPatch()
value = WaitingLeaf(rig, patch)

def effect(label):
    with effects.open('a') as file:
        file.write(label + '\n'); file.flush(); os.fsync(file.fileno())

def boundary(label):
    row = rig.record(value.target)
    say('boundary', label=label, row=row, target=rig.record(value.t),
        blocker=rig.record(value.u), events=value.snapshot())
    if not threading.Event().wait(60):
        raise AssertionError('parent did not kill the observed boundary')

async def abort(row, runtime, context):
    effect('abort')
    await asyncio.to_thread(boundary, 'abort-entered')

value.abort_action = abort
value.park()
effect('wait-retired')
if cut != 'wait-unmarked':
    assert asyncio.run(value.scheduler.aabort_leaf(value.target)) == 'marked'
say('ready', id=value.target, target=value.t, blocker=value.u)
assert command() == 'go'
if cut in ('wait-unmarked', 'wait-marked'):
    boundary(cut)
value.scheduler.resume()
if not threading.Event().wait(60):
    raise AssertionError('abort boundary never arrived')
"""


def physical_wait_cut(root, cut):
    root.mkdir()
    effects = root / "effects.txt"
    with Child(WAITING_CUT_CHILD, str(root), cut, str(effects), home=root) as child:
        ready = child.expect("ready")
        child.send("go")
        reached = child.expect("boundary")
        assert reached["label"] == cut
        before = sqlite_rows(root / "migration.sqlite")
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        process = child.evidence()
    assert not group_exists(child.group)
    entries = effects.read_text().splitlines()
    assert entries == (
        ["wait-retired", "abort"] if cut == "abort-entered" else ["wait-retired"]
    )
    report = {
        "cut": cut,
        "ready": ready,
        "boundary": reached,
        "SQL_beforedeath": sql_evidence(before),
        "actual_effects": entries,
        "actual_process": process,
        "leader_reaped": True,
        "measured_group_absent": True,
        "limit": "Finite observed boundaries, not exactly-once/atomic-counter/Pi-concurrency evidence",
    }
    (root / "process.json").write_text(json.dumps(report, indent=2) + "\n")
    return root / "migration.sqlite", ready, reached, before


def observe_close_wait(session, monkeypatch):
    """Forward the actual Session close wait; never replace its completion."""
    entered = threading.Event()
    original = type(session)._closed

    async def observed(actual):
        if actual is session:
            entered.set()
        return await original(actual)

    monkeypatch.setattr(type(session), "_closed", observed)
    return entered
