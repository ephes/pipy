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
        cancel_token: object = None,
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


def _openrouter_spec(**over: Any) -> NativeModelSpec:
    base: dict[str, Any] = dict(
        provider_name="openrouter",
        model_id="moonshotai/kimi-k2.6",
        display_name="Kimi",
        api="openai-completions",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)


def _resolve_or(spec: NativeModelSpec, tmp_path: Path, thinking_level):
    return resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENROUTER_API_KEY": "k"},
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level=thinking_level,
    )


def test_openrouter_off_state_emits_none_effort(tmp_path):
    # A reasoning-capable OpenRouter model with thinking off/unset and no explicit
    # off mapping disables reasoning at the router via reasoning.effort = "none"
    # (Pi openai-completions.ts:578-580), matching the shipped anthropic
    # thinking:{type:"disabled"} off-state pattern.
    resolved = _resolve_or(_openrouter_spec(), tmp_path, None)
    assert resolved.reasoning_effort is None
    assert resolved.body_extra["reasoning"] == {"effort": "none"}


def test_openrouter_off_state_skipped_for_non_reasoning_model(tmp_path):
    # Pi gates the whole branch on model.reasoning; a non-reasoning OpenRouter row
    # emits no reasoning key.
    resolved = _resolve_or(_openrouter_spec(reasoning=False), tmp_path, None)
    assert "reasoning" not in resolved.body_extra


def test_openrouter_off_state_explicit_null_off_suppresses_emission(tmp_path):
    # thinkingLevelMap.off === null suppresses the off emission entirely.
    spec = _openrouter_spec(thinking_level_map={"high": "high", "off": None})
    resolved = _resolve_or(spec, tmp_path, None)
    assert "reasoning" not in resolved.body_extra


def test_openrouter_off_state_uses_mapped_off_value(tmp_path):
    # A string off mapping is emitted verbatim (thinkingLevelMap.off ?? "none").
    spec = _openrouter_spec(thinking_level_map={"high": "high", "off": "minimal"})
    resolved = _resolve_or(spec, tmp_path, None)
    assert resolved.body_extra["reasoning"] == {"effort": "minimal"}


def test_openrouter_unsupported_level_does_not_emit_off_state(tmp_path):
    # An unsupported level clamps away (map_thinking_level -> None) but the raw
    # level is neither None nor "off"; Pi treats that as still-thinking-and-clamp,
    # so the off-state branch must not fire.
    spec = _openrouter_spec(thinking_level_map={"high": "high"})
    resolved = _resolve_or(spec, tmp_path, "medium")
    assert "reasoning" not in resolved.body_extra


def test_openrouter_off_state_reaches_request_body(tmp_path):
    # The off-state is itself a request-shape field to match: like the anthropic
    # thinking:{type:"disabled"} off-state, the OpenRouter reasoning:{effort:"none"}
    # disable must survive end-to-end onto the wire through the completions
    # adapter's extra_body plumbing, not merely sit in resolved.body_extra. Mirror
    # test_anthropic_disabled_thinking_when_reasoning_model_off at the adapter
    # boundary so a regression in build_provider's extra_body wiring is caught.
    for thinking_level in (None, "off"):
        resolved = _resolve_or(_openrouter_spec(), tmp_path, thinking_level)
        http = CapturingHTTPClient()
        build_provider(resolved, http_client=http).complete(_request(tmp_path))
        assert http.requests[-1]["body"]["reasoning"] == {"effort": "none"}


def test_openrouter_unsupported_level_emits_no_reasoning_on_wire(tmp_path):
    # The clamped-away level must also stay off the wire, not just out of
    # body_extra: Pi treats it as still-thinking-and-clamp, so no reasoning key is
    # sent for the off-state. (No reasoning_effort either, since map_thinking_level
    # returned None.)
    spec = _openrouter_spec(thinking_level_map={"high": "high"})
    resolved = _resolve_or(spec, tmp_path, "medium")
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    body = http.requests[-1]["body"]
    assert "reasoning" not in body
    assert "reasoning_effort" not in body


def _deepseek_spec(**over: Any) -> NativeModelSpec:
    base: dict[str, Any] = dict(
        provider_name="deepseek",
        model_id="deepseek-reasoner",
        display_name="DeepSeek Reasoner",
        api="openai-completions",
        base_url="https://api.deepseek.com/v1",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)


def _resolve_ds(spec: NativeModelSpec, tmp_path: Path, thinking_level):
    return resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"DEEPSEEK_API_KEY": "k"},
        runtime_api_key="k",
        models_json_auth=ProviderAuthRequestConfig(api_key="k", headers={}),
        thinking_level=thinking_level,
    )


def test_deepseek_on_state_emits_thinking_enabled_and_reasoning_effort(tmp_path):
    # Pi deepseek format (openai-completions.ts:565-570): a reasoning-capable model
    # with an active level sets thinking:{type:"enabled"} AND (supportsReasoningEffort
    # is true for DeepSeek) the top-level reasoning_effort.
    resolved = _resolve_ds(_deepseek_spec(), tmp_path, "high")
    assert resolved.body_extra["thinking"] == {"type": "enabled"}
    assert resolved.reasoning_effort == "high"


def test_deepseek_off_state_emits_thinking_disabled_without_effort(tmp_path):
    # Off/unset on a reasoning DeepSeek model is the Pi-forced explicit disable:
    # thinking:{type:"disabled"} and no reasoning_effort.
    for thinking_level in (None, "off"):
        resolved = _resolve_ds(_deepseek_spec(), tmp_path, thinking_level)
        assert resolved.body_extra["thinking"] == {"type": "disabled"}
        assert resolved.reasoning_effort is None


def test_deepseek_thinking_skipped_for_non_reasoning_model(tmp_path):
    # Pi gates the whole branch on model.reasoning; a non-reasoning DeepSeek row
    # emits neither the thinking object nor reasoning_effort.
    resolved = _resolve_ds(_deepseek_spec(reasoning=False), tmp_path, None)
    assert "thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_deepseek_unsupported_level_emits_neither(tmp_path):
    # An unsupported level clamps to None (pipy does not clamp like Pi); the raw
    # level is neither None nor "off", so neither the on-state nor the off-state
    # fires (matching the openrouter still-thinking-and-clamp divergence).
    resolved = _resolve_ds(_deepseek_spec(), tmp_path, "medium")
    assert "thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_deepseek_explicit_unsupported_reasoning_effort_omits_effort(tmp_path):
    # supportsReasoningEffort is resolved independently of thinkingFormat. An
    # explicit compat.supportsReasoningEffort=False keeps thinking:{type:"enabled"}
    # but drops reasoning_effort (Pi getCompat).
    spec = _deepseek_spec(compat={"supportsReasoningEffort": False})
    resolved = _resolve_ds(spec, tmp_path, "high")
    assert resolved.body_extra["thinking"] == {"type": "enabled"}
    assert resolved.reasoning_effort is None


def test_deepseek_explicit_format_on_excluded_provider_omits_effort(tmp_path):
    # An explicit thinkingFormat="deepseek" on a base URL Pi excludes from
    # reasoning_effort support (e.g. Together) yields thinking:{type:"enabled"}
    # with NO reasoning_effort, because detectCompat resolves
    # supportsReasoningEffort independently (isTogether -> false).
    spec = _deepseek_spec(
        provider_name="together",
        base_url="https://api.together.xyz/v1",
        compat={"thinkingFormat": "deepseek"},
    )
    resolved = _resolve_ds(spec, tmp_path, "high")
    assert resolved.body_extra["thinking"] == {"type": "enabled"}
    assert resolved.reasoning_effort is None


def test_deepseek_thinking_reaches_request_body(tmp_path):
    # The thinking object is a request-shape field to match; it must survive
    # end-to-end onto the wire through the completions adapter's extra_body
    # plumbing, in both on- and off-states.
    on = _resolve_ds(_deepseek_spec(), tmp_path, "high")
    http_on = CapturingHTTPClient()
    build_provider(on, http_client=http_on).complete(_request(tmp_path))
    body_on = http_on.requests[-1]["body"]
    assert body_on["thinking"] == {"type": "enabled"}
    assert body_on["reasoning_effort"] == "high"

    off = _resolve_ds(_deepseek_spec(), tmp_path, None)
    http_off = CapturingHTTPClient()
    build_provider(off, http_client=http_off).complete(_request(tmp_path))
    body_off = http_off.requests[-1]["body"]
    assert body_off["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body_off


def test_explicit_openrouter_format_wins_over_deepseek_base_url(tmp_path):
    # getCompat precedence: an explicit compat.thinkingFormat="openrouter" on a
    # deepseek.com base URL uses the nested reasoning object, not the deepseek
    # thinking shape (explicit compat wins over base-URL detection).
    spec = _deepseek_spec(compat={"thinkingFormat": "openrouter"})
    resolved = _resolve_ds(spec, tmp_path, "high")
    assert resolved.body_extra.get("reasoning") == {"effort": "high"}
    assert "thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def _together_spec(**over: Any) -> NativeModelSpec:
    base: dict[str, Any] = dict(
        provider_name="together",
        model_id="deepseek-ai/DeepSeek-R1",
        display_name="Together DeepSeek R1",
        api="openai-completions",
        base_url="https://api.together.xyz/v1",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)


def _resolve_tg(spec: NativeModelSpec, tmp_path: Path, thinking_level):
    return resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"TOGETHER_API_KEY": "k"},
        runtime_api_key="k",
        models_json_auth=ProviderAuthRequestConfig(api_key="k", headers={}),
        thinking_level=thinking_level,
    )


def test_together_on_state_emits_reasoning_enabled_without_effort(tmp_path):
    # Pi together format (openai-completions.ts:586-594): a reasoning-capable model
    # with an active level sets reasoning:{enabled:true}. Together auto-detects
    # supportsReasoningEffort=False (isTogether), so reasoning_effort is omitted.
    resolved = _resolve_tg(_together_spec(), tmp_path, "high")
    assert resolved.body_extra["reasoning"] == {"enabled": True}
    assert resolved.reasoning_effort is None


def test_together_off_state_emits_reasoning_disabled_without_effort(tmp_path):
    # Off/unset on a reasoning Together model is the Pi-forced explicit disable:
    # reasoning:{enabled:false} and no reasoning_effort.
    for thinking_level in (None, "off"):
        resolved = _resolve_tg(_together_spec(), tmp_path, thinking_level)
        assert resolved.body_extra["reasoning"] == {"enabled": False}
        assert resolved.reasoning_effort is None


def test_together_thinking_skipped_for_non_reasoning_model(tmp_path):
    # Pi gates the whole branch on model.reasoning; a non-reasoning Together row
    # emits neither the reasoning object nor reasoning_effort.
    resolved = _resolve_tg(_together_spec(reasoning=False), tmp_path, None)
    assert "reasoning" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_together_unsupported_level_emits_neither(tmp_path):
    # An unsupported level clamps to None (pipy does not clamp like Pi); the raw
    # level is neither None nor "off", so neither the on-state nor the off-state
    # fires (matching the deepseek/openrouter still-thinking-and-clamp divergence).
    resolved = _resolve_tg(_together_spec(), tmp_path, "medium")
    assert "reasoning" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_together_explicit_supports_reasoning_effort_adds_effort(tmp_path):
    # supportsReasoningEffort is resolved independently of thinkingFormat. An
    # explicit compat.supportsReasoningEffort=True flips Together's auto-False back
    # on, so the on-state adds reasoning_effort alongside reasoning:{enabled:true}.
    spec = _together_spec(compat={"supportsReasoningEffort": True})
    resolved = _resolve_tg(spec, tmp_path, "high")
    assert resolved.body_extra["reasoning"] == {"enabled": True}
    assert resolved.reasoning_effort == "high"


def test_together_explicit_format_on_non_excluded_provider_adds_effort(tmp_path):
    # An explicit thinkingFormat="together" on a base URL Pi does NOT exclude from
    # reasoning_effort support (e.g. api.openai.com) yields reasoning:{enabled:true}
    # WITH reasoning_effort, because detectCompat resolves supportsReasoningEffort
    # independently (no exclusion match -> true). This is the inverse of the
    # deepseek explicit-format-on-excluded-provider case.
    spec = _together_spec(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        compat={"thinkingFormat": "together"},
    )
    resolved = _resolve_tg(spec, tmp_path, "high")
    assert resolved.body_extra["reasoning"] == {"enabled": True}
    assert resolved.reasoning_effort == "high"


def test_together_thinking_reaches_request_body(tmp_path):
    # The reasoning object is a request-shape field to match; it must survive
    # end-to-end onto the wire through the completions adapter's extra_body
    # plumbing, in both on- and off-states.
    on = _resolve_tg(_together_spec(), tmp_path, "high")
    http_on = CapturingHTTPClient()
    build_provider(on, http_client=http_on).complete(_request(tmp_path))
    body_on = http_on.requests[-1]["body"]
    assert body_on["reasoning"] == {"enabled": True}
    assert "reasoning_effort" not in body_on

    off = _resolve_tg(_together_spec(), tmp_path, None)
    http_off = CapturingHTTPClient()
    build_provider(off, http_client=http_off).complete(_request(tmp_path))
    body_off = http_off.requests[-1]["body"]
    assert body_off["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in body_off


def test_together_detection_precedes_openrouter(tmp_path):
    # Pi's detectCompat thinkingFormat chain evaluates isTogether before
    # isOpenRouter (openai-completions.ts:1126-1136), so a row matching both the
    # together provider and an openrouter.ai base URL resolves to the together
    # shape (reasoning:{enabled}), not the openrouter nested reasoning:{effort}.
    spec = _together_spec(base_url="https://openrouter.ai/api/v1")
    resolved = _resolve_tg(spec, tmp_path, "high")
    assert resolved.body_extra["reasoning"] == {"enabled": True}
    assert resolved.reasoning_effort is None


def _zai_spec(**over: Any) -> NativeModelSpec:
    base: dict[str, Any] = dict(
        provider_name="zai",
        model_id="glm-4.6",
        display_name="Z.ai GLM 4.6",
        api="openai-completions",
        base_url="https://api.z.ai/api/paas/v4",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)


def _resolve_zai(spec: NativeModelSpec, tmp_path: Path, thinking_level):
    return resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={"ZAI_API_KEY": "k"},
        runtime_api_key="k",
        models_json_auth=ProviderAuthRequestConfig(api_key="k", headers={}),
        thinking_level=thinking_level,
    )


def test_zai_on_state_emits_enable_thinking_true_without_effort(tmp_path):
    # Pi zai format (openai-completions.ts:556-557): a reasoning-capable model with
    # an active level sets the boolean enable_thinking=true and emits NO
    # reasoning_effort (the zai branch never consults supportsReasoningEffort).
    resolved = _resolve_zai(_zai_spec(), tmp_path, "high")
    assert resolved.body_extra["enable_thinking"] is True
    assert resolved.reasoning_effort is None


def test_zai_off_state_emits_enable_thinking_false_without_effort(tmp_path):
    # Off/unset on a reasoning Z.ai model is the Pi-forced explicit disable:
    # enable_thinking=false and no reasoning_effort.
    for thinking_level in (None, "off"):
        resolved = _resolve_zai(_zai_spec(), tmp_path, thinking_level)
        assert resolved.body_extra["enable_thinking"] is False
        assert resolved.reasoning_effort is None


def test_zai_thinking_skipped_for_non_reasoning_model(tmp_path):
    # Pi gates the whole branch on model.reasoning; a non-reasoning Z.ai row emits
    # neither enable_thinking nor reasoning_effort.
    resolved = _resolve_zai(_zai_spec(reasoning=False), tmp_path, None)
    assert "enable_thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_zai_ignores_supports_reasoning_effort(tmp_path):
    # Unlike deepseek/together, the zai branch never emits reasoning_effort even
    # when an explicit compat.supportsReasoningEffort=True flips the (irrelevant)
    # secondary flag on; the emitted shape stays a bare enable_thinking boolean.
    spec = _zai_spec(compat={"supportsReasoningEffort": True})
    resolved = _resolve_zai(spec, tmp_path, "high")
    assert resolved.body_extra["enable_thinking"] is True
    assert resolved.reasoning_effort is None


def test_zai_unsupported_level_emits_neither(tmp_path):
    # An unsupported level clamps to None (pipy does not clamp like Pi); the raw
    # level is neither None nor "off", so neither the on-state nor the off-state
    # fires (matching the deepseek/together/openrouter still-thinking-and-clamp
    # divergence).
    resolved = _resolve_zai(_zai_spec(), tmp_path, "medium")
    assert "enable_thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


def test_zai_thinking_reaches_request_body(tmp_path):
    # enable_thinking is a request-shape field to match; it must survive end-to-end
    # onto the wire through the completions adapter's extra_body plumbing, in both
    # on- and off-states.
    on = _resolve_zai(_zai_spec(), tmp_path, "high")
    http_on = CapturingHTTPClient()
    build_provider(on, http_client=http_on).complete(_request(tmp_path))
    body_on = http_on.requests[-1]["body"]
    assert body_on["enable_thinking"] is True
    assert "reasoning_effort" not in body_on

    off = _resolve_zai(_zai_spec(), tmp_path, None)
    http_off = CapturingHTTPClient()
    build_provider(off, http_client=http_off).complete(_request(tmp_path))
    body_off = http_off.requests[-1]["body"]
    assert body_off["enable_thinking"] is False
    assert "reasoning_effort" not in body_off


def test_zai_detection_precedes_together_and_openrouter(tmp_path):
    # Pi's detectCompat thinkingFormat chain evaluates isZai before isTogether and
    # isOpenRouter (openai-completions.ts:1126-1136), so a zai-provider row on a
    # together or openrouter base URL resolves to the zai shape (enable_thinking),
    # not together's reasoning:{enabled} or openrouter's nested reasoning:{effort}.
    for base_url in (
        "https://api.together.xyz/v1",
        "https://openrouter.ai/api/v1",
    ):
        resolved = _resolve_zai(_zai_spec(base_url=base_url), tmp_path, "high")
        assert resolved.body_extra["enable_thinking"] is True
        assert "reasoning" not in resolved.body_extra
        assert resolved.reasoning_effort is None


def test_explicit_openrouter_format_wins_over_zai_base_url(tmp_path):
    # getCompat precedence: an explicit compat.thinkingFormat="openrouter" on an
    # api.z.ai base URL uses the nested reasoning object, not enable_thinking.
    spec = _zai_spec(compat={"thinkingFormat": "openrouter"})
    resolved = _resolve_zai(spec, tmp_path, "high")
    assert resolved.body_extra.get("reasoning") == {"effort": "high"}
    assert "enable_thinking" not in resolved.body_extra
    assert resolved.reasoning_effort is None


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
    # The deterministic ``fake`` bootstrap is not catalog-constructed; it falls
    # back to the legacy factory (build returns None to signal "not
    # catalog-constructed here").
    spec = NativeModelSpec(
        provider_name="fake",
        model_id="fake-native-bootstrap",
        display_name="Fake",
        api="fake",
        base_url=None,
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
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
    # Default to a non-adaptive reasoning model so the shared fixture exercises
    # the budget_tokens thinking path. Adaptive Claude ids (opus-4-6/4-7/4-8,
    # sonnet-4-6) take the adaptive shape, so a bare _anthropic_spec() must not
    # default to one, or future tests silently flip onto the adaptive path. The
    # adaptive path is covered explicitly via model_id overrides below.
    base = dict(
        provider_name="anthropic",
        model_id="claude-sonnet-4-5",
        display_name="Sonnet",
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
    # claude-sonnet-4-5 is a non-adaptive reasoning model, so it keeps the
    # budget_tokens thinking path (adaptive models are covered separately below).
    resolved = _resolve(
        _anthropic_spec(model_id="claude-sonnet-4-5"),
        tmp_path,
        {"ANTHROPIC_API_KEY": "ak"},
        thinking_level="high",
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["body"]["model"] == "claude-sonnet-4-5"
    # native auth header is x-api-key (not Authorization)
    assert sent["headers"]["x-api-key"] == "ak"
    assert "Authorization" not in sent["headers"]
    # thinking placed in anthropic's native key as a budget, display forced
    assert sent["body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
        "display": "summarized",
    }
    assert "output_config" not in sent["body"]


def test_anthropic_adaptive_thinking_construction(tmp_path):
    # claude-opus-4-8 is an adaptive Claude model (compat.forceAdaptiveThinking),
    # so it uses the adaptive thinking + output_config.effort shape, not budget.
    resolved = _resolve(
        _anthropic_spec(model_id="claude-opus-4-8"),
        tmp_path,
        {"ANTHROPIC_API_KEY": "ak"},
        thinking_level="high",
    )
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["body"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["body"]["output_config"] == {"effort": "high"}


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
    # Claude's budget path has no xhigh; Pi clamps it to high (16384). Use a
    # non-adaptive reasoning model so the budget path applies; the row maps only
    # xhigh, so thinking_level="xhigh" is honored.
    spec = _anthropic_spec(
        model_id="claude-sonnet-4-5", thinking_level_map={"xhigh": "xhigh"}
    )
    resolved = _resolve(
        spec, tmp_path, {"ANTHROPIC_API_KEY": "ak"}, thinking_level="xhigh"
    )
    http = CapturingHTTPClient()
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    assert http.requests[-1]["body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
        "display": "summarized",
    }


def test_anthropic_disabled_thinking_when_reasoning_model_off(tmp_path):
    # A reasoning-capable model run with thinking off/unset matches Pi by making
    # the off-state explicit on the wire (anthropic.ts streamSimpleAnthropic ->
    # buildParams thinkingEnabled === false), not by omitting the key. The
    # disabled shape carries only ``type``.
    for thinking_level in (None, "off"):
        resolved = _resolve(
            _anthropic_spec(), tmp_path, {"ANTHROPIC_API_KEY": "ak"},
            thinking_level=thinking_level,
        )
        assert resolved.thinking_disabled is True
        http = CapturingHTTPClient()
        build_provider(resolved, http_client=http).complete(_request(tmp_path))
        body = http.requests[-1]["body"]
        assert body["thinking"] == {"type": "disabled"}
        assert "output_config" not in body


def test_anthropic_omits_thinking_for_non_reasoning_model(tmp_path):
    # Non-reasoning models omit thinking entirely (Pi's outer `if (model.reasoning)`
    # guard); no disabled shape is emitted.
    resolved = _resolve(
        _anthropic_spec(reasoning=False), tmp_path, {"ANTHROPIC_API_KEY": "ak"}
    )
    assert resolved.thinking_disabled is False
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


# ---- Slice D: Tier 2 composed/template-endpoint families --------------------
#
# google-generative-ai (model-in-path + ?key=), azure-openai-responses
# (deployment + api-version), cloudflare-workers-ai (account embedded in the
# base_url via {ENV} substitution + two-part auth).


def _google_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="google",
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        api="google-generative-ai",
        base_url="https://generativelanguage.googleapis.com",
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def _azure_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="azure-openai",
        model_id="gpt-5.4",
        display_name="Azure GPT-5.4",
        api="azure-openai-responses",
        base_url="https://azure-openai.example",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def _cloudflare_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="cloudflare",
        model_id="@cf/meta/llama-3.3-70b-instruct",
        display_name="CF Llama",
        api="cloudflare-workers-ai",
        base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def test_google_catalog_construction(tmp_path):
    resolved = _resolve(_google_spec(), tmp_path, {"GEMINI_API_KEY": "gk"})
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-pro:generateContent?key=gk"
    )
    assert sent["body"]["contents"]


def test_google_repr_hides_secret(tmp_path):
    resolved = _resolve(_google_spec(), tmp_path, {"GEMINI_API_KEY": "SECRET-GK"})
    provider = build_provider(resolved, http_client=CapturingHTTPClient())
    assert "SECRET-GK" not in repr(provider)


def test_azure_catalog_construction(tmp_path):
    resolved = _resolve(
        _azure_spec(),
        tmp_path,
        {"AZURE_OPENAI_API_KEY": "azk"},
        thinking_level="high",
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    # Pi's AzureOpenAI v1 surface: <base>/responses?api-version=v1. The default
    # api-version (v1) is asserted explicitly here. Note that the ``_resolve``
    # env dict does NOT carry api-version: the adapter's ``api_version`` field
    # reads the *process* environment (an ``os.environ`` default_factory), not
    # this dict, so an ``AZURE_OPENAI_API_VERSION`` entry here would be a silent
    # no-op. The genuine env-override path is exercised in the dedicated
    # monkeypatch test below. A non-Azure host base URL is respected verbatim
    # (no /openai/v1 normalization).
    assert sent["url"] == (
        "https://azure-openai.example/responses?api-version=v1"
    )
    # The deployment (here the model id) is the body ``model`` field.
    assert sent["body"]["model"] == "gpt-5.4"
    # Azure uses the api-key header, not Authorization: Bearer
    assert sent["headers"]["api-key"] == "azk"
    assert "Authorization" not in sent["headers"]
    # Azure shares the Responses thinking shape
    assert sent["body"]["reasoning"] == {"effort": "high"}


def test_azure_catalog_construction_api_version_env_override(tmp_path, monkeypatch):
    # The adapter's ``api_version`` is backed by an ``os.environ`` default_factory,
    # not the ``_resolve`` env dict, so the override path must be exercised by
    # patching the *process* environment. Without this, the default test's
    # "AZURE_OPENAI_API_VERSION overrides via the environment" claim is unverified
    # (a stale env entry equal to the old default would silently pass).
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    resolved = _resolve(
        _azure_spec(),
        tmp_path,
        {"AZURE_OPENAI_API_KEY": "azk"},
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    # The process-env override genuinely reaches the composed URL.
    assert sent["url"] == (
        "https://azure-openai.example/responses?api-version=2024-12-01-preview"
    )


def test_cloudflare_catalog_construction(tmp_path):
    resolved = _resolve(
        _cloudflare_spec(),
        tmp_path,
        {"CLOUDFLARE_ACCOUNT_ID": "acct-123", "CLOUDFLARE_API_KEY": "cfk"},
    )
    http = CapturingHTTPClient()
    provider = build_provider(resolved, http_client=http)
    assert provider is not None
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    # account id substituted into the base_url, then /chat/completions appended
    assert sent["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/acct-123/ai/v1/chat/completions"
    )
    assert sent["body"]["model"] == "@cf/meta/llama-3.3-70b-instruct"
    assert sent["headers"]["Authorization"] == "Bearer cfk"


def test_cloudflare_missing_account_env_fails_closed(tmp_path):
    # base_url references {CLOUDFLARE_ACCOUNT_ID} but env doesn't set it ->
    # fail-closed (Pi's resolveCloudflareBaseUrl throws on a missing var).
    resolved = _resolve(_cloudflare_spec(), tmp_path, {"CLOUDFLARE_API_KEY": "cfk"})
    assert resolved.ok is False
    provider = build_provider(resolved, http_client=None)
    assert provider is not None
    result = provider.complete(_request(tmp_path))
    assert result.status.name != "SUCCEEDED"


# ---- Slice E: Tier 3 IAM/OAuth families -------------------------------------
#
# amazon-bedrock (SigV4), google-vertex (ADC OAuth token), openai-codex-responses
# (Codex OAuth). Auth + region/project endpoint stay env-resolved by the adapter;
# catalog construction injects model_id + provider_name + headers + thinking
# (bedrock Anthropic budget, codex reasoning.effort; vertex thinking deferred).


def _bedrock_spec(**over: Any) -> NativeModelSpec:
    base = dict(
        provider_name="amazon-bedrock",
        model_id="us.anthropic.claude-opus-4-6-v1",
        display_name="Bedrock Opus",
        api="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        thinking_level_map={"high": "high"},
        cost=NativeModelCost(),
    )
    base.update(over)
    return NativeModelSpec(**base)  # type: ignore[arg-type]


def test_bedrock_catalog_construction_type_and_thinking_wired(tmp_path):
    from pipy_harness.native.bedrock_provider import AmazonBedrockProvider

    resolved = _resolve(
        _bedrock_spec(),
        tmp_path,
        {"AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"},
        thinking_level="high",
    )
    provider = build_provider(resolved, http_client=None)
    assert isinstance(provider, AmazonBedrockProvider)
    assert provider.model_id == "us.anthropic.claude-opus-4-6-v1"
    assert provider.provider_name == "amazon-bedrock"
    # thinking effort is threaded; auth stays env-resolved (not from catalog)
    assert provider.reasoning_effort == "high"


def _bedrock_adapter(model_id, **over):
    from datetime import UTC, datetime

    from pipy_harness.native.bedrock_provider import AmazonBedrockProvider

    defaults = dict(
        model_id=model_id,
        region="us-east-1",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        _clock=lambda: datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
    )
    defaults.update(over)
    return AmazonBedrockProvider(**defaults)


def test_bedrock_adaptive_thinking_reaches_signed_body(tmp_path):
    # opus-4-6 is an adaptive Claude model -> adaptive thinking + output_config.
    http = CapturingHTTPClient()
    provider = _bedrock_adapter(
        "us.anthropic.claude-opus-4-6-v1", http_client=http, reasoning_effort="high"
    )
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["url"] == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
        "us.anthropic.claude-opus-4-6-v1/invoke"
    )
    # display is forced to "summarized" on the adaptive path (Pi includes it
    # except on GovCloud); the adaptive models' API default is "omitted".
    assert sent["body"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["body"]["output_config"] == {"effort": "high"}
    # the SigV4 Authorization header is present and signed
    assert sent["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")


def test_bedrock_budget_thinking_for_non_adaptive_model(tmp_path):
    # A non-adaptive Claude model uses the budget_tokens path; display is forced
    # to "summarized" there too (Pi amazon-bedrock.ts:974-978).
    http = CapturingHTTPClient()
    provider = _bedrock_adapter(
        "anthropic.claude-3-7-sonnet-20250219-v1:0",
        http_client=http,
        reasoning_effort="high",
    )
    provider.complete(_request(tmp_path))
    assert http.requests[-1]["body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
        "display": "summarized",
    }
    assert "output_config" not in http.requests[-1]["body"]


def test_bedrock_govcloud_region_omits_display_on_both_paths(tmp_path):
    # GovCloud Bedrock rejects the Claude thinking.display field, so Pi omits it
    # when the configured region is us-gov-* (amazon-bedrock.ts:933-936, :952-954).
    http = CapturingHTTPClient()
    adaptive = _bedrock_adapter(
        "us.anthropic.claude-opus-4-6-v1",
        http_client=http,
        region="us-gov-east-1",
        reasoning_effort="high",
    )
    adaptive.complete(_request(tmp_path))
    assert http.requests[-1]["body"]["thinking"] == {"type": "adaptive"}
    assert "display" not in http.requests[-1]["body"]["thinking"]

    budget = _bedrock_adapter(
        "anthropic.claude-3-7-sonnet-20250219-v1:0",
        http_client=http,
        region="us-gov-west-1",
        reasoning_effort="high",
    )
    budget.complete(_request(tmp_path))
    assert http.requests[-1]["body"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
    }
    assert "display" not in http.requests[-1]["body"]["thinking"]


def test_bedrock_govcloud_model_id_prefix_omits_display(tmp_path):
    # GovCloud detected from the model id prefix even on a non-gov region
    # (amazon-bedrock.ts:939-940): us-gov. and arn:aws-us-gov: both qualify.
    http = CapturingHTTPClient()
    for model_id in (
        "us-gov.anthropic.claude-opus-4-6-v1",
        "arn:aws-us-gov:bedrock:us-east-1::foundation-model/anthropic.claude-opus-4-6-v1",
    ):
        provider = _bedrock_adapter(
            model_id, http_client=http, region="us-east-1", reasoning_effort="high"
        )
        provider.complete(_request(tmp_path))
        assert "display" not in http.requests[-1]["body"]["thinking"]


def test_bedrock_omits_thinking_block_when_reasoning_unset(tmp_path):
    # With thinking off there is no thinking block and therefore no display.
    http = CapturingHTTPClient()
    provider = _bedrock_adapter(
        "us.anthropic.claude-opus-4-6-v1", http_client=http
    )
    provider.complete(_request(tmp_path))
    assert "thinking" not in http.requests[-1]["body"]
    assert "output_config" not in http.requests[-1]["body"]


def test_bedrock_drops_reserved_headers_before_signing(tmp_path):
    # authorization/host/x-amz-* custom headers must not collide with SigV4.
    http = CapturingHTTPClient()
    provider = _bedrock_adapter(
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
        http_client=http,
        extra_headers={
            "X-Custom": "ok",
            "Authorization": "Bearer nope",
            "x-amz-target": "nope",
            "Host": "evil",
        },
    )
    provider.complete(_request(tmp_path))
    sent = http.requests[-1]
    assert sent["headers"].get("X-Custom") == "ok"
    # the custom Authorization did not override the SigV4 signature
    assert sent["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert "x-amz-target" not in sent["headers"]


def test_vertex_catalog_construction(tmp_path):
    from pipy_harness.native.google_vertex_provider import GoogleVertexProvider

    spec = NativeModelSpec(
        provider_name="google-vertex",
        model_id="gemini-2.5-pro",
        display_name="Vertex Gemini",
        api="google-vertex",
        base_url="https://aiplatform.googleapis.com",
        cost=NativeModelCost(),
    )
    resolved = _resolve(spec, tmp_path, {"GOOGLE_CLOUD_API_KEY": "vk"})
    provider = build_provider(resolved, http_client=None)
    assert isinstance(provider, GoogleVertexProvider)
    assert provider.model_id == "gemini-2.5-pro"
    assert provider.provider_name == "google-vertex"
    # The resolved Vertex Express api key is forwarded so the adapter can use
    # api-key (express) mode rather than only the ADC bearer path.
    assert provider.api_key == "vk"


def test_vertex_catalog_no_key_resolves_to_adc(tmp_path):
    # With no Vertex Express key (and no ADC detected) the forwarded api key is
    # None, so the adapter uses its ADC bearer path.
    from pipy_harness.native.google_vertex_provider import GoogleVertexProvider

    spec = NativeModelSpec(
        provider_name="google-vertex",
        model_id="gemini-2.5-pro",
        display_name="Vertex Gemini",
        api="google-vertex",
        base_url="https://aiplatform.googleapis.com",
        cost=NativeModelCost(),
    )
    resolved = _resolve(spec, tmp_path, {})
    provider = build_provider(resolved, http_client=None)
    assert isinstance(provider, GoogleVertexProvider)
    assert provider.api_key is None
    assert provider._resolve_express_api_key() is None


def test_codex_stays_on_legacy_factory(tmp_path):
    # openai-codex-responses is deliberately NOT catalog-constructed (the legacy
    # factory injects a settings-derived RetryPolicy that catalog construction
    # would drop); build_provider returns None so the caller falls back.
    spec = NativeModelSpec(
        provider_name="openai-codex",
        model_id="gpt-5.5",
        display_name="Codex GPT-5.5",
        api="openai-codex-responses",
        base_url="https://chatgpt.com/backend-api/codex",
        cost=NativeModelCost(),
    )
    resolved = _resolve(spec, tmp_path, {})
    assert build_provider(resolved, http_client=None) is None


def test_tier3_boundary_constructs_bedrock_from_catalog(tmp_path):
    from pipy_harness.native.bedrock_provider import AmazonBedrockProvider
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    state = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )

    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    repl_state = NativeReplProviderState(
        selection=NativeModelSelection(
            "amazon-bedrock", "us.anthropic.claude-opus-4-6-v1"
        ),
        provider_factory=_no_legacy,
        catalog_state=state,
        persist_defaults=False,
    )
    provider = repl_state.current_provider()
    assert isinstance(provider, AmazonBedrockProvider)
    assert provider.model_id == "us.anthropic.claude-opus-4-6-v1"
