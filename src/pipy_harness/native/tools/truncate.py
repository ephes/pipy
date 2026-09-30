"""Shared truncation helpers for tool output, ported from Pi.

Mirrors ``packages/coding-agent/src/core/tools/truncate.ts`` (pi-mono
``4df157433``). Truncation applies two independent limits, and whichever is
hit first wins: a line limit (default 2000 lines) and a byte limit (default
50 KB). Head truncation never returns a partial line; tail truncation returns
a partial line only when the last line alone exceeds the byte limit.

Pi's ``truncateMiddle`` has no caller among pipy's tools and is not ported.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class TruncationResult:
    """Pi's ``TruncationResult``."""

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

    def to_details(self) -> dict[str, object]:
        """The object Pi stores as ``details.truncation`` (camelCase)."""

        return {
            "content": self.content,
            "truncated": self.truncated,
            "truncatedBy": self.truncated_by,
            "totalLines": self.total_lines,
            "totalBytes": self.total_bytes,
            "outputLines": self.output_lines,
            "outputBytes": self.output_bytes,
            "lastLinePartial": self.last_line_partial,
            "firstLineExceedsLimit": self.first_line_exceeds_limit,
            "maxLines": self.max_lines,
            "maxBytes": self.max_bytes,
        }


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


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the last lines of ``content`` that fit both limits (Pi ``truncateTail``).

    Suits command output, where the end holds errors and final results. If the
    last line alone exceeds the byte limit, its end is kept and
    ``last_line_partial`` is set.
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

    output: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(output) >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if output else 0)
        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not output:
                partial = _truncate_string_to_bytes_from_end(line, max_bytes)
                output.append(partial)
                output_bytes_count = _byte_length(partial)
                last_line_partial = True
            break
        output.append(line)
        output_bytes_count += line_bytes
    output.reverse()

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
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    start = len(encoded) - max_bytes
    # Advance past UTF-8 continuation bytes to the start of a character.
    while start < len(encoded) and (encoded[start] & 0xC0) == 0x80:
        start += 1
    return encoded[start:].decode("utf-8")


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    """Cut one line to ``max_chars`` with a ``... [truncated]`` suffix (Pi ``truncateLine``).

    Returns the text and whether it was cut. Pi counts UTF-16 code units;
    pipy counts code points, which differs only for astral characters.
    """

    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "TruncationResult",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
