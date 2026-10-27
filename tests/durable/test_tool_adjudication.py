"""Explicit same-attempt decisions over real public product holds."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from adjudication_sdk_support import logical
from test_product_session import profile, tools
from test_tool_recovery_historical_holds import install_policy_control
from tool_support import CountedWire, InfrastructureFailureFirst, counts

from pipy_harness.native.durable import adjudication
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.records import TaskId
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.sdk import (
    DurableProductError,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def test_actual_infrastructure_hold_decision_then_next_answer_and_cold_nonce(tmp_path):
    registry = tools(tmp_path, port_type=InfrastructureFailureFirst)
    wire, path = CountedWire(tmp_path), tmp_path / "product.sqlite"

    async def scenario():
        async with await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            admitted = await product.submit("input")
            held = await product.resume()
            assert held.status == "held"
            rows = await product.inspect()
            child = next(
                t
                for t in rows.tasks
                if t.family == "model_tool" and t.phase == "blocked"
            )
            task_id = child.task_id
            before = counts(tmp_path)
            assert before == {
                "calls": 1,
                "applications": 1,
                "prepares": 1,
                "providers": 1,
            }
            assert (
                await product._read(
                    lambda: peek_document(
                        product._session._owner.storage,
                        RECEIPT_DOC,
                        admitted.conversation_id,
                        task_id,
                    )
                )
                is None
            )
            decision = await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert decision.to_tree() == {"task_id": task_id, "decision": "interrupted"}
            assert counts(tmp_path) == before
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                == decision
            )
            with pytest.raises(DurableProductError, match="conflict"):
                await product.adjudicate_interrupted(task_id + 1, nonce="a" * 32)
            result = await product.resume()
            assert result.status == "terminal", result.inspection
            assert result.inspection.outcome == "completed"
            assert counts(tmp_path) == {
                "calls": 2,
                "applications": 2,
                "prepares": 2,
                "providers": 2,
            }
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                == decision
            )
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as product:
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                == decision
            )
            with pytest.raises(DurableProductError, match="profile_required"):
                await product.adjudicate_interrupted(task_id, nonce="b" * 32)

    asyncio.run(asyncio.wait_for(scenario(), 20))


@asynccontextmanager
async def held_product(root):
    registry = tools(root, port_type=InfrastructureFailureFirst)
    async with await create_durable_product_session(
        root / "product.sqlite",
        workspace=root,
        profile=profile(root, CountedWire(root)),
        tools=registry,
    ) as product:
        admission = await product.submit("input")
        assert (await product.resume()).status == "held"
        child = next(t for t in (await product.inspect()).tasks if t.phase == "blocked")
        yield product, admission, child.task_id


@pytest.mark.parametrize(
    "reason", ["uncertain", "incompatible", "invalid_result", "intent_none"]
)
@pytest.mark.parametrize(
    "kind", ["absent", "empty", "normal", "interrupted", "opaque", "newer"]
)
def test_typed_receipt_policy_over_real_reporting_hold(tmp_path, reason, kind):
    """Only the infrastructure hold is produced; reason/receipt variants are controls."""

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            actual_id, _ = await owned_call(
                lambda: install_policy_control(product, reason, kind)
            )
            assert actual_id == task_id
            before = await owned_call(lambda: logical(product._session))
            original = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    task_id,
                )
            )
            error = (
                "receipt_held_needs_repair"
                if kind in ("opaque", "newer")
                else "not_supported"
                if reason == "intent_none" and kind in ("absent", "empty")
                else None
            )
            if error:
                with pytest.raises(DurableProductError) as refused:
                    await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                assert refused.value.code == error
                assert await owned_call(lambda: logical(product._session)) == before
            else:
                acknowledgement = await product.adjudicate_interrupted(
                    task_id, nonce="a" * 32
                )
                assert acknowledgement.decision == (
                    "receipt" if kind in ("normal", "interrupted") else "interrupted"
                )
                saved, audit = await product._read(
                    lambda: (
                        peek_document(
                            product._session._owner.storage,
                            RECEIPT_DOC,
                            admission.conversation_id,
                            task_id,
                        ),
                        peek_document(
                            product._session._owner.storage,
                            adjudication.AUDIT,
                            admission.conversation_id,
                            key="a" * 32,
                        ),
                    )
                )
                assert audit["value"]["receipt"][
                    "identity"
                ] == adjudication.document_identity(saved)
                if kind in ("normal", "interrupted"):
                    assert saved == original
                assert (
                    await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                ) == acknowledgement
                with pytest.raises(DurableProductError) as refused:
                    await product.adjudicate_interrupted(task_id, nonce="b" * 32)
                assert refused.value.code == "not_supported"
                assert (await product.resume()).status == "terminal"
                assert (
                    await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                ) == acknowledgement

    asyncio.run(asyncio.wait_for(scenario(), 20))


@pytest.mark.parametrize("kind", ["normal", "interrupted"])
def test_receipt_winner_ignores_invalid_restart_and_opaque_progress(tmp_path, kind):
    from pipy_harness.native.durable.tool_execution import PROGRESS_DOC

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            await owned_call(
                lambda: install_policy_control(product, "intent_none", kind)
            )

            def poison(tx):
                row = tx.task(task_id).to_tree()
                tx.doc(PROGRESS_DOC, task_id=task_id).clear()
                tx.doc(PROGRESS_DOC, task_id=task_id).update(
                    {"opaque": "UNRELATED_PRIVATE"}
                )
                tx.set_task({**row, "restarts": 1.5})

            await owned_call(lambda: product._session.commit(poison))
            saved = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    task_id,
                )
            )
            result = await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert result.decision == "receipt"
            assert (
                await product._read(
                    lambda: peek_document(
                        product._session._owner.storage,
                        RECEIPT_DOC,
                        admission.conversation_id,
                        task_id,
                    )
                )
                == saved
            )
            row = await product._read(
                lambda: product._session._owner.storage.task(TaskId(task_id)).to_tree()
            )
            assert row["restarts"] == 1.5
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32) == result
            )

    asyncio.run(asyncio.wait_for(scenario(), 20))


@pytest.mark.parametrize("field", ["abortRequested", "background", "abandonOnRestart"])
def test_parent_control_refuses_before_any_document_or_task_write(tmp_path, field):
    """Kernel controls over the real round; no public abort API is implied."""

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            await product._scheduler.apause()

            def change(tx):
                parent = tx.task(admission.generation_task_id).to_tree()
                assert parent["state"]["status"] == "waiting"
                assert task_id in parent["state"]["on"]
                tx.set_task({**parent, field: True})

            await owned_call(lambda: product._session.commit(change))
            before = await owned_call(lambda: logical(product._session))
            effects = counts(tmp_path)
            with pytest.raises(DurableProductError) as error:
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert error.value.code == "incompatible"
            assert await owned_call(lambda: logical(product._session)) == before
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))
