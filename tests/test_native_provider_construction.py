"""Tests for catalog-driven provider construction reaching real requests.

Covers spec item 18: a custom models.json provider/model runs a real (fake-HTTP)
turn whose request uses the catalog baseUrl, model id, resolved auth, merged
headers, routing, and mapped thinking.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.native import ProviderRequest
from pipy_harness.native.openai_completions_provider import (
    JsonResponse,
    OpenAIChatCompletionsProvider,
)


class CapturingHTTPClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        self.requests.append({"url": url, "headers": dict(headers), "body": dict(body)})
        return JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="ds4",
        model_id="deepseek-v4-flash",
        cwd=tmp_path,
    )


# ---- Slice A: the completions adapter carries catalog request config -------


def test_adapter_uses_catalog_endpoint_model_auth(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="deepseek-v4-flash",
        api_key="catalog-key",
        http_client=http,
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        provider_name="ds4",
    )
    result = provider.complete(_request(tmp_path))
    assert result.final_text == "OK"
    sent = http.requests[-1]
    assert sent["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert sent["body"]["model"] == "deepseek-v4-flash"
    assert sent["headers"]["Authorization"] == "Bearer catalog-key"


def test_adapter_merges_extra_headers(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="k",
        http_client=http,
        endpoint="http://h/v1/chat/completions",
        extra_headers={"X-Org": "org-123", "X-Beta": "on"},
    )
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["headers"]["X-Org"] == "org-123"
    assert sent["headers"]["X-Beta"] == "on"
    # api_key still wins the Authorization header
    assert sent["headers"]["Authorization"] == "Bearer k"


def test_adapter_merges_routing_into_body(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="k",
        http_client=http,
        endpoint="http://h/v1/chat/completions",
        extra_body={"provider": {"order": ["fireworks"], "data_collection": "deny"}},
    )
    provider.complete(_request(tmp_path))
    body = http.requests[-1]["body"]
    assert body["provider"] == {"order": ["fireworks"], "data_collection": "deny"}


def test_adapter_sends_reasoning_effort(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="k",
        http_client=http,
        endpoint="http://h/v1/chat/completions",
        reasoning_effort="high",
    )
    provider.complete(_request(tmp_path))
    assert http.requests[-1]["body"]["reasoning_effort"] == "high"


def test_adapter_omits_reasoning_effort_when_unset(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="k",
        http_client=http,
        endpoint="http://h/v1/chat/completions",
    )
    provider.complete(_request(tmp_path))
    assert "reasoning_effort" not in http.requests[-1]["body"]


def test_adapter_no_secret_in_result_metadata(tmp_path):
    http = CapturingHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="SUPER-SECRET",
        http_client=http,
        endpoint="http://h/v1/chat/completions",
        extra_headers={"X-Secret": "HDR-SECRET"},
    )
    result = provider.complete(_request(tmp_path))
    serialized = json.dumps(
        {
            "final_text": result.final_text,
            "metadata": result.metadata,
            "model_id": result.model_id,
            "provider": result.provider_name,
        }
    )
    assert "SUPER-SECRET" not in serialized
    assert "HDR-SECRET" not in serialized
    assert "Bearer" not in serialized


# ---- Slice B: catalog -> adapter construction ------------------------------

from pipy_harness.native.auth_store import AuthStore, ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
)


def _ds4_spec() -> NativeModelSpec:
    return NativeModelSpec(
        provider_name="ds4",
        model_id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        api="openai-completions",
        base_url="http://127.0.0.1:8000/v1",
        reasoning=True,
        thinking_level_map={"high": "high", "low": "low"},
        cost=NativeModelCost(),
    )


def test_resolve_construction_uses_models_json_auth(tmp_path):
    resolved = resolve_construction(
        _ds4_spec(),
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(api_key="local-key"),
        thinking_level=None,
    )
    assert resolved.ok
    assert resolved.base_url == "http://127.0.0.1:8000/v1"
    assert resolved.model_id == "deepseek-v4-flash"
    assert resolved.api == "openai-completions"
    assert resolved.api_key == "local-key"


def test_resolve_construction_runtime_api_key_wins(tmp_path):
    store = AuthStore(path=tmp_path / "auth.json")
    store.set("ds4", {"type": "api_key", "key": "stored"})
    resolved = resolve_construction(
        _ds4_spec(),
        store=store,
        env={},
        runtime_api_key="runtime-wins",
        models_json_auth=ProviderAuthRequestConfig(api_key="local-key"),
        thinking_level=None,
    )
    assert resolved.api_key == "runtime-wins"


def test_resolve_construction_maps_thinking(tmp_path):
    resolved = resolve_construction(
        _ds4_spec(),
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(api_key="k"),
        thinking_level="high",
    )
    assert resolved.reasoning_effort == "high"


def test_resolve_construction_carries_routing_and_headers(tmp_path):
    spec = NativeModelSpec(
        provider_name="openrouter",
        model_id="moonshotai/kimi-k2.6",
        display_name="Kimi",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        headers={"X-Title": "pipy"},
        compat={"openRouterRouting": {"order": ["fireworks"]}},
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENROUTER_API_KEY": "or-key"},
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level=None,
    )
    assert resolved.body_extra["provider"] == {"order": ["fireworks"]}
    assert resolved.headers.get("X-Title") == "pipy"
    assert resolved.api_key == "or-key"


def test_build_provider_constructs_completions_adapter_from_catalog(tmp_path):
    resolved = resolve_construction(
        _ds4_spec(),
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(api_key="local-key"),
        thinking_level="high",
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert sent["body"]["model"] == "deepseek-v4-flash"
    assert sent["headers"]["Authorization"] == "Bearer local-key"
    assert sent["body"]["reasoning_effort"] == "high"


def test_build_provider_returns_none_for_unwired_api_family(tmp_path):
    spec = NativeModelSpec(
        provider_name="anthropic",
        model_id="claude-opus-4-7",
        display_name="Opus",
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"ANTHROPIC_API_KEY": "k"},
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level=None,
    )
    # Non-completions families fall back to the legacy factory (build returns
    # None to signal "not catalog-constructed here").
    assert build_provider(resolved, http_client=None) is None
