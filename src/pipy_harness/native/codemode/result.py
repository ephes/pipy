"""The model-visible text of a codemode run (spike result §4.6, Pi-adopted).

::

    Script completed|Script failed
    Wall time 1.2 seconds
    Output:
    <text/print output, in order, truncated per budget>
    Script error:
    <traceback | Script timed out: … | Script aborted: … | Script sandbox failed: …>

    Tool calls made before the failure (they are not undone): read (ok), bash (error)

Output produced before a failure is kept. The error block and the call
summary appear only on failure; with no calls the summary is "No tool calls
were made." Output and error are truncated together to a token budget
(chars/4), keeping the head and the tail. Spilling the full text to a file is
the caller's job (T8): :class:`ResultText` hands it the untruncated text.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pipy_harness.native.codemode.outcome import (
    ErrorKind,
    ScriptError,
    ScriptOutcome,
    ToolCallRecord,
)

DEFAULT_MAX_OUTPUT_TOKENS = 10_000
CHARS_PER_TOKEN = 4

_ERROR_PREFIX = {
    ErrorKind.TIMEOUT: "Script timed out: ",
    ErrorKind.ABORTED: "Script aborted: ",
    ErrorKind.SANDBOX: "Script sandbox failed: ",
}


@dataclass(frozen=True, slots=True)
class ResultText:
    """``text`` is what the model sees; ``is_error`` marks a failed run.

    ``full_output`` is the untruncated output and error block when
    ``text`` was truncated, else ``None``.
    """

    text: str
    is_error: bool
    full_output: str | None = None


def format_result(
    outcome: ScriptOutcome, *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
) -> ResultText:
    status = "Script completed" if outcome.ok else "Script failed"
    header = f"{status}\nWall time {outcome.wall_seconds:.1f} seconds\nOutput:\n"
    items = list(outcome.output)
    if outcome.error is not None:
        items.append(f"Script error:\n{error_text(outcome.error, outcome.calls)}")
    body = join_output(items)
    shown = truncate_output(body, max_output_tokens)
    return ResultText(
        text=header + shown,
        is_error=not outcome.ok,
        full_output=body if shown != body else None,
    )


def join_output(items: Iterable[str]) -> str:
    """Join output items in order, starting each on a new line.

    ``print()`` items already end with a newline; a ``text()`` item does not,
    so a newline is inserted after it (Pi shows each ``text()`` value as its
    own block).
    """

    parts: list[str] = []
    for item in items:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(item)
    return "".join(parts)


def error_text(error: ScriptError, calls: Sequence[ToolCallRecord]) -> str:
    head = _ERROR_PREFIX.get(error.kind, "") + error.message
    return f"{head}\n\n{call_summary(calls)}"


def call_summary(calls: Sequence[ToolCallRecord]) -> str:
    if not calls:
        return "No tool calls were made."
    listed = ", ".join(f"{call.name} ({call.status})" for call in calls)
    return f"Tool calls made before the failure (they are not undone): {listed}"


def truncate_output(text: str, max_tokens: int) -> str:
    """Keep the head and tail of ``text`` within ``max_tokens`` (chars/4)."""

    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    budget = max_tokens * CHARS_PER_TOKEN
    if len(text) <= budget:
        return text
    head_chars = budget // 2
    tail_chars = budget - head_chars
    removed = len(text) - head_chars - tail_chars
    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    return (
        "Warning: truncated output (original token count: "
        f"{math.ceil(len(text) / CHARS_PER_TOKEN)})\n"
        f"Total output lines: {len(text.split(chr(10)))}\n\n"
        f"{head}…{math.ceil(removed / CHARS_PER_TOKEN)} tokens truncated…{tail}"
    )
