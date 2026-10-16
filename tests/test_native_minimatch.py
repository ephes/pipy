"""Tests for `pipy_harness.native.minimatch`, Pi's glob matching.

The expected results are what `minimatch(path, pattern)` of minimatch
10.2.6 (the version Pi's coding agent depends on) returns with its default
options; each case was checked against it with Node.
"""

from __future__ import annotations

import time

import pytest

from pipy_harness.native.minimatch import minimatch


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        # Braces are expanded first.
        ("builtin:mcp", "builtin:{mcp,codemode}", True),
        ("builtin:tool-search", "builtin:{mcp,codemode}", False),
        ("x2", "x{1..3}", True),
        ("b", "{a..c}", True),
        ("02", "{01..03}", True),
        ("{a}", "{a}", True),
        ("a", "{a}", False),
        ("{a,b}", "\\{a,b}", True),
        ("a", "\\{a,b}", False),
        # Extended globs and character classes.
        ("builtin:mcp", "builtin:+(mcp|codemode)", True),
        ("builtin:codemode", "builtin:@(mcp|code*)", True),
        ("builtin:mcp", "builtin:!(mcp)", False),
        ("builtin:codemode", "builtin:!(mcp)", True),
        ("builtin:mcp", "builtin:?(x)mcp", True),
        ("builtin:mcp", "builtin:*(m|c|p)", True),
        ("builtin:mcp", "builtin:[a-m]cp", True),
        ("builtin:mcp", "builtin:[[:alpha:]]cp", True),
        ("Builtin:MCP", "builtin:mcp", False),
        ("builtin:mcp", "builtin:{mcp,${codemode,tool-search}}", True),
        # `**(` is a star and a `*(` group.
        ("builtin:mcp", "builtin:**(mcp|codemode)", True),
        ("builtin:mcpx", "builtin:**(mcp)x", True),
        ("ab", "**(a)b", True),
        # POSIX classes are Unicode properties; without one, a segment is
        # matched in UTF-16 code units.
        ("émcp", "[[:alpha:]]mcp", True),
        ("1mcp", "[[:alpha:]]mcp", False),
        ("\u0661", "[[:digit:]]", True),
        ("éa", "[![:alpha:]]a", False),
        ("\U0001f600", "?", False),
        ("\U0001f600", "??", True),
        ("a\U0001f600", "a?", False),
        ("\U0001f600", "[\U0001f600]?", True),
        # A set is read item by item: a `-` after a class is a character; a
        # range that ends in a class, or a set without items, never matches.
        ("builtin:mcp", "builtin:[[:space:]-m]cp", True),
        ("-", "[x[:alpha:]-]", True),
        ("a", "[a-[:alpha:]]", False),
        ("builtin:mcp", "builtin:[z-a]mcp", False),
        # ... but only in its own extended-glob alternative.
        ("builtin:mcp", "builtin:@([z-a]|mcp)", True),
        ("a", "+([z-a]|a)", True),
        ("b", "?([z-a])b", True),
        ("ab", "!([z-a])b", True),
        ("x", "@([a-[:alpha:]]|x)", True),
        # minimatch's own handling of nested extended globs.
        ("builtin:mcp", "builtin:!([z-a]@(x))mcp", False),
        ("builtin:\U0001f600", "builtin:@([z-a][[:alpha:]]|?)", False),
        ("x", "@(@(@(@(x))))", True),
        ("[]", "[]", True),
        ("]", "[]]", True),
        # A long sequence bound is a JavaScript number.
        ("builtin:mcp", "builtin:{" + "0" * 4301 + "..1}", False),
        # A leading `!` run negates; `!(` at the start is a negation too.
        ("builtin:mcp", "!builtin:mcp", False),
        ("builtin:codemode", "!builtin:mcp", True),
        ("builtin:mcp", "!!builtin:mcp", True),
        ("builtin:mcp", "!(builtin:mcp)", True),
        # Comments and empty patterns.
        ("#a", "#a", False),
        ("", "", True),
        ("a", "", False),
        # `*` stays in its segment; `**` takes zero or more segments.
        ("a/b", "*", False),
        ("a/b", "*/b", True),
        ("a/b", "**", True),
        ("builtin:mcp", "**/builtin:mcp", True),
        ("a/b/builtin:mcp", "**/builtin:mcp", True),
        ("a/b", "a/**", True),
        ("a", "a/**", False),
        ("a/", "a/**", True),
        ("a/b/", "a/*", True),
        ("a/b/", "a/**/*", True),
        ("b", "a/../b", True),
        ("/b", "/a/../b", True),
        ("/../b", "/../b", True),
        ("/b", "/../b", False),
        ("../b", "a/../../b", True),
        ("../builtin:mcp", "../*", True),
        ("../builtin:mcp", "**/builtin:mcp", False),
        # No wildcard matches a leading dot, and `**` passes no dot segment.
        (".a", "*", False),
        (".a", "*.a", False),
        (".a", ".*", True),
        ("..", ".*", False),
        ("a/.b/builtin:mcp", "**/builtin:mcp", False),
        # A lone trailing backslash stands for itself.
        ("a\\", "a\\", True),
        ("a", "a\\", False),
        # minimatch's own results: the segments after the last section are
        # checked for dots up to the end, and the last start of a section is
        # minimatch's bound, which is too small when an earlier section is
        # longer than a later one.
        ("x/a/.b", "**/a/**/.b", False),
        ("a/b/c/d/e/f/g", "**/a/b/c/d/e/**/f/**/g", False),
        ("x/a/b/c/g", "x/**/a/b/**/c/**/g", False),
        ("x/a/b/c/g", "x/**/a/**/b/**/c/**/g", True),
    ],
)
def test_matches_as_minimatch(path: str, pattern: str, expected: bool) -> None:
    assert minimatch(path, pattern) is expected


def test_at_most_200_sections_between_globstars_match() -> None:
    # minimatch's maxGlobstarRecursion.
    path = "a/" * 300 + "b"
    assert minimatch(path, "**/a/" * 200 + "**/b") is True
    assert minimatch(path, "**/a/" * 201 + "**/b") is False


def test_braces_match_their_first_expansions() -> None:
    # brace-expansion keeps the first 100 000 expansions.
    assert minimatch("a" * 40, "{a,b}" * 40) is True
    assert minimatch("b" * 40, "{a,b}" * 40) is False
    assert minimatch("b" * 40, "!" + "{a,b}" * 40) is True


def test_braces_nested_beyond_the_recursion_limit_match_nothing() -> None:
    pattern = "builtin:" + "{" * 500 + "mcp,codemode" + "}" * 500
    assert minimatch("builtin:mcp", pattern) is False


def test_globstars_do_not_multiply_the_work() -> None:
    started = time.monotonic()
    many = "/".join(f"**/part{index}" for index in range(18)) + "/**/nope"
    assert minimatch("a/" * 100 + "nope", many) is False
    assert minimatch("a/" * 30 + "b", "**/a/" * 15 + "**/c") is False
    assert minimatch("a/" * 300 + "b", "**/a/" * 150 + "**/b") is True
    # Adjacent globstars are one, so there is nothing to nest.
    assert minimatch("other", "**/" * 2000 + "other") is True
    assert minimatch("mcp", "**/" * 2000 + "other") is False
    assert time.monotonic() - started < 5
