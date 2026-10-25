"""Real host admission/retirement barriers for the product's explicit dispatch."""

import asyncio
import threading

import pytest

from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session import SessionClosedError
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tasks import define_task


@pytest.fixture(params=["memory", "sqlite"])
def owner(request, tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    session = Session(
        MemoryStorage()
        if request.param == "memory"
        else open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    yield session
    session.close()


async def abort(*args):
    raise AssertionError("Pause is not abort")


def admit(session, task):
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    return session.commit(
        lambda tx: tx.create_task(
            task, {}, ownership={"kind": "conversation"}, conversation_id=cid
        )
    )


async def until_disabled(scheduler):
    async def wait():
        while True:
            with scheduler._guard:
                enabled = scheduler._enabled
            if not enabled:
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 5)


def terminal(tx, current):
    return {"status": "terminal", "outcome": {"status": "completed", "result": 1}}


def test_pause_prevents_commit_notification_from_dispatching_new_work(owner):
    calls = []
    first = threading.Event()

    async def work(record, runtime, context):
        calls.append(runtime.task_id)
        await runtime.commit(terminal)
        first.set()

    task = define_task(
        "pause.work", 1, lambda _: {"phase": "work"}, {"work": work}, abort
    )
    admit(owner, task)
    scheduler = TaskScheduler(owner, [task])
    scheduler.open()
    scheduler.resume()
    assert first.wait(5)
    asyncio.run(scheduler.apause())
    second = admit(owner, task)
    asyncio.run(scheduler.apause())
    assert len(calls) == 1
    scheduler.resume()

    async def finish():
        while second not in calls:
            await asyncio.sleep(0)
        await scheduler.apause()

    asyncio.run(asyncio.wait_for(finish(), 5))
    assert len(calls) == 2


def test_pause_waits_for_reservation_before_invocation_install(owner, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    called = []

    async def work(record, runtime, context):
        called.append(True)
        await runtime.commit(terminal)

    task = define_task(
        "pause.reserve", 1, lambda _: {"phase": "work"}, {"work": work}, abort
    )
    admit(owner, task)
    scheduler = TaskScheduler(owner, [task])
    scheduler.open()
    real = scheduler._candidate

    async def held(*args):
        result = await real(*args)
        entered.set()
        assert await owned_call(lambda: release.wait(5))
        return result

    monkeypatch.setattr(scheduler, "_candidate", held)
    scheduler.resume()
    assert entered.wait(5)

    async def scenario():
        pause = asyncio.create_task(scheduler.apause())
        await until_disabled(scheduler)
        with scheduler._guard:
            assert scheduler._invocation is None and scheduler._draining
        assert not pause.done()
        release.set()
        await asyncio.wait_for(pause, 5)

    try:
        asyncio.run(scenario())
        assert called == []
    finally:
        release.set()


def test_cancelled_pause_waiter_keeps_disable_and_real_finalizer_ownership(owner):
    entered, release, finalizing, finish = (threading.Event() for _ in range(4))

    def actual():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            finalizing.set()
            assert finish.wait(5)

    async def work(record, runtime, context):
        await owned_call(actual)
        await runtime.commit(terminal)

    task = define_task(
        "pause.finalizer", 1, lambda _: {"phase": "work"}, {"work": work}, abort
    )
    admit(owner, task)
    scheduler = TaskScheduler(owner, [task])
    scheduler.open()
    scheduler.resume()
    assert entered.wait(5)

    async def scenario():
        pause = asyncio.create_task(scheduler.apause())
        await until_disabled(scheduler)
        release.set()
        assert await asyncio.to_thread(finalizing.wait, 5)
        assert not pause.done()
        pause.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pause
        with scheduler._guard:
            assert scheduler._draining and scheduler._invocation is not None
        second = asyncio.create_task(scheduler.apause())
        await asyncio.sleep(0)
        assert not second.done()
        finish.set()
        await asyncio.wait_for(second, 5)
        with scheduler._guard:
            assert not scheduler._draining and scheduler._invocation is None

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        finish.set()


def test_phase_cannot_pause_and_join_itself(owner):
    caught = []
    ended = threading.Event()

    async def work(record, runtime, context):
        try:
            await asyncio.wait_for(scheduler.apause(), 1)
        except (RuntimeError, TimeoutError) as error:
            caught.append(error)
        await runtime.commit(terminal)
        ended.set()

    task = define_task(
        "pause.self", 1, lambda _: {"phase": "work"}, {"work": work}, abort
    )
    admit(owner, task)
    scheduler = TaskScheduler(owner, [task])
    scheduler.open()
    scheduler.resume()
    assert ended.wait(5)
    asyncio.run(scheduler.apause())
    assert len(caught) == 1 and type(caught[0]) is RuntimeError
    assert "phase host" in str(caught[0])


def test_close_supersedes_pause_but_waits_for_real_finalizer(owner):
    entered, finalizing, finish = (threading.Event() for _ in range(3))

    async def work(record, runtime, context):
        entered.set()
        await context.signal.wait()
        finalizing.set()
        assert await owned_call(lambda: finish.wait(5))

    task = define_task(
        "pause.close", 1, lambda _: {"phase": "work"}, {"work": work}, abort
    )
    admit(owner, task)
    scheduler = TaskScheduler(owner, [task])
    scheduler.open()
    scheduler.resume()
    assert entered.wait(5)

    async def scenario():
        pause = asyncio.create_task(scheduler.apause())
        await until_disabled(scheduler)
        close = asyncio.create_task(owner.aclose())
        assert await asyncio.to_thread(finalizing.wait, 5)
        assert not close.done()
        # Close may be noticed before the pause starts waiting, or only
        # after the actual invocation retires. Neither is a quiescent ACK.
        if pause.done():
            assert isinstance(pause.exception(), SessionClosedError)
        assert not close.done()
        finish.set()
        with pytest.raises(SessionClosedError):
            await asyncio.wait_for(pause, 5)
        await asyncio.wait_for(close, 5)

    try:
        asyncio.run(scenario())
    finally:
        finish.set()
