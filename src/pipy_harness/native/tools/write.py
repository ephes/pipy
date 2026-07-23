"""The `write` tool: create-only workspace-relative file writes.

`WriteTool` creates a new UTF-8 file at a workspace-relative path. The
tool is create-only in this slice: it refuses paths that already exist,
paths under `.git`, ignored paths, paths that escape the workspace, and
paths whose parent directory does not exist. The resulting unified diff
is streamed to the loop's `error_stream` via `ToolContext.stderr_sink`;
the archive (pipy_session.recorder) is not touched from inside the
tool.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
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
class _WriteFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _WriteTarget:
    candidate: Path


@dataclass(frozen=True, slots=True)
class WriteTool:
    """Create a new workspace-relative file with the provided content."""

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
                "WriteTool max_content_bytes must be in "
                f"[1, {self.HARD_MAX_CONTENT_BYTES}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description=(
                "Create a new workspace-relative UTF-8 file with the "
                "provided content. Refuses existing files, paths under "
                ".git, ignored paths, absolute paths, parent traversal, "
                "and paths whose parent directory does not exist."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path for the new file."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "maxLength": self.max_content_bytes,
                        "description": (
                            "UTF-8 file content. Empty strings create an empty file."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        path_value = request.arguments["path"]
        content = request.arguments["content"]
        path_arg = self._validated_path_argument(path_value)
        if not isinstance(content, str):
            raise ToolArgumentError(
                "write",
                "content must be a string",
                field_path=("content",),
            )

        target = self._resolve_target(path_arg, context.workspace_root)
        if isinstance(target, _WriteFailure):
            return self._error(request, target.message)

        write_failure = self._write_content(target.candidate, content)
        if write_failure is not None:
            return self._error(request, write_failure.message)

        self._stream_diff(path_arg, content, context)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"wrote {path_arg} ({len(content.encode('utf-8'))} bytes)",
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _validated_path_argument(path_arg: object) -> str:
        try:
            if not isinstance(path_arg, str):
                raise ValueError("workspace_relative_path must be a string")
            _validate_workspace_relative_path(path_arg)
        except ValueError as exc:
            raise ToolArgumentError("write", str(exc), field_path=("path",)) from None
        return path_arg

    @staticmethod
    def _resolve_target(
        path_arg: str, workspace_root: Path
    ) -> _WriteTarget | _WriteFailure:
        workspace = workspace_root.resolve()
        candidate = (workspace / path_arg).resolve()
        if not _is_relative_to(candidate, workspace):
            return _WriteFailure("path escapes the workspace")
        resolved_label = _resolved_relative_label(candidate, workspace)
        if resolved_label is None:
            return _WriteFailure("path escapes the workspace")
        if _is_ignored_or_generated(path_arg, workspace) or _is_ignored_or_generated(
            resolved_label, workspace
        ):
            return _WriteFailure("path is ignored or under .git/generated directories")
        if candidate.exists():
            return _WriteFailure("file already exists")
        parent = candidate.parent
        if not parent.exists():
            return _WriteFailure("parent directory does not exist")
        if not parent.is_dir():
            return _WriteFailure("parent is not a directory")
        parent_label = _resolved_relative_label(parent.resolve(), workspace)
        if parent_label is None:
            return _WriteFailure("parent directory escapes the workspace")
        if parent_label and _is_ignored_or_generated(parent_label, workspace):
            return _WriteFailure(
                "parent directory is ignored or under .git/generated directories"
            )
        return _WriteTarget(candidate=candidate)

    @staticmethod
    def _write_content(candidate: Path, content: str) -> _WriteFailure | None:
        try:
            candidate.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _WriteFailure(f"failed to write file: {exc}")
        return None

    def _stream_diff(self, path_arg: str, content: str, context: ToolContext) -> None:
        diff_text = self._unified_diff(path_arg=path_arg, new_content=content)
        if context.stderr_sink is not None and diff_text:
            context.stderr_sink(diff_text)

    @staticmethod
    def _unified_diff(*, path_arg: str, new_content: str) -> str:
        diff_lines = difflib.unified_diff(
            [],
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path_arg}",
            tofile=f"b/{path_arg}",
        )
        return "".join(diff_lines)

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"write error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["WriteTool"]
