"""Tests for scoped-model cycling helpers (`pipy_harness.native.scoped_models`).

`enabledModels` holds model patterns that constrain the Ctrl+P cycle set; when
empty the cycle uses the full available catalog. Patterns match a model
reference (`provider/model`) exactly or as an fnmatch glob (e.g. `openai/*`).
"""

from __future__ import annotations

from pipy_harness.native.scoped_models import (
    filter_scoped_references,
    next_reference,
)

REFS = ["openai/gpt-5.5", "openai/gpt-4", "anthropic/claude", "google/gemini"]


def test_no_patterns_keeps_all_references() -> None:
    assert filter_scoped_references(REFS, []) == REFS


def test_exact_pattern_match() -> None:
    assert filter_scoped_references(REFS, ["anthropic/claude"]) == ["anthropic/claude"]


def test_glob_pattern_match_preserves_order() -> None:
    assert filter_scoped_references(REFS, ["openai/*"]) == [
        "openai/gpt-5.5",
        "openai/gpt-4",
    ]


def test_multiple_patterns_union_in_reference_order() -> None:
    out = filter_scoped_references(REFS, ["google/*", "anthropic/claude"])
    assert out == ["anthropic/claude", "google/gemini"]


def test_pattern_with_no_match_yields_empty() -> None:
    assert filter_scoped_references(REFS, ["nope/*"]) == []


def test_next_reference_forward_wraps() -> None:
    assert next_reference(REFS, "openai/gpt-5.5", forward=True) == "openai/gpt-4"
    assert next_reference(REFS, "google/gemini", forward=True) == "openai/gpt-5.5"


def test_next_reference_backward_wraps() -> None:
    assert next_reference(REFS, "openai/gpt-5.5", forward=False) == "google/gemini"
    assert next_reference(REFS, "openai/gpt-4", forward=False) == "openai/gpt-5.5"


def test_next_reference_current_not_in_set_starts_at_edge() -> None:
    assert next_reference(REFS, "other/x", forward=True) == "openai/gpt-5.5"
    assert next_reference(REFS, "other/x", forward=False) == "google/gemini"


def test_next_reference_single_entry_returns_itself() -> None:
    assert next_reference(["only/one"], "only/one", forward=True) == "only/one"


def test_next_reference_empty_returns_none() -> None:
    assert next_reference([], "x", forward=True) is None
