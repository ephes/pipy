"""Pi's session search (``session-selector-search.ts`` at pi-mono ``1b347794e``).

A query is either ``re:<pattern>`` (a case-insensitive regular expression) or
whitespace-separated tokens, where ``"quoted text"`` is a phrase matched as a
substring after whitespace normalization and every other token is a
:func:`fuzzy_match`. All tokens must match. An unbalanced quote falls back to
plain whitespace tokens. The ``/resume`` picker filters with it; its sort
modes stay pipy's (backlog DF1-F7b).

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
    utf16_units,
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


def _match_regex(text: str, regex: re.Pattern[str] | None) -> tuple[bool, float]:
    if regex is None:
        return False, 0
    found = regex.search(text)
    if found is None:
        return False, 0
    return True, len(utf16_units(text[: found.start()])) * 0.1


def match_session_text(text: str, parsed: ParsedSearchQuery) -> tuple[bool, float]:
    """Pi ``matchSession`` over a session's search text: ``(matches, score)``."""

    if parsed.mode == "regex":
        return _match_regex(text, parsed.regex)

    if not parsed.tokens:
        return True, 0

    total: float = 0
    normalized: str | None = None
    for token in parsed.tokens:
        if token.kind == "phrase":
            if normalized is None:
                normalized = _normalize_whitespace_lower(text)
            phrase = _normalize_whitespace_lower(token.value)
            if not phrase:
                continue
            index = normalized.find(phrase)
            if index < 0:
                return False, 0
            total += len(utf16_units(normalized[:index])) * 0.1
            continue
        match = fuzzy_match(token.value, text)
        if not match.matches:
            return False, 0
        total += match.score
    return True, total


def session_search_text(
    session_id: str, name: str | None, all_messages_text: str, cwd: str
) -> str:
    """Pi ``getSessionSearchText``: ``id name allMessagesText cwd``."""

    return f"{session_id} {name or ''} {all_messages_text} {cwd}"


__all__ = [
    "ParsedSearchQuery",
    "SearchToken",
    "match_session_text",
    "parse_search_query",
    "session_search_text",
]
