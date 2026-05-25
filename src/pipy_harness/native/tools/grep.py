"""The `grep` tool: bounded literal-string workspace search.

`GrepTool` searches for a literal (non-regex) string across workspace
files. When `rg` is available on `PATH`, the tool invokes it through
`subprocess.run` with a fixed argv, `shell=False`,
`cwd=workspace_root`, a hard timeout, and capped output. When `rg` is
unavailable, a stdlib walk fallback keeps the no-new-runtime-dep
invariant.

Output rows are `"<relative-path>:<line-number>:<text>"`; trailing
truncation appends a stable `"... (truncated)"` marker. No regex syntax
is exposed; the loop must request literal matches.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pipy_harness.capture import looks_sensitive
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

TRUNCATION_MARKER = "... (truncated)"


@dataclass(frozen=True, slots=True)
class GrepTool:
    """Search for a literal string across workspace files."""

    max_results: int = 100
    max_output_bytes: int = 32 * 1024
    timeout_seconds: float = 5.0

    DEFAULT_MAX_RESULTS: ClassVar[int] = 100
    HARD_MAX_RESULTS: ClassVar[int] = 1000
    HARD_MAX_OUTPUT_BYTES: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_results, int)
            or isinstance(self.max_results, bool)
            or self.max_results < 1
            or self.max_results > self.HARD_MAX_RESULTS
        ):
            raise ValueError(
                f"GrepTool max_results must be in [1, {self.HARD_MAX_RESULTS}]"
            )
        if (
            not isinstance(self.max_output_bytes, int)
            or isinstance(self.max_output_bytes, bool)
            or self.max_output_bytes < 1
            or self.max_output_bytes > self.HARD_MAX_OUTPUT_BYTES
        ):
            raise ValueError(
                "GrepTool max_output_bytes must be in "
                f"[1, {self.HARD_MAX_OUTPUT_BYTES}]"
            )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 60.0
        ):
            raise ValueError(
                "GrepTool timeout_seconds must be in (0, 60]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="grep",
            description=(
                "Search for a literal (non-regex) string across workspace "
                "files. Paths under .git or matching .gitignore are refused; "
                "absolute paths and parent traversal are refused. Use '.' to "
                "search the whole workspace."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Literal string to search for; not a regex."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative POSIX path to search; use "
                            "'.' for the workspace root."
                        ),
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        pattern = request.arguments["pattern"]
        path_arg = request.arguments.get("path", ".")
        if not isinstance(pattern, str) or not pattern:
            raise ToolArgumentError(
                "grep",
                "pattern must be a non-empty string",
                field_path=("pattern",),
            )

        workspace = context.workspace_root.resolve()
        if path_arg == ".":
            search_root = workspace
            relative_root = ""
        else:
            try:
                _validate_workspace_relative_path(path_arg)
            except ValueError as exc:
                raise ToolArgumentError(
                    "grep", str(exc), field_path=("path",)
                ) from None
            search_root = (workspace / path_arg).resolve()
            relative_root = path_arg
            if not _is_relative_to(search_root, workspace):
                return self._error(request, "path escapes the workspace")
            resolved_root_label = _resolved_relative_label(search_root, workspace)
            if resolved_root_label is None:
                return self._error(request, "path escapes the workspace")
            if _is_ignored_or_generated(
                path_arg, workspace
            ) or _is_ignored_or_generated(resolved_root_label, workspace):
                return self._error(
                    request,
                    "path is ignored or under .git/generated directories",
                )
            if not search_root.exists():
                return self._error(request, "path does not exist")

        if shutil.which("rg") is not None:
            output, truncated = self._search_with_rg(
                pattern=pattern,
                search_root=search_root,
                workspace=workspace,
                relative_root=relative_root,
            )
        else:
            output, truncated = self._search_with_stdlib(
                pattern=pattern,
                search_root=search_root,
                workspace=workspace,
                relative_root=relative_root,
            )

        if truncated:
            if output:
                output = output + "\n" + TRUNCATION_MARKER
            else:
                output = TRUNCATION_MARKER
        if not output:
            output = "(no matches)"

        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            provider_correlation_id=request.provider_correlation_id,
        )

    def _search_with_rg(
        self,
        *,
        pattern: str,
        search_root: Path,
        workspace: Path,
        relative_root: str,
    ) -> tuple[str, bool]:
        argv = [
            "rg",
            "--no-heading",
            "--line-number",
            "--color=never",
            "--with-filename",
            "--fixed-strings",
            "--",
            pattern,
            str(search_root),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - argv is fixed, shell=False
                argv,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return ("grep error: rg timed out", False)
        except OSError as exc:
            return (f"grep error: failed to spawn rg: {exc}", False)

        rows: list[str] = []
        truncated = False
        cumulative_bytes = 0
        for raw_line in completed.stdout.splitlines():
            if len(rows) >= self.max_results:
                truncated = True
                break
            normalized = self._normalize_rg_row(
                raw_line, workspace=workspace, relative_root=relative_root
            )
            if normalized is None:
                continue
            row_bytes = len(normalized.encode("utf-8")) + 1
            if cumulative_bytes + row_bytes > self.max_output_bytes:
                truncated = True
                break
            cumulative_bytes += row_bytes
            rows.append(normalized)
        return ("\n".join(rows), truncated)

    @staticmethod
    def _normalize_rg_row(
        raw_line: str, *, workspace: Path, relative_root: str
    ) -> str | None:
        parts = raw_line.split(":", 2)
        if len(parts) < 3:
            return None
        path_text, line_text, body = parts
        candidate = Path(path_text)
        try:
            relative = candidate.resolve().relative_to(workspace)
        except (ValueError, OSError):
            return None
        relative_label = relative.as_posix()
        if _is_ignored_or_generated(relative_label, workspace):
            return None
        if relative_root and not (
            relative_label == relative_root
            or relative_label.startswith(relative_root.rstrip("/") + "/")
        ):
            return None
        return f"{relative_label}:{line_text}:{body}"

    def _search_with_stdlib(
        self,
        *,
        pattern: str,
        search_root: Path,
        workspace: Path,
        relative_root: str,
    ) -> tuple[str, bool]:
        rows: list[str] = []
        truncated = False
        cumulative_bytes = 0
        for relative_label in self._walk(search_root, workspace):
            if len(rows) >= self.max_results:
                truncated = True
                break
            try:
                text = (workspace / relative_label).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "\x00" in text:
                continue
            if any(char in _CONTROL_CHARS for char in text):
                continue
            if looks_sensitive(text):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    if len(rows) >= self.max_results:
                        truncated = True
                        break
                    row = f"{relative_label}:{line_number}:{line}"
                    row_bytes = len(row.encode("utf-8")) + 1
                    if cumulative_bytes + row_bytes > self.max_output_bytes:
                        truncated = True
                        break
                    cumulative_bytes += row_bytes
                    rows.append(row)
            if truncated:
                break
            _ = relative_root  # documents that the walk is already rooted
        return ("\n".join(rows), truncated)

    @staticmethod
    def _walk(search_root: Path, workspace: Path) -> Iterable[str]:
        for dirpath, dirnames, filenames in os.walk(search_root):
            kept_dirs: list[str] = []
            for name in dirnames:
                try:
                    resolved = (Path(dirpath) / name).resolve()
                except OSError:
                    continue
                resolved_label = _resolved_relative_label(resolved, workspace)
                if resolved_label is None:
                    continue
                if _is_ignored_or_generated(resolved_label, workspace):
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for filename in filenames:
                try:
                    resolved = (Path(dirpath) / filename).resolve()
                except OSError:
                    continue
                relative = _resolved_relative_label(resolved, workspace)
                if relative is None:
                    continue
                if _is_ignored_or_generated(relative, workspace):
                    continue
                yield relative

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"grep error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["GrepTool", "TRUNCATION_MARKER"]
