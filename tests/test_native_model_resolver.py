"""Tests for the pipy model-pattern matcher (M2).

Mirrors Pi's model-resolver.ts semantics over pipy ``NativeModelSpec`` rows.
"""

from __future__ import annotations

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.model_resolver import (
    find_exact_model_reference,
    is_alias,
    is_valid_thinking_level,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
)


def _row(provider: str, model_id: str, name: str | None = None) -> NativeModelSpec:
    return NativeModelSpec(
        provider_name=provider,
        model_id=model_id,
        display_name=name or model_id,
        api="openai-completions",
    )


# A deterministic fixture catalog exercising the tricky cases.
ROWS = [
    _row("anthropic", "claude-opus-4-7", "Claude Opus 4.7"),
    _row("anthropic", "claude-sonnet-4-5", "Claude Sonnet 4.5"),
    _row("anthropic", "claude-sonnet-4-5-20250929", "Claude Sonnet 4.5 (dated)"),
    _row("openai", "gpt-5.5", "GPT-5.5"),
    _row("openai", "gpt-4o", "GPT-4o"),
    _row("openrouter", "openai/gpt-4o:extended", "OpenRouter GPT-4o extended"),
    _row("openrouter", "moonshotai/kimi-k2.6", "Kimi K2.6"),
    _row("google", "gpt-4o", "Google clash id"),  # bare-id clash with openai
]


# ---- thinking-level helpers ------------------------------------------------


def test_is_valid_thinking_level_accepts_six_value_set():
    for level in ("off", "minimal", "low", "medium", "high", "xhigh"):
        assert is_valid_thinking_level(level)
    assert not is_valid_thinking_level("turbo")
    assert not is_valid_thinking_level("")


def test_is_alias_treats_dated_suffix_as_non_alias():
    assert is_alias("claude-sonnet-4-5")
    assert is_alias("claude-3-5-sonnet-latest")
    assert not is_alias("claude-sonnet-4-5-20250929")


# ---- exact reference matching ----------------------------------------------


def test_exact_canonical_provider_slash_id_match():
    row = find_exact_model_reference("anthropic/claude-opus-4-7", ROWS)
    assert row is not None and row.reference == "anthropic/claude-opus-4-7"


def test_exact_match_is_case_insensitive():
    row = find_exact_model_reference("ANTHROPIC/Claude-Opus-4-7", ROWS)
    assert row is not None and row.model_id == "claude-opus-4-7"


def test_bare_id_unique_match():
    row = find_exact_model_reference("gpt-5.5", ROWS)
    assert row is not None and row.provider_name == "openai"


def test_bare_id_ambiguous_across_providers_is_rejected():
    # "gpt-4o" exists on both openai and google → ambiguous bare id → None.
    assert find_exact_model_reference("gpt-4o", ROWS) is None


# ---- parse_model_pattern ----------------------------------------------------


def test_parse_attaches_explicit_thinking_level():
    result = parse_model_pattern("openai/gpt-5.5:high", ROWS)
    assert result.model is not None and result.model.model_id == "gpt-5.5"
    assert result.thinking_level == "high"
    assert result.warning is None


def test_parse_colon_in_id_matches_before_level_split():
    # openrouter id literally contains ":extended"; it must match as a model via
    # the fuzzy tryMatchModel step before any colon split.
    result = parse_model_pattern("openai/gpt-4o:extended", ROWS)
    assert result.model is not None
    assert result.model.model_id == "openai/gpt-4o:extended"
    assert result.thinking_level is None


def test_parse_invalid_suffix_scope_mode_warns_and_uses_default_level():
    result = parse_model_pattern("openai/gpt-5.5:turbo", ROWS)
    assert result.model is not None and result.model.model_id == "gpt-5.5"
    assert result.thinking_level is None
    assert result.warning is not None and "turbo" in result.warning


def test_parse_invalid_suffix_strict_mode_returns_no_model():
    result = parse_model_pattern(
        "openai/gpt-5.5:turbo", ROWS, allow_invalid_thinking_level_fallback=False
    )
    assert result.model is None


def test_parse_fuzzy_prefers_alias_over_dated_version():
    result = parse_model_pattern("sonnet-4-5", ROWS)
    assert result.model is not None
    assert result.model.model_id == "claude-sonnet-4-5"


# ---- glob scoping -----------------------------------------------------------


def test_resolve_model_scope_glob_over_provider_and_bare_id():
    scope = resolve_model_scope(["anthropic/claude-*"], ROWS)
    ids = {sm.model.model_id for sm in scope.models}
    assert ids == {"claude-opus-4-7", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929"}


def test_resolve_model_scope_glob_with_level_suffix():
    scope = resolve_model_scope(["openai/gpt-*:medium"], ROWS)
    assert all(sm.thinking_level == "medium" for sm in scope.models)
    # Matches openai rows via "provider/id" and the openrouter row via its bare
    # id "openai/gpt-4o:extended" (same as Pi's minimatch bare-id pass).
    assert {sm.model.model_id for sm in scope.models} == {
        "gpt-5.5",
        "gpt-4o",
        "openai/gpt-4o:extended",
    }


def test_resolve_model_scope_glob_star_does_not_cross_slash():
    # minimatch semantics: "*" does not cross "/". Every openrouter id in the
    # fixture contains a slash, so "openrouter/*" matches nothing (Pi behavior).
    scope = resolve_model_scope(["openrouter/*"], ROWS)
    assert scope.models == []
    assert any("openrouter/*" in w for w in scope.warnings)


def test_resolve_model_scope_glob_matches_within_one_segment():
    scope = resolve_model_scope(["openrouter/openai/*"], ROWS)
    ids = {sm.model.model_id for sm in scope.models}
    assert ids == {"openai/gpt-4o:extended"}


def test_fuzzy_alias_tiebreak_is_case_insensitive_like_localecompare():
    # Pi's b.id.localeCompare(a.id) orders case-insensitively, so "Model-B"
    # sorts above "model-a"; a raw codepoint sort would wrongly pick "model-a".
    rows = [_row("x", "model-a"), _row("x", "Model-B")]
    result = parse_model_pattern("model", rows)
    assert result.model is not None and result.model.model_id == "Model-B"


def test_resolve_model_scope_unmatched_pattern_warns_and_skips():
    scope = resolve_model_scope(["does-not-exist"], ROWS)
    assert scope.models == []
    assert any("does-not-exist" in w for w in scope.warnings)


def test_resolve_model_scope_dedupes_across_patterns():
    scope = resolve_model_scope(["anthropic/claude-opus-4-7", "*opus*"], ROWS)
    refs = [sm.model.reference for sm in scope.models]
    assert refs.count("anthropic/claude-opus-4-7") == 1


# ---- resolve_cli_model ------------------------------------------------------


def test_resolve_cli_model_with_provider_and_model():
    result = resolve_cli_model(cli_provider="openai", cli_model="gpt-5.5", rows=ROWS)
    assert result.error is None
    assert result.model is not None and result.model.reference == "openai/gpt-5.5"


def test_resolve_cli_model_infers_provider_from_slash_prefix():
    result = resolve_cli_model(
        cli_provider=None, cli_model="anthropic/claude-opus-4-7", rows=ROWS
    )
    assert result.error is None
    assert result.model is not None and result.model.provider_name == "anthropic"


def test_resolve_cli_model_synthesizes_per_provider_fallback_with_warning():
    result = resolve_cli_model(
        cli_provider="anthropic", cli_model="claude-future-9", rows=ROWS
    )
    assert result.error is None
    assert result.model is not None
    assert result.model.provider_name == "anthropic"
    assert result.model.model_id == "claude-future-9"
    assert result.warning is not None and "claude-future-9" in result.warning


def test_resolve_cli_model_unknown_provider_errors():
    result = resolve_cli_model(
        cli_provider="nope", cli_model="whatever", rows=ROWS
    )
    assert result.model is None
    assert result.error is not None and "nope" in result.error


def test_resolve_cli_model_no_provider_no_match_errors():
    result = resolve_cli_model(
        cli_provider=None, cli_model="totally-unknown-id", rows=ROWS
    )
    assert result.model is None
    assert result.error is not None


def test_resolve_cli_model_slash_id_falls_back_to_full_string_match():
    # "openai/gpt-4o:extended" — "openai" looks like a provider but the full
    # string is an openrouter model id; after no match within openai, fall back
    # to the full-string id match across all rows.
    result = resolve_cli_model(
        cli_provider=None, cli_model="openrouter/openai/gpt-4o:extended", rows=ROWS
    )
    assert result.error is None
    assert result.model is not None
    assert result.model.model_id == "openai/gpt-4o:extended"
