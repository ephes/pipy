"""Actual public SQLite reporting, failed raw task and next provider/CLI reads."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from functools import partial

import pytest
from cli_process_support import run_cli
from generation_support import seed
from test_product_session import profile
from test_tool_boundaries import MultiRound, isolated

from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.automation.durable_output import json_output, run_output
from pipy_harness.native.durable import tool_execution
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.documents import resolve
from pipy_harness.native.durable.sqlite import _decode, open_sqlite_storage
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.mark.parametrize("failed", [False, True])
def test_public_reported_fact2_actual_next_provider_and_auth_free_cli(
    tmp_path, monkeypatch, failed
):
    isolated(monkeypatch, tmp_path)
    calls = []
    captured_receipts = {}
    original_settle = tool_execution._Tool.settle

    async def capture_actual_receipt(self, record, runtime, context):
        async def capture(tx, current):
            captured_receipts[runtime.task_id] = (
                await tx.doc(tool_execution.RECEIPT_DOC)
            ).snapshot()

        await runtime.commit(capture)
        await original_settle(self, record, runtime, context)

    monkeypatch.setattr(tool_execution._Tool, "settle", capture_actual_receipt)
    private_argument = "FIXTURE_PRIVATE_PREPARED_ARGUMENT"
    private_implementation = "FIXTURE_PRIVATE_IMPLEMENTATION"

    class Reported:
        definition = ToolDefinition(
            "reported",
            "reported",
            {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "prepared": {"type": "string"},
                },
            },
        )

        def prepare_arguments(self, arguments):
            return {**arguments, "prepared": private_argument}

        def invoke(self, request, context):
            assert request.arguments["prepared"] == private_argument
            calls.append(context.call_key)
            (tmp_path / "effect").write_text("one\n")
            context.output_sink("partial output")
            context.report_details([1, None])
            context.report_diagnostic(
                severity="warning", message="reported warning", code="fixture"
            )
            if failed:
                raise ValueError("PRIVATE exception must not appear in durable output")
            return ToolExecutionResult(request.tool_request_id, "normal output")

    port = Reported()
    wire = MultiRound((("reported",), ()))
    path = tmp_path / "reporting.sqlite"
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )

    run = partial(
        _run_reporting_scenario,
        path=path,
        tmp_path=tmp_path,
        wire=wire,
        port=port,
        private_implementation=private_implementation,
        template=template,
        failed=failed,
        calls=calls,
        private_argument=private_argument,
        captured_receipts=captured_receipts,
    )

    asyncio.run(asyncio.wait_for(run(), 20))


def _assert_actual_carrier(path, captured_receipts, private_argument, failed):
    with sqlite3.connect(path) as db:
        tasks = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
        entries = [_decode(row[0]) for row in db.execute("SELECT record FROM entries")]
        documents = [
            _decode(row[0]) for row in db.execute("SELECT record FROM documents")
        ]
    child = next(row for row in tasks if row["kind"] == "pi.tool")
    entry = next(row for row in entries if row["kind"] == "pi.tool-result")
    assert set(entry["data"]) == {"nativeToolReceipt"}
    assert (
        OwnedJson(entry["data"]["nativeToolReceipt"]) == captured_receipts[child["id"]]
    )
    assert (
        entry["data"]["nativeToolReceipt"]["intent"]["arguments"]["prepared"]
        == private_argument
    )
    receipt_doc = next(row for row in documents if row["kind"] == "pi.tool.receipt")
    assert type(receipt_doc["retiredAt"]) is int
    raw_store = open_sqlite_storage(str(path), mode="open")
    try:
        _, address = resolve(tool_execution.RECEIPT_DOC, task_id=child["id"])
        assert raw_store.find_document(address) is None
    finally:
        raw_store.close()
    assert child["state"]["outcome"]["status"] == ("failed" if failed else "completed")
    assert child["state"]["outcome"]["result"]["kind"] == "model"


async def _run_reporting_scenario(
    *,
    path,
    tmp_path,
    wire,
    port,
    private_implementation,
    template,
    failed,
    calls,
    private_argument,
    captured_receipts,
):
    product = await create_durable_product_session(
        path,
        workspace=tmp_path,
        profile=profile(tmp_path, wire),
        tools={"reported": ToolRegistration(port, private_implementation, "1")},
        request_template=template,
    )
    try:
        await product.submit("input")
        result = await product.resume()
        assert result.status == "terminal", result.inspection
        assert result.inspection.outcome == "completed"
        message = next(
            row
            for row in result.completed.messages
            if isinstance(row.decode(), AgentToolResultMessage)
        )
        decoded = message.decode()
        assert decoded.details == [1, None]
        assert decoded.is_error is failed
        assert "reported warning" in decoded.content.value
        assert "PRIVATE" not in decoded.content.value
        full = message.tool_output.thaw()
        assert full["version"] == 2
        assert full["ending"] == ("failed" if failed else "completed")
        assert full["recovery"] == (
            {"reason": "tool_error", "category": "invocation_exception"}
            if failed
            else None
        )
        assert len(calls) == 1 and len(wire.calls) == 2
        sent = next(
            m for m in wire.calls[1][1]["body"]["messages"] if m["role"] == "tool"
        )
        assert "reported warning" in str(sent["content"])
        assert "details" not in sent
        markers = (
            private_argument,
            private_implementation,
            str(tmp_path),
            calls[0],
        )
        public_json = json_output(run_output(result))
        metadata = repr(await product.inspect(message.by_task_id))
        for marker in markers:
            assert marker not in public_json
            assert marker not in metadata
            assert marker not in json.dumps(wire.calls[1][1]["body"])
    except Exception:
        if product._session.failure is not None:
            raise product._session.failure
        raise
    finally:
        await product.close()
    _assert_actual_carrier(path, captured_receipts, private_argument, failed)
    for _ in range(2):
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            assert (await reopened.resume()).status == "terminal"
        finally:
            await reopened.close()
    cli = run_cli(
        tmp_path,
        "reported-terminal",
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
    payload = json.loads(cli.stdout)
    for marker in markers:
        assert marker.encode() not in cli.stdout
    row = next(
        r for r in payload["completed"]["messages"] if r["tool_output"] is not None
    )
    assert row["tool_output"]["ending"] == ("failed" if failed else "completed")
    assert row["message"]["details"] == [1, None]
    assert len(calls) == 1 and len(wire.calls) == 2
    assert (tmp_path / "effect").read_text() == "one\n"
