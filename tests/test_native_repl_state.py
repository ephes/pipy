"""Focused tests for native REPL provider-state helpers."""

from __future__ import annotations

import ast
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.catalog import (
    NativeModelCost,
    NativeModelSpec,
    build_builtin_catalog,
)
from pipy_harness.native.repl_state import (
    NativeDefaultsStore,
    AUTO_DEFAULT_PROVIDER_PRIORITY,
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderReloadState,
    NativeReplProviderState,
    StaticNativeReplProviderState,
    UnavailableAfterReloadProvider,
    auto_default_selection,
    default_selection_for,
    resolve_cli_selection,
    settings_overlay_lines,
)
from pipy_harness.native.models import ProviderRequest


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


def test_unavailable_after_reload_provider_fails_closed_without_using_sinks(
    tmp_path: Path,
):
    message = (
        "active model disappeared on reload and no available tool-capable "
        "fallback was found"
    )
    provider = UnavailableAfterReloadProvider(
        name="uniqueext",
        model_id="m",
        error_message=message,
    )
    request = ProviderRequest(
        system_prompt="system",
        user_prompt="hello",
        provider_name="uniqueext",
        model_id="m",
        cwd=tmp_path,
    )
    sink_chunks: list[str] = []
    cancel_token = CancelToken()
    cancel_token.cancel()

    before = datetime.now(UTC)
    result = provider.complete(
        request,
        stream_sink=sink_chunks.append,
        reasoning_sink=sink_chunks.append,
        cancel_token=cancel_token,
    )
    after = datetime.now(UTC)

    assert (provider.name, provider.model_id) == ("uniqueext", "m")
    assert provider.supports_tool_calls is True
    assert sink_chunks == []
    assert result.status is HarnessStatus.FAILED
    assert (result.provider_name, result.model_id) == ("uniqueext", "m")
    assert result.error_type == "ProviderUnavailableAfterReload"
    assert result.error_message == message
    assert result.started_at.tzinfo is UTC
    assert before <= result.started_at <= after


def test_logout_persists_shared_bootstrap_default_not_fake_tools(tmp_path):
    """Logout must persist the inert shared default, not the tool-loop fake.

    The persisted/shared selection after a codex logout must stay
    ``fake-native-bootstrap`` so it never leaks the automation fake into shared
    state; the REPL upgrades the *live* selection at its consumption point.
    """

    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeDefaultsStore,
        normalize_repl_fake_selection,
    )

    class _StubAuthManager:
        def logout(self) -> bool:
            return True

    store = NativeDefaultsStore(tmp_path / "defaults.json")
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai-codex", "gpt-5.5"),
        model_runtime=ModelRuntime(catalog=catalog),
        defaults_store=store,
        auth_manager_factory=lambda: _StubAuthManager(),
    )

    ok, _msg = state.logout("openai-codex")

    assert ok
    # Shared/persisted default is the inert bootstrap, NOT fake-tools.
    assert state.selection == NativeModelSelection("fake", "fake-native-bootstrap")
    # Persistence is post-commit: the selection is live immediately, the file
    # is written only when the caller drains the pending default.
    assert store.load() is None
    assert state.flush_pending_default() is None
    assert store.load() == NativeModelSelection("fake", "fake-native-bootstrap")
    # The REPL consumption point still yields a tool-capable selection.
    assert normalize_repl_fake_selection(state.selection) == NativeModelSelection(
        "fake", "fake-tools"
    )


class _StubProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True


def test_repl_reload_publication_preserves_thinking_changed_after_preparation(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog),
        persist_defaults=False,
        thinking_level="low",
    )
    live_selection = state.selection
    live_pending_default = state.pending_default
    replacement = NativeModelSelection("fake", "fake-tools")
    prepared = state.prepare_reload_state(
        selection=replacement,
        pending_default=replacement,
    )
    assert isinstance(prepared, NativeReplProviderReloadState)
    assert [field.name for field in fields(prepared.selection)] == [
        "expected",
        "replacement",
    ]
    assert [field.name for field in fields(prepared.pending_default)] == [
        "expected",
        "replacement",
    ]
    assert prepared.selection.expected is live_selection
    assert prepared.pending_default.expected is live_pending_default
    assert not hasattr(prepared.selection, "thinking_level")
    assert state.reload_state_matches_expected(
        prepared.selection, prepared.pending_default
    )

    state.thinking_level = "high"
    assert state.reload_state_matches_expected(
        prepared.selection, prepared.pending_default
    )
    state.publish_reload_state(prepared.selection, prepared.pending_default)

    assert state.selection == replacement
    assert state.thinking_level == "high"
    assert state.pending_default == replacement


def test_repl_reload_captured_owner_state_detects_later_mutations(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.catalog_state import ProviderCatalogState

    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "before"),
        model_runtime=ModelRuntime(
            ProviderCatalogState(models_json_path=tmp_path / "models.json", env={})
        ),
        persist_defaults=False,
        pending_default=NativeModelSelection("fake", "pending-before"),
    )
    live_selection = state.selection
    live_pending_default = state.pending_default
    prepared = state.prepare_reload_state(
        selection=NativeModelSelection("fake", "after"),
        pending_default=NativeModelSelection("fake", "after"),
    )

    assert prepared.selection.expected is live_selection
    assert prepared.pending_default.expected is live_pending_default
    assert state.reload_state_matches_expected(
        prepared.selection, prepared.pending_default
    )
    state.selection = NativeModelSelection("fake", "intervening")
    assert not state.reload_state_matches_expected(
        prepared.selection, prepared.pending_default
    )
    state.selection = prepared.selection.expected
    state.pending_default = NativeModelSelection("fake", "intervening-default")
    assert not state.reload_state_matches_expected(
        prepared.selection, prepared.pending_default
    )


def test_repl_reload_expected_check_has_no_inner_guard_or_writes() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/repl_state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    checker = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "reload_state_matches_expected"
    )
    assert not any(
        isinstance(
            node,
            (
                ast.With,
                ast.AsyncWith,
                ast.Assign,
                ast.AnnAssign,
                ast.AugAssign,
                ast.NamedExpr,
            ),
        )
        for node in ast.walk(checker)
    )


def test_repl_reload_publisher_ast_has_exact_assignments_and_no_calls() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/repl_state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    publisher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "publish_reload_state"
    )
    assert not any(isinstance(node, ast.Call) for node in ast.walk(publisher))
    assert not any(
        isinstance(node, (ast.With, ast.AsyncWith, ast.AnnAssign, ast.AugAssign))
        for node in ast.walk(publisher)
    )
    assert [type(node) for node in publisher.body] == [
        ast.Expr,
        ast.Assign,
        ast.Assign,
    ]
    assignments = [node for node in publisher.body[1:] if isinstance(node, ast.Assign)]
    assert all(len(node.targets) == 1 for node in assignments)
    assert [ast.unparse(node.targets[0]) for node in assignments] == [
        "self.selection",
        "self.pending_default",
    ]
    assert [ast.unparse(node.value) for node in assignments] == [
        "selection.replacement",
        "pending_default.replacement",
    ]


def test_settings_overlay_lines_renders_active_and_single_static_option():
    lines = settings_overlay_lines(StaticNativeReplProviderState(_StubProvider()))

    assert lines[0] == "pipy native REPL settings:"
    assert lines[1] == "  active: fake/fake-native-bootstrap"
    assert lines[2] == "  registered providers:"
    assert lines[3] == "    fake/fake-native-bootstrap [available]"


def test_settings_overlay_lines_reports_availability_reasons(tmp_path: Path):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )

    lines = settings_overlay_lines(state)
    body = "\n".join(lines)

    assert "  active: fake/fake-native-bootstrap" in body
    # Local availability probes surface the same reasons as no-tool /settings.
    assert "openai-codex/gpt-5.5 [unavailable (login-required)]" in body
    assert "openai/gpt-5.5 [unavailable (auth-missing)]" in body
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
        model_runtime=ModelRuntime(catalog=state),
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
    from pipy_harness.native.providers.openai_completions import (
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
                            {
                                "id": "deepseek-v4-flash",
                                "reasoning": True,
                                "thinkingLevelMap": {"high": "high"},
                            }
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
        model_runtime=ModelRuntime(catalog=state),
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


def test_current_provider_constructs_fake_bootstrap(tmp_path):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.fake import FakeNativeProvider
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
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
    )
    # The deterministic fake bootstrap is built by the construction boundary
    # (build_fake_provider) — there is no separate legacy factory.
    provider = repl_state.current_provider()
    assert isinstance(provider, FakeNativeProvider)
    assert provider.model_id == "fake-native-bootstrap"


def test_current_provider_constructs_anthropic_from_catalog(tmp_path):
    from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
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

    # claude-opus-4-7's catalog row maps only xhigh (thinking_level_map keys
    # override the default reasoning levels).
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("anthropic", "claude-opus-4-7"),
        model_runtime=ModelRuntime(catalog=state),
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
        model_runtime=ModelRuntime(catalog=state),
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
    from pipy_harness.native.providers.openai_completions import (
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
                        {
                            "id": "deepseek-v4-flash",
                            "reasoning": True,
                            "thinkingLevelMap": {"medium": "medium", "high": "high"},
                        }
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
    from pipy_harness.native.providers.openai_completions import (
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


def _codex_repl_state(tmp_path, model_id, thinking_level):
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.provider_construction import ConstructionOptions
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )
    from pipy_harness.native.retry import RetryPolicy

    policy = RetryPolicy(
        max_attempts=7, initial_delay_seconds=1.5, max_delay_seconds=9.0
    )
    state = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("openai-codex", model_id),
        construction_options=ConstructionOptions(retry_policy=policy),
        model_runtime=ModelRuntime(catalog=state),
        thinking_level=thinking_level,
        persist_defaults=False,
    )
    return repl_state, policy


def test_codex_provider_receives_mapped_effort_and_keeps_retry_policy(tmp_path):
    repl_state, policy = _codex_repl_state(tmp_path, "gpt-5.6-sol", "max")
    provider = repl_state.current_provider()
    assert provider.reasoning_effort == "max"
    # the options-threaded retry policy must survive effort injection
    assert provider.retry_policy is policy
    assert provider.supports_tool_search is True


def test_codex_tool_search_compat_is_model_specific_without_thinking(tmp_path):
    supported, _ = _codex_repl_state(tmp_path, "gpt-5.4", None)
    unsupported, _ = _codex_repl_state(tmp_path, "gpt-5.1-codex", None)
    assert supported.current_provider().supports_tool_search is True
    assert unsupported.current_provider().supports_tool_search is False


def test_codex_provider_maps_minimal_to_low(tmp_path):
    repl_state, _ = _codex_repl_state(tmp_path, "gpt-5.6-sol", "minimal")
    assert repl_state.current_provider().reasoning_effort == "low"


def test_codex_provider_clamps_unsupported_max_to_xhigh(tmp_path):
    # gpt-5.5 (Codex) maps xhigh but not max -> clamp down to xhigh (Pi parity).
    repl_state, _ = _codex_repl_state(tmp_path, "gpt-5.5", "max")
    assert repl_state.current_provider().reasoning_effort == "xhigh"


def test_codex_provider_omits_effort_when_off(tmp_path):
    repl_state, _ = _codex_repl_state(tmp_path, "gpt-5.6-sol", "off")
    assert repl_state.current_provider().reasoning_effort is None


def test_codex_provider_omits_effort_when_no_level(tmp_path):
    repl_state, _ = _codex_repl_state(tmp_path, "gpt-5.6-sol", None)
    assert repl_state.current_provider().reasoning_effort is None


def _persisting_state(tmp_path: Path) -> NativeReplProviderState:
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        env={},
        models_json_path=tmp_path / "models.json",
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=catalog),
        defaults_store=NativeDefaultsStore(path=tmp_path / "state" / "defaults.json"),
    )


def test_defaults_persistence_creates_the_file_when_none_exists(
    tmp_path: Path,
) -> None:
    """The first-ever save has no prior file and no prior directory."""

    state = _persisting_state(tmp_path)
    target = tmp_path / "state" / "defaults.json"
    assert not target.parent.exists()

    state._save_default(NativeModelSelection("fake", "fake-tools"))
    assert state.flush_pending_default() is None

    assert target.exists()
    assert state.defaults_store is not None
    assert state.defaults_store.load() == NativeModelSelection("fake", "fake-tools")


def test_defaults_persistence_is_idempotent(tmp_path: Path) -> None:
    """Repeating a flush rewrites the same bytes; a drained queue is a no-op."""

    state = _persisting_state(tmp_path)
    target = tmp_path / "state" / "defaults.json"

    state._save_default(NativeModelSelection("fake", "fake-tools"))
    assert state.flush_pending_default() is None
    first = target.read_bytes()

    # Queue drained: nothing further to do, and nothing changes.
    assert state.flush_pending_default() is None
    assert target.read_bytes() == first

    # Re-queueing the same selection reproduces byte-identical content.
    state._save_default(NativeModelSelection("fake", "fake-tools"))
    assert state.flush_pending_default() is None
    assert target.read_bytes() == first


def test_defaults_persistence_failure_reports_without_claiming_rollback(
    tmp_path: Path,
) -> None:
    """An unwritable location is diagnosed; the live selection is untouched."""

    blocker = tmp_path / "state"
    blocker.write_text("not a directory", encoding="utf-8")
    state = _persisting_state(tmp_path)
    state.selection = NativeModelSelection("fake", "fake-tools")

    state._save_default(NativeModelSelection("fake", "fake-tools"))
    diagnostic = state.flush_pending_default()

    assert diagnostic is not None
    assert "could not be saved as the default" in diagnostic
    assert "this session is unaffected" in diagnostic
    # No claim that the semantic selection rolled back — it did not.
    assert state.selection == NativeModelSelection("fake", "fake-tools")
    assert "rolled back" not in diagnostic


def test_a_concurrent_overwrite_never_leaves_a_torn_defaults_file(
    tmp_path: Path,
) -> None:
    """Atomic replace: a reader sees one whole revision, never a partial one."""

    import threading

    state = _persisting_state(tmp_path)
    target = tmp_path / "state" / "defaults.json"
    state._save_default(NativeModelSelection("fake", "fake-tools"))
    assert state.flush_pending_default() is None

    other = NativeDefaultsStore(path=target)
    stop = threading.Event()
    torn: list[str] = []

    def _write_repeatedly() -> None:
        index = 0
        while not stop.is_set():
            index += 1
            model = "fake-tools" if index % 2 else "fake-native-bootstrap"
            other.save(NativeModelSelection("fake", model))

    worker = threading.Thread(target=_write_repeatedly, daemon=True)
    worker.start()
    try:
        for _ in range(2000):
            loaded = other.load()
            # `load` returns None for an unparseable body, which is exactly
            # what a torn write would produce.
            if loaded is None:
                torn.append("unparseable defaults file")
                break
    finally:
        stop.set()
        worker.join(timeout=5)

    assert torn == [], torn


def test_an_abandoned_selection_does_not_leak_into_a_later_flush(
    tmp_path: Path,
) -> None:
    """A queued default belongs to its own operation, not to the next one."""

    state = _persisting_state(tmp_path)
    assert state.defaults_store is not None

    # An operation queues a default and is then abandoned without flushing.
    state._save_default(NativeModelSelection("fake", "fake-tools"))
    assert state.pending_default == NativeModelSelection("fake", "fake-tools")

    # A later, unrelated selection operation starts from an empty queue, so the
    # abandoned value can never be persisted by that operation's flush.
    state.reset_to_first_available_model()
    assert state.pending_default != NativeModelSelection("fake", "fake-tools")

    state.pending_default = None
    assert state.flush_pending_default() is None
    assert state.defaults_store.load() is None
