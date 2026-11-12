"""Pi's session search (``session-selector-search.ts``) and the picker filter.

Pinned results come from Pi's ``parseSearchQuery`` + ``matchSession`` run by
Node at pi-mono ``8c03fef4d`` over the sessions below. Each field is matched
separately (name text, then id/message/cwd text, then a scattered name/cwd
match). ``test_matches_pi_under_node`` repeats the comparison over a random
corpus when Node and a pi-mono checkout are present (``PI_MONO_DIR``, default
``/Users/jochen/src/pi-mono``) and is skipped otherwise.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from pipy_harness.native.session_search import (
    SessionSearchFields,
    match_session,
    parse_search_query,
)
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    build_session_picker_rows,
)

_SESSION = SessionSearchFields(
    "01J9ZK3Q", "Fix  the Login bug", "", "/Users/me/src/app"
)

# (query, mode, tokens, has_error, matches, score)
_PINNED = [
    ("", "tokens", [], False, True, 0),
    ("login", "tokens", [("fuzzy", "login")], False, True, -54.5),
    (
        "fix bug",
        "tokens",
        [("fuzzy", "fix"), ("fuzzy", "bug")],
        False,
        True,
        -59.89999999999999,
    ),
    ('"the login"', "tokens", [("phrase", "the login")], False, True, -192.8),
    ('"login the"', "tokens", [("phrase", "login the")], False, False, 0),
    ("fx lgn", "tokens", [("fuzzy", "fx"), ("fuzzy", "lgn")], False, True, 3999984.5),
    (
        '"unclosed quote',
        "tokens",
        [("fuzzy", '"unclosed'), ("fuzzy", "quote")],
        False,
        False,
        0,
    ),
    ("re:LOG.N", "regex", [], False, True, 0),
    ("re:", "regex", [], True, False, 0),
    ("re:(", "regex", [], True, False, 0),
    ("app 01j", "tokens", [("fuzzy", "app"), ("fuzzy", "01j")], False, True, 2000000),
    ("zzz", "tokens", [("fuzzy", "zzz")], False, False, 0),
    ('"fix  the"', "tokens", [("phrase", "fix  the")], False, True, -157.9),
    (
        ' "fix" "bug" ',
        "tokens",
        [("phrase", "fix"), ("phrase", "bug")],
        False,
        True,
        -60.199999999999996,
    ),
    ("me/src", "tokens", [("fuzzy", "me/src")], False, True, 1000000),
    # Ids match only as text: "j9z" is in the id, "fixapp" spans two fields.
    ("j9z", "tokens", [("fuzzy", "j9z")], False, True, 1000000),
    ("fixapp", "tokens", [("fuzzy", "fixapp")], False, False, 0),
    ("fix app", "tokens", [("fuzzy", "fix"), ("fuzzy", "app")], False, True, 999960.3),
    ("re:^/users", "regex", [], False, True, 1000000),
    ("re:fix", "regex", [], False, True, 0),
]

_MESSAGES = SessionSearchFields("abc", None, "Please   Refactor the PARSER now", "/w")

# (query, matches, score): message text matches only as text, never fuzzily.
_PINNED_MESSAGES = [
    ("refactor", True, 1000000),
    ("rfp", False, 0),
    ('"refactor the parser"', True, 1000000),
    ("re:parser", True, 1000000),
    ("w", True, 1000000),
    ("parser abc", True, 2000000),
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
    assert match_session(_SESSION, parsed) == (matches, score)


@pytest.mark.parametrize(("query", "matches", "score"), _PINNED_MESSAGES)
def test_message_text_matches_only_as_text(
    query: str, matches: bool, score: float
) -> None:
    assert match_session(_MESSAGES, parse_search_query(query)) == (matches, score)


_NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const m = await import(process.argv[2]);
const cases = JSON.parse(readFileSync(0, "utf8"));
const out = cases.map(([s, q]) => {
  const session = { id: s[0], name: s[1] ?? undefined, allMessagesText: s[2], cwd: s[3] };
  const parsed = m.parseSearchQuery(q);
  return [!!parsed.error, m.matchSession(session, parsed)];
});
process.stdout.write(JSON.stringify(out));
"""

_ALPHABET = 'abfgilnopx01 -_/."Σσ😀\u0085'
_QUERY_ALPHABET = 'abfgilnopx01 /."Σ😀'


def _random_text(rng: random.Random, alphabet: str, low: int, high: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(low, high)))


def _pi_session_search_module(tmp_path: Path) -> Path | None:
    root = Path(os.environ.get("PI_MONO_DIR", "/Users/jochen/src/pi-mono"))
    fuzzy = root / "packages" / "tui" / "src" / "fuzzy.ts"
    search = (
        root
        / "packages"
        / "coding-agent"
        / "src"
        / "modes"
        / "interactive"
        / "components"
        / "session-selector-search.ts"
    )
    if shutil.which("node") is None or not fuzzy.is_file() or not search.is_file():
        return None
    # Resolve the package import to the fuzzy module file (no install needed).
    source = search.read_text(encoding="utf-8").replace(
        '"@earendil-works/pi-tui"', json.dumps(fuzzy.as_uri())
    )
    module = tmp_path / "session-selector-search.ts"
    module.write_text(source, encoding="utf-8")
    return module


def test_matches_pi_under_node(tmp_path: Path) -> None:
    module = _pi_session_search_module(tmp_path)
    if module is None:
        pytest.skip("node or the pi-mono checkout is not available")
    rng = random.Random(20261112)
    cases: list[tuple[SessionSearchFields, str]] = []
    for _ in range(400):
        session = SessionSearchFields(
            _random_text(rng, _ALPHABET, 1, 10),
            rng.choice([None, "", _random_text(rng, _ALPHABET, 1, 16)]),
            _random_text(rng, _ALPHABET, 0, 30),
            _random_text(rng, _ALPHABET, 1, 14),
        )
        query = _random_text(rng, _QUERY_ALPHABET, 0, 8)
        if rng.random() < 0.15:
            query = "re:" + rng.choice(["a", "^/", "b.l", "x$", "(", "Σ"])
        cases.append((session, query))
    script = tmp_path / "session_search_diff.mjs"
    script.write_text(_NODE_SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script), module.as_uri()],
        input=json.dumps(
            [
                [[s.session_id, s.name, s.all_messages_text, s.cwd], query]
                for s, query in cases
            ]
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    expected = json.loads(proc.stdout)
    for (session, query), (pi_error, pi) in zip(cases, expected, strict=True):
        parsed = parse_search_query(query)
        result = match_session(session, parsed)
        assert (parsed.error is not None) == pi_error, (session, query)
        assert result == (pi["matches"], pi["score"]), (session, query)


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
