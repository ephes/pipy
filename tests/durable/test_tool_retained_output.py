"""P16 in the durable host: the same omitted-output cases as the ordinary host.

Each case runs a fixture tool through the durable product session. Where the
tool reports details, ``report_details`` waits for the committed prefix, so the
call is cut between its report and its return.
"""

import asyncio
from dataclasses import replace

import pytest
from generation_support import seed
from test_product_session import profile
from test_tool_boundaries import MultiRound, isolated

from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.tools.base import (
    RETAINED_OUTPUT,
    RetainedOutputSnapshot,
    ToolDefinition,
    ToolExecutionResult,
)
from pipy_harness.native.tools.truncate import OutputTail
from pipy_harness.sdk import create_durable_product_session

LINES = ["l\n"] * 2100  # 2100 lines, above the 2000-line window


def _body(case, context, snapshots):
    if case == "chunks":
        context.output_sink("first\n")
        context.output_sink("second")
    elif case == "explicit":
        context.output_sink("reported")
        return "explicit", NO_TOOL_DETAILS
    elif case == "truncated":
        context.report_diagnostic(severity="info", message="started", code="start")
        for line in LINES:
            context.output_sink(line)
    elif case == "full_budget":
        # Review R1: a tool that used its whole diagnostic budget still gets
        # the Harness's truncated diagnostic, as the ordinary host gives it.
        context.report_diagnostic(severity="info", message="d" * 13300)
        for line in LINES:
            context.output_sink(line)
    elif case == "details":
        context.output_sink("with details")
        context.report_details({"step": 1})  # waits for the committed prefix
    snapshots.append(context.retained_output())
    return RETAINED_OUTPUT, NO_TOOL_DETAILS


def _expected(case):
    if case == "chunks":
        return "first\nsecond", NO_TOOL_DETAILS
    if case == "explicit":
        return "explicit", NO_TOOL_DETAILS
    if case == "empty":
        return "", NO_TOOL_DETAILS
    if case == "details":
        return "with details", {"step": 1}
    tail = OutputTail()
    for line in LINES:
        tail = tail.append(line)
    first = (
        "[info] " + "d" * 13300 if case == "full_budget" else "[info (start)] started"
    )
    return (
        tail.text
        + "\n<harness>\n"
        + first
        + "\n"
        + "[warning (truncated)] Output truncated to its end: "
        + f"{tail.dropped_lines} lines, {tail.dropped_bytes} bytes dropped"
        + "\n</harness>"
    ), NO_TOOL_DETAILS


@pytest.mark.parametrize(
    "case", ["chunks", "explicit", "empty", "truncated", "full_budget", "details"]
)
def test_durable_omitted_output_matches_the_ordinary_host(case, tmp_path, monkeypatch):
    isolated(monkeypatch, tmp_path)
    snapshots = []

    class Port:
        definition = ToolDefinition(
            "retained",
            "retained",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            output, details = _body(case, context, snapshots)
            return ToolExecutionResult(request.tool_request_id, output, details=details)

    port, wire = Port(), MultiRound((("retained",), ()))
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
            tmp_path / "retained.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"retained": ToolRegistration(port, "fixture.retained", "1")},
            request_template=template,
        )
        try:
            await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "completed"
            return next(
                row.decode()
                for row in result.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
        finally:
            await product.close()

    message = asyncio.run(run())
    text, details = _expected(case)
    assert message.content.value == text
    if details is NO_TOOL_DETAILS:
        assert message.details is NO_TOOL_DETAILS
    else:
        assert message.details == details
    if case == "truncated":
        assert (
            snapshots[0].truncated and snapshots[0].text == text.split("\n<harness>")[0]
        )
    elif case in ("chunks", "details"):
        assert snapshots == [RetainedOutputSnapshot(text, False)]
