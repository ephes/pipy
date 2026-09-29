"""The `ls` tool, following Pi's `ls`.

Mirrors ``packages/coding-agent/src/core/tools/ls.ts`` (pi-mono
``4df157433``): optional ``path`` (default ``.``) and ``limit`` (default
500), entries sorted case-insensitively with a ``/`` suffix for directories,
dotfiles included, and the output capped at 50 KB. When the limit or the byte
cap cuts the listing, a ``[500 entries limit reached. Use limit=1000 for
more]`` style notice says so.

Deviations from Pi, each tracked in ``docs/backlog.md``:

- Paths resolve through pipy's shared ``resolve_tool_path`` (workspace plus
  configured reference roots), and ``.git``, ``.gitignore`` matches and
  generated entries are refused or left out (READ2). Their error texts stay
  pipy's.
- Entries sort by ``str.lower()`` code points; Pi's ``localeCompare`` orders
  punctuation differently (TOOLS2).
- ``limit`` is ``integer`` in pipy's schema subset, which has no ``number``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)

DEFAULT_LIMIT = 500
# Pi passes Number.MAX_SAFE_INTEGER: the entry count already caps the rows.
_NO_LINE_LIMIT = 2**53 - 1

LS_TOOL_DESCRIPTION = (
    "List directory contents. Returns entries sorted alphabetically, with '/' "
    "suffix for directories. Includes dotfiles. Output is truncated to "
    f"{DEFAULT_LIMIT} entries or {DEFAULT_MAX_BYTES // 1024}KB (whichever is "
    "hit first)."
)


@dataclass(frozen=True, slots=True)
class _LsFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _LsTarget:
    target: Path
    root: Path
    relative_prefix: str


@dataclass(frozen=True, slots=True)
class LsTool:
    """List a directory like Pi's `ls`."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ls",
            description=LS_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list (default: current directory)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of entries to return (default: 500)"
                        ),
                    },
                },
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        limit = request.arguments.get("limit")
        effective_limit = DEFAULT_LIMIT if limit is None else limit
        target = self._resolve_target(request.arguments.get("path") or ".", context)
        if isinstance(target, _LsFailure):
            return self._error(request, target.message)

        names = self._list_names(target.target)
        if isinstance(names, _LsFailure):
            return self._error(request, names.message)

        rows: list[str] = []
        limit_reached = False
        for name in names:
            if len(rows) >= effective_limit:
                limit_reached = True
                break
            row = self._row(name, target)
            if row is not None:
                rows.append(row)

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=_format_output(
                rows, limit_reached=limit_reached, limit=effective_limit
            ),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _resolve_target(
        path_arg: object, context: ToolContext
    ) -> _LsTarget | _LsFailure:
        if path_arg == ".":
            workspace = context.workspace_root.resolve()
            return _LsTarget(workspace, workspace, "")
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
        return _LsTarget(resolved.resolved, resolved.root, relative_prefix)

    @staticmethod
    def _list_names(target: Path) -> list[str] | _LsFailure:
        if not target.exists():
            return _LsFailure("directory does not exist")
        if not target.is_dir():
            return _LsFailure("path is not a directory")
        try:
            names = os.listdir(target)
        except OSError as exc:
            return _LsFailure(f"Cannot read directory: {exc}")
        # Pi: a.toLowerCase().localeCompare(b.toLowerCase()).
        return sorted(names, key=str.lower)

    @staticmethod
    def _row(name: str, target: _LsTarget) -> str | None:
        """Return ``name`` or ``name/``, or ``None`` for a skipped entry."""

        if _is_ignored_or_generated(target.relative_prefix + name, target.root):
            return None
        child = target.target / name
        try:
            resolved_label = _resolved_relative_label(child.resolve(), target.root)
            # Pi stats each entry (following symlinks) and skips failures.
            is_dir = child.is_dir()
            child.stat()
        except OSError:
            return None
        if resolved_label is None or _is_ignored_or_generated(
            resolved_label, target.root
        ):
            return None
        return name + "/" if is_dir else name

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ls error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _format_output(rows: list[str], *, limit_reached: bool, limit: int) -> str:
    if not rows:
        return "(empty directory)"
    truncation = truncate_head("\n".join(rows), max_lines=_NO_LINE_LIMIT)
    output = truncation.content
    notices: list[str] = []
    if limit_reached:
        notices.append(f"{limit} entries limit reached. Use limit={limit * 2} for more")
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output


__all__ = ["DEFAULT_LIMIT", "LS_TOOL_DESCRIPTION", "LsTool"]
