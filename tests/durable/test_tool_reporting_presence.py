"""Retained ACK values reach saved requests, completed SDK output and actual CLI."""

import asyncio
import sqlite3
import sys
from dataclasses import replace

import pytest
from cli_process_support import run_cli
from generation_support import seed
from test_product_session import profile
from test_tool_boundaries import MultiRound, isolated

from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
)
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.sqlite import _decode
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)


def _reported_value(case):
    if case == "deep1500":
        value = {"leaf": "\ud83d\ude00"}
        for _ in range(1500):
            value = {"child": value}
        return value
    return {
        "absent": NO_TOOL_DETAILS,
        "null": None,
        "false": False,
        "zero": 0,
        "string": "scalar",
        "array": [None, False, 1],
        "object": {"nested": [1]},
        "final_null": [1],
        "final_empty": [1],
        "final_legacy": [1],
    }[case]


def _equal(expected, actual):
    if expected is NO_TOOL_DETAILS or expected is LEGACY_TOOL_DETAILS_NONE:
        assert actual is expected
    else:
        assert OwnedJson(actual) == OwnedJson(expected)


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "null",
        "false",
        "zero",
        "string",
        "array",
        "object",
        "deep1500",
        "final_null",
        "final_empty",
        "final_legacy",
    ],
)
def test_public_retained_presence_ack_saved_request_and_auth_free_cli(
    case, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    value = _reported_value(case)
    final = {
        "final_null": None,
        "final_empty": {},
        "final_legacy": LEGACY_TOOL_DETAILS_NONE,
    }.get(case, NO_TOOL_DETAILS)
    expected = value if final is NO_TOOL_DETAILS else final
    calls, acknowledgements = [], []
    if case == "deep1500" and sys.version_info[:2] == (3, 11):
        assert sys.getrecursionlimit() == 1000
        assert sys.get_int_max_str_digits() == 4300

    class Port:
        definition = ToolDefinition(
            "reported",
            "reported",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            calls.append(context.call_key)
            context.report_details({"older": "must be replaced or cleared"})
            context.report_details(value)
            acknowledgements.append(True)
            return ToolExecutionResult(request.tool_request_id, "final", details=final)

    port, wire = Port(), MultiRound((("reported",), ()))
    path = tmp_path / "retained.sqlite"
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )

    async def run():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"reported": ToolRegistration(port, "fixture.reported", "1")},
            request_template=template,
        )
        try:
            await product.submit("input")
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            message = next(
                row
                for row in result.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            _equal(expected, message.decode().details)
            assert message.tool_output.thaw()["version"] == 1
            assert acknowledgements == [True]
        finally:
            await product.close()
        with sqlite3.connect(path) as db:
            entries = [
                _decode(row[0]) for row in db.execute("SELECT record FROM entries")
            ]
        assistant = next(
            row
            for row in entries
            if row["kind"] == "pi.assistant"
            and not decode_canonical(row["model"][0]).tool_calls
        )
        saved = decode_canonical(assistant["data"]["request"])
        returned = next(
            row
            for row in saved.request.messages
            if isinstance(row, AgentToolResultMessage)
        )
        _equal(expected, returned.details)
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            resumed = await reopened.resume()
            assert resumed.status == "terminal"
            _equal(
                expected,
                next(
                    row.decode()
                    for row in resumed.completed.messages
                    if isinstance(row.decode(), AgentToolResultMessage)
                ).details,
            )
        finally:
            await reopened.close()
        cli = run_cli(
            tmp_path,
            "retained-terminal",
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
        emitted = _decode(cli.stdout)
        output = next(
            row["message"]
            for row in emitted["completed"]["messages"]
            if row["message"]["role"] == "tool_result"
        )
        if expected is NO_TOOL_DETAILS or expected is LEGACY_TOOL_DETAILS_NONE:
            assert "details" not in output
        elif case == "deep1500":
            # JSON text represents the pair as one Unicode scalar when read;
            # the canonical stored/request values above retain raw code points.
            cursor = output["details"]
            for _ in range(1500):
                cursor = cursor["child"]
            assert cursor == {"leaf": "😀"}
        else:
            _equal(expected, output["details"])
        assert len(calls) == 1 and len(wire.calls) == 2
        if case == "deep1500":
            cursor = returned.details
            for _ in range(1500):
                cursor = cursor["child"]
            assert cursor == {"leaf": "\ud83d\ude00"}

    asyncio.run(asyncio.wait_for(run(), 20))
