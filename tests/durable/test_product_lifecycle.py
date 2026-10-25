"""Public cancellation ownership with real workers and fresh OS lease probes."""

import asyncio
import threading

import pytest
from generation_support import Wire
from test_product_session import profile
from test_tool_lifecycle import independent_opener

from pipy_harness import durable_product_api as product_api
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.product_wake import ProductWake
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session
from pipy_harness.sdk import (
    create_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("cut", ["raw_opener", "header_commit"])
def test_cancelled_acquisition_keeps_real_lease_until_owned_work_exits(
    tmp_path, monkeypatch, cut
):
    path = tmp_path / "product.sqlite"
    entered, release, closed = (threading.Event() for _ in range(3))
    original = product_api.open_sqlite_storage

    def opening(*args, **kwargs):
        backend = original(*args, **kwargs)
        close = backend.close

        def closing():
            close()
            closed.set()

        monkeypatch.setattr(backend, "close", closing)
        if cut == "raw_opener":
            entered.set()
            assert release.wait(15)
        return backend

    def header_cut(connection):
        if connection.execute("SELECT count(*) FROM documents").fetchone()[0]:
            entered.set()
            assert release.wait(15)

    monkeypatch.setattr(product_api, "open_sqlite_storage", opening)
    if cut == "header_commit":
        monkeypatch.setattr(sqlite, "_before_commit", header_cut)

    async def scenario():
        acquisition = asyncio.create_task(
            create_durable_product_session(
                path,
                workspace=tmp_path,
                profile=profile(tmp_path, Wire()),
                tools={},
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        acquisition.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(acquisition, 1)
        assert not closed.is_set()
        await asyncio.to_thread(
            independent_opener, path, tmp_path, cut + "-held", "refused"
        )
        release.set()
        assert await asyncio.to_thread(closed.wait, 5)
        await asyncio.to_thread(
            independent_opener, path, tmp_path, cut + "-closed", "opened"
        )

    try:
        asyncio.run(asyncio.wait_for(scenario(), 15))
    finally:
        release.set()


def test_cancelled_resume_close_waiters_retain_actual_provider_and_finalizer(tmp_path):
    path = tmp_path / "product.sqlite"
    finalizing, final_release, exited = (threading.Event() for _ in range(3))

    class HeldWire(Wire):
        def post_json(self, *args, **kwargs):
            try:
                return super().post_json(*args, **kwargs)
            finally:
                finalizing.set()
                assert final_release.wait(15)
                exited.set()

    wire = HeldWire()
    wire.release.clear()

    async def scenario():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        try:
            await product.submit("input")
            running = asyncio.create_task(product.resume())
            assert await asyncio.to_thread(wire.entered.wait, 5)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            close = asyncio.create_task(product.close())
            await asyncio.sleep(0)
            close.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close
            assert not exited.is_set()
            await asyncio.to_thread(
                independent_opener,
                path,
                tmp_path,
                "provider-held",
                "refused",
                invoke_held=True,
            )
            wire.release.set()
            assert await asyncio.to_thread(finalizing.wait, 5)
            await asyncio.to_thread(
                independent_opener, path, tmp_path, "provider-finalizer-held", "refused"
            )
            assert not exited.is_set()
            final_release.set()
            await asyncio.wait_for(product.close(), 5)
            assert exited.is_set()
            assert not product._scheduler._thread.is_alive()
            await asyncio.to_thread(
                independent_opener, path, tmp_path, "provider-closed", "opened"
            )
            assert len(wire.calls) == 1
        finally:
            wire.release.set()
            final_release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_public_control_reentry_from_held_session_line_refuses_before_wait(tmp_path):
    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, Wire()),
            tools={},
        ) as product:

            async def held(tx):
                for action in (
                    lambda: product.submit("input"),
                    product.resume,
                    product.close,
                    product.observe_completed,
                ):
                    with pytest.raises(ReentrantSessionCall):
                        await action()

            await product._session.acommit(held)
            assert (await product.inspect()).tasks == ()

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_wake_bridge_coalesces_real_publisher_work_without_payload_filtering(
    monkeypatch,
):
    session = Session(MemoryStorage())
    scheduled = []

    async def scenario():
        event = asyncio.Event()
        bridge = ProductWake(session, event)
        loop = asyncio.get_running_loop()
        original = loop.call_soon_threadsafe

        def schedule(callback, *args, **kwargs):
            if getattr(callback, "__self__", None) is bridge:
                scheduled.append(callback)
            return original(callback, *args, **kwargs)

        monkeypatch.setattr(loop, "call_soon_threadsafe", schedule)

        def publish():
            for _ in range(50):
                session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"}))

        publisher = threading.Thread(target=publish)
        publisher.start()
        publisher.join(5)  # Hold this loop to make queued work independently countable.
        assert not publisher.is_alive()
        assert len(scheduled) == 1
        assert not event.is_set()
        await asyncio.sleep(0)
        assert event.is_set()
        bridge.stop()
        with session._owner._cond:
            assert not session._commit_listeners.copy()

    try:
        asyncio.run(scenario())
    finally:
        session.close()


def test_closed_loop_bridge_detaches_on_next_actual_publication():
    session = Session(MemoryStorage())
    loop = asyncio.new_event_loop()

    async def attach():
        return ProductWake(session, asyncio.Event())

    bridge = loop.run_until_complete(attach())
    loop.close()
    try:
        session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"}))
        with session._owner._cond:
            assert not session._commit_listeners.copy()
        assert session.failure is None
    finally:
        bridge.stop()
        session.close()


def test_held_commit_between_metadata_read_and_wait_cannot_be_lost(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from test_product_session import tools
    from tool_support import CountedWire, Keyed

    entered, release, blocked = (threading.Event() for _ in range(3))

    class InvalidHeld(Keyed):
        def invoke(self, request, context):
            entered.set()
            assert release.wait(10)
            return replace(
                super().invoke(request, context), details={"invalid": object()}
            )

    registry = tools(tmp_path, port_type=InvalidHeld)
    actual = product_api.ainspect_live

    async def scenario():
        read_held, allow_read = asyncio.Event(), asyncio.Event()
        once = False

        async def delayed(*args, **kwargs):
            nonlocal once
            page = await actual(*args, **kwargs)
            if not once and any(
                t.family == "model_tool" and t.phase == "execute" for t in page.tasks
            ):
                once = True
                assert await asyncio.to_thread(entered.wait, 5)
                assert all(t.reason is None for t in page.tasks)
                read_held.set()
                await allow_read.wait()
            return page

        monkeypatch.setattr(product_api, "ainspect_live", delayed)
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=registry,
        ) as product:

            def publication(commit):
                for change in commit.changes:
                    if hasattr(change, "to_tree"):
                        tree = change.to_tree()
                        if (
                            tree["type"] == "task"
                            and tree["value"]["state"]
                            .get("checkpoint", {})
                            .get("phase")
                            == "blocked"
                        ):
                            blocked.set()

            unsubscribe = product._session.subscribe_commits(publication)
            try:
                await product.submit("input")
                work = asyncio.create_task(product.resume())
                await asyncio.wait_for(read_held.wait(), 5)
                release.set()
                assert await asyncio.to_thread(blocked.wait, 5)
                allow_read.set()
                assert (await asyncio.wait_for(work, 5)).status == "held"
            finally:
                release.set()
                allow_read.set()
                unsubscribe()

    try:
        asyncio.run(asyncio.wait_for(scenario(), 15))
    finally:
        release.set()


def test_cancelled_acquisition_wrappers_reaped_loop_closed_real_opener_still_owns_path(
    tmp_path, monkeypatch
):
    path = tmp_path / "product.sqlite"
    entered, release, exited = (threading.Event() for _ in range(3))
    workers = []
    actual = product_api.open_sqlite_storage

    def held(*args, **kwargs):
        backend = actual(*args, **kwargs)
        workers.append(threading.current_thread())
        close = backend.close

        def closing():
            close()
            exited.set()

        monkeypatch.setattr(backend, "close", closing)
        entered.set()
        assert release.wait(15)
        return backend

    monkeypatch.setattr(product_api, "open_sqlite_storage", held)
    loop = asyncio.new_event_loop()

    async def acquire_then_cancel():
        outer = asyncio.create_task(
            create_durable_product_session(
                path, workspace=tmp_path, profile=profile(tmp_path, Wire()), tools={}
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        for task in pending:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 1)
        assert all(task.done() for task in pending)
        assert not exited.is_set()

    try:
        loop.run_until_complete(acquire_then_cancel())
        loop.close()
        assert not exited.is_set() and workers[0].is_alive()
        independent_opener(path, tmp_path, "caller-loop-gone-held", "refused")
        release.set()
        assert exited.wait(5)
        workers[0].join(5)
        assert not workers[0].is_alive()
        independent_opener(path, tmp_path, "caller-loop-gone-joined", "opened")
    finally:
        release.set()
        if not loop.is_closed():
            loop.close()
        for worker in workers:
            worker.join(5)


def test_second_input_refuses_promptly_while_explicit_resume_owns_control(tmp_path):
    from pipy_harness.sdk import DurableProductError

    wire = Wire()
    wire.release.clear()

    async def scenario():
        product = await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        try:
            await product.submit("input")
            running = asyncio.create_task(product.resume())
            assert await asyncio.to_thread(wire.entered.wait, 5)
            with pytest.raises(DurableProductError, match="already_admitted"):
                await asyncio.wait_for(
                    product.submit("must not become queued input"), 1
                )
            wire.release.set()
            assert (await running).inspection.outcome == "completed"
            assert len(wire.calls) == 1
        finally:
            wire.release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_cancelled_first_admission_does_not_queue_second_input_before_ack(
    tmp_path, monkeypatch
):
    from pipy_harness.sdk import DurableProductError

    entered, release = threading.Event(), threading.Event()

    def hold(connection):
        if connection.execute("SELECT count(*) FROM tasks").fetchone()[0]:
            entered.set()
            assert release.wait(10)

    async def scenario():
        product = await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, Wire()),
            tools={},
        )
        try:
            with monkeypatch.context() as patch:
                patch.setattr(sqlite, "_after_commit", hold)
                submission = asyncio.create_task(product.submit("input"))
                assert await asyncio.to_thread(entered.wait, 5)
                submission.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await submission
                with pytest.raises(DurableProductError, match="busy"):
                    await asyncio.wait_for(product.submit("never accepted"), 1)
                release.set()
                # The actual strongly-owned control, not the cancelled API
                # wrapper, installs the observed committed admission fact.
                async with product._control:
                    pass
            with pytest.raises(DurableProductError, match="already_admitted"):
                await product.submit("never accepted either")
            result = await product.resume()
            assert [m.decode().content.value for m in result.completed.messages] == [
                "input",
                "done",
            ]
        finally:
            release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 12))


@pytest.mark.parametrize("control", ["terminate", "invalid_result"])
def test_positive_hold_refreshes_parent_after_precommit_metadata_capture(
    tmp_path, monkeypatch, control
):
    from dataclasses import replace

    from test_product_session import tools
    from tool_support import CountedWire, Keyed, counts

    from pipy_harness.native.durable.storage import StorageWrite

    entered, release = threading.Event(), threading.Event()

    class Held(Keyed):
        def invoke(self, request, context):
            entered.set()
            assert release.wait(10)
            result = super().invoke(request, context)
            return (
                replace(result, details={"invalid": object()})
                if control == "invalid_result"
                else result
            )

    registry = tools(tmp_path, port_type=Held, terminate=control == "terminate")
    actual = product_api.ainspect_task

    async def scenario():
        committed = asyncio.Event()
        loop = asyncio.get_running_loop()
        captured = []

        async def delayed(*args, **kwargs):
            metadata = await actual(*args, **kwargs)
            if (
                not captured
                and metadata is not None
                and metadata.family == "generation"
                and metadata.status == "waiting"
                and metadata.reason is None
            ):
                captured.append(metadata)
                assert await asyncio.to_thread(entered.wait, 5)
                release.set()
                await asyncio.wait_for(committed.wait(), 5)
            return metadata

        monkeypatch.setattr(product_api, "ainspect_task", delayed)
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=registry,
        ) as product:

            def publication(value):
                for change in value.changes:
                    if type(change) is not StorageWrite:
                        continue
                    row = change.to_tree()
                    if row["type"] != "task" or row["value"]["kind"] != "pi.tool":
                        continue
                    state = row["value"]["state"]
                    positive = (
                        control == "terminate"
                        and state["status"] == "terminal"
                        and state["outcome"]["status"] == "completed"
                        or control == "invalid_result"
                        and state.get("checkpoint", {}).get("reason")
                        == "invalid_result"
                    )
                    if positive:
                        loop.call_soon_threadsafe(committed.set)

            unsubscribe = product._session.subscribe_commits(publication)
            try:
                admission = await product.submit("input")
                result = await product.resume()
                assert (
                    committed.is_set()
                    and len(captured) == 1
                    and captured[0].reason is None
                )
                assert result.status == "held" and result.inspection.status == "waiting"
                expected = "retained_terminate" if control == "terminate" else None
                current = await product.inspect(admission.generation_task_id)
                assert current.reason == expected
                assert counts(tmp_path) == {
                    "providers": 1,
                    "calls": 1,
                    "applications": 1,
                    "prepares": 1,
                }
                assert result.inspection.reason == current.reason
                assert any(
                    t.reason == "invalid_result"
                    for t in (await product.inspect()).tasks
                ) == (control == "invalid_result")
                assert not result.completed.rounds
                assert counts(tmp_path) == {
                    "providers": 1,
                    "calls": 1,
                    "applications": 1,
                    "prepares": 1,
                }
            finally:
                unsubscribe()
                release.set()

    try:
        asyncio.run(asyncio.wait_for(scenario(), 20))
    finally:
        release.set()
