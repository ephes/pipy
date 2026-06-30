"""Tests for google-vertex thinkingConfig injection (Pi parity).

Pi's `google-vertex.ts` injects `generationConfig.thinkingConfig` per model
family: a `thinkingLevel` enum for Gemini 3 Pro/Flash and a `thinkingBudget`
token count otherwise, with `includeThoughts: true` when thinking is enabled, and
a per-model disabled config (no `includeThoughts`) when a reasoning model runs
with thinking off/unset. Non-reasoning models (and the bare-default adapter)
omit it.

This is the `THINKING_LEVEL_MAP` variant of `google.ts` and diverges from
`google_provider` in two places: no `2.5-flash-lite` budget table (flash-lite
falls into the `2.5-flash` branch → minimal 128, not 512) and no Gemma 4
special-casing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.google_vertex_provider import (
    GoogleVertexProvider,
    JsonResponse,
    _build_thinking_config,
    _disabled_thinking_config,
    _google_thinking_budget,
    _google_thinking_level,
    _is_gemini3_flash,
    _is_gemini3_pro,
    _uses_thinking_level,
)


class FakeJsonHTTPClient:
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
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
        return self.response


def _ok_response() -> JsonResponse:
    return JsonResponse(
        status_code=200,
        body={
            "candidates": [
                {"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {},
        },
    )


def _request(tmp_path: Path, model_id: str) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="GOAL",
        provider_name="google-vertex",
        model_id=model_id,
        cwd=tmp_path,
    )


def _express_provider(model_id, client, *, reasoning_effort, thinking_disabled):
    # Express (api-key) mode: api_key set, no project/token needed.
    return GoogleVertexProvider(
        model_id=model_id,
        api_key="vk",
        access_token=None,
        project_id=None,
        http_client=client,
        reasoning_effort=reasoning_effort,
        thinking_disabled=thinking_disabled,
    )


def _thinking_config(tmp_path, model_id, *, reasoning_effort, thinking_disabled):
    client = FakeJsonHTTPClient(_ok_response())
    provider = _express_provider(
        model_id,
        client,
        reasoning_effort=reasoning_effort,
        thinking_disabled=thinking_disabled,
    )
    result = provider.complete(_request(tmp_path, model_id))
    assert result.status == HarnessStatus.SUCCEEDED
    body = client.requests[0]["body"]
    gen = body.get("generationConfig")
    return gen.get("thinkingConfig") if isinstance(gen, Mapping) else None


# ---- enabled: budget families (Gemini 2.5) -------------------------------


def test_enabled_budget_2_5_pro_high(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.5-pro", reasoning_effort="high", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": 32768}


def test_enabled_budget_2_5_flash_high(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.5-flash", reasoning_effort="high", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": 24576}


def test_enabled_budget_2_5_flash_lite_minimal_is_128(tmp_path):
    # Divergence from generative-ai (512): vertex has no flash-lite table, so
    # flash-lite matches the 2.5-flash branch -> minimal 128.
    cfg = _thinking_config(
        tmp_path,
        "gemini-2.5-flash-lite",
        reasoning_effort="minimal",
        thinking_disabled=False,
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": 128}


def test_enabled_budget_unknown_model_dynamic(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-1.5-pro", reasoning_effort="high", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": -1}


# ---- enabled: level families (Gemini 3) ----------------------------------


def test_enabled_level_gemini3_pro_medium_is_high(tmp_path):
    cfg = _thinking_config(
        tmp_path,
        "gemini-3.1-pro-preview",
        reasoning_effort="medium",
        thinking_disabled=False,
    )
    assert cfg == {"includeThoughts": True, "thinkingLevel": "HIGH"}


def test_enabled_level_gemini3_pro_low(tmp_path):
    cfg = _thinking_config(
        tmp_path,
        "gemini-3.1-pro-preview",
        reasoning_effort="low",
        thinking_disabled=False,
    )
    assert cfg == {"includeThoughts": True, "thinkingLevel": "LOW"}


def test_enabled_level_gemini3_flash_minimal(tmp_path):
    cfg = _thinking_config(
        tmp_path,
        "gemini-3-flash",
        reasoning_effort="minimal",
        thinking_disabled=False,
    )
    assert cfg == {"includeThoughts": True, "thinkingLevel": "MINIMAL"}


# ---- disabled config (reasoning model, thinking off) ---------------------


def test_disabled_2_5_pro_budget_zero(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.5-pro", reasoning_effort=None, thinking_disabled=True
    )
    assert cfg == {"thinkingBudget": 0}
    assert "includeThoughts" not in cfg


def test_disabled_gemini3_pro_level_low(tmp_path):
    cfg = _thinking_config(
        tmp_path,
        "gemini-3.1-pro-preview",
        reasoning_effort=None,
        thinking_disabled=True,
    )
    assert cfg == {"thinkingLevel": "LOW"}


def test_disabled_gemini3_flash_level_minimal(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-3-flash", reasoning_effort=None, thinking_disabled=True
    )
    assert cfg == {"thinkingLevel": "MINIMAL"}


# ---- omission -------------------------------------------------------------


def test_omit_when_no_thinking_intent(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.5-pro", reasoning_effort=None, thinking_disabled=False
    )
    assert cfg is None


def test_default_construction_omits_thinking(tmp_path):
    client = FakeJsonHTTPClient(_ok_response())
    provider = GoogleVertexProvider(
        model_id="gemini-2.5-pro",
        api_key="vk",
        access_token=None,
        project_id=None,
        http_client=client,
    )
    result = provider.complete(_request(tmp_path, "gemini-2.5-pro"))
    assert result.status == HarnessStatus.SUCCEEDED
    assert "generationConfig" not in client.requests[0]["body"]


# ---- no Gemma 4 special-case (divergence from generative-ai) -------------


def test_no_gemma4_special_case_uses_budget_path(tmp_path):
    # google.ts routes gemma-4 through the level path; vertex must not — gemma
    # is not a Vertex Gemini model, so it falls into the budget path (-1).
    cfg = _thinking_config(
        tmp_path, "gemma-4-it", reasoning_effort="low", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": -1}
    assert _uses_thinking_level("gemma-4-it") is False
    assert _disabled_thinking_config("gemma-4-it") == {"thinkingBudget": 0}


# ---- both auth modes inject thinkingConfig --------------------------------


def test_thinking_injected_in_express_mode(tmp_path):
    client = FakeJsonHTTPClient(_ok_response())
    provider = _express_provider(
        "gemini-2.5-pro", client, reasoning_effort="high", thinking_disabled=False
    )
    provider.complete(_request(tmp_path, "gemini-2.5-pro"))
    sent = client.requests[0]
    assert sent["headers"].get("x-goog-api-key") == "vk"
    assert "Authorization" not in sent["headers"]
    assert sent["body"]["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 32768,
    }


def test_thinking_injected_in_adc_mode(tmp_path):
    client = FakeJsonHTTPClient(_ok_response())
    provider = GoogleVertexProvider(
        model_id="gemini-2.5-pro",
        api_key=None,
        access_token="tok",
        project_id="proj",
        location="us-central1",
        http_client=client,
        reasoning_effort="high",
        thinking_disabled=False,
    )
    provider.complete(_request(tmp_path, "gemini-2.5-pro"))
    sent = client.requests[0]
    assert sent["headers"].get("Authorization") == "Bearer tok"
    assert "x-goog-api-key" not in sent["headers"]
    assert sent["body"]["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 32768,
    }


# ---- helper-level units ---------------------------------------------------


def test_model_family_regexes():
    assert _is_gemini3_pro("gemini-3.1-pro-preview")
    assert _is_gemini3_flash("gemini-3-flash")
    assert _is_gemini3_flash("gemini-3.1-flash-lite")
    assert not _is_gemini3_pro("gemini-2.5-pro")


def test_thinking_level_default_family_passthrough():
    assert _google_thinking_level("medium", "gemini-3-flash") == "MEDIUM"
    assert _google_thinking_level("minimal", "gemini-3-flash") == "MINIMAL"
    # Gemini 3 pro collapses minimal/low -> LOW, medium/high -> HIGH.
    assert _google_thinking_level("minimal", "gemini-3.1-pro-preview") == "LOW"
    assert _google_thinking_level("high", "gemini-3.1-pro-preview") == "HIGH"


def test_thinking_budget_tables_no_flash_lite_branch():
    assert _google_thinking_budget("gemini-2.5-pro", "high") == 32768
    assert _google_thinking_budget("gemini-2.5-flash", "minimal") == 128
    # flash-lite shares the flash table on vertex (not 512).
    assert _google_thinking_budget("gemini-2.5-flash-lite", "minimal") == 128
    assert _google_thinking_budget("gemini-1.5-pro", "high") == -1


def test_disabled_config_per_family_no_gemma():
    assert _disabled_thinking_config("gemini-3.1-pro-preview") == {"thinkingLevel": "LOW"}
    assert _disabled_thinking_config("gemini-3-flash") == {"thinkingLevel": "MINIMAL"}
    assert _disabled_thinking_config("gemini-2.5-pro") == {"thinkingBudget": 0}
    # No gemma branch: a gemma id gets the 2.x budget-zero fallback.
    assert _disabled_thinking_config("gemma-4-it") == {"thinkingBudget": 0}


def test_build_thinking_config_precedence():
    # reasoning_effort wins over thinking_disabled.
    cfg = _build_thinking_config("gemini-2.5-pro", "high", True)
    assert cfg == {"includeThoughts": True, "thinkingBudget": 32768}
    assert _build_thinking_config("gemini-2.5-pro", None, False) is None
