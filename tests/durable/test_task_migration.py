"""Cold generic migration through real Session and scheduler admission paths."""

import asyncio
import json
import threading

import pytest
from task_migration_support import Rig, definition, observe_reservation, pause

from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.inspection_values import InspectionSupport
from pipy_harness.native.durable.scheduler import TaskScheduler


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "PIPY_NATIVE_THEME_PATH",
    ):
        monkeypatch.setenv(name, str(tmp_path / name))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.close()


def own(rig, definitions):
    scheduler = TaskScheduler(rig.session, definitions)
    rig.scheduler = scheduler
    scheduler.open()
    return scheduler


def report_pair(rig, task_id, definitions):
    ordinary = inspect_task(
        rig.session, task_id, support=InspectionSupport(tuple(definitions))
    )
    owner = rig.scheduler.inspect_task(task_id)
    assert ordinary is not None and owner is not None
    assert len(ordinary.to_tree()) == len(owner.to_tree()) == 17
    return ordinary, owner


@pytest.mark.parametrize(
    ("stored", "current", "capability", "compatibility", "reason"),
    [
        (0.5, None, False, "unsupported", "definition_missing"),
        (2, 1, False, "unsupported", "stored_version_newer"),
        (-1.5, -1.5, True, "supported", None),
        (1, 2, False, "unsupported", "migration_failed"),
        (1, 2, True, "unknown", "migration_required"),
    ],
)
def test_exact_generic_inspection_table_never_calls_migration(
    rig, stored, current, capability, compatibility, reason
):
    calls = []

    def migrate(*_args):
        calls.append("inspection executed code")
        raise AssertionError("Inspection is not migration")

    task_id = rig.create(definition(version=stored))
    definitions = (
        []
        if current is None
        else [definition(version=current, migrate=migrate if capability else None)]
    )
    own(rig, definitions)
    before = rig.silence()
    for report in report_pair(rig, task_id, definitions):
        assert (report.compatibility, report.reason) == (compatibility, reason)
    asynchronous = asyncio.run(rig.scheduler.ainspect_task(task_id))
    assert (asynchronous.compatibility, asynchronous.reason) == (
        compatibility,
        reason,
    )
    assert calls == []
    rig.assert_silent(before)


@pytest.mark.parametrize("status", ["terminal", "completing"])
def test_ended_inspection_does_not_consult_definition_or_migrate(rig, status):
    task_id = rig.create(definition())
    rig.change(
        task_id,
        lambda row: row.update(
            state={"status": status, "outcome": {"status": "completed", "result": 1}}
        ),
    )
    calls = []

    def migrate(*_args):
        calls.append("bad")
        raise AssertionError("An ended record does not migrate")

    definitions = [definition(version=2, migrate=migrate)]
    own(rig, definitions)
    before = rig.silence()
    for report in report_pair(rig, task_id, definitions):
        assert report.reason is None
        assert report.compatibility == (
            "terminal_readonly" if status == "terminal" else "unknown"
        )
    assert calls == []
    rig.assert_silent(before)


def test_failure_is_owner_local_and_inspection_never_retries_or_leaks(rig):
    calls = []
    task_id = rig.create(definition(), {"private-input-marker": [1]})

    def migrate(value, checkpoint, from_version):
        calls.append(from_version)
        value["private-input-marker"].append(99)
        checkpoint["private-checkpoint-marker"] = True
        raise RuntimeError("private-exception-marker")

    definitions = [definition(version=2, migrate=migrate)]
    scheduler = own(rig, definitions)
    completed, _, _ = observe_reservation(scheduler)
    original = rig.record(task_id)
    before = rig.silence()
    scheduler.resume()
    assert completed.wait(5)
    pause(scheduler)
    assert calls == [1]
    for _ in range(3):
        ordinary, owner = report_pair(rig, task_id, definitions)
        assert (ordinary.compatibility, ordinary.reason) == (
            "unknown",
            "migration_required",
        )
        assert (owner.compatibility, owner.reason) == (
            "unsupported",
            "migration_failed",
        )
        shown = json.dumps(owner.to_tree())
        assert "private-exception-marker" not in shown
        assert "private-input-marker" not in shown
        assert "private-checkpoint-marker" not in shown
        completed.clear()
        scheduler.resume()
        assert completed.wait(5)
        pause(scheduler)
    assert rig.record(task_id) == original
    assert rig.session.failure is None
    assert calls == [1]
    rig.assert_silent(before)


def test_failing_first_candidate_admits_second_in_same_actual_transaction(rig):
    calls, completed = [], threading.Event()
    failed = rig.create(definition(name="migration.first"))
    second = rig.create(definition(name="migration.second"))
    rig.change(failed, lambda row: row.update(restarts=3, memos={"key": "saved"}))
    original = rig.record(failed)

    def migrate(*_args):
        calls.append("failed")
        raise RuntimeError("ordinary migration failure")

    async def finish(_task, runtime, _context):
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": "second"},
            }
        )
        completed.set()

    scheduler = own(
        rig,
        [
            definition(name="migration.first", version=2, migrate=migrate),
            definition(name="migration.second", phase=finish),
        ],
    )
    _, visits, passes = observe_reservation(scheduler)
    scheduler.resume()
    assert completed.wait(5)
    pause(scheduler)
    first_visits = [visit for visit in visits if visit[0] == 1]
    assert [visit[2] for visit in first_visits] == [failed, second]
    assert len({visit[1] for visit in first_visits}) == 1
    assert passes[0] == second
    assert calls == ["failed"]
    assert rig.record(failed) == original
    assert rig.record(second)["state"]["outcome"] == {
        "status": "completed",
        "result": "second",
    }
    assert rig.session.failure is None


@pytest.mark.parametrize("stored,current", [(-2, -1.5), (-0.0, 0), (0.5, 1.5)])
def test_numeric_versions_and_same_content_preserve_memos_and_restart_streak(
    rig, stored, current
):
    calls, admitted = [], threading.Event()
    task_id = rig.create(definition(version=stored))
    rig.change(task_id, lambda row: row.update(restarts=3, memos={"key": None}))
    original = rig.record(task_id)
    observed = []

    def migrate(value, checkpoint, from_version):
        calls.append(from_version)
        return {"input": value, "checkpoint": checkpoint}

    async def phase(task, _runtime, context):
        observed.append(task)
        admitted.set()
        await context.signal.wait()

    scheduler = own(rig, [definition(version=current, migrate=migrate, phase=phase)])
    scheduler.resume()
    assert admitted.wait(5)
    assert calls == ([] if stored == current else [stored])
    actual = observed[0]
    for name in ("id", "kind", "input", "conversationId", "background", "memos"):
        assert actual[name] == original[name]
    assert actual["version"] == current and actual["restarts"] == 3
    assert actual["state"]["checkpoint"] == original["state"]["checkpoint"]


@pytest.mark.parametrize("committed", [False, True], ids=["rollback", "ack-loss"])
def test_backend_failure_is_not_migration_failure_or_handler_ack(rig, committed):
    calls, faults, handlers = [], [], []
    task_id = rig.create(definition())
    rig.change(task_id, lambda row: row.update(restarts=3, memos={"key": "stable"}))
    original = rig.record(task_id)

    def migrate(value, checkpoint, from_version):
        calls.append(from_version)
        return {"input": {"new": value}, "checkpoint": checkpoint}

    async def forbidden(*_args):
        handlers.append(True)
        raise AssertionError("A failed acknowledgement never dispatches")

    scheduler = own(rig, [definition(version=2, migrate=migrate, phase=forbidden)])

    def fail(_writes):
        faults.append(True)
        raise RuntimeError("actual migration storage failure")

    if committed:
        rig.storage.after_commit = fail
    else:
        rig.storage.before_commit = fail
    scheduler.resume()
    end = rig.session.wait_closed(timeout=5)
    assert end.reason == "failed"
    assert faults == [True] and calls == [1] and handlers == []
    assert type(rig.session.failure) is RuntimeError
    assert str(rig.session.failure) == "actual migration storage failure"
    assert not scheduler._thread.is_alive() and rig.storage.closed.is_set()
    row = next(r.to_tree() for r in rig.storage.final_tasks if r.id == task_id)
    assert row["restarts"] == 3 and row["memos"] == {"key": "stable"}
    if committed:
        assert row["version"] == 2
        assert row["input"] == {"new": original["input"]}
        assert row["state"]["status"] == "running"
    else:
        assert row == original


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_failure_memory_ends_with_owner_and_fresh_owner_restores(rig):
    from task_migration_support import ObservedStorage

    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    calls, admitted = [], threading.Event()
    task_id = rig.create(definition())

    def failing(*_args):
        calls.append("first-owner")
        raise ValueError("cannot migrate yet")

    scheduler = own(rig, [definition(version=2, migrate=failing)])
    finished, _, _ = observe_reservation(scheduler)
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    original = rig.record(task_id)
    rig.close()

    def fitting(value, checkpoint, from_version):
        calls.append(("second-owner", from_version))
        return {"input": value, "checkpoint": checkpoint}

    async def phase(task, _runtime, context):
        assert task["version"] == 2
        admitted.set()
        await context.signal.wait()

    storage = ObservedStorage(open_sqlite_storage(str(rig.path), mode="open"))
    session = Session(storage)
    owner = TaskScheduler(
        session, [definition(version=2, migrate=fitting, phase=phase)]
    )
    try:
        owner.open()
        assert calls == ["first-owner"] and not admitted.is_set()
        assert session.read_on_line(lambda: storage.task(task_id).to_tree()) == original
        owner.resume()
        assert admitted.wait(5)
        assert calls == ["first-owner", ("second-owner", 1)]
    finally:
        session.close(timeout=5)
    assert storage.closed.is_set() and not owner._thread.is_alive()


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("held", ["callback", "backend"])
def test_close_retains_actual_migration_worker_and_sqlite_lease(rig, tmp_path, held):
    from task_migration_support import opener

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    calls, handlers = [], []
    task_id = rig.create(definition())

    def barrier():
        entered.set()
        try:
            assert release.wait(10)
        finally:
            exited.set()

    def migrate(value, checkpoint, from_version):
        calls.append(from_version)
        if held == "callback":
            barrier()
        return {"input": value, "checkpoint": checkpoint}

    async def forbidden(*_args):
        handlers.append(True)
        raise AssertionError("No late handler after close")

    scheduler = own(rig, [definition(version=2, migrate=migrate, phase=forbidden)])
    if held == "backend":
        rig.storage.before_commit = lambda _writes: barrier()
    scheduler.resume()
    try:
        assert entered.wait(5)
        rig.session.request_close()

        async def cancel_close_waiter():
            waiter = asyncio.create_task(rig.session.aclose())
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

        asyncio.run(cancel_close_waiter())
        assert rig.session.end is None and not rig.storage.closed.is_set()
        assert not exited.is_set() and scheduler._thread.is_alive()
        opener(rig.path, tmp_path, held + "-held", "refused")
        assert rig.session.end is None and not exited.is_set()
    finally:
        release.set()
        rig.storage.before_commit = None
        rig.session.close(timeout=5)
    assert exited.is_set() and calls == [1] and handlers == []
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    opener(rig.path, tmp_path, held + "-closed", "opened")
    saved = next(r.to_tree() for r in rig.storage.final_tasks if r.id == task_id)
    assert saved["state"]["status"] == "pending"


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "extra",
        "dict-subclass",
        "nonfinite",
        "tuple",
        "awaitable",
        "coroutine",
    ],
)
def test_invalid_migration_results_are_once_only_and_durably_silent(rig, invalid):
    import inspect

    calls, foreign_calls, coroutines = [], [], []
    task_id = rig.create(definition())
    original = rig.record(task_id)

    class Foreign:
        def __await__(self):
            foreign_calls.append("await")
            raise AssertionError("A foreign awaitable must not execute")

    class ForeignDict(dict):
        pass

    async def forbidden_coroutine():
        foreign_calls.append("coroutine-body")
        return {"input": None, "checkpoint": {"phase": "start"}}

    def migrate(value, checkpoint, _version):
        calls.append(True)
        if invalid == "coroutine":
            result = forbidden_coroutine()
            coroutines.append(result)
            return result
        return {
            "missing": {"input": value},
            "extra": {"input": value, "checkpoint": checkpoint, "extra": True},
            "dict-subclass": ForeignDict(input=value, checkpoint=checkpoint),
            "nonfinite": {"input": float("nan"), "checkpoint": checkpoint},
            "tuple": {"input": (1, 2), "checkpoint": checkpoint},
            "awaitable": Foreign(),
        }[invalid]

    scheduler = own(rig, [definition(version=2, migrate=migrate)])
    finished, _, _ = observe_reservation(scheduler)
    before = rig.silence()
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert rig.scheduler.inspect_task(task_id).reason == "migration_failed"
    assert rig.record(task_id) == original and rig.session.failure is None
    assert calls == [True] and foreign_calls == []
    assert all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED for c in coroutines)
    rig.assert_silent(before)


def test_parked_then_explicit_reset_migrates_without_inventing_old_streak(rig):
    calls, admitted, records = [], threading.Event(), []
    task_id = rig.create(definition())
    rig.change(task_id, lambda row: row.update(restarts=5, memos={"stable": [1]}))

    def migrate(value, checkpoint, version):
        calls.append(version)
        return {"input": value, "checkpoint": checkpoint}

    async def phase(task, _runtime, context):
        records.append(task)
        admitted.set()
        await context.signal.wait()

    definitions = [definition(version=2, migrate=migrate, phase=phase)]
    scheduler = own(rig, definitions)
    finished, _, _ = observe_reservation(scheduler)
    before = rig.silence()
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    report = scheduler.inspect_task(task_id)
    assert report.restart_parked is True and report.restart_count == 5
    assert report.reason == "migration_required"
    assert calls == [] and not admitted.is_set()
    rig.assert_silent(before)
    scheduler.retry(task_id)
    assert admitted.wait(5)
    assert calls == [1]
    assert "restarts" not in records[0]
    assert records[0]["memos"] == {"stable": [1]}


def test_counter_reset_does_not_repeat_failed_captured_definition(rig):
    calls = []
    task_id = rig.create(definition())
    rig.change(task_id, lambda row: row.update(restarts=3))

    def failing(*_args):
        calls.append(True)
        raise RuntimeError("migration cannot proceed")

    scheduler = own(rig, [definition(version=2, migrate=failing)])
    finished, _, _ = observe_reservation(scheduler)
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [True]
    finished.clear()
    assert scheduler.retry(task_id) is False
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [True] and rig.record(task_id)["restarts"] == 3
    # Typed counter setup, not a historical producer: now actually parked.
    rig.change(task_id, lambda row: row.update(restarts=5))
    finished.clear()
    assert scheduler.retry(task_id) is True
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [True]
    assert "restarts" not in rig.record(task_id)
    before = rig.silence()
    assert scheduler.inspect_task(task_id).reason == "migration_failed"
    rig.assert_silent(before)


def test_deep_unicode_owned_arguments_and_result_aliases_stay_detached(rig):
    import math

    from pipy_harness.native.durable.codec import OwnedJson

    deep = None
    for _ in range(1500):
        deep = [deep]
    source = {"deep": deep, "\ud800": "\udc00", "zero": -0.0}
    expected = OwnedJson(source)
    task_id = rig.create(definition(), source)
    aliases, entered, records = [], threading.Event(), []

    def migrate(value, checkpoint, version):
        assert version == 1 and OwnedJson(value) == expected
        aliases.append((value, checkpoint))
        return {"input": value, "checkpoint": checkpoint}

    async def phase(task, _runtime, context):
        records.append(task)
        entered.set()
        await context.signal.wait()

    scheduler = own(rig, [definition(version=2, migrate=migrate, phase=phase)])
    scheduler.resume()
    assert entered.wait(5)
    assert len(aliases) == 1
    aliases[0][0].clear()
    aliases[0][1]["saved"].append("late caller mutation")
    source.clear()
    actual = rig.record(task_id)
    assert OwnedJson(actual["input"]) == expected
    assert math.copysign(1, actual["input"]["zero"]) == -1
    assert actual["state"]["checkpoint"] == {"phase": "start", "saved": [1, None]}
    assert OwnedJson(records[0]["input"]) == expected


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_sqlite_silence_oracle_detects_restored_payload_revisions(rig):
    from pipy_harness.native.durable.documents import define_doc

    doc = define_doc("migration.oracle", 1, "session", lambda: {"stable": True})
    rig.session.commit(lambda tx: tx.doc(doc))
    before = rig.snapshot()

    def edit(tx, restore):
        value = tx.doc(doc)
        if restore:
            del value["temporary"]
        else:
            value["temporary"] = True

    rig.session.commit(lambda tx: edit(tx, False))
    rig.session.commit(lambda tx: edit(tx, True))
    after = rig.snapshot()
    for table in ("tasks", "entries", "conversations", "documents"):
        assert before[table] == after[table]
    assert before["document_revisions"] != after["document_revisions"]
    assert before != after


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_actual_sigkill_reservation_recovery_and_explicit_resume(rig, tmp_path, cut):
    import hashlib
    import signal
    from pathlib import Path

    from sqlite_child_support import Child, group_exists
    from task_migration_support import MIGRATION_CUT_CHILD, opener

    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    path = tmp_path / "killed-migration.sqlite"
    with Child(MIGRATION_CUT_CHILD, str(path), cut, home=tmp_path) as child:
        task_id = child.expect("ready")["task_id"]
        child.send("go")
        observed = child.expect("cut")
        assert observed["point"] == cut and observed["migration_calls"] == [1]
        source = Path(__file__).resolve().parents[2] / "src"
        for item in observed["origins"].values():
            file = Path(item["path"])
            assert file.is_relative_to(source)
            assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
        opener(path, tmp_path, cut + "-before-kill", "refused")
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        evidence = child.evidence()
    evidence.update(
        argv=child.process.args,
        reaped=child.process.returncode is not None,
        scoped_group_absent=not group_exists(child.group),
    )
    (tmp_path / ("migration-kill-" + cut + ".json")).write_text(
        json.dumps({"observed": observed, "process": evidence}, indent=2)
    )
    assert not Path(str(path) + ".effects").exists()
    opener(path, tmp_path, cut + "-after-kill", "opened")
    migrations, effects, done = [], [], threading.Event()

    def migrate(value, checkpoint, version):
        migrations.append(version)
        return {"input": {"new": value}, "checkpoint": checkpoint}

    async def phase(task, runtime, _context):
        effects.append(task["input"])
        await runtime.commit(
            lambda _tx, _current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": "once"},
            }
        )
        done.set()

    storage = open_sqlite_storage(str(path), mode="open")
    session = Session(storage)
    owner = TaskScheduler(
        session, [definition(version=2, migrate=migrate, phase=phase)]
    )
    try:
        persisted = storage.task(task_id).to_tree()
        assert persisted["version"] == (1 if cut == "before" else 2)
        assert persisted["state"]["status"] == (
            "pending" if cut == "before" else "running"
        )
        owner.open()
        recovered = storage.task(task_id).to_tree()
        assert recovered["restarts"] == (2 if cut == "before" else 3)
        assert recovered["memos"] == {"key": "retained"}
        assert migrations == effects == []
        owner.resume()
        assert done.wait(5)
        assert migrations == ([1] if cut == "before" else [])
        assert effects == [{"new": {"old": [1]}}]
    finally:
        session.close(timeout=5)
    assert not owner._thread.is_alive()
    opener(path, tmp_path, cut + "-recovered-closed", "opened")


@pytest.mark.parametrize("with_second", [False, True])
def test_real_reconciliation_commits_around_failed_generic_candidate(
    rig, tmp_path, with_second
):
    from generation_support import admit
    from tool_support import counts, definitions

    generation, tool, snapshot = definitions(tmp_path, replay="safe")
    _, _, parent = admit(rig.session, generation, snapshot)
    calls, terminal_children, waiting_states = [], set(), []
    before_reconcile, release, finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    second_done = threading.Event()

    def migrate(*_args):
        calls.append("failed")
        raise ValueError("ordinary generic failure")

    async def second_phase(_task, runtime, _context):
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": "second"},
            }
        )
        second_done.set()

    scheduler = own(
        rig,
        [
            generation,
            tool,
            definition(name="migration.first", version=2, migrate=migrate),
            definition(name="migration.second", phase=second_phase),
        ],
    )

    from task_migration_support import terminal_tool_watcher

    unsubscribe = rig.session.subscribe_commits(
        terminal_tool_watcher(terminal_children, waiting_states, parent)
    )
    original = scheduler._reserve
    held_once = []

    async def reserve():
        target = len(terminal_children) == 2 and not held_once
        if target:
            held_once.append(True)
            before_reconcile.set()
            assert await asyncio.to_thread(release.wait, 10)
        try:
            return await original()
        finally:
            if target:
                finished.set()

    scheduler._reserve = reserve
    scheduler.resume()
    try:
        assert before_reconcile.wait(10)
        from concurrent.futures import ThreadPoolExecutor

        from task_migration_support import assert_only_parent_seed

        assert rig.record(parent)["state"]["status"] == "pending"
        with ThreadPoolExecutor() as pool:
            paused = pool.submit(pause, scheduler)
            with scheduler._guard:
                assert scheduler._guard.wait_for(lambda: not scheduler._enabled, 5)
            release.set()
            paused.result(5)
        with scheduler._guard:
            assert scheduler._invocation is None and not scheduler._draining
        assert waiting_states
        produced = rig.record(parent)
        assert produced["state"]["checkpoint"] == waiting_states[-1]["checkpoint"]
        before_seed = rig.snapshot()
        # Explicit kernel control over the actual produced graph: not a
        # naturally outstanding reconciliation or a crash provenance claim.
        rig.change(parent, lambda row: row.update(state=waiting_states[-1], restarts=5))
        assert rig.record(parent) == {
            **produced,
            "state": waiting_states[-1],
            "restarts": 5,
        }
        assert_only_parent_seed(rig, before_seed, rig.snapshot(), parent)
        scheduler._reserve = original
        finished, visits, passes = observe_reservation(scheduler)
        failed = rig.create(definition(name="migration.first"))
        failed_before = rig.record(failed)
        second = (
            rig.create(definition(name="migration.second")) if with_second else None
        )
        effect_before = counts(tmp_path)
        scheduler.resume()
        assert finished.wait(10)
        if with_second:
            assert second_done.wait(5)
        pause(scheduler)
        assert calls == ["failed"] and rig.session.failure is None
        target_visits = [visit for visit in visits if visit[0] == 1]
        assert failed in [visit[2] for visit in target_visits]
        assert len({visit[1] for visit in target_visits}) == 1
        assert passes[0] == second
        assert rig.record(failed) == failed_before
        actual_parent = rig.record(parent)
        assert actual_parent["state"]["status"] == "pending"
        assert actual_parent["restarts"] == 5
        assert counts(tmp_path) == effect_before
        if with_second:
            assert rig.record(second)["state"]["outcome"]["result"] == "second"
    finally:
        release.set()
        unsubscribe()


@pytest.mark.parametrize("current", [1, 2])
def test_old_phase_is_not_checked_against_new_definition_before_migration(rig, current):
    calls = []
    task_id = rig.create(definition())
    rig.change(
        task_id,
        lambda row: row.update(
            state={"status": "pending", "checkpoint": {"phase": "old-unsupported"}}
        ),
    )

    def migrate(*_args):
        calls.append(True)
        raise AssertionError("Metadata must not execute")

    definitions = [definition(version=current, migrate=migrate)]
    own(rig, definitions)
    before = rig.silence()
    ordinary_without_support = inspect_task(rig.session, task_id)
    assert (
        ordinary_without_support.compatibility,
        ordinary_without_support.reason,
    ) == ("unknown", None)
    for report in report_pair(rig, task_id, definitions):
        assert (report.compatibility, report.reason) == (
            ("unsupported", "unsupported_phase")
            if current == 1
            else ("unknown", "migration_required")
        )
    assert calls == []
    rig.assert_silent(before)
