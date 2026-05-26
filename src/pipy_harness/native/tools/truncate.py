"""The `truncate` tool: shrink an oversized text payload deterministically.

`TruncateTool` is a pure-transformation tool the model can call to bound a
previous payload it intends to feed back into the loop. It performs no I/O,
no archive event emission, and no provider interaction. Given an input text
and optional `max_bytes` and `max_lines` caps, the tool either returns the
text unchanged (if both caps are satisfied) or returns a deterministic
head + omission marker + tail composition that fits both caps.

The omission marker is a single line of the shape
``--- omitted N lines, M bytes ---`` reporting the exact number of lines
and bytes elided between the head and tail slices. Output is fully
deterministic: identical inputs always produce identical outputs.

This tool returns provider-visible content through `ToolExecutionResult`
and emits no archive events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

DEFAULT_MAX_BYTES = 8192
DEFAULT_MAX_LINES = 200
MIN_MAX_BYTES = 1
MAX_MAX_BYTES = 65536
MIN_MAX_LINES = 1
MAX_MAX_LINES = 5000


def _marker(omitted_lines: int, omitted_bytes: int) -> str:
    """Return the deterministic omission marker line."""

    return f"--- omitted {omitted_lines} lines, {omitted_bytes} bytes ---"


@dataclass(frozen=True, slots=True)
class TruncateTool:
    """Shrink an oversized text payload with a deterministic marker."""

    DEFAULT_MAX_BYTES: ClassVar[int] = DEFAULT_MAX_BYTES
    DEFAULT_MAX_LINES: ClassVar[int] = DEFAULT_MAX_LINES
    MIN_MAX_BYTES: ClassVar[int] = MIN_MAX_BYTES
    MAX_MAX_BYTES: ClassVar[int] = MAX_MAX_BYTES
    MIN_MAX_LINES: ClassVar[int] = MIN_MAX_LINES
    MAX_MAX_LINES: ClassVar[int] = MAX_MAX_LINES

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="truncate",
            description=(
                "Shrink an oversized text payload to fit byte and line caps. "
                "Returns the input unchanged if it already fits both caps. "
                "Otherwise returns the first head_lines lines, a deterministic "
                "omission marker reporting elided line and byte counts, and "
                "the last tail_lines lines. This tool performs no I/O; it is "
                "a pure transformation the model can use to summarize a "
                "previous tool result for itself."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to potentially truncate.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "minimum": MIN_MAX_BYTES,
                        "maximum": MAX_MAX_BYTES,
                        "description": (
                            "Soft cap on the UTF-8 byte length of the output. "
                            f"Default {DEFAULT_MAX_BYTES}; "
                            f"bounded to [{MIN_MAX_BYTES}, {MAX_MAX_BYTES}]."
                        ),
                    },
                    "max_lines": {
                        "type": "integer",
                        "minimum": MIN_MAX_LINES,
                        "maximum": MAX_MAX_LINES,
                        "description": (
                            "Soft cap on the number of lines in the output. "
                            f"Default {DEFAULT_MAX_LINES}; "
                            f"bounded to [{MIN_MAX_LINES}, {MAX_MAX_LINES}]."
                        ),
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        del context  # pure transformation; no workspace access
        arguments = request.arguments
        if "text" not in arguments:
            raise ToolArgumentError(
                "truncate",
                "text is required",
                field_path=("text",),
            )
        text = arguments["text"]
        if not isinstance(text, str):
            raise ToolArgumentError(
                "truncate",
                "text must be a string",
                field_path=("text",),
            )

        max_bytes = self._coerce_bound(
            value=arguments.get("max_bytes", DEFAULT_MAX_BYTES),
            field="max_bytes",
            minimum=MIN_MAX_BYTES,
            maximum=MAX_MAX_BYTES,
        )
        max_lines = self._coerce_bound(
            value=arguments.get("max_lines", DEFAULT_MAX_LINES),
            field="max_lines",
            minimum=MIN_MAX_LINES,
            maximum=MAX_MAX_LINES,
        )

        output = _truncate_text(text, max_bytes=max_bytes, max_lines=max_lines)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _coerce_bound(
        *, value: Any, field: str, minimum: int, maximum: int
    ) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < minimum
            or value > maximum
        ):
            raise ToolArgumentError(
                "truncate",
                f"{field} must be an integer in [{minimum}, {maximum}]",
                field_path=(field,),
            )
        return value


def _truncate_text(text: str, *, max_bytes: int, max_lines: int) -> str:
    """Return `text` unchanged or a deterministic head+marker+tail composition.

    The head and tail are taken in whole-line slices. If the composed output
    still exceeds `max_bytes` (because individual lines themselves are large),
    the head and tail are shrunk one line at a time, alternating from tail
    first to keep the most recent context, until the marker plus the
    remaining lines fit. As a last resort the marker alone is returned
    (truncated to `max_bytes` if necessary).
    """

    total_bytes = len(text.encode("utf-8"))
    # `count('\n') + 1` matches the spec; an empty string has 1 line by
    # this metric, which is fine because both caps are >= 1.
    total_lines = text.count("\n") + 1
    if total_bytes <= max_bytes and total_lines <= max_lines:
        return text

    lines = text.split("\n")
    head_lines = max_lines // 2
    tail_lines = max_lines - head_lines - 1
    if head_lines < 0:
        head_lines = 0
    if tail_lines < 0:
        tail_lines = 0
    # Ensure head + tail does not exceed the total line count; otherwise
    # the "omitted" region would have negative size.
    if head_lines + tail_lines >= len(lines):
        # Nothing meaningful to omit on the line axis; clamp so at least
        # one line is elided when we got here because of the line cap, or
        # the byte-cap pass below takes over.
        excess = head_lines + tail_lines - len(lines) + 1
        # Trim from the tail first to preserve the head context.
        trim_from_tail = min(excess, tail_lines)
        tail_lines -= trim_from_tail
        excess -= trim_from_tail
        if excess > 0:
            head_lines = max(0, head_lines - excess)

    return _compose(
        lines=lines,
        head_lines=head_lines,
        tail_lines=tail_lines,
        max_bytes=max_bytes,
    )


def _compose(
    *,
    lines: list[str],
    head_lines: int,
    tail_lines: int,
    max_bytes: int,
) -> str:
    """Build a head + marker + tail composition that fits `max_bytes`."""

    total = len(lines)
    # Iteratively shrink until the encoded output fits. Each iteration drops
    # one line, alternating tail-first then head, to keep recent context.
    drop_from_tail_next = False
    while True:
        head_slice = lines[:head_lines] if head_lines > 0 else []
        tail_slice = lines[total - tail_lines:] if tail_lines > 0 else []
        omitted_lines = total - head_lines - tail_lines
        if omitted_lines < 0:
            omitted_lines = 0
        omitted_bytes = _omitted_bytes(
            lines=lines,
            head_lines=head_lines,
            tail_lines=tail_lines,
        )
        marker = _marker(omitted_lines, omitted_bytes)
        parts: list[str] = []
        if head_slice:
            parts.append("\n".join(head_slice))
        parts.append(marker)
        if tail_slice:
            parts.append("\n".join(tail_slice))
        output = "\n".join(parts)
        if len(output.encode("utf-8")) <= max_bytes:
            return output
        # Still too big: drop one line, preferring tail first so we keep
        # the head intact, then head, alternating to balance shrinkage.
        if head_lines == 0 and tail_lines == 0:
            # Cannot shrink further. Return the marker alone, byte-clamped.
            return _clamp_to_bytes(marker, max_bytes)
        if drop_from_tail_next and tail_lines > 0:
            tail_lines -= 1
        elif head_lines > 0:
            head_lines -= 1
        elif tail_lines > 0:
            tail_lines -= 1
        drop_from_tail_next = not drop_from_tail_next


def _omitted_bytes(
    *, lines: list[str], head_lines: int, tail_lines: int
) -> int:
    """Return the UTF-8 byte count of the omitted middle slice."""

    total = len(lines)
    start = head_lines
    end = total - tail_lines
    if end <= start:
        return 0
    middle = lines[start:end]
    # Include the newlines that separated the omitted lines from each other
    # and from their neighbors, so the reported byte count corresponds to
    # the actual span removed from the source text.
    text = "\n".join(middle)
    extra_newlines = 0
    if head_lines > 0:
        extra_newlines += 1
    if tail_lines > 0:
        extra_newlines += 1
    return len(text.encode("utf-8")) + extra_newlines


def _clamp_to_bytes(text: str, max_bytes: int) -> str:
    """Clamp `text` so its UTF-8 encoding fits within `max_bytes`."""

    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    if max_bytes <= 0:
        return ""
    # Walk back to a UTF-8 boundary so we never split a multi-byte char.
    end = max_bytes
    while end > 0 and (encoded[end - 1] & 0xc0) == 0x80:
        end -= 1
    # If we are sitting on a leading byte of a multi-byte sequence, drop it.
    if end > 0 and (encoded[end - 1] & 0xc0) == 0xc0:
        end -= 1
    return encoded[:end].decode("utf-8", errors="ignore")


__all__ = ["TruncateTool"]
