"""Real-line witnesses for same-owner restart-policy tests only."""

import asyncio
import threading
from contextlib import contextmanager

from task_migration_support import Rig, definition, opener

from pipy_harness.native.durable.scheduler import TaskScheduler


def owner(rig, definitions, limit=5):
    scheduler = TaskScheduler(rig.session, definitions, restart_limit=limit)
    rig.scheduler = scheduler
    scheduler.open()
    return scheduler


def seed_count(rig, task_id, value):
    rig.change(task_id, lambda row: row.update(restarts=value))


def completing(calls, name="live.policy", *, release=None, entered=None):
    async def phase(row, runtime, _context):
        calls.append((row["id"], row.get("restarts")))
        if entered is not None:
            entered.set()
        if release is not None:
            from pipy_harness.native.durable.generation_transport import owned_call

            assert await owned_call(lambda: release.wait(10))
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    return definition(name, phase=phase)


def pause(scheduler):
    asyncio.run(scheduler.apause())


def idle(scheduler):
    """Wait on the event on its owning loop, after a witnessed real pass."""
    asyncio.run_coroutine_threadsafe(
        scheduler._quiescent.wait(), scheduler._loop
    ).result(10)


def pass_finished(scheduler, monkeypatch):
    event = threading.Event()
    original = scheduler._reserve

    async def observed():
        try:
            return await original()
        finally:
            event.set()

    monkeypatch.setattr(scheduler, "_reserve", observed)
    return event


@contextmanager
def held_line(session, pool):
    entered, release, retired = (threading.Event() for _ in range(3))

    def job():
        entered.set()
        assert release.wait(10)

    def run():
        try:
            session.read_on_line(job)
        finally:
            retired.set()

    work = pool.submit(run)
    try:
        assert entered.wait(5)
        yield release
    finally:
        release.set()
        work.result(10)
        assert retired.is_set()


class Tickets:
    """Observe actual enqueue, without changing grant or line behavior."""

    def __init__(self, session, monkeypatch):
        self.condition = threading.Condition()
        self.queued = []
        owner = session._owner
        original = type(owner)._line_enter

        def enter(actual, ticket):
            original(actual, ticket)
            if actual is not owner:
                return
            with owner._cond:
                queued = any(value is ticket for value in owner._queue)
            if queued:
                with self.condition:
                    self.queued.append((id(ticket), ticket.thread))
                    self.condition.notify_all()

        monkeypatch.setattr(type(owner), "_line_enter", enter)

    def wait(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda: len(self.queued) >= count, 5)
            return tuple(self.queued[:count])


class PublicationHold:
    """Hold a real setter line job, before or after actual publication."""

    def __init__(self, session, monkeypatch, stage):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.retired = threading.Event()
        self.actual_results = []
        self.actual_errors = []
        original = type(session)._on_line_sync

        def on_line(actual, method, job):
            if actual is not session or method != "set_restart_limit":
                return original(actual, method, job)

            def held():
                if stage == "before":
                    self.entered.set()
                    assert self.release.wait(10)
                try:
                    value = job()
                except BaseException as error:
                    self.actual_errors.append(type(error))
                    raise
                self.actual_results.append(value)
                if stage == "after":
                    self.entered.set()
                    assert self.release.wait(10)
                return value

            try:
                return original(actual, method, held)
            finally:
                self.retired.set()

        monkeypatch.setattr(type(session), "_on_line_sync", on_line)


__all__ = [
    "PublicationHold",
    "Rig",
    "Tickets",
    "completing",
    "definition",
    "held_line",
    "idle",
    "opener",
    "owner",
    "pass_finished",
    "pause",
    "seed_count",
]
