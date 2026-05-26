"""The first model-driven tool: `read`.

`ReadTool` returns a bounded UTF-8 excerpt of a bounded-size workspace-relative file. It
reuses `pipy_harness.native.read_only_tool` validation helpers (path safety,
`.git`/`.gitignore` defaults, control-character and secret-looking content
checks) so the existing `/read`, `/ask-file`, and `/propose-file` boundaries
and the new model-driven tool loop share the same workspace policy.

The tool returns provider-visible content through `ToolExecutionResult`. No
prompts, raw arguments, diffs, or file paths cross the archive boundary from
inside this module; metadata-only events are emitted by the loop (later
slices) using the existing `NativeToolResult`/`NativeToolObservation` shapes,
not the provider-visible output text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _CONTROL_CHARS,
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

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        try:
            resolved = resolve_tool_path(
                path_arg,
                workspace_root=context.workspace_root,
                reference_roots=context.reference_roots,
            )
        except ValueError as exc:
            raise ToolArgumentError(
                "read", str(exc), field_path=("path",)
            ) from None

        candidate = resolved.resolved
        if _is_ignored_or_generated(
            resolved.relative_label, resolved.root
        ):
            return self._error(
                request,
                "path is ignored or under .git/generated directories",
            )
        if not candidate.exists():
            return self._error(request, "file does not exist")
        if not candidate.is_file():
            return self._error(request, "path is not a regular file")
        try:
            if candidate.stat().st_size > self.MAX_CONTENT_BYTES:
                return self._error(request, "file exceeds max_content_bytes")
        except OSError as exc:
            return self._error(request, f"failed to stat file: {exc}")
        try:
            raw = candidate.read_bytes()
        except OSError as exc:
            return self._error(request, f"failed to read file: {exc}")
        if b"\0" in raw[: self.byte_limit + 1]:
            return self._error(request, "binary content detected")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(request, "non-UTF-8 content")
        if any(char in _CONTROL_CHARS for char in text):
            return self._error(request, "binary content detected")
        if has_secret_shaped_content(text):
            return self._error(request, "secret-looking content detected")

        lines = text.splitlines(keepends=True)
        truncated_text = "".join(lines[: self.line_limit])
        encoded = truncated_text.encode("utf-8")
        if len(encoded) > self.byte_limit:
            truncated_text = encoded[: self.byte_limit].decode(
                "utf-8", errors="ignore"
            )

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=truncated_text,
            provider_correlation_id=request.provider_correlation_id,
        )

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"read error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["ReadTool"]
