"""Pi's fuzzy matching (``packages/tui/src/fuzzy.ts`` at pi-mono ``1b347794e``).

A query matches when all of its characters appear in the text in order; a
lower score is a better match. The port is literal, including JavaScript's
string model: indices, lengths and the ``i * 0.1`` position penalty count
UTF-16 code units, ``\\s`` and ``trim()`` use the ECMAScript whitespace set,
and the score is built with the same float operations in the same order, so
``tests/test_native_fuzzy.py`` can compare results with Pi's own code run by
Node.
"""

from __future__ import annotations

import re
import sys
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

# ECMAScript WhiteSpace + LineTerminator: what `\s` and `String.prototype.trim`
# accept. Python's `str.isspace` differs (it adds U+001C-U+001F and U+0085
# and drops U+FEFF).
JS_WHITESPACE = frozenset(
    "\t\n\v\f\r       　﻿" + "".join(chr(code) for code in range(0x2000, 0x200B))
)
_JS_WHITESPACE_CLASS = "".join(sorted(re.escape(ch) for ch in JS_WHITESPACE))
_BOUNDARY_UNITS = frozenset(ord(ch) for ch in JS_WHITESPACE | set("-_./:"))
_TOKEN_SPLIT = re.compile(f"[{_JS_WHITESPACE_CLASS}/]+")
_ALPHA_NUMERIC = re.compile(r"(?P<letters>[a-z]+)(?P<digits>[0-9]+)")
_NUMERIC_ALPHA = re.compile(r"(?P<digits>[0-9]+)(?P<letters>[a-z]+)")


@dataclass(frozen=True, slots=True)
class FuzzyMatch:
    matches: bool
    score: float


def js_trim(text: str) -> str:
    """``String.prototype.trim``: strip the ECMAScript whitespace set."""

    start = 0
    end = len(text)
    while start < end and text[start] in JS_WHITESPACE:
        start += 1
    while end > start and text[end - 1] in JS_WHITESPACE:
        end -= 1
    return text[start:end]


def js_whitespace_split(text: str) -> list[str]:
    """``text.split(/\\s+/)`` with the ECMAScript whitespace set."""

    return re.split(f"[{_JS_WHITESPACE_CLASS}]+", text)


def utf16_units(text: str) -> array[int]:
    """The UTF-16 code units of ``text`` (lone surrogates kept as units)."""

    units = array("H")
    units.frombytes(text.encode("utf-16-le", "surrogatepass"))
    if sys.byteorder == "big":  # pragma: no cover - little-endian hosts only
        units.byteswap()
    return units


def _index_of(units: array[int], unit: int, start: int) -> int:
    for index in range(max(0, start), len(units)):
        if units[index] == unit:
            return index
    return -1


def _match_query(query: array[int], text: array[int], exact: bool) -> FuzzyMatch:
    if len(query) == 0:
        return FuzzyMatch(True, 0)
    if len(query) > len(text):
        return FuzzyMatch(False, 0)

    query_index = 0
    score: float = 0
    last_match_index = -1
    consecutive_matches = 0

    while query_index < len(query):
        i = _index_of(text, query[query_index], last_match_index + 1)
        if i == -1:
            break

        is_word_boundary = i == 0 or text[i - 1] in _BOUNDARY_UNITS

        # Reward consecutive matches
        if last_match_index == i - 1:
            consecutive_matches += 1
            score -= consecutive_matches * 5
        else:
            consecutive_matches = 0
            # Penalize gaps
            if last_match_index >= 0:
                score += (i - last_match_index - 1) * 2

        # Reward word boundary matches
        if is_word_boundary:
            score -= 10

        # Slight penalty for later matches
        score += i * 0.1

        last_match_index = i
        query_index += 1

    if query_index < len(query):
        return FuzzyMatch(False, 0)

    if exact:
        score -= 100

    return FuzzyMatch(True, score)


def fuzzy_match(query: str, text: str) -> FuzzyMatch:
    """Pi ``fuzzyMatch``: all query characters in order, lower score better."""

    query_lower = query.lower()
    text_lower = text.lower()
    text_units = utf16_units(text_lower)

    primary = _match_query(
        utf16_units(query_lower), text_units, query_lower == text_lower
    )
    if primary.matches:
        return primary

    alpha_numeric = _ALPHA_NUMERIC.fullmatch(query_lower)
    numeric_alpha = _NUMERIC_ALPHA.fullmatch(query_lower)
    if alpha_numeric is not None:
        swapped = alpha_numeric["digits"] + alpha_numeric["letters"]
    elif numeric_alpha is not None:
        swapped = numeric_alpha["letters"] + numeric_alpha["digits"]
    else:
        swapped = ""

    if not swapped:
        return primary

    swapped_match = _match_query(
        utf16_units(swapped), text_units, swapped == text_lower
    )
    if not swapped_match.matches:
        return primary

    return FuzzyMatch(True, swapped_match.score + 5)


def fuzzy_filter(
    items: Sequence[T], query: str, get_text: Callable[[T], str]
) -> list[T]:
    """Pi ``fuzzyFilter``: keep items matching every token, best first.

    Tokens are separated by whitespace or ``/``. A blank query keeps every
    item in its order; the sort is stable, like ``Array.prototype.sort``.
    """

    if not js_trim(query):
        return list(items)

    tokens = [token for token in _TOKEN_SPLIT.split(js_trim(query)) if token]
    if not tokens:
        return list(items)

    results: list[tuple[T, float]] = []
    for item in items:
        text = get_text(item)
        total_score: float = 0
        all_match = True
        for token in tokens:
            match = fuzzy_match(token, text)
            if match.matches:
                total_score += match.score
            else:
                all_match = False
                break
        if all_match:
            results.append((item, total_score))

    results.sort(key=lambda result: result[1])
    return [item for item, _score in results]


__all__ = [
    "JS_WHITESPACE",
    "FuzzyMatch",
    "fuzzy_filter",
    "fuzzy_match",
    "js_trim",
    "js_whitespace_split",
    "utf16_units",
]
