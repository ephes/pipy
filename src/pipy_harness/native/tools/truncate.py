"""Shared truncation helpers for tool output, ported from Pi.

Mirrors ``packages/coding-agent/src/core/tools/truncate.ts`` (pi-mono
``4df157433``). Truncation applies two independent limits, and whichever is
hit first wins: a line limit (default 2000 lines) and a byte limit (default
50 KB). Head truncation never returns a partial line.

Only the helpers the ``read`` tool uses are ported so far; Pi's
``truncateTail``, ``truncateLine`` and ``truncateMiddle`` arrive with the
tools that use them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


@dataclass(frozen=True, slots=True)
class TruncationResult:
    """Pi's ``TruncationResult`` for head truncation."""

    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_lines_for_counting(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def format_size(num_bytes: int) -> str:
    """Format a byte count like Pi's ``formatSize``.

    JavaScript's ``toFixed(1)`` rounds an exact tie up (1280 B is ``1.3KB``),
    where Python's float formatting rounds half to even. Both quotients are
    exact in binary, so ``Decimal`` with ``ROUND_HALF_UP`` reproduces Pi.
    """

    if num_bytes < 1024:
        return f"{num_bytes}B"
    tenth = Decimal("0.1")
    if num_bytes < 1024 * 1024:
        value = (Decimal(num_bytes) / Decimal(1024)).quantize(tenth, ROUND_HALF_UP)
        return f"{value}KB"
    value = (Decimal(num_bytes) / Decimal(1024 * 1024)).quantize(tenth, ROUND_HALF_UP)
    return f"{value}MB"


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the first lines of ``content`` that fit both limits.

    Never returns a partial line. If the first line alone exceeds the byte
    limit, the content is empty and ``first_line_exceeds_limit`` is set.
    """

    total_bytes = _byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    if _byte_length(lines[0]) > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    output: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = _byte_length(line) + (1 if index > 0 else 0)
        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        output.append(line)
        output_bytes_count += line_bytes

    if len(output) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(output)
    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output),
        output_bytes=_byte_length(output_content),
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "TruncationResult",
    "format_size",
    "truncate_head",
]
