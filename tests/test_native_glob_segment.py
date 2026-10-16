"""Tests for `pipy_harness.native.glob_segment`, one segment as minimatch
compiles it.

The expected results are what `minimatch(path, pattern)` of minimatch
10.2.6 (the version Pi's coding agent depends on) returns for a path and a
pattern of one segment; each case was checked against it with Node.
"""

from __future__ import annotations

import pytest

from pipy_harness.native.glob_segment import segment_matcher


@pytest.mark.parametrize(
    ("segment", "pattern", "expected"),
    [
        ("abc", "+(a|@(b|c))", True),
        ("b", "@(a|@(b|c))", True),
        ("ab", "*(a|+(b))", True),
        ("x", "@(@(@(@(x))))", True),
        ("@(@(@(x)))", "@(!(!(@(@(x)))))", False),
        ("builtin:mcp", "builtin:!(x@(y))mcp", False),
        ("builtin:mcp", "builtin:!(x)mcp", True),
        ("builtin:xmcp", "builtin:!(x)mcp", False),
        ("builtin:mcp", "builtin:!(@(x))mcp", False),
        ("@()", "@()", True),
        ("a", "@()", False),
        ("+(a", "+(a", True),
        ("a", "+(a", False),
        ("ab", "a\\|b", False),
        ("b", "a\\|b", False),
        ("x.a", "*\\.a", False),
        ("x\\.a", "*\\.a", True),
        ("ab", "??", True),
        ("a.b", "*.*", True),
        (".a", ".*", True),
        ("a", "[a]", True),
        ("[a]", "[a]", False),
        ("é", "[[:alpha:]]", True),
        ("😀", "?", False),
        ("😀", "??", True),
        ("😀", "[😀]", False),
        ("\ud83d", "[😀]", True),
        ("builtin:mcp", "builtin:[[:space:]-m]cp", True),
        ("builtin:mcp", "builtin:@([z-a]|mcp)", True),
        ("builtin:mcp", "builtin:[z-a]mcp", False),
        ("builtin:😀", "builtin:@([z-a][[:alpha:]]|?)", False),
        ("x.y", "*(?)", True),
        (".x", "*(?)", False),
        ("ab", "+(*)b", True),
        ("aXb", "a!(x)b", True),
        ("ab", "a!(x)b", True),
        ("axb", "a!(x)b", False),
    ],
)
def test_matches_a_segment_as_minimatch(
    segment: str, pattern: str, expected: bool
) -> None:
    assert segment_matcher(pattern)(segment) is expected


@pytest.mark.parametrize(
    ("segment", "pattern", "expected"),
    [
        # Node 26's Unicode 17 tables, whatever Python's `unicodedata` says.
        ("ʕ", "[[:lower:]]", False),
        ("꟎", "[[:alpha:]]", True),
        ("é", "[[:lower:]]", True),
        ("١", "[[:digit:]]", True),
        ("　", "[[:space:]]", True),
    ],
)
def test_posix_classes_use_nodes_unicode_tables(
    segment: str, pattern: str, expected: bool
) -> None:
    assert segment_matcher(pattern)(segment) is expected
