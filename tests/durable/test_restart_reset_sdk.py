"""Actual cold SQLite public controls; same-task reset does not execute work."""

import asyncio
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


def test_cold_running_reset_requires_recovery_then_explicit_resume(tmp_path):
    start_cut(tmp_path, "progress-after")
    typed = typed_counter(tmp_path, 4)
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == before["prepares"] == 1
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        try:
            graph = snapshot(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.retry_task(typed["id"])
            assert refused.value.code == "unavailable"
            assert snapshot(tmp_path) == graph
            assert counts(tmp_path) == before
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
            reset = await product.retry_task(typed["id"])
            assert reset.to_tree() == {"task_id": typed["id"], "reset": True}
            expected = dict(parked_row)
            del expected["restarts"]
            assert child_row(tmp_path) == expected
            graph = snapshot(tmp_path)
            assert (await product.retry_task(typed["id"])).to_tree() == {
                "task_id": typed["id"],
                "reset": False,
            }
            assert snapshot(tmp_path) == graph
            assert counts(tmp_path) == before
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(run())
    assert_terminal_once(tmp_path, before)
    receipt = final_tool(tmp_path)["data"]["nativeToolReceipt"]
    assert receipt["nativeToolOutput"]["recovery"] == {
        "reason": "interrupted",
        "attempts": 1,
        "restarts": None,
    }
    assert (
        len([r for r in raw(tmp_path)["entries"] if r["kind"] == "pi.tool-result"]) == 1
    )
    authfree_repeated_terminal(tmp_path)


def test_distinct_cli_processes_reset_ack_is_not_resume(tmp_path):
    from restart_reset_sdk_support import cli

    start_cut(tmp_path, "effect")
    target = typed_counter(tmp_path, 4)["id"]
    before = counts(tmp_path)
    graph = snapshot(tmp_path)
    code, value, error = cli(tmp_path, "cold-refusal", "retry-task", target=target)
    assert code == 1 and value is None and "unavailable" in error
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before
    code, value, _ = cli(tmp_path, "recover-park", "resume")
    assert code == 3 and value["status"] == "held"
    assert child_row(tmp_path)["restarts"] == 5
    assert counts(tmp_path) == before
    code, value, error = cli(tmp_path, "reset", "retry-task", target=target)
    assert code == 0 and not error
    assert value == {
        "format": "pipy-durable-output",
        "version": 1,
        "operation": "retry-task",
        "task_id": target,
        "reset": True,
    }
    assert "restarts" not in child_row(tmp_path)
    assert counts(tmp_path) == before
    graph = snapshot(tmp_path)
    code, value, _ = cli(tmp_path, "duplicate", "retry-task", target=target)
    assert code == 0 and value["reset"] is False
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before
    code, value, _ = cli(tmp_path, "explicit-resume", "resume")
    assert code == 0 and value["status"] == "terminal"
    assert_terminal_once(tmp_path, before)
    full = final_tool(tmp_path)["data"]["nativeToolReceipt"]["nativeToolOutput"]
    assert full["recovery"]["restarts"] is None
    authfree_repeated_terminal(tmp_path)


@pytest.mark.parametrize(
    "count", [7.5, 2**53, 1e300], ids=["fraction", "large", "exponent"]
)
def test_saved_receipt_parks_but_reset_preserves_first_winner(tmp_path, count):
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
            assert (await product.retry_task(target)).reset
            assert counts(tmp_path) == before
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


@pytest.mark.parametrize(
    "current,with_profile,expected",
    [
        ("same", False, "profile_required"),
        ("missing", True, "incompatible"),
        ("declaration", True, "incompatible"),
        ("version", True, "incompatible"),
    ],
)
def test_reset_profile_and_catalog_fences_are_revision_free(
    tmp_path, current, with_profile, expected
):
    start_cut(tmp_path, "effect")
    target = typed_counter(tmp_path, 5, pending=True)["id"]
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        product = await open_product(
            tmp_path, current=current, with_profile=with_profile
        )
        try:
            graph = snapshot(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.retry_task(target)
            assert refused.value.code == expected
            assert snapshot(tmp_path) == graph and counts(tmp_path) == before
        finally:
            await product.close()

    asyncio.run(run())


def test_selected_root_child_and_invalid_target_fences(tmp_path):
    from pipy_harness.sdk import DurableRunSelection

    start_cut(tmp_path, "effect")
    row = typed_counter(tmp_path, 5, pending=True)
    root = row["owner"]
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        try:
            graph = snapshot(tmp_path)
            assert (await product.retry_task(root)).to_tree() == {
                "task_id": root,
                "reset": False,
            }
            assert snapshot(tmp_path) == graph
            for target, options, expected in (
                (True, {}, "invalid_request"),
                (-1, {}, "invalid_request"),
                (999999, {}, "incompatible"),
                (
                    row["id"],
                    {"selection": DurableRunSelection(root, "a" * 32)},
                    "incompatible",
                ),
                (
                    row["id"],
                    {"selection": DurableRunSelection(row["id"], "b" * 32)},
                    "incompatible",
                ),
            ):
                with pytest.raises(DurableProductError) as refused:
                    await product.retry_task(target, **options)
                assert refused.value.code == expected
                assert snapshot(tmp_path) == graph and counts(tmp_path) == before
            assert (await product.retry_task(row["id"])).reset
            assert counts(tmp_path) == before
        finally:
            await product.close()

    asyncio.run(run())


def test_reset_cancelled_waiter_and_close_keep_real_commit_and_path_owned(tmp_path):
    import threading

    from test_tool_lifecycle import independent_opener

    start_cut(tmp_path, "effect")
    target = typed_counter(tmp_path, 5, pending=True)["id"]
    before = counts(tmp_path)
    install_wire(tmp_path)
    entered, release, callback_exited = (threading.Event() for _ in range(3))

    async def run():
        product = await open_product(tmp_path)
        assert (await product.resume()).status == "held"

        def publication(commit):
            for change in commit.changes:
                if not hasattr(change, "to_tree"):
                    continue
                tree = change.to_tree()
                if (
                    tree["type"] == "task"
                    and tree["value"]["id"] == target
                    and "restarts" not in tree["value"]
                ):
                    entered.set()
                    try:
                        assert release.wait(15)
                    finally:
                        callback_exited.set()

        unsubscribe = product._session.subscribe_commits(publication)
        try:
            resetting = asyncio.create_task(product.retry_task(target))
            assert await asyncio.to_thread(entered.wait, 5)
            # The actual COMMIT already removed the streak. The callback and
            # physical Session ticket still own its accepted acknowledgement.
            assert "restarts" not in child_row(tmp_path)
            resetting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await resetting
            queued = asyncio.create_task(product.retry_task(target))
            closing = asyncio.create_task(product.close())
            await asyncio.sleep(0)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert not callback_exited.is_set()
            await asyncio.to_thread(
                independent_opener,
                tmp_path / "session.sqlite",
                tmp_path,
                "reset-callback-held",
                "refused",
            )
            assert not callback_exited.is_set()
            assert counts(tmp_path) == before
            release.set()
            await asyncio.wait_for(product.close(), 5)
            assert callback_exited.is_set()
            with pytest.raises(DurableProductError) as refused:
                await queued
            assert refused.value.code == "closed"
            await asyncio.to_thread(
                independent_opener,
                tmp_path / "session.sqlite",
                tmp_path,
                "reset-callback-exited",
                "opened",
            )
        finally:
            release.set()
            unsubscribe()
            await product.close()

        reopened = await open_product(tmp_path)
        try:
            graph = snapshot(tmp_path)
            assert not (await reopened.retry_task(target)).reset
            assert snapshot(tmp_path) == graph and counts(tmp_path) == before
            result = await reopened.resume()
            assert result.status == "terminal"
        finally:
            await reopened.close()

    asyncio.run(asyncio.wait_for(run(), 25))
    assert_terminal_once(tmp_path, before)


def test_foreign_live_graph_refuses_reset_without_mutation(tmp_path):
    from pipy_harness.native.durable.tasks import define_task

    start_cut(tmp_path, "effect")
    target = typed_counter(tmp_path, 5, pending=True)["id"]
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def forbidden(*args):
        raise AssertionError("No foreign dispatch during reset")

    foreign = define_task(
        "fixture.reset-foreign",
        1,
        lambda _: {"phase": "start"},
        {"start": forbidden},
        forbidden,
    )

    async def run():
        product = await open_product(tmp_path)
        try:

            def add(tx):
                conversation = tx.create_conversation({"kind": "ownerless"})
                return tx.create_task(
                    foreign,
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation.id,
                )

            # Typed kernel graph control, preserving the genuine public header.
            await asyncio.to_thread(product._session.commit, add)
            graph = snapshot(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.retry_task(target)
            assert refused.value.code == "foreign_live_task"
            assert snapshot(tmp_path) == graph and counts(tmp_path) == before
        finally:
            await product.close()

    asyncio.run(run())


def test_wrong_model_and_cli_missing_profile_are_pure_refusals(tmp_path):
    from restart_reset_sdk_support import cli

    start_cut(tmp_path, "effect")
    target = typed_counter(tmp_path, 5, pending=True)["id"]
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        graph = snapshot(tmp_path)
        with pytest.raises(DurableProductError) as refused:
            await open_product(tmp_path, wrong_model=True)
        assert refused.value.code == "incompatible"
        assert snapshot(tmp_path) == graph and counts(tmp_path) == before

    asyncio.run(run())
    graph = snapshot(tmp_path)
    code, value, error = cli(
        tmp_path, "profile-required", "retry-task", target=target, credentials=False
    )
    assert code == 2 and value is None and "invalid_request" in error
    assert snapshot(tmp_path) == graph and counts(tmp_path) == before


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
