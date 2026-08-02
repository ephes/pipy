"""Tests for the pipy models.json loader/merge/validate (M3)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields, replace
from types import MappingProxyType

import pytest

from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec
from pipy_harness.native.models_json import (
    ModelCatalog,
    ModelDefinition,
    ModelOverride,
    ModelsConfig,
    ProviderConfig,
    ProviderRequestConfig,
    default_models_json_path,
    strip_json_comments,
)


def _write(path, payload: dict | str) -> None:
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")


def _assert_catalog_field_identity_pairs(
    catalog: ModelCatalog,
    field_names: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    assert all(
        getattr(catalog, name) is value
        for name, value in zip(field_names, values, strict=True)
    )


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
                    "models": [{"id": "claude-opus-4-7", "name": "OVERRIDDEN OPUS"}]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None
    assert row.display_name == "OVERRIDDEN OPUS"


def test_custom_replacement_keeps_position_and_appends_in_config_order(tmp_path):
    baseline = ModelCatalog(models_json_path=tmp_path / "absent.json").get_all()
    replaced_reference = "anthropic/claude-opus-4-7"
    baseline_index = [row.reference for row in baseline].index(replaced_reference)
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [
                        {"id": "claude-opus-4-7", "name": "replacement"},
                        {"id": "new-anthropic"},
                    ]
                },
                "custom": {
                    "baseUrl": "https://custom.example/v1",
                    "apiKey": "key",
                    "api": "openai-completions",
                    "models": [{"id": "new-custom"}],
                },
            }
        },
    )

    catalog = ModelCatalog(models_json_path=path)
    references = [row.reference for row in catalog.get_all()]

    assert catalog.error is None
    assert references.index(replaced_reference) == baseline_index
    assert references[-2:] == ["anthropic/new-anthropic", "custom/new-custom"]


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
                    "modelOverrides": {"claude-opus-4-7": {"cost": {"input": 0}}}
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
        {
            "providers": {
                "custom": {"api": "openai-completions", "models": [{"id": "m"}]}
            }
        },
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
        {"providers": {"anthropic": {"models": [{"id": "m", "contextWindow": -5}]}}},
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "contextWindow" in catalog.error


def test_model_semantics_preserve_first_failure_order(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [{"id": "m", "contextWindow": -1, "maxTokens": -1}]
                }
            }
        },
    )

    catalog = ModelCatalog(models_json_path=path)

    assert catalog.error is not None
    assert "model m: invalid contextWindow" in catalog.error
    assert "maxTokens" not in catalog.error


def test_thinking_level_map_rejects_non_string_values(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "modelOverrides": {
                        "claude-opus-4-7": {"thinkingLevelMap": {"high": True}}
                    }
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "thinkingLevelMap" in catalog.error


def test_thinking_level_map_rejects_unknown_level_key(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [{"id": "m", "thinkingLevelMap": {"turbo": "turbo"}}]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is not None
    assert "thinkingLevelMap" in catalog.error


def test_thinking_level_map_accepts_string_and_null(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "models": [
                        {"id": "m", "thinkingLevelMap": {"high": "high", "off": None}}
                    ]
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    assert catalog.error is None
    row = catalog.find("anthropic", "m")
    assert row is not None
    assert row.thinking_level_map == {"high": "high", "off": None}


def test_input_values_restricted_to_text_and_image(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {"models": [{"id": "m", "input": ["text", "video"]}]}
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
        {"providers": {"anthropic": {"models": [{"id": "m", "cost": {"input": 1.0}}]}}},
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


def test_boolean_context_window_is_rejected_as_non_number(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {"providers": {"anthropic": {"models": [{"id": "m", "contextWindow": True}]}}},
    )

    catalog = ModelCatalog(models_json_path=path)

    assert catalog.error is not None
    assert (
        "providers.anthropic.models.0.contextWindow: expected number" in catalog.error
    )


def test_headers_require_string_values(tmp_path):
    path = tmp_path / "models.json"
    _write(path, {"providers": {"anthropic": {"headers": {"x-test": 1}}}})

    catalog = ModelCatalog(models_json_path=path)

    assert catalog.error is not None
    assert (
        "providers.anthropic.headers: expected object of string values" in catalog.error
    )


def test_present_empty_headers_and_compat_are_usable_provider_fields(tmp_path):
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {"headers": {}},
                "openai": {"compat": {}},
            }
        },
    )

    catalog = ModelCatalog(models_json_path=path)

    assert catalog.error is None


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


def test_live_refresh_reset_failure_semantics_and_non_tautological_request(tmp_path):
    path = tmp_path / "models.json"
    _write(path, {"providers": {"anthropic": {"headers": {"x-live": "yes"}}}})
    catalog = ModelCatalog(models_json_path=path)
    expected_request = dict(catalog.provider_request_configs)
    old_rows = catalog.rows

    def fail(_rows):
        raise RuntimeError("live modifier")

    catalog.set_oauth_modifiers([fail])
    with pytest.raises(RuntimeError, match="live modifier"):
        catalog.refresh()
    assert catalog.rows is old_rows
    assert catalog.provider_request_configs == expected_request
    assert isinstance(catalog.provider_request_configs, dict)
    assert catalog.error is None and catalog._config is not None


@pytest.mark.parametrize("shorter_side", ["field_names", "values"])
def test_catalog_field_identity_assertion_rejects_unequal_lengths(
    tmp_path, shorter_side
):
    catalog = ModelCatalog(models_json_path=tmp_path / "absent.json")
    field_names = tuple(field.name for field in fields(ModelCatalog))
    values = tuple(getattr(catalog, name) for name in field_names)
    if shorter_side == "field_names":
        field_names = field_names[:-1]
    else:
        values = values[:-1]

    with pytest.raises(ValueError):
        _assert_catalog_field_identity_pairs(catalog, field_names, values)


def test_prepared_refresh_covers_all_inputs_is_pure_and_reentrant(tmp_path):
    nested = [{"value": 1}]
    extra = {
        "extra": ProviderConfig(
            api="openai-completions",
            base_url="https://extra.invalid",
            compat={"nested": nested},
            models=(ModelDefinition(id="extra-custom"),),
        )
    }
    paths = (tmp_path / "live.json", tmp_path / "detached.json")
    file_config = {
        "providers": {
            "anthropic": {
                "models": [{"id": "file-custom"}],
                "modelOverrides": {"claude-opus-4-7": {"name": "file-override"}},
            }
        }
    }
    for path in paths:
        _write(path, file_config)
    live, detached = (
        ModelCatalog(models_json_path=path, extra_providers=extra) for path in paths
    )
    registered = ProviderConfig(
        api="openai-completions",
        base_url="https://registered.invalid",
        models=(ModelDefinition(id="registered-custom"),),
    )

    def modifier(rows):
        return [replace(row, display_name=f"oauth:{row.display_name}") for row in rows]

    for catalog in (live, detached):
        catalog.register_provider("registered", registered)
        catalog.set_oauth_modifiers([modifier])
    live.refresh()
    field_names = tuple(field.name for field in fields(ModelCatalog))
    before = tuple(getattr(detached, name) for name in field_names)
    prepared = detached.prepare_catalog_reload()
    _assert_catalog_field_identity_pairs(detached, field_names, before)
    assert detached.validate_prepared_catalog_reload(prepared)
    prepared_ids = {row.model_id for row in prepared.rows}
    assert {"file-custom", "extra-custom", "registered-custom"} <= prepared_ids
    assert (
        next(
            row for row in prepared.rows if row.model_id == "claude-opus-4-7"
        ).display_name
        == "oauth:file-override"
    )
    assert isinstance(
        next(row for row in prepared.rows if row.model_id == "extra-custom").compat,
        MappingProxyType,
    )
    replacement_token = prepared.replacement_owner_token
    detached.publish_catalog_reload(prepared)
    assert (detached.rows, detached.provider_request_configs) == (
        live.rows,
        live.provider_request_configs,
    )
    assert prepared.rows == prepared.replacement_rows == ()
    assert detached._reload_identity is replacement_token

    def mutate_owner(rows):
        detached.set_oauth_modifiers([])
        nested[0].update(value=2)
        return rows

    detached.set_oauth_modifiers([mutate_owner])
    captured = detached.capture_catalog_reload_expected()
    assert set(captured) == set(
        "owner_token extra_providers registered_providers oauth_modifiers".split()
    )
    reentrant = detached.prepare_catalog_reload_from_snapshot(captured)
    assert not detached.catalog_reload_matches_expected(reentrant)
    assert captured["extra_providers"]["extra"].compat == {"nested": [{"value": 1}]}
    nested_proxy = MappingProxyType({"items": [MappingProxyType({"value": 1})]})
    detached.register_provider(
        "frozen",
        ProviderConfig(headers=MappingProxyType({"x": "yes"}), compat=nested_proxy),
    )
    captured = detached.capture_catalog_reload_expected()
    frozen = captured["registered_providers"]["frozen"]
    assert type(frozen.headers) is dict and type(frozen.compat["items"][0]) is dict
    assert detached.validate_prepared_catalog_reload(
        detached.prepare_catalog_reload_from_snapshot(captured)
    )


def test_model_cost_and_refresh_fields_are_complete_and_immutable(tmp_path):
    definition = ModelDefinition(id="m", cost=NativeModelCost(input=1))
    spec = NativeModelSpec("p", "m", "m", "api", cost=NativeModelCost(output=2))
    assert type(definition.cost) is type(spec.cost) is NativeModelCost
    with pytest.raises(FrozenInstanceError):
        setattr(definition.cost, "input", 3)
    path = tmp_path / "models.json"
    _write(
        path,
        {
            "providers": {
                "anthropic": {
                    "apiKey": "private-api-key",
                    "headers": {"x-private-header": "private-header-value"},
                    "modelOverrides": {"claude-opus-4-7": {"cost": {"input": 3}}},
                }
            }
        },
    )
    catalog = ModelCatalog(models_json_path=path)
    prepared = catalog.prepare_catalog_reload()
    rendered = repr(prepared)
    assert "private-api-key" not in rendered
    assert "x-private-header" not in rendered
    assert "private-header-value" not in rendered
    # fmt: off
    expected_fields = (
        (ModelDefinition, "id name api base_url reasoning thinking_level_map input cost context_window max_tokens headers compat"),
        (ModelOverride, "name reasoning thinking_level_map input cost context_window max_tokens headers compat"),
        (ProviderConfig, "name base_url api_key api headers auth_header compat models model_overrides"),
        (ProviderRequestConfig, "api_key headers auth_header"),
        (ModelsConfig, "providers"),
        (NativeModelSpec, "provider_name model_id display_name api base_url reasoning thinking_level_map input cost context_window max_tokens headers compat"),
        (prepared.__class__, "expected_owner_token replacement_owner_token rows error provider_request_configs config replacement_rows replacement_provider_request_configs replacement_config"),
    )
    # fmt: on
    for value_type, names in expected_fields:
        assert {field.name for field in fields(value_type)} == set(names.split())
    override = prepared.config.providers["anthropic"].model_overrides["claude-opus-4-7"]
    assert isinstance(override.cost, MappingProxyType)
    catalog.publish_catalog_reload(prepared)
    assert prepared.rows == prepared.replacement_rows == ()
    assert prepared.provider_request_configs == {}
    assert prepared.replacement_provider_request_configs == {}
    assert prepared.config is prepared.replacement_config is None
    assert prepared.error is prepared.replacement_owner_token is None
    names = ("rows", "error", "provider_request_configs", "_config")
    live = tuple(getattr(catalog, name) for name in names)
    catalog.publish_catalog_reload(prepared)
    assert tuple(getattr(catalog, name) for name in names) == live
    assert "private" not in repr(prepared)
