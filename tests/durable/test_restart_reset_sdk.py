"""Actual cold SQLite parking controls; a fresh higher owner continues the work.

Release 1.5 removed same-task retry, so no Product or CLI reset exists.
"""

import asyncio
import math
import os

import pytest
from interrupted_cut_support import authority, counts, install_wire, raw
from restart_reset_sdk_support import child_row, open_product, snapshot, typed_counter
from test_tool_interrupted_sdk import (
    assert_terminal_once,
    authfree_repeated_terminal,
    final_tool,
    start_cut,
)

from pipy_harness.sdk import DurableProductError


@pytest.fixture(autouse=True)
def private_environment(tmp_path, monkeypatch):
    from pipy_harness import durable_cli

    previous = dict(os.environ)
    monkeypatch.setattr(
        durable_cli, "DurableProviderProfile", durable_cli.DurableProviderProfile
    )
    try:
        authority(tmp_path)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def test_cold_running_recovers_its_count_then_parks_until_a_higher_owner(tmp_path):
    start_cut(tmp_path, "progress-after")
    typed = typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == before["prepares"] == 1
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        try:
            running = await product.inspect(typed["id"])
            assert running.restart_count == 4 and not running.restart_parked
            held = await product.resume()
            assert held.status == "held"
            parked = await product.inspect(typed["id"])
            assert parked.restart_count == 5 and parked.restart_parked
            assert parked.restart_limit == 5
            parked_row = child_row(tmp_path)
            assert parked_row["state"]["status"] == "pending"
            assert counts(tmp_path) == before
        finally:
            await product.close()
        product = await open_product(tmp_path, restart_limit=math.inf)
        try:
            unlimited = await product.inspect(typed["id"])
            assert unlimited.restart_limit is None and not unlimited.restart_parked
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(run())
    assert_terminal_once(tmp_path, before)
    receipt = final_tool(tmp_path)["data"]["nativeToolReceipt"]
    # The streak was never reset: the interruption records it.
    assert receipt["nativeToolOutput"]["recovery"] == {
        "reason": "interrupted",
        "attempts": 1,
        "restarts": 5,
    }
    assert (
        len([r for r in raw(tmp_path)["entries"] if r["kind"] == "pi.tool-result"]) == 1
    )
    authfree_repeated_terminal(tmp_path)


@pytest.mark.parametrize(
    "count", [7.5, 2**53, 1e300], ids=["fraction", "large", "exponent"]
)
def test_saved_receipt_parks_and_a_higher_owner_preserves_first_winner(tmp_path, count):
    start_cut(tmp_path, "receipt-after")
    receipt = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    # Pending/counter control over a genuinely committed public receipt, not a
    # historical recovery claim. It avoids changing the saved attempt counters.
    target = typed_counter(tmp_path, count, pending=True)["id"]
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        try:
            graph = snapshot(tmp_path)
            inspection = await product.inspect(target)
            assert inspection.restart_count == count and inspection.restart_parked
            assert snapshot(tmp_path) == graph
            held = await product.resume()
            assert held.status == "held"
            assert counts(tmp_path) == before
            assert raw(tmp_path)["documents"]["pi.tool.receipt"][-1] == receipt
        finally:
            await product.close()
        product = await open_product(tmp_path, restart_limit=math.inf)
        try:
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(run())
    assert_terminal_once(tmp_path, before)
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": receipt}
    authfree_repeated_terminal(tmp_path)


def test_wrong_model_open_is_a_pure_refusal(tmp_path):
    start_cut(tmp_path, "effect")
    typed_counter(tmp_path, 5, pending=True)
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        graph = snapshot(tmp_path)
        with pytest.raises(DurableProductError) as refused:
            await open_product(tmp_path, wrong_model=True)
        assert refused.value.code == "incompatible"
        assert snapshot(tmp_path) == graph and counts(tmp_path) == before

    asyncio.run(run())


def test_cli_unlimited_inspection_then_receipt_first_resume(tmp_path):
    from restart_reset_sdk_support import cli

    start_cut(tmp_path, "receipt-after")
    target = typed_counter(tmp_path, 7.5, pending=True)["id"]
    receipt = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    before = counts(tmp_path)
    graph = snapshot(tmp_path)
    code, value, error = cli(
        tmp_path,
        "unlimited-inspect",
        "inspect",
        credentials=False,
        extra=("--task", str(target), "--restart-limit", "Infinity"),
    )
    assert code == 0 and not error
    assert value["tasks"][0]["restart_count"] == 7.5
    assert value["tasks"][0]["restart_limit"] is None
    assert value["tasks"][0]["restart_parked"] is False
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before
    code, value, _ = cli(
        tmp_path, "unlimited-resume", "resume", extra=("--restart-limit", "Infinity")
    )
    assert code == 0 and value["status"] == "terminal"
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": receipt}
    assert_terminal_once(tmp_path, before)
