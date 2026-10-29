"""B5 controls built from genuine B4 Session/Tx fixtures, not historical rows."""

import asyncio

from definition_publication_live_support import owner
from orphan_missing_definition_support import terminal_fields
from task_migration_support import ObservedStorage

from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task

GRAPH_REFUSAL = "Task requires a validated unmarked pending root leaf"
VERSION_REFUSAL = "Task definition is not older than the stored task"


class TargetCalls:
    """Creation initializers are counted before the explicit-action baseline."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def declaration(self, version):
        def initial(_value):
            self.calls.append(("initial", version))
            return {"phase": "start", "saved": [1, None]}

        async def phase(*_args):
            self.calls.append(("phase", version))
            raise AssertionError("The target phase must not run")

        async def abort(*_args):
            self.calls.append(("abort", version))
            raise AssertionError("The explicit control must not abort target code")

        def migrate(_value, _checkpoint, from_version):
            self.calls.append(("migrate", version, from_version))
            raise AssertionError("The explicit control must not migrate the target")

        return define_task(
            self.name,
            version,
            initial,
            {"start": phase},
            abort,
            migrate=migrate,
        )

    def checkpoint(self):
        return tuple(self.calls)


class FitCalls:
    """Forward real dispatch helpers; count only visits for the selected target."""

    def __init__(self, scheduler, target, monkeypatch):
        self.calls = []

        def instrument(name):
            original = getattr(scheduler, name)

            def observed(row, *args, **kwargs):
                if row["id"] == target:
                    self.calls.append(name)
                return original(row, *args, **kwargs)

            monkeypatch.setattr(scheduler, name, observed)

        instrument("_fit")
        instrument("_fitting_definition")


def created_target(rig, name):
    calls = TargetCalls(name)
    stored, older = calls.declaration(2), calls.declaration(1)
    target = rig.create(stored)
    assert calls.checkpoint() == (("initial", 2),)
    assert rig.record(target)["version"] == 2
    return target, older, calls


def assert_too_old_terminal(rig, target, prior):
    after = rig.record(target)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "task_too_old"},
    }
    assert terminal_fields(after) == terminal_fields(prior)
    assert "memos" not in after and "restarts" not in after
    assert after["abortRequested"] is False
    assert type(after["endedAt"]) in (int, float)
    return after


def assert_terminal_replay(rig, scheduler, target):
    before = rig.silence()
    assert scheduler.orphan_task_too_old(target) == "terminal"
    assert asyncio.run(scheduler.aorphan_task_too_old(target)) == "terminal"
    rig.assert_silent(before)


def reopen_sqlite(rig, definitions):
    """A real new owner over the same database, without a second setup commit."""
    assert rig.backend == "sqlite"
    rig.close()
    assert rig.storage.closed.is_set()
    rig.storage = ObservedStorage(open_sqlite_storage(str(rig.path), mode="open"))
    rig.session = Session(rig.storage)
    rig.scheduler = None
    return owner(rig, definitions)
