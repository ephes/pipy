"""Tests for the pipy-owned built-in provider/model catalog (M1)."""

from __future__ import annotations

from pipy_harness.native.catalog import (
    NativeModelCost,
    NativeModelSpec,
    build_builtin_catalog,
    default_model_per_provider,
)
from pipy_harness.native.provider_registry import (
    DEFAULT_NATIVE_MODELS,
    SUPPORTED_NATIVE_PROVIDERS,
)


# Providers pipy implements with a real adapter (the A-rows). ``fake`` is the
# deterministic bootstrap; ``ds4`` is reframed as a models.json custom provider
# and is intentionally NOT a built-in row.
IMPLEMENTED_PROVIDERS = {
    "anthropic",
    "openai",
    "openai-codex",
    "openai-completions",
    "openrouter",
    "google",
    "google-vertex",
    "mistral",
    "amazon-bedrock",
    "azure-openai",
    "cloudflare",
}


def test_catalog_has_multiple_rows_per_implemented_provider():
    catalog = build_builtin_catalog()
    for provider in IMPLEMENTED_PROVIDERS:
        rows = catalog.models_for(provider)
        assert len(rows) >= 2, f"{provider} should expose multiple catalog rows"


def test_catalog_rows_carry_real_capability_metadata():
    catalog = build_builtin_catalog()
    opus = catalog.find("anthropic", "claude-opus-4-7")
    assert opus is not None
    assert opus.api == "anthropic-messages"
    assert opus.context_window > 0
    assert opus.max_tokens > 0
    assert opus.reasoning is True
    assert "image" in opus.input
    assert isinstance(opus.cost, NativeModelCost)
    assert opus.cost.input > 0
    # xhigh thinking is only available because this model maps it.
    assert opus.thinking_level_map.get("xhigh") is not None


def test_default_model_per_provider_covers_every_implemented_provider():
    for provider in IMPLEMENTED_PROVIDERS:
        assert provider in default_model_per_provider
        default_id = default_model_per_provider[provider]
        assert build_builtin_catalog().find(provider, default_id) is not None


def test_ds4_is_not_a_builtin_catalog_row():
    catalog = build_builtin_catalog()
    assert catalog.models_for("ds4") == []
    assert "ds4" not in default_model_per_provider


def test_derived_supported_providers_and_default_models_stay_consistent():
    # The legacy constants used across cli.py / repl_state.py must keep working
    # and be derived from the catalog (every implemented provider present, each
    # with a default model row in the catalog).
    catalog = build_builtin_catalog()
    for provider in IMPLEMENTED_PROVIDERS:
        assert provider in SUPPORTED_NATIVE_PROVIDERS
        assert provider in DEFAULT_NATIVE_MODELS
        default_id = DEFAULT_NATIVE_MODELS[provider]
        assert catalog.find(provider, default_id) is not None


def test_model_spec_reference_is_provider_slash_id():
    spec = NativeModelSpec(
        provider_name="anthropic",
        model_id="claude-opus-4-7",
        display_name="Claude Opus 4.7",
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
        reasoning=True,
        thinking_level_map={"xhigh": "xhigh"},
        input=("text", "image"),
        cost=NativeModelCost(input=5.0, output=25.0, cache_read=0.5, cache_write=6.25),
        context_window=1_000_000,
        max_tokens=128_000,
    )
    assert spec.reference == "anthropic/claude-opus-4-7"


def test_catalog_data_module_importable_first_without_cycle():
    # Importing catalog_data before catalog must not trigger an import cycle.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import pipy_harness.native.catalog_data"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_get_all_returns_every_row_sorted_stable():
    catalog = build_builtin_catalog()
    all_rows = catalog.get_all()
    assert len(all_rows) == len(set(r.reference for r in all_rows))
    # get_all is deterministic across calls.
    assert [r.reference for r in catalog.get_all()] == [r.reference for r in all_rows]
