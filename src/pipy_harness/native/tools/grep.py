"""The `grep` tool, following Pi's `grep`.

Mirrors ``packages/coding-agent/src/core/tools/grep.ts`` (pi-mono
``4df157433``): a regex (or, with ``literal``, fixed-string) ``pattern`` plus
optional ``path``, ``glob``, ``ignoreCase``, ``context`` and ``limit``
(default 100 matches). Rows are ``path:N: text`` for matches and
``path-N- text`` for context lines, paths relative to the search directory
(or the file's name when ``path`` is a file), long lines cut to 500
characters, output capped at 50 KB, and a ``[100 matches limit reached. Use
limit=200 for more, or refine pattern]`` style notice when a cap applies.

Pi runs ``rg --json`` (downloading ripgrep when it is missing). pipy runs
``rg`` when it is on ``PATH`` and otherwise
:mod:`pipy_harness.native.tools.grep_fallback`, a Python search with rg's
rules and rg's JSON output (``re`` instead of Rust regex syntax, and only the
root ``.gitignore``). Both run as a child process that the tool reads
line by line, kills at the match limit, and kills when ``cancel_event`` is
set, so a pathological regex in the fallback can be stopped.

``rg`` runs in the resolved root (the workspace, or the reference root for a
``--read-root`` path); Pi runs it in its process cwd. Deviations from Pi,
each tracked in ``docs/backlog.md``: paths resolve through pipy's
``resolve_tool_path``, and ``.git``, ``.gitignore`` matches and generated
paths are refused as a search root and dropped from rg's results before the
limit counts them (READ2); ``context``/``limit`` are ``integer``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
from pipy_harness.native.tools.grep_fallback import kept
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
)

DEFAULT_LIMIT = 100
_NO_LINE_LIMIT = 2**53 - 1
_POLL_SECONDS = 0.1
_READ_CHUNK_BYTES = 64 * 1024

GREP_TOOL_DESCRIPTION = (
    "Search file contents for a pattern. Returns matching lines with file paths "
    "and line numbers. Respects .gitignore. Output is truncated to "
    f"{DEFAULT_LIMIT} matches or {DEFAULT_MAX_BYTES // 1024}KB (whichever is "
    f"hit first). Long lines are truncated to {GREP_MAX_LINE_LENGTH} chars."
)


class _GrepFailure(Exception):
    """A search error reported to the model as ``grep error: …``."""


@dataclass(frozen=True, slots=True)
class _Options:
    pattern: str
    glob: str | None
    ignore_case: bool
    literal: bool
    context: int
    limit: int


@dataclass(frozen=True, slots=True)
class _Location:
    search_path: Path
    root: Path
    is_directory: bool


@dataclass(frozen=True, slots=True)
class _Match:
    file_path: Path
    line_number: int
    line_text: str | None


@dataclass(slots=True)
class _Matches:
    items: list[_Match] = field(default_factory=list)
    limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class GrepTool:
    """Search file contents like Pi's `grep`."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="grep",
            description=GREP_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (regex or literal string)",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory or file to search (default: current directory)"
                        ),
                    },
                    "glob": {
                        "type": "string",
                        "description": (
                            "Filter files by glob pattern, e.g. '*.ts' or "
                            "'**/*.spec.ts'"
                        ),
                    },
                    "ignoreCase": {
                        "type": "boolean",
                        "description": "Case-insensitive search (default: false)",
                    },
                    "literal": {
                        "type": "boolean",
                        "description": (
                            "Treat pattern as literal string instead of regex "
                            "(default: false)"
                        ),
                    },
                    "context": {
                        "type": "integer",
                        "description": (
                            "Number of lines to show before and after each match "
                            "(default: 0)"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of matches to return (default: 100)"
                        ),
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        options = _options(request.arguments)
        location = self._location(request.arguments.get("path") or ".", context)
        if isinstance(location, str):
            return self._error(request, location)
        try:
            matches = _search(options, location, context.cancel_event)
        except _GrepFailure as exc:
            return self._error(request, str(exc))
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=_format_output(matches, options, location),
            provider_correlation_id=request.provider_correlation_id,
        )

    @staticmethod
    def _location(path_arg: object, context: ToolContext) -> _Location | str:
        if path_arg == ".":
            workspace = context.workspace_root.resolve()
            return _Location(workspace, workspace, is_directory=True)
        try:
            if not isinstance(path_arg, str):
                raise ValueError("path must be a string")
            resolved = resolve_tool_path(
                path_arg,
                workspace_root=context.workspace_root,
                reference_roots=context.reference_roots,
            )
        except ValueError as exc:
            raise ToolArgumentError("grep", str(exc), field_path=("path",)) from None
        if _is_ignored_or_generated(resolved.relative_label, resolved.root):
            return "path is ignored or under .git/generated directories"
        if not resolved.resolved.exists():
            return "path does not exist"
        return _Location(
            resolved.resolved, resolved.root, is_directory=resolved.resolved.is_dir()
        )

    def _error(self, request: ToolRequest, message: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"grep error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _options(arguments: Mapping[str, object]) -> _Options:
    pattern = arguments["pattern"]
    if not isinstance(pattern, str):
        raise ToolArgumentError(
            "grep", "pattern must be a string", field_path=("pattern",)
        )
    glob = arguments.get("glob")
    context_arg = arguments.get("context")
    limit_arg = arguments.get("limit")
    context_value = (
        context_arg if isinstance(context_arg, int) and context_arg > 0 else 0
    )
    limit_value = DEFAULT_LIMIT if not isinstance(limit_arg, int) else limit_arg
    return _Options(
        pattern=pattern,
        glob=glob if isinstance(glob, str) and glob else None,
        ignore_case=arguments.get("ignoreCase") is True,
        literal=arguments.get("literal") is True,
        context=context_value,
        # Pi: Math.max(1, limit ?? DEFAULT_LIMIT).
        limit=max(1, limit_value),
    )


# ---------------------------------------------------------------------------
# search process (rg, or the Python fallback with the same JSON output)


def _rg_argv(options: _Options, location: _Location) -> list[str]:
    argv = ["rg", "--json", "--line-number", "--color=never", "--hidden"]
    if options.ignore_case:
        argv.append("--ignore-case")
    if options.literal:
        argv.append("--fixed-strings")
    if options.glob is not None:
        argv.extend(["--glob", options.glob])
    argv.extend(["--", options.pattern, str(location.search_path)])
    return argv


def _event_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if isinstance(text, str):
        return text
    raw = value.get("bytes")
    if isinstance(raw, str):
        try:
            return os.fsdecode(base64.b64decode(raw))
        except ValueError:
            return None
    return None


def _fallback_argv(options: _Options, location: _Location) -> list[str]:
    config = {
        "pattern": options.pattern,
        "literal": options.literal,
        "ignore_case": options.ignore_case,
        "glob": options.glob,
        "root": str(location.root),
        "search_path": str(location.search_path),
        "limit": options.limit,
    }
    return [
        sys.executable,
        "-m",
        "pipy_harness.native.tools.grep_fallback",
        json.dumps(config),
    ]


def _collect(
    proc: subprocess.Popen[bytes],
    root: Path,
    limit: int,
    cancel: threading.Event | None,
    matches: _Matches,
) -> bool:
    """Read match events until the limit; return True when cancelled."""

    for line in _stdout_lines(proc, cancel):
        if line is None:
            return True
        match = _rg_match(line, root)
        if match is None:
            continue
        matches.items.append(match)
        if len(matches.items) >= limit:
            # Pi kills rg here; the process is killed by the caller.
            matches.limit_reached = True
            return False
    return False


def _search(
    options: _Options, location: _Location, cancel: threading.Event | None
) -> _Matches:
    if shutil.which("rg") is not None:
        argv, name = _rg_argv(options, location), "ripgrep"
    else:
        argv, name = _fallback_argv(options, location), "the grep fallback"
    matches = _Matches()
    with tempfile.TemporaryFile() as stderr_file:
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, shell=False
                argv,
                cwd=str(location.root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                shell=False,
            )
        except OSError as exc:
            raise _GrepFailure(f"Failed to run {name}: {exc}") from None
        try:
            aborted = _collect(proc, location.root, options.limit, cancel, matches)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        if aborted:
            raise _GrepFailure("Operation aborted")
        if not matches.limit_reached and proc.returncode not in (0, 1):
            stderr_file.seek(0)
            message = stderr_file.read().decode("utf-8", "replace").strip()
            raise _GrepFailure(message or f"{name} exited with code {proc.returncode}")
    return matches


def _stdout_lines(
    proc: subprocess.Popen[bytes], cancel: threading.Event | None
) -> Iterator[bytes | None]:
    """Yield the search's stdout lines; yield ``None`` once on ``cancel``."""

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    buffer = b""
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            if cancel is not None and cancel.is_set():
                yield None
                return
            if not selector.select(timeout=_POLL_SECONDS):
                continue
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            yield from lines
    if buffer:
        yield buffer


def _rg_match(line: bytes, root: Path) -> _Match | None:
    if not line.strip():
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("type") != "match":
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    path_text = _event_text(data.get("path"))
    line_number = data.get("line_number")
    if path_text is None or not isinstance(line_number, int):
        return None
    file_path = Path(path_text)
    if not file_path.is_absolute():
        file_path = root / file_path
    if not kept(file_path, root):
        return None
    lines = data.get("lines")
    line_text = lines.get("text") if isinstance(lines, dict) else None
    return _Match(
        file_path, line_number, line_text if isinstance(line_text, str) else None
    )


# ---------------------------------------------------------------------------
# output


@dataclass(slots=True)
class _Formatter:
    location: _Location
    context: int
    lines_truncated: bool = False
    _cache: dict[Path, list[str]] = field(default_factory=dict)

    def label(self, file_path: Path) -> str:
        if self.location.is_directory:
            try:
                relative = os.path.relpath(file_path, self.location.search_path)
            except ValueError:
                relative = ""
            if relative and not relative.startswith(".."):
                return relative.replace(os.sep, "/")
        return file_path.name

    def cut(self, text: str) -> str:
        result, was_truncated = truncate_line(text)
        if was_truncated:
            self.lines_truncated = True
        return result

    def file_lines(self, file_path: Path) -> list[str]:
        lines = self._cache.get(file_path)
        if lines is None:
            try:
                content = file_path.read_bytes().decode("utf-8", errors="replace")
                lines = re.split(r"\r\n|\r|\n", content)
            except OSError:
                lines = []
            self._cache[file_path] = lines
        return lines

    def rows(self, match: _Match) -> list[str]:
        label = self.label(match.file_path)
        if self.context == 0 and match.line_text is not None:
            text = match.line_text.replace("\r\n", "\n").replace("\r", "")
            text = text.removesuffix("\n")
            return [f"{label}:{match.line_number}: {self.cut(text)}"]
        lines = self.file_lines(match.file_path)
        if not lines:
            return [f"{label}:{match.line_number}: (unable to read file)"]
        if self.context > 0:
            start = max(1, match.line_number - self.context)
            end = min(len(lines), match.line_number + self.context)
        else:
            start = end = match.line_number
        block: list[str] = []
        for current in range(start, end + 1):
            text = lines[current - 1] if current - 1 < len(lines) else ""
            text = self.cut(text.replace("\r", ""))
            if current == match.line_number:
                block.append(f"{label}:{current}: {text}")
            else:
                block.append(f"{label}-{current}- {text}")
        return block


def _format_output(matches: _Matches, options: _Options, location: _Location) -> str:
    if not matches.items:
        return "No matches found"
    formatter = _Formatter(location, options.context)
    rows: list[str] = []
    for match in matches.items:
        rows.extend(formatter.rows(match))
    truncation = truncate_head("\n".join(rows), max_lines=_NO_LINE_LIMIT)
    output = truncation.content
    notices: list[str] = []
    if matches.limit_reached:
        notices.append(
            f"{options.limit} matches limit reached. Use limit={options.limit * 2} "
            "for more, or refine pattern"
        )
    if truncation.truncated:
        notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if formatter.lines_truncated:
        notices.append(
            f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. Use read tool "
            "to see full lines"
        )
    if notices:
        output += f"\n\n[{'. '.join(notices)}]"
    return output


__all__ = ["DEFAULT_LIMIT", "GREP_TOOL_DESCRIPTION", "GrepTool"]
