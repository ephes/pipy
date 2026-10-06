"""CM1 T3: the model-visible result text of a codemode run (spike §4.6)."""

from __future__ import annotations

import pytest

from pipy_harness.native.codemode.outcome import (
    CallStatus,
    ErrorKind,
    ScriptError,
    ScriptOutcome,
    ToolCallRecord,
)
from pipy_harness.native.codemode.result import (
    call_summary,
    format_result,
    join_output,
    truncate_output,
)


def test_a_completed_run_has_the_header_and_output_only() -> None:
    outcome = ScriptOutcome(
        None,
        output=("one\n", "two"),
        calls=(ToolCallRecord("read", CallStatus.OK),),
        wall_seconds=1.24,
    )
    result = format_result(outcome)
    assert result.text == "Script completed\nWall time 1.2 seconds\nOutput:\none\ntwo"
    assert result.is_error is False
    assert result.full_output is None


def test_an_empty_completed_run_matches_pi_header() -> None:
    result = format_result(ScriptOutcome(None, wall_seconds=0.04))
    assert result.text == "Script completed\nWall time 0.0 seconds\nOutput:\n"


@pytest.mark.parametrize(
    ("kind", "message", "head"),
    [
        (
            ErrorKind.SCRIPT,
            "Traceback (most recent call last):\nValueError: x",
            "Traceback (most recent call last):\nValueError: x",
        ),
        (
            ErrorKind.TIMEOUT,
            "Execution timed out after 120 s",
            "Script timed out: Execution timed out after 120 s",
        ),
        (ErrorKind.ABORTED, "Execution aborted", "Script aborted: Execution aborted"),
        (
            ErrorKind.SANDBOX,
            "protocol violation: x",
            "Script sandbox failed: protocol violation: x",
        ),
    ],
)
def test_a_failed_run_keeps_output_then_the_error_and_call_summary(
    kind: ErrorKind, message: str, head: str
) -> None:
    outcome = ScriptOutcome(
        ScriptError(kind, message),
        output=("partial",),
        calls=(
            ToolCallRecord("write", CallStatus.OK),
            ToolCallRecord("bash", CallStatus.ERROR),
            ToolCallRecord("read", CallStatus.CANCELLED),
        ),
        wall_seconds=2.0,
    )
    result = format_result(outcome)
    assert result.is_error is True
    assert result.text == (
        "Script failed\nWall time 2.0 seconds\nOutput:\npartial\n"
        f"Script error:\n{head}\n\n"
        "Tool calls made before the failure (they are not undone): "
        "write (ok), bash (error), read (cancelled)"
    )


def test_a_failure_without_calls_says_so() -> None:
    outcome = ScriptOutcome(ScriptError(ErrorKind.SCRIPT, "NameError: x"))
    assert format_result(outcome).text.endswith(
        "Output:\nScript error:\nNameError: x\n\nNo tool calls were made."
    )
    assert call_summary(()) == "No tool calls were made."


def test_items_start_on_new_lines_without_doubling_print_newlines() -> None:
    assert join_output(["a\n", "b", "c", "d\n", "e"]) == "a\nb\nc\nd\ne"
    assert join_output([]) == ""


def test_truncation_keeps_head_and_tail_like_pi() -> None:
    text = "h" * 30 + "\n" + "m" * 50 + "t" * 19
    truncated = truncate_output(text, max_tokens=10)  # 40 characters
    assert truncated == (
        "Warning: truncated output (original token count: 25)\n"
        "Total output lines: 2\n\n"
        + "h" * 20
        + "…15 tokens truncated…"
        + "m" * 1
        + "t" * 19
    )
    assert truncate_output("short", max_tokens=10) == "short"
    with pytest.raises(ValueError):
        truncate_output("x", max_tokens=0)


def test_truncation_covers_the_error_and_exposes_the_full_text() -> None:
    outcome = ScriptOutcome(
        ScriptError(ErrorKind.SCRIPT, "E" * 100),
        output=("o" * 100,),
    )
    result = format_result(outcome, max_output_tokens=20)
    assert result.text.startswith(
        "Script failed\nWall time 0.0 seconds\nOutput:\nWarning: truncated output"
    )
    assert result.text.endswith("No tool calls were made.")
    assert result.full_output is not None
    assert result.full_output.startswith("o" * 100 + "\nScript error:\n" + "E" * 100)
