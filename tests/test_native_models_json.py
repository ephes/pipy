"""Tests for the pipy models.json loader/merge/validate (M3)."""

from __future__ import annotations

import json

from pipy_harness.native.models_json import (
    ModelCatalog,
    default_models_json_path,
    strip_json_comments,
)


def _write(path, payload: dict | str) -> None:
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")


# ---- comment / trailing-comma stripping ------------------------------------


def test_strip_json_comments_removes_line_comments_and_trailing_commas():
    raw = """
    {
      // leading comment
      "providers": {
        "ds4": {
          "baseUrl": "http://x", // inline comment
          "apiKey": "local",
        },
      }
    }
    """
    parsed = json.loads(strip_json_comments(raw))
    assert parsed["providers"]["ds4"]["baseUrl"] == "http://x"


def test_strip_json_comments_leaves_string_literals_untouched():
    raw = '{"providers": {"x": {"baseUrl": "http://h//path", "apiKey": "a,b"}}}'
    parsed = json.loads(strip_json_comments(raw))
    assert parsed["providers"]["x"]["baseUrl"] == "http://h//path"
    assert parsed["providers"]["x"]["apiKey"] == "a,b"


# ---- config root resolution ------------------------------------------------


def test_default_models_json_path_prefers_pipy_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path))
    assert default_models_json_path() == tmp_path / "models.json"


def test_default_models_json_path_falls_back_to_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("PIPY_CONFIG_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_models_json_path() == tmp_path / "pipy" / "models.json"


# ---- merge behavior --------------------------------------------------------


def test_custom_provider_with_custom_models_appends(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "ds4": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "local",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "deepseek-v4-flash",
                            "reasoning": True,
                            "contextWindow": 131072,
                        }
                    ],
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("ds4", "deepseek-v4-flash")
    assert row is not None
    assert row.api == "openai-completions"
    assert row.base_url == "http://127.0.0.1:8000/v1"
    assert row.reasoning is True
    assert row.context_window == 131072
    # default maxTokens for custom local models
    assert row.max_tokens == 16384


def test_custom_model_wins_on_provider_id_conflict(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [
                        {"id": "claude-opus-4-7", "name": "OVERRIDDEN OPUS"}
                    ]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None
    assert row.display_name == "OVERRIDDEN OPUS"


def test_per_model_override_deep_merges_cost_and_thinking(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "modelOverrides": {
                        "claude-opus-4-7": {
                            "cost": {"input": 99.0},
                            "thinkingLevelMap": {"low": "low"},
                        }
                    }
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None
    # cost.input overridden, others fall back to built-in
    assert row.cost.input == 99.0
    assert row.cost.output == 25.0
    # thinking map deep-merged: built-in xhigh preserved + new low added
    assert row.thinking_level_map.get("xhigh") == "xhigh"
    assert row.thinking_level_map.get("low") == "low"


def test_per_model_override_explicit_zero_cost_wins(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "modelOverrides": {
                        "claude-opus-4-7": {"cost": {"input": 0}}
                    }
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None
    # explicit 0 must override the built-in 5.0 (Pi uses ?? nullish, not truthy)
    assert row.cost.input == 0.0
    # untouched field falls back to built-in
    assert row.cost.output == 25.0


def test_provider_level_baseurl_override_applies_to_builtins(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {"providers": {"anthropic": {"baseUrl": "https://proxy.example/v1"}}},
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None and row.base_url == "https://proxy.example/v1"


# ---- graceful degradation --------------------------------------------------


def test_malformed_json_keeps_builtins_and_reports_path(tmp_path):
    path = tmp_path / "models.json"
    _write(path, "{ this is not json")
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert str(path) in catalog.error
    # built-ins survive
    assert catalog.find("anthropic", "claude-opus-4-7") is not None


def test_missing_file_is_not_an_error(tmp_path):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")
    assert catalog.error is None
    assert catalog.find("anthropic", "claude-opus-4-7") is not None


# ---- validation rules ------------------------------------------------------


def test_override_only_provider_with_no_usable_fields_rejected(tmp_path):
    path = tmp_path / "models.json"
    _write(path, {"providers": {"anthropic": {"name": "x"}}})
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "anthropic" in catalog.error
    # built-ins preserved on validation failure
    assert catalog.find("anthropic", "claude-opus-4-7") is not None


def test_non_builtin_provider_with_models_requires_baseurl_and_apikey(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {"providers": {"custom": {"api": "openai-completions", "models": [{"id": "m"}]}}},
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "baseUrl" in catalog.error


def test_builtin_provider_may_define_custom_model_without_baseurl(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {"providers": {"anthropic": {"models": [{"id": "claude-experimental"}]}}},
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-experimental")
    assert row is not None
    # api/baseUrl inherited from built-in anthropic defaults
    assert row.api == "anthropic-messages"
    assert row.base_url == "https://api.anthropic.com"


def test_invalid_context_window_rejected(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {"models": [{"id": "m", "contextWindow": -5}]}
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "contextWindow" in catalog.error


def test_input_values_restricted_to_text_and_image(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [{"id": "m", "input": ["text", "video"]}]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "input" in catalog.error


def test_custom_model_cost_requires_all_four_fields(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [{"id": "m", "cost": {"input": 1.0}}]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "cost" in catalog.error


def test_float_context_window_accepted(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {"models": [{"id": "m", "contextWindow": 200000.0}]}
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "m")
    assert row is not None and row.context_window == 200000


def test_schema_error_uses_dot_paths(tmp_path):
    path = tmp_path / "models.json"
    _write(path, {"providers": {"anthropic": {"baseUrl": 123}}})
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "providers.anthropic.baseUrl" in catalog.error


def test_every_builtin_provider_row_has_base_url():
    from pipy_harness.native.catalog import build_builtin_catalog

    for row in build_builtin_catalog().get_all():
        if row.provider_name == "fake":
            continue
        assert row.base_url, f"{row.reference} missing base_url"


def test_refresh_picks_up_edits(tmp_path):
    path = tmp_path / "models.json"
    _write(path, {"providers": {"anthropic": {"models": [{"id": "m1"}]}}})
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.find("anthropic", "m1") is not None
    assert catalog.find("anthropic", "m2") is None
    _write(path, {"providers": {"anthropic": {"models": [{"id": "m2"}]}}})
    catalog.refresh()
    assert catalog.find("anthropic", "m2") is not None
    assert catalog.find("anthropic", "m1") is None
