"""The model-driven `read` tool, following Pi's `read`.

Mirrors ``packages/coding-agent/src/core/tools/read.ts`` (pi-mono
``4df157433``): ``path`` plus optional 1-indexed ``offset`` and ``limit``,
output truncated to 2000 lines or 50 KB (whichever is hit first), and an
actionable ``[Showing lines … Use offset=N to continue.]`` notice whenever
the model did not see the whole file. The file is read whole and decoded as
UTF-8 with replacement characters; there is no size cap and no content
refusal, as in Pi.

The path resolves like Pi's ``resolveReadPath`` (READ2, see
:mod:`pipy_harness.native.tools.path_utils`): relative to the working
directory, ``~`` expanded, a leading ``@`` stripped, the macOS screenshot
name variants tried, and no deny list, so any readable file can be read.

A supported image (JPEG, PNG, GIF, WebP; BMP is detected) is returned as an
image attachment after a ``Read image file [<type>]`` line, like Pi's
``processImage`` (:mod:`pipy_harness.native.tools.read_image`). Every result
also carries Pi's structured content for programmatic callers such as
codemode scripts (``READ_OUTPUT_SCHEMA``): the text, or for an attached image
``{"type": "image", "data", "mimeType", "note"}``.

Deviations from Pi, each tracked in ``docs/backlog.md``:

- pipy has no image codec: an image over 2000 x 2000 pixels or 4.5 MB of
  base64, or a BMP, is not resized or converted but omitted with Pi's text
  for an image it could not process, and EXIF orientation is not applied.
  The file's structure is checked instead of decoding it, so a cut or
  malformed file is omitted the same way.
  The ``Current model does not support images`` note is not added (the tool
  does not know the model; the provider request leaves the image out).
- ``offset``/``limit`` are ``integer`` in pipy's schema subset, which has no
  ``number`` type.

The tool returns provider-visible content through `ToolExecutionResult`. No
prompts, raw arguments, or file paths cross the archive boundary from inside
this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.agent.content import ImageContent, TextContent
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
from pipy_harness.native.tools.path_utils import resolve_read_path
from pipy_harness.native.tools.prompts import BUILTIN_TOOL_PROMPTS
from pipy_harness.native.tools.read_image import process_image
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)

READ_TOOL_DESCRIPTION = (
    "Read the contents of a file. Supports text files and images (jpg, png, "
    "gif, webp, bmp). Images are sent as attachments. For text files, "
    f"output is truncated to {DEFAULT_MAX_LINES} lines or "
    f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Use offset/limit "
    "for large files. When you need the full file, continue with offset until "
    "complete."
)

# Pi ``readOutputSchema``: the text, or an image block that codemode's
# ``image()`` accepts, with the text that goes with it as ``note``.
READ_OUTPUT_SCHEMA: dict[str, object] = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "type": {"const": "image", "type": "string"},
                "data": {"type": "string"},
                "mimeType": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["type", "data", "mimeType", "note"],
        },
    ]
}


@dataclass(frozen=True, slots=True)
class _ReadFailure:
    message: str


# Image types the `@image:` prompt reference accepts (see image_attachment).
_ATTACHABLE_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


@dataclass(frozen=True, slots=True)
class ReadTool:
    """Read a file, truncated like Pi's `read`, with offset/limit paging.

    ``attach_images=False`` is for a reader whose result is used as text
    only (``@file`` references): an image is then an error that points to
    ``@image:`` instead of a result whose image would be lost.
    """

    attach_images: bool = True

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=READ_TOOL_DESCRIPTION,
            prompt_snippet=BUILTIN_TOOL_PROMPTS["read"].snippet,
            prompt_guidelines=BUILTIN_TOOL_PROMPTS["read"].guidelines,
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
            output_schema=READ_OUTPUT_SCHEMA,
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        offset = _optional_int(request.arguments.get("offset"), "offset")
        limit = _optional_int(request.arguments.get("limit"), "limit")

        if not isinstance(path_arg, str):
            raise ToolArgumentError(
                "read", "path must be a string", field_path=("path",)
            )
        try:
            resolved = resolve_read_path(path_arg, context.workspace_root)
        except ValueError as exc:
            return self._error(request, str(exc))

        target_failure = self._validate_target(resolved)
        if target_failure is not None:
            return self._error(request, target_failure.message)

        try:
            mime_type = detect_supported_image_mime_type_from_file(resolved)
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")
        if mime_type is not None:
            if not self.attach_images:
                return self._error(request, _not_text_message(mime_type))
            return self._image(request, raw, mime_type)

        output = _format_text(
            raw.decode("utf-8", errors="replace"),
            path=str(path_arg),
            offset=offset,
            limit=limit,
        )
        if isinstance(output, _ReadFailure):
            return self._error(request, output.message)
        text, details = output
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            provider_correlation_id=request.provider_correlation_id,
            details=details,
            structured_content=text,
        )

    @staticmethod
    def _image(request: ToolRequest, raw: bytes, mime_type: str) -> ToolExecutionResult:
        """Pi's image branch: a ``Read image file`` line and the image, or
        the line and the reason it could not be attached."""

        processed = process_image(raw, mime_type)
        if isinstance(processed, str):
            note = f"Read image file [{mime_type}]\n{processed}"
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=note,
                provider_correlation_id=request.provider_correlation_id,
                structured_content=note,
            )
        note = f"Read image file [{processed.mime_type}]"
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=note,
            provider_correlation_id=request.provider_correlation_id,
            structured_content={
                "type": "image",
                "data": processed.data,
                "mimeType": processed.mime_type,
                "note": note,
            },
            blocks=(
                TextContent(note),
                ImageContent(processed.data, processed.mime_type),
            ),
        )

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


def _not_text_message(mime_type: str) -> str:
    message = f"image files cannot be read as text ({mime_type})."
    if mime_type in _ATTACHABLE_IMAGE_TYPES:
        message += " Attach it with @image:<path>."
    return message


def _format_text(
    text: str, *, path: str, offset: int | None, limit: int | None
) -> tuple[str, dict[str, object] | None] | _ReadFailure:
    """Select and truncate lines exactly like Pi's `read` text branch.

    Returns the output and Pi's ``details`` (``{truncation}`` when the line
    or byte limit cut the output, else None).

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
        ), {"truncation": truncation.to_details()}
    if truncation.truncated:
        end_line_display = start_line_display + truncation.output_lines - 1
        next_offset = end_line_display + 1
        shown = f"Showing lines {start_line_display}-{end_line_display} of {total_file_lines}"
        details: dict[str, object] = {"truncation": truncation.to_details()}
        if truncation.truncated_by == "lines":
            return (
                f"{truncation.content}\n\n[{shown}. Use offset={next_offset} to continue.]",
                details,
            )
        return (
            f"{truncation.content}\n\n[{shown} ({format_size(DEFAULT_MAX_BYTES)} "
            f"limit). Use offset={next_offset} to continue.]"
        ), details
    if (
        user_limited_lines is not None
        and start_line + user_limited_lines < total_file_lines
    ):
        remaining = total_file_lines - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        return (
            f"{truncation.content}\n\n[{remaining} more lines in file. "
            f"Use offset={next_offset} to continue.]"
        ), None
    return truncation.content, None


__all__ = ["READ_TOOL_DESCRIPTION", "ReadTool"]
