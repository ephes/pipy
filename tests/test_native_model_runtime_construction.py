"""Characterization tests for the ``ModelRuntime`` construction/spec owner.

Slice 5.3a extracts catalog spec resolution and catalog-driven provider
construction out of ``NativeReplProviderState`` into ``ModelRuntime``. These
tests pin the three construction shapes the runtime now owns — a catalog-wired
API family, the legacy-factory fallback with Codex option injection, and the
plain legacy-factory passthrough — plus ``resolve_spec`` and ``thinking_levels``,
so the extraction is proven byte-for-byte behavior preserving at the new seam.

The equivalent behavior is also pinned through the stable public surface
(``NativeReplProviderState.current_provider``/``provider_for``) in
``test_native_repl_state.py``; this module exercises the extracted owner
directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.openai_codex_provider import OpenAICodexResponsesProvider
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.openai_completions import (
    OpenAIChatCompletionsProvider,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.repl_state import ModelRuntime, NativeModelSelection
from pipy_harness.native.retry import RetryPolicy

# Inline lambdas (contextually typed against ``NativeProviderFactory``) are used
# for the ``provider_factory`` argument throughout; a catalog-wired selection
# must never reach it.
_NO_LEGACY = "legacy factory must not be used for a catalog model"


def _catalog(
    tmp_path: Path,
    *,
    env: dict[str, str] | None = None,
    models_json: dict | None = None,
) -> ProviderCatalogState:
    models_path = tmp_path / "models.json"
    if models_json is not None:
        models_path.write_text(json.dumps(models_json), encoding="utf-8")
    return ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env=env or {},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )


def test_resolve_spec_finds_catalog_row(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path, env={"OPENAI_API_KEY": "sk"}))
    spec = runtime.resolve_spec(NativeModelSelection("openai", "gpt-5.5"))
    assert spec is not None
    assert spec.provider_name == "openai"
    assert spec.model_id == "gpt-5.5"


def test_resolve_spec_synthesizes_fallback_for_uncataloged_id(tmp_path: Path) -> None:
    # A not-yet-cataloged model id on a known custom provider clones that
    # provider's catalog base (baseUrl/auth) into a fallback row.
    catalog = _catalog(
        tmp_path,
        models_json={
            "providers": {
                "acme": {
                    "baseUrl": "https://acme.example/v1",
                    "apiKey": "acme-key",
                    "api": "openai-completions",
                    "models": [{"id": "rocket-1"}],
                }
            }
        },
    )
    runtime = ModelRuntime(catalog=catalog)
    spec = runtime.resolve_spec(NativeModelSelection("acme", "rocket-NEW"))
    assert spec is not None
    assert spec.provider_name == "acme"
    assert spec.model_id == "rocket-NEW"
    assert spec.base_url == "https://acme.example/v1"


def test_construct_catalog_wired_completions_family(tmp_path: Path) -> None:
    catalog = _catalog(
        tmp_path,
        models_json={
            "providers": {
                "ds4": {
                    "baseUrl": "http://127.0.0.1:9000/v1",
                    "apiKey": "local-key",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "deepseek-v4-flash",
                            "reasoning": True,
                            "thinkingLevelMap": {"high": "high"},
                        }
                    ],
                }
            }
        },
    )
    runtime = ModelRuntime(catalog=catalog)
    provider = runtime.construct(
        NativeModelSelection("ds4", "deepseek-v4-flash"),
        thinking_level="high",
        provider_factory=lambda selection: (_ for _ in ()).throw(
            AssertionError(_NO_LEGACY)
        ),
    )
    assert isinstance(provider, OpenAIChatCompletionsProvider)
    assert provider.endpoint == "http://127.0.0.1:9000/v1/chat/completions"
    assert provider.api_key == "local-key"
    assert provider.model_id == "deepseek-v4-flash"
    assert provider.reasoning_effort == "high"
    assert provider.provider_name == "ds4"


def test_construct_catalog_wired_anthropic_maps_thinking(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, env={"ANTHROPIC_API_KEY": "k"})
    runtime = ModelRuntime(catalog=catalog)
    # claude-opus-4-7 maps only xhigh in its catalog row.
    provider = runtime.construct(
        NativeModelSelection("anthropic", "claude-opus-4-7"),
        thinking_level="xhigh",
        provider_factory=lambda selection: (_ for _ in ()).throw(
            AssertionError(_NO_LEGACY)
        ),
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.reasoning_effort == "xhigh"


def test_construct_fake_falls_through_to_legacy_factory(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path))
    sentinel = FakeNativeProvider(supports_tool_calls=True)
    provider = runtime.construct(
        NativeModelSelection("fake", "fake-native-bootstrap"),
        thinking_level=None,
        provider_factory=lambda selection: sentinel,
    )
    # The deterministic fake bootstrap is not catalog-constructed.
    assert provider is sentinel


def test_construct_codex_injects_options_and_keeps_retry(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path))
    policy = RetryPolicy(
        max_attempts=7, initial_delay_seconds=1.5, max_delay_seconds=9.0
    )
    provider = runtime.construct(
        NativeModelSelection("openai-codex", "gpt-5.6-sol"),
        thinking_level="max",
        provider_factory=lambda sel: OpenAICodexResponsesProvider(
            model_id=sel.model_id, retry_policy=policy
        ),
    )
    assert isinstance(provider, OpenAICodexResponsesProvider)
    # Codex catalog options are injected onto the legacy-built provider ...
    assert provider.reasoning_effort == "max"
    assert provider.supports_tool_search is True
    # ... while the legacy-factory retry policy survives the option injection.
    assert provider.retry_policy is policy


def test_construct_codex_tool_search_is_model_specific(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path))
    supported = runtime.construct(
        NativeModelSelection("openai-codex", "gpt-5.4"),
        thinking_level=None,
        provider_factory=lambda selection: OpenAICodexResponsesProvider(
            model_id=selection.model_id
        ),
    )
    unsupported = runtime.construct(
        NativeModelSelection("openai-codex", "gpt-5.1-codex"),
        thinking_level=None,
        provider_factory=lambda selection: OpenAICodexResponsesProvider(
            model_id=selection.model_id
        ),
    )
    assert isinstance(supported, OpenAICodexResponsesProvider)
    assert isinstance(unsupported, OpenAICodexResponsesProvider)
    assert supported.supports_tool_search is True
    assert unsupported.supports_tool_search is False


def test_thinking_levels_reflect_catalog_row(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path, env={"OPENAI_API_KEY": "sk"}))
    # gpt-5.5 maps xhigh -> the cycle includes it (Pi's getSupportedThinkingLevels).
    levels = runtime.thinking_levels(NativeModelSelection("openai", "gpt-5.5"))
    assert levels == ["off", "minimal", "low", "medium", "high", "xhigh"]


def test_thinking_levels_fallback_when_spec_absent(tmp_path: Path) -> None:
    runtime = ModelRuntime(catalog=_catalog(tmp_path))
    # An unknown provider has no spec and no fallback base -> the ordinary tier.
    levels = runtime.thinking_levels(NativeModelSelection("nope", "whatever"))
    assert levels == ["off", "minimal", "low", "medium", "high"]
