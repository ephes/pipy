"""Tests for the changelog parser/renderer (`pipy_harness.native.changelog`).

Mirrors Pi's `/changelog`: the package CHANGELOG.md is parsed newest-first, the
explicit command renders entries **oldest-first** under a "What's New" header,
and the startup display shows only the entries newer than `lastChangelogVersion`.
"""

from __future__ import annotations

from pipy_harness.native.changelog import (
    changelog_startup,
    new_entries_since,
    parse_changelog,
    render_changelog,
)

SAMPLE = """\
# Changelog

## [0.3.0] - 2026-06-03

- Newest feature.

## [0.2.0] - 2026-05-01

- Middle feature.

## [0.1.0] - 2026-04-01

- First release.
"""


def test_parse_changelog_newest_first() -> None:
    entries = parse_changelog(SAMPLE)
    assert [e.version for e in entries] == ["0.3.0", "0.2.0", "0.1.0"]
    assert "Newest feature." in entries[0].content
    assert entries[0].content.startswith("## ")


def test_parse_empty_changelog() -> None:
    assert parse_changelog("# Changelog\n\nNothing yet.\n") == []


def test_render_changelog_oldest_first_under_header() -> None:
    out = render_changelog(parse_changelog(SAMPLE))
    assert "What's New" in out
    # Oldest-first: 0.1.0 appears before 0.3.0 in the rendered output.
    assert out.index("0.1.0") < out.index("0.3.0")


def test_render_empty_changelog() -> None:
    assert "No changelog entries" in render_changelog([])


def test_new_entries_since_returns_newer_only() -> None:
    entries = parse_changelog(SAMPLE)
    newer = new_entries_since(entries, "0.2.0")
    assert [e.version for e in newer] == ["0.3.0"]


def test_new_entries_since_none_returns_all() -> None:
    entries = parse_changelog(SAMPLE)
    assert [e.version for e in new_entries_since(entries, None)] == [
        "0.3.0",
        "0.2.0",
        "0.1.0",
    ]


def test_new_entries_since_current_returns_empty() -> None:
    entries = parse_changelog(SAMPLE)
    assert new_entries_since(entries, "0.3.0") == []


def test_startup_first_run_shows_nothing_but_records_version() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries, last_version=None, current_version="0.3.0", collapse=False, is_fresh=True
    )
    assert lines == []
    assert store == "0.3.0"


def test_startup_version_bump_shows_new_entries_and_records() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries, last_version="0.1.0", current_version="0.3.0", collapse=False, is_fresh=True
    )
    text = "\n".join(lines)
    assert "0.3.0" in text and "0.2.0" in text
    assert "0.1.0" not in text  # already seen
    assert store == "0.3.0"


def test_startup_collapse_shows_condensed_line() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries, last_version="0.2.0", current_version="0.3.0", collapse=True, is_fresh=True
    )
    text = "\n".join(lines)
    assert "0.3.0" in text
    assert "/changelog" in text
    assert store == "0.3.0"


def test_startup_no_bump_shows_nothing() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries, last_version="0.3.0", current_version="0.3.0", collapse=False, is_fresh=True
    )
    assert lines == []
    assert store is None


def test_startup_resumed_session_skips_entirely() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries, last_version="0.1.0", current_version="0.3.0", collapse=False, is_fresh=False
    )
    assert lines == []
    assert store is None
