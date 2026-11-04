"""Actual cold SQLite cuts: a built-in tool call at the restart limit ends itself.

Release 1.5 ends a `pi.tool` call that was cut off `restart_limit` times in a
row with an error result instead of running it again (bridge R15-2b). Typed
counter seeds extend one genuine SIGKILL cut to the limit; they are controls,
not historical producers.
"""

import asyncio
import math
import os

import pytest
from interrupted_cut_support import (
    authority,
    counts,
    install_wire,
    provider_bodies,
    raw,
)
from restart_limit_sdk_support import child_row, open_product, snapshot, typed_counter
from test_tool_interrupted_sdk import (
    assert_terminal_once,
    authfree_repeated_terminal,
    final_tool,
    start_cut,
)

from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.sdk import DurableProductError

LIMIT_TEXT = "Tool execution was interrupted 5 times in a row; it is not run again."
LIMIT_ERROR = {"message": LIMIT_TEXT, "detail": {"reason": "restart_limit"}}


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


def tool_task(root):
    return next(row for row in raw(root)["tasks"] if row["kind"] == "pi.tool")


async def resumed(root, **options):
    product = await open_product(root, **options)
    try:
        return await product.resume()
    finally:
        await product.close()


def assert_limit_ending(root, *, attempts):
    entry = final_tool(root)
    full = entry["data"]["nativeToolReceipt"]["nativeToolOutput"]
    assert full["version"] == 2 and full["ending"] == "interrupted"
    assert full["recovery"] == {
        "reason": "restart_limit",
        "attempts": attempts,
        "restarts": 5,
    }
    decoded = decode_canonical(full["outcome"])
    assert decoded.result.is_error and not decoded.result.terminate
    assert decoded.interruption.value == "settled" and decoded.execution_seconds is None
    text = decode_canonical(entry["model"][0]).content.value
    assert text.endswith(LIMIT_TEXT + "\n</harness>")
    child = tool_task(root)
    assert child["state"]["status"] == "terminal" and "restarts" not in child
    assert child["state"]["outcome"]["status"] == "failed"
    assert child["state"]["outcome"]["error"] == LIMIT_ERROR
    # The model sees the call's own restart_limit result and answers.
    body = provider_bodies(root)[-1]
    assert LIMIT_TEXT in next(
        row["content"] for row in body["messages"] if row["role"] == "tool"
    )
    return entry


def test_unsafe_execute_at_the_limit_ends_with_its_retained_output(tmp_path):
    start_cut(tmp_path, "progress-after")
    typed = typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == before["prepares"] == 1
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        try:
            running = await product.inspect(typed["id"])
            assert running.restart_count == 4 and running.reason is None
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(run())
    assert_terminal_once(tmp_path, before)
    assert_limit_ending(tmp_path, attempts=1)
    authfree_repeated_terminal(tmp_path)


@pytest.mark.parametrize("count", [3, 4])
def test_safe_replay_runs_again_only_below_the_limit(tmp_path, count):
    start_cut(tmp_path, "effect", replay="safe")
    typed_counter(tmp_path, count)
    before = counts(tmp_path)
    install_wire(tmp_path)
    # The unchanged safe catalog makes this a genuine replay decision.
    result = asyncio.run(resumed(tmp_path, replay="safe"))
    assert result.status == "terminal" and result.inspection.outcome == "completed"
    if count == 3:
        assert_terminal_once(tmp_path, before, reissued=True)
        full = final_tool(tmp_path)["data"]["nativeToolReceipt"]["nativeToolOutput"]
        assert full.get("ending", "completed") == "completed"
    else:
        # No replay at the limit: the tool ran once, before the cut.
        assert_terminal_once(tmp_path, before)
        assert_limit_ending(tmp_path, attempts=1)


def test_call_phase_at_the_limit_ends_without_resolving_the_tool(tmp_path):
    start_cut(tmp_path, "child")
    assert child_row(tmp_path)["state"]["checkpoint"] == {"phase": "call"}
    typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == before["prepares"] == 0
    install_wire(tmp_path)
    result = asyncio.run(resumed(tmp_path))
    assert result.status == "terminal" and result.inspection.outcome == "completed"
    after = counts(tmp_path)
    # Neither preparation nor the tool ran; only the follow-up request was sent.
    assert {k: after[k] for k in ("calls", "applications", "prepares")} == {
        "calls": 0,
        "applications": 0,
        "prepares": 0,
    }
    assert after["providers"] == before["providers"] + 1
    entry = assert_limit_ending(tmp_path, attempts=None)
    receipt = entry["data"]["nativeToolReceipt"]
    assert receipt["intent"] is None and receipt["version"] == 2
    assert (
        decode_canonical(receipt["nativeToolOutput"]["outcome"]).result.content.value
        == ""
    )
    authfree_repeated_terminal(tmp_path)


def test_a_raised_owner_limit_lets_the_call_run_its_recovery(tmp_path):
    start_cut(tmp_path, "progress-after")
    typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    install_wire(tmp_path)
    result = asyncio.run(resumed(tmp_path, restart_limit=6))
    assert result.status == "terminal" and result.inspection.outcome == "completed"
    assert_terminal_once(tmp_path, before)
    full = final_tool(tmp_path)["data"]["nativeToolReceipt"]["nativeToolOutput"]
    # Below the limit the unsafe call ends interrupted, recording its streak.
    assert full["recovery"] == {"reason": "interrupted", "attempts": 1, "restarts": 5}


@pytest.mark.parametrize("cut", ["effect", "child"])
def test_changed_catalog_admits_a_call_the_limit_ends(tmp_path, cut):
    # An unrelated tool is removed: a safe replay or a pre-intent call would need
    # the current catalog, but at the limit neither runs again.
    start_cut(tmp_path, cut, replay="safe")
    typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    install_wire(tmp_path)
    result = asyncio.run(resumed(tmp_path, current="unrelated_removed", replay="safe"))
    assert result.status == "terminal" and result.inspection.outcome == "completed"
    after = counts(tmp_path)
    assert after["calls"] == before["calls"]
    assert after["applications"] == before["applications"]
    assert after["prepares"] == before["prepares"]
    assert_limit_ending(tmp_path, attempts=1 if cut == "effect" else None)


@pytest.mark.parametrize("count", [3, 2.5])
def test_changed_catalog_still_refuses_below_the_limit_or_an_unrecorded_count(
    tmp_path, count
):
    start_cut(tmp_path, "effect", replay="safe")
    typed_counter(tmp_path, count)
    before = counts(tmp_path)
    graph = snapshot(tmp_path)
    install_wire(tmp_path)
    with pytest.raises(DurableProductError) as refused:
        asyncio.run(resumed(tmp_path, current="unrelated_removed", replay="safe"))
    assert refused.value.code == "incompatible"
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before


@pytest.mark.parametrize("count", [5, 7.5], ids=["limit", "fraction"])
def test_changed_catalog_settles_a_valid_receipt_at_any_count(tmp_path, count):
    start_cut(tmp_path, "receipt-after", replay="safe")
    receipt = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    typed_counter(tmp_path, count, pending=True)
    before = counts(tmp_path)
    install_wire(tmp_path)
    result = asyncio.run(resumed(tmp_path, current="unrelated_removed", replay="safe"))
    assert result.status == "terminal" and result.inspection.outcome == "completed"
    assert_terminal_once(tmp_path, before)
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": receipt}


def test_changed_catalog_refuses_a_cold_count_the_ending_could_not_record(tmp_path):
    # Recovery would raise the stored safe maximum past the safe integer range.
    start_cut(tmp_path, "child", replay="safe")
    typed_counter(tmp_path, 2**53 - 1)
    before = counts(tmp_path)
    graph = snapshot(tmp_path)
    install_wire(tmp_path)
    with pytest.raises(DurableProductError) as refused:
        asyncio.run(resumed(tmp_path, current="unrelated_removed", replay="safe"))
    assert refused.value.code == "incompatible"
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before


@pytest.mark.parametrize(
    "count", [5, 7.5, 2**53, 1e300], ids=["limit", "fraction", "large", "exponent"]
)
def test_saved_receipt_at_the_limit_settles_its_first_winner(tmp_path, count):
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
            assert inspection.restart_count == count and inspection.reason is None
            assert snapshot(tmp_path) == graph
            # Settle runs no tool again: the committed receipt is the result.
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
    from restart_limit_sdk_support import cli

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
    assert "restart_parked" not in value["tasks"][0]
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before
    code, value, _ = cli(
        tmp_path, "unlimited-resume", "resume", extra=("--restart-limit", "Infinity")
    )
    assert code == 0 and value["status"] == "terminal"
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": receipt}
    assert_terminal_once(tmp_path, before)


def test_cli_resume_at_the_limit_reports_the_completed_run(tmp_path):
    from restart_limit_sdk_support import cli

    start_cut(tmp_path, "progress-after")
    typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    code, value, error = cli(tmp_path, "limit-resume", "resume")
    assert code == 0 and not error and value["status"] == "terminal"
    assert_terminal_once(tmp_path, before)
    assert_limit_ending(tmp_path, attempts=1)
    outcome = next(
        row for row in value["completed"]["outcomes"] if row["family"] == "model_tool"
    )
    assert outcome["status"] == "failed"


def test_unlimited_owner_never_reaches_the_limit(tmp_path):
    start_cut(tmp_path, "progress-after")
    typed_counter(tmp_path, 10**6)
    before = counts(tmp_path)
    install_wire(tmp_path)
    result = asyncio.run(resumed(tmp_path, restart_limit=math.inf))
    assert result.status == "terminal"
    assert_terminal_once(tmp_path, before)
    full = final_tool(tmp_path)["data"]["nativeToolReceipt"]["nativeToolOutput"]
    assert full["recovery"]["reason"] == "interrupted"
