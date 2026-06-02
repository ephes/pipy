"""Focused tests for native REPL provider-state helpers."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import (
    AUTO_DEFAULT_PROVIDER_PRIORITY,
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
    auto_default_selection,
    settings_overlay_lines,
)


class _StubProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True


def test_settings_overlay_lines_renders_active_and_single_static_option():
    lines = settings_overlay_lines(StaticNativeReplProviderState(_StubProvider()))

    assert lines[0] == "pipy native REPL settings:"
    assert lines[1] == "  active: fake/fake-native-bootstrap"
    assert lines[2] == "  registered providers:"
    assert lines[3] == "    fake/fake-native-bootstrap [available]"


def test_settings_overlay_lines_reports_availability_reasons(tmp_path: Path):
    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda selection: cast(ProviderPort, _StubProvider()),
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )

    lines = settings_overlay_lines(state)
    body = "\n".join(lines)

    assert "  active: fake/fake-native-bootstrap" in body
    # Local availability probes surface the same reasons as no-tool /settings.
    assert "openai-codex/gpt-5.5 [unavailable (login-required)]" in body
    assert "openai/gpt-5.5 [unavailable (env-missing)]" in body
    # The shared builder never emits a command-availability footer; callers
    # append their own honest footer for their command surface.
    assert "/login" not in body
    assert "read-only" not in body


def test_auto_default_priority_preserves_hosted_provider_preference(
    tmp_path: Path,
):
    assert AUTO_DEFAULT_PROVIDER_PRIORITY == (
        "openai-codex",
        "openai",
        "anthropic",
        "google",
        "openrouter",
        "mistral",
        "amazon-bedrock",
        "azure-openai",
        "cloudflare",
        "google-vertex",
        "openai-completions",
    )

    assert auto_default_selection(
        env={
            "OPENROUTER_API_KEY": "openrouter-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
        },
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    ) == NativeModelSelection("anthropic", "claude-3-5-sonnet-20241022")
    assert auto_default_selection(
        env={
            "OPENROUTER_API_KEY": "openrouter-key",
            "GEMINI_API_KEY": "gemini-key",
        },
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    ) == NativeModelSelection("google", "gemini-2.0-flash-exp")


def test_catalog_backed_model_options_and_select(tmp_path, monkeypatch):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    state = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda sel: None,
        catalog_state=state,
        persist_defaults=False,
    )

    options = repl_state.model_options()
    # full catalog, not one-per-provider
    assert len([o for o in options if o.selection.provider_name == "openai"]) >= 3
    openai_option = next(o for o in options if o.selection.provider_name == "openai")
    assert openai_option.available is True
    assert openai_option.context_window and openai_option.context_window > 0
    anthropic_option = next(
        o for o in options if o.selection.provider_name == "anthropic"
    )
    assert anthropic_option.available is False  # no ANTHROPIC_API_KEY

    # select with :level on an available provider
    ok, message = repl_state.select_model("openai/gpt-5.5:high")
    assert ok, message
    assert repl_state.selection.reference == "openai/gpt-5.5"
    assert repl_state.thinking_level == "high"

    # selecting an unavailable provider is rejected with a reason
    ok2, message2 = repl_state.select_model("anthropic/claude-opus-4-7")
    assert ok2 is False
    assert "anthropic" in message2


def test_current_provider_catalog_constructs_custom_completions_provider(tmp_path):
    import json as _json

    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.openai_completions_provider import (
        OpenAIChatCompletionsProvider,
    )
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    models_path = tmp_path / "models.json"
    models_path.write_text(
        _json.dumps(
            {
                "providers": {
                    "ds4": {
                        "baseUrl": "http://127.0.0.1:9000/v1",
                        "apiKey": "local-key",
                        "api": "openai-completions",
                        "models": [
                            {"id": "deepseek-v4-flash", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("ds4", "deepseek-v4-flash"),
        provider_factory=lambda sel: (_ for _ in ()).throw(
            AssertionError("legacy factory must not be used for a catalog model")
        ),
        catalog_state=state,
        thinking_level="high",
        persist_defaults=False,
    )
    provider = repl_state.current_provider()
    assert isinstance(provider, OpenAIChatCompletionsProvider)
    assert provider.endpoint == "http://127.0.0.1:9000/v1/chat/completions"
    assert provider.api_key == "local-key"
    assert provider.model_id == "deepseek-v4-flash"
    assert provider.reasoning_effort == "high"
    assert provider.provider_name == "ds4"


def test_current_provider_falls_back_to_legacy_for_non_completions(tmp_path):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    state = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"ANTHROPIC_API_KEY": "k"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    sentinel = object()
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("anthropic", "claude-opus-4-7"),
        provider_factory=lambda sel: sentinel,
        catalog_state=state,
        persist_defaults=False,
    )
    # anthropic is not the openai-completions family -> legacy factory used.
    assert repl_state.current_provider() is sentinel


def _catalog_repl_state(tmp_path, env, *, models_json=None):
    import json as _json

    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    models_path = tmp_path / "models.json"
    if models_json is not None:
        models_path.write_text(_json.dumps(models_json), encoding="utf-8")
    state = ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env=env,
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda sel: None,
        catalog_state=state,
        persist_defaults=False,
    )


_ALL_KEYS = {
    "OPENAI_API_KEY": "k",
    "MISTRAL_API_KEY": "k",
    "OPENROUTER_API_KEY": "k",
    "ANTHROPIC_API_KEY": "k",
}


def test_direct_model_exact_provider_id(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("openai/gpt-5.5")
    assert ok and s.selection.reference == "openai/gpt-5.5", msg


def test_direct_model_bare_id(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("mistral-large-latest")
    assert ok and s.selection.reference == "mistral/mistral-large-latest", msg


def test_direct_model_fuzzy_alias(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("sonnet-4-5")
    assert ok and s.selection.model_id == "claude-sonnet-4-5", msg


def test_direct_model_provider_id_level(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("openai/gpt-5.5:high")
    assert ok and s.selection.reference == "openai/gpt-5.5"
    assert s.thinking_level == "high"


def test_direct_model_colon_in_id(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("openrouter/openai/gpt-4o:extended")
    assert ok and s.selection.model_id == "openai/gpt-4o:extended", msg


def test_direct_model_invalid_suffix_synthesizes_fallback_with_warning(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("openai/gpt-5.5:turbo")
    # strict CLI: invalid suffix -> per-provider fallback synthesis (Pi), warned
    assert ok and s.selection.model_id == "gpt-5.5:turbo", msg
    assert "gpt-5.5:turbo" in msg


def test_direct_model_unavailable_provider_refused(tmp_path):
    s = _catalog_repl_state(tmp_path, {"OPENAI_API_KEY": "k"})  # no anthropic
    prior = s.selection.reference
    ok, msg = s.select_model("anthropic/claude-opus-4-7")
    assert ok is False
    assert "anthropic" in msg
    assert s.selection.reference == prior  # selection unchanged on refusal


def test_direct_model_unknown_errors(tmp_path):
    s = _catalog_repl_state(tmp_path, _ALL_KEYS)
    ok, msg = s.select_model("totally-unknown-xyz")
    assert ok is False and "not found" in msg.lower() or "unknown" in msg.lower()
