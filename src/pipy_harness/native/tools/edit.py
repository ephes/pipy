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
from pathlib import Path
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
class _EditArguments:
    path_arg: str
    old_string: str
    new_string: str
    replace_all: bool


@dataclass(frozen=True, slots=True)
class _EditFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _EditTarget:
    candidate: Path


@dataclass(frozen=True, slots=True)
class _EditContent:
    original_text: str


@dataclass(frozen=True, slots=True)
class _EditReplacement:
    original_text: str
    new_text: str
    occurrences: int


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

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        arguments = self._validated_arguments(request)
        target = self._resolve_target(arguments.path_arg, context.workspace_root)
        if isinstance(target, _EditFailure):
            return self._error(request, target.message)

        content = self._read_content(target.candidate)
        if isinstance(content, _EditFailure):
            return self._error(request, content.message)
        replacement = self._replace_content(arguments, content)
        if isinstance(replacement, _EditFailure):
            return self._error(request, replacement.message)
        write_failure = self._write_content(target.candidate, replacement.new_text)
        if write_failure is not None:
            return self._error(request, write_failure.message)

        self._stream_diff(arguments.path_arg, replacement, context)
        replacement_count = replacement.occurrences if arguments.replace_all else 1
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=(
                f"edited {arguments.path_arg} ({replacement_count} replacement(s))"
            ),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _validated_arguments(request: ToolRequest) -> _EditArguments:
        path_value = request.arguments["path"]
        old_string = request.arguments["old_string"]
        new_string = request.arguments["new_string"]
        try:
            _validate_workspace_relative_path(path_value)
        except ValueError as exc:
            raise ToolArgumentError("edit", str(exc), field_path=("path",)) from None
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
        assert isinstance(path_value, str)
        return _EditArguments(
            path_arg=path_value,
            old_string=old_string,
            new_string=new_string,
            replace_all=bool(request.arguments.get("replace_all", False)),
        )

    @staticmethod
    def _resolve_target(
        path_arg: str, workspace_root: Path
    ) -> _EditTarget | _EditFailure:
        workspace = workspace_root.resolve()
        candidate = (workspace / path_arg).resolve()
        if not _is_relative_to(candidate, workspace):
            return _EditFailure("path escapes the workspace")
        resolved_label = _resolved_relative_label(candidate, workspace)
        if resolved_label is None:
            return _EditFailure("path escapes the workspace")
        if _is_ignored_or_generated(path_arg, workspace) or _is_ignored_or_generated(
            resolved_label, workspace
        ):
            return _EditFailure("path is ignored or under .git/generated directories")
        if not candidate.exists():
            return _EditFailure("file does not exist")
        if not candidate.is_file():
            return _EditFailure("path is not a regular file")
        return _EditTarget(candidate)

    def _read_content(self, candidate: Path) -> _EditContent | _EditFailure:
        try:
            if candidate.stat().st_size > self.max_content_bytes:
                return _EditFailure("file exceeds max_content_bytes")
        except OSError as exc:
            return _EditFailure(f"failed to stat file: {exc}")
        try:
            original_bytes = candidate.read_bytes()
        except OSError as exc:
            return _EditFailure(f"failed to read file: {exc}")
        if b"\0" in original_bytes:
            return _EditFailure("binary content detected")
        if len(original_bytes) > self.max_content_bytes:
            return _EditFailure("file exceeds max_content_bytes")
        try:
            original_text = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return _EditFailure("non-UTF-8 content")
        if any(char in _CONTROL_CHARS for char in original_text):
            return _EditFailure("binary content detected")
        return _EditContent(original_text)

    def _replace_content(
        self, arguments: _EditArguments, content: _EditContent
    ) -> _EditReplacement | _EditFailure:
        occurrences = content.original_text.count(arguments.old_string)
        if occurrences == 0:
            return _EditFailure("old_string not found")
        if not arguments.replace_all and occurrences > 1:
            return _EditFailure(
                f"old_string is not unique ({occurrences} matches); "
                "set replace_all=true to replace all"
            )
        count = -1 if arguments.replace_all else 1
        new_text = content.original_text.replace(
            arguments.old_string, arguments.new_string, count
        )
        if len(new_text.encode("utf-8")) > self.max_content_bytes:
            return _EditFailure("edited content exceeds max_content_bytes")
        return _EditReplacement(content.original_text, new_text, occurrences)

    @staticmethod
    def _write_content(candidate: Path, new_text: str) -> _EditFailure | None:
        try:
            candidate.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return _EditFailure(f"failed to write file: {exc}")
        return None

    def _stream_diff(
        self, path_arg: str, replacement: _EditReplacement, context: ToolContext
    ) -> None:
        diff_text = self._unified_diff(
            path_arg=path_arg,
            original_text=replacement.original_text,
            new_text=replacement.new_text,
        )
        if context.stderr_sink is not None and diff_text:
            context.stderr_sink(diff_text)

    @staticmethod
    def _unified_diff(*, path_arg: str, original_text: str, new_text: str) -> str:
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
