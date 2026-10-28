"""Real Session-line and callback witnesses for definition publication tests."""

import asyncio
import threading
from contextlib import contextmanager

from restart_policy_live_support import Tickets, held_line, idle, pause
from task_migration_support import Rig, definition, opener

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.task_values import identity
from pipy_harness.native.durable.tasks import Task


def owner(rig, definitions):
    scheduler = TaskScheduler(rig.session, definitions)
    rig.scheduler = scheduler
    scheduler.open()
    return scheduler


def generation(scheduler):
    def read():
        with scheduler._guard:
            return scheduler._definition_generation

    return scheduler._session.read_on_line(read)


def same_fields(task):
    """A genuinely different Task with the exact original callback identities."""
    return Task(
        task.name,
        task.version,
        task.initial,
        dict(task.phases),
        task.abort,
        support=task.support,
        migrate=task.migrate,
    )


class Passes:
    """Observe actual reserve calls and wait for host-owned real retirement."""

    def __init__(self, scheduler, monkeypatch):
        self.condition = threading.Condition()
        self.count = 0
        self.visits = []
        reserve = scheduler._reserve
        candidate = scheduler._candidate

        async def observed_candidate(tx, row, *args, **kwargs):
            with self.condition:
                self.visits.append((id(tx), row["id"]))
            return await candidate(tx, row, *args, **kwargs)

        async def observed_reserve():
            try:
                return await reserve()
            finally:
                with self.condition:
                    self.count += 1
                    self.condition.notify_all()

        monkeypatch.setattr(scheduler, "_reserve", observed_reserve)
        monkeypatch.setattr(scheduler, "_candidate", observed_candidate)
        self.scheduler = scheduler

    def checkpoint(self):
        with self.condition:
            return self.count

    def retired_after(self, before):
        with self.condition:
            assert self.condition.wait_for(lambda: self.count > before, 5)
        idle(self.scheduler)


class Wakes:
    def __init__(self, scheduler, monkeypatch):
        self.condition = threading.Condition()
        self.count = 0
        original = scheduler._notify

        def notify():
            with self.condition:
                self.count += 1
            return original()

        monkeypatch.setattr(scheduler, "_notify", notify)

    def checkpoint(self):
        with self.condition:
            return self.count


class DefinitionHold:
    """Hold an actual accepted job before/after its publication callback."""

    def __init__(self, session, monkeypatch, stage, method="install_definition"):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.retired = threading.Event()
        self.results = []
        self.errors = []
        original = type(session)._on_line_sync

        def on_line(actual, label, job):
            if actual is not session or label != method:
                return original(actual, label, job)

            def observed():
                if stage == "before":
                    self.entered.set()
                    assert self.release.wait(10)
                try:
                    result = job()
                except BaseException as error:
                    self.errors.append(type(error))
                    raise
                self.results.append(result)
                if stage == "after":
                    self.entered.set()
                    assert self.release.wait(10)
                return result

            try:
                return original(actual, label, observed)
            finally:
                self.retired.set()

        monkeypatch.setattr(type(session), "_on_line_sync", on_line)


@contextmanager
def held_real_drain(scheduler, monkeypatch):
    """The service admits draining; the real continuation leaves the line free."""
    entered, release, retired = (threading.Event() for _ in range(3))
    original = scheduler._drain

    async def held():
        entered.set()
        try:
            assert await asyncio.to_thread(release.wait, 10)
            return await original()
        finally:
            retired.set()

    monkeypatch.setattr(scheduler, "_drain", held)
    scheduler.resume()
    try:
        assert entered.wait(5)
        with scheduler._guard:
            assert scheduler._draining is True
            assert scheduler._invocation is None
        yield
    finally:
        release.set()
        assert retired.wait(5)
        idle(scheduler)
        pause(scheduler)


class CallbackProbe:
    """Callable equality/disposal observations without changing Task weakrefs."""

    def __init__(self, calls, *, failure=False, drops=None, session=None, guard=None):
        self.calls = calls
        self.failure = failure
        self.drops = drops
        self.session = session
        self.guard = guard

    def __call__(self, input_value, checkpoint, from_version):
        self.calls.append((input_value, checkpoint, from_version))
        if self.failure:
            raise ValueError("private migration failure")
        return {"input": input_value, "checkpoint": checkpoint}

    def __eq__(self, _other):
        raise AssertionError("Publication must compare callback identities")

    def __del__(self):
        if self.drops is None:
            return
        held = self.guard._is_owned()
        outcome = "no_session"
        if self.session is not None:
            try:
                self.session.read_on_line(lambda: None)
            except Exception as error:  # noqa: BLE001 - retain exact disposal outcome
                outcome = type(error).__name__
            else:
                outcome = "read_returned"
        self.drops.append((held, outcome))


def resume_retired(scheduler, passes):
    before = passes.checkpoint()
    scheduler.resume()
    passes.retired_after(before)


def successful_waking_install(scheduler, task, expected, passes, wakes):
    idle(scheduler)
    before_pass, before_wake = passes.checkpoint(), wakes.checkpoint()
    assert scheduler.install_definition(task) is expected
    assert wakes.checkpoint() == before_wake + 1
    passes.retired_after(before_pass)


def assert_binding(snapshot, name, source, captured=None):
    key = identity(name)
    binding = snapshot.bindings[key]
    assert binding.source is source
    assert snapshot.definitions[key] is binding.captured
    if captured is not None:
        assert binding.captured is captured
    return binding.captured


__all__ = [
    "CallbackProbe",
    "DefinitionHold",
    "Passes",
    "Rig",
    "Tickets",
    "Wakes",
    "assert_binding",
    "definition",
    "generation",
    "held_line",
    "held_real_drain",
    "idle",
    "opener",
    "owner",
    "pause",
    "resume_retired",
    "same_fields",
    "successful_waking_install",
]
