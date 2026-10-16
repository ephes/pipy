"""Tests for resource enable/disable patterns (`pipy_harness.native.resource_enablement`).

Mirrors Pi's `pi config` model: enablement is persisted by writing `-pattern`
(disable) and `+pattern` (re-enable) entries into the relevant settings array
(`skills`/`prompts`/`themes`/`extensions`) rather than removing discovered
paths. Default is enabled; the last matching directive wins. Bare entries
(resource source paths) are additive sources and do not affect enablement.
"""

from __future__ import annotations

import time

from pipy_harness.native.resource_enablement import (
    disable_entry,
    enable_entry,
    filter_enabled,
    is_resource_enabled,
)


def test_enabled_by_default_when_no_patterns() -> None:
    assert is_resource_enabled("review", []) is True


def test_disabled_by_minus_pattern() -> None:
    assert is_resource_enabled("review", ["-review"]) is False


def test_reenabled_by_later_plus_pattern() -> None:
    # Last matching directive wins: -review then +review -> enabled.
    assert is_resource_enabled("review", ["-review", "+review"]) is True


def test_redisabled_by_later_minus_pattern() -> None:
    assert is_resource_enabled("review", ["+review", "-review"]) is False


def test_glob_disable() -> None:
    assert is_resource_enabled("draft-pr", ["-draft-*"]) is False
    assert is_resource_enabled("review", ["-draft-*"]) is True


def test_patterns_match_as_minimatch() -> None:
    # Goal check r5 U1: braces and extended globs, as Pi's minimatch.
    assert is_resource_enabled("draft-pr", ["-{draft,wip}-*"]) is False
    assert is_resource_enabled("wip-1", ["-{draft,wip}-*"]) is False
    assert is_resource_enabled("review", ["-{draft,wip}-*"]) is True
    assert is_resource_enabled("draft-pr", ["-+(draft|wip)-*"]) is False
    assert is_resource_enabled("draft-pr", ["-draft-!(pr)"]) is True
    assert is_resource_enabled("draft-x", ["-draft-!(pr)"]) is False
    # A `*` does not cross a `/`, and an exact name always matches itself.
    assert is_resource_enabled("team/draft", ["-*"]) is True
    assert is_resource_enabled("a[b", ["-a[b"]) is False


def test_globstar_also_matches_no_directory() -> None:
    # Goal check r4 U4: as in Pi's minimatch, `**/` matches zero or more
    # directories, at the start of a pattern and after a `/`.
    assert is_resource_enabled("draft-pr", ["-**/draft-*"]) is False
    assert is_resource_enabled("team/draft-pr", ["-**/draft-*"]) is False
    assert is_resource_enabled("team/draft-pr", ["-team/**/draft-*"]) is False
    assert is_resource_enabled("team/a/b/draft-pr", ["-team/**/draft-*"]) is False
    assert is_resource_enabled("x/team/draft", ["-**/team/**/draft"]) is False
    assert is_resource_enabled("review", ["-**/draft-*"]) is True
    # Only a whole `**` segment is a globstar.
    assert is_resource_enabled("draft-pr", ["-x**/draft-*"]) is True
    # Many globstars do not multiply the work.
    many = "/".join(f"**/part{index}" for index in range(18)) + "/**/nope"
    started = time.monotonic()
    assert is_resource_enabled("a/" * 100 + "nope", [f"-{many}"]) is True
    assert is_resource_enabled("builtin:mcp", [f"-{many}"]) is True
    assert time.monotonic() - started < 5
    # Nor do they nest: far more globstars than the recursion limit.
    assert is_resource_enabled("other", ["-" + "**/" * 2000 + "other"]) is False
    assert is_resource_enabled("mcp", ["-" + "**/" * 2000 + "other"]) is True


def test_bare_source_entry_is_ignored_for_enablement() -> None:
    # A bare path/source entry is additive, not an enable/disable directive.
    assert is_resource_enabled("review", ["/some/path/skills", "-review"]) is False
    assert is_resource_enabled("review", ["/some/path/skills"]) is True


def test_filter_enabled_keeps_enabled_names_in_order() -> None:
    names = ["a", "b", "c"]
    assert filter_enabled(names, ["-b"]) == ["a", "c"]
    assert filter_enabled(names, []) == ["a", "b", "c"]


def test_disable_entry_appends_minus_and_drops_redundant_plus() -> None:
    # Disabling appends "-name" and removes any existing "+name" directive.
    assert disable_entry(["+review"], "review") == ["-review"]
    assert disable_entry([], "review") == ["-review"]
    # Idempotent: disabling an already-disabled entry does not duplicate.
    assert disable_entry(["-review"], "review") == ["-review"]


def test_enable_entry_appends_plus_and_drops_redundant_minus() -> None:
    assert enable_entry(["-review"], "review") == ["+review"]
    # Enabling a default-enabled resource still records an explicit +entry so
    # the state is unambiguous, but does not duplicate.
    assert enable_entry(["+review"], "review") == ["+review"]


def test_disable_preserves_unrelated_entries() -> None:
    assert disable_entry(["/src/skills", "-other"], "review") == [
        "/src/skills",
        "-other",
        "-review",
    ]
