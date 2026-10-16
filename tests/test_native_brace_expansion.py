"""Tests for `pipy_harness.native.brace_expansion`.

The expected results are what `expand()` of brace-expansion 5.0.9 (the
version minimatch 10.2.6 uses in Pi) returns; each case was checked against
it with Node.
"""

from __future__ import annotations

import random

import pytest

from pipy_harness.native import brace_expansion
from pipy_harness.native.brace_expansion import (
    EXPANSION_MAX,
    EXPANSION_MAX_LENGTH,
    expand,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a{b,c}d", ["abd", "acd"]),
        ("{1..3}", ["1", "2", "3"]),
        ("{a..e..2}", ["a", "c", "e"]),
        ("{01..03}", ["01", "02", "03"]),
        ("{3..1}", ["3", "2", "1"]),
        ("x{-1..1}", ["x-1", "x0", "x1"]),
        ("{5..05}", ["05"]),
        ("{a,{b,c}}", ["a", "b", "c"]),
        ("{a}", ["{a}"]),
        ("${a,b}", ["${a,b}"]),
        ("{mcp,${codemode,tool-search}}", ["mcp", "${codemode,tool-search}"]),
        ("{},a}b", ["{},a}b"]),
        ("a{},b}c", ["a}c", "abc"]),
        ("{a},b}", ["a}", "b"]),
        ("x{{a,b}}y", ["x{a}y", "x{b}y"]),
        ("\\{a,b}", ["{a,b}"]),
        ("{a,\\,b}", ["a", ",b"]),
        ("a\\\\{b,c}", ["a\\b", "a\\c"]),
        ("{a..\\}", ["{a..}"]),
        ("{a,,b}", ["a", "b"]),
        ("{,a}", ["a"]),
        ("a{,b}", ["a", "ab"]),
        ("", []),
    ],
)
def test_expands_as_brace_expansion(text: str, expected: list[str]) -> None:
    assert expand(text) == expected


def test_the_output_is_cut_at_the_count_and_length_limits() -> None:
    by_count = expand("{a,b}" * 17)
    assert len(by_count) == EXPANSION_MAX
    assert by_count[0] == "a" * 17
    by_length = expand("x" * 1000 + "{a,b}" * 16)
    assert len(by_length) == 3937
    assert sum(map(len, by_length)) == 3_999_992 <= EXPANSION_MAX_LENGTH
    # Lengths are counted in UTF-16 code units.
    assert len(expand("\U0001f600" * 40 + "{a,b}" * 17)) == 41237


def test_the_length_limit_counts_the_escape_markers() -> None:
    # As brace-expansion, the limit sees an escaped `{` or `}` as its
    # marker (`\0OPEN` + a random number's digits + `\0`), not as one
    # character.
    each = 16 + len(brace_expansion._ESC_OPEN) + len(brace_expansion._ESC_CLOSE)
    expected = min(2**16, EXPANSION_MAX_LENGTH // each)
    assert len(expand("{}" + "{a,b}" * 16)) == expected


@pytest.mark.parametrize(
    ("number", "text"),
    [
        (0.1234567890123456, "0.1234567890123456"),
        (8.848497548774592e-05, "0.00008848497548774592"),
        (0.0004259357813757303, "0.0004259357813757303"),
        (1.5e-07, "1.5e-7"),
        (0.0, "0"),
    ],
)
def test_escape_markers_write_the_random_number_as_javascript(
    monkeypatch: pytest.MonkeyPatch, number: float, text: str
) -> None:
    monkeypatch.setattr(random, "random", lambda: number)
    assert brace_expansion._marker("OPEN") == "\0OPEN" + text + "\0"


def test_sequence_bounds_are_javascript_numbers() -> None:
    assert expand("{" + "0" * 4301 + "..1}") == ["0" * 4301, "0" * 4300 + "1"]
    assert expand("{" + "9" * 400 + ".." + "9" * 400 + "}")[:2] == [
        "Infinity",
        "Infinity",
    ]
    assert expand("{99999999999999999999..100000000000000000003}")[0] == (
        "100000000000000000000"
    )
    assert expand("{" + "1" * 22 + ".." + "1" * 22 + "}")[0] == (
        "1.1111111111111111e+21"
    )
    assert expand("{-0..1}") == ["0", "1"]
    # The shortest digits that read back as the number, as JavaScript.
    assert expand("{1000000000000000100..1000000000000000100}")[0] == (
        "1000000000000000100"
    )
