"""Changelog parsing/rendering and the version surface (Pi parity).

Mirrors Pi's `handleChangelogCommand` / `getChangelogForDisplay`:

- The package ``CHANGELOG.md`` is parsed into entries **newest-first** (the
  order they are written, Keep-a-Changelog style with ``## [x.y.z]`` headers).
- The explicit ``/changelog`` command renders entries **oldest-first** as
  Markdown under a "What's New" header (Pi calls ``allEntries.reverse()``).
- The startup display shows only the entries **newer than**
  ``lastChangelogVersion`` (``new_entries_since``); the caller decides when to
  show them (version bump on a fresh session) vs. skip (first run / resumed).

Stdlib-only; no provider turn or network access is involved here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADER_RE = re.compile(r"^##\s+.*?(\d+\.\d+\.\d+[0-9A-Za-z.\-]*)", re.MULTILINE)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    version: str
    content: str


def default_changelog_path() -> Path:
    """Resolve the shipped ``CHANGELOG.md`` (repo/package root).

    Walks up from this module to the first ancestor containing a
    ``CHANGELOG.md`` (the repo root in development, the package root when
    installed). Callers (tests, the conformance gate) may pass an explicit path
    instead.
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
    return here.parent / "CHANGELOG.md"


def read_changelog_entries(path: Path | None = None) -> list[ChangelogEntry]:
    """Read and parse the shipped CHANGELOG.md; empty list when unreadable."""

    target = path if path is not None else default_changelog_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_changelog(text)


def parse_changelog(text: str) -> list[ChangelogEntry]:
    """Parse changelog text into entries, newest-first (as written).

    Each entry spans from a ``## …`` version header to the next header (or EOF)
    and carries the full section text (header + body). A header without a
    parseable ``x.y.z`` version is skipped.
    """

    matches = list(_HEADER_RE.finditer(text))
    entries: list[ChangelogEntry] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        entries.append(ChangelogEntry(version=match.group(1), content=content))
    return entries


def render_changelog(entries: list[ChangelogEntry]) -> str:
    """Render entries oldest-first as Markdown under a "What's New" header."""

    if not entries:
        return "## What's New\n\nNo changelog entries found."
    body = "\n\n".join(entry.content for entry in reversed(entries))
    return f"## What's New\n\n{body}"


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(version)
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def new_entries_since(
    entries: list[ChangelogEntry], last_version: str | None
) -> list[ChangelogEntry]:
    """Return entries newer than ``last_version`` (newest-first order kept).

    ``None`` returns all entries (the caller suppresses display on a true first
    run). A pre-release/suffix on the same ``x.y.z`` is treated as equal.
    """

    if last_version is None:
        return list(entries)
    threshold = _version_tuple(last_version)
    return [e for e in entries if _version_tuple(e.version) > threshold]


def changelog_startup(
    entries: list[ChangelogEntry],
    *,
    last_version: str | None,
    current_version: str,
    collapse: bool,
    is_fresh: bool,
) -> tuple[list[str], str | None]:
    """Compute the startup changelog display (Pi ``getChangelogForDisplay``).

    Returns ``(lines, version_to_store)``:

    - Resumed/continued sessions (``is_fresh=False``): show nothing, store
      nothing.
    - First run (``last_version is None``): show nothing, but record the current
      version so the next upgrade shows the diff.
    - No bump (``last_version`` >= current): show nothing, store nothing.
    - Version bump: show the new entries (or, when ``collapse``, a one-line
      "Updated to vX" notice) and record the current version.
    """

    if not is_fresh:
        return [], None
    if last_version is None:
        return [], current_version
    if _version_tuple(last_version) >= _version_tuple(current_version):
        return [], None
    if collapse:
        lines = [
            f"Updated to v{current_version}. Use /changelog for the full release notes."
        ]
    else:
        new = new_entries_since(entries, last_version)
        lines = [render_changelog(new)] if new else []
    return lines, current_version
