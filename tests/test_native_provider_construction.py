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
from pipy_harness.native.auth_store import AuthStore, ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec
from pipy_harness.native.openai_completions_provider import (
    JsonResponse,
    OpenAIChatCompletionsProvider,
)
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
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


def test_openrouter_thinking_uses_nested_reasoning_effort(tmp_path):
    spec = NativeModelSpec(
        provider_name="openrouter",
        model_id="moonshotai/kimi-k2.6",
        display_name="Kimi",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENROUTER_API_KEY": "k"},
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level="high",
    )
    # OpenRouter normalises reasoning into a nested object, not reasoning_effort.
    assert resolved.reasoning_effort is None
    assert resolved.body_extra["reasoning"] == {"effort": "high"}


def test_explicit_models_json_authorization_header_preserved(tmp_path):
    # A models.json headers.Authorization without authHeader must be preserved,
    # not overwritten by a Bearer api_key (Pi only overwrites when authHeader).
    resolved = resolve_construction(
        _ds4_spec(),
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key="ignored-key",
        models_json_auth=ProviderAuthRequestConfig(
            api_key="ignored-key", headers={"Authorization": "Custom secret-token"}
        ),
        thinking_level=None,
    )
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    assert http.requests[-1]["headers"]["Authorization"] == "Custom secret-token"


def test_constructed_adapter_repr_hides_secrets(tmp_path):
    provider = OpenAIChatCompletionsProvider(
        model_id="m",
        api_key="SUPER-SECRET",
        endpoint="http://h/v1/chat/completions",
        extra_headers={"Authorization": "Bearer HDR-SECRET"},
    )
    text = repr(provider)
    assert "SUPER-SECRET" not in text
    assert "HDR-SECRET" not in text


def test_auth_failure_returns_fail_closed_provider(tmp_path):
    # authHeader with no resolvable key -> resolve_request_auth ok=False ->
    # build_provider must NOT return None (would fall back to legacy); it returns
    # a fail-closed provider that reports the auth error on use.
    spec = _ds4_spec()
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(auth_header=True),  # no key
        thinking_level=None,
    )
    assert resolved.ok is False
    provider = build_provider(resolved, http_client=None)
    assert provider is not None
    result = provider.complete(_request(tmp_path))
    assert result.status.name != "SUCCEEDED"
    assert result.error_type == "CatalogAuthError"


def test_build_provider_returns_none_for_unwired_api_family(tmp_path):
    # google-generative-ai is not yet catalog-wired (Tier 2); it falls back to
    # the legacy factory (build returns None to signal "not catalog-constructed
    # here").
    spec = NativeModelSpec(
        provider_name="google",
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        api="google-generative-ai",
        base_url="https://generativelanguage.googleapis.com",
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"GOOGLE_API_KEY": "k"},
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level=None,
    )
    assert build_provider(resolved, http_client=None) is None


# ---- Slice C: Tier 1 non-completions families (catalog construction) --------
#
# anthropic-messages, openai-responses, and mistral are pure api_key + endpoint
# adapters. Catalog construction derives the endpoint by appending each family's
# path suffix to the catalog base_url, routes the resolved key into the family's
# native auth header, merges models.json/model headers, and places the mapped
# thinking effort in each family's native body key.


def _anthropic_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="anthropic",
        model_id="claude-opus-4-7",
        display_name="Opus",
        api="anthropic-messages",
        base_url="https://api.anthropic.com",
        reasoning=True,
        thinking_level_map={"high": "high", "xhigh": "xhigh"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def _responses_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="openai",
        model_id="gpt-5.5",
        display_name="GPT-5.5",
        api="openai-responses",
        base_url="https://api.openai.com/v1",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def _mistral_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="mistral",
        model_id="mistral-large-latest",
        display_name="Mistral Large",
        api="mistral",
        base_url="https://api.mistral.ai/v1",
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def _resolve(spec, tmp_path, env, *, thinking_level=None, models_json_auth=None):
    return resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env=env,
        runtime_api_key=None,
        models_json_auth=models_json_auth,
        thinking_level=thinking_level,
    )


def test_anthropic_catalog_construction(tmp_path):
    resolved = _resolve(
        _anthropic_spec(), tmp_path, {"ANTHROPIC_API_KEY": "ak"}, thinking_level="high"
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["body"]["model"] == "claude-opus-4-7"
    # native auth header is x-api-key (not Authorization)
    assert sent["headers"]["x-api-key"] == "ak"
    assert "Authorization" not in sent["headers"]
    # thinking placed in anthropic's native key as a budget
    assert sent["body"]["thinking"] == {"type": "enabled", "budget_tokens": 16384}


def test_anthropic_catalog_custom_baseurl_and_headers(tmp_path):
    spec = _anthropic_spec(
        provider_name="acme", base_url="https://acme.example", headers={"X-Acme": "1"}
    )
    resolved = _resolve(
        spec,
        tmp_path,
        {},
        models_json_auth=ProviderAuthRequestConfig(api_key="mk"),
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "https://acme.example/v1/messages"
    assert sent["headers"]["x-api-key"] == "mk"
    assert sent["headers"]["X-Acme"] == "1"
    assert provider.name == "acme"


def test_anthropic_xhigh_thinking_clamps_to_high_budget(tmp_path):
    # Claude's budget path has no xhigh; Pi clamps it to high (16384). The
    # opus-4-7-style row maps only xhigh, so thinking_level="xhigh" is honored.
    spec = _anthropic_spec(thinking_level_map={"xhigh": "xhigh"})
    resolved = _resolve(
        spec, tmp_path, {"ANTHROPIC_API_KEY": "ak"}, thinking_level="xhigh"
    )
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    assert http.requests[-1]["body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
    }


def test_anthropic_omits_thinking_when_unset(tmp_path):
    resolved = _resolve(_anthropic_spec(), tmp_path, {"ANTHROPIC_API_KEY": "ak"})
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    assert "thinking" not in http.requests[-1]["body"]


def test_anthropic_explicit_authorization_header_wins(tmp_path):
    spec = _anthropic_spec(provider_name="acme", base_url="https://acme.example")
    resolved = _resolve(
        spec,
        tmp_path,
        {},
        models_json_auth=ProviderAuthRequestConfig(
            api_key="mk", headers={"Authorization": "Custom tok"}
        ),
    )
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    sent = http.requests[-1]
    # explicit Authorization preserved; x-api-key not added on top
    assert sent["headers"]["Authorization"] == "Custom tok"
    assert "x-api-key" not in sent["headers"]


def test_anthropic_repr_hides_secret(tmp_path):
    resolved = _resolve(_anthropic_spec(), tmp_path, {"ANTHROPIC_API_KEY": "SECRET-AK"})
    provider = build_provider(resolved, http_client=CapturingHTTPClient())
    assert "SECRET-AK" not in repr(provider)


def test_openai_responses_catalog_construction(tmp_path):
    resolved = _resolve(
        _responses_spec(), tmp_path, {"OPENAI_API_KEY": "ok"}, thinking_level="high"
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["body"]["model"] == "gpt-5.5"
    assert sent["headers"]["Authorization"] == "Bearer ok"
    # responses thinking is the nested reasoning.effort object
    assert sent["body"]["reasoning"] == {"effort": "high"}


def test_openai_responses_omits_reasoning_when_unset(tmp_path):
    resolved = _resolve(_responses_spec(), tmp_path, {"OPENAI_API_KEY": "ok"})
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    assert "reasoning" not in http.requests[-1]["body"]


def test_mistral_catalog_construction(tmp_path):
    resolved = _resolve(_mistral_spec(), tmp_path, {"MISTRAL_API_KEY": "mk"})
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert sent["body"]["model"] == "mistral-large-latest"
    assert sent["headers"]["Authorization"] == "Bearer mk"


def test_tier1_auth_failure_fails_closed(tmp_path):
    # authHeader set with no resolvable key -> fail-closed provider, not None.
    resolved = _resolve(
        _anthropic_spec(provider_name="acme"),
        tmp_path,
        {},
        models_json_auth=ProviderAuthRequestConfig(auth_header=True),
    )
    assert resolved.ok is False
    provider = build_provider(resolved, http_client=None)
    assert provider is not None
    result = provider.complete(_request(tmp_path))
    assert result.error_type == "CatalogAuthError"
