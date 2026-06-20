"""Focused tests for native REPL provider-state helpers."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.catalog import (
    NativeModelCost,
    NativeModelSpec,
    build_builtin_catalog,
)
from pipy_harness.native.repl_state import (
    AUTO_DEFAULT_PROVIDER_PRIORITY,
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
    auto_default_selection,
    default_selection_for,
    resolve_cli_selection,
    settings_overlay_lines,
)


def _builtin_rows():
    return build_builtin_catalog().get_all()


def _custom_rows():
    # built-in rows plus a custom models.json-style provider
    rows = list(_builtin_rows())
    rows.append(
        NativeModelSpec(
            provider_name="acme",
            model_id="rocket-1",
            display_name="Acme Rocket 1",
            api="openai-completions",
            base_url="https://acme.example/v1",
            cost=NativeModelCost(),
        )
    )
    return rows


def test_resolve_cli_selection_bare_model_infers_provider():
    # a bare --native-model resolves its provider (not fake/<ref>)
    selection, error = resolve_cli_selection(None, "claude-opus-4-7", _builtin_rows())
    assert error is None
    assert selection == NativeModelSelection("anthropic", "claude-opus-4-7")


def test_resolve_cli_selection_provider_slash_model():
    selection, error = resolve_cli_selection(
        None, "anthropic/claude-sonnet-4-5", _builtin_rows()
    )
    assert error is None
    assert selection == NativeModelSelection("anthropic", "claude-sonnet-4-5")


def test_resolve_cli_selection_custom_models_json_provider():
    selection, error = resolve_cli_selection("acme", "rocket-1", _custom_rows())
    assert error is None
    assert selection == NativeModelSelection("acme", "rocket-1")


def test_resolve_cli_selection_provider_only_uses_default_model():
    selection, error = resolve_cli_selection("anthropic", None, _builtin_rows())
    assert error is None
    assert selection is not None
    assert selection.provider_name == "anthropic"
    # the default model is a real anthropic catalog row
    assert any(
        r.provider_name == "anthropic" and r.model_id == selection.model_id
        for r in _builtin_rows()
    )


def test_resolve_cli_selection_unknown_provider_errors():
    selection, error = resolve_cli_selection("nope", None, _builtin_rows())
    assert selection is None
    assert error is not None
    assert 'Unknown provider "nope"' in error


def test_resolve_cli_selection_neither_flag_returns_none():
    assert resolve_cli_selection(None, None, _builtin_rows()) == (None, None)


def test_default_selection_for_rows_accepts_custom_provider():
    selection = default_selection_for(
        native_provider="acme", native_model="rocket-1", rows=_custom_rows()
    )
    assert selection == NativeModelSelection("acme", "rocket-1")


def test_default_selection_for_rows_unknown_provider_raises():
    import pytest

    with pytest.raises(ValueError, match='Unknown provider "nope"'):
        default_selection_for(
            native_provider="nope", native_model=None, rows=_builtin_rows()
        )


def test_shared_default_selection_fallback_stays_fake_native_bootstrap(tmp_path):
    """The SHARED default resolver keeps the inert ``fake-native-bootstrap``.

    ``default_selection_for`` is shared by non-REPL callers (e.g. one-shot
    ``pipy run``), so its no-provider fallback must NOT be the tool-loop
    automation fake. Only the product REPL upgrades fake -> fake-tools, at its
    own resolution point.
    """

    selection = default_selection_for(
        native_provider=None,
        native_model=None,
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    )

    assert selection == NativeModelSelection("fake", "fake-native-bootstrap")


def test_normalize_repl_fake_selection_upgrades_fake_and_leaves_real():
    """REPL-only normalization upgrades any ``fake`` selection to fake-tools."""

    from pipy_harness.native.repl_state import (
        REPL_FAKE_FALLBACK_SELECTION,
        normalize_repl_fake_selection,
    )

    assert (
        normalize_repl_fake_selection(
            NativeModelSelection("fake", "fake-native-bootstrap")
        )
        == REPL_FAKE_FALLBACK_SELECTION
    )
    assert REPL_FAKE_FALLBACK_SELECTION == NativeModelSelection("fake", "fake-tools")
    # Real providers are returned unchanged (so genuinely tool-incapable real
    # providers still error at the session gate rather than being rewritten).
    real = NativeModelSelection("openai", "gpt-5.5")
    assert normalize_repl_fake_selection(real) == real


def test_logout_persists_shared_bootstrap_default_not_fake_tools(tmp_path):
    """Logout must persist the inert shared default, not the tool-loop fake.

    The persisted/shared selection after a codex logout must stay
    ``fake-native-bootstrap`` so it never leaks the automation fake into shared
    state; the REPL upgrades the *live* selection at its consumption point.
    """

    from pipy_harness.native.repl_state import (
        NativeDefaultsStore,
        normalize_repl_fake_selection,
    )

    class _StubAuthManager:
        def logout(self) -> bool:
            return True

    store = NativeDefaultsStore(tmp_path / "defaults.json")
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai-codex", "gpt-5.5"),
        provider_factory=lambda sel: _StubProvider(),
        defaults_store=store,
        auth_manager_factory=lambda: _StubAuthManager(),
    )

    ok, _msg = state.logout("openai-codex")

    assert ok
    # Shared/persisted default is the inert bootstrap, NOT fake-tools.
    assert state.selection == NativeModelSelection("fake", "fake-native-bootstrap")
    assert store.load() == NativeModelSelection("fake", "fake-native-bootstrap")
    # The REPL consumption point still yields a tool-capable selection.
    assert normalize_repl_fake_selection(state.selection) == NativeModelSelection(
        "fake", "fake-tools"
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


def test_current_provider_falls_back_to_legacy_for_unwired_family(tmp_path):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    state = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    sentinel = object()
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda sel: sentinel,
        catalog_state=state,
        persist_defaults=False,
    )
    # the deterministic fake bootstrap is not catalog-constructed -> legacy factory.
    assert repl_state.current_provider() is sentinel


def test_current_provider_constructs_anthropic_from_catalog(tmp_path):
    from pipy_harness.native.anthropic_provider import AnthropicProvider
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

    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    # claude-opus-4-7's catalog row maps only xhigh (thinking_level_map keys
    # override the default reasoning levels).
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("anthropic", "claude-opus-4-7"),
        provider_factory=_no_legacy,
        catalog_state=state,
        thinking_level="xhigh",
        persist_defaults=False,
    )
    provider = repl_state.current_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.endpoint == "https://api.anthropic.com/v1/messages"
    assert provider.api_key == "k"
    assert provider.reasoning_effort == "xhigh"
    # api_key is repr-hidden so a stray log of the adapter never leaks it
    assert "api_key" not in repr(provider)


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


def test_product_path_thinking_level_reaches_constructed_adapter(tmp_path):
    from pipy_harness.native.openai_completions_provider import (
        OpenAIChatCompletionsProvider,
    )

    s = _catalog_repl_state(
        tmp_path,
        {},
        models_json={
            "providers": {
                "ds4": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "local",
                    "api": "openai-completions",
                    "models": [
                        {"id": "deepseek-v4-flash", "reasoning": True,
                         "thinkingLevelMap": {"medium": "medium", "high": "high"}}
                    ],
                }
            }
        },
    )
    # Direct /model with :level sets the active thinking level...
    ok, msg = s.select_model("ds4/deepseek-v4-flash:medium")
    assert ok, msg
    assert s.thinking_level == "medium"
    # ...and the product construction boundary maps it into the adapter request.
    provider = s.current_provider()
    assert isinstance(provider, OpenAIChatCompletionsProvider)
    assert provider.reasoning_effort == "medium"
    assert provider.endpoint == "http://127.0.0.1:8000/v1/chat/completions"
    assert provider.api_key == "local"


def test_fallback_selection_constructs_from_catalog_base(tmp_path):
    # A synthesized fallback model (known provider, uncataloged id) must still
    # construct from the provider's catalog base, not the legacy factory.
    from pipy_harness.native.openai_completions_provider import (
        OpenAIChatCompletionsProvider,
    )
    from pipy_harness.native.repl_state import NativeModelSelection

    s = _catalog_repl_state(
        tmp_path,
        {},
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
    # Select an uncataloged id on the known provider (fallback synthesis).
    s.selection = NativeModelSelection("acme", "rocket-NEW")
    provider = s.current_provider()
    assert isinstance(provider, OpenAIChatCompletionsProvider)
    assert provider.endpoint == "https://acme.example/v1/chat/completions"
    assert provider.api_key == "acme-key"
    assert provider.model_id == "rocket-NEW"
