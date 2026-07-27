"""Tests for routing/compat reaching the resolved model request config (M4)."""

from __future__ import annotations

import json

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.models_json import ModelCatalog
from pipy_harness.native.routing import model_request_routing


def _catalog(tmp_path, payload: dict) -> ModelCatalog:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ModelCatalog(models_json_path=path)


def test_openrouter_routing_becomes_provider_param(tmp_path):
    catalog = _catalog(
        tmp_path,
        {
            "providers": {
                "openrouter": {
                    "modelOverrides": {
                        "moonshotai/kimi-k2.6": {
                            "compat": {
                                "openRouterRouting": {
                                    "order": ["fireworks", "together"],
                                    "data_collection": "deny",
                                }
                            }
                        }
                    }
                }
            }
        },
    )
    row = catalog.find("openrouter", "moonshotai/kimi-k2.6")
    assert row is not None
    routing = model_request_routing(row)
    assert routing["provider"]["order"] == ["fireworks", "together"]
    assert routing["provider"]["data_collection"] == "deny"


def test_vercel_routing_becomes_provideroptions_gateway(tmp_path):
    catalog = _catalog(
        tmp_path,
        {
            "providers": {
                "vercel": {
                    "baseUrl": "https://ai-gateway.vercel.sh/v1",
                    "apiKey": "k",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "glm-5.1",
                            "compat": {
                                "vercelGatewayRouting": {
                                    "only": ["zai"],
                                    "order": ["zai", "openai"],
                                }
                            },
                        }
                    ],
                }
            }
        },
    )
    row = catalog.find("vercel", "glm-5.1")
    assert row is not None
    routing = model_request_routing(row)
    assert routing["providerOptions"]["gateway"]["only"] == ["zai"]
    assert routing["providerOptions"]["gateway"]["order"] == ["zai", "openai"]


def test_vercel_empty_routing_omits_provider_options(tmp_path):
    catalog = _catalog(
        tmp_path,
        {
            "providers": {
                "vercel": {
                    "baseUrl": "https://ai-gateway.vercel.sh/v1",
                    "apiKey": "k",
                    "api": "openai-completions",
                    "models": [{"id": "glm", "compat": {"vercelGatewayRouting": {}}}],
                }
            }
        },
    )
    row = catalog.find("vercel", "glm")
    assert row is not None
    # Empty gateway routing -> no stray providerOptions (Pi gates on only/order).
    assert model_request_routing(row) == {}


def test_no_routing_when_base_url_does_not_match(tmp_path):
    # OpenRouter routing only applies when the base URL is openrouter.ai.
    catalog = _catalog(
        tmp_path,
        {
            "providers": {
                "anthropic": {
                    "modelOverrides": {
                        "claude-opus-4-7": {
                            "compat": {"openRouterRouting": {"order": ["x"]}}
                        }
                    }
                }
            }
        },
    )
    row = catalog.find("anthropic", "claude-opus-4-7")
    assert row is not None
    assert model_request_routing(row) == {}


def test_routing_block_survives_merge(tmp_path):
    catalog = _catalog(
        tmp_path,
        {
            "providers": {
                "openrouter": {
                    "compat": {"openRouterRouting": {"sort": "throughput"}},
                    "modelOverrides": {
                        "moonshotai/kimi-k2.6": {
                            "compat": {"openRouterRouting": {"order": ["a"]}}
                        }
                    },
                }
            }
        },
    )
    row = catalog.find("openrouter", "moonshotai/kimi-k2.6")
    assert row is not None
    routing = model_request_routing(row)
    # provider-level sort deep-merged with per-model order
    assert routing["provider"]["sort"] == "throughput"
    assert routing["provider"]["order"] == ["a"]


def test_openrouter_routing_is_copied_from_catalog_compat():
    compat = {"openRouterRouting": {"order": ["a"]}}
    row = NativeModelSpec(
        provider_name="openrouter",
        model_id="model",
        display_name="model",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        compat=compat,
    )

    routing = model_request_routing(row)
    routing["provider"]["order"] = ["changed"]

    assert compat == {"openRouterRouting": {"order": ["a"]}}


def test_routing_rejects_non_mapping_compat_and_omits_falsey_vercel_fields():
    non_mapping = NativeModelSpec(
        provider_name="vercel",
        model_id="model",
        display_name="model",
        api="openai-completions",
        base_url="https://ai-gateway.vercel.sh/v1",
        compat=("not", "a", "mapping"),
    )
    falsey_gateway = NativeModelSpec(
        provider_name="vercel",
        model_id="model",
        display_name="model",
        api="openai-completions",
        base_url="https://ai-gateway.vercel.sh/v1",
        compat={
            "vercelGatewayRouting": {
                "only": [],
                "order": None,
                "unrelated": ["ignored"],
            }
        },
    )

    assert model_request_routing(non_mapping) == {}
    assert model_request_routing(falsey_gateway) == {}
