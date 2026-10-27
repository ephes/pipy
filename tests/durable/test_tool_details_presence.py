"""Normal rich details in real task storage, next requests and public reopen."""

import asyncio
import json
from dataclasses import replace

import pytest
from cli_process_support import run_cli
from generation_support import admit, definition, seed
from test_product_session import profile
from test_tool_boundaries import MultiRound, RichTool, isolated, run_idle

from pipy_harness.native.agent.content import NO_STRUCTURED_CONTENT
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.automation.durable_output import json_output, run_output
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import _decode
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)

VALUES = [
    NO_TOOL_DETAILS,
    None,
    False,
    0,
    "scalar",
    [None, False, -0.0],
    {"a": [1, 1.0]},
]


class ValueTool(RichTool):
    def __init__(self, value):
        super().__init__(NO_STRUCTURED_CONTENT)
        self.value = value

    def invoke(self, request, context):
        return replace(super().invoke(request, context), details=self.value)


def assert_value(value, actual):
    if value is NO_TOOL_DETAILS:
        assert actual is NO_TOOL_DETAILS
    else:
        assert OwnedJson(value) == OwnedJson(actual)


@pytest.mark.parametrize("value", VALUES)
def test_memory_actual_generation_receipt_and_next_request(
    value, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    session = Session(MemoryStorage())
    wire = MultiRound((("rich",), ()))
    port = ValueTool(value)
    tools = define_tool_task(
        {"rich": ToolRegistration(port, "fixture.rich", "1")}, workspace_root=tmp_path
    )
    generation = definition(tmp_path, wire)
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    cid, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tools])
        root = next(task.to_tree() for task in scheduler.inspect() if task.id == parent)
        assert root["state"]["outcome"]["status"] == "completed"
        entries = session.read_on_line(
            lambda: (
                session._owner.storage.scan_entries(
                    EntryQuery({"conversationId": cid}), 100
                ).items
            )
        )
        final = next(
            row.to_tree()
            for row in entries
            if row.to_tree()["kind"] == "pi.assistant"
            and not decode_canonical(row.to_tree()["model"][0]).tool_calls
        )
        next_request = decode_canonical(final["data"]["request"])
        returned = next(
            message
            for message in next_request.request.messages
            if isinstance(message, AgentToolResultMessage)
        )
        assert_value(value, returned.details)
        assert returned.provider_correlation_id == "call:0"
        assert [type(block).__name__ for block in returned.blocks] == [
            "TextContent",
            "ImageContent",
            "TextContent",
        ]
        assert len(port.calls) == 1 and len(wire.calls) == 2
        next_body = wire.calls[-1][1]["body"]
        assert any(message["role"] == "tool" for message in next_body["messages"])
        assert "details" not in json.dumps(next_body)
    finally:
        session.close()


@pytest.mark.parametrize("value", VALUES)
def test_public_sqlite_and_actual_terminal_cli_preserve_normal_details(
    value, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    wire = MultiRound((("rich",), ()))
    port = ValueTool(value)
    registry = {"rich": ToolRegistration(port, "fixture.rich", "1")}
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )
    path = tmp_path / "normal.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
            request_template=template,
        )
        try:
            await product.submit("input")
            outcome = await product.resume()
            assert (
                outcome.status == "terminal"
                and outcome.inspection.outcome == "completed"
            )
            row = next(
                row
                for row in outcome.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            assert_value(value, row.decode().details)
            encoded = run_output(outcome)
            assert encoded["version"] == 2
            result = next(
                row["message"]
                for row in encoded["completed"]["messages"]
                if row["message"]["role"] == "tool_result"
            )
            assert result["details_state"] == (
                "absent" if value is NO_TOOL_DETAILS else "value"
            )
            assert ("details" in result) is (value is not NO_TOOL_DETAILS)
            if value is not NO_TOOL_DETAILS:
                assert OwnedJson(result["details"]) == OwnedJson(value)
            expected = json.loads(json_output(encoded))
        finally:
            await product.close()
        cli = run_cli(
            tmp_path,
            "normal-terminal",
            [
                "resume",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--format",
                "json",
            ],
        )
        assert cli.returncode == 0, cli.stderr
        fresh = json.loads(cli.stdout)
        # Capture revision and observed publication watermark belong to this
        # observer instance. All committed content and entry commit_seq stay exact.
        (tmp_path / "initial-output.json").write_text(json_output(expected))
        assert fresh["completed"]["revision"] == 1
        assert fresh["completed"]["publication_seq"] is None
        assert expected["completed"]["revision"] >= 1
        assert type(expected["completed"]["publication_seq"]) is int
        expected["completed"]["revision"] = 1
        expected["completed"]["publication_seq"] = None
        assert OwnedJson(fresh) == OwnedJson(expected)
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            assert OwnedJson(run_output(await reopened.resume())) == OwnedJson(expected)
        finally:
            await reopened.close()
        assert len(port.calls) == 1 and len(wire.calls) == 2

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize("kind", ["deep1500", "digits5001"])
def test_public_sqlite_tool_producer_admission_and_reopen_do_not_reissue(
    kind, tmp_path, monkeypatch
):
    # Supersedes the R6 promise that every ToolPort can return deep1500 on every
    # interpreter. Historical canonical value readability is tested separately.
    import sys

    isolated(monkeypatch, tmp_path)
    value = {"end": None}
    if kind == "digits5001":
        value = {"number": 10**5000}
    else:
        for _ in range(1500):
            value = {"child": value}
    try:
        json.dumps(value, allow_nan=False)
        admitted = True
    except (ValueError, RecursionError):
        admitted = False
    if sys.version_info[:2] == (3, 11):
        assert sys.getrecursionlimit() == 1000
        assert sys.get_int_max_str_digits() == 4300
        assert not admitted
    wire = MultiRound((("rich",), ()))
    port = ValueTool(value)
    path = tmp_path / "producer-admission.sqlite"

    async def assert_outcome(product):
        outcome = await product.resume()
        if admitted:
            assert outcome.status == "terminal"
            assert outcome.inspection.outcome == "completed"
            message = next(
                row.decode()
                for row in outcome.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            assert OwnedJson(message.details) == OwnedJson(value)
        else:
            # Admission still refuses the same producer value. DE13b now
            # carries the actual invoke exception as a fixed failed result.
            assert outcome.status == "terminal"
            message = next(
                row
                for row in outcome.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            assert message.decode().is_error
            assert message.decode().details is NO_TOOL_DETAILS
            assert message.tool_output.thaw()["recovery"] == {
                "reason": "tool_error",
                "category": "invocation_exception",
            }
        assert len(port.calls) == 1
        assert len(wire.calls) == 2

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"rich": ToolRegistration(port, "fixture.rich", "1")},
        )
        try:
            await product.submit("input")
            await assert_outcome(product)
            await assert_outcome(product)
        finally:
            await product.close()
        reopened = await open_durable_product_session(
            path,
            workspace=tmp_path,
            tools={},
        )
        try:
            await assert_outcome(reopened)
        finally:
            await reopened.close()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_historical_deep_v1_sqlite_read_and_cli_json_writer_without_producer_gate(
    tmp_path,
):
    from pathlib import Path

    from pipy_harness.native.automation.durable_output import message as message_json
    from pipy_harness.native.durable.canonical_codec import encode_canonical
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    arena = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/durable/tool-details-legacy-deep-v1.json"
        ).read_text()
    )
    path = tmp_path / "historical.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    try:
        cid = session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        ).id
        entry = session.commit(
            lambda tx: tx.append_entry(
                cid, {"kind": "fixture.history", "model": [arena]}
            )
        )
        assert (
            session.commit(lambda tx: tx.entry(entry.id)).to_tree()["model"][0] == arena
        )
    finally:
        session.close()
    reopened = Session(open_sqlite_storage(str(path), mode="open"))
    try:
        stored = reopened.commit(lambda tx: tx.entry(entry.id)).to_tree()["model"][0]
        message = decode_canonical(stored)
        assert encode_canonical(message, codec_version=1) == arena
        cursor = message.details
        for _ in range(1500):
            cursor = cursor["child"]
        assert cursor == {"end": None}
        # The real durable CLI JSON writer, not a full product CLI command or
        # a forged completed tool receipt: generic Session storage owns this fact.
        emitted = json_output(message_json(message))
        assert OwnedJson(_decode(emitted.encode())["details"]) == OwnedJson(
            message.details
        )
    finally:
        reopened.close()


def test_public_durable_sdk_refuses_nonexact_normal_integer_before_result_commit(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    wire = MultiRound((("rich",), ()))
    port = ValueTool({"n": 9007199254740993})
    registry = {"rich": ToolRegistration(port, "fixture.rich", "1")}
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )

    async def scenario():
        product = await create_durable_product_session(
            tmp_path / "strict.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
            request_template=template,
        )
        try:
            await product.submit("strict durable boundary")
            first = await product.resume()
            assert first.status == "terminal"
            message = next(
                row
                for row in first.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            assert message.decode().is_error
            assert message.decode().details is NO_TOOL_DETAILS
            assert message.tool_output.thaw()["recovery"] == {
                "reason": "tool_error",
                "category": "invalid_result",
            }
            assert len(port.calls) == 1 and len(wire.calls) == 2
            second = await product.resume()
            assert second.status == "terminal"
            assert len(port.calls) == 1 and len(wire.calls) == 2
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))
