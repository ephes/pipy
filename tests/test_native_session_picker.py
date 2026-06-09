"""Pure helpers behind the interactive session picker (/resume + -r overlay).

These cover the filter/scope/sort/named-only row building, current-session
marking, relative-age formatting, and the escape-safe label rendering shared by
the captured-stream and live-TTY picker paths.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    build_session_picker_rows,
    format_relative_age,
    format_session_picker_label,
    sanitize_label_text,
)


def _entry(
    name: str | None, sid: str, *, cwd: str = "/ws", mtime: float = 0.0, msgs: int = 1
) -> SessionListEntry:
    return SessionListEntry(
        path=Path(f"/store/{sid}.jsonl"),
        session_id=sid,
        name=name,
        message_count=msgs,
        cwd=cwd,
        mtime=mtime,
    )


def test_sanitize_strips_control_and_escape_bytes() -> None:
    raw = "ok\x1b[31mred\x07\x9bbad\x7f"
    cleaned = sanitize_label_text(raw)
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "\x9b" not in cleaned
    assert "\x7f" not in cleaned
    assert "ok" in cleaned and "red" in cleaned


def test_relative_age_buckets() -> None:
    assert format_relative_age(now=100.0, mtime=100.0) == "now"
    assert format_relative_age(now=100.0 + 5 * 60, mtime=100.0) == "5m"
    assert format_relative_age(now=100.0 + 2 * 3600, mtime=100.0) == "2h"
    assert format_relative_age(now=100.0 + 3 * 86400, mtime=100.0) == "3d"


def test_scope_toggle_switches_source() -> None:
    project = [_entry("p", "aaa", mtime=2.0)]
    everything = [_entry("p", "aaa", mtime=2.0), _entry("o", "bbb", cwd="/other", mtime=1.0)]
    cur = build_session_picker_rows(project, everything, scope="current")
    assert [r.session_id for r in cur] == ["aaa"]
    all_rows = build_session_picker_rows(project, everything, scope="all")
    assert {r.session_id for r in all_rows} == {"aaa", "bbb"}


def test_named_only_filter() -> None:
    rows = build_session_picker_rows(
        [_entry("named", "aaa"), _entry(None, "bbb")],
        [],
        named_only=True,
    )
    assert [r.session_id for r in rows] == ["aaa"]


def test_query_matches_name_id_and_cwd() -> None:
    sessions = [
        _entry("alpha", "111", cwd="/projects/foo"),
        _entry("beta", "222", cwd="/projects/bar"),
    ]
    assert [r.session_id for r in build_session_picker_rows(sessions, [], query="alph")] == ["111"]
    assert [r.session_id for r in build_session_picker_rows(sessions, [], query="222")] == ["222"]
    assert [r.session_id for r in build_session_picker_rows(sessions, [], query="bar")] == ["222"]


def test_sort_modes() -> None:
    sessions = [
        _entry("b", "111", mtime=1.0),
        _entry("a", "222", mtime=2.0),
    ]
    recent = build_session_picker_rows(sessions, [], sort="recent")
    assert [r.session_id for r in recent] == ["222", "111"]
    by_name = build_session_picker_rows(sessions, [], sort="name")
    assert [r.name for r in by_name] == ["a", "b"]


def test_unnamed_sorted_last_by_name() -> None:
    sessions = [_entry(None, "111"), _entry("a", "222")]
    by_name = build_session_picker_rows(sessions, [], sort="name")
    assert [r.session_id for r in by_name] == ["222", "111"]


def test_current_session_marked_and_labeled() -> None:
    sessions = [_entry("cur", "111"), _entry("other", "222")]
    rows = build_session_picker_rows(
        sessions, [], current_path=Path("/store/111.jsonl")
    )
    current = next(r for r in rows if r.session_id == "111")
    assert current.is_current is True
    label = format_session_picker_label(current)
    assert "●" in label
    assert "cur" in label


def test_label_is_escape_safe() -> None:
    row = build_session_picker_rows([_entry("x\x1b[31m", "111", cwd="/a\x07b")], [])[0]
    label = format_session_picker_label(row, show_path=True, show_cwd=True, now=1.0)
    assert "\x1b" not in label
    assert "\x07" not in label


def test_label_sanitizes_user_controlled_session_id() -> None:
    # --session-id makes the id user-controlled; it must be sanitized too.
    row = build_session_picker_rows([_entry("n", "id\x1b[31mx")], [])[0]
    label = format_session_picker_label(row)
    assert "\x1b" not in label
