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
from pathlib import PurePosixPath
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

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        pattern = request.arguments["pattern"]
        path_arg = request.arguments.get("path", ".")
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
        parts = PurePosixPath(pattern).parts
        if ".." in parts:
            raise ToolArgumentError(
                "find",
                "pattern must not contain '..'",
                field_path=("pattern",),
            )

        if path_arg == ".":
            workspace = context.workspace_root.resolve()
            root = workspace
            search_root = workspace
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
                    "find", str(exc), field_path=("path",)
                ) from None
            root = resolved.root
            search_root = resolved.resolved
            relative_prefix = (
                resolved.relative_label.rstrip("/") + "/"
                if resolved.relative_label not in {"", "."}
                else ""
            )
            if _is_ignored_or_generated(
                resolved.relative_label, root
            ):
                return self._error(
                    request,
                    "path is ignored or under .git/generated directories",
                )
            if not search_root.exists():
                return self._error(request, "path does not exist")
            if not search_root.is_dir():
                return self._error(request, "path is not a directory")
            if resolved.is_workspace:
                display_prefix = ""
            else:
                root_label = root.name or "reference-root"
                display_prefix = root_label + "/"

        rows: list[str] = []
        truncated = False
        try:
            matches = sorted(search_root.glob(pattern))
        except (OSError, ValueError) as exc:
            return self._error(request, f"glob expansion failed: {exc}")

        for match in matches:
            try:
                relative = match.resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
            if not relative:
                continue
            if _is_ignored_or_generated(relative, root):
                continue
            if relative_prefix and not (
                relative == relative_prefix.rstrip("/")
                or relative.startswith(relative_prefix)
            ):
                continue
            if len(rows) >= self.max_results:
                truncated = True
                break
            rows.append(display_prefix + relative)

        output = "\n".join(rows)
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

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"find error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["FindTool", "TRUNCATION_MARKER"]
