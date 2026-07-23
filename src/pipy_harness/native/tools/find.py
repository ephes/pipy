"""The `find` tool: bounded workspace-relative glob lookup.

`FindTool` returns workspace-relative POSIX paths that match a POSIX
glob pattern (for example `**/*.py`). The search root defaults to `.`
and is validated identically to `ReadTool`. Patterns containing `..` or
starting with `/` are refused so glob expansion cannot escape the
workspace. Results are capped at `max_results` and append the stable
`"... (truncated)"` marker on overflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
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
class _FindFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _SearchRoot:
    root: Path
    search_root: Path
    relative_prefix: str
    display_prefix: str


@dataclass(frozen=True, slots=True)
class _FindRows:
    rows: list[str]
    truncated: bool


@dataclass(frozen=True, slots=True)
class FindTool:
    """Return workspace-relative paths matching a POSIX glob pattern."""

    max_results: int = 200

    DEFAULT_MAX_RESULTS: ClassVar[int] = 200
    HARD_MAX_RESULTS: ClassVar[int] = 1000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_results, int)
            or isinstance(self.max_results, bool)
            or self.max_results < 1
            or self.max_results > self.HARD_MAX_RESULTS
        ):
            raise ValueError(
                f"FindTool max_results must be in [1, {self.HARD_MAX_RESULTS}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="find",
            description=(
                "Return paths matching a POSIX glob pattern (for example "
                "'**/*.py'). The search root may be workspace-relative, "
                "'.' for the workspace root, or an absolute path under the "
                "workspace or a configured reference root (such as a "
                "sibling project added with --read-root). Patterns "
                "containing '..' or starting with '/' are refused; .git "
                "and ignored matches are filtered."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 512,
                        "description": "POSIX glob pattern.",
                    },
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "description": (
                            "Workspace-relative search root, '.' for the "
                            "workspace root, or absolute path under the "
                            "workspace or a configured reference root."
                        ),
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        pattern = self._validated_pattern(request.arguments["pattern"])
        search = self._resolve_search_root(request.arguments.get("path", "."), context)
        if isinstance(search, _FindFailure):
            return self._error(request, search.message)
        collected = self._collect_rows(search, pattern)
        if isinstance(collected, _FindFailure):
            return self._error(request, collected.message)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=self._format_output(collected),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _validated_pattern(pattern: object) -> str:
        if not isinstance(pattern, str) or not pattern:
            raise ToolArgumentError(
                "find",
                "pattern must be a non-empty string",
                field_path=("pattern",),
            )
        if pattern.startswith("/") or "\\" in pattern:
            raise ToolArgumentError(
                "find",
                "pattern must not be absolute or contain backslashes",
                field_path=("pattern",),
            )
        if ".." in PurePosixPath(pattern).parts:
            raise ToolArgumentError(
                "find",
                "pattern must not contain '..'",
                field_path=("pattern",),
            )
        return pattern

    @staticmethod
    def _resolve_search_root(
        path_arg: object, context: ToolContext
    ) -> _SearchRoot | _FindFailure:
        if path_arg == ".":
            workspace = context.workspace_root.resolve()
            return _SearchRoot(workspace, workspace, "", "")
        try:
            if not isinstance(path_arg, str):
                raise ValueError("path must be a string")
            resolved = resolve_tool_path(
                path_arg,
                workspace_root=context.workspace_root,
                reference_roots=context.reference_roots,
            )
        except ValueError as exc:
            raise ToolArgumentError("find", str(exc), field_path=("path",)) from None

        if _is_ignored_or_generated(resolved.relative_label, resolved.root):
            return _FindFailure("path is ignored or under .git/generated directories")
        if not resolved.resolved.exists():
            return _FindFailure("path does not exist")
        if not resolved.resolved.is_dir():
            return _FindFailure("path is not a directory")
        relative_prefix = (
            resolved.relative_label.rstrip("/") + "/"
            if resolved.relative_label not in {"", "."}
            else ""
        )
        display_prefix = ""
        if not resolved.is_workspace:
            display_prefix = (resolved.root.name or "reference-root") + "/"
        return _SearchRoot(
            resolved.root,
            resolved.resolved,
            relative_prefix,
            display_prefix,
        )

    def _collect_rows(
        self, search: _SearchRoot, pattern: str
    ) -> _FindRows | _FindFailure:
        try:
            matches = sorted(search.search_root.glob(pattern))
        except (OSError, ValueError) as exc:
            return _FindFailure(f"glob expansion failed: {exc}")

        rows: list[str] = []
        for match in matches:
            relative = self._project_match(match, search)
            if relative is None:
                continue
            if len(rows) >= self.max_results:
                return _FindRows(rows, truncated=True)
            rows.append(search.display_prefix + relative)
        return _FindRows(rows, truncated=False)

    @staticmethod
    def _project_match(match: Path, search: _SearchRoot) -> str | None:
        try:
            relative = match.resolve().relative_to(search.root).as_posix()
        except (ValueError, OSError):
            return None
        if not relative or _is_ignored_or_generated(relative, search.root):
            return None
        if search.relative_prefix and not (
            relative == search.relative_prefix.rstrip("/")
            or relative.startswith(search.relative_prefix)
        ):
            return None
        return relative

    @staticmethod
    def _format_output(collected: _FindRows) -> str:
        output = "\n".join(collected.rows)
        if collected.truncated:
            if output:
                output = output + "\n" + TRUNCATION_MARKER
            else:
                output = TRUNCATION_MARKER
        return output or "(no matches)"

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"find error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["FindTool", "TRUNCATION_MARKER"]
