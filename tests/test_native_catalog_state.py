"""Tests for ProviderCatalogState + --list-models rendering (M8)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import (
    ProviderCatalogReloadState,
    ProviderCatalogState,
    format_list_models,
)
from pipy_harness.native.extension_types import (
    ExtensionOAuthConfig,
    ExtensionProvider,
    RegisteredProvider,
)


def _state(tmp_path, *, env=None, models_json: dict | None = None, codex=False):
    auth_path = tmp_path / "auth.json"
    codex_path = tmp_path / "openai-codex.json"
    if codex:
        codex_path.write_text("{}", encoding="utf-8")
    models_path = tmp_path / "models.json"
    if models_json is not None:
        models_path.write_text(json.dumps(models_json), encoding="utf-8")
    return ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=auth_path),
        env=env or {},
        openai_codex_auth_path=codex_path,
    )


def test_get_available_filters_by_env_key(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k"})
    available = {r.provider_name for r in state.get_available()}
    assert "openai" in available
    assert "anthropic" not in available  # no ANTHROPIC_API_KEY


def test_fake_is_always_available(tmp_path):
    state = _state(tmp_path)
    assert any(r.provider_name == "fake" for r in state.get_available())


def test_openai_codex_available_when_logged_in(tmp_path):
    state = _state(tmp_path, codex=True)
    assert any(r.provider_name == "openai-codex" for r in state.get_available())


def test_custom_models_json_provider_available_via_apikey(tmp_path):
    state = _state(
        tmp_path,
        models_json={
            "providers": {
                "ds4": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "local",
                    "api": "openai-completions",
                    "models": [{"id": "deepseek-v4-flash"}],
                }
            }
        },
    )
    available = {r.reference for r in state.get_available()}
    assert "ds4/deepseek-v4-flash" in available


def test_stored_api_key_makes_provider_available(tmp_path):
    state = _state(tmp_path)
    assert not state.provider_available("anthropic")
    state.auth_store.set("anthropic", {"type": "api_key", "key": "sk"})
    assert state.provider_available("anthropic")


def test_stored_copilot_oauth_rewrites_base_url_via_modify_models(tmp_path):
    # A github-copilot custom provider + stored OAuth cred → the OAuth
    # modify_models hook rewrites the row's base URL from the token's proxy-ep.
    auth_path = tmp_path / "auth.json"
    store = AuthStore(path=auth_path)
    store.set(
        "github-copilot",
        {
            "type": "oauth",
            "access": "tid=x;proxy-ep=proxy.example.com;",
            "refresh": "r",
            "expires": 9999999999000,
        },
    )
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "github-copilot": {
                        "baseUrl": "https://old.example",
                        "apiKey": "x",
                        "api": "openai-completions",
                        "models": [{"id": "gpt-5.4"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = ProviderCatalogState(
        models_json_path=models_path,
        auth_store=store,
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    row = state.find("github-copilot", "gpt-5.4")
    assert row is not None and row.base_url == "https://api.example.com"


def test_detached_provider_overlay_has_no_container_alias_and_publishes_only_assignments(
    tmp_path,
):
    state = _state(tmp_path)
    calls: list[str] = []
    registered = RegisteredProvider(
        ExtensionProvider(
            "detached", "m", ("m",), lambda _context: calls.append("factory")
        ),
        "test.py",
    )
    providers = [registered]
    unregistered = ["hidden"]

    prepared = state.prepare_extension_provider_contributions(  # type: ignore[arg-type]
        providers,
        unregistered,  # exercise defensive copies from mutable callers
    )
    providers.clear()
    unregistered.append("detached")

    assert isinstance(prepared, ProviderCatalogReloadState)
    assert prepared.providers == (registered,)
    assert prepared.unregistered == ("hidden",)
    with pytest.raises(TypeError):
        prepared.provider_map["other"] = registered  # type: ignore[index]
    prior = state.extension_providers
    state.publish_extension_provider_contributions(prepared)
    assert prior == ()
    assert state.extension_providers is prepared.providers
    assert state.extension_provider_for("detached") is registered
    assert calls == []


def test_detached_publish_matches_live_extension_provider_overlay_rebuild(
    tmp_path,
) -> None:
    calls: list[str] = []

    def registered(name: str, *, oauth: bool = False) -> RegisteredProvider:
        oauth_config = None
        if oauth:
            oauth_config = ExtensionOAuthConfig(
                name=f"{name} OAuth",
                login=lambda *args: calls.append(f"{name}:login"),
                refresh_token=lambda *args: calls.append(f"{name}:refresh"),
                get_api_key=lambda *args: calls.append(f"{name}:key"),
            )
        return RegisteredProvider(
            ExtensionProvider(
                name,
                "m",
                ("m",),
                lambda _context: calls.append(f"{name}:factory"),
                oauth_config,
            ),
            f"{name}.py",
        )

    hidden = registered("Hidden", oauth=True)
    duplicate_first = registered("Duplicate")
    duplicate_later = registered("duplicate", oauth=True)
    oauth = registered("OAuth", oauth=True)
    plain = registered("Plain")
    providers = (hidden, duplicate_first, duplicate_later, oauth, plain)
    unregistered = ("hIDdEn",)

    live = _state(tmp_path / "live")
    live.set_extension_provider_contributions(providers, unregistered)
    detached = _state(tmp_path / "detached")
    prepared = detached.prepare_extension_provider_contributions(
        providers, unregistered
    )
    detached.publish_extension_provider_contributions(prepared)

    live_maps = (
        live._extension_provider_map,
        live.extension_oauth_provider_map,
    )
    detached_maps = (
        detached._extension_provider_map,
        detached.extension_oauth_provider_map,
    )
    assert all(
        type(value) is MappingProxyType for value in (*live_maps, *detached_maps)
    )
    assert tuple(map(dict, live_maps)) == tuple(map(dict, detached_maps))
    assert live.extension_providers == detached.extension_providers == providers
    assert live.extension_unregistered_providers == unregistered
    assert detached.extension_unregistered_providers == unregistered
    assert live.extension_provider_for("HIDDEN") is None
    assert live.extension_provider_for("duplicate") is duplicate_first
    assert live.extension_oauth_provider_for("duplicate") is None
    assert live.extension_oauth_provider_for("OAUTH") is oauth
    assert live.extension_oauth_provider_for("plain") is None
    assert calls == []


def test_overlay_publisher_has_exact_assignments_and_no_calls() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/catalog_state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    publisher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "publish_extension_provider_contributions"
    )
    assignments = [node for node in publisher.body if isinstance(node, ast.Assign)]
    assert [ast.unparse(target) for node in assignments for target in node.targets] == [
        "self.extension_providers",
        "self.extension_unregistered_providers",
        "self._extension_provider_map",
        "self.extension_oauth_provider_map",
    ]
    assert all(isinstance(node, (ast.Expr, ast.Assign)) for node in publisher.body)
    assert not any(isinstance(node, ast.Call) for node in ast.walk(publisher))


# ---- list-models rendering --------------------------------------------------


def test_format_list_models_columns_and_sorting(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k", "MISTRAL_API_KEY": "k"})
    rows = state.get_available()
    output = format_list_models(rows, search=None, load_error=None)
    lines = output.splitlines()
    assert lines[0].split() == [
        "provider",
        "model",
        "context",
        "max-out",
        "thinking",
        "images",
    ]
    # providers sorted: mistral before openai
    body = [line for line in lines[1:] if line.strip()]
    providers_in_order = [line.split()[0] for line in body]
    assert providers_in_order == sorted(providers_in_order)
    # token formatting present (e.g. 400K or 1M)
    assert any("K" in line or "M" in line for line in body)


def test_format_list_models_fuzzy_filter(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k", "MISTRAL_API_KEY": "k"})
    rows = state.get_available()
    output = format_list_models(rows, search="mistral", load_error=None)
    body = [line for line in output.splitlines()[1:] if line.strip()]
    assert body and all(line.split()[0] == "mistral" for line in body)


def test_format_list_models_no_models_guidance():
    output = format_list_models([], search=None, load_error=None)
    assert "No models available" in output


def test_format_list_models_no_match_message(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k"})
    output = format_list_models(
        state.get_available(), search="zzzznope", load_error=None
    )
    assert 'No models matching "zzzznope"' in output


def test_format_list_models_load_error_warning(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k"})
    output = format_list_models(
        state.get_available(), search=None, load_error="boom\n\nFile: /x"
    )
    assert "Warning: errors loading models.json" in output
    assert "boom" in output
