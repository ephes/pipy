"""The model-driven `read` tool, following Pi's `read`.

Mirrors ``packages/coding-agent/src/core/tools/read.ts`` (pi-mono
``4df157433``): ``path`` plus optional 1-indexed ``offset`` and ``limit``,
output truncated to 2000 lines or 50 KB (whichever is hit first), and an
actionable ``[Showing lines … Use offset=N to continue.]`` notice whenever
the model did not see the whole file. The file is read whole and decoded as
UTF-8 with replacement characters; there is no size cap and no content
refusal, as in Pi.

Deviations from Pi, each tracked in ``docs/backlog.md``:

- Paths resolve through pipy's shared ``resolve_tool_path`` (workspace plus
  configured reference roots, ``.git``/``.gitignore`` refused) rather than
  Pi's ``resolveReadPath`` (READ2).
- Tool results are text-only, so a supported image returns an error instead
  of an image attachment (READ-IMG).
- ``offset``/``limit`` are ``integer`` in pipy's schema subset, which has no
  ``number`` type.

The tool returns provider-visible content through `ToolExecutionResult`. No
prompts, raw arguments, or file paths cross the archive boundary from inside
this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.read_only_tool import (
    ResolvedToolPath,
    _is_ignored_or_generated,
    resolve_tool_path,
)
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.image_mime import (
    detect_supported_image_mime_type_from_file,
)
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)

# Pi's description, except the image sentence: pipy's tool results are
# text-only (READ-IMG), so it says what pipy does with images.
READ_TOOL_DESCRIPTION = (
    "Read the contents of a file. Supports text files; images (jpg, png, gif, "
    "webp, bmp) are not supported yet and return an error. For text files, "
    f"output is truncated to {DEFAULT_MAX_LINES} lines or "
    f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Use offset/limit "
    "for large files. When you need the full file, continue with offset until "
    "complete."
)

# Image types the `@image:` prompt reference accepts (see image_attachment).
_ATTACHABLE_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


@dataclass(frozen=True, slots=True)
class _ReadFailure:
    message: str


@dataclass(frozen=True, slots=True)
class ReadTool:
    """Read a file, truncated like Pi's `read`, with offset/limit paging."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=READ_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (relative or absolute)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        offset = _optional_int(request.arguments.get("offset"), "offset")
        limit = _optional_int(request.arguments.get("limit"), "limit")

        resolved = self._resolve_target(path_arg, context)
        if isinstance(resolved, _ReadFailure):
            return self._error(request, resolved.message)

        target_failure = self._validate_target(resolved.resolved)
        if target_failure is not None:
            return self._error(request, target_failure.message)

        try:
            mime_type = detect_supported_image_mime_type_from_file(resolved.resolved)
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")
        if mime_type is not None:
            return self._error(request, _image_message(mime_type))

        try:
            raw = resolved.resolved.read_bytes()
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")

        output = _format_text(
            raw.decode("utf-8", errors="replace"),
            path=str(path_arg),
            offset=offset,
            limit=limit,
        )
        if isinstance(output, _ReadFailure):
            return self._error(request, output.message)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _resolve_target(
        path_arg: object, context: ToolContext
    ) -> ResolvedToolPath | _ReadFailure:
        try:
            if not isinstance(path_arg, str):
                raise ValueError("path must be a string")
            resolved = resolve_tool_path(
                path_arg,
                workspace_root=context.workspace_root,
                reference_roots=context.reference_roots,
            )
        except ValueError as exc:
            raise ToolArgumentError("read", str(exc), field_path=("path",)) from None
        except OSError as exc:
            return _ReadFailure(f"failed to resolve path: {exc}")
        if _is_ignored_or_generated(resolved.relative_label, resolved.root):
            return _ReadFailure("path is ignored or under .git/generated directories")
        return resolved

    @staticmethod
    def _validate_target(candidate: Path) -> _ReadFailure | None:
        try:
            if not candidate.exists():
                return _ReadFailure("file does not exist")
            if not candidate.is_file():
                return _ReadFailure("path is not a regular file")
        except OSError as exc:
            return _ReadFailure(f"failed to stat file: {exc}")
        return None

    @staticmethod
    def _error(request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"read error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError("read", "expected integer", field_path=(field,))
    return value


def _image_message(mime_type: str) -> str:
    message = f"image files are not supported by read yet ({mime_type})."
    if mime_type in _ATTACHABLE_IMAGE_TYPES:
        message += " Ask the user to attach it with @image:<path>."
    return message


def _format_text(
    text: str, *, path: str, offset: int | None, limit: int | None
) -> str | _ReadFailure:
    """Select and truncate lines exactly like Pi's `read` text branch.

    The offset/limit arithmetic is transcribed from Pi, including its
    JavaScript ``slice`` semantics for zero and negative values (Python
    slicing treats a negative end the same way).
    """

    all_lines = text.split("\n")
    total_file_lines = len(all_lines)
    start_line = max(0, offset - 1) if offset else 0
    start_line_display = start_line + 1
    if start_line >= total_file_lines:
        return _ReadFailure(
            f"Offset {offset} is beyond end of file ({total_file_lines} lines total)"
        )

    user_limited_lines: int | None = None
    if limit is not None:
        end_line = min(start_line + limit, total_file_lines)
        selected = "\n".join(all_lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected = "\n".join(all_lines[start_line:])

    truncation = truncate_head(selected)
    if truncation.first_line_exceeds_limit:
        first_line_size = format_size(len(all_lines[start_line].encode("utf-8")))
        return (
            f"[Line {start_line_display} is {first_line_size}, exceeds "
            f"{format_size(DEFAULT_MAX_BYTES)} limit. Use bash: sed -n "
            f"'{start_line_display}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
    if truncation.truncated:
        end_line_display = start_line_display + truncation.output_lines - 1
        next_offset = end_line_display + 1
        shown = f"Showing lines {start_line_display}-{end_line_display} of {total_file_lines}"
        if truncation.truncated_by == "lines":
            return f"{truncation.content}\n\n[{shown}. Use offset={next_offset} to continue.]"
        return (
            f"{truncation.content}\n\n[{shown} ({format_size(DEFAULT_MAX_BYTES)} "
            f"limit). Use offset={next_offset} to continue.]"
        )
    if (
        user_limited_lines is not None
        and start_line + user_limited_lines < total_file_lines
    ):
        remaining = total_file_lines - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        return (
            f"{truncation.content}\n\n[{remaining} more lines in file. "
            f"Use offset={next_offset} to continue.]"
        )
    return truncation.content


__all__ = ["READ_TOOL_DESCRIPTION", "ReadTool"]
