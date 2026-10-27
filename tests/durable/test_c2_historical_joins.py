"""Real old public SDK producer→current public C2 decision→answer→cold nonce."""

import asyncio
import json

import pytest
from c2_historical_join_support import (
    dump,
    graph,
    historical,
    historical_sources,
    keyed_counts,
)
from test_product_session import profile, tools
from tool_support import CountedWire, counts

from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.records import TaskId
from pipy_harness.native.durable.tool_execution import PROGRESS_DOC, RECEIPT_DOC
from pipy_harness.sdk import DurableProductError, open_durable_product_session


@pytest.fixture(scope="module")
def pinned_sources(tmp_path_factory):
    return historical_sources(tmp_path_factory.mktemp("c2-pinned-sources"))


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch, pinned_sources):
    monkeypatch.setenv("PIPY_C2_HISTORICAL_SOURCE_ROOT", str(pinned_sources))
    monkeypatch.delenv("C2_SOURCE", raising=False)
    for name in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / "current-private" / name
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(path))


@pytest.mark.parametrize("role", ["DE12", "B"])
@pytest.mark.parametrize("cut", ["intent-after", "effect", "error"])
def test_actual_old_sdk_hold_public_c2_decision_answer_cold_nonce(
    tmp_path, monkeypatch, role, cut
):
    root = tmp_path
    for k in ["HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME", "TMPDIR"]:
        monkeypatch.setenv(k, str(root / "current-private" / k))
    before = historical(root, role, cut)
    task = before["child"]
    task_id = task["id"]
    intent = task["state"]["checkpoint"]["intent"]
    oldkey = intent["key"]
    oldkeycounts = keyed_counts(root, oldkey)
    nonce = "c" * 32

    async def scenario():
        wire = CountedWire(root)
        async with await open_durable_product_session(
            root / "product.sqlite",
            workspace=root,
            profile=profile(root, wire),
            tools=tools(root),
        ) as product:
            # Inspection and explicit decision must not reissue original preparation/effect.
            await product.inspect()
            assert counts(root) == before["counts"]
            retained = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    PROGRESS_DOC,
                    task["conversationId"],
                    task_id,
                )
            )
            assert (retained is not None) == (role == "B")
            if retained is not None:
                assert (
                    retained["value"]["output"] == ""
                    and retained["value"]["details"] == {"present": False}
                    and retained["value"]["diagnostics"] == []
                )
                assert retained["value"]["attempts"] == 1
            decision = await product.adjudicate_interrupted(task_id, nonce=nonce)
            assert decision.to_tree() == {"task_id": task_id, "decision": "interrupted"}
            assert (
                counts(root) == before["counts"]
                and keyed_counts(root, oldkey) == oldkeycounts
            )
            saved = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    task["conversationId"],
                    task_id,
                )
            )
            assert saved is not None and saved["value"]["intent"] == intent
            value = saved["value"]
            assert value["version"] == 2
            # Required nullable counters are validated by actual closed receipt parser.
            from pipy_harness.native.durable.tool_values import receipt

            _, outcome = receipt(value)
            assert value["nativeToolOutput"]["ending"] == "interrupted"
            assert set(value["nativeToolOutput"]["recovery"]) == {
                "reason",
                "attempts",
                "restarts",
            }
            assert value["nativeToolOutput"]["recovery"]["reason"] == "interrupted"
            assert value["nativeToolOutput"]["recovery"]["attempts"] == (
                None if retained is None else retained["value"]["attempts"]
            )
            assert value["nativeToolOutput"]["diagnostics"] == (
                [] if retained is None else retained["value"]["diagnostics"]
            )
            from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS

            assert (
                outcome.result.content.value == ""
                and outcome.result.details is NO_TOOL_DETAILS
            )
            assert value["nativeToolOutput"]["recovery"]["restarts"] == task.get(
                "restarts"
            )
            assert (
                outcome.result.is_error
                and not outcome.result.terminate
                and outcome.execution_seconds is None
            )
            pending = await product._read(
                lambda: product._session._owner.storage.task(TaskId(task_id)).to_tree()
            )
            assert (
                pending["state"]["status"] == "pending"
                and pending["state"]["checkpoint"]["phase"] == "settle"
            )
            assert (
                await product.adjudicate_interrupted(task_id, nonce=nonce) == decision
            )
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            assert keyed_counts(root, oldkey) == oldkeycounts
            expected = {k: v + 1 for k, v in before["counts"].items()}
            assert counts(root) == expected  # only later child plus one next provider
            assert len(wire.calls) == 1 and any(
                m["role"] == "tool" for m in wire.calls[0][1]["body"]["messages"]
            )
            entries = graph(root)["entries"]
            matches = [
                e
                for e in entries
                if e["kind"] == "pi.tool-result" and e.get("byTaskId") == task_id
            ]
            assert len(matches) == 1
            encoded = json.dumps(matches[0])
            assert (
                encoded.count("nativeToolReceipt") == 1
                and set(matches[0]["data"]) == {"nativeToolReceipt"}
                and len(matches[0]["model"]) == 1
            )
            assert matches[0]["data"]["nativeToolReceipt"] == value
            from pipy_harness.native.durable.canonical_codec import encode_canonical

            public = json.dumps(
                [encode_canonical(m.decode()) for m in result.completed.messages]
            ) + json.dumps(wire.calls[0][1]["body"])
            for marker in [
                oldkey,
                str(root),
                json.dumps(intent["arguments"], sort_keys=True),
            ]:
                assert marker not in public
            assert (
                await product.adjudicate_interrupted(task_id, nonce=nonce) == decision
            )
            dump(
                root / "C2-JOIN-WITNESS.json",
                {
                    "role": role,
                    "cut": cut,
                    "task_id": task_id,
                    "before_counts": before["counts"],
                    "after_counts": counts(root),
                    "oldkey_counts_before": oldkeycounts,
                    "oldkey_counts_after": keyed_counts(root, oldkey),
                    "decision": decision.to_tree(),
                    "result_status": result.status,
                    "receipt": saved,
                    "retained_progress": retained,
                    "model_result_entry": matches[0],
                },
            )
        async with await open_durable_product_session(
            root / "product.sqlite", workspace=root, tools={}
        ) as product:
            assert (
                await product.adjudicate_interrupted(task_id, nonce=nonce) == decision
            )
        assert counts(root) == expected and keyed_counts(root, oldkey) == oldkeycounts

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_actual_old_preintent_no_saved_authority_refuses_without_writes(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from tool_support import Keyed

    class SequentialPort(Keyed):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.definition.name == "keyed":
                self.definition = replace(self.definition, execution_mode="sequential")

    before = historical(tmp_path, "B", "preintent")
    task = before["child"]
    assert task["state"]["checkpoint"]["intent"] is None

    async def scenario():
        async with await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=tools(tmp_path, port_type=SequentialPort),
        ) as product:
            original = graph(tmp_path)
            prior = counts(tmp_path)
            with pytest.raises(DurableProductError) as error:
                await product.adjudicate_interrupted(task["id"], nonce="d" * 32)
            assert error.value.code == "not_supported"
            assert graph(tmp_path) == original and counts(tmp_path) == prior
            dump(
                tmp_path / "C2-PREINTENT-REFUSAL.json",
                {
                    "origin": "actual oldB sequential model-tool call blocks before prepared intent",
                    "code": error.value.code,
                    "counts": prior,
                    "no_writes": True,
                },
            )

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_opaque_receipt_control_over_genuine_old_hold_refuses_without_writes(tmp_path):
    """Opaque receipt is a typed control; original blocked hold is actual oldB."""
    from pipy_harness.native.durable.generation_transport import owned_call

    before = historical(tmp_path, "B", "error")
    task = before["child"]

    async def scenario():
        async with await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=tools(tmp_path),
        ) as product:
            await owned_call(
                lambda: product._session.commit(
                    lambda tx: tx.doc(RECEIPT_DOC, task_id=task["id"]).update(
                        {"opaque": "SYNTHETIC_CONTROL_ONLY"}
                    )
                )
            )
            original = graph(tmp_path)
            prior = counts(tmp_path)
            with pytest.raises(DurableProductError) as error:
                await product.adjudicate_interrupted(task["id"], nonce="e" * 32)
            assert error.value.code == "receipt_held_needs_repair"
            assert graph(tmp_path) == original and counts(tmp_path) == prior
            dump(
                tmp_path / "C2-OPAQUE-REFUSAL.json",
                {
                    "origin": "typed opaque receipt control over actual old reporting hold",
                    "code": error.value.code,
                    "counts": prior,
                    "no_writes": True,
                },
            )

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_actual_failed_backend_running_frontier_uses_c1_not_c2(tmp_path):
    from c2_historical_join_support import actor

    actor(tmp_path, "B", "produce", "backend")
    before = json.loads((tmp_path / "HISTORICAL-FRONTIER.json").read_text())
    task = before["child"]
    frame = task["state"]["checkpoint"]
    assert task["state"]["status"] == "running" and frame["phase"] == "execute"
    injection = json.loads((tmp_path / "BACKEND-INJECTION.json").read_text())
    assert (
        len(injection) == 1
        and injection[0]["state"]["checkpoint"]["phase"] == "blocked"
    )
    key = frame["intent"]["key"]
    oldcounts = keyed_counts(tmp_path, key)

    async def scenario():
        async with await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=tools(tmp_path),
        ) as product:
            original = graph(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.adjudicate_interrupted(task["id"], nonce="f" * 32)
            assert refused.value.code == "not_supported"
            assert graph(tmp_path) == original and counts(tmp_path) == before["counts"]
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            assert keyed_counts(tmp_path, key) == oldcounts
            assert counts(tmp_path) == {k: v + 1 for k, v in before["counts"].items()}
            dump(
                tmp_path / "C1-RUNNING-CONTROL.json",
                {
                    "staged": "blocked",
                    "persisted": "running_execute",
                    "injection_count": 1,
                    "c2_refusal": refused.value.code,
                    "c1_result": result.status,
                    "oldkey_counts_unchanged": True,
                },
            )

    asyncio.run(asyncio.wait_for(scenario(), 25))


def test_actual_c1_receipt_old_b_parser_intentless_first_winner_is_preserved(tmp_path):
    from c2_historical_join_support import actor

    actor(tmp_path, "C1", "produce", "intent-after")
    actor(tmp_path, "C1", "recover", "receipt-after")

    # Actual current receipt before the older parser sees it.
    def saved_receipt():
        from pipy_harness.native.durable.session_core import Session
        from pipy_harness.native.durable.sqlite import open_sqlite_storage

        session = Session(
            open_sqlite_storage(str(tmp_path / "product.sqlite"), mode="open")
        )
        try:
            rows = graph(tmp_path)["tasks"]
            child = next(
                r
                for r in rows
                if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
            )
            found = session.read_on_line(
                lambda: peek_document(
                    session._owner.storage,
                    RECEIPT_DOC,
                    child["conversationId"],
                    child["id"],
                )
            )
            return child, found
        finally:
            session.close()

    child, original = saved_receipt()
    assert original["value"]["nativeToolOutput"]["ending"] == "interrupted"
    actor(tmp_path, "B", "recover", "parser")
    before = json.loads((tmp_path / "HISTORICAL-FRONTIER.json").read_text())
    task = before["child"]
    assert (
        task["state"]["checkpoint"]["phase"] == "blocked"
        and task["state"]["checkpoint"]["intent"] is None
    )
    key = original["value"]["key"]
    prior = counts(tmp_path)
    old = keyed_counts(tmp_path, key)

    async def scenario():
        async with await open_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=tools(tmp_path),
        ) as product:
            actual = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    child["conversationId"],
                    child["id"],
                )
            )
            assert actual == original
            decision = await product.adjudicate_interrupted(task["id"], nonce="b" * 32)
            assert decision.decision == "receipt"
            actual = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    child["conversationId"],
                    child["id"],
                )
            )
            assert actual == original and counts(tmp_path) == prior
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            assert keyed_counts(tmp_path, key) == old
            assert counts(tmp_path) == {k: v + 1 for k, v in prior.items()}
            dump(
                tmp_path / "C2-FIRST-WINNER.json",
                {
                    "origin": "current actual interrupted receipt→actual oldB parser hold",
                    "decision": decision.to_tree(),
                    "receipt_exactly_preserved": True,
                    "oldkey_counts_unchanged": True,
                    "result": result.status,
                },
            )
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as product:
            assert (
                await product.adjudicate_interrupted(task["id"], nonce="b" * 32)
                == decision
            )

    asyncio.run(asyncio.wait_for(scenario(), 30))
