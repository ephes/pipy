"""The `edit_diff` tool: unified-diff-driven workspace edits.

`EditDiffTool` reads an existing workspace-relative file, applies a
unified-diff patch supplied by the caller, and writes the result back
atomically (temp file + rename). The resulting unified diff is streamed
to `ToolContext.stderr_sink`, mirroring `WriteTool`/`EditTool`. The
archive boundary is unaffected; the tool only returns a
`ToolExecutionResult`.

The unified-diff parser is in-process and stdlib-only. It refuses
malformed headers, mismatched context, and any hunk whose context or
deletion lines do not match the file at the claimed line numbers. On
failure no bytes are written.
"""

from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass, field
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
    _is_relative_to,
    _resolved_relative_label,
    _validate_workspace_relative_path,
)
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


@dataclass(frozen=True, slots=True)
class _Hunk:
    """One parsed hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    # Each entry is a tuple of (tag, line_without_prefix) where tag is one of
    # " ", "-", "+". Lines retain their trailing newline (if present).
    body: tuple[tuple[str, str], ...] = field(default_factory=tuple)


class _DiffParseError(ValueError):
    """Raised when the unified diff text cannot be parsed."""


@dataclass(frozen=True, slots=True)
class EditDiffTool:
    """Apply a unified-diff patch to a workspace-relative file."""

    max_content_bytes: int = 256 * 1024

    HARD_MAX_CONTENT_BYTES: ClassVar[int] = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_content_bytes, int)
            or isinstance(self.max_content_bytes, bool)
            or self.max_content_bytes < 1
            or self.max_content_bytes > self.HARD_MAX_CONTENT_BYTES
        ):
            raise ValueError(
                "EditDiffTool max_content_bytes must be in "
                f"[1, {self.HARD_MAX_CONTENT_BYTES}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit_diff",
            description=(
                "Apply a unified-diff patch to an existing workspace-relative "
                "UTF-8 file. The diff must include `--- a/<path>` and "
                "`+++ b/<path>` markers and at least one `@@` hunk. Context "
                "and deletion lines must match the file exactly at the "
                "claimed line numbers. Refuses .git, ignored paths, absolute "
                "paths, parent traversal, missing files, and any hunk that "
                "fails to apply; on failure the file is not modified."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path of the file to edit."
                        ),
                    },
                    "unified_diff": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": self.max_content_bytes,
                        "description": (
                            "Unified-diff text including `--- a/<path>`, "
                            "`+++ b/<path>`, and at least one `@@` hunk."
                        ),
                    },
                },
                "required": ["path", "unified_diff"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        unified_diff = request.arguments["unified_diff"]

        try:
            _validate_workspace_relative_path(path_arg)
        except ValueError as exc:
            raise ToolArgumentError(
                "edit_diff", str(exc), field_path=("path",)
            ) from None
        if not isinstance(unified_diff, str) or not unified_diff:
            raise ToolArgumentError(
                "edit_diff",
                "unified_diff must be a non-empty string",
                field_path=("unified_diff",),
            )

        workspace = context.workspace_root.resolve()
        candidate = (workspace / path_arg).resolve()
        if not _is_relative_to(candidate, workspace):
            return self._error(request, "path escapes the workspace")
        resolved_label = _resolved_relative_label(candidate, workspace)
        if resolved_label is None:
            return self._error(request, "path escapes the workspace")
        if _is_ignored_or_generated(
            path_arg, workspace
        ) or _is_ignored_or_generated(resolved_label, workspace):
            return self._error(
                request,
                "path is ignored or under .git/generated directories",
            )
        if not candidate.exists():
            return self._error(request, "file does not exist")
        if not candidate.is_file():
            return self._error(request, "path is not a regular file")

        try:
            original_bytes = candidate.read_bytes()
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")
        if b"\0" in original_bytes:
            return self._error(request, "binary content detected")
        if len(original_bytes) > self.max_content_bytes:
            return self._error(request, "file exceeds max_content_bytes")
        try:
            original_text = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(request, "non-UTF-8 content")

        try:
            hunks = _parse_unified_diff(unified_diff, path_arg=path_arg)
        except _DiffParseError as exc:
            return self._error(request, f"malformed diff: {exc}")
        if not hunks:
            return self._error(request, "malformed diff: no hunks present")

        original_lines = original_text.splitlines(keepends=True)
        try:
            new_lines = _apply_hunks(original_lines, hunks)
        except _DiffParseError as exc:
            return self._error(request, f"hunk failed to apply: {exc}")
        new_text = "".join(new_lines)

        if len(new_text.encode("utf-8")) > self.max_content_bytes:
            return self._error(
                request, "patched content exceeds max_content_bytes"
            )

        try:
            _atomic_write(candidate, new_text)
        except OSError as exc:
            return self._error(request, f"failed to write file: {exc}")

        diff_text = self._unified_diff(
            path_arg=path_arg,
            original_text=original_text,
            new_text=new_text,
        )
        if context.stderr_sink is not None and diff_text:
            context.stderr_sink(diff_text)

        added = sum(1 for hunk in hunks for tag, _ in hunk.body if tag == "+")
        removed = sum(1 for hunk in hunks for tag, _ in hunk.body if tag == "-")
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=(
                f"applied {len(hunks)} hunk(s) "
                f"({added}+ / {removed}-) to {path_arg}"
            ),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _unified_diff(
        *, path_arg: str, original_text: str, new_text: str
    ) -> str:
        diff_lines = difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path_arg}",
            tofile=f"b/{path_arg}",
        )
        return "".join(diff_lines)

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"edit-diff error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _atomic_write(target, text: str) -> None:
    """Write `text` to `target` atomically via a sibling temp file."""

    parent = target.parent
    original_mode = target.stat().st_mode
    fd, temp_name = tempfile.mkstemp(
        prefix=".edit_diff.",
        suffix=".tmp",
        dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(temp_name, original_mode)
        os.replace(temp_name, str(target))
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _parse_unified_diff(text: str, *, path_arg: str) -> list[_Hunk]:
    """Parse a unified-diff text into a list of hunks.

    Requires a `--- a/<path_arg>` line followed by `+++ b/<path_arg>` and at
    least one `@@ -<s>,<c> +<s>,<c> @@` hunk. Lines outside of hunks are
    rejected (apart from the file headers).
    """

    lines = text.splitlines(keepends=True)
    if not lines:
        raise _DiffParseError("empty diff")

    expected_minus = f"--- a/{path_arg}"
    expected_plus = f"+++ b/{path_arg}"
    index = 0

    # Skip any leading blank lines.
    while index < len(lines) and lines[index].strip() == "":
        index += 1

    if index >= len(lines):
        raise _DiffParseError("missing --- header")
    minus_line = lines[index].rstrip("\n").rstrip("\r")
    if not minus_line.startswith("--- "):
        raise _DiffParseError("missing --- header")
    # Allow optional trailing tab + timestamp by truncating at the first tab.
    minus_value = minus_line[4:].split("\t", 1)[0]
    if minus_value != f"a/{path_arg}":
        raise _DiffParseError(
            f"--- header must be {expected_minus!r}; got {minus_line!r}"
        )
    index += 1

    if index >= len(lines):
        raise _DiffParseError("missing +++ header")
    plus_line = lines[index].rstrip("\n").rstrip("\r")
    if not plus_line.startswith("+++ "):
        raise _DiffParseError("missing +++ header")
    plus_value = plus_line[4:].split("\t", 1)[0]
    if plus_value != f"b/{path_arg}":
        raise _DiffParseError(
            f"+++ header must be {expected_plus!r}; got {plus_line!r}"
        )
    index += 1

    hunks: list[_Hunk] = []
    while index < len(lines):
        header = lines[index]
        header_stripped = header.rstrip("\n").rstrip("\r")
        if not header_stripped.startswith("@@"):
            if header_stripped.strip() == "":
                index += 1
                continue
            raise _DiffParseError(
                f"unexpected line outside hunk: {header_stripped!r}"
            )
        hunk, consumed = _parse_hunk_header_and_body(lines, index)
        hunks.append(hunk)
        index += consumed

    return hunks


def _parse_hunk_header_and_body(
    lines: list[str], start: int
) -> tuple[_Hunk, int]:
    header_line = lines[start].rstrip("\n").rstrip("\r")
    old_start, old_count, new_start, new_count = _parse_hunk_header(header_line)

    body: list[tuple[str, str]] = []
    seen_old = 0
    seen_new = 0
    consumed = 1
    pos = start + 1
    while pos < len(lines):
        line = lines[pos]
        if line.startswith("@@"):
            break
        # `\ No newline at end of file` is informational; tolerate it but do
        # not count it.
        if line.startswith("\\"):
            pos += 1
            consumed += 1
            continue
        if not line:
            # Empty trailing line in the diff text — stop.
            break
        tag = line[0]
        if tag not in (" ", "-", "+"):
            raise _DiffParseError(
                f"unexpected line in hunk: {line.rstrip()!r}"
            )
        body.append((tag, line[1:]))
        if tag in (" ", "-"):
            seen_old += 1
        if tag in (" ", "+"):
            seen_new += 1
        pos += 1
        consumed += 1
        if seen_old >= old_count and seen_new >= new_count:
            break

    if seen_old != old_count or seen_new != new_count:
        raise _DiffParseError(
            "hunk body line counts do not match header "
            f"(expected -{old_count} +{new_count}, "
            f"got -{seen_old} +{seen_new})"
        )

    return (
        _Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            body=tuple(body),
        ),
        consumed,
    )


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    # Expected format: "@@ -<old_start>[,<old_count>] +<new_start>[,<new_count>] @@[ optional]"
    if not header.startswith("@@"):
        raise _DiffParseError(f"invalid hunk header: {header!r}")
    rest = header[2:].lstrip()
    parts = rest.split(" ")
    # We need at least three space-separated tokens: -<...>, +<...>, "@@" (or
    # "@@..." with optional context label).
    if len(parts) < 3:
        raise _DiffParseError(f"invalid hunk header: {header!r}")
    old_token = parts[0]
    new_token = parts[1]
    closer = parts[2]
    if not closer.startswith("@@"):
        raise _DiffParseError(f"invalid hunk header: {header!r}")
    if not old_token.startswith("-") or not new_token.startswith("+"):
        raise _DiffParseError(f"invalid hunk header: {header!r}")
    try:
        old_start, old_count = _parse_range(old_token[1:])
        new_start, new_count = _parse_range(new_token[1:])
    except ValueError as exc:
        raise _DiffParseError(f"invalid hunk header: {header!r}") from exc
    return old_start, old_count, new_start, new_count


def _parse_range(token: str) -> tuple[int, int]:
    if "," in token:
        start_str, count_str = token.split(",", 1)
        start = int(start_str)
        count = int(count_str)
    else:
        start = int(token)
        count = 1
    if start < 0 or count < 0:
        raise ValueError("range numbers must be non-negative")
    return start, count


def _apply_hunks(
    original_lines: list[str], hunks: list[_Hunk]
) -> list[str]:
    """Apply parsed hunks to `original_lines` and return the new lines."""

    result: list[str] = []
    # `cursor` is the 0-based index into original_lines for the next
    # unprocessed source line.
    cursor = 0
    for hunk in hunks:
        # Convert 1-based old_start to 0-based. Empty-count hunks (insertions
        # against an empty source range) use old_start as the line *after*
        # which to insert; the unified-diff convention is that when
        # old_count==0, old_start is the line number after which to insert,
        # so the target index is old_start (1-based -> 0-based is the same).
        if hunk.old_count == 0:
            target = hunk.old_start
        else:
            target = hunk.old_start - 1

        if target < cursor:
            raise _DiffParseError(
                f"hunks out of order at line {hunk.old_start}"
            )
        if target > len(original_lines):
            raise _DiffParseError(
                f"hunk references line {hunk.old_start} past end of file"
            )
        # Copy lines between the previous cursor and the start of this hunk.
        result.extend(original_lines[cursor:target])
        cursor = target

        for tag, body_line in hunk.body:
            if tag == " ":
                if cursor >= len(original_lines):
                    raise _DiffParseError(
                        "context line past end of file at "
                        f"line {cursor + 1}"
                    )
                actual = original_lines[cursor]
                if not _lines_equal(actual, body_line):
                    raise _DiffParseError(
                        f"context mismatch at line {cursor + 1}"
                    )
                result.append(actual)
                cursor += 1
            elif tag == "-":
                if cursor >= len(original_lines):
                    raise _DiffParseError(
                        "delete line past end of file at "
                        f"line {cursor + 1}"
                    )
                actual = original_lines[cursor]
                if not _lines_equal(actual, body_line):
                    raise _DiffParseError(
                        f"delete-line mismatch at line {cursor + 1}"
                    )
                cursor += 1
            elif tag == "+":
                result.append(body_line)
            else:  # pragma: no cover - guarded by parser
                raise _DiffParseError(f"unexpected tag {tag!r}")

    # Append any trailing source lines after the last hunk.
    result.extend(original_lines[cursor:])
    return result


def _lines_equal(actual: str, expected: str) -> bool:
    """Compare a file line against a diff body line ignoring trailing newline.

    Diff body lines may or may not carry a trailing newline depending on how
    the diff was emitted; we compare the content modulo a single trailing
    `\\n` so the parser doesn't get tripped up by the absence of `keepends`.
    """

    return actual.rstrip("\n").rstrip("\r") == expected.rstrip("\n").rstrip(
        "\r"
    )


__all__ = ["EditDiffTool"]
