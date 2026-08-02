"""The first model-driven tool: `read`.

`ReadTool` returns a bounded UTF-8 excerpt of a bounded-size workspace-relative file. It
reuses `pipy_harness.native.read_only_tool` validation helpers (path safety,
`.git`/`.gitignore` defaults, control-character and secret-looking content
checks) so the model-driven tool loop shares the same workspace policy as the
other archive-safe read boundaries.

The tool returns provider-visible content through `ToolExecutionResult`. No
prompts, raw arguments, diffs, or file paths cross the archive boundary from
inside this module; metadata-only events are emitted by the loop (later
slices) using the existing `NativeToolResult`/`NativeToolObservation` shapes,
not the provider-visible output text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _CONTROL_CHARS,
    ResolvedToolPath,
    _is_ignored_or_generated,
    has_secret_shaped_content,
    resolve_tool_path,
)
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


@dataclass(frozen=True, slots=True)
class _ReadFailure:
    message: str


@dataclass(frozen=True, slots=True)
class ReadTool:
    """Read a workspace-relative UTF-8 file and return a bounded excerpt."""

    byte_limit: int = 8 * 1024
    line_limit: int = 200

    DEFAULT_BYTE_LIMIT: ClassVar[int] = 8 * 1024
    DEFAULT_LINE_LIMIT: ClassVar[int] = 200
    MAX_BYTE_LIMIT: ClassVar[int] = 32 * 1024
    MAX_LINE_LIMIT: ClassVar[int] = 1000
    MAX_CONTENT_BYTES: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.byte_limit, int)
            or isinstance(self.byte_limit, bool)
            or self.byte_limit < 1
            or self.byte_limit > self.MAX_BYTE_LIMIT
        ):
            raise ValueError(
                f"ReadTool byte_limit must be in [1, {self.MAX_BYTE_LIMIT}]"
            )
        if (
            not isinstance(self.line_limit, int)
            or isinstance(self.line_limit, bool)
            or self.line_limit < 1
            or self.line_limit > self.MAX_LINE_LIMIT
        ):
            raise ValueError(
                f"ReadTool line_limit must be in [1, {self.MAX_LINE_LIMIT}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=(
                "Read a UTF-8 file and return a bounded excerpt. Paths may be "
                "workspace-relative POSIX paths, or absolute paths that lie "
                "under the workspace or a configured reference root (such as "
                "a sibling project added with --read-root). Paths under .git "
                "or matching .gitignore are refused; parent traversal is "
                "refused."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path or absolute path "
                            "under the workspace or a configured reference "
                            "root."
                        ),
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        resolved = self._resolve_target(request.arguments["path"], context)
        if isinstance(resolved, _ReadFailure):
            return self._error(request, resolved.message)

        target_failure = self._validate_target(resolved)
        if target_failure is not None:
            return self._error(request, target_failure.message)

        loaded = self._read_bytes(resolved.resolved)
        if isinstance(loaded, _ReadFailure):
            return self._error(request, loaded.message)

        text = self._validate_content(loaded)
        if isinstance(text, _ReadFailure):
            return self._error(request, text.message)

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=self._excerpt(text),
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
        if _is_ignored_or_generated(resolved.relative_label, resolved.root):
            return _ReadFailure("path is ignored or under .git/generated directories")
        return resolved

    @staticmethod
    def _validate_target(resolved: ResolvedToolPath) -> _ReadFailure | None:
        candidate = resolved.resolved
        if not candidate.exists():
            return _ReadFailure("file does not exist")
        if not candidate.is_file():
            return _ReadFailure("path is not a regular file")
        return None

    def _read_bytes(self, candidate: Path) -> bytes | _ReadFailure:
        try:
            if candidate.stat().st_size > self.MAX_CONTENT_BYTES:
                return _ReadFailure("file exceeds max_content_bytes")
        except OSError as exc:
            return _ReadFailure(f"failed to stat file: {exc}")
        try:
            return candidate.read_bytes()
        except OSError as exc:
            return _ReadFailure(f"failed to read file: {exc}")

    def _validate_content(self, raw: bytes) -> str | _ReadFailure:
        if b"\0" in raw[: self.byte_limit + 1]:
            return _ReadFailure("binary content detected")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _ReadFailure("non-UTF-8 content")
        if any(char in _CONTROL_CHARS for char in text):
            return _ReadFailure("binary content detected")
        if has_secret_shaped_content(text):
            return _ReadFailure("secret-looking content detected")
        return text

    def _excerpt(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        truncated_text = "".join(lines[: self.line_limit])
        encoded = truncated_text.encode("utf-8")
        if len(encoded) > self.byte_limit:
            return encoded[: self.byte_limit].decode("utf-8", errors="ignore")
        return truncated_text

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"read error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["ReadTool"]
