"""Genuine owner-local failed migrations; no seeded failure-cache authority."""

from definition_publication_live_support import (
    Passes,
    definition,
    owner,
    pause,
    resume_retired,
)
from orphan_missing_definition_support import terminal_fields

from pipy_harness.native.durable.task_values import identity
from pipy_harness.native.durable.tasks import define_task

REFUSAL = "Task has no observed failed migration for its current definition"
GRAPH_REFUSAL = "Task requires a validated unmarked pending root leaf"


class Calls:
    def __init__(self, name="failed.broad"):
        self.name = name
        self.events = []

    def declaration(self, version, label, *, malformed=False):
        def initial(_value):
            self.events.append(("initial", label))
            return {"phase": "start", "saved": [1, None]}

        async def phase(*_args):
            self.events.append(("phase", label))
            raise AssertionError("Failed target must not dispatch")

        async def abort(*_args):
            self.events.append(("abort", label))
            raise AssertionError("Orphan control must not invoke abort")

        def migrate(value, checkpoint, from_version):
            self.events.append(("migrate", label, from_version))
            assert value == {"input": [1, None]}
            assert checkpoint == {"phase": "start", "saved": [1, None]}
            if malformed:
                return {"input": value}  # Actual result validation must fail.
            raise ValueError("genuine broad migration failure")

        return define_task(
            self.name, version, initial, {"start": phase}, abort, migrate=migrate
        )

    def checkpoint(self):
        return tuple(self.events)


def authority(scheduler, target, name="failed.broad"):
    def read():
        with scheduler._guard:
            return (
                scheduler._definition_generation.bindings.get(identity(name)),
                scheduler._migration_failures.get(target),
            )

    return scheduler._session.read_on_line(read)


def observed_failure(rig, monkeypatch, *, malformed=False):
    calls = Calls()
    target = rig.create(calls.declaration(1, "stored"))
    source = calls.declaration(2, "A", malformed=malformed)
    scheduler = owner(rig, [source])
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    binding, memo = authority(scheduler, target)
    assert binding is not None and memo is binding
    assert binding.source is source
    assert calls.checkpoint() == (("initial", "stored"), ("migrate", "A", 1))
    assert rig.record(target)["state"]["status"] == "pending"
    return target, source, calls, scheduler, passes, binding


def assert_failed_terminal(rig, target, before):
    after = rig.record(target)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "migration_failed"},
    }
    assert terminal_fields(after) == terminal_fields(before)
    assert "memos" not in after and "restarts" not in after
    assert type(after["endedAt"]) in (int, float)
    return after


def terminal_replay(rig, scheduler, target):
    before = rig.silence()
    assert scheduler.orphan_failed_migration(target) == "terminal"
    rig.assert_silent(before)


def unrelated_definition():
    return definition("failed.unrelated")
