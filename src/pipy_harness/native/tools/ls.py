"""The `ls` tool, following Pi's `ls`.

Mirrors ``packages/coding-agent/src/core/tools/ls.ts`` (pi-mono
``4df157433``): optional ``path`` (default ``.``) and ``limit`` (default
500), entries sorted case-insensitively with a ``/`` suffix for directories,
dotfiles included, and the output capped at 50 KB. When the limit or the byte
cap cuts the listing, a ``[500 entries limit reached. Use limit=1000 for
more]`` style notice says so.

The path resolves like Pi's ``resolveToCwd`` (READ2, see
:mod:`pipy_harness.native.tools.path_utils`), and every entry is listed
(``.git``, ignored and generated ones included); an entry whose ``stat``
fails is skipped, as in Pi.

Deviations from Pi, each tracked in ``docs/backlog.md``:

- Error texts stay pipy's.
- Entries sort by :func:`~pipy_harness.native.tools.collation.ls_sort_key`, a
  table-driven approximation of Node's ICU ``localeCompare``; characters
  outside the measured table can order differently within one script.
- ``limit`` is ``integer`` in pipy's schema subset, which has no ``number``.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.collation import ls_sort_key
from pipy_harness.native.tools.path_utils import resolve_to_cwd
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
        path_arg = request.arguments.get("path") or "."
        if not isinstance(path_arg, str):
            raise ToolArgumentError("ls", "path must be a string", field_path=("path",))
        try:
            target = resolve_to_cwd(path_arg, context.workspace_root)
        except ValueError as exc:
            return self._error(request, str(exc))

        names = self._list_names(target)
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

        output, details = _format_output(
            rows, limit_reached=limit_reached, limit=effective_limit
        )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            provider_correlation_id=request.provider_correlation_id,
            details=details,
        )

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
        return sorted(names, key=ls_sort_key)

    @staticmethod
    def _row(name: str, target: Path) -> str | None:
        """Return ``name`` or ``name/``, or ``None`` for a skipped entry."""

        child = target / name
        try:
            # Pi stats each entry (following symlinks) and skips failures.
            is_dir = stat.S_ISDIR(child.stat().st_mode)
        except OSError:
            return None
        return name + "/" if is_dir else name

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ls error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _format_output(
    rows: list[str], *, limit_reached: bool, limit: int
) -> tuple[str, dict[str, object] | None]:
    """The output and Pi's ``details`` (omitted when no limit was hit)."""

    if not rows:
        return "(empty directory)", None
    truncation = truncate_head("\n".join(rows), max_lines=_NO_LINE_LIMIT)
    output = truncation.content
    notices: list[str] = []
    details: dict[str, object] = {}
    if limit_reached:
        notices.append(f"{limit} entries limit reached. Use limit={limit * 2} for more")
        details["entryLimitReached"] = limit
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        details["truncation"] = truncation.to_details()
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output, details or None


__all__ = ["DEFAULT_LIMIT", "LS_TOOL_DESCRIPTION", "LsTool"]
