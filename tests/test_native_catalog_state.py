"""Tests for ProviderCatalogState + --list-models rendering (M8)."""

from __future__ import annotations

import json

from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import (
    ProviderCatalogState,
    format_list_models,
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
    output = format_list_models(state.get_available(), search="zzzznope", load_error=None)
    assert 'No models matching "zzzznope"' in output


def test_format_list_models_load_error_warning(tmp_path):
    state = _state(tmp_path, env={"OPENAI_API_KEY": "k"})
    output = format_list_models(
        state.get_available(), search=None, load_error="boom\n\nFile: /x"
    )
    assert "Warning: errors loading models.json" in output
    assert "boom" in output
