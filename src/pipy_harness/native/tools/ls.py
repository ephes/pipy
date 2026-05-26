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
    _resolved_relative_label,
    resolve_tool_path,
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
                "List direct children of a directory. Paths may be "
                "workspace-relative POSIX paths, '.' for the workspace root, "
                "or absolute paths that lie under the workspace or a "
                "configured reference root (such as a sibling project added "
                "with --read-root). Returns up to a bounded number of "
                "entries; paths under .git or matching .gitignore are "
                "refused; parent traversal is refused."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path, '.' for the "
                            "workspace root, or absolute path under the "
                            "workspace or a configured reference root."
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
        if path_arg == ".":
            target = context.workspace_root.resolve()
            root = target
            relative_prefix = ""
            display_prefix = ""
        else:
            try:
                resolved = resolve_tool_path(
                    path_arg,
                    workspace_root=context.workspace_root,
                    reference_roots=context.reference_roots,
                )
            except ValueError as exc:
                raise ToolArgumentError(
                    "ls", str(exc), field_path=("path",)
                ) from None
            target = resolved.resolved
            root = resolved.root
            if _is_ignored_or_generated(
                resolved.relative_label, root
            ):
                return self._error(
                    request,
                    "path is ignored or under .git/generated directories",
                )
            relative_prefix = (
                resolved.relative_label.rstrip("/") + "/"
                if resolved.relative_label not in {"", "."}
                else ""
            )
            display_prefix = (
                resolved.display_label.rstrip("/") + "/"
                if resolved.display_label not in {"", "."}
                else resolved.display_label
            )
            if display_prefix and not display_prefix.endswith("/"):
                display_prefix = display_prefix + "/"

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
            if _is_ignored_or_generated(relative_child, root):
                continue
            try:
                resolved_child_label = _resolved_relative_label(
                    child.resolve(), root
                )
            except OSError:
                resolved_child_label = None
            if resolved_child_label is None:
                continue
            if _is_ignored_or_generated(resolved_child_label, root):
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
            display_child = display_prefix + child.name
            rows.append(f"{label} {display_child}")

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
