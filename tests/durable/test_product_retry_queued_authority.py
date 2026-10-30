"""Two finite accepted-matrix families over actual public C1/C2 authority."""

import asyncio

import pytest
from adjudication_sdk_support import disk_logical
from product_retry_queued_authority_support import (
    ControlObserver,
    DeferredOwnerRetirement,
    caller_turn,
    control,
    save_facts,
)
from test_tool_adjudication import held_product
from tool_support import counts

from pipy_harness.sdk import DurableProductError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("kind", ["C1", "C2"])
def test_postack_failure_gates_actual_queued_authority_before_retirement_observer(
    tmp_path, monkeypatch, kind
):
    """Only late result error/retirement delay are synthetic lifecycle seams."""

    async def run():
        completed = False
        with pytest.raises(DurableProductError, match="continuation_failed"):
            async with held_product(tmp_path) as (product, _, target):
                ready, fail = asyncio.Event(), asyncio.Event()
                original_result = product._wait_result
                operation = None
                queued = None
                retirement = DeferredOwnerRetirement(product._operations)

                async def late_result(*args, **kwargs):
                    value = await original_result(*args, **kwargs)
                    assert value.status == "held"
                    ready.set()
                    await fail.wait()
                    raise DurableProductError("incompatible")

                try:
                    with monkeypatch.context() as patch:
                        patch.setattr(product, "_operations", retirement)
                        patch.setattr(product, "_wait_result", late_result)
                        observed = ControlObserver(patch, product, failure=True)
                        operation = product._retry_operation(target)
                        observed.owner = retirement.owner = operation.task
                        assert not (await asyncio.shield(operation.ack)).reset
                        await asyncio.wait_for(ready.wait(), 5)
                        assert operation.acknowledged and not operation.task.done()
                        before = disk_logical(tmp_path / "product.sqlite")
                        effects = counts(tmp_path)

                        # A genuine selected run already has an input. Submit
                        # cannot naturally queue without forged header/flags.
                        assert product._initial_admitted
                        assert product._submission_inflight is False
                        with pytest.raises(
                            DurableProductError, match="already_admitted"
                        ):
                            await product.submit("must not reserve another input")
                        assert product._submission_inflight is False
                        assert disk_logical(tmp_path / "product.sqlite") == before

                        queued = asyncio.create_task(
                            control(product, target, kind, "a" * 32)
                        )
                        await observed.actually_queued()
                        assert not queued.done() and not observed.body_calls
                        assert operation.outcome is None
                        assert disk_logical(tmp_path / "product.sqlite") == before
                        fail.set()
                        with pytest.raises(
                            DurableProductError, match="continuation_failed"
                        ):
                            await queued
                        with pytest.raises(
                            DurableProductError, match="continuation_failed"
                        ):
                            await asyncio.shield(operation.task)
                        await asyncio.wait_for(retirement.called.wait(), 5)
                        assert retirement.deferred == [operation.task]
                        assert operation.task in product._operations
                        assert not retirement.released
                        assert observed.release_facts == [
                            {
                                "outcome": "continuation_failed",
                                "locked": True,
                                "owner_task_done": False,
                            }
                        ]
                        assert not observed.body_calls
                        assert observed.timeline == ["owner-release"]
                        assert disk_logical(tmp_path / "product.sqlite") == before
                        assert counts(tmp_path) == effects
                        with pytest.raises(
                            DurableProductError, match="continuation_failed"
                        ):
                            await product.submit(
                                "must not leak an inflight reservation"
                            )
                        assert product._submission_inflight is False
                        assert disk_logical(tmp_path / "product.sqlite") == before
                        save_facts(
                            tmp_path,
                            "queued-failure-facts.json",
                            {
                                "control": kind,
                                "release_facts": observed.release_facts,
                                "actual_lock_waiter_observed": True,
                                "specific_actual_retirement_callback_deferred": True,
                                "all_mutation_bodies_entered": len(observed.body_calls),
                                "submission_inflight": product._submission_inflight,
                                "before_SQL": before,
                                "after_SQL": disk_logical(tmp_path / "product.sqlite"),
                                "before_effects": effects,
                                "after_effects": counts(tmp_path),
                                "qualification": "Late result error and exact owner discard observer delay are labelled lifecycle injections. Real public C1/C2 was queued on the existing lock. No forged authority/flags or fake Future. Submit is immediate, not falsely called queued.",
                            },
                        )
                        completed = True
                finally:
                    fail.set()
                    work = ([] if queued is None else [queued]) + (
                        [] if operation is None else [operation.task]
                    )
                    await asyncio.gather(*work, return_exceptions=True)
                    retirement.release()
        assert completed

    asyncio.run(asyncio.wait_for(run(), 30))


@pytest.mark.parametrize("kind", ["C1", "C2"])
def test_active_resume_joins_owner_then_ordered_winner_changes_fresh_run(
    tmp_path, monkeypatch, kind
):
    """The positive neighbour: the same actual authority really commits."""

    async def run():
        async with held_product(tmp_path) as (product, _, target):
            ready, release = asyncio.Event(), asyncio.Event()
            original_result = product._wait_result
            first_result = []
            held_once = False
            offered = []
            queued = None

            async def held_result(*args, **kwargs):
                nonlocal held_once
                value = await original_result(*args, **kwargs)
                if not held_once:
                    held_once = True
                    assert value.status == "held"
                    first_result.append(value)
                    ready.set()
                    await release.wait()
                return value

            try:
                with monkeypatch.context() as patch:
                    patch.setattr(product, "_wait_result", held_result)
                    observed = ControlObserver(patch, product, failure=False)
                    first = asyncio.create_task(product.resume())
                    offered.append(first)
                    await asyncio.wait_for(ready.wait(), 5)
                    operation = product._current
                    observed.owner = operation.task
                    before = disk_logical(tmp_path / "product.sqlite")
                    effects = counts(tmp_path)
                    queued = asyncio.create_task(
                        control(product, target, kind, "b" * 32)
                    )
                    await observed.actually_queued()
                    join = asyncio.create_task(product.resume())
                    offered.append(join)
                    await caller_turn()
                    assert product._current is operation and not join.done()
                    assert not queued.done() and not observed.body_calls
                    assert disk_logical(tmp_path / "product.sqlite") == before
                    release.set()
                    one, two = await asyncio.gather(first, join)
                    assert one is two is first_result[0]
                    assert one.status == "held"
                    winner = await queued
                    assert observed.release_facts == [
                        {"outcome": "success", "locked": True, "owner_task_done": False}
                    ]
                    assert len(observed.body_calls) == 1
                    assert observed.timeline == ["owner-release", "mutation-body"]
                    after_mutation = disk_logical(tmp_path / "product.sqlite")
                    assert after_mutation != before
                    assert counts(tmp_path) == effects
                    if kind == "C1":
                        assert winner.to_tree() == {
                            "task_id": target,
                            "decision": "interrupted",
                        }
                    else:
                        assert (
                            winner.source_task_id == target
                            and winner.new_task_id != target
                        )
                    assert await control(product, target, kind, "b" * 32) == winner
                    assert disk_logical(tmp_path / "product.sqlite") == after_mutation
                    fresh = await product.resume()
                    assert product._current is not operation
                    assert (
                        fresh.status == "terminal"
                        and fresh.inspection.outcome == "completed"
                    )
                    assert counts(tmp_path) == {
                        "calls": 2 if kind == "C1" else 3,
                        "applications": 2 if kind == "C1" else 3,
                        "prepares": 2 if kind == "C1" else 3,
                        "providers": 2,
                    }
                    save_facts(
                        tmp_path,
                        "queued-positive-winner-facts.json",
                        {
                            "control": kind,
                            "owner_joined_by_identity": one is two,
                            "release_facts": observed.release_facts,
                            "actual_lock_waiter_observed": True,
                            "timeline_before_replay": observed.timeline[:2],
                            "actual_winner": winner.to_tree(),
                            "before_SQL": before,
                            "after_mutation_SQL": after_mutation,
                            "after_resume_SQL": disk_logical(
                                tmp_path / "product.sqlite"
                            ),
                            "before_effects": effects,
                            "after_effects": counts(tmp_path),
                            "qualification": "Actual C1 same-attempt saved receipt versus C2 explicitly authorized new attempt have intentionally different application counts. No duplicate unauthorized execution or healthy result-cache claim.",
                        },
                    )
            finally:
                release.set()
                await asyncio.gather(
                    *offered,
                    *([] if queued is None else [queued]),
                    return_exceptions=True,
                )

    asyncio.run(asyncio.wait_for(run(), 30))
