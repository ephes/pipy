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
  matches the entry's name at any depth, with ``/`` the path relative to the
  root (the working directory); a leading ``!`` excludes matching files and
  directories; a file named as the search path is searched whatever the glob;
- the glob is an override checked before the ignore rules: a file matching
  a positive glob is searched even when ignored, a file not matching it is
  skipped, a directory matching it is entered even when ignored, and any
  other directory (and, with a ``!`` glob, any non-matching entry) falls
  through to the ignore rules;
- the walk includes hidden entries and ``.git``, applies rg's ignore files
  (:mod:`pipy_harness.native.tools.ignore_walk`), visits directories in
  sorted order and does not follow symlinks;
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

from pipy_harness.native.tools.glob_match import GlobError, compile_glob
from pipy_harness.native.tools.ignore_walk import RG_IGNORE_FILE, WalkIgnore

_BINARY_PROBE_BYTES = 64 * 1024


class _SearchError(Exception):
    pass


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

    def decide(self, path: Path, *, is_dir: bool) -> bool | None:
        """Include (True), skip (False) or defer to the ignore rules (None)."""

        hit = self._hit(path)
        if self.negated:
            return False if hit else None
        if hit:
            return True
        return None if is_dir else False


def _walk_files(search_path: Path, glob_filter: _GlobFilter | None) -> Iterator[Path]:
    """Yield the files rg would search below ``search_path``, in sorted order."""

    rules = WalkIgnore.for_search(
        search_path, tool_file=RG_IGNORE_FILE, no_require_git_outside_repo=False
    )
    stack: list[tuple[Path, WalkIgnore]] = [(search_path, rules)]
    while stack:
        directory, rules = stack.pop()
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        subdirs: list[Path] = []
        for name in names:
            entry = directory / name
            if entry.is_symlink():
                continue
            is_dir = entry.is_dir()
            decision = (
                glob_filter.decide(entry, is_dir=is_dir)
                if glob_filter is not None
                else None
            )
            if decision is None:
                decision = not rules.ignored(entry, is_dir=is_dir)
            if not decision:
                continue
            if is_dir:
                subdirs.append(entry)
            else:
                yield entry
        stack.extend((subdir, rules.descend(subdir)) for subdir in reversed(subdirs))


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
        files: Iterator[Path] = _walk_files(search_path, glob_filter)
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
