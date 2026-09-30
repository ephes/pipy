"""The `find` tool, following Pi's `find`.

Mirrors ``packages/coding-agent/src/core/tools/find.ts`` (pi-mono
``4df157433``): ``pattern`` plus optional ``path`` (default ``.``) and
``limit`` (default 1000); paths relative to the search directory, directories
with a trailing ``/``; output capped at 50 KB; a ``[1000 results limit
reached. Use limit=2000 for more, or refine pattern]`` style notice when a cap
cuts the results.

Pi runs ``fd --glob --hidden`` (downloading ``fd`` when it is missing). pipy
walks the tree in Python with the same observable rules, measured on fd
10.5.0:

- A pattern without ``/`` matches each entry's name at any depth, so ``*.ts``
  finds ``src/a/y.ts``.
- A pattern with ``/`` matches the entry's absolute path, with ``**/``
  prepended unless it starts with ``/`` or ``**/`` or is ``**``; there ``*``
  and ``?`` do not cross ``/``. A ``..`` component never matches.
- Smart case: case-insensitive unless the pattern has an uppercase letter.
- Directories and files both match; symlinks are listed but not followed.
- Hidden entries and ``.git`` are walked; fd's ignore files apply
  (:mod:`pipy_harness.native.tools.ignore_walk`, ``.fdignore``, and
  ``.gitignore`` outside a repository too, as Pi passes
  ``--no-require-git`` there).
- The walk stops at ``limit`` matches, like ``--max-results``; ``limit = 0``
  means no cap. The walk is depth-first with each directory's entries sorted,
  so results are deterministic where fd's order is not.

The path resolves like Pi's ``resolveToCwd`` (READ2, see
:mod:`pipy_harness.native.tools.path_utils`). Deviation from Pi, tracked in
``docs/backlog.md``: ``limit`` is ``integer``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.glob_match import GlobError, compile_glob
from pipy_harness.native.tools.ignore_walk import FD_IGNORE_FILE, WalkIgnore
from pipy_harness.native.tools.path_utils import resolve_to_cwd
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)

DEFAULT_LIMIT = 1000
_NO_LINE_LIMIT = 2**53 - 1

FIND_TOOL_DESCRIPTION = (
    "Search for files by glob pattern. Returns matching file paths relative to "
    "the search directory. Respects .gitignore. Output is truncated to "
    f"{DEFAULT_LIMIT} results or {DEFAULT_MAX_BYTES // 1024}KB (whichever is "
    "hit first)."
)


@dataclass(frozen=True, slots=True)
class _FindFailure:
    message: str


@dataclass(frozen=True, slots=True)
class _Matcher:
    regex: re.Pattern[str]
    full_path: bool

    def matches(self, name: str, absolute: str) -> bool:
        subject = absolute if self.full_path else name
        return self.regex.match(subject) is not None


@dataclass(frozen=True, slots=True)
class FindTool:
    """Find paths by glob like Pi's `find` (fd semantics, Python walk)."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="find",
            description=FIND_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern to match files, e.g. '*.ts', "
                            "'**/*.json', or 'src/**/*.spec.ts'"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory to search in (default: current directory)"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 1000)",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        pattern = self._validated_pattern(request.arguments["pattern"])
        limit = request.arguments.get("limit")
        effective_limit = DEFAULT_LIMIT if limit is None else limit
        if effective_limit < 0:
            return self._error(request, f"invalid limit {effective_limit}")
        search = self._resolve_search_root(
            request.arguments.get("path") or ".", context
        )
        if isinstance(search, _FindFailure):
            return self._error(request, search.message)
        try:
            matcher = _matcher(pattern)
        except GlobError as exc:
            return self._error(request, str(exc))

        results: list[str] = []
        for relative in self._walk(search, matcher, context):
            results.append(relative)
            if effective_limit and len(results) >= effective_limit:
                break
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=_format_output(results, limit=effective_limit),
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
        return pattern

    @staticmethod
    def _resolve_search_root(
        path_arg: object, context: ToolContext
    ) -> Path | _FindFailure:
        if not isinstance(path_arg, str):
            raise ToolArgumentError(
                "find", "path must be a string", field_path=("path",)
            )
        try:
            search_root = resolve_to_cwd(path_arg, context.workspace_root)
        except ValueError as exc:
            return _FindFailure(str(exc))
        if not search_root.exists():
            return _FindFailure("path does not exist")
        if not search_root.is_dir():
            return _FindFailure("path is not a directory")
        return search_root

    @staticmethod
    def _walk(
        search_root: Path, matcher: _Matcher, context: ToolContext
    ) -> Iterator[str]:
        """Yield matching paths relative to the search root, in walk order."""

        # Pi adds --no-require-git outside a repository; inside one the
        # repository root resets the .gitignore rules from above it.
        rules = WalkIgnore.for_search(
            search_root,
            tool_file=FD_IGNORE_FILE,
            no_require_git_outside_repo=True,
            cwd=context.workspace_root,
        )
        stack: list[tuple[str, WalkIgnore]] = [("", rules)]
        cancel = context.cancel_event
        while stack:
            if cancel is not None and cancel.is_set():
                return
            relative_dir, rules = stack.pop()
            directory = search_root / relative_dir
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            subdirs: list[tuple[str, WalkIgnore]] = []
            for name in names:
                relative = f"{relative_dir}{name}"
                entry = directory / name
                is_dir = entry.is_dir() and not entry.is_symlink()
                if rules.ignored(entry, is_dir=is_dir):
                    continue
                absolute = f"{search_root.as_posix()}/{relative}"
                if matcher.matches(name, absolute):
                    yield relative + "/" if is_dir else relative
                if is_dir:
                    subdirs.append((relative + "/", rules.descend(entry)))
            stack.extend(reversed(subdirs))

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"find error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _matcher(pattern: str) -> _Matcher:
    ignore_case = not any(char.isupper() for char in pattern)
    if "/" not in pattern:
        return _Matcher(
            compile_glob(pattern, literal_separator=False, ignore_case=ignore_case),
            full_path=False,
        )
    effective = pattern
    if not pattern.startswith(("/", "**/")) and pattern != "**":
        effective = f"**/{pattern}"
    return _Matcher(
        compile_glob(effective, literal_separator=True, ignore_case=ignore_case),
        full_path=True,
    )


def _format_output(results: list[str], *, limit: int) -> str:
    if not results:
        return "No files found matching pattern"
    truncation = truncate_head("\n".join(results), max_lines=_NO_LINE_LIMIT)
    output = truncation.content
    notices: list[str] = []
    # Pi: relativized.length >= effectiveLimit (always true for limit 0).
    if len(results) >= limit:
        notices.append(
            f"{limit} results limit reached. Use limit={limit * 2} for more, "
            "or refine pattern"
        )
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output


__all__ = ["DEFAULT_LIMIT", "FIND_TOOL_DESCRIPTION", "FindTool"]
