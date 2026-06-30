"""Tests for google-generative-ai thinkingConfig injection (Pi parity).

Pi's `google.ts` injects `generationConfig.thinkingConfig` per model family: a
`thinkingLevel` enum for Gemini 3 Pro/Flash and Gemma 4, a `thinkingBudget`
token count for the Gemini 2.5 family, with `includeThoughts: true` when thinking
is enabled, and a per-model disabled config (no `includeThoughts`) when a
reasoning model runs with thinking off/unset. Non-reasoning models omit it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.google_provider import (
    GoogleGenerativeAIProvider,
    JsonResponse,
    _build_thinking_config,
    _disabled_thinking_config,
    _google_thinking_budget,
    _google_thinking_level,
    _is_gemini3_flash,
    _is_gemini3_pro,
    _is_gemma4,
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
        provider_name="google",
        model_id=model_id,
        cwd=tmp_path,
    )


def _thinking_config(tmp_path, model_id, *, reasoning_effort, thinking_disabled):
    client = FakeJsonHTTPClient(_ok_response())
    provider = GoogleGenerativeAIProvider(
        model_id=model_id,
        api_key="key",
        http_client=client,
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


def test_enabled_budget_2_5_flash_lite_minimal(tmp_path):
    cfg = _thinking_config(
        tmp_path,
        "gemini-2.5-flash-lite",
        reasoning_effort="minimal",
        thinking_disabled=False,
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": 512}


def test_enabled_budget_unknown_model_dynamic(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.0-pro", reasoning_effort="medium", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingBudget": -1}


# ---- enabled: level families (Gemini 3, Gemma 4) -------------------------


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


def test_enabled_level_gemma4_low_is_minimal(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemma-4-it", reasoning_effort="low", thinking_disabled=False
    )
    assert cfg == {"includeThoughts": True, "thinkingLevel": "MINIMAL"}


# ---- disabled: reasoning model, thinking off/unset -----------------------


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


# ---- omit: non-reasoning / no thinking intent ----------------------------


def test_omit_when_no_thinking_intent(tmp_path):
    cfg = _thinking_config(
        tmp_path, "gemini-2.0-flash-exp", reasoning_effort=None, thinking_disabled=False
    )
    assert cfg is None


def test_default_construction_omits_thinking(tmp_path):
    client = FakeJsonHTTPClient(_ok_response())
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-2.5-pro", api_key="key", http_client=client
    )
    provider.complete(_request(tmp_path, "gemini-2.5-pro"))
    assert "generationConfig" not in client.requests[0]["body"]


# ---- helper unit tests ----------------------------------------------------


def test_model_family_regexes():
    assert _is_gemini3_pro("gemini-3.1-pro-preview")
    assert _is_gemini3_pro("gemini-3-pro")
    assert not _is_gemini3_pro("gemini-2.5-pro")
    assert _is_gemini3_flash("gemini-3-flash")
    assert _is_gemini3_flash("gemini-3.1-flash-lite")
    assert not _is_gemini3_flash("gemini-2.5-flash")
    assert _is_gemma4("gemma-4-it")
    assert _is_gemma4("gemma4")
    assert not _is_gemma4("gemma-3")


def test_thinking_level_default_family_passthrough():
    assert _google_thinking_level("minimal", "gemini-3-flash") == "MINIMAL"
    assert _google_thinking_level("low", "gemini-3-flash") == "LOW"
    assert _google_thinking_level("medium", "gemini-3-flash") == "MEDIUM"
    assert _google_thinking_level("high", "gemini-3-flash") == "HIGH"


def test_thinking_budget_tables():
    assert _google_thinking_budget("gemini-2.5-pro", "low") == 2048
    assert _google_thinking_budget("gemini-2.5-flash", "medium") == 8192
    assert _google_thinking_budget("gemini-2.5-flash-lite", "high") == 24576
    assert _google_thinking_budget("other", "high") == -1


def test_disabled_config_per_family():
    assert _disabled_thinking_config("gemini-3.1-pro-preview") == {"thinkingLevel": "LOW"}
    assert _disabled_thinking_config("gemini-3-flash") == {"thinkingLevel": "MINIMAL"}
    assert _disabled_thinking_config("gemma-4-it") == {"thinkingLevel": "MINIMAL"}
    assert _disabled_thinking_config("gemini-2.5-pro") == {"thinkingBudget": 0}


def test_build_thinking_config_precedence():
    # enabled wins over disabled when both supplied (mutually exclusive by
    # construction, but pin the precedence).
    assert _build_thinking_config("gemini-2.5-pro", "high", True) == {
        "includeThoughts": True,
        "thinkingBudget": 32768,
    }
    assert _build_thinking_config("gemini-2.5-pro", None, False) is None
