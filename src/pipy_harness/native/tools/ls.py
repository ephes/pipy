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
from pathlib import Path
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    ResolvedToolPath,
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
class _LsFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _LsTarget:
    target: Path
    root: Path
    relative_prefix: str
    display_prefix: str


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

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        target = self._resolve_target(request.arguments["path"], context)
        if isinstance(target, _LsFailure):
            return self._error(request, target.message)

        children = self._list_children(target.target)
        if isinstance(children, _LsFailure):
            return self._error(request, children.message)

        rows, truncated = self._format_children(children, target)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=self._format_output(rows, truncated=truncated),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _resolve_target(
        path_arg: object, context: ToolContext
    ) -> _LsTarget | _LsFailure:
        if path_arg == ".":
            workspace = context.workspace_root.resolve()
            return _LsTarget(workspace, workspace, "", "")
        try:
            if not isinstance(path_arg, str):
                raise ValueError("path must be a string")
            resolved = resolve_tool_path(
                path_arg,
                workspace_root=context.workspace_root,
                reference_roots=context.reference_roots,
            )
        except ValueError as exc:
            raise ToolArgumentError("ls", str(exc), field_path=("path",)) from None
        if _is_ignored_or_generated(resolved.relative_label, resolved.root):
            return _LsFailure("path is ignored or under .git/generated directories")
        return LsTool._target_from_resolved(resolved)

    @staticmethod
    def _target_from_resolved(resolved: ResolvedToolPath) -> _LsTarget:
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
        return _LsTarget(
            resolved.resolved,
            resolved.root,
            relative_prefix,
            display_prefix,
        )

    @staticmethod
    def _list_children(target: Path) -> list[Path] | _LsFailure:
        if not target.exists():
            return _LsFailure("directory does not exist")
        if not target.is_dir():
            return _LsFailure("path is not a directory")
        try:
            return sorted(target.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            return _LsFailure(f"failed to list directory: {exc}")

    def _format_children(
        self, children: list[Path], target: _LsTarget
    ) -> tuple[list[str], bool]:
        rows: list[str] = []
        for child in children:
            display_child = self._visible_child(child, target)
            if display_child is None:
                continue
            if len(rows) >= self.max_entries:
                return rows, True
            rows.append(f"{self._classify_child(child)} {display_child}")
        return rows, False

    @staticmethod
    def _visible_child(child: Path, target: _LsTarget) -> str | None:
        relative_child = target.relative_prefix + child.name
        if _is_ignored_or_generated(relative_child, target.root):
            return None
        try:
            resolved_child_label = _resolved_relative_label(
                child.resolve(), target.root
            )
        except OSError:
            return None
        if resolved_child_label is None:
            return None
        if _is_ignored_or_generated(resolved_child_label, target.root):
            return None
        return target.display_prefix + child.name

    @staticmethod
    def _classify_child(child: Path) -> str:
        try:
            if child.is_file():
                return "file"
            if child.is_dir():
                return "directory"
            return "other"
        except OSError:
            return "other"

    @staticmethod
    def _format_output(rows: list[str], *, truncated: bool) -> str:
        output = "\n".join(rows)
        if truncated:
            if output:
                return output + "\n" + TRUNCATION_MARKER
            return TRUNCATION_MARKER
        if not output:
            return "(empty directory)"
        return output

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ls error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["LsTool", "TRUNCATION_MARKER"]
