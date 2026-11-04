"""Host-only restart policy and actual Session-line scheduler controls."""

import asyncio
import math
import sys
import threading

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


@pytest.mark.parametrize("count", [5, 7.5, 2**53, 1e300])
def test_parking_on_actual_line_holds_without_dispatch(rig, count):
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
