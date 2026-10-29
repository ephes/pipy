"""Structural absent-callback fixtures; no invented migration-failure memo."""

import asyncio

from definition_publication_live_support import owner
from orphan_missing_definition_support import terminal_fields

from pipy_harness.native.durable.tasks import define_task

REFUSAL = "Task has no strictly newer definition without a migration handler"
GRAPH_REFUSAL = "Task requires a validated unmarked pending root leaf"


class Calls:
    def __init__(self, name="missing-migration.broad"):
        self.name = name
        self.events = []

    def declaration(self, version, label, *, migration=False):
        def initial(_value):
            self.events.append(("initial", label))
            return {"phase": "start", "saved": [1, None]}

        async def phase(*_args):
            self.events.append(("phase", label))
            raise AssertionError("The structurally blocked target must not run")

        async def abort(*_args):
            self.events.append(("abort", label))
            raise AssertionError("Explicit orphaning must not invoke abort")

        def migrate(_value, _checkpoint, from_version):
            self.events.append(("migrate", label, from_version))
            raise AssertionError("The explicit action must not fit or migrate code")

        return define_task(
            self.name,
            version,
            initial,
            {"start": phase},
            abort,
            migrate=migrate if migration else None,
        )

    def checkpoint(self):
        return tuple(self.events)


def structural_target(rig, *, name="missing-migration.broad"):
    calls = Calls(name)
    target = rig.create(calls.declaration(1, "stored"))
    current = calls.declaration(2, "current")
    scheduler = owner(rig, [current])
    assert calls.checkpoint() == (("initial", "stored"),)
    assert rig.record(target)["state"]["status"] == "pending"
    return target, current, calls, scheduler


def assert_missing_terminal(rig, target, before):
    after = rig.record(target)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "migration_failed"},
    }
    assert terminal_fields(after) == terminal_fields(before)
    assert after["abortRequested"] is False
    assert "memos" not in after and "restarts" not in after
    assert type(after["endedAt"]) in (int, float)
    return after


def terminal_replay(rig, scheduler, target):
    before = rig.silence()
    assert scheduler.orphan_missing_migration(target) == "terminal"
    assert asyncio.run(scheduler.aorphan_missing_migration(target)) == "terminal"
    rig.assert_silent(before)
