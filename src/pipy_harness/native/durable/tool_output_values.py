"""Closed diagnostic/failure facts and their deterministic model projection."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pipy_harness.native.agent.content import ProductContent, TextContent
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.tools.truncate import truncate_tail

from .records import safe_integer, shape
from .tool_progress_values import (
    FAILURE_CATEGORIES,
    OUTPUT_BYTES,
    OUTPUT_LINES,
    diagnostics,
)


def failure_text(category: object) -> str:
    if type(category) is not str or category not in FAILURE_CATEGORIES:
        raise ValueError("Unsupported durable tool failure")
    return "Tool failed (" + category + ")."


INTERRUPTED_TEXT = "Tool execution interrupted; result is uncertain."


def ending_text(tree: dict[str, Any]) -> str:
    """The same closed cause determines projection and raw terminal failure."""
    if tree["ending"] == "interrupted":
        return INTERRUPTED_TEXT
    return failure_text(tree["recovery"]["category"])


def check_ending(tree: dict[str, Any], outcome: ToolExecutionOutcome) -> None:
    """Validate the entire cause before any projection or reader acceptance."""
    diagnostics(tree["diagnostics"])
    ending, recovery = tree["ending"], tree["recovery"]
    if type(ending) is not str or ending not in ("completed", "failed", "interrupted"):
        raise ValueError("Unsupported durable tool ending")
    if ending == "completed":
        if recovery is not None:
            raise ValueError("Completed output cannot have a failure cause")
        return
    if ending == "interrupted":
        _interrupted(recovery, outcome)
    else:
        cause = shape(recovery, {"reason", "category"})
        if cause["reason"] != "tool_error":
            raise ValueError("Unsupported durable tool failure cause")
        failure_text(cause["category"])
    if (
        not outcome.result.is_error
        or outcome.result.terminate
        or outcome.cancelled
        or outcome.malformed_arguments
        or outcome.interruption is not ToolExecutionInterruption.SETTLED
    ):
        raise ValueError("Unsupported failed tool controls")


def _interrupted(recovery: object, outcome: ToolExecutionOutcome) -> None:
    cause = shape(recovery, {"reason", "attempts", "restarts"})
    if cause["reason"] != "interrupted" or outcome.execution_seconds is not None:
        raise ValueError("Unsupported durable interrupted cause")
    for key in ("attempts", "restarts"):
        if cause[key] is not None and safe_integer(cause[key], positive=False) < 0:
            raise ValueError("Unsupported durable interrupted counter")


def _projected_blocks(
    message: AgentToolResultMessage, retained: str, suffix: str
) -> tuple[Any, ...]:
    if not message.blocks:
        return ()
    start = (
        message.content.value.rfind(retained)
        if retained
        else len(message.content.value)
    )
    if start < 0:
        raise ValueError("Invalid durable output projection")
    end, position, seen_text = start + len(retained), 0, False
    result = []
    for block in message.blocks:
        if type(block) is not TextContent:
            result.append(block)
            continue
        if seen_text:
            position += 1
        seen_text = True
        left, right = max(0, start - position), min(len(block.text), end - position)
        # Empty edge blocks can own a retained separator (leading/trailing
        # newline). Dropping them changes the model text even though their
        # own text slice is empty. An entirely empty tail owns no separator.
        if retained and right >= left:
            text = block.text[left:right]
            result.append(block if text == block.text else TextContent(text))
        position += len(block.text)
    if suffix:
        result.append(TextContent(suffix))
    return tuple(result)


def project_tool_output(
    tree: dict[str, Any], outcome: ToolExecutionOutcome
) -> AgentToolResultMessage:
    """Full fact owns originals; bounded derived text owns only valid signatures."""
    if tree["version"] == 1:
        return outcome.result
    check_ending(tree, outcome)
    notices = [item.render() for item in diagnostics(tree["diagnostics"])]
    tail = truncate_tail(
        outcome.result.content.value, max_bytes=OUTPUT_BYTES, max_lines=OUTPUT_LINES
    )
    if tail.truncated:
        notices.insert(0, "[tool output truncated]")
    if tree["ending"] in ("failed", "interrupted"):
        notices.append(ending_text(tree))
    suffix = "<harness>\n" + "\n".join(notices) + "\n</harness>" if notices else ""
    text = tail.content + ("\n" if tail.content and suffix else "") + suffix
    return replace(
        outcome.result,
        content=ProductContent(text),
        blocks=_projected_blocks(outcome.result, tail.content, suffix),
    )
