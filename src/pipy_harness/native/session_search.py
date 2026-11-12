"""Pi's session search (``session-selector-search.ts`` at pi-mono ``8c03fef4d``).

A query is either ``re:<pattern>`` (a case-insensitive regular expression) or
whitespace-separated tokens, where ``"quoted text"`` is a phrase and every
other token is a fuzzy token. All tokens must match; an unbalanced quote falls
back to plain whitespace tokens. Each session field is matched separately, so
a token never matches across field boundaries (different tokens may match
different fields): a token found as text in the name ranks first, then text
in the id, the messages or the working directory, then a fuzzy match
(:func:`fuzzy_match`) scattered over the name or the working directory. Ids
and messages match only as exact (case-insensitive) text. The ``/resume``
picker filters with it; its sort modes stay pipy's (backlog DF1-F7b).

Deviation: the pattern of ``re:`` is compiled by Python's ``re``, whose syntax
differs from JavaScript's in edge cases (a pattern either engine rejects
matches nothing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pipy_harness.native.fuzzy import (
    JS_WHITESPACE,
    fuzzy_match,
    js_trim,
    js_whitespace_split,
)

_JS_WHITESPACE_RUN = re.compile(
    "[" + "".join(re.escape(ch) for ch in sorted(JS_WHITESPACE)) + "]+"
)


@dataclass(frozen=True, slots=True)
class SearchToken:
    kind: Literal["fuzzy", "phrase"]
    value: str


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    mode: Literal["tokens", "regex"]
    tokens: tuple[SearchToken, ...] = ()
    regex: re.Pattern[str] | None = None
    error: str | None = field(default=None)


def _normalize_whitespace_lower(text: str) -> str:
    return js_trim(_JS_WHITESPACE_RUN.sub(" ", text.lower()))


def parse_search_query(query: str) -> ParsedSearchQuery:
    """Pi ``parseSearchQuery``."""

    trimmed = js_trim(query)
    if not trimmed:
        return ParsedSearchQuery("tokens")

    if trimmed.startswith("re:"):
        return _parse_regex(js_trim(trimmed[3:]))
    return _parse_tokens(trimmed)


def _parse_regex(pattern: str) -> ParsedSearchQuery:
    if not pattern:
        return ParsedSearchQuery("regex", error="Empty regex")
    try:
        return ParsedSearchQuery("regex", regex=re.compile(pattern, re.IGNORECASE))
    except (re.error, OverflowError, RecursionError, ValueError) as exc:
        # `re` raises OverflowError for a repeat count past its limit
        # (`a{4294967296}`) and RecursionError for pathological nesting;
        # like a JS syntax error, the query then matches nothing.
        return ParsedSearchQuery("regex", error=str(exc) or type(exc).__name__)


def _parse_tokens(trimmed: str) -> ParsedSearchQuery:
    """Token mode with quote support, e.g. ``foo "node cve" bar``."""

    tokens: list[SearchToken] = []
    buffer = ""
    in_quote = False

    def flush(kind: Literal["fuzzy", "phrase"]) -> None:
        nonlocal buffer
        value = js_trim(buffer)
        buffer = ""
        if value:
            tokens.append(SearchToken(kind, value))

    for ch in trimmed:
        if ch == '"':
            if in_quote:
                flush("phrase")
                in_quote = False
            else:
                flush("fuzzy")
                in_quote = True
            continue
        if not in_quote and ch in JS_WHITESPACE:
            flush("fuzzy")
            continue
        buffer += ch

    if in_quote:
        # Unbalanced quotes: plain whitespace tokens.
        return ParsedSearchQuery(
            "tokens",
            tokens=tuple(
                SearchToken("fuzzy", js_trim(part))
                for part in js_whitespace_split(trimmed)
                if js_trim(part)
            ),
        )

    flush("fuzzy")
    return ParsedSearchQuery("tokens", tokens=tuple(tokens))


# Match tiers, best first. Each token is scored by the best field it
# matches; tiers dominate fuzzy scores, so an exact hit anywhere beats a
# scattered subsequence in a short field.
_TIER_NAME = 0  # query text appears in the session name
_TIER_TEXT = 1  # query text appears in the session id, messages or cwd
_TIER_FUZZY = 2  # query characters appear scattered in the name or cwd
_TIER_WEIGHT = 1_000_000


@dataclass(frozen=True, slots=True)
class SessionSearchFields:
    """The fields Pi's ``matchSession`` reads from a ``SessionInfo``."""

    session_id: str
    name: str | None
    all_messages_text: str
    cwd: str


def _tier_score(tier: int, score: float = 0) -> float:
    return tier * _TIER_WEIGHT + score


def _match_literal_token(session: SessionSearchFields, value: str) -> float | None:
    """Exact (case-insensitive) text; ids and messages match only this way."""

    name = (session.name or "").lower()
    if value in name:
        return _tier_score(_TIER_NAME, fuzzy_match(value, name).score)
    if value in session.session_id.lower():
        return _tier_score(_TIER_TEXT)
    if value in session.all_messages_text.lower():
        return _tier_score(_TIER_TEXT)
    if value in session.cwd.lower():
        return _tier_score(_TIER_TEXT)
    return None


def _match_fuzzy_token(session: SessionSearchFields, value: str) -> float | None:
    literal = _match_literal_token(session, value.lower())
    if literal is not None:
        return literal

    best: float | None = None
    for field_text in (session.name or "", session.cwd):
        match = fuzzy_match(value, field_text)
        if match.matches and (best is None or match.score < best):
            best = match.score
    return None if best is None else _tier_score(_TIER_FUZZY, best)


def _match_phrase_token(session: SessionSearchFields, phrase: str) -> float | None:
    name = _normalize_whitespace_lower(session.name or "")
    if phrase in name:
        return _tier_score(_TIER_NAME, fuzzy_match(phrase, name).score)
    if phrase in session.session_id.lower():
        return _tier_score(_TIER_TEXT)
    if phrase in _normalize_whitespace_lower(session.all_messages_text):
        return _tier_score(_TIER_TEXT)
    if phrase in _normalize_whitespace_lower(session.cwd):
        return _tier_score(_TIER_TEXT)
    return None


def _match_regex(session: SessionSearchFields, regex: re.Pattern[str]) -> float | None:
    if session.name and regex.search(session.name):
        return _tier_score(_TIER_NAME)
    if (
        regex.search(session.session_id)
        or regex.search(session.all_messages_text)
        or regex.search(session.cwd)
    ):
        return _tier_score(_TIER_TEXT)
    return None


def match_session(
    session: SessionSearchFields, parsed: ParsedSearchQuery
) -> tuple[bool, float]:
    """Pi ``matchSession``: ``(matches, score)``, lower score better."""

    if parsed.mode == "regex":
        if parsed.regex is None:
            return False, 0
        score = _match_regex(session, parsed.regex)
        return (False, 0) if score is None else (True, score)

    total: float = 0
    for token in parsed.tokens:
        if token.kind == "phrase":
            phrase = _normalize_whitespace_lower(token.value)
            if not phrase:
                continue
            token_score = _match_phrase_token(session, phrase)
        else:
            token_score = _match_fuzzy_token(session, token.value)
        if token_score is None:
            return False, 0
        total += token_score
    return True, total


__all__ = [
    "ParsedSearchQuery",
    "SearchToken",
    "SessionSearchFields",
    "match_session",
    "parse_search_query",
]
