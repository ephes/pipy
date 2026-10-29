"""A genuine C2 winner stays authoritative across private orphan refusal."""

import asyncio
import json

import pytest
from adjudication_sdk_support import logical
from test_tool_adjudication import held_product
from tool_support import counts

from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.sdk import open_durable_product_session


def test_actual_c2_receipt_nonce_and_cold_winner_survive_orphan_refusal(tmp_path):
    async def scenario():
        nonce = "b" * 32
        evidence = {}
        async with held_product(tmp_path) as (product, admission, task_id):
            effects = counts(tmp_path)
            assert effects == {
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
                        admission.conversation_id,
                        task_id,
                    )
                )
                is None
            )
            ack = await product.adjudicate_interrupted(task_id, nonce=nonce)
            assert ack.decision == "interrupted"
            saved = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    task_id,
                )
            )
            assert saved["value"]["version"] == 2
            assert saved["value"]["nativeToolOutput"]["ending"] == "interrupted"
            scheduler = product._scheduler
            assert scheduler is not None
            # Real pause joins reservation/invocation retirement. This is a
            # private refusal control, not a new Product orphan API.
            await scheduler.apause()
            with scheduler._guard:
                assert scheduler._draining is False
                assert scheduler._invocation is None
                assert scheduler._enabled is False
            before = await owned_call(lambda: logical(product._session))
            assert len(before) == 9 and "document_revisions" in before
            with pytest.raises(UnsupportedTaskExecution, match="Built-in"):
                await scheduler.aorphan_missing_definition(task_id)
            after = await owned_call(lambda: logical(product._session))
            assert after == before
            assert counts(tmp_path) == effects
            assert await product.adjudicate_interrupted(task_id, nonce=nonce) == ack
            assert await owned_call(lambda: logical(product._session)) == before
            evidence.update(
                target=task_id,
                conversation=admission.conversation_id,
                nonce=nonce,
                ack=ack.to_tree(),
                receipt=saved,
                effects=effects,
                before_refusal=before,
                after_refusal=after,
            )
        # The prior owner actually closed before the fresh lease acquisition.
        # Empty tools and no provider profile cannot authorize a new attempt;
        # the existing same-nonce receipt remains the winner.
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as cold:
            before = await owned_call(lambda: logical(cold._session))
            assert await cold.adjudicate_interrupted(task_id, nonce=nonce) == ack
            after = await owned_call(lambda: logical(cold._session))
            assert after == before
            assert counts(tmp_path) == effects
            assert (
                await cold._read(
                    lambda: peek_document(
                        cold._session._owner.storage,
                        RECEIPT_DOC,
                        admission.conversation_id,
                        task_id,
                    )
                )
                == saved
            )
            evidence.update(cold_before=before, cold_after=after)
        (tmp_path / "orphan-authority-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    asyncio.run(asyncio.wait_for(scenario(), 20))
