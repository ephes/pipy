"""Pi's session search (``session-selector-search.ts``) and the picker filter.

Pinned results come from Pi's ``parseSearchQuery`` + ``matchSession`` run by
Node at pi-mono ``1b347794e`` over the session below (no message text).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.session_search import (
    match_session_text,
    parse_search_query,
    session_search_text,
)
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    build_session_picker_rows,
)

_TEXT = session_search_text("01J9ZK3Q", "Fix  the Login bug", "", "/Users/me/src/app")

# (query, mode, tokens, has_error, matches, score)
_PINNED = [
    ("", "tokens", [], False, True, 0),
    ("login", "tokens", [("fuzzy", "login")], False, True, -49.99999999999999),
    ("fix bug", "tokens", [("fuzzy", "fix"), ("fuzzy", "bug")], False, True, -39.5),
    ('"the login"', "tokens", [("phrase", "the login")], False, True, 1.3),
    ('"login the"', "tokens", [("phrase", "login the")], False, False, 0),
    (
        "fx lgn",
        "tokens",
        [("fuzzy", "fx"), ("fuzzy", "lgn")],
        False,
        True,
        -5.999999999999999,
    ),
    (
        '"unclosed quote',
        "tokens",
        [("fuzzy", '"unclosed'), ("fuzzy", "quote")],
        False,
        False,
        0,
    ),
    ("re:LOG.N", "regex", [], False, True, 1.8),
    ("re:", "regex", [], True, False, 0),
    ("re:(", "regex", [], True, False, 0),
    (
        "app 01j",
        "tokens",
        [("fuzzy", "app"), ("fuzzy", "01j")],
        False,
        True,
        -51.49999999999999,
    ),
    ("zzz", "tokens", [("fuzzy", "zzz")], False, False, 0),
    ('"fix  the"', "tokens", [("phrase", "fix  the")], False, True, 0.9),
    (
        ' "fix" "bug" ',
        "tokens",
        [("phrase", "fix"), ("phrase", "bug")],
        False,
        True,
        3.2,
    ),
]


@pytest.mark.parametrize(
    ("query", "mode", "tokens", "has_error", "matches", "score"), _PINNED
)
def test_parse_and_match_follow_pi(
    query: str,
    mode: str,
    tokens: list[tuple[str, str]],
    has_error: bool,
    matches: bool,
    score: float,
) -> None:
    parsed = parse_search_query(query)
    assert parsed.mode == mode
    assert [(token.kind, token.value) for token in parsed.tokens] == tokens
    assert (parsed.error is not None) == has_error
    assert match_session_text(_TEXT, parsed) == (matches, score)


def _entry(
    tmp_path: Path, session_id: str, name: str | None, mtime: float
) -> SessionListEntry:
    return SessionListEntry(
        path=tmp_path / f"{session_id}.jsonl",
        session_id=session_id,
        name=name,
        message_count=1,
        cwd="/w",
        mtime=mtime,
    )


def test_picker_filters_with_pi_search_and_keeps_its_sort(tmp_path: Path) -> None:
    entries = [
        _entry(tmp_path, "aaa111", "refactor parser", 3.0),
        _entry(tmp_path, "bbb222", "fix login", 2.0),
        _entry(tmp_path, "ccc333", None, 1.0),
    ]
    rows = build_session_picker_rows(entries, entries, query="rfp")
    assert [row.session_id for row in rows] == ["aaa111"]
    rows = build_session_picker_rows(entries, entries, query='"fix login"')
    assert [row.session_id for row in rows] == ["bbb222"]
    # Filter only: the recent order stays.
    rows = build_session_picker_rows(entries, entries, query="w")
    assert [row.session_id for row in rows] == ["aaa111", "bbb222", "ccc333"]
    # An invalid regex matches nothing (Pi returns an empty list).
    assert build_session_picker_rows(entries, entries, query="re:(") == []


@pytest.mark.parametrize(
    "query",
    ["re:a{4294967296}", "re:" + "(" * 2000 + ")" * 2000, "re:(?P<1>x)"],
)
def test_regex_compile_failures_match_nothing(tmp_path: Path, query: str) -> None:
    # Python `re` raises OverflowError / RecursionError / re.error for these;
    # the picker must treat them like Pi's invalid regex: no rows, no crash.
    parsed = parse_search_query(query)
    assert parsed.error
    entries = [_entry(tmp_path, "aaa111", "x", 1.0)]
    assert build_session_picker_rows(entries, entries, query=query) == []
