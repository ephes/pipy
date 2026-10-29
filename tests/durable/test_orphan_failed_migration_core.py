"""Observed owner-local migration failures, with real reservation and Tx witnesses."""

import asyncio

import pytest
from definition_publication_live_support import (
    CallbackProbe,
    Passes,
    Rig,
    definition,
    owner,
    pause,
    resume_retired,
    same_fields,
)

from pipy_harness.native.durable import orphan_control
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session_core import ReentrantSessionCall
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.task_values import identity

REFUSAL = "^Task has no observed failed migration for its current definition$"


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.close()


def authority(scheduler, task_id):
    def read():
        with scheduler._guard:
            binding = scheduler._definition_generation.bindings.get(
                identity("migration.work")
            )
            return binding, scheduler._migration_failures.get(task_id)

    return scheduler._session.read_on_line(read)


def failed(rig, monkeypatch, *, paused=True):
    task_id = rig.create(definition())
    calls = []
    source = definition(version=2, migrate=CallbackProbe(calls, failure=True))
    scheduler = owner(rig, [source])
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    if paused:
        pause(scheduler)
    binding, memo = authority(scheduler, task_id)
    assert binding is not None and memo is binding
    assert binding.captured.version == 2 and len(calls) == 1
    with scheduler._guard:
        assert scheduler._enabled is not paused
        assert not scheduler._draining and scheduler._invocation is None
    return task_id, source, scheduler, passes, calls, binding


def unexpected(*args, **kwargs):
    raise AssertionError("Explicit orphan action must not fit or invoke task code")


def test_paused_failure_settles_standard_terminal_and_retires_task_doc(
    rig, monkeypatch
):
    task_id, _, scheduler, _, calls, _ = failed(rig, monkeypatch)
    doc = define_doc("failed-doc", 1, "task", lambda: {"value": 1})

    def prepare(tx):
        tx.doc(doc, task_id=task_id)["value"] = 2
        row = tx.task(task_id).to_tree()
        tx.set_task({**row, "memos": {"saved": 3}, "restarts": 2})

    rig.session.commit(prepare)
    address = DocumentAddress(
        {"kind": "failed-doc", "scope": {"kind": "task", "taskId": task_id}}
    )
    document_id = rig.session.read_on_line(
        lambda: rig.storage.find_document(address).id
    )
    before = rig.record(task_id)
    count = len(rig.storage.commits)
    monkeypatch.setattr(TaskScheduler, "_fit", unexpected)
    monkeypatch.setattr(TaskScheduler, "_fitting_definition", unexpected)
    assert asyncio.run(scheduler.aorphan_failed_migration(task_id)) == "orphaned"
    after = rig.record(task_id)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "migration_failed"},
    }
    assert "memos" not in after and "restarts" not in after
    assert "endedAt" in after
    for key, value in before.items():
        if key not in {"state", "memos", "restarts", "endedAt"}:
            assert after[key] == value
    assert rig.session.read_on_line(lambda: rig.storage.document(document_id)) is None
    assert rig.session.read_on_line(lambda: rig.storage.find_document(address)) is None
    assert authority(scheduler, task_id)[1] is None
    assert len(rig.storage.commits) == count + 1 and len(calls) == 1
    silent = rig.silence()
    assert scheduler.orphan_failed_migration(task_id) == "terminal"
    rig.assert_silent(silent)


def test_actual_retired_enabled_failure_can_settle_without_another_callback(
    rig, monkeypatch
):
    task_id, _, scheduler, _, calls, _ = failed(rig, monkeypatch, paused=False)
    assert scheduler.orphan_failed_migration(task_id) == "orphaned"
    pause(scheduler)
    assert len(calls) == 1
    assert rig.record(task_id)["state"]["outcome"]["reason"] == "migration_failed"


@pytest.mark.parametrize("stored_version", [2, 3])
def test_strict_newer_relation_isolated_with_same_real_memo(
    rig, monkeypatch, stored_version
):
    task_id, _, scheduler, passes, calls, binding = failed(rig, monkeypatch)
    # Real pause and retirement precede the Tx update: equal-fit must not dispatch.
    before_passes = passes.checkpoint()
    rig.change(task_id, lambda row: row.update(version=stored_version))
    current, memo = authority(scheduler, task_id)
    assert current is binding and memo is binding
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
        scheduler.orphan_failed_migration(task_id)
    rig.assert_silent(before)
    assert authority(scheduler, task_id) == (binding, binding)
    assert passes.checkpoint() == before_passes and len(calls) == 1
    with scheduler._guard:
        assert not scheduler._enabled and not scheduler._draining
        assert scheduler._invocation is None


@pytest.mark.parametrize("available", ["missing", "no-migrator", "unobserved"])
def test_inspection_or_available_migrator_is_not_failure_authority(rig, available):
    task_id = rig.create(definition())
    calls = []
    declarations = (
        []
        if available == "missing"
        else [
            definition(
                version=2,
                migrate=CallbackProbe(calls, failure=True)
                if available == "unobserved"
                else None,
            )
        ]
    )
    scheduler = owner(rig, declarations)
    if available == "no-migrator":
        assert scheduler.inspect_task(task_id).reason == "migration_failed"
    assert authority(scheduler, task_id)[1] is None
    before = rig.silence()
    with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
        scheduler.orphan_failed_migration(task_id)
    rig.assert_silent(before)
    assert calls == []


@pytest.mark.parametrize(
    "replacement", ["same", "unrelated", "remove-reinstall", "distinct"]
)
def test_observed_authority_tracks_retained_source_binding(
    rig, monkeypatch, replacement
):
    task_id, source, scheduler, _, calls, binding = failed(rig, monkeypatch)
    if replacement == "same":
        assert scheduler.install_definition(source) is False
    elif replacement == "unrelated":
        assert scheduler.install_definition(definition("other")) is True
    elif replacement == "remove-reinstall":
        assert scheduler.remove_definition(source.name) is True
        assert scheduler.install_definition(source) is True
    else:
        assert scheduler.install_definition(same_fields(source)) is True
    current, memo = authority(scheduler, task_id)
    assert memo is binding
    if replacement == "distinct":
        assert current is not binding
        before = rig.silence()
        with pytest.raises(UnsupportedTaskExecution, match=REFUSAL):
            scheduler.orphan_failed_migration(task_id)
        rig.assert_silent(before)
    else:
        assert current is binding
        assert scheduler.orphan_failed_migration(task_id) == "orphaned"
    assert len(calls) == 1


def test_record_checkpoint_change_does_not_invent_a_failure_fingerprint(
    rig, monkeypatch
):
    task_id, _, scheduler, _, calls, binding = failed(rig, monkeypatch)
    rig.change(
        task_id,
        lambda row: row.update(
            input={"changed": True},
            state={
                "status": "pending",
                "checkpoint": {"phase": "start", "saved": "changed"},
            },
        ),
    )
    current, memo = authority(scheduler, task_id)
    assert current is binding and memo is binding
    assert scheduler.orphan_failed_migration(task_id) == "orphaned"
    assert rig.record(task_id)["input"] == {"changed": True} and len(calls) == 1


@pytest.mark.parametrize("name", ["migration.work", "pi.tool"])
def test_terminal_precedes_graph_and_binding_but_reserved_gate_stays_first(
    rig, monkeypatch, name
):
    task_id = rig.create(definition(name))
    rig.change(
        task_id,
        lambda row: row.update(
            state={
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        ),
    )
    scheduler = owner(rig, [])
    before = rig.silence()
    monkeypatch.setattr(orphan_control, "require_unmarked_root_leaf", unexpected)
    monkeypatch.setattr(orphan_control, "identity", unexpected)
    if name.startswith("pi."):
        with pytest.raises(UnsupportedTaskExecution, match="Built-in tasks"):
            scheduler.orphan_failed_migration(task_id)
    else:
        assert scheduler.orphan_failed_migration(task_id) == "terminal"
    rig.assert_silent(before)


@pytest.mark.parametrize("value", [True, 1.0, 0, 2**53])
def test_new_methods_retain_safe_id_before_unopened_lifecycle(rig, value):
    scheduler = TaskScheduler(rig.session, [])
    rig.scheduler = scheduler
    before = rig.silence()
    with pytest.raises(RecordError):
        scheduler.orphan_failed_migration(value)
    with pytest.raises(RecordError):
        asyncio.run(scheduler.aorphan_failed_migration(value))
    with pytest.raises(RuntimeError, match="Open the scheduler"):
        scheduler.orphan_failed_migration(1)
    rig.assert_silent(before)


def test_reentry_offloop_and_unknown_id(rig):
    scheduler = owner(rig, [])
    before = rig.silence()
    with pytest.raises(ReentrantSessionCall):
        rig.session.read_on_line(lambda: scheduler.orphan_failed_migration(True))

    async def call():
        with pytest.raises(RuntimeError, match="aorphan_failed_migration"):
            scheduler.orphan_failed_migration(True)
        with pytest.raises(ValueError, match="Unknown task"):
            await scheduler.aorphan_failed_migration(999999)

    asyncio.run(call())
    rig.assert_silent(before)
