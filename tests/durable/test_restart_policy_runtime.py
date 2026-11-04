"""Host-only restart policy and actual Session-line scheduler controls."""

import asyncio
import math
import sys
import threading
import time

import pytest
from test_task_runtime import create
from test_task_runtime import rig as rig

from pipy_harness.native.automation.durable_args import DurableCLIError, parse_args
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.restart_policy import RestartPolicy
from pipy_harness.native.durable.scheduler import TaskScheduler
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


def empty_pass(scheduler):
    """Set once an actual reservation pass returns without an invocation."""
    done = threading.Event()
    original = scheduler._reserve

    async def observed():
        result = await original()
        if result is None:
            done.set()
        return result

    scheduler._reserve = observed
    return done


@pytest.mark.parametrize(
    ("count", "said"),
    [(5, "5"), (7.5, "7.5"), (2**53, "9007199254740992"), (1e300, "1e+300")],
)
def test_limit_faults_a_generic_task_on_actual_line_without_dispatch(rig, count, said):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, count)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    metadata = inspect_task(session, task_id)
    # The limit is no hold reason: the task is plain pending until reserved.
    assert metadata.restart_count == count and metadata.restart_limit == 5
    assert metadata.reason is None and metadata.status == "pending"
    done = empty_pass(scheduler)
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    assert not called.is_set()
    row = session.read_on_line(lambda: storage.task(task_id).to_tree())
    assert row["state"] == {
        "status": "terminal",
        "outcome": {
            "status": "faulted",
            "error": {
                "message": f"Task {task_id} (restart-test) was cut off {said} times"
                " without progress",
                "detail": {"reason": "restart_limit"},
            },
        },
    }
    assert "restarts" not in row
    # An ended task stays ended: a higher limit later does not revive it.
    assert scheduler.set_restart_limit(float("inf")) is True
    done.clear()
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    assert not called.is_set()
    assert session.read_on_line(lambda: storage.task(task_id).to_tree()) == row


def test_limit_fault_holds_an_owner_with_live_ordinary_owned_work(rig):
    """A faulted owner holds completing while its ordinary owned work is live.

    The fault is cancellation intent, so the owned child waits for its abort
    cascade, which DE16 owns; it never runs here.
    """
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    parent = create(session, task)

    def child_of(tx):
        return tx.create_task(
            task,
            {"child": True},
            ownership={"kind": "task", "taskId": parent},
        )

    child = session.commit(child_of)
    set_count(session, parent, 5)
    # The child is at the limit too, but work below an owner with cancellation
    # intent waits for its cascade instead of ending (upstream drops it from
    # the reservation before the limit check).
    set_count(session, child, 5)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    done = empty_pass(scheduler)
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    owner = session.read_on_line(lambda: storage.task(parent).to_tree())
    assert owner["state"]["status"] == "completing" and "restarts" not in owner
    assert owner["state"]["outcome"]["status"] == "faulted"
    assert owner["state"]["outcome"]["error"]["detail"] == {"reason": "restart_limit"}
    row = session.read_on_line(lambda: storage.task(child).to_tree())
    assert row["state"]["status"] == "pending" and row["restarts"] == 5
    assert not called.is_set()


def test_a_pass_after_pause_leaves_an_exhausted_task_for_the_next_reservation(rig):
    """Pausing while a phase runs stages no fault in the pass that drains it.

    Upstream's reservation commit returns at once while scheduling is paused,
    so the exhausted task is still pending, and a raised limit then runs it.
    """
    session, storage = rig
    started, release, called = threading.Event(), threading.Event(), threading.Event()

    async def slow_start(task, runtime, context):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        await runtime.commit(
            lambda tx, row: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )

    async def abort(*args):
        raise AssertionError("No abort in this control")

    slow = define_task(
        "slow-test", 1, lambda _: {"phase": "start"}, {"start": slow_start}, abort
    )
    exhausted = definition(called)
    first = create(session, slow)
    second = create(session, exhausted)
    assert first < second  # The running task is reserved first.
    set_count(session, second, 5)
    scheduler = TaskScheduler(session, [slow, exhausted])
    scheduler.open()
    scheduler.resume()
    assert started.wait(5)
    pausing = threading.Thread(target=lambda: asyncio.run(scheduler.apause()))
    pausing.start()
    deadline = time.monotonic() + 5
    while True:
        with scheduler._guard:
            if not scheduler._enabled:
                break
        assert time.monotonic() < deadline, "Pause did not disable dispatch"
        time.sleep(0.005)
    release.set()
    pausing.join(10)
    assert not pausing.is_alive()
    done = session.read_on_line(lambda: storage.task(first).to_tree())
    assert done["state"]["outcome"]["status"] == "completed"
    row = session.read_on_line(lambda: storage.task(second).to_tree())
    assert row["state"]["status"] == "pending" and row["restarts"] == 5
    assert not called.is_set()
    # The next enabled reservation reads the raised limit and runs it.
    assert scheduler.set_restart_limit(6) is True
    scheduler.resume()
    assert called.wait(5)
    asyncio.run(scheduler.apause())
    row = session.read_on_line(lambda: storage.task(second).to_tree())
    assert row["state"]["outcome"]["status"] == "completed"


def test_limit_faults_a_task_whose_definition_is_missing(rig):
    """The limit check precedes definition resolution, as upstream's does."""
    session, storage = rig
    called = threading.Event()
    task_id = create(session, definition(called))
    set_count(session, task_id, 5)
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    assert scheduler.inspect_task(task_id).reason == "definition_missing"
    done = empty_pass(scheduler)
    scheduler.resume()
    assert done.wait(5)
    asyncio.run(scheduler.apause())
    row = session.read_on_line(lambda: storage.task(task_id).to_tree())
    assert row["state"]["status"] == "terminal"
    assert row["state"]["outcome"]["status"] == "faulted"
    assert row["state"]["outcome"]["error"] == {
        "message": f"Task {task_id} (restart-test) was cut off 5 times without progress",
        "detail": {"reason": "restart_limit"},
    }
    assert not called.is_set()


def test_recovery_increments_once_and_higher_fresh_policy_admits(rig):
    session, storage = rig
    called = threading.Event()
    task = definition(called)
    task_id = create(session, task)
    set_count(session, task_id, 4, running=True)
    scheduler = TaskScheduler(session, [task], restart_limit=6)
    scheduler.open()
    metadata = inspect_task(session, task_id, restart_limit=6)
    assert metadata.restart_count == 5 and metadata.reason is None
    assert not called.is_set()
    scheduler.resume()
    assert called.wait(5)
