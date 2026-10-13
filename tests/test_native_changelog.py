"""Tests for the changelog parser/renderer (`pipy_harness.native.changelog`).

Mirrors Pi's `/changelog`: the package CHANGELOG.md is parsed newest-first, the
explicit command renders entries **oldest-first** under a "What's New" header,
and the startup display shows only the entries newer than `lastChangelogVersion`.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.changelog import (
    changelog_startup,
    new_entries_since,
    parse_changelog,
    read_changelog_entries,
    render_changelog,
)
from pipy_harness.native.version_check import pipy_version

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
        entries,
        last_version=None,
        current_version="0.3.0",
        collapse=False,
        is_fresh=True,
    )
    assert lines == []
    assert store == "0.3.0"


def test_startup_version_bump_shows_new_entries_and_records() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries,
        last_version="0.1.0",
        current_version="0.3.0",
        collapse=False,
        is_fresh=True,
    )
    text = "\n".join(lines)
    assert "0.3.0" in text and "0.2.0" in text
    assert "0.1.0" not in text  # already seen
    assert store == "0.3.0"


def test_startup_collapse_shows_condensed_line() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries,
        last_version="0.2.0",
        current_version="0.3.0",
        collapse=True,
        is_fresh=True,
    )
    text = "\n".join(lines)
    assert "0.3.0" in text
    assert "/changelog" in text
    assert store == "0.3.0"


def test_startup_no_bump_shows_nothing() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries,
        last_version="0.3.0",
        current_version="0.3.0",
        collapse=False,
        is_fresh=True,
    )
    assert lines == []
    assert store is None


def test_startup_resumed_session_skips_entirely() -> None:
    entries = parse_changelog(SAMPLE)
    lines, store = changelog_startup(
        entries,
        last_version="0.1.0",
        current_version="0.3.0",
        collapse=False,
        is_fresh=False,
    )
    assert lines == []
    assert store is None


def test_shipped_changelog_newest_release_matches_package_version() -> None:
    # A release bump must add its CHANGELOG section, and vice versa: the
    # startup "What's New" notice compares lastChangelogVersion with the
    # package version and shows the sections newer than the stored one.
    entries = read_changelog_entries()
    assert entries, "CHANGELOG.md has no released sections"
    assert entries[0].version == pipy_version()


def test_invalid_utf8_changelog_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(b"## [0.3.0]\n\n\xff")
    entries = read_changelog_entries(path)
    assert entries == []
    assert path.read_bytes() == b"## [0.3.0]\n\n\xff"
    assert "No changelog entries" in render_changelog(entries)
    assert changelog_startup(
        entries,
        last_version="0.2.0",
        current_version="0.3.0",
        collapse=False,
        is_fresh=True,
    ) == ([], "0.3.0")


def test_explicit_utf8_changelog_preserves_unicode(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    text = SAMPLE + "\n- Grüße 👋\n"
    path.write_text(text, encoding="utf-8")
    assert read_changelog_entries(path) == parse_changelog(text)


def test_missing_changelog_is_unavailable(tmp_path: Path) -> None:
    assert read_changelog_entries(tmp_path / "missing.md") == []


def test_invalid_changelog_does_not_block_startup_or_command(tmp_path, monkeypatch):
    import io

    from pipy_harness.native import FakeNativeProvider, changelog
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.settings import SettingsManager

    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(b"\xff")
    monkeypatch.setattr(changelog, "default_changelog_path", lambda: path)
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text('{"lastChangelogVersion":"0.0.0"}', encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")
    error_stream = io.StringIO()
    CodingSession(provider=provider, settings_manager=manager).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/changelog\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert "No changelog entries" in error_stream.getvalue()
    assert provider._call_counter[0] == 0
    assert manager.get_last_changelog_version() == pipy_version()
    assert path.read_bytes() == b"\xff"
