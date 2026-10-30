"""Structural absent-migrator authority, distinct from observed failure authority."""

import asyncio
import gc
import weakref

import pytest
from definition_publication_live_support import (
    CallbackProbe,
    Passes,
    definition,
    owner,
    pause,
    resume_retired,
)
from test_orphan_failed_migration_core import authority, failed, unexpected
from test_orphan_failed_migration_core import rig as rig

from pipy_harness.native.durable import orphan_control
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session_core import ReentrantSessionCall
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.tasks import define_task

REFUSAL = "^Task has no strictly newer definition without a migration handler$"
B6_REFUSAL = "^Task has no observed failed migration for its current definition$"


def declaration(version, calls):
    def initial(_value):
        calls.append("initial")
        return {"phase": "start"}

    async def forbidden(*_args):
        calls.append("unexpected-task-callback")
        raise AssertionError("Orphan settlement must not dispatch target code")

    return define_task(
        "migration.work", version, initial, {"start": forbidden}, forbidden
    )


def test_unobserved_absence_settles_without_fitting_and_retires_task_doc(
    rig, monkeypatch
):
    calls = []
    target = rig.create(declaration(1, calls))
    scheduler = owner(rig, [declaration(2, calls)])
    token = define_doc(
        "missing-migrator-doc",
        1,
        "task",
        lambda: {"value": 1},
        checkpoint_when=lambda _value, _ops, _info: False,
    )

    def prepare(tx):
        tx.doc(token, task_id=target)["value"] = 2
        row = tx.task(target).to_tree()
        tx.set_task({**row, "memos": {"saved": True}, "restarts": 2.5})

    rig.session.commit(prepare)
    address = DocumentAddress(
        {"kind": token.kind, "scope": {"kind": "task", "taskId": target}}
    )
    document_id = rig.session.read_on_line(
        lambda: rig.storage.find_document(address).id
    )
    current, memo = authority(scheduler, target)
    assert current.captured.migrate is None and memo is None
    monkeypatch.setattr(TaskScheduler, "_fit", unexpected)
    monkeypatch.setattr(TaskScheduler, "_fitting_definition", unexpected)
    silent = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match=B6_REFUSAL):
        scheduler.orphan_failed_migration(target)
    rig.assert_silent(silent)
    before = rig.record(target)
    assert asyncio.run(scheduler.aorphan_missing_migration(target)) == "orphaned"
    after = rig.record(target)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "migration_failed"},
    }
    assert "memos" not in after and "restarts" not in after and "endedAt" in after
    for key, value in before.items():
        if key not in {"state", "memos", "restarts", "endedAt"}:
            assert after[key] == value
    assert rig.session.read_on_line(lambda: rig.storage.document(document_id)) is None
    assert rig.session.read_on_line(lambda: rig.storage.find_document(address)) is None
    assert len(rig.storage.commits) == silent[1] + 1
    assert calls == ["initial"] and authority(scheduler, target)[1] is None
    with scheduler._guard:
        assert scheduler._enabled is False
    silent = rig.silence()
    assert scheduler.orphan_missing_migration(target) == "terminal"
    rig.assert_silent(silent)


@pytest.mark.parametrize("available", ["missing", "equal", "older", "callable"])
def test_structural_partition_refuses_without_observation_or_writes(
    rig, monkeypatch, available
):
    target = rig.create(definition())
    current = {
        "missing": [],
        "equal": [definition(version=1)],
        "older": [definition(version=0)],
        "callable": [definition(version=2, migrate=unexpected)],
    }[available]
    scheduler = owner(rig, current)
    assert authority(scheduler, target)[1] is None
    monkeypatch.setattr(TaskScheduler, "_fit", unexpected)
    monkeypatch.setattr(TaskScheduler, "_fitting_definition", unexpected)
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
        scheduler.orphan_missing_migration(target)
    rig.assert_silent(before)
    assert rig.session.failure is None


def test_real_observed_callable_failure_refuses_b7_and_preserves_b6(rig, monkeypatch):
    target, _, scheduler, _, calls, binding = failed(rig, monkeypatch)
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
        scheduler.orphan_missing_migration(target)
    rig.assert_silent(before)
    current, memo = authority(scheduler, target)
    assert current is binding and memo is binding and len(calls) == 1
    assert scheduler.orphan_failed_migration(target) == "orphaned"
    assert len(calls) == 1


def test_stale_real_memo_does_not_block_absence_and_terminal_releases_it(
    rig, monkeypatch
):
    target = rig.create(definition())
    scheduler = owner(rig, [])
    calls, drops = [], []
    probe = CallbackProbe(
        calls, failure=True, drops=drops, session=rig.session, guard=scheduler._guard
    )
    weak = weakref.ref(probe)
    source = definition(version=2, migrate=probe)
    assert scheduler.install_definition(source) is True
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    binding, memo = authority(scheduler, target)
    assert binding is memo and binding is not None and len(calls) == 1
    assert scheduler.install_definition(definition(version=3)) is True
    current, memo = authority(scheduler, target)
    assert current is not binding and memo is binding
    assert current.captured.migrate is None
    del source, probe, binding, memo
    gc.collect()
    assert weak() is not None and drops == []
    assert scheduler.orphan_missing_migration(target) == "orphaned"
    gc.collect()
    assert authority(scheduler, target)[1] is None and weak() is None
    assert len(drops) == 1 and drops[0][0] is False
    assert drops[0][1] in {"ReentrantSessionCall", "read_returned"}
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("captured_version", "captured_handler", "reflected_version", "allowed"),
    [(2, None, 0, True), (2, unexpected, 3, False), (1, None, 3, False)],
)
def test_captured_callback_and_version_ignore_mutable_source_reflection(
    rig, captured_version, captured_handler, reflected_version, allowed
):
    target = rig.create(definition())
    source = definition(version=captured_version, migrate=captured_handler)
    scheduler = owner(rig, [source])
    object.__setattr__(source, "version", reflected_version)
    object.__setattr__(
        source, "migrate", unexpected if captured_handler is None else None
    )
    before = rig.silence()
    if allowed:
        assert scheduler.orphan_missing_migration(target) == "orphaned"
    else:
        with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
            scheduler.orphan_missing_migration(target)
        rig.assert_silent(before)


@pytest.mark.parametrize(
    ("stored", "available", "allowed"),
    [
        (0.0, -0.0, False),
        (-0.0, 0, False),
        (-1.5, -1, True),
        (-1, -1.5, False),
        (1.25, 1.5, True),
    ],
)
def test_existing_finite_numeric_domain_uses_strict_comparison(
    rig, stored, available, allowed
):
    target = rig.create(definition(version=stored))
    scheduler = owner(rig, [definition(version=available)])
    before = rig.silence()
    if allowed:
        assert scheduler.orphan_missing_migration(target) == "orphaned"
    else:
        with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
            scheduler.orphan_missing_migration(target)
        rig.assert_silent(before)


@pytest.mark.parametrize("name", ["migration.work", "pi.tool"])
def test_terminal_precedes_graph_generation_but_reserved_refusal_stays_first(
    rig, monkeypatch, name
):
    target = rig.create(definition(name))
    rig.change(
        target,
        lambda row: row.update(
            state={
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        ),
    )
    scheduler = owner(rig, [])
    monkeypatch.setattr(orphan_control, "require_unmarked_root_leaf", unexpected)
    monkeypatch.setattr(orphan_control, "identity", unexpected)
    before = rig.silence()
    if name.startswith("pi."):
        with pytest.raises(UnsupportedTaskExecution, match="Built-in tasks"):
            scheduler.orphan_missing_migration(target)
    else:
        assert scheduler.orphan_missing_migration(target) == "terminal"
    rig.assert_silent(before)


@pytest.mark.parametrize("value", [True, 1.0, 0, 2**53])
def test_new_wrappers_keep_id_validation_before_unopened_admission(rig, value):
    scheduler = TaskScheduler(rig.session, [])
    rig.scheduler = scheduler
    before = rig.silence()
    with pytest.raises(RecordError):
        scheduler.orphan_missing_migration(value)
    with pytest.raises(RecordError):
        asyncio.run(scheduler.aorphan_missing_migration(value))
    with pytest.raises(RuntimeError, match="Open the scheduler"):
        scheduler.orphan_missing_migration(1)
    rig.assert_silent(before)


def test_new_wrappers_retain_reentry_offloop_and_unknown_id(rig):
    scheduler = owner(rig, [])
    before = rig.silence()
    with pytest.raises(ReentrantSessionCall):
        rig.session.read_on_line(lambda: scheduler.orphan_missing_migration(True))

    async def call():
        with pytest.raises(RuntimeError, match="aorphan_missing_migration"):
            scheduler.orphan_missing_migration(True)
        with pytest.raises(ValueError, match="Unknown task"):
            await scheduler.aorphan_missing_migration(999999)

    asyncio.run(call())
    rig.assert_silent(before)
