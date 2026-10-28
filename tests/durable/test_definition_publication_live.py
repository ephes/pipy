"""Dual-backend definition publication through real owner/Session admission."""

import asyncio
import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from definition_publication_live_support import (
    CallbackProbe,
    DefinitionHold,
    Passes,
    Rig,
    Tickets,
    Wakes,
    assert_binding,
    definition,
    generation,
    held_line,
    held_real_drain,
    idle,
    opener,
    owner,
    pause,
    resume_retired,
    same_fields,
    successful_waking_install,
)
from task_migration_support import sqlite_rows
from test_resolution import setup as resolution_setup
from tool_support import counts

from pipy_harness.native.durable import resolution
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session import (
    SessionClosedError,
    SessionFailed,
)
from pipy_harness.native.durable.session_core import ReentrantSessionCall
from pipy_harness.native.durable.task_values import identity


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_paused_missing_install_remove_is_silent_and_never_initializes(
    rig, monkeypatch
):
    calls = []
    initials = []

    async def phase(row, runtime, _context):
        calls.append(row["id"])
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    task = definition("definition.missing", phase=phase)
    original_initial = task.initial

    def initial(value):
        initials.append(value)
        return original_initial(value)

    task = replace(task, initial=initial)
    task_id = rig.create(task)
    scheduler = owner(rig, [])
    before = rig.silence()
    record = rig.record(task_id)
    old_dto = scheduler.inspect_task(task_id)
    old_tree = old_dto.to_tree()
    passes = Passes(scheduler, monkeypatch)
    wakes = Wakes(scheduler, monkeypatch)
    assert old_dto.reason == "definition_missing"
    assert scheduler.install_definition(task) is True
    assert scheduler.install_definition(task) is False
    assert scheduler.remove_definition("not.installed") is False
    assert scheduler.inspect_task(task_id).compatibility == "supported"
    assert scheduler.remove_definition(task.name) is True
    assert scheduler.inspect_task(task_id).reason == "definition_missing"
    assert scheduler.install_definition(task) is True
    assert old_dto.to_tree() == old_tree
    assert calls == [] and wakes.checkpoint() == 0 and len(initials) == 1
    assert rig.record(task_id) == record
    rig.assert_silent(before)
    resume_retired(scheduler, passes)
    pause(scheduler)
    assert calls == [task_id]


@pytest.mark.parametrize("first", ["publication", "inspection"])
def test_owner_inspection_reads_one_actual_definition_generation(
    rig, monkeypatch, first
):
    task = definition("definition.inspected")
    task_id = rig.create(task)
    scheduler = owner(rig, [])
    old_dto = scheduler.inspect_task(task_id)
    old_tree = old_dto.to_tree()
    tickets = Tickets(rig.session, monkeypatch)
    before = rig.silence()
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            if first == "publication":
                update = pool.submit(scheduler.install_definition, task)
                tickets.wait(1)
                read = pool.submit(scheduler.inspect_task, task_id)
            else:
                read = pool.submit(scheduler.inspect_task, task_id)
                tickets.wait(1)
                update = pool.submit(scheduler.install_definition, task)
            tickets.wait(2)
        assert update.result(5) is True
        observed = read.result(5)
    assert observed.compatibility == (
        "supported" if first == "publication" else "unsupported"
    )
    assert observed.reason == (None if first == "publication" else "definition_missing")
    assert scheduler.inspect_task(task_id).compatibility == "supported"
    assert old_dto.to_tree() == old_tree
    rig.assert_silent(before)


def test_unrelated_and_replacement_keep_unchanged_capture_and_old_snapshots(rig):
    calls = []
    callback = CallbackProbe(calls)
    first = definition("definition.first", migrate=callback)
    other = definition("definition.other")
    scheduler = owner(rig, [first, other])
    initial = generation(scheduler)
    captured = assert_binding(initial, first.name, first)
    old_support = initial.support.definition(first.name)
    before = rig.silence()
    assert scheduler.install_definition(definition("definition.third")) is True
    assert_binding(generation(scheduler), first.name, first, captured)
    replacement = same_fields(first)
    assert scheduler.install_definition(replacement) is True
    current = generation(scheduler)
    assert_binding(current, first.name, replacement)
    assert current.definitions[identity(first.name)] is not captured
    assert list(current.bindings) == [
        identity(first.name),
        identity(other.name),
        identity("definition.third"),
    ]
    assert initial.support.definition(first.name) == old_support
    assert_binding(initial, first.name, first, captured)
    assert calls == []
    rig.assert_silent(before)


def test_utf16_equivalent_name_replaces_position_without_alias_or_callback_equality(
    rig,
):
    calls = []
    probe = CallbackProbe(calls)
    first = definition("\U0001f600", version=-1.5, migrate=probe)
    later = definition("ordered.later")
    scheduler = owner(rig, [first, later])
    replacement = definition("\ud83d\ude00", version=-1.5, migrate=probe)
    assert scheduler.install_definition(replacement) is True
    snapshot = generation(scheduler)
    assert list(snapshot.bindings) == [identity(first.name), identity(later.name)]
    assert_binding(snapshot, first.name, replacement)
    assert calls == []


def test_two_paused_install_tickets_do_not_lose_distinct_updates(rig, monkeypatch):
    baseline = definition("definition.base")
    one, two = definition("definition.one"), definition("definition.two")
    scheduler = owner(rig, [baseline])
    captured = assert_binding(generation(scheduler), baseline.name, baseline)
    tickets = Tickets(rig.session, monkeypatch)
    before = rig.silence()
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            first = pool.submit(scheduler.install_definition, one)
            tickets.wait(1)
            second = pool.submit(scheduler.install_definition, two)
            order = tickets.wait(2)
            assert order[0][0] != order[1][0]
        assert first.result(5) is True
        assert second.result(5) is True
    snapshot = generation(scheduler)
    assert list(snapshot.bindings) == list(
        map(identity, [baseline.name, one.name, two.name])
    )
    assert_binding(snapshot, baseline.name, baseline, captured)
    assert_binding(snapshot, one.name, one)
    assert_binding(snapshot, two.name, two)
    rig.assert_silent(before)


@pytest.mark.parametrize("first", ["replace", "remove"])
def test_paused_replace_remove_uses_generation_current_on_each_ticket(
    rig, monkeypatch, first
):
    old = definition("definition.order")
    replacement = definition(old.name)
    scheduler = owner(rig, [old])
    tickets = Tickets(rig.session, monkeypatch)
    operations = {
        "replace": lambda: scheduler.install_definition(replacement),
        "remove": lambda: scheduler.remove_definition(old.name),
    }
    second = "remove" if first == "replace" else "replace"
    before = rig.silence()
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            one = pool.submit(operations[first])
            tickets.wait(1)
            two = pool.submit(operations[second])
            tickets.wait(2)
        assert one.result(5) is True and two.result(5) is True
    snapshot = generation(scheduler)
    if second == "remove":
        assert identity(old.name) not in snapshot.bindings
    else:
        assert_binding(snapshot, old.name, replacement)
    rig.assert_silent(before)


def test_enabled_retired_failure_identical_unrelated_and_readd_really_ack_and_wake(
    rig, monkeypatch
):
    calls = []
    old = definition("definition.failed", version=1)
    task_id = rig.create(old)
    failed = definition(old.name, version=2, migrate=CallbackProbe(calls, failure=True))
    scheduler = owner(rig, [failed])
    passes = Passes(scheduler, monkeypatch)
    wakes = Wakes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    assert len(calls) == 1
    assert scheduler.inspect_task(task_id).reason == "migration_failed"
    captured = assert_binding(generation(scheduler), failed.name, failed)
    successful_waking_install(scheduler, failed, False, passes, wakes)
    assert len(calls) == 1
    unrelated = definition("definition.unrelated")
    successful_waking_install(scheduler, unrelated, True, passes, wakes)
    assert_binding(generation(scheduler), failed.name, failed, captured)
    assert len(calls) == 1
    no_wake = wakes.checkpoint()
    assert scheduler.remove_definition("definition.absent") is False
    assert wakes.checkpoint() == no_wake
    before = passes.checkpoint()
    assert scheduler.remove_definition(failed.name) is True
    assert wakes.checkpoint() == no_wake + 1
    passes.retired_after(before)
    successful_waking_install(scheduler, failed, True, passes, wakes)
    assert_binding(generation(scheduler), failed.name, failed, captured)
    assert len(calls) == 1
    assert scheduler.inspect_task(task_id).reason == "migration_failed"
    pause(scheduler)


def test_distinct_source_same_fields_retries_once_and_old_memo_is_bounded(
    rig, monkeypatch
):
    calls = []
    old = definition("definition.single.memo", version=1)
    task_id = rig.create(old)
    callback = CallbackProbe(calls, failure=True)
    first = definition(old.name, version=2, migrate=callback)
    scheduler = owner(rig, [first])
    passes = Passes(scheduler, monkeypatch)
    wakes = Wakes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    assert len(calls) == 1
    second = same_fields(first)
    successful_waking_install(scheduler, second, True, passes, wakes)
    assert len(calls) == 2
    successful_waking_install(scheduler, second, False, passes, wakes)
    assert len(calls) == 2
    successful_waking_install(scheduler, first, True, passes, wakes)
    assert len(calls) == 3
    assert scheduler.inspect_task(task_id).reason == "migration_failed"
    pause(scheduler)


@pytest.mark.parametrize("retire", ["terminal", "owner"])
def test_removed_failed_callback_lives_only_through_real_memo_then_disposes(
    rig, monkeypatch, retire
):
    calls, drops = [], []
    old = definition("definition.retention", version=1)
    task_id = rig.create(old)
    scheduler = owner(rig, [])
    probe = CallbackProbe(calls, failure=True, drops=drops, guard=scheduler._guard)
    weak = weakref.ref(probe)
    task = definition(old.name, version=2, migrate=probe)
    assert scheduler.install_definition(task) is True
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    assert len(calls) == 1
    assert scheduler.remove_definition(task.name) is True
    del task, probe
    gc.collect()
    assert weak() is not None and drops == []
    if retire == "terminal":
        # Labeled typed kernel terminal publication, never a fake handler result.
        rig.change(
            task_id,
            lambda row: row.update(
                state={
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": None},
                }
            ),
        )
    else:
        rig.session.close(timeout=5)
        assert not scheduler._thread.is_alive()
    gc.collect()
    assert weak() is None
    assert drops == [(False, "no_session")]


def test_removed_nonfailed_callback_disposal_is_outside_condition_and_reentry_finite(
    rig,
):
    drops = []
    scheduler = owner(rig, [])
    probe = CallbackProbe([], drops=drops, session=rig.session, guard=scheduler._guard)
    weak = weakref.ref(probe)
    task = definition("definition.drop", migrate=probe)
    assert scheduler.install_definition(task) is True
    del task, probe
    assert scheduler.remove_definition("definition.drop") is True
    gc.collect()
    assert weak() is None
    assert len(drops) == 1 and drops[0][0] is False
    assert drops[0][1] in {"ReentrantSessionCall", "read_returned"}


def test_new_actual_failure_releases_old_removed_source_memo_outside_condition(
    rig, monkeypatch
):
    first_calls, second_calls, drops = [], [], []
    old = definition("definition.overwrite", version=1)
    task_id = rig.create(old)
    scheduler = owner(rig, [])
    first_probe = CallbackProbe(
        first_calls, failure=True, drops=drops, guard=scheduler._guard
    )
    weak = weakref.ref(first_probe)
    first = definition(old.name, version=2, migrate=first_probe)
    assert scheduler.install_definition(first) is True
    passes = Passes(scheduler, monkeypatch)
    resume_retired(scheduler, passes)
    pause(scheduler)
    assert len(first_calls) == 1
    assert scheduler.remove_definition(first.name) is True
    del first, first_probe
    gc.collect()
    assert weak() is not None and drops == []
    second = definition(
        old.name, version=2, migrate=CallbackProbe(second_calls, failure=True)
    )
    assert scheduler.install_definition(second) is True
    resume_retired(scheduler, passes)
    pause(scheduler)
    gc.collect()
    assert len(first_calls) == len(second_calls) == 1
    assert weak() is None and drops == [(False, "no_session")]
    assert scheduler.inspect_task(task_id).reason == "migration_failed"


def test_reflected_known_snapshot_refuses_without_changing_capture_or_store(rig):
    task = definition("definition.known")
    scheduler = owner(rig, [task])
    captured = assert_binding(generation(scheduler), task.name, task)
    before = rig.silence()
    object.__setattr__(task, "version", 2)
    try:
        with pytest.raises(ValueError):
            scheduler.install_definition(task)
        assert_binding(generation(scheduler), task.name, task, captured)
    finally:
        object.__setattr__(task, "version", 1)
    assert scheduler.install_definition(task) is False
    rig.assert_silent(before)


def test_async_queued_capture_is_owned_before_source_mutation(rig, monkeypatch):
    scheduler = owner(rig, [])
    incoming = definition("definition.early", version=2)
    tickets = Tickets(rig.session, monkeypatch)
    before = rig.silence()

    async def scenario():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with held_line(rig.session, pool):
                waiter = asyncio.create_task(scheduler.ainstall_definition(incoming))
                await asyncio.to_thread(tickets.wait, 1)
                object.__setattr__(incoming, "version", 3)
            assert await waiter is True

    try:
        asyncio.run(scenario())
        assert generation(scheduler).definitions[identity(incoming.name)].version == 2
        with pytest.raises(ValueError):
            scheduler.install_definition(incoming)
    finally:
        object.__setattr__(incoming, "version", 2)
    assert scheduler.install_definition(incoming) is False
    rig.assert_silent(before)


@pytest.mark.parametrize("lifecycle", ["unopened", "closed", "failed"])
def test_early_invalid_input_precedes_line_lifecycle_but_valid_input_does_not(
    rig, lifecycle
):
    task = definition("definition.lifecycle")
    scheduler = TaskScheduler(rig.session, [task])
    rig.scheduler = scheduler
    expected = RuntimeError
    if lifecycle != "unopened":
        scheduler.open()
        if lifecycle == "failed":
            rig.session.fail(RuntimeError("private owner failure"))
            expected = SessionFailed
        else:
            rig.session.close(timeout=5)
            expected = SessionClosedError
    with pytest.raises(TypeError):
        scheduler.install_definition(object())
    with pytest.raises(TypeError):
        scheduler.remove_definition(object())
    for reserved in ("pi.generation", "pi.tool", "pi.compaction"):
        with pytest.raises(ValueError):
            scheduler.remove_definition(reserved)
        with pytest.raises(ValueError):
            scheduler.install_definition(definition(reserved))
    object.__setattr__(task, "version", 2)
    try:
        with pytest.raises(expected):
            scheduler.install_definition(task)
        with pytest.raises(expected):
            scheduler.remove_definition("definition.absent")
    finally:
        object.__setattr__(task, "version", 1)


def test_real_line_free_drain_busy_refusal_is_separate_from_successful_install(
    rig, monkeypatch
):
    scheduler = owner(rig, [])
    incoming = definition("definition.busy")
    before = rig.silence()
    with held_real_drain(scheduler, monkeypatch):
        with pytest.raises(RuntimeError):
            scheduler.install_definition(incoming)
        with pytest.raises(RuntimeError):
            scheduler.remove_definition("definition.absent")
    assert identity(incoming.name) not in generation(scheduler).bindings
    rig.assert_silent(before)
    assert scheduler.install_definition(incoming) is True


def test_real_migrator_holds_line_and_publication_is_only_decided_after_release(
    rig, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def migrate(input_value, checkpoint, _version):
        calls.append((input_value, checkpoint))
        entered.set()
        assert release.wait(10)
        raise ValueError("private actual migration failure")

    old = definition("definition.queued", version=1)
    task_id = rig.create(old)
    failed = definition(old.name, version=2, migrate=migrate)
    incoming = definition("definition.after.migrator")
    scheduler = owner(rig, [failed])
    initial = generation(scheduler)
    passes = Passes(scheduler, monkeypatch)
    tickets = Tickets(rig.session, monkeypatch)
    before_pass = passes.checkpoint()
    scheduler.resume()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert entered.wait(5)
            pending = pool.submit(scheduler.install_definition, incoming)
            tickets.wait(1)
            with scheduler._guard:
                assert scheduler._definition_generation is initial
                assert scheduler._invocation is None
        finally:
            release.set()
        try:
            changed = pending.result(5)
        except RuntimeError as error:
            # Distinct from the deterministic successful enabled-owner test:
            # this real queued request may meet a still-admitted drain.
            assert type(error) is RuntimeError
            assert str(error) == "Task scheduler is busy"
            accepted = False
        else:
            assert changed is True
            accepted = True
    passes.retired_after(before_pass)
    pause(scheduler)
    assert (identity(incoming.name) in generation(scheduler).bindings) is accepted
    assert len(calls) == 1 and rig.session.failure is None
    assert scheduler.inspect_task(task_id).reason == "migration_failed"


def test_active_real_invocation_cannot_be_replaced_then_retirement_allows_install(rig):
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def phase(row, runtime, _context):
        calls.append(row["id"])
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        await runtime.commit(
            lambda _tx, _row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    task = definition("definition.active", phase=phase)
    task_id = rig.create(task)
    scheduler = owner(rig, [task])
    scheduler.resume()
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError):
            scheduler.install_definition(definition(task.name, version=2))
    finally:
        release.set()
        pause(scheduler)
    assert calls == [task_id]
    assert scheduler.install_definition(definition(task.name, version=2)) is True


def test_same_line_reentry_and_sync_off_loop_precede_invalid_capture(rig):
    scheduler = owner(rig, [])

    def reentrant():
        with pytest.raises(ReentrantSessionCall):
            scheduler.install_definition(object())
        with pytest.raises(ReentrantSessionCall):
            scheduler.remove_definition(object())

    rig.session.read_on_line(reentrant)

    async def scenario():
        async def change(_tx):
            with pytest.raises(ReentrantSessionCall):
                await scheduler.ainstall_definition(object())
            with pytest.raises(ReentrantSessionCall):
                await scheduler.aremove_definition(object())

        await rig.session.acommit(change)
        with pytest.raises(RuntimeError):
            scheduler.install_definition(object())
        with pytest.raises(RuntimeError):
            scheduler.remove_definition(object())

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["before", "after"])
def test_cancelled_async_install_joins_actual_job_without_rolling_back(
    rig, monkeypatch, stage
):
    scheduler = owner(rig, [])
    incoming = definition("definition.cancelled")
    hold = DefinitionHold(rig.session, monkeypatch, stage)
    before = rig.silence()

    async def scenario():
        waiter = asyncio.create_task(scheduler.ainstall_definition(incoming))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            assert not hold.retired.is_set()
            assert not rig.storage.closed.is_set()
        finally:
            hold.release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await asyncio.to_thread(hold.retired.wait, 5)
        assert hold.results == [True] and hold.errors == []

    asyncio.run(scenario())
    assert_binding(generation(scheduler), incoming.name, incoming)
    rig.assert_silent(before)


def test_cancelled_queued_install_joins_the_real_ticket_before_caller_completion(
    rig, monkeypatch
):
    scheduler = owner(rig, [])
    incoming = definition("definition.cancelled.queue")
    tickets = Tickets(rig.session, monkeypatch)
    original = generation(scheduler)
    before = rig.silence()

    async def scenario():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with held_line(rig.session, pool):
                waiter = asyncio.create_task(scheduler.ainstall_definition(incoming))
                await asyncio.to_thread(tickets.wait, 1)
                waiter.cancel()
                with scheduler._guard:
                    assert scheduler._definition_generation is original
            with pytest.raises(asyncio.CancelledError):
                await waiter

    asyncio.run(scenario())
    assert_binding(generation(scheduler), incoming.name, incoming)
    rig.assert_silent(before)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_close_joins_definition_job_and_holds_real_sqlite_lease(
    rig, monkeypatch, tmp_path, stage
):
    scheduler = owner(rig, [])
    incoming = definition("definition.closing")
    hold = DefinitionHold(rig.session, monkeypatch, stage)
    snapshots = []

    async def scenario():
        waiter = asyncio.create_task(scheduler.ainstall_definition(incoming))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            with scheduler._guard:
                snapshots.append(scheduler._definition_generation)
            rig.session.request_close()
            assert rig.session.end is None
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            if rig.backend == "sqlite":
                opener(rig.path, tmp_path, "definition-held-" + stage, "refused")
        finally:
            hold.release.set()
        if stage == "before":
            with pytest.raises(SessionClosedError):
                await waiter
            assert hold.results == [] and hold.errors == [SessionClosedError]
        else:
            assert await waiter is True
            assert hold.results == [True] and hold.errors == []
        assert await asyncio.to_thread(hold.retired.wait, 5)

    asyncio.run(scenario())
    rig.session.wait_closed(timeout=5)
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    installed = identity(incoming.name) in snapshots[0].bindings
    assert installed is (stage == "after")
    if rig.backend == "sqlite":
        opener(rig.path, tmp_path, "definition-retired-" + stage, "opened")


def test_caller_loop_teardown_and_close_join_published_job_before_sqlite_release(
    rig, monkeypatch, tmp_path
):
    scheduler = owner(rig, [])
    incoming = definition("definition.loop.retirement")
    hold = DefinitionHold(rig.session, monkeypatch, "after")
    cancelled, loop_exited = threading.Event(), threading.Event()
    snapshots = []

    def caller():
        async def scenario():
            waiter = asyncio.create_task(scheduler.ainstall_definition(incoming))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiter.cancel()
            cancelled.set()

        try:
            asyncio.run(scenario())
        finally:
            loop_exited.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(caller)
        try:
            assert cancelled.wait(5)
            with scheduler._guard:
                snapshots.append(scheduler._definition_generation)
            rig.session.request_close()
            assert rig.session.end is None
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert not loop_exited.is_set()
            if rig.backend == "sqlite":
                opener(rig.path, tmp_path, "definition-loop-held", "refused")
        finally:
            hold.release.set()
        work.result(10)
    rig.session.wait_closed(timeout=5)
    assert loop_exited.is_set() and hold.retired.is_set()
    assert hold.results == [True] and hold.errors == []
    assert not scheduler._thread.is_alive() and rig.storage.closed.is_set()
    assert_binding(snapshots[0], incoming.name, incoming)
    if rig.backend == "sqlite":
        opener(rig.path, tmp_path, "definition-loop-retired", "opened")


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_resolution_consumes_generation_after_real_publication_ticket_and_winner_first(
    tmp_path, monkeypatch, backend
):
    session, scheduler, _, _, child, _ = resolution_setup(tmp_path, backend)
    marker = definition("definition.resolution.marker")
    seen = []
    original = resolution._support

    def observed(actual):
        value = original(actual)
        seen.append(value.definition(marker.name))
        return value

    monkeypatch.setattr(resolution, "_support", observed)
    try:
        pause(scheduler)
        idle(scheduler)
        target = scheduler.resolution_target(child["id"])
        assert generation(scheduler).support.definition(marker.name) is None
        tickets = Tickets(session, monkeypatch)
        before_counts = counts(tmp_path)
        with ThreadPoolExecutor(max_workers=3) as pool:
            with held_line(session, pool):
                publication = pool.submit(scheduler.install_definition, marker)
                tickets.wait(1)
                resolving = pool.submit(scheduler.resolve, target, nonce="b" * 32)
                order = tickets.wait(2)
                assert order[0][0] != order[1][0]
                # Old resolve captured OLD support before queuing this second
                # real ticket; new resolve must not capture until change owns it.
                assert seen == []
            assert publication.result(5) is True
            result = resolving.result(10)
        assert result.source_task_id == child["id"]
        assert result.new_task_id != child["id"]
        assert seen and all(value is not None for value in seen)
        assert counts(tmp_path) == before_counts
        assert scheduler.remove_definition(marker.name) is True
        before = (
            sqlite_rows(tmp_path / "session.sqlite") if backend == "sqlite" else None
        )

        def forbidden(_actual):
            raise AssertionError("Saved winner must precede current catalog capture")

        monkeypatch.setattr(resolution, "_support", forbidden)
        replay = scheduler.resolve(target, nonce="b" * 32)
        assert replay.source_task_id == result.source_task_id
        assert replay.new_task_id == result.new_task_id
        assert counts(tmp_path) == before_counts
        if backend == "sqlite":
            assert sqlite_rows(tmp_path / "session.sqlite") == before
    finally:
        session.close(timeout=10)
        assert not scheduler._thread.is_alive()
