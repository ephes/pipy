"""Completed value fidelity, actual terminate holds and conversation fences."""

import asyncio
import math
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from test_tool_boundaries import MultiRound, RichTool, run_idle
from test_tool_execution import KeyedTool

from pipy_harness.native.agent.content import NO_STRUCTURED_CONTENT
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.completed_projection import (
    ProjectionClosed,
    observe_completed,
)
from pipy_harness.native.durable.generation import message_entry
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_history import ORDER_DOC
from pipy_harness.native.durable.tool_values import read_native_tool_output


@pytest.mark.parametrize(
    "structured", [NO_STRUCTURED_CONTENT, None, {"values": [0, 0.0, -0.0]}]
)
def test_actual_rich_receipt_snapshot_retains_owned_exact_values(
    tmp_path, monkeypatch, structured
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    port = RichTool(structured)
    wire = MultiRound((("rich",), ()))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {"rich": ToolRegistration(port, "fixture.rich", "1")}, workspace_root=tmp_path
    )
    cid, _, _ = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(tmp_path).request, available_tools=(port.definition,))
        ),
    )
    run_idle(session, [generation, tool])

    async def run():
        watch = await observe_completed(session, cid)
        model = next(
            m
            for m in watch.snapshot.messages
            if type(m.decode()) is AgentToolResultMessage
        )
        decoded = model.decode()
        _, outcome = read_native_tool_output(model.tool_output.thaw())
        assert decoded.blocks == outcome.result.blocks and len(decoded.blocks) == 3
        assert outcome.execution_seconds is not None and outcome.execution_seconds >= 0
        assert decoded.tool_request_id == port.calls[0].tool_request_id
        assert decoded.provider_correlation_id == "call:0"
        if structured is NO_STRUCTURED_CONTENT:
            assert decoded.structured_content is NO_STRUCTURED_CONTENT
        elif structured is None:
            assert decoded.structured_content is None
        else:
            values = decoded.structured_content["values"]
            assert [type(v) for v in values] == [int, float, float]
            assert math.copysign(1, values[2]) == -1
            values.append("consumer mutation")
            assert len(model.decode().structured_content["values"]) == 3
        decoded.details["nested"]["null"] = "mutated"
        assert model.decode().details["nested"]["null"] is None
        assistant = watch.snapshot.messages[1].decode()
        assert assistant.tool_calls[0].arguments_json.value == ' { "x":  1 } '
        assert (
            decode_canonical(watch.snapshot.messages[1].provider_outcome.thaw()).result
            is not None
        )
        before = (len(port.calls), len(wire.calls))
        watch.stop()
        await watch.wait_closed()
        assert (len(port.calls), len(wire.calls)) == before == (1, 2)

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize(
    "names,completed",
    [
        (("rich", "later"), ("rich",)),
        (("rich",), ("rich",)),
        (("later", "rich"), ("later", "rich")),
        (("rich", "unavailable", "later"), ("rich", "unavailable")),
        (("rich", "unavailable"), ("rich", "unavailable")),
    ],
)
def test_terminate_hold_is_completed_prefix_not_closed_round_or_terminal_parent(
    tmp_path, names, completed
):
    session = Session(MemoryStorage())
    rich, later = RichTool(None, terminate=True), KeyedTool("later", [])
    wire = Wire(names)
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in (rich, later)
        },
        workspace_root=tmp_path,
    )
    cid, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(
                seed(tmp_path).request,
                available_tools=(rich.definition, later.definition),
            )
        ),
    )
    scheduler = run_idle(session, [generation, tool])
    owner = next(row.to_tree() for row in scheduler.inspect() if row.id == parent)
    if "later" in names and names.index("later") > names.index("rich"):
        assert owner["state"]["status"] == "waiting"
    else:
        assert owner["state"]["status"] == "pending"
        assert owner["state"]["checkpoint"]["phase"] == "blocked"
    index = session.snapshot(ORDER_DOC, conversation_id=cid)
    assert index is None or index.value.thaw() == {"rounds": {}}

    async def run():
        watch = await observe_completed(session, cid)
        snapshot = watch.snapshot
        assert not snapshot.rounds
        assert all(outcome.task_id != parent for outcome in snapshot.outcomes)
        results = [
            m.decode()
            for m in snapshot.messages
            if type(m.decode()) is AgentToolResultMessage
        ]
        assert [m.tool_name for m in results] == list(completed)
        assert next(m for m in results if m.tool_name == "rich").terminate
        assert len(wire.calls) == 1 and len(later.calls) == int(names[0] == "later")
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=10)


def test_other_conversation_does_not_wake_and_unknown_model_refuses_without_payload():
    session = Session(MemoryStorage())
    cid, other = [
        session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
        for _ in range(2)
    ]

    async def run():
        watch = await observe_completed(session, cid)
        seen = []

        async def consume(snapshot):
            seen.append(snapshot)

        watch.start(consume)
        await session.acommit(
            lambda tx: tx.append_entry(
                other, {"kind": "future", "data": "private-material"}
            )
        )
        await asyncio.sleep(0)
        assert not seen and watch.snapshot.revision == 0
        final = await watch.drain()
        assert not final.messages and final.publication_seq is None
        await session.acommit(
            lambda tx: tx.append_entry(
                cid, {"kind": "future", "data": "private-material"}
            )
        )
        assert await asyncio.wait_for(watch.wait_closed(), 5) == "capture_error"
        with pytest.raises(ProjectionClosed) as caught:
            await watch.drain()
        assert "private-material" not in str(caught.value)
        assert session.failure is None

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


@pytest.mark.parametrize("optional", [None, 0])
def test_optional_usage_absence_zero_and_no_projection_accumulator(
    tmp_path, monkeypatch, optional
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    session = Session(MemoryStorage())
    generation = definition(tmp_path, Wire())
    cid, _, _ = admit(session, generation, seed(tmp_path))
    run_idle(session, [generation])

    async def run():
        first = await observe_completed(session, cid)
        original = first.snapshot.messages[-1].decode()
        usage = replace(original.usage, reasoning=optional, cache_write_1h=optional)
        message = replace(original, usage=usage)
        # A trusted caller appends a canonical completed model entry. The
        # observer must preserve its fields without manufacturing a settlement
        # or adding its usage to the existing committed ledger.
        await session.acommit(lambda tx: tx.append_entry(cid, message_entry(message)))
        fresh = await observe_completed(session, cid)
        decoded = fresh.snapshot.messages[-1].decode()
        assert (
            decoded.usage.reasoning == optional
            and decoded.usage.cache_write_1h == optional
        )
        assert (decoded.usage.reasoning is None) == (optional is None)
        assert fresh.snapshot.usage == first.snapshot.usage
        assert len(fresh.snapshot.usage.thaw()["settlements"]) == 1
        for watch in (first, fresh):
            watch.stop()
            await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)
