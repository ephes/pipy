"""Host-only restart policy and actual Session-line scheduler controls."""

import asyncio
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_task_runtime import create
from test_task_runtime import rig as rig

from pipy_harness.native.automation.durable_args import DurableCLIError, parse_args
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.restart_policy import RestartPolicy
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session import SessionClosedError
from pipy_harness.native.durable.tasks import define_task


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 5),
        (True, 5),
        ("Infinity", 5),
        (math.nan, 5),
        (-math.inf, 5),
        (math.inf, None),
        (0, 5),
        (-2, 5),
        (1.9, 1),
        (5.5, 5),
        (2**53 + 1, 5),
        (float(2**53 + 1), 2**53),
        (10**5000, 5),
        (2**1000, 2**1000),
        (sys.float_info.max, math.floor(sys.float_info.max)),
    ],
    ids=[
        "none",
        "bool",
        "text",
        "nan",
        "negative-inf",
        "unlimited",
        "zero",
        "negative",
        "floor-one",
        "floor-five",
        "nonexact-int",
        "float",
        "huge-int",
        "large-exact",
        "max-float",
    ],
)
def test_host_numeric_domain(value, expected):
    assert RestartPolicy(value).limit == expected


def test_subclasses_do_not_execute_conversion():
    class Foreign(int):
        def __float__(self):
            raise AssertionError("No foreign numeric conversion")

    assert RestartPolicy(Foreign(10)).limit == 5


def command(value):
    return parse_args(["inspect", "--store", "unused", "--restart-limit", value])


@pytest.mark.parametrize(
    "value", ["NaN", "nan", "inf", "+Infinity", "-Infinity", "1e400", "01", "+5", ".5"]
)
def test_cli_policy_rejects_non_json_or_overflow(value):
    with pytest.raises(DurableCLIError) as error:
        command(value)
    assert error.value.code == "invalid_request"


def test_cli_binary64_rounding_is_distinct_from_raw_sdk_integer():
    assert RestartPolicy(command("9007199254740993").restart_limit).limit == 2**53
    assert RestartPolicy(2**53 + 1).limit == 5
    assert RestartPolicy(float(2**53 + 1)).limit == 2**53
    assert RestartPolicy(command("Infinity").restart_limit).limit is None
    assert RestartPolicy(command("2.9e1").restart_limit).limit == 29


def definition(called):
    async def start(task, runtime, context):
        await runtime.commit(
            lambda tx, row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )
        called.set()

    async def abort(*args):
        raise AssertionError("DE14a does not activate abort")

    return define_task(
        "restart-test", 1, lambda _: {"phase": "start"}, {"start": start}, abort
    )


def set_count(session, task_id, count, *, running=False):
    def change(tx):
        row = tx.task(task_id).to_tree()
        row["restarts"] = count
        if running:
            row["state"]["status"] = "running"
        tx.set_task(row)

    session.commit(change)


@pytest.mark.parametrize("count", [5, 7.5, 2**53, 1e300])
def test_parking_and_same_task_reset_on_actual_line(rig, count):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, count)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    before = session.read_on_line(lambda: storage.task(task_id).to_tree())
    scheduler.resume()
    asyncio.run(scheduler.apause())
    assert not called.is_set()
    assert session.read_on_line(lambda: storage.task(task_id).to_tree()) == before
    metadata = inspect_task(session, task_id)
    assert metadata.restart_parked and metadata.restart_count == count
    assert metadata.restart_limit == 5 and metadata.reason == "restart_limit"
    assert scheduler.retry(task_id) is True
    assert called.wait(5)
    assert scheduler.inspect()[0].id == task_id
    assert "restarts" not in scheduler.inspect()[0].to_tree()


def test_private_retry_unopened_refuses_before_counter_write(rig):
    session, storage = rig
    task = definition(threading.Event())
    task_id = create(session, task)
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [task])
    before = len(storage.commits)
    with pytest.raises(RuntimeError, match="Open the scheduler"):
        scheduler.retry(task_id)
    assert len(storage.commits) == before
    assert (
        session.read_on_line(lambda: storage.task(task_id).to_tree()["restarts"]) == 5
    )


def test_private_successful_noop_enables_and_unknown_does_not(rig):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    with pytest.raises(ValueError, match="Unknown task"):
        scheduler.retry(task_id + 100)
    assert not called.is_set()
    assert scheduler.retry(task_id) is False
    assert called.wait(5)
    session.close(timeout=5)
    with pytest.raises(SessionClosedError):
        scheduler.retry(task_id)


def test_recovery_increments_once_and_higher_fresh_policy_admits(rig):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 4, running=True)
    scheduler = TaskScheduler(session, [task], restart_limit=6)
    scheduler.open()
    metadata = inspect_task(session, task_id, restart_limit=6)
    assert metadata.restart_count == 5 and not metadata.restart_parked
    assert not called.is_set()
    scheduler.resume()
    assert called.wait(5)


@pytest.mark.parametrize("after_commit", [False, True], ids=["rollback", "ack-loss"])
def test_reset_backend_failure_never_enables_and_actual_commit_wins(
    rig, monkeypatch, after_commit
):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    actual = storage.commit
    faults = []

    def fail(writes):
        faults.append(True)
        if after_commit:
            actual(writes)
        raise RuntimeError("reset backend failure")

    monkeypatch.setattr(storage, "commit", fail)
    with pytest.raises(RuntimeError):
        scheduler.retry(task_id)
    end = session.wait_closed(timeout=5)
    assert end.reason == "failed" and faults == [True]
    assert not called.is_set() and not scheduler._thread.is_alive()
    actual_row = next(row.to_tree() for row in storage.latest if row.id == task_id)
    assert ("restarts" not in actual_row) == after_commit


def test_reset_commit_held_close_keeps_physical_owner_until_commit_retires(
    rig, monkeypatch
):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    entered, release = threading.Event(), threading.Event()
    actual = storage.commit

    def hold(writes):
        entered.set()
        assert release.wait(5)
        return actual(writes)

    monkeypatch.setattr(storage, "commit", hold)
    with ThreadPoolExecutor() as pool:
        reset = pool.submit(scheduler.retry, task_id)
        try:
            assert entered.wait(5)
            session.request_close()
            assert session.end is None and scheduler._thread.is_alive()
            assert not reset.done() and not called.is_set()
        finally:
            release.set()
        assert reset.result(5) is True
    session.wait_closed(timeout=5)
    assert not scheduler._thread.is_alive() and not called.is_set()
    row = next(row.to_tree() for row in storage.latest if row.id == task_id)
    assert row["state"]["status"] == "pending" and "restarts" not in row


def test_cancelled_retry_waiter_does_not_cancel_admitted_reset(rig, monkeypatch):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    entered, release = threading.Event(), threading.Event()
    actual = storage.commit

    def hold(writes):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return actual(writes)

    monkeypatch.setattr(storage, "commit", hold)

    async def scenario():
        waiting = asyncio.create_task(scheduler.aretry(task_id))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not called.is_set()
        finally:
            release.set()
        assert await asyncio.to_thread(called.wait, 5)

    asyncio.run(scenario())
    assert scheduler.inspect()[0].to_tree()["state"]["status"] == "terminal"
