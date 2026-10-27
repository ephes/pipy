"""Typed historical-policy controls over an actual reporting-infrastructure hold.

Only invalid_result is produced here. Other reason/receipt combinations are
explicitly reconstructed controls, not claims about historical execution.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from test_product_session import profile, tools
from test_tool_fact2_reader_matrix import world
from tool_support import (
    CountedWire,
    InfrastructureFailureFirst,
    counts,
    reporting_faults,
)

from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolExecutor
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.inspection_read import round_facts
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.native.durable.tool_values import native_tool_output, receipt
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def logical(session):
    def capture():
        value = world(session._owner.storage)
        return OwnedJson(
            {
                "tasks": value["rows"],
                "documents": value["documents"],
                "entries": [row.to_tree() for row in value["entries"].values()],
                "conversations": value["conversations"],
            }
        )

    return session.read_on_line(capture)


def control_receipt(kind, original, call):
    if kind == "empty":
        return {}
    if kind == "opaque":
        return {"future_private": "C1_POLICY_PRIVATE"}
    outcome = ToolExecutionOutcome(
        ToolExecutor({}).error_result(call, "C1_POLICY_PRIVATE")
    )
    full = native_tool_output(
        outcome,
        datetime(2026, 10, 27, tzinfo=timezone.utc),
        interrupted={"reason": "interrupted", "attempts": None, "restarts": 0}
        if kind == "interrupted"
        else None,
    )
    saved = {
        "version": full["version"],
        "call": original["call"],
        "key": original["key"],
        "intent": original,
        "nativeToolOutput": full,
    }
    receipt(saved)  # Closed-shape control, not an observed effect receipt.
    if kind == "newer":
        saved["version"] = 99
    return saved


def install_policy_control(product, reason, receipt_kind):
    session = product._session

    def capture():
        value = world(session._owner.storage)
        child = next(row for row in value["rows"] if row["kind"] == "pi.tool")
        assert child["state"]["checkpoint"]["reason"] == "invalid_result"
        _, assistant, _, _ = round_facts(
            session._owner.storage, value["parent"], value["rows"]
        )
        return child, assistant.tool_calls[0]

    child, call = session.read_on_line(capture)
    original = child["state"]["checkpoint"]["intent"]
    frame = {
        "phase": "blocked",
        "version": 1,
        "intent": None if reason == "intent_none" else original,
        "reason": "incompatible" if reason == "intent_none" else reason,
    }
    if reason == "intent_none":
        frame["call"] = child["input"]
    saved = (
        None
        if receipt_kind == "absent"
        else control_receipt(receipt_kind, original, call)
    )

    def change(tx):
        if saved is not None:
            tx.doc(RECEIPT_DOC, task_id=child["id"]).update(saved)
        tx.set_task({**child, "state": {"status": "pending", "checkpoint": frame}})

    session.commit(change)
    return child["id"], original["key"]


async def check_retry(product, root, child_id, receipt_kind, before):
    if receipt_kind != "absent":
        original = await asyncio.to_thread(logical, product._session)
        with pytest.raises(ResolutionError, match="receipt_held_needs_repair"):
            await product.authorize_retry(child_id, nonce="f" * 32)
        assert await asyncio.to_thread(logical, product._session) == original
    else:
        winner = await product.authorize_retry(child_id, nonce="f" * 32)
        assert winner.source_task_id == child_id and winner.new_task_id != child_id
        assert winner == await product.authorize_retry(child_id, nonce="f" * 32)
        assert counts(root) == before
        result = await product.resume()
        assert result.status == "terminal" and result.inspection.outcome == "completed"
        assert counts(root) == {
            "calls": 3,
            "applications": 3,
            "prepares": 3,
            "providers": 2,
        }


@pytest.mark.parametrize(
    "reason", ["uncertain", "incompatible", "invalid_result", "intent_none"]
)
@pytest.mark.parametrize(
    "receipt_kind", ["absent", "empty", "normal", "interrupted", "opaque", "newer"]
)
def test_all_historical_blocked_origins_precede_receipts(
    tmp_path, reason, receipt_kind
):
    async def scenario():
        registry = tools(tmp_path, port_type=InfrastructureFailureFirst)
        path = tmp_path / "product.sqlite"
        async with await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=registry,
        ) as product:
            await product.submit("input")
            assert (await product.resume()).status == "held"
            child_id, old_key = await asyncio.to_thread(
                install_policy_control, product, reason, receipt_kind
            )
            assert reporting_faults(tmp_path) == [("keyed", old_key, 1, 1)]
            before = counts(tmp_path)
            assert before == {
                "calls": 1,
                "applications": 1,
                "prepares": 1,
                "providers": 1,
            }
            snapshot = await asyncio.to_thread(logical, product._session)
        (tmp_path / "policy-control.json").write_text(
            json.dumps(
                {
                    "actual_origin": "reporting infrastructure failure after one effect",
                    "reconstructed_reason": reason,
                    "reconstructed_receipt": receipt_kind,
                    "not_historical_execution_evidence": True,
                },
                indent=2,
            )
        )
        async with await open_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=registry,
        ) as product:
            result = await product.resume()
            assert result.status == "held"
            metadata = await product.inspect(child_id)
            assert metadata.reason == (
                "receipt_held_needs_repair"
                if receipt_kind != "absent"
                else "incompatible"
                if reason == "intent_none"
                else reason
            )
            assert "C1_POLICY_PRIVATE" not in repr(metadata)
            assert not any(
                message.tool_output is not None for message in result.completed.messages
            )
            assert await asyncio.to_thread(logical, product._session) == snapshot
            assert counts(tmp_path) == before
            await check_retry(product, tmp_path, child_id, receipt_kind, before)

    asyncio.run(asyncio.wait_for(scenario(), 20))
