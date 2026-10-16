"""The model-visible text of a codemode run (spike result §4.6, Pi-adopted).

::

    Script completed|Script failed
    Wall time 1.2 seconds
    Output:
    <text() items and images, in order, then the print() output; truncated per budget>
    Script error:
    <traceback | Script timed out: … | Script aborted: … | Script sandbox failed: …>

    Tool calls made before the failure (they are not undone): read (ok), bash (error)

Output produced before a failure is kept. The error block and the call
summary appear only on failure; with no calls the summary is "No tool calls
were made." Output and error are truncated together to a token budget
(chars/4), keeping the head and the tail. Spilling the full text to a file is
the composite caller's job (T8a): :class:`ResultText` hands it the untruncated text.

Pi ``formatOutput`` lays the output out so the model can tell items apart:
with more than one ``text()`` item, each starts with a ``==> text N/M <==``
line, and what the script printed (``print()`` and writes to ``sys.stdout``
or ``sys.stderr``, Pi's ``console.*``) follows all other output in one
``<console_output>`` block.

Images a script showed with ``image()`` stay where they are in the output,
between the text around them (Pi ``formatOutput``). They are never cut: when
the text is over the budget it becomes one truncated item and the images
follow it (Pi ``truncateOutput``). Saving each image to a file and naming
its path is the composite caller's job as well.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pipy_harness.native.codemode.outcome import (
    ConsoleText,
    ErrorKind,
    ScriptError,
    ScriptImage,
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
    """``text`` is what the model sees as text; ``is_error`` marks a failed run.

    ``full_output`` is the untruncated output and error block when
    ``text`` was truncated, else ``None``.

    ``header`` and ``items`` are the same result in parts: the three header
    lines, then the output in order, each item a text or an image, adjacent
    texts joined. Without an image there is one text item and ``text`` is
    the header followed by it. With images, ``text`` is the header and the
    text items, each on its own line.
    """

    text: str
    is_error: bool
    full_output: str | None = None
    header: str = ""
    items: tuple[str | ScriptImage, ...] = ()


def format_result(
    outcome: ScriptOutcome, *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
) -> ResultText:
    status = "Script completed" if outcome.ok else "Script failed"
    header = f"{status}\nWall time {outcome.wall_seconds:.1f} seconds\nOutput:\n"
    output = format_output(outcome.output)
    if outcome.error is not None:
        output.append(f"Script error:\n{error_text(outcome.error, outcome.calls)}")
    items = join_adjacent_text(output)
    images = [item for item in items if isinstance(item, ScriptImage)]
    texts = [item for item in items if isinstance(item, str)]
    # Pi ``truncateOutput``: the budget is for the text items joined by newlines.
    body = "\n".join(texts)
    shown = truncate_output(body, max_output_tokens) if texts else ""
    truncated = shown != body
    if truncated or not images:
        items = [shown, *images]
    return ResultText(
        text=header + (shown if truncated else body),
        is_error=not outcome.ok,
        full_output=body if truncated else None,
        header=header,
        items=tuple(items),
    )


def format_output(
    output: Iterable[str | ScriptImage],
) -> list[str | ScriptImage]:
    """Pi ``formatOutput``: mark where each ``text()`` item starts, and
    gather the console output into one block after everything else.

    With more than one ``text()`` item, each starts with a
    ``==> text N/M <==`` line. Console output is a stream (a ``print()``
    ends its line itself), so its writes are joined as they are; the block
    ends its last line when the script did not.
    """

    items = list(output)
    total = sum(
        1
        for item in items
        if isinstance(item, str) and not isinstance(item, ConsoleText)
    )
    formatted: list[str | ScriptImage] = []
    console: list[str] = []
    index = 0
    for item in items:
        if isinstance(item, ScriptImage):
            formatted.append(item)
        elif isinstance(item, ConsoleText):
            console.append(item)
        else:
            index += 1
            formatted.append(
                f"==> text {index}/{total} <==\n{item}" if total > 1 else str(item)
            )
    if console:
        stream = "".join(console)
        end = "" if stream.endswith("\n") else "\n"
        formatted.append(f"<console_output>\n{stream}{end}</console_output>")
    return formatted


def join_adjacent_text(
    items: Iterable[str | ScriptImage],
) -> list[str | ScriptImage]:
    """Join each run of text items into one item (:func:`join_output`)."""

    joined: list[str | ScriptImage] = []
    run: list[str] = []
    for item in items:
        if isinstance(item, str):
            run.append(item)
            continue
        if run:
            joined.append(join_output(run))
            run = []
        joined.append(item)
    if run:
        joined.append(join_output(run))
    return joined


def join_output(items: Iterable[str]) -> str:
    """Join text items in order, each starting on its own line (Pi
    ``joinAdjacentText``): a newline goes between two items unless the
    text so far is empty or already ends with one."""

    parts: list[str] = []
    # Whether the text so far is empty or ends with a newline.
    ends_line = True
    for item in items:
        if not ends_line:
            parts.append("\n")
        parts.append(item)
        ends_line = not item or item.endswith("\n")
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
    """Keep the head and tail of ``text`` within ``max_tokens`` (chars/4).

    Pi allows 0, which keeps neither.
    """

    if max_tokens < 0:
        raise ValueError("max_tokens must not be negative")
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
