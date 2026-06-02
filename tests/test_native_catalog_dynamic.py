"""Tests for catalog refresh + dynamic provider registration (M12)."""

from __future__ import annotations

from dataclasses import replace

from pipy_harness.native.models_json import (
    ModelCatalog,
    ModelDefinition,
    ProviderConfig,
)


def test_register_provider_adds_models_and_refreshes(tmp_path):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")
    assert catalog.find("acme", "rocket-1") is None
    catalog.register_provider(
        "acme",
        ProviderConfig(
            base_url="https://acme.example/v1",
            api_key="k",
            api="openai-completions",
            models=(ModelDefinition(id="rocket-1"),),
        ),
    )
    row = catalog.find("acme", "rocket-1")
    assert row is not None and row.base_url == "https://acme.example/v1"


def test_register_provider_override_only_changes_builtin_baseurl(tmp_path):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")
    catalog.register_provider(
        "anthropic", ProviderConfig(base_url="https://proxy.example/v1")
    )
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None and row.base_url == "https://proxy.example/v1"


def test_unregister_provider_reverts(tmp_path):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")
    catalog.register_provider(
        "anthropic", ProviderConfig(base_url="https://proxy.example/v1")
    )
    catalog.unregister_provider("anthropic")
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None and row.base_url == "https://api.anthropic.com"


def test_oauth_modifier_rewrites_rows_after_merge(tmp_path):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")

    def copilot_proxy_rewrite(rows):
        # Simulate GitHub Copilot's modifyModels base-URL rewrite from proxy-ep.
        return [
            replace(r, base_url="https://copilot-proxy.example")
            if r.provider_name == "anthropic"
            else r
            for r in rows
        ]

    catalog.set_oauth_modifiers([copilot_proxy_rewrite])
    catalog.refresh()
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None and row.base_url == "https://copilot-proxy.example"


def test_refresh_picks_up_models_json_edit(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        '{"providers": {"anthropic": {"models": [{"id": "v1"}]}}}', encoding="utf-8"
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.find("anthropic", "v1") is not None
    path.write_text(
        '{"providers": {"anthropic": {"models": [{"id": "v2"}]}}}', encoding="utf-8"
    )
    catalog.refresh()
    assert catalog.find("anthropic", "v2") is not None
    assert catalog.find("anthropic", "v1") is None
