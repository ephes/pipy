"""Real Session/phase observations for the bounded ordinary-owned executor."""

import asyncio
import threading

from task_migration_support import sqlite_rows, unexpected_abort

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.storage import StorageWrite
from pipy_harness.native.durable.tasks import define_task

DONE = {"status": "terminal", "outcome": {"status": "completed", "result": None}}
HELD = {"status": "completing", "outcome": {"status": "completed", "result": None}}


def definition(name, phase, *, phases=None):
    return define_task(
        name,
        1,
        lambda _: {"phase": "start"},
        {"start": phase} if phases is None else phases,
        unexpected_abort,
    )


class States:
    """Actual commit publications, confirmed by a subsequent genuine line read."""

    def __init__(self, rig):
        self.rig = rig
        self.condition = threading.Condition()
        self.rows = []

        def observe(commit):
            with self.condition:
                for change in commit.changes:
                    if type(change) is StorageWrite:
                        row = change.to_tree()
                        if row["type"] == "task":
                            self.rows.append(row["value"])
                self.condition.notify_all()

        self.unsubscribe = rig.session.subscribe_commits(observe)

    def wait(self, task_id, status):
        with self.condition:
            assert self.condition.wait_for(
                lambda: any(
                    row["id"] == task_id and row["state"]["status"] == status
                    for row in self.rows
                ),
                10,
            )
        return self.rig.record(task_id)


class Passes:
    """Observe actual reserve returns, never replace its admission decision."""

    def __init__(self, scheduler, monkeypatch):
        self.scheduler = scheduler
        self.condition = threading.Condition()
        self.returns = []
        self.consumed = 0
        original = scheduler._reserve

        async def reserve():
            value = await original()
            with self.condition:
                self.returns.append(None if value is None else value.task_id)
                self.condition.notify_all()
            return value

        monkeypatch.setattr(scheduler, "_reserve", reserve)

    def idle(self):
        with self.condition:
            assert self.condition.wait_for(
                lambda: len(self.returns) > self.consumed and self.returns[-1] is None,
                10,
            )
            self.consumed = len(self.returns)
        asyncio.run(self.scheduler.apause())


def start(rig, definitions, monkeypatch):
    rig.scheduler = TaskScheduler(rig.session, definitions)
    passes = Passes(rig.scheduler, monkeypatch)
    rig.scheduler.open()
    return rig.scheduler, passes


def make_child(rig, owner, source):
    return rig.session.commit(
        lambda tx: tx.create_task(
            source, None, ownership={"kind": "task", "taskId": owner}
        )
    )


def end(rig, target):
    rig.change(target, lambda row: row.update(state=DONE))


class FinalizationCut:
    """Filter actual SQLite COMMIT cuts to the real final parent transaction."""

    def __init__(self, rig, parent, cut):
        self.rig, self.parent, self.cut = rig, parent, cut
        self.connection = rig.storage.inner._database.connection()
        self.before_hook, self.after_hook = sqlite._before_commit, sqlite._after_commit
        self.error = RuntimeError("owned-finalize-" + cut)
        self.fired, self.before_rows = [], []

    def target(self, actual):
        if actual is not self.connection:
            return False
        row = actual.execute(
            "SELECT record FROM tasks WHERE id=?", (self.parent,)
        ).fetchone()
        return (
            row is not None and sqlite._decode(row[0])["state"]["status"] == "terminal"
        )

    def before(self, actual):
        if self.target(actual):
            self.before_rows.append(sqlite_rows(self.rig.path))
            if self.cut == "before":
                self.fired.append(actual.in_transaction)
                raise self.error
        return self.before_hook(actual)

    def after(self, actual):
        if self.target(actual) and self.cut == "after":
            self.fired.append(actual.in_transaction)
            raise self.error
        return self.after_hook(actual)

    def install(self, patch):
        patch.setattr(sqlite, "_before_commit", self.before)
        patch.setattr(sqlite, "_after_commit", self.after)
