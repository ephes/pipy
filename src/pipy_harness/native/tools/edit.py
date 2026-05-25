"""The `edit` tool: string-replace edits on workspace-relative files.

`EditTool` reads an existing workspace-relative file, replaces
occurrences of `old_string` with `new_string`, writes the result back,
and streams the resulting unified diff to `ToolContext.stderr_sink`.
Defaults to requiring a unique `old_string`; `replace_all=True` opts in
to replacing every occurrence. Archive contracts remain metadata-only.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _CONTROL_CHARS,
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
class EditTool:
    """Replace `old_string` with `new_string` in a workspace file."""

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
                "EditTool max_content_bytes must be in "
                f"[1, {self.HARD_MAX_CONTENT_BYTES}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit",
            description=(
                "Replace `old_string` with `new_string` in an existing "
                "workspace-relative UTF-8 file. By default, `old_string` "
                "must appear exactly once; set `replace_all` to true to "
                "replace every occurrence. Refuses .git, ignored paths, "
                "binary content, and oversized files."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    },
                    "old_string": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": self.max_content_bytes,
                    },
                    "new_string": {
                        "type": "string",
                        "maxLength": self.max_content_bytes,
                    },
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        old_string = request.arguments["old_string"]
        new_string = request.arguments["new_string"]
        replace_all = bool(request.arguments.get("replace_all", False))

        try:
            _validate_workspace_relative_path(path_arg)
        except ValueError as exc:
            raise ToolArgumentError(
                "edit", str(exc), field_path=("path",)
            ) from None
        if not isinstance(old_string, str) or not old_string:
            raise ToolArgumentError(
                "edit",
                "old_string must be a non-empty string",
                field_path=("old_string",),
            )
        if not isinstance(new_string, str):
            raise ToolArgumentError(
                "edit",
                "new_string must be a string",
                field_path=("new_string",),
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
        if any(char in _CONTROL_CHARS for char in original_text):
            return self._error(request, "binary content detected")

        occurrences = original_text.count(old_string)
        if occurrences == 0:
            return self._error(request, "old_string not found")
        if not replace_all and occurrences > 1:
            return self._error(
                request,
                f"old_string is not unique ({occurrences} matches); "
                "set replace_all=true to replace all",
            )

        if replace_all:
            new_text = original_text.replace(old_string, new_string)
        else:
            new_text = original_text.replace(old_string, new_string, 1)

        if len(new_text.encode("utf-8")) > self.max_content_bytes:
            return self._error(
                request, "edited content exceeds max_content_bytes"
            )

        try:
            candidate.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return self._error(request, f"failed to write file: {exc}")

        diff_text = self._unified_diff(
            path_arg=path_arg,
            original_text=original_text,
            new_text=new_text,
        )
        if context.stderr_sink is not None and diff_text:
            context.stderr_sink(diff_text)

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=(
                f"edited {path_arg} ({occurrences if replace_all else 1} "
                "replacement(s))"
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
            output_text=f"edit error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["EditTool"]
