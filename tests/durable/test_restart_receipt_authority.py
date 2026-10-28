"""A host ceiling delays dispatch but never changes an acknowledged decision."""

import asyncio

from adjudication_sdk_support import logical
from test_tool_adjudication import held_product
from tool_support import counts

from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.sdk import open_durable_product_session


def test_c2_decision_and_cold_nonce_replay_remain_available_while_parked(tmp_path):
    async def run():
        async with held_product(tmp_path) as (product, admission, target):

            def seed(tx):
                row = tx.task(target).to_tree()
                tx.set_task({**row, "restarts": 5})

            # Typed streak control on a genuine infrastructure hold. The C2
            # operation is still the actual public same-attempt authority.
            await owned_call(lambda: product._session.commit(seed))
            before = counts(tmp_path)
            assert (await product.inspect(target)).restart_parked
            ack = await product.adjudicate_interrupted(target, nonce="d" * 32)
            assert ack.decision == "interrupted"
            saved = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    target,
                )
            )
            assert saved["value"]["nativeToolOutput"]["recovery"]["restarts"] == 5
            graph = await owned_call(lambda: logical(product._session))
            assert await product.adjudicate_interrupted(target, nonce="d" * 32) == ack
            assert await owned_call(lambda: logical(product._session)) == graph
            assert (await product.resume()).status == "held"
            assert counts(tmp_path) == before
            assert (await product.inspect(target)).restart_parked
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as cold:
            graph = await owned_call(lambda: logical(cold._session))
            assert await cold.adjudicate_interrupted(target, nonce="d" * 32) == ack
            assert await owned_call(lambda: logical(cold._session)) == graph
            assert counts(tmp_path) == before
            assert (
                await cold._read(
                    lambda: peek_document(
                        cold._session._owner.storage,
                        RECEIPT_DOC,
                        admission.conversation_id,
                        target,
                    )
                )
                == saved
            )

    asyncio.run(asyncio.wait_for(run(), 20))
