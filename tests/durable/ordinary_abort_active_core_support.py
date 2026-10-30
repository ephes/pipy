"""Real Session/host barriers for active strict-leaf Core cancellation tests.

These are in-process ownership witnesses, not physical crash or Pi evidence.
"""

import asyncio
import threading

from owned_completing_support import DONE, Passes, States
from task_migration_support import Rig

from pipy_harness.native.durable import ordinary_abort
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.storage import StorageWrite
from pipy_harness.native.durable.tasks import define_task

CHECKPOINT = {"phase": "start", "saved": [1, None]}


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    async def wait(self):
        self.entered.set()
        assert await asyncio.to_thread(self.release.wait, 10)


class Active:
    def __init__(self, rig, monkeypatch, *, body=None, abort=None, version=1):
        self.rig = rig
        self.finally_gate = Gate()
        self.abort_gate = Gate()
        self.abort_gate.release.set()
        self.entered = threading.Event()
        self.marked = threading.Event()
        self.finished = threading.Event()
        self.abort_entered = threading.Event()
        self.calls = []
        self.runtime = self.context = None
        self.writes = []
        self.requests = threading.Condition()
        self.mark_returns = []
        original_mark = ordinary_abort._mark

        async def mark(scheduler, task_id):
            result = await original_mark(scheduler, task_id)
            if scheduler is self.scheduler:
                with self.requests:
                    self.mark_returns.append(result)
                    self.requests.notify_all()
            return result

        monkeypatch.setattr(ordinary_abort, "_mark", mark)

        async def phase(row, runtime, context):
            self.runtime, self.context = runtime, context
            self.entered.set()
            try:
                if body is None:
                    await context.signal.wait()
                else:
                    await body(row, runtime, context)
            finally:
                await self.finally_gate.wait()
                self.calls.append("run-finally")
                self.finished.set()

        async def cleanup(row, runtime, context):
            assert self.finished.is_set()
            self.calls.append("abort")
            self.abort_entered.set()
            await self.abort_gate.wait()
            if abort is None:
                await runtime.commit(lambda _tx, _row: DONE)
            else:
                await abort(row, runtime, context)

        self.task = define_task(
            "active.leaf", version, lambda _: CHECKPOINT, {"start": phase}, cleanup
        )
        self.target = rig.create(self.task)
        self.scheduler = rig.scheduler = TaskScheduler(rig.session, [self.task])
        self.passes = Passes(self.scheduler, monkeypatch)
        self.states = States(rig)

        self.unsubscribe = rig.session.subscribe_commits(self._observe)
        self.scheduler.open()

    def _observe(self, publication):
        for change in publication.changes:
            if type(change) is StorageWrite:
                tree = change.to_tree()
                if tree["type"] == "task" and tree["value"]["id"] == self.target:
                    row = tree["value"]
                    self.writes.append(row)
                    if row["abortRequested"]:
                        self.marked.set()

    def start(self):
        self.scheduler.resume()
        assert self.entered.wait(10)
        return self.runtime._invocation

    def request(self):
        return asyncio.run(self.scheduler.aabort_leaf(self.target))

    def marked_requests(self, count):
        with self.requests:
            assert self.requests.wait_for(lambda: len(self.mark_returns) >= count, 10)

    def release(self):
        self.finally_gate.release.set()
        self.abort_gate.release.set()

    def done(self):
        assert self.states.wait(self.target, "terminal")["state"] == DONE
        self.passes.idle()
        self.unsubscribe()
        self.states.unsubscribe()


def isolated_rig(request, tmp_path, monkeypatch):
    for key in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("PIPY_THEME", "pi")
    return Rig(request.param, tmp_path)
