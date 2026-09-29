"""The `grep` tool's search when ripgrep is not installed.

Run as ``python -m pipy_harness.native.tools.grep_fallback <config-json>``. It
behaves like the ``rg --json`` call the `grep` tool makes: it prints one
ripgrep-style ``match`` event per matching line on stdout, exits 0 when it
found a match and 1 when it found none, and on a bad regex or glob prints the
error on stderr and exits 2. Running in a child process lets the tool kill it
at the match limit or on cancellation, which also stops a pathological
(backtracking) regex that Python's ``re`` cannot interrupt in-process.

The rules follow ripgrep's, with the differences the `grep` module lists:

- ``re`` syntax instead of Rust regex (``re.escape`` for ``literal``);
- ``glob`` as ``rg --glob`` applies it: case-sensitive; without ``/`` it
  matches the file name at any depth, with ``/`` the path relative to the
  root (the working directory); a leading ``!`` excludes matching files and
  directories; a file named as the search path is searched whatever the glob;
- the walk visits directories in sorted order, does not follow symlinks, and
  applies pipy's path policy (``.git``, the root ``.gitignore``, generated
  paths);
- a file whose first 64 KB hold a NUL byte is skipped (rg's binary rule), and
  a later NUL byte ends the search in that file. Files are read line by line.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
    _resolved_relative_label,
)
from pipy_harness.native.tools.glob_match import GlobError, compile_glob

_BINARY_PROBE_BYTES = 64 * 1024


class _SearchError(Exception):
    pass


def kept(file_path: Path, root: Path) -> bool:
    """Apply pipy's path policy to one file or directory (READ2 keeps it)."""

    try:
        label = _resolved_relative_label(file_path.resolve(), root)
    except OSError:
        return False
    return label is not None and not _is_ignored_or_generated(label, root)


@dataclass(frozen=True, slots=True)
class _GlobFilter:
    """One ``rg --glob`` override: a whitelist glob, or an exclusion with ``!``."""

    regex: re.Pattern[str]
    negated: bool
    by_path: bool
    root: Path

    @classmethod
    def build(cls, glob: str | None, root: Path) -> _GlobFilter | None:
        if glob is None:
            return None
        negated = glob.startswith("!")
        body = glob[1:] if negated else glob
        by_path = "/" in body
        try:
            regex = compile_glob(
                body.lstrip("/") if by_path else body,
                literal_separator=True,
                ignore_case=False,
            )
        except GlobError as exc:
            raise _SearchError(str(exc)) from None
        return cls(regex, negated, by_path, root)

    def _hit(self, path: Path) -> bool:
        if not self.by_path:
            return self.regex.match(path.name) is not None
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        return self.regex.match(relative) is not None

    def keeps_file(self, path: Path) -> bool:
        return not self._hit(path) if self.negated else self._hit(path)

    def enters_directory(self, path: Path) -> bool:
        # A whitelist glob never prunes directories; an exclusion does.
        return not (self.negated and self._hit(path))


def _walk_files(
    search_path: Path, root: Path, glob_filter: _GlobFilter | None
) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(search_path):
        directory = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not (directory / name).is_symlink()
            and kept(directory / name, root)
            and (glob_filter is None or glob_filter.enters_directory(directory / name))
        )
        for name in sorted(filenames):
            candidate = directory / name
            if candidate.is_symlink() or not kept(candidate, root):
                continue
            if glob_filter is not None and not glob_filter.keeps_file(candidate):
                continue
            yield candidate


def _matching_lines(
    file_path: Path, regex: re.Pattern[str]
) -> Iterator[tuple[int, str]]:
    try:
        handle = file_path.open("rb")
    except OSError:
        return
    with handle:
        try:
            if b"\x00" in handle.read(_BINARY_PROBE_BYTES):
                return
            handle.seek(0)
            for number, raw in enumerate(handle, start=1):
                if b"\x00" in raw:
                    return
                line = raw.decode("utf-8", errors="replace")
                if regex.search(line.removesuffix("\n")) is not None:
                    yield number, line
        except OSError:
            return


def search(config: dict[str, object], out: TextIO) -> int:
    """Run one search; write match events to ``out``; return the exit code."""

    pattern = str(config["pattern"])
    source = re.escape(pattern) if config.get("literal") else pattern
    try:
        regex = re.compile(source, re.IGNORECASE if config.get("ignore_case") else 0)
    except re.error as exc:
        raise _SearchError(f"regex parse error: {exc}") from None
    root = Path(str(config["root"]))
    search_path = Path(str(config["search_path"]))
    glob = config.get("glob")
    glob_filter = _GlobFilter.build(glob if isinstance(glob, str) else None, root)
    limit = int(str(config["limit"]))

    if search_path.is_dir():
        files: Iterator[Path] = _walk_files(search_path, root, glob_filter)
    else:
        # rg searches a file named on the command line whatever the glob.
        files = iter([search_path])

    found = 0
    for file_path in files:
        for number, line in _matching_lines(file_path, regex):
            event = {
                "type": "match",
                "data": {
                    "path": {"text": str(file_path)},
                    "lines": {"text": line},
                    "line_number": number,
                },
            }
            out.write(json.dumps(event) + "\n")
            out.flush()
            found += 1
            if found >= limit:
                return 0
    return 0 if found else 1


def main(argv: list[str]) -> int:
    try:
        return search(json.loads(argv[1]), sys.stdout)
    except _SearchError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
