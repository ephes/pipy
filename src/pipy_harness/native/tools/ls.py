"""The `ls` tool: bounded workspace-relative directory listing.

`LsTool` lists at most `max_entries` direct children of a
workspace-relative directory. It reuses the same path validation as
`ReadTool` so `.git`, `.gitignore`-matched paths, absolute paths, and
parent traversal are refused identically. Output is a deterministic
newline-separated list of `"<type> <relative-path>"` rows where `<type>`
is one of `file`, `directory`, or `other`.

No sizes, timestamps, owners, or modes are returned in this slice. The
tool returns provider-visible content through `ToolExecutionResult` and
emits no archive events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
    _is_relative_to,
    _validate_workspace_relative_path,
)
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

TRUNCATION_MARKER = "... (truncated)"


@dataclass(frozen=True, slots=True)
class LsTool:
    """List workspace-relative directory entries with bounded output."""

    max_entries: int = 200

    DEFAULT_MAX_ENTRIES: ClassVar[int] = 200
    HARD_MAX_ENTRIES: ClassVar[int] = 1000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_entries, int)
            or isinstance(self.max_entries, bool)
            or self.max_entries < 1
            or self.max_entries > self.HARD_MAX_ENTRIES
        ):
            raise ValueError(
                f"LsTool max_entries must be in [1, {self.HARD_MAX_ENTRIES}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ls",
            description=(
                "List direct children of a workspace-relative directory. "
                "Returns up to a bounded number of entries; paths under .git "
                "or matching .gitignore are refused; absolute paths and "
                "parent traversal are refused. Use '.' to list the workspace "
                "root."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path to the directory. "
                            "Use '.' for the workspace root."
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
        workspace = context.workspace_root.resolve()
        if path_arg == ".":
            target = workspace
            relative_prefix = ""
        else:
            try:
                _validate_workspace_relative_path(path_arg)
            except ValueError as exc:
                raise ToolArgumentError(
                    "ls", str(exc), field_path=("path",)
                ) from None
            target = (workspace / path_arg).resolve()
            relative_prefix = path_arg.rstrip("/") + "/"
            if not _is_relative_to(target, workspace):
                return self._error(request, "path escapes the workspace")
            if _is_ignored_or_generated(path_arg, workspace):
                return self._error(
                    request,
                    "path is ignored or under .git/generated directories",
                )

        if not target.exists():
            return self._error(request, "directory does not exist")
        if not target.is_dir():
            return self._error(request, "path is not a directory")

        try:
            children = sorted(target.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            return self._error(request, f"failed to list directory: {exc}")

        rows: list[str] = []
        truncated = False
        for child in children:
            relative_child = relative_prefix + child.name
            if _is_ignored_or_generated(relative_child, workspace):
                continue
            if len(rows) >= self.max_entries:
                truncated = True
                break
            try:
                if child.is_file():
                    label = "file"
                elif child.is_dir():
                    label = "directory"
                else:
                    label = "other"
            except OSError:
                label = "other"
            rows.append(f"{label} {relative_child}")

        output = "\n".join(rows)
        if truncated:
            if output:
                output = output + "\n" + TRUNCATION_MARKER
            else:
                output = TRUNCATION_MARKER
        if not output:
            output = "(empty directory)"

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            provider_correlation_id=request.provider_correlation_id,
        )

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ls error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["LsTool", "TRUNCATION_MARKER"]
