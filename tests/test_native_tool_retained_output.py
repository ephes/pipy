"""P16 in the ordinary host: omitted output, the retained window and reports.

A tool may return ``RETAINED_OUTPUT`` instead of its output (Pi durable
``ToolExecutionResult.output`` omitted). The executor then uses the retained
window of the call's live output, adds Pi's ``truncated`` diagnostic when the
window dropped bytes, falls back to the last reported details and shows the
reported diagnostics, on the synchronous and the interruptible executor alike.
"""

from __future__ import annotations

import threading
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent.content import ProductContent, ToolPartialResult
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.agent.tools import (
    RetainedOutputWindow,
    ToolExecutionInterruption,
    ToolExecutor,
)
from pipy_harness.native.tools.base import (
    RETAINED_OUTPUT,
    RetainedOutputSnapshot,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from pipy_harness.native.tools.truncate import OutputTail

CHUNK = "x" * 99 + "\n"  # 100 bytes, one line


class Scripted:
    definition = ToolDefinition(
        "scripted", "scripted", {"type": "object", "properties": {}}
    )

    def __init__(self, body: Any) -> None:
        self.body = body
        self.snapshots: list[RetainedOutputSnapshot] = []

    def invoke(self, request: Any, context: ToolContext) -> ToolExecutionResult:
        output, details = self.body(context, self)
        return ToolExecutionResult(request.tool_request_id, output, details=details)


def sink(context: ToolContext) -> Any:
    """The executor always gives a tool an output sink (P16)."""
    assert context.output_sink is not None
    return context.output_sink


def run(body: Any, tmp_path: Path, *, interruptible: bool = False) -> Any:
    tool = Scripted(body)
    live: list[Any] = []

    def waiter(done: threading.Event, cancel: threading.Event) -> Any:
        assert done.wait(10)
        return ToolExecutionInterruption.SETTLED

    outcome = ToolExecutor({"scripted": tool}).execute(
        AgentToolCall("c", "scripted", ProductContent("{}")),
        ToolContext(tmp_path, output_sink=live.append),
        waiter if interruptible else None,
    )
    return outcome.result, tool, live


EXECUTORS = pytest.mark.parametrize(
    "interruptible", [False, True], ids=["sync", "interruptible"]
)


@EXECUTORS
def test_omitted_output_after_two_chunks_is_the_retained_text(tmp_path, interruptible):
    def body(context: ToolContext, tool: Scripted) -> Any:
        sink(context)("first\n")
        sink(context)("second")
        tool.snapshots.append(context.retained_output())
        return RETAINED_OUTPUT, NO_TOOL_DETAILS

    result, tool, live = run(body, tmp_path, interruptible=interruptible)
    assert isinstance(result, AgentToolResultMessage)
    assert result.content.value == "first\nsecond"
    assert live == ["first\n", "second"]  # the live sink still gets every chunk
    assert tool.snapshots == [RetainedOutputSnapshot("first\nsecond", False)]


@EXECUTORS
def test_explicit_text_wins_over_the_reported_output(tmp_path, interruptible):
    def body(context: ToolContext, tool: Scripted) -> Any:
        sink(context)("reported")
        return "explicit", NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path, interruptible=interruptible)
    assert result.content.value == "explicit"


@EXECUTORS
@pytest.mark.parametrize("output", [RETAINED_OUTPUT, ""])
def test_nothing_reported_or_explicit_empty_is_empty_content(
    tmp_path, interruptible, output
):
    result, _, _ = run(
        lambda context, tool: (output, NO_TOOL_DETAILS),
        tmp_path,
        interruptible=interruptible,
    )
    assert result.content.value == ""


@EXECUTORS
def test_a_window_that_dropped_bytes_adds_the_truncated_diagnostic(
    tmp_path, interruptible
):
    chunks = [CHUNK] * 2500  # 2500 lines, above the 2000-line window

    def body(context: ToolContext, tool: Scripted) -> Any:
        context.report_diagnostic(severity="info", message="started", code="start")
        for chunk in chunks:
            sink(context)(chunk)
        tool.snapshots.append(context.retained_output())
        return RETAINED_OUTPUT, NO_TOOL_DETAILS

    result, tool, _ = run(body, tmp_path, interruptible=interruptible)
    tail = OutputTail()
    for chunk in chunks:
        tail = tail.append(chunk)
    assert tail.dropped_bytes > 0
    assert tool.snapshots == [RetainedOutputSnapshot(tail.text, True)]
    assert result.content.value == (
        tail.text
        + "\n<harness>\n[info (start)] started\n"
        + "[warning (truncated)] Output truncated to its end: "
        + f"{tail.dropped_lines} lines, {tail.dropped_bytes} bytes dropped"
        + "\n</harness>"
    )


@EXECUTORS
def test_omitted_details_are_the_last_reported_ones(tmp_path, interruptible):
    reported = {"step": 1}

    def body(context: ToolContext, tool: Scripted) -> Any:
        context.report_details({"step": 0})
        context.report_details(reported)
        reported["step"] = 99  # owned when reported
        return RETAINED_OUTPUT, NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path, interruptible=interruptible)
    assert result.details == {"step": 1}


def test_explicit_details_win_and_partial_details_are_not_reported(tmp_path):
    def body(context: ToolContext, tool: Scripted) -> Any:
        sink(context)(ToolPartialResult("so far", {"partial": True}))
        return RETAINED_OUTPUT, NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path)
    assert result.content.value == "so far"
    assert result.details is NO_TOOL_DETAILS  # as Pi's agent: no partial fallback

    def explicit(context: ToolContext, tool: Scripted) -> Any:
        context.report_details({"reported": True})
        return "done", {"final": True}

    result, _, _ = run(explicit, tmp_path)
    assert result.details == {"final": True}


def test_without_an_executor_the_context_api_is_inert(tmp_path):
    context = ToolContext(tmp_path)
    assert context.retained_output() == RetainedOutputSnapshot("", False)
    context.report_details({"x": 1})
    context.report_diagnostic(severity="warning", message="ignored")


def test_invalid_reports_raise_to_the_tool(tmp_path):
    window = RetainedOutputWindow(None)
    with pytest.raises(ValueError):
        window.report_diagnostic(severity="warn", message="Pi spelling")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        window.report_details({"nan": float("nan")})


def test_omitted_output_cannot_carry_blocks():
    from pipy_harness.native.agent.content import TextContent

    with pytest.raises(ValueError, match="omitted output"):
        ToolExecutionResult(
            "pipy-tool-request-x", RETAINED_OUTPUT, blocks=(TextContent("a"),)
        )


def test_the_largest_window_and_its_diagnostics_fit_a_tool_result(tmp_path):
    """Design §4.6 item 4: the 50 KB window plus the diagnostics block stays
    within ``AgentToolResultMessage.CONTENT_MAX_LENGTH``."""

    def body(context: ToolContext, tool: Scripted) -> Any:
        context.report_diagnostic(severity="error", message="m" * 13000)
        sink(context)("y" * (200 * 1024))  # one huge line: bytes bound
        return RETAINED_OUTPUT, NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path)
    assert len(result.content.value) <= AgentToolResultMessage.CONTENT_MAX_LENGTH
    assert "(truncated)] Output truncated to its end" in result.content.value


def test_the_window_holds_bounded_memory_however_much_output_passes(tmp_path):
    """Owner question 3: the ordinary executor holds the window in memory.
    Measured: about 150 KB per running call (twice the 50 KB byte bound of
    rolling input plus the 50 KB window), independent of the output volume."""
    window = RetainedOutputWindow(None)
    tracemalloc.start()
    try:
        for _ in range(2000):
            window(CHUNK * 10)  # 2 MB in total
        current, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert current < 400 * 1024


def test_diagnostics_are_budgeted_as_rendered_lines_and_must_be_utf8(tmp_path):
    """Review R1: many empty diagnostics cannot overflow the result; lone
    surrogates are refused, as the durable host refuses them."""
    window = RetainedOutputWindow(None)
    with pytest.raises(ValueError, match="budget"):
        for _ in range(10_000):
            window.report_diagnostic(severity="info", message="")
    with pytest.raises(ValueError):
        RetainedOutputWindow(None).report_diagnostic(severity="info", message="\ud800")

    def body(context: ToolContext, tool: Scripted) -> Any:
        try:
            while True:
                context.report_diagnostic(severity="info", message="")
        except ValueError:
            pass
        return "done", NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path)
    assert result.content.value.startswith("done\n<harness>\n[info] \n")


def test_an_explicit_output_too_long_for_its_diagnostics_is_bounded(tmp_path):
    """Review R1: only a tool that reports diagnostics; the cut is the window's,
    with the truncated diagnostic, and image blocks stay consistent."""
    from pipy_harness.native.agent.content import ImageContent, TextContent

    def body(context: ToolContext, tool: Scripted) -> Any:
        context.report_diagnostic(severity="info", message="hi")
        return "z" * ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH, NO_TOOL_DETAILS

    result, _, _ = run(body, tmp_path)
    text = result.content.value
    assert text == "z" * (50 * 1024) + (
        "\n<harness>\n[tool output truncated]\n[info] hi\n</harness>"
    )
    assert len(text) <= ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH

    class Imaged(Scripted):
        def invoke(self, request: Any, context: ToolContext) -> ToolExecutionResult:
            context.report_diagnostic(severity="info", message="hi")
            image = ImageContent(data="aGk=", mime_type="image/png")
            big = "q" * (ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH - 1)
            return ToolExecutionResult(
                request.tool_request_id, big, blocks=(TextContent(big), image)
            )

    outcome = ToolExecutor({"scripted": Imaged(None)}).execute(
        AgentToolCall("c", "scripted", ProductContent("{}")), ToolContext(tmp_path)
    )
    assert any(type(b) is ImageContent for b in outcome.result.blocks)
    assert len(outcome.result.content.value) <= (
        ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
    )
