"""Public control serialization, separate nonce authority and real successor roots."""

import asyncio
import json

import pytest
from adjudication_sdk_support import cli, logical
from test_product_session import profile, tools
from test_tool_adjudication import held_product
from tool_support import CountedWire, InfrastructureFailureFirst, counts

from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.resolution_values import (
    ResolutionError,
    ResolutionResult,
)
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.sdk import (
    DurableAdjudication,
    DurableProductError,
    DurableRunSelection,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("same_nonce", [False, True])
def test_concurrent_offered_decisions_have_one_actual_winner(tmp_path, same_nonce):
    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            effects = counts(tmp_path)
            results = await asyncio.gather(
                product.adjudicate_interrupted(task_id, nonce="a" * 32),
                product.adjudicate_interrupted(
                    task_id, nonce=("a" if same_nonce else "b") * 32
                ),
                return_exceptions=True,
            )
            assert results[0].decision == "interrupted"
            if same_nonce:
                assert results[1] == results[0]
            else:
                assert type(results[1]) is DurableProductError
                assert results[1].code == "not_supported"
            assert counts(tmp_path) == effects
            before = await owned_call(lambda: logical(product._session))
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                == results[0]
            )
            assert await owned_call(lambda: logical(product._session)) == before

    asyncio.run(asyncio.wait_for(scenario(), 20))


class _HeldFirstControl:
    """Observe actual lock admission, holding the first lease before its body."""

    def __init__(self, lock):
        self.lock = lock
        self.entered = asyncio.Event()
        self.queued = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts = 0

    async def __aenter__(self):
        self.attempts += 1
        attempt = self.attempts
        if attempt == 2:
            assert self.lock.locked()
            self.queued.set()
        await self.lock.acquire()
        if attempt == 1:
            self.entered.set()
            await self.release.wait()

    async def __aexit__(self, *args):
        self.lock.release()


@pytest.mark.parametrize("first", ["retry", "adjudicate"])
def test_offered_retry_and_adjudication_serialize_one_applicable_transition(
    tmp_path, first
):
    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            effects = counts(tmp_path)
            offers = {
                "retry": product.authorize_retry(task_id, nonce="a" * 32),
                "adjudicate": product.adjudicate_interrupted(task_id, nonce="a" * 32),
            }
            order = (first, "adjudicate" if first == "retry" else "retry")
            control = _HeldFirstControl(product._control)
            product._control = control
            earlier = asyncio.create_task(offers[order[0]])
            later = None
            try:
                await asyncio.wait_for(control.entered.wait(), 5)
                later = asyncio.create_task(offers[order[1]])
                await asyncio.wait_for(control.queued.wait(), 5)
                assert not earlier.done() and not later.done()
                assert counts(tmp_path) == effects
                control.release.set()
                results = dict(
                    zip(
                        order,
                        await asyncio.gather(earlier, later, return_exceptions=True),
                        strict=True,
                    )
                )
            finally:
                control.release.set()
                await asyncio.gather(
                    earlier,
                    *(() if later is None else (later,)),
                    return_exceptions=True,
                )
                if later is None:
                    offers[order[1]].close()
                product._control = control.lock
            if first == "retry":
                assert type(results["retry"]) is ResolutionResult
                assert results["retry"].source_task_id == task_id
                assert results["retry"].new_task_id != task_id
                assert type(results["adjudicate"]) is DurableProductError
                assert results["adjudicate"].code == "not_supported"
            else:
                assert type(results["adjudicate"]) is DurableAdjudication
                assert results["adjudicate"].decision == "interrupted"
                assert type(results["retry"]) is ResolutionError
                assert results["retry"].code == "not_supported"
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_real_generation_retry_successor_keeps_selection_and_nonce_namespaces(tmp_path):
    registry = tools(tmp_path, port_type=InfrastructureFailureFirst)
    wire = CountedWire(tmp_path)
    wire.failure = True
    nonce = "d" * 32

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            admission = await product.submit("input")
            failed = await product.resume()
            assert failed.inspection.outcome == "faulted"
            retry = await product.authorize_retry(
                admission.generation_task_id, nonce=nonce
            )
            assert retry.new_task_id != admission.generation_task_id
            selection = DurableRunSelection(retry.new_task_id, nonce)
            wire.failure = False
            assert (await product.resume(selection)).status == "held"
            child = next(
                t for t in (await product.inspect()).tasks if t.phase == "blocked"
            )
            effects = counts(tmp_path)
            assert effects == dict(calls=1, applications=1, prepares=1, providers=2)
            ack = await product.adjudicate_interrupted(child.task_id, nonce=nonce)
            assert ack.decision == "interrupted"
            assert (
                await product.authorize_retry(admission.generation_task_id, nonce=nonce)
                == retry
            )
            assert counts(tmp_path) == effects
            for wrong, code in (
                (None, "selection_required"),
                (DurableRunSelection(retry.new_task_id, "e" * 32), "incompatible"),
            ):
                with pytest.raises(DurableProductError) as refused:
                    await product.resume(wrong)
                assert refused.value.code == code
                assert counts(tmp_path) == effects
        # Actual cold CLI selection is required too; the decision nonce does not
        # select a root merely because its spelling matches the retry nonce.
        code, out, err, _ = await asyncio.to_thread(
            cli,
            tmp_path,
            "successor-unselected",
            child.task_id,
            nonce,
            kind="same",
            action="resume",
        )
        assert (code, out, err) == (
            1,
            "",
            "Durable command unavailable: selection_required\n",
        )
        assert counts(tmp_path) == effects
        code, out, err, _ = await asyncio.to_thread(
            cli,
            tmp_path,
            "successor-selected",
            child.task_id,
            nonce,
            kind="same",
            action="resume",
            extra=("--task", str(retry.new_task_id), "--resolution-nonce", nonce),
        )
        assert code == 0 and err == ""
        assert json.loads(out)["status"] == "terminal"
        assert counts(tmp_path) == dict(
            calls=2, applications=2, prepares=2, providers=3
        )
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as reopened:
            assert (
                await reopened.adjudicate_interrupted(child.task_id, nonce=nonce) == ack
            )
            finished = await reopened.resume(selection)
            assert finished.inspection.outcome == "completed"
            assert finished.task_id == retry.new_task_id

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_cancelled_opening_stays_owned_then_unopened_cache_refuses_closed(
    tmp_path, monkeypatch
):
    async def scenario():
        async with held_product(tmp_path):
            pass
        effects = counts(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def failed_open(self):
            # Kernel-injected opening failure, before recovery has written.
            entered.set()
            await release.wait()
            raise RuntimeError("private-open-failure")

        monkeypatch.setattr(TaskScheduler, "aopen", failed_open)
        async with await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=tools(tmp_path, port_type=InfrastructureFailureFirst),
        ) as product:
            task_id = next(
                t.task_id
                for t in (await product.inspect()).tasks
                if t.phase == "blocked"
            )
            before = await owned_call(lambda: logical(product._session))
            running = asyncio.create_task(product.resume())
            assert await asyncio.wait_for(entered.wait(), 5)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            decision = asyncio.create_task(
                product.adjudicate_interrupted(task_id, nonce="a" * 32)
            )
            await asyncio.sleep(0)
            assert not decision.done()
            assert product._scheduler is not None
            with product._scheduler._guard:
                assert not product._scheduler._opened
            release.set()
            with pytest.raises(DurableProductError) as refused:
                await asyncio.wait_for(decision, 5)
            assert refused.value.code == "closed"
            assert await owned_call(lambda: logical(product._session)) == before
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))
