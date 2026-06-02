"""Tests for the ds4 models.json reframe + env shim (M11)."""

from __future__ import annotations

import json

from pipy_harness.native.catalog import build_builtin_catalog, default_model_per_provider
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.ds4 import (
    DS4_DEFAULT_BASE_URL,
    ds4_preset_dict,
    synthesize_ds4_provider_config,
)
from pipy_harness.native.models_json import ModelCatalog


def test_ds4_absent_from_builtin_catalog_and_defaults():
    catalog = build_builtin_catalog()
    assert catalog.models_for("ds4") == []
    assert "ds4" not in default_model_per_provider


def test_ds4_preset_is_valid_models_json(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(ds4_preset_dict()), encoding="utf-8")
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("ds4", "deepseek-v4-flash")
    assert row is not None
    assert row.api == "openai-completions"
    assert row.base_url == DS4_DEFAULT_BASE_URL
    assert row.reasoning is True


def test_env_shim_synthesizes_provider_config():
    config = synthesize_ds4_provider_config(
        {"PIPY_DS4_BASE_URL": "http://localhost:9000/v1"}
    )
    assert config is not None
    assert config.base_url == "http://localhost:9000/v1"
    assert config.api == "openai-completions"
    assert config.api_key  # placeholder so validateConfig passes
    assert config.models and config.models[0].id == "deepseek-v4-flash"


def test_env_shim_absent_without_env():
    assert synthesize_ds4_provider_config({}) is None


def test_env_shim_and_preset_produce_equivalent_rows(tmp_path):
    # Preset file path
    preset_path = tmp_path / "models.json"
    preset_path.write_text(json.dumps(ds4_preset_dict()), encoding="utf-8")
    preset_state = ProviderCatalogState(
        models_json_path=preset_path, env={}
    )
    preset_row = preset_state.find("ds4", "deepseek-v4-flash")

    # Env shim path (no models.json file)
    empty_path = tmp_path / "empty.json"
    shim_state = ProviderCatalogState(
        models_json_path=empty_path,
        env={"PIPY_DS4_BASE_URL": DS4_DEFAULT_BASE_URL},
    )
    shim_row = shim_state.find("ds4", "deepseek-v4-flash")

    assert preset_row is not None and shim_row is not None
    assert preset_row.api == shim_row.api
    assert preset_row.base_url == shim_row.base_url
    assert preset_row.reasoning == shim_row.reasoning


def test_env_shim_makes_ds4_available(tmp_path):
    state = ProviderCatalogState(
        models_json_path=tmp_path / "empty.json",
        env={"PIPY_DS4_BASE_URL": DS4_DEFAULT_BASE_URL},
    )
    assert state.provider_available("ds4")
    assert any(r.reference == "ds4/deepseek-v4-flash" for r in state.get_available())
