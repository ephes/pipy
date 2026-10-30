"""A-prime Broad witnesses: real authority, SQLite cuts and owned lifetimes.

Bound against the immutable first Core/CLI SUT; no execution is claimed.
The labelled failed-ACK/real-watch cleanup and local-handoff error barriers live
in the retained Core module, rather than a duplicated Broad state-machine test.
"""

import asyncio
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from adjudication_sdk_support import disk_logical
from generation_support import Wire
from interrupted_cut_support import authority, counts, install_wire
from product_retry_continuation_broad_support import (
    RETRY_CLI,
    FinalizerWire,
    FirstCommitGate,
    caller_turn,
    entered,
)
from restart_reset_sdk_support import child_row, open_product, snapshot
from sqlite_child_support import Child
from test_product_session import profile
from test_tool_interrupted_sdk import assert_terminal_once, start_cut
from test_tool_lifecycle import independent_opener

from pipy_harness import durable_product_api as product_api
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.records import TaskId, task
from pipy_harness.sdk import (
    DurableProductError,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("refusal", ["cold-running", "nonmember"])
def test_preack_target_refusal_coalesces_real_own_selection_fallback(
    tmp_path, monkeypatch, refusal
):
    authority(tmp_path)
    start_cut(tmp_path, "effect")
    target = child_row(tmp_path)["id"] if refusal == "cold-running" else 999999
    before = counts(tmp_path)
    install_wire(tmp_path)

    async def run():
        product = await open_product(tmp_path)
        gate = FirstCommitGate(monkeypatch, product._session)
        offers = []
        retry = None
        try:
            graph = snapshot(tmp_path)
            retry = asyncio.create_task(product.retry_task(target))
            await entered(gate.entered)
            offers = [asyncio.create_task(product.resume()) for _ in range(3)]
            await caller_turn()
            offers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await offers[0]
            assert not retry.done() and all(not x.done() for x in offers[1:])
            assert counts(tmp_path) == before and snapshot(tmp_path) == graph
            gate.release.set()
            with pytest.raises(DurableProductError) as error:
                await retry
            assert error.value.code == (
                "unavailable" if refusal == "cold-running" else "incompatible"
            )
            first, second = await asyncio.gather(*offers[1:])
            assert first is second
            assert (
                first.status == "terminal" and first.inspection.outcome == "completed"
            )
            assert gate.exited.is_set()
            after_fallback = counts(tmp_path)
            assert after_fallback["prepares"] == before["prepares"] == 1
            assert after_fallback["calls"] == before["calls"]
            assert after_fallback["applications"] == before["applications"]
            assert after_fallback["providers"] == 2

            # A later public call performs actual metadata reads rather than
            # retrieving the handoff's completed healthy result as a cache.
            actual = product_api.ainspect_task
            reads = []

            async def inspect(*args, **kwargs):
                reads.append(args[1])
                return await actual(*args, **kwargs)

            monkeypatch.setattr(product_api, "ainspect_task", inspect)
            stable = snapshot(tmp_path)
            again = await product.resume()
            assert reads and again.status == "terminal"
            assert snapshot(tmp_path) == stable
            assert counts(tmp_path) == after_fallback
        finally:
            gate.release.set()
            await asyncio.gather(
                *offers, *([] if retry is None else [retry]), return_exceptions=True
            )
            await product.close()

    asyncio.run(asyncio.wait_for(run(), 25))
    assert_terminal_once(tmp_path, before)


@pytest.mark.parametrize("cut", ["before", "after-COMMIT"])
def test_real_reset_transaction_rollback_differs_from_lost_ack(
    tmp_path, monkeypatch, cut
):
    path = tmp_path / "session.sqlite"
    wire = Wire()
    reached = threading.Event()

    async def run():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        admission = await product.submit("input")
        target = admission.generation_task_id

        def parked(tx):
            row = tx.task(target).to_tree()
            row["restarts"] = 5
            task(row)
            tx.set_task(row)
            return row

        # Typed policy control, not a claim of repeated physical crashes.
        original = await asyncio.to_thread(product._session.commit, parked)
        before = disk_logical(path)

        def fault(connection):
            rows = [
                sqlite._decode(row[0])
                for row in connection.execute("SELECT record FROM tasks")
            ]
            if any(row["id"] == target and "restarts" not in row for row in rows):
                reached.set()
                if cut == "before":
                    raise sqlite3.OperationalError("private reset precommit fault")
                raise RuntimeError("private reset COMMIT-before-ACK loss")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(
                    sqlite,
                    "_before_commit" if cut == "before" else "_after_commit",
                    fault,
                )
                with pytest.raises(DurableProductError) as error:
                    await product.retry_task(target)
                assert error.value.code == "unavailable"
            assert reached.is_set()
            assert wire.calls == [] and not wire.entered.is_set()
            assert product._scheduler is None
        finally:
            await product.close()

        after = disk_logical(path)
        if cut == "before":
            assert after == before
        else:
            assert after != before
            for name in set(before) - {"tasks", "durable_metadata"}:
                assert after[name] == before[name]
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        try:

            def read_target():
                stored = reopened._session._owner.storage.task(TaskId(target))
                assert stored is not None
                return stored.to_tree()

            row = await reopened._read(read_target)
            expected = dict(original)
            if cut == "after-COMMIT":
                del expected["restarts"]
            assert row == expected
            assert wire.calls == []
        finally:
            await reopened.close()

    asyncio.run(asyncio.wait_for(run(), 20))


def test_early_ack_cancelled_waiters_close_joins_actual_provider_finalizer_and_lease(
    tmp_path,
):
    path = tmp_path / "session.sqlite"
    wire = FinalizerWire()

    async def run():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        result_waiter = None
        try:
            admission = await product.submit("input")
            ack = await asyncio.wait_for(
                product.retry_task(admission.generation_task_id), 5
            )
            assert ack.to_tree() == {
                "task_id": admission.generation_task_id,
                "reset": False,
            }
            await entered(wire.entered)
            result_waiter = asyncio.create_task(product.resume())
            await caller_turn()
            assert not result_waiter.done()
            result_waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await result_waiter
            close = asyncio.create_task(product.close())
            await caller_turn()
            close.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close
            assert not wire.exited.is_set()
            await asyncio.to_thread(
                independent_opener,
                path,
                tmp_path,
                "retry-provider-held",
                "refused",
                invoke_held=True,
            )
            wire.release.set()
            await entered(wire.finalizing)
            second = asyncio.create_task(product.close())
            await caller_turn()
            assert not second.done() and not wire.exited.is_set()
            await asyncio.to_thread(
                independent_opener, path, tmp_path, "retry-finalizer-held", "refused"
            )
            wire.final_release.set()
            await second
            await product.close()
            assert wire.exited.is_set() and len(wire.calls) == 1
            assert not product._scheduler._thread.is_alive()
            await asyncio.to_thread(
                independent_opener, path, tmp_path, "retry-closed", "opened"
            )
        finally:
            wire.unblock()
            if result_waiter is not None:
                await asyncio.gather(result_waiter, return_exceptions=True)
            await product.close()

    asyncio.run(asyncio.wait_for(run(), 25))


def test_unrelated_body_error_wins_without_consuming_unawaited_postack_latch(
    tmp_path, monkeypatch
):
    """Synthetic post-observation error; real ACK, result, context and cleanup."""
    path = tmp_path / "session.sqlite"
    wire = Wire()
    body_error = LookupError("unrelated caller body")
    actual = product_api.DurableProductSession._wait_result

    async def run():
        observed = asyncio.Event()
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )

        async def result(owner, *args, **kwargs):
            value = await actual(owner, *args, **kwargs)
            if owner is product:
                assert value.status == "terminal"
                observed.set()
                raise DurableProductError("incompatible")
            return value

        monkeypatch.setattr(product_api.DurableProductSession, "_wait_result", result)
        with pytest.raises(LookupError) as caught:
            async with product:
                admission = await product.submit("input")
                await product.retry_task(admission.generation_task_id)
                await asyncio.wait_for(observed.wait(), 5)
                # No caller awaited/reported the continuation's own failure.
                raise body_error
        assert caught.value is body_error
        closed = product._close_work
        for _ in range(2):
            with pytest.raises(DurableProductError) as failure:
                await product.close()
            assert failure.value.code == "continuation_failed"
            assert product._close_work is closed
        assert len(wire.calls) == 1
        assert not product._scheduler._thread.is_alive()
        await asyncio.to_thread(
            independent_opener, path, tmp_path, "body-error-closed", "opened"
        )

    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize("mode", ["completed", "post-result-fault"])
def test_actual_retry_cli_owns_run_and_finalizer_before_its_only_report(tmp_path, mode):
    async def prepare():
        product = await create_durable_product_session(
            tmp_path / "session.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, Wire()),
            tools={},
        )
        try:
            return (await product.submit("input")).generation_task_id
        finally:
            await product.close()

    target = asyncio.run(prepare())
    with Child(RETRY_CLI, str(tmp_path), str(target), mode, home=tmp_path) as child:
        child.expect("invoke")
        assert (tmp_path / "retry-output.json").read_text() == ""
        independent_opener(
            tmp_path / "session.sqlite",
            tmp_path,
            "retry-cli-invoke",
            "refused",
            invoke_held=True,
        )
        child.send("release-invoke")
        child.expect("finalizer")
        assert (tmp_path / "retry-output.json").read_text() == ""
        assert child.process.poll() is None
        independent_opener(
            tmp_path / "session.sqlite", tmp_path, "retry-cli-finalizer", "refused"
        )
        child.send("release-finalizer")
        child.expect("worker-exiting")
        if mode == "post-result-fault":
            child.expect("synthetic-post-result-fault")
        code = 0 if mode == "completed" else 1
        outcome = child.expect("finished")
        assert outcome["code"] == code
        source = Path(__file__).resolve().parents[2] / "src"
        assert outcome["native_origins"]
        for row in outcome["native_origins"]:
            loaded = Path(row["file"])
            assert loaded.is_relative_to(source)
            assert hashlib.sha256(loaded.read_bytes()).hexdigest() == row["sha256"]
        assert child.wait() == code
        child.wait_stdout_eof()
        evidence = child.evidence()
        evidence["actual_spawn_argv"] = child.process.args
    (tmp_path / "retry-cli-process.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    independent_opener(
        tmp_path / "session.sqlite", tmp_path, "retry-cli-closed", "opened"
    )
    output = (tmp_path / "retry-output.json").read_text()
    if mode == "completed":
        value = json.loads(output)
        assert value["operation"] == "retry-task" and value["reset"] is False
        assert value["task_id"] == target
        assert value["run"]["status"] == "terminal"
        assert value["run"]["inspection"]["outcome"] == "completed"
    else:
        assert output == ""
        assert "continuation_failed" in evidence["stderr"]
