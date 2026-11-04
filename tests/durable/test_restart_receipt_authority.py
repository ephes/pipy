"""The restart limit never changes an acknowledged decision or its receipt."""

import asyncio
import sqlite3
from contextlib import closing

from adjudication_sdk_support import logical
from test_tool_adjudication import held_product
from tool_support import counts

from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.sdk import open_durable_product_session


def keyed_calls(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return db.execute("SELECT COUNT(*) FROM calls WHERE name='keyed'").fetchone()[0]


def test_c2_decision_settles_unchanged_at_the_limit_and_replays_cold(tmp_path):
    async def run():
        async with held_product(tmp_path) as (product, admission, target):

            def seed(tx):
                row = tx.task(target).to_tree()
                tx.set_task({**row, "restarts": 5})

            # Typed streak control on a genuine infrastructure hold. The C2
            # operation is still the actual public same-attempt authority.
            await owned_call(lambda: product._session.commit(seed))
            before = counts(tmp_path)
            held = await product.inspect(target)
            assert held.restart_count == 5 and held.reason is not None
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
            assert saved["value"]["nativeToolOutput"]["recovery"] == {
                "reason": "interrupted",
                "attempts": saved["value"]["nativeToolOutput"]["recovery"]["attempts"],
                "restarts": 5,
            }
            graph = await owned_call(lambda: logical(product._session))
            assert await product.adjudicate_interrupted(target, nonce="d" * 32) == ack
            assert await owned_call(lambda: logical(product._session)) == graph
            # The decided call settles its receipt at the limit: settlement runs
            # no tool again, and the run continues to its answer.
            result = await product.resume()
            assert result.status == "terminal"
            assert result.inspection.outcome == "completed"
            after = counts(tmp_path)
            # Only the round's other call ran; the decided one never again.
            assert keyed_calls(tmp_path) == 1
            assert after["calls"] == before["calls"] + 1
            settled = await product.inspect(target)
            assert settled.status == "terminal" and settled.outcome == "failed"
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as cold:
            graph = await owned_call(lambda: logical(cold._session))
            assert await cold.adjudicate_interrupted(target, nonce="d" * 32) == ack
            assert await owned_call(lambda: logical(cold._session)) == graph
            assert counts(tmp_path) == after

    asyncio.run(asyncio.wait_for(run(), 20))
