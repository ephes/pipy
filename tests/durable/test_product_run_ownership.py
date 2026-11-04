"""Selected Product run ownership; labelled lifecycle cuts use real run cleanup."""

import asyncio

import pytest
from generation_support import Wire
from test_product_session import profile

from pipy_harness import durable_product_api as api
from pipy_harness.sdk import (
    DurableProductError,
    DurableRunSelection,
    create_durable_product_session,
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


def test_resume_coalesces_and_a_cancelled_waiter_keeps_the_owned_run(tmp_path):
    wire = Wire()
    wire.release.clear()

    async def scenario():
        product, target = await admitted(tmp_path, wire)
        try:
            first = asyncio.create_task(product.resume())
            assert await asyncio.to_thread(wire.entered.wait, 5)
            operation = product._current
            assert not operation.task.done()
            second = asyncio.create_task(product.resume())
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not operation.task.done() and not second.done()
            with pytest.raises(DurableProductError, match="selection_required"):
                await product.resume(DurableRunSelection(target, "a" * 32))
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
        product, _ = await admitted(tmp_path, wire)
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()

        def refuse_factory(loop, coroutine, **kwargs):
            raise RuntimeError("labelled task creation failure")

        try:
            loop.set_task_factory(refuse_factory)
            with pytest.raises(RuntimeError, match="creation failure"):
                product._start_operation(None)
            assert product._current is None and not product._operations
            assert not wire.calls
            loop.set_task_factory(getattr(asyncio, "eager_task_factory", previous))
            operation = product._start_operation(None)
            assert (
                product._current is operation and operation.task in product._operations
            )
            assert not operation.task.done() and not wire.calls
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
        product, _ = await admitted(tmp_path, Wire())
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
        running = asyncio.create_task(product.resume())
        await entered.wait()
        operation = product._current
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
            await closing
            with pytest.raises(DurableProductError) as refused:
                await running
            # Only the Product's own close stop is reported as closed.
            assert refused.value.code == ("unavailable" if capture_error else "closed")
            await product.close()
        finally:
            release.set()
            await asyncio.gather(closing, running, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 15))
