"""Selected Product retry ownership; labelled lifecycle cuts use real run cleanup."""

import asyncio

import pytest
from generation_support import Wire
from test_product_session import profile

from pipy_harness import durable_product_api as api
from pipy_harness.sdk import (
    DurableProductError,
    DurableRunSelection,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


async def admitted(tmp_path, wire):
    product = await create_durable_product_session(
        tmp_path / "product.sqlite",
        workspace=tmp_path,
        profile=profile(tmp_path, wire),
        tools={},
    )
    admission = await product.submit("input")
    return product, admission.generation_task_id


def test_retry_ack_precedes_real_callback_retirement_and_resume_coalesces(tmp_path):
    wire = Wire()
    wire.release.clear()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        try:
            reset = await product.retry_task(target)
            assert reset.to_tree() == {"task_id": target, "reset": False}
            assert await asyncio.to_thread(wire.entered.wait, 5)
            operation = product._current
            assert operation.acknowledged and not operation.task.done()
            first = asyncio.create_task(product.resume())
            second = asyncio.create_task(product.resume())
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not operation.task.done() and not second.done()
            with pytest.raises(DurableProductError, match="busy"):
                await product.retry_task(target)
            with pytest.raises(DurableProductError, match="selection_required"):
                await product.retry_task(
                    target, selection=DurableRunSelection(target, "a" * 32)
                )
            wire.release.set()
            assert (await second).inspection.outcome == "completed"
            assert len(wire.calls) == 1
            previous = product._current
            assert (await product.resume()).status == "terminal"
            assert product._current is not previous  # No healthy result cache.
        finally:
            wire.release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_retry_during_resume_origin_active_refuses_without_queued_reset(tmp_path):
    wire = Wire()
    wire.release.clear()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        try:
            running = asyncio.create_task(product.resume())
            assert await asyncio.to_thread(wire.entered.wait, 5)
            current = product._current
            with pytest.raises(DurableProductError, match="busy"):
                await product.retry_task(target)
            assert product._current is current and current.ack is None
            wire.release.set()
            assert (await running).status == "terminal"
        finally:
            wire.release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_terminal_root_ack_uses_exact_operation_without_creating_scheduler(tmp_path):
    wire = Wire()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        await product.resume()
        await product.close()
        product = await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        try:
            operation = product._retry_operation(target)
            reset = await asyncio.shield(operation.ack)
            assert not reset.reset
            assert (await asyncio.shield(operation.task)).status == "terminal"
            assert product._scheduler is None and len(wire.calls) == 1
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize("handoff_error", [False, True])
def test_failed_ack_cleanup_barrier_keeps_late_resume_on_same_owner(
    tmp_path, monkeypatch, handoff_error
):
    # Labelled admission/handoff error injection, NOT a physical crash claim.
    # The acquired real CompletedWatch and its actual joined waiter are retained.
    wire = Wire()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        cleanup_entered, release = asyncio.Event(), asyncio.Event()
        original_wait = api.CompletedWatch.wait_closed

        async def held_wait(watch):
            try:
                return await original_wait(watch)
            finally:
                cleanup_entered.set()
                await release.wait()

        async def admission_refusal(*args):
            raise DurableProductError("incompatible")

        async def local_handoff_error(*args):
            raise DurableProductError("unavailable")

        try:
            with monkeypatch.context() as cut:
                cut.setattr(api.CompletedWatch, "wait_closed", held_wait)
                cut.setattr(product, "_scheduler_for_run", admission_refusal)
                if handoff_error:
                    cut.setattr(product, "_handoff_target", local_handoff_error)
                operation = product._retry_operation(target)
                with pytest.raises(DurableProductError, match="incompatible"):
                    await asyncio.shield(operation.ack)
                await cleanup_entered.wait()
                assert not operation.task.done()
                first = asyncio.create_task(product.resume())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                handoff = operation.handoff
                assert handoff.pending and not handoff.task.done()
                late = asyncio.create_task(product.resume())
                await asyncio.sleep(0)
                assert operation.handoff is handoff
                assert not first.done() and not late.done() and not wire.calls
                with pytest.raises(DurableProductError, match="busy"):
                    await product.retry_task(target)
                # Remove admission injection before the real fallback can start.
                cut.undo()
                release.set()
                results = await asyncio.gather(first, late, return_exceptions=True)
                assert operation.task.done() and not handoff.pending
                if handoff_error:
                    assert all(isinstance(v, DurableProductError) for v in results)
                    assert [v.code for v in results] == ["unavailable"] * 2
                    assert not wire.calls and product._current is None
                    assert (await product.resume()).inspection.outcome == "completed"
                else:
                    assert all(v.inspection.outcome == "completed" for v in results)
                assert len(wire.calls) == 1
        finally:
            release.set()
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize("body_error", [False, True])
def test_post_ack_latch_close_and_unrelated_body_priority(
    tmp_path, monkeypatch, body_error
):
    wire = Wire()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        settled = asyncio.Event()

        async def fail_result(*args):
            raise DurableProductError("incompatible")

        monkeypatch.setattr(product, "_wait_result", fail_result)
        if body_error:
            with pytest.raises(LookupError, match="body"):
                async with product:
                    operation = product._retry_operation(target)
                    operation.task.add_done_callback(lambda _: settled.set())
                    await asyncio.shield(operation.ack)
                    await settled.wait()  # No result await or latch report.
                    raise LookupError("body")
        else:
            await product.retry_task(target)
            operation = product._current
            with pytest.raises(DurableProductError, match="continuation_failed"):
                await asyncio.shield(operation.task)
            for action in (
                product.resume,
                lambda: product.retry_task(target),
                lambda: product.submit("second"),
                lambda: product.adjudicate_interrupted(target, nonce="a" * 32),
                lambda: product.authorize_retry(target, nonce="a" * 32),
            ):
                with pytest.raises(DurableProductError, match="continuation_failed"):
                    await action()
        for _ in range(2):
            with pytest.raises(DurableProductError, match="continuation_failed"):
                await product.close()
        assert not product._scheduler._thread.is_alive()

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_native_close_failure_keeps_priority_over_unrelated_body(tmp_path, monkeypatch):
    async def scenario():
        product, _ = await admitted(tmp_path, Wire())
        original = type(product._session).aclose

        async def closing(session):
            await original(session)
            if session is product._session:
                raise RuntimeError("labelled native close failure")

        monkeypatch.setattr(type(product._session), "aclose", closing)
        with pytest.raises(RuntimeError, match="native close failure"):
            async with product:
                raise LookupError("body")
        with pytest.raises(RuntimeError, match="native close failure"):
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_eager_registration_and_factory_failure_have_no_unowned_effect(tmp_path):
    async def scenario():
        wire = Wire()
        product, target = await admitted(tmp_path, wire)
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()

        def refuse_factory(loop, coroutine, **kwargs):
            raise RuntimeError("labelled task creation failure")

        try:
            loop.set_task_factory(refuse_factory)
            with pytest.raises(RuntimeError, match="creation failure"):
                product._retry_operation(target)
            assert product._current is None and not product._operations
            assert not wire.calls
            loop.set_task_factory(getattr(asyncio, "eager_task_factory", previous))
            operation = product._retry_operation(target)
            assert (
                product._current is operation and operation.task in product._operations
            )
            assert not operation.task.done() and not wire.calls
            assert not (await asyncio.shield(operation.ack)).reset
            assert (await asyncio.shield(operation.task)).status == "terminal"
        finally:
            loop.set_task_factory(previous)
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize("capture_error", [False, True])
def test_actual_close_during_drain_distinguishes_own_stop_from_observer_error(
    tmp_path, monkeypatch, capture_error
):
    async def scenario():
        product, target = await admitted(tmp_path, Wire())
        entered, release = asyncio.Event(), asyncio.Event()
        original = api.CompletedWatch.drain
        close_requested = asyncio.Event()
        original_close = type(product._session).request_close

        def request_close(session):
            original_close(session)
            if session is product._session:
                close_requested.set()

        monkeypatch.setattr(type(product._session), "request_close", request_close)
        active_watch = []

        async def held_drain(watch):
            active_watch.append(watch)
            entered.set()
            await release.wait()
            return await original(watch)

        monkeypatch.setattr(api.CompletedWatch, "drain", held_drain)
        await product.retry_task(target)
        operation = product._current
        await entered.wait()
        if capture_error:
            # Labelled native observer failure, then genuine close racing it.
            # The watch's first end reason is immutable; close must not mask it.
            active_watch[0]._end("capture_error")
        closing = asyncio.create_task(product.close())
        try:
            await close_requested.wait()
            reason = await active_watch[0].wait_closed()
            assert (
                reason == "capture_error"
                if capture_error
                else reason in ("stopped", "session_closed")
            )
            assert operation.close_requested
            assert not closing.done() and not operation.task.done()
            release.set()
            if capture_error:
                with pytest.raises(DurableProductError, match="continuation_failed"):
                    await closing
                with pytest.raises(DurableProductError, match="continuation_failed"):
                    await product.close()
            else:
                await closing
                assert operation.outcome.kind == "own_close"
                with pytest.raises(DurableProductError, match="closed"):
                    await asyncio.shield(operation.task)
                await product.close()
        finally:
            release.set()
            await asyncio.gather(closing, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 15))
