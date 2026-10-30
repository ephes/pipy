"""Actual Session/Tx waiting witnesses; no fabricated graph or waiter authority."""

import threading

from owned_completing_support import DONE, States, definition, start
from task_migration_support import sqlite_rows

from pipy_harness.native.durable import sqlite


def waiting(*targets, phase="finish", receipt="saved"):
    return {
        "status": "waiting",
        "checkpoint": {"phase": phase, "receipt": receipt},
        "on": list(targets),
        "policy": "allSettled",
    }


class WaitingCommitCut:
    """Real SQLite transaction cut, filtered to publication of this exact wait."""

    def __init__(self, rig, target, cut):
        self.rig = rig
        self.target = target
        self.cut = cut
        self.connection = rig.storage.inner._database.connection()
        self.before_hook = sqlite._before_commit
        self.after_hook = sqlite._after_commit
        self.error = RuntimeError("ordinary-wait-" + cut)
        self.fired = []
        self.before_rows = []

    def matches(self, connection):
        if connection is not self.connection:
            return False
        found = connection.execute(
            "SELECT record FROM tasks WHERE id=?", (self.target,)
        ).fetchone()
        return (
            found is not None
            and sqlite._decode(found[0])["state"]["status"] == "waiting"
        )

    def before(self, connection):
        if self.matches(connection):
            self.before_rows.append(sqlite_rows(self.rig.path))
            if self.cut == "before":
                self.fired.append(connection.in_transaction)
                raise self.error
        return self.before_hook(connection)

    def after(self, connection):
        if self.matches(connection) and self.cut == "after":
            self.fired.append(connection.in_transaction)
            raise self.error
        return self.after_hook(connection)

    def install(self, monkeypatch):
        monkeypatch.setattr(sqlite, "_before_commit", self.before)
        monkeypatch.setattr(sqlite, "_after_commit", self.after)


class PhaseGate:
    """A real running phase waits outside the Session line; no fake Future."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.refused = threading.Event()
        self.continue_ = threading.Event()


__all__ = [
    "DONE",
    "PhaseGate",
    "States",
    "WaitingCommitCut",
    "definition",
    "start",
    "waiting",
]
