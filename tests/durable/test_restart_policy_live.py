"""Live policy publication uses real Session ordering and physical ownership."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from restart_policy_live_support import (
    PublicationHold,
    Rig,
    Tickets,
    completing,
    definition,
    held_line,
    idle,
    opener,
    owner,
    pass_finished,
    pause,
    seed_count,
)

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.restart_policy import RestartPolicy
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session import SessionClosedError, SessionFailed
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("new_limit", [6, float("inf")], ids=["finite", "infinite"])
def test_paused_publication_is_write_free_and_explicit_resume_uses_new_budget(
    rig, monkeypatch, new_limit
):
    calls = []
    task = completing(calls)
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    old_record = rig.record(task_id)
    notifications = []
    original = scheduler._notify
    monkeypatch.setattr(scheduler, "_notify", lambda: notifications.append(True))
    before = rig.silence()
    assert scheduler.inspect_task(task_id).restart_parked
    assert scheduler.set_restart_limit(new_limit) is True
    assert scheduler.set_restart_limit(new_limit) is False
    report = scheduler.inspect_task(task_id)
    assert not report.restart_parked
    assert report.restart_limit == (6 if new_limit == 6 else None)
    assert report.restart_count == 5
    assert inspect_task(rig.session, task_id).restart_limit == 5
    with scheduler._guard:
        assert not scheduler._enabled
    assert notifications == [] and calls == []
    rig.assert_silent(before)
    assert rig.record(task_id) == old_record
    monkeypatch.setattr(scheduler, "_notify", original)
    finished = pass_finished(scheduler, monkeypatch)
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [(task_id, 5)]
    assert rig.record(task_id)["state"]["status"] == "terminal"


def test_enabled_idle_owner_does_not_wake_on_setter(rig, monkeypatch):
    calls = []
    task = completing(calls)
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    finished = pass_finished(scheduler, monkeypatch)
    scheduler.resume()
    assert finished.wait(5)
    idle(scheduler)
    notifications = []
    original = scheduler._notify
    monkeypatch.setattr(scheduler, "_notify", lambda: notifications.append(True))
    before = rig.silence()
    assert scheduler.set_restart_limit(6) is True
    assert scheduler.set_restart_limit(6) is False
    with scheduler._guard:
        assert scheduler._enabled and not scheduler._draining
    assert calls == notifications == []
    rig.assert_silent(before)
    monkeypatch.setattr(scheduler, "_notify", original)
    finished.clear()
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [(task_id, 5)]


def test_sqlite_oracle_detects_revision_only_write(tmp_path):
    rig = Rig("sqlite", tmp_path)
    doc = define_doc("live.policy.oracle", 1, "session", lambda: {"stable": True})
    try:
        rig.session.commit(lambda tx: tx.doc(doc))
        before = rig.snapshot()

        def update(tx, restore):
            draft = tx.doc(doc)
            draft["stable"] = restore

        rig.session.commit(lambda tx: update(tx, False))
        rig.session.commit(lambda tx: update(tx, True))
        after = rig.snapshot()
        assert before["documents"] == after["documents"]
        assert before["document_revisions"] != after["document_revisions"]
        assert before != after
    finally:
        rig.close()


@pytest.mark.parametrize("first", ["publication", "reservation"])
def test_actual_session_ticket_order_decides_next_admission(rig, monkeypatch, first):
    calls = []
    task = completing(calls)
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    finished = pass_finished(scheduler, monkeypatch)
    tickets = Tickets(rig.session, monkeypatch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            if first == "publication":
                update = pool.submit(scheduler.set_restart_limit, 6)
                tickets.wait(1)
                scheduler.resume()
            else:
                scheduler.resume()
                tickets.wait(1)
                update = pool.submit(scheduler.set_restart_limit, 6)
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0]
        assert update.result(5) is True
        assert finished.wait(5)
        pause(scheduler)
    assert calls == ([(task_id, 5)] if first == "publication" else [])
    if first == "reservation":
        assert rig.record(task_id)["state"]["status"] == "pending"
        finished.clear()
        scheduler.resume()
        assert finished.wait(5)
        pause(scheduler)
        assert calls == [(task_id, 5)]


@pytest.mark.parametrize("first", ["publication", "retry"])
def test_retry_uses_policy_at_its_actual_ticket(rig, monkeypatch, first):
    calls = []
    entered = threading.Event()
    task = completing(calls, entered=entered)
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    tickets = Tickets(rig.session, monkeypatch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            if first == "publication":
                update = pool.submit(scheduler.set_restart_limit, 6)
                tickets.wait(1)
                retried = pool.submit(scheduler.retry, task_id)
            else:
                retried = pool.submit(scheduler.retry, task_id)
                tickets.wait(1)
                update = pool.submit(scheduler.set_restart_limit, 6)
            tickets.wait(2)
        assert update.result(5) is True
        assert retried.result(5) is (first == "retry")
        assert entered.wait(5)
        pause(scheduler)
    assert calls == [(task_id, None if first == "retry" else 5)]


@pytest.mark.parametrize("first", ["publication", "inspection"])
def test_owner_inspection_observes_one_real_policy_epoch(rig, monkeypatch, first):
    task = definition()
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    tickets = Tickets(rig.session, monkeypatch)
    before = rig.silence()
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            if first == "publication":
                update = pool.submit(scheduler.set_restart_limit, 6)
                tickets.wait(1)
                read = pool.submit(scheduler.inspect_task, task_id)
            else:
                read = pool.submit(scheduler.inspect_task, task_id)
                tickets.wait(1)
                update = pool.submit(scheduler.set_restart_limit, 6)
            tickets.wait(2)
        assert update.result(5) is True
        report = read.result(5)
    assert report.restart_count == 5
    assert (report.restart_limit, report.restart_parked) == (
        (6, False) if first == "publication" else (5, True)
    )
    assert scheduler.inspect_task(task_id).restart_limit == 6
    rig.assert_silent(before)


def test_lowering_does_not_revoke_active_invocation_and_controls_next(rig, monkeypatch):
    calls = []
    entered, release = threading.Event(), threading.Event()
    first = completing(calls, "live.first", release=release, entered=entered)
    second = completing(calls, "live.second")
    one, two = rig.create(first), rig.create(second)
    seed_count(rig, two, 3)
    scheduler = owner(rig, [first, second])
    finished = pass_finished(scheduler, monkeypatch)
    scheduler.resume()
    try:
        assert entered.wait(5)
        before = rig.silence()
        assert scheduler.set_restart_limit(1) is True
        assert rig.record(one)["state"]["status"] == "running"
        assert calls == [(one, None)]
        rig.assert_silent(before)
        finished.clear()
    finally:
        release.set()
    assert finished.wait(5)
    idle(scheduler)
    pause(scheduler)
    assert calls == [(one, None)]
    assert rig.record(two)["state"]["status"] == "pending"
    assert scheduler.inspect_task(two).restart_parked
    assert scheduler.set_restart_limit(4) is True
    finished.clear()
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert calls == [(one, None), (two, 3)]


def test_policy_change_does_not_repeat_owner_failed_migration(rig, monkeypatch):
    calls = []
    old = definition(version=1)
    task_id = rig.create(old)

    def migrate(*_args):
        calls.append("actual-failed-migrator")
        raise RuntimeError("private migration failure")

    scheduler = owner(rig, [definition(version=2, migrate=migrate)])
    finished = pass_finished(scheduler, monkeypatch)
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    before = rig.silence()
    assert scheduler.set_restart_limit(float("inf")) is True
    assert scheduler.set_restart_limit(float("inf")) is False
    assert scheduler.inspect_task(task_id).reason == "migration_failed"
    rig.assert_silent(before)
    finished.clear()
    scheduler.resume()
    assert finished.wait(5)
    pause(scheduler)
    assert calls == ["actual-failed-migrator"]
    rig.assert_silent(before)


def test_both_setters_refuse_same_line_reentry_before_publication(rig):
    task = definition()
    task_id = rig.create(task)
    scheduler = owner(rig, [task])
    before = rig.silence()

    def synchronous():
        with pytest.raises(ReentrantSessionCall):
            scheduler.set_restart_limit(9)

    rig.session.read_on_line(synchronous)

    async def scenario():
        async def change(_tx):
            with pytest.raises(ReentrantSessionCall):
                await scheduler.aset_restart_limit(9)

        await rig.session.acommit(change)
        with pytest.raises(RuntimeError, match="aset_restart_limit"):
            scheduler.set_restart_limit(9)

    asyncio.run(scenario())
    assert scheduler.inspect_task(task_id).restart_limit == 5
    rig.assert_silent(before)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_cancelled_setter_joins_actual_line_worker_without_rollback(
    rig, monkeypatch, stage
):
    task = definition()
    task_id = rig.create(task)
    scheduler = owner(rig, [task])
    hold = PublicationHold(rig.session, monkeypatch, stage)
    before = rig.silence()

    async def scenario():
        waiting = asyncio.create_task(scheduler.aset_restart_limit(9))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiting.cancel()
            assert not hold.retired.is_set()
            assert not rig.storage.closed.is_set()
        finally:
            hold.release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await asyncio.to_thread(hold.retired.wait, 5)
        assert hold.actual_results == [True] and hold.actual_errors == []
        assert await scheduler.aset_restart_limit(9) is False

    asyncio.run(scenario())
    assert scheduler.inspect_task(task_id).restart_limit == 9
    rig.assert_silent(before)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_close_joins_accepted_publication_and_keeps_actual_lease(
    rig, monkeypatch, tmp_path, stage
):
    scheduler = owner(rig, [])
    hold = PublicationHold(rig.session, monkeypatch, stage)

    async def scenario():
        waiting = asyncio.create_task(scheduler.aset_restart_limit(9))
        try:
            assert await asyncio.to_thread(hold.entered.wait, 5)
            rig.session.request_close()
            assert rig.session.end is None
            assert not hold.retired.is_set()
            assert not rig.storage.closed.is_set()
            if rig.backend == "sqlite":
                opener(rig.path, tmp_path, "live-held-" + stage, "refused")
        finally:
            hold.release.set()
        if stage == "before":
            with pytest.raises(SessionClosedError):
                await waiting
            assert hold.actual_results == []
            assert hold.actual_errors == [SessionClosedError]
        else:
            assert await waiting is True
            assert hold.actual_results == [True] and hold.actual_errors == []
        assert await asyncio.to_thread(hold.retired.wait, 5)

    asyncio.run(scenario())
    rig.session.wait_closed(timeout=5)
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    with scheduler._guard:
        assert scheduler._restart_policy.limit == (5 if stage == "before" else 9)
    if rig.backend == "sqlite":
        opener(rig.path, tmp_path, "live-retired-" + stage, "opened")


def test_failed_owner_precedes_closed_refusal_without_policy_change(rig):
    scheduler = owner(rig, [])
    failure = RuntimeError("private first failure")
    rig.session.fail(failure)
    with pytest.raises(SessionFailed):
        scheduler.set_restart_limit(9)
    with scheduler._guard:
        assert scheduler._restart_policy.limit == 5


def test_fresh_sqlite_owner_uses_its_constructor_not_a_persisted_policy(tmp_path):
    rig = Rig("sqlite", tmp_path)
    calls = []
    task = completing(calls)
    task_id = rig.create(task)
    seed_count(rig, task_id, 5)
    scheduler = owner(rig, [task])
    original = rig.record(task_id)
    try:
        before = rig.silence()
        assert scheduler.set_restart_limit(9) is True
        assert scheduler.inspect_task(task_id).restart_limit == 9
        rig.assert_silent(before)
    finally:
        rig.close()
    session = Session(open_sqlite_storage(str(rig.path), mode="open"))
    fresh = None
    try:
        fresh = TaskScheduler(session, [task])
        fresh.open()
        owner_view = fresh.inspect_task(task_id)
        assert owner_view.restart_limit == 5 and owner_view.restart_parked
        assert (
            session.read_on_line(lambda: session._owner.storage.task(task_id).to_tree())
            == original
        )
        assert calls == []
    finally:
        session.close(timeout=5)
    assert fresh is not None and not fresh._thread.is_alive()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(0, 5, id="zero-default"),
        pytest.param(True, 5, id="boolean-default"),
        pytest.param(-1, 5, id="negative-default"),
        pytest.param(float("nan"), 5, id="nan-default"),
        pytest.param(6.9, 6, id="fraction-floor"),
        pytest.param(float("inf"), None, id="positive-infinity"),
        pytest.param(10**5000, 5, id="arbitrary-integer-default"),
        pytest.param(2**53 + 1, 5, id="nonbinary64-integer-default"),
        pytest.param(2**53, 2**53, id="exact-binary64-integer"),
        pytest.param(RestartPolicy(8), 8, id="exact-policy"),
    ],
)
def test_live_normalizer_matches_existing_host_domain_without_writes(
    rig, value, expected
):
    task = definition()
    task_id = rig.create(task)
    scheduler = owner(rig, [task], limit=7)
    before = rig.silence()
    assert scheduler.set_restart_limit(value) is True
    assert scheduler.set_restart_limit(value) is False
    assert scheduler.inspect_task(task_id).restart_limit == expected
    rig.assert_silent(before)


def test_hostile_values_never_execute_user_conversion_or_equality(rig):
    invoked = []

    def forbidden(*_args):
        invoked.append("user conversion")
        raise AssertionError("Not part of the constructor domain")

    class Foreign:
        __float__ = __int__ = __index__ = __eq__ = forbidden

    class ForeignInt(int):
        __float__ = __int__ = __index__ = __eq__ = forbidden

    class ForeignFloat(float):
        __float__ = __int__ = __index__ = __eq__ = forbidden

    class ForeignPolicy(RestartPolicy):
        pass

    task = definition()
    task_id = rig.create(task)
    scheduler = owner(rig, [task], limit=7)
    before = rig.silence()
    for index, value in enumerate(
        (Foreign(), ForeignInt(8), ForeignFloat(8), ForeignPolicy(8))
    ):
        assert scheduler.set_restart_limit(value) is (index == 0)
        assert scheduler.inspect_task(task_id).restart_limit == 5
    assert invoked == []
    rig.assert_silent(before)


def test_entire_multi_candidate_pass_precedes_queued_publication(rig, monkeypatch):
    calls = []
    one = completing(calls, "live.pass.one")
    two = completing(calls, "live.pass.two")
    first, second = rig.create(one), rig.create(two)
    for task_id in (first, second):
        seed_count(rig, task_id, 5)
    scheduler = owner(rig, [one, two])
    entered, release = threading.Event(), threading.Event()
    original = scheduler._candidate
    visits = []

    async def candidate(tx, row, *args):
        visits.append((id(tx), row["id"]))
        if row["id"] == first and not entered.is_set():
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
        return await original(tx, row, *args)

    monkeypatch.setattr(scheduler, "_candidate", candidate)
    tickets = Tickets(rig.session, monkeypatch)
    finished = pass_finished(scheduler, monkeypatch)
    scheduler.resume()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert entered.wait(5)
            update = pool.submit(scheduler.set_restart_limit, 6)
            tickets.wait(1)
            assert calls == []
        finally:
            release.set()
        assert update.result(5) is True
    assert finished.wait(5)
    idle(scheduler)
    pause(scheduler)
    assert calls == []
    assert len(visits) == 2 and visits[0][0] == visits[1][0]
    assert {task_id for _, task_id in visits} == {first, second}
    assert all(rig.record(task_id)["restarts"] == 5 for task_id in (first, second))


def test_loop_teardown_and_close_retain_real_publication_worker_and_lease(
    rig, monkeypatch, tmp_path
):
    scheduler = owner(rig, [])
    hold = PublicationHold(rig.session, monkeypatch, "after")
    cancelled, loop_exited = threading.Event(), threading.Event()

    def caller():
        async def scenario():
            waiting = asyncio.create_task(scheduler.aset_restart_limit(9))
            assert await asyncio.to_thread(hold.entered.wait, 5)
            waiting.cancel()
            cancelled.set()

        try:
            asyncio.run(scenario())
        finally:
            loop_exited.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        invocation = pool.submit(caller)
        try:
            assert cancelled.wait(5)
            rig.session.request_close()
            assert rig.session.end is None
            assert not rig.storage.closed.is_set() and not hold.retired.is_set()
            if rig.backend == "sqlite":
                opener(rig.path, tmp_path, "live-loop-held", "refused")
        finally:
            hold.release.set()
        invocation.result(10)
    rig.session.wait_closed(timeout=5)
    assert loop_exited.is_set() and hold.retired.is_set()
    assert hold.actual_results == [True] and hold.actual_errors == []
    assert rig.storage.closed.is_set() and not scheduler._thread.is_alive()
    with scheduler._guard:
        assert scheduler._restart_policy.limit == 9
    if rig.backend == "sqlite":
        opener(rig.path, tmp_path, "live-loop-retired", "opened")
