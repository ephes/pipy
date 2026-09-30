"""Model-currency parity with Pi @4df157433 (catalog rows, defaults, thinking).

Expected values come from Pi's generator output
(``packages/ai/scripts/generate-models.ts`` run against models.dev) and
``defaultModelPerProvider`` (``coding-agent/src/core/model-resolver.ts``). Each
wire test pins the request shape Pi's adapter emits for the same row and level.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native import ProviderRequest
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog import (
    NativeModelSpec,
    build_builtin_catalog,
    default_model_per_provider,
)
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.openai_codex_provider import _codex_request_body
from pipy_harness.native.provider_construction import (
    ConstructionOptions,
    ResolvedConstruction,
    build_openai_codex_provider,
    build_provider,
    resolve_construction,
)
from pipy_harness.native.provider_registry import DEFAULT_NATIVE_MODELS
from pipy_harness.native.providers.google_generative_ai import _build_thinking_config
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.thinking import available_thinking_levels

# Pi defaultModelPerProvider, mapped onto pipy's provider names
# (azure-openai-responses -> azure-openai, cloudflare-workers-ai -> cloudflare).
# openai-completions is pipy-only and takes Pi's `openai` default.
PI_DEFAULTS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.5",
    "openai-codex": "gpt-6.1-sol",
    "openai-completions": "gpt-5.5",
    "openrouter": "moonshotai/kimi-k2.6",
    "google": "gemini-3.1-pro-preview",
    "google-vertex": "gemini-3.1-pro-preview",
    "mistral": "devstral-medium-latest",
    "amazon-bedrock": "us.anthropic.claude-opus-4-6-v1",
    "azure-openai": "gpt-5.4",
    "cloudflare": "@cf/moonshotai/kimi-k2.6",
    "github-copilot": "gpt-5.4",
    "xai": "grok-4.7",
}

_ALL = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
_NO_OFF = _ALL[1:]
_NO_OFF_NO_MINIMAL = _ALL[2:]
_NO_MAX = _ALL[:-1]

# (provider, id) -> (cost in/out/cacheRead/cacheWrite, context, max tokens,
# Pi-available thinking levels)
PI_ROWS: dict[tuple[str, str], tuple[tuple[float, ...], int, int, list[str]]] = {
    ("anthropic", "claude-opus-5-5"): (
        (4, 20, 0.2, 5),
        1_000_000,
        128_000,
        _NO_OFF_NO_MINIMAL,
    ),
    ("anthropic", "claude-sonnet-5-5"): (
        (2, 10, 0.2, 2.5),
        1_000_000,
        128_000,
        _NO_OFF_NO_MINIMAL,
    ),
    ("anthropic", "claude-opus-5"): ((5, 25, 0.5, 6.25), 1_000_000, 128_000, _NO_OFF),
    ("anthropic", "claude-sonnet-5"): ((2, 10, 0.2, 2.5), 1_000_000, 128_000, _ALL),
    ("anthropic", "claude-fable-5"): ((10, 50, 1, 12.5), 1_000_000, 128_000, _NO_OFF),
    ("anthropic", "claude-fable-5-1"): (
        (10, 50, 0.25, 12.5),
        1_000_000,
        128_000,
        _NO_OFF,
    ),
    ("anthropic", "claude-opus-4-8"): ((5, 25, 0.5, 6.25), 1_000_000, 128_000, _ALL),
    ("anthropic", "claude-opus-4-7"): ((5, 25, 0.5, 6.25), 1_000_000, 128_000, _ALL),
    ("anthropic", "claude-sonnet-4-5"): (
        (3, 15, 0.3, 3.75),
        1_000_000,
        64_000,
        _ALL[:5],
    ),
    ("anthropic", "claude-haiku-4-5"): ((1, 5, 0.1, 1.25), 200_000, 64_000, _ALL[:5]),
    ("anthropic", "claude-haiku-4-5-20251001"): (
        (1, 5, 0.1, 1.25),
        200_000,
        64_000,
        _ALL[:5],
    ),
    # Pi 12c416e1a: GPT-6.1 Sol rejects reasoning.effort "none" (off: null)
    ("openai", "gpt-6.1-sol"): (
        (2, 10, 0.1, 2.5),
        272_000,
        128_000,
        _NO_OFF_NO_MINIMAL,
    ),
    ("openai", "gpt-6-sol"): (
        (2, 10, 0.2, 2.5),
        272_000,
        128_000,
        ["off", *_NO_OFF_NO_MINIMAL],
    ),
    ("openai", "gpt-6-luna"): (
        (0.1, 0.5, 0.01, 0.125),
        272_000,
        128_000,
        ["off", *_NO_OFF_NO_MINIMAL],
    ),
    ("openai", "gpt-6-astra"): (
        (10, 50, 1, 12.5),
        272_000,
        128_000,
        _NO_OFF_NO_MINIMAL,
    ),
    ("openai", "gpt-5.6-sol"): (
        (4, 20, 0.4, 5),
        272_000,
        128_000,
        ["off", *_NO_OFF_NO_MINIMAL],
    ),
    ("openai", "gpt-5.6-terra"): (
        (2, 12, 0.2, 2.5),
        272_000,
        128_000,
        ["off", *_NO_OFF_NO_MINIMAL],
    ),
    ("openai", "gpt-5.6-luna"): (
        (0.2, 1.2, 0.02, 0.25),
        272_000,
        128_000,
        ["off", *_NO_OFF_NO_MINIMAL],
    ),
    ("openai", "gpt-5.5"): (
        (5, 30, 0.5, 0),
        272_000,
        128_000,
        ["off", "low", "medium", "high", "xhigh"],
    ),
    ("openai", "gpt-5.4"): (
        (2.5, 15, 0.25, 0),
        272_000,
        128_000,
        ["off", "low", "medium", "high", "xhigh"],
    ),
    ("openai-codex", "gpt-6.1-sol"): ((2, 10, 0.1, 2.5), 272_000, 128_000, _NO_OFF),
    ("openai-codex", "gpt-6-sol"): ((2, 10, 0.2, 2.5), 272_000, 128_000, _ALL),
    ("openai-codex", "gpt-6-luna"): ((0.1, 0.5, 0.01, 0.125), 272_000, 128_000, _ALL),
    ("openai-codex", "gpt-6-astra"): ((10, 50, 1, 12.5), 272_000, 128_000, _NO_OFF),
    ("openai-codex", "gpt-5.6-sol"): ((4, 20, 0.4, 5), 272_000, 128_000, _ALL),
    ("openai-codex", "gpt-5.6-terra"): ((2, 12, 0.2, 2.5), 272_000, 128_000, _ALL),
    ("openai-codex", "gpt-5.6-luna"): ((0.2, 1.2, 0.02, 0.25), 272_000, 128_000, _ALL),
    ("openai-codex", "gpt-5.5"): ((5, 30, 0.5, 0), 272_000, 128_000, _NO_MAX),
    ("google", "gemini-3.5-flash"): (
        (1.5, 9, 0.15, 0),
        1_048_576,
        65_536,
        ["minimal", "low", "medium", "high"],
    ),
    ("google", "gemini-3.1-flash-lite"): (
        (0.25, 1.5, 0.025, 0),
        1_048_576,
        65_536,
        ["minimal", "low", "medium", "high"],
    ),
    ("google", "gemini-3.1-pro-preview"): (
        (2, 12, 0.2, 0),
        1_048_576,
        65_536,
        ["low", "medium", "high"],
    ),
    ("google-vertex", "gemini-3.5-flash"): (
        (1.5, 9, 0.15, 0),
        1_048_576,
        65_536,
        ["minimal", "low", "medium", "high"],
    ),
    ("google-vertex", "gemini-3.1-flash-lite"): (
        (0.25, 1.5, 0.025, 0),
        1_048_576,
        65_536,
        ["minimal", "low", "medium", "high"],
    ),
    ("google-vertex", "gemini-3.1-pro-preview"): (
        (2, 12, 0.2, 0),
        1_048_576,
        65_536,
        ["low", "medium", "high"],
    ),
    ("amazon-bedrock", "us.anthropic.claude-opus-5-5"): (
        (4.4, 22, 0.22, 5.5),
        1_000_000,
        128_000,
        _ALL,
    ),
    ("amazon-bedrock", "us.anthropic.claude-opus-5"): (
        (5.5, 27.5, 0.55, 6.875),
        1_000_000,
        128_000,
        _ALL,
    ),
    ("amazon-bedrock", "us.anthropic.claude-sonnet-5"): (
        (2.2, 11, 0.22, 2.75),
        1_000_000,
        128_000,
        _ALL,
    ),
    ("amazon-bedrock", "us.anthropic.claude-fable-5"): (
        (11, 55, 1.1, 13.75),
        1_000_000,
        128_000,
        _NO_OFF,
    ),
    ("amazon-bedrock", "us.anthropic.claude-opus-4-8"): (
        (5.5, 27.5, 0.55, 6.875),
        1_000_000,
        128_000,
        _ALL,
    ),
    ("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1"): (
        (5.5, 27.5, 0.55, 6.875),
        1_000_000,
        128_000,
        ["off", "minimal", "low", "medium", "high", "max"],
    ),
    ("amazon-bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0"): (
        (1.1, 5.5, 0.11, 1.375),
        200_000,
        64_000,
        _ALL[:5],
    ),
    ("azure-openai", "gpt-5.4"): (
        (2.5, 15, 0.25, 0),
        1_050_000,
        128_000,
        ["minimal", "low", "medium", "high", "xhigh"],
    ),
    ("openrouter", "moonshotai/kimi-k2.6"): (
        (0.65, 3.41, 0.15, 0),
        262_144,
        235_929,
        _ALL[:5],
    ),
    ("cloudflare", "@cf/moonshotai/kimi-k2.6"): (
        (0.95, 4, 0.16, 0),
        262_144,
        256_000,
        ["off", "high"],
    ),
}


def _row(provider: str, model_id: str) -> NativeModelSpec:
    row = build_builtin_catalog().find(provider, model_id)
    assert row is not None, f"{provider}/{model_id}"
    return row


# ---- catalog rows ------------------------------------------------------------


@pytest.mark.parametrize(("key", "expected"), sorted(PI_ROWS.items()))
def test_row_matches_pi_generated_metadata(
    key: tuple[str, str], expected: tuple[tuple[float, ...], int, int, list[str]]
) -> None:
    cost, context_window, max_tokens, levels = expected
    row = _row(*key)
    assert row.reasoning is True
    assert (
        row.cost.input,
        row.cost.output,
        row.cost.cache_read,
        row.cost.cache_write,
    ) == cost
    assert row.context_window == context_window
    assert row.max_tokens == max_tokens
    assert available_thinking_levels(row) == levels


def test_adaptive_anthropic_rows_carry_the_pi_compat_flag() -> None:
    for model_id in (
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-opus-4-8",
        "claude-opus-4-7",
    ):
        compat = _row("anthropic", model_id).compat
        assert isinstance(compat, Mapping)
        assert compat.get("forceAdaptiveThinking") is True, model_id
    for model_id in ("claude-sonnet-4-5", "claude-haiku-4-5"):
        compat = _row("anthropic", model_id).compat or {}
        assert "forceAdaptiveThinking" not in compat


def test_codex_gpt_5_4_is_retired_like_pi() -> None:
    catalog = build_builtin_catalog()
    assert catalog.find("openai-codex", "gpt-5.4") is None
    # the non-Codex GPT-5.4 rows Pi still ships stay
    assert catalog.find("openai", "gpt-5.4") is not None
    assert catalog.find("azure-openai", "gpt-5.4") is not None


# ---- defaults ------------------------------------------------------------------


def test_provider_defaults_match_pi_default_model_per_provider() -> None:
    assert default_model_per_provider == PI_DEFAULTS
    for provider, model_id in PI_DEFAULTS.items():
        assert DEFAULT_NATIVE_MODELS[provider] == model_id


@pytest.mark.parametrize(("provider", "model_id"), sorted(PI_DEFAULTS.items()))
def test_every_provider_default_resolves_to_a_catalog_row(
    provider: str, model_id: str
) -> None:
    assert build_builtin_catalog().find(provider, model_id) is not None


# ---- wire shapes -----------------------------------------------------------------


class _CapturingHTTPClient:
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
        return JsonResponse(status_code=500, body={"error": {"message": "stop"}})


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="p",
        model_id="m",
        cwd=tmp_path,
    )


_ENV = {
    "ANTHROPIC_API_KEY": "ak",
    "OPENAI_API_KEY": "ok",
    "AZURE_OPENAI_API_KEY": "zk",
    "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com",
    "GEMINI_API_KEY": "gk",
    "AWS_ACCESS_KEY_ID": "a",
    "AWS_SECRET_ACCESS_KEY": "s",
}


def _resolve(
    tmp_path: Path, provider: str, model_id: str, level: str | None
) -> ResolvedConstruction:
    return resolve_construction(
        _row(provider, model_id),
        store=AuthStore(path=tmp_path / "auth.json"),
        env=_ENV,
        runtime_api_key=None,
        models_json_auth=None,
        thinking_level=level,
    )


def _sent_body(
    tmp_path: Path, provider: str, model_id: str, level: str | None
) -> dict[str, Any]:
    http = _CapturingHTTPClient()
    resolved = _resolve(tmp_path, provider, model_id, level)
    build_provider(resolved, http_client=http).complete(_request(tmp_path))
    return http.requests[-1]["body"]


@pytest.mark.parametrize(
    ("model_id", "level", "thinking", "effort"),
    [
        # Opus/Sonnet 5.5 and Opus 4.7: adaptive + max passthrough.
        (
            "claude-opus-5-5",
            "max",
            {"type": "adaptive", "display": "summarized"},
            "max",
        ),
        (
            "claude-sonnet-5-5",
            "xhigh",
            {"type": "adaptive", "display": "summarized"},
            "xhigh",
        ),
        (
            "claude-opus-4-7",
            "max",
            {"type": "adaptive", "display": "summarized"},
            "max",
        ),
        # Fable: minimal is identity-available and maps to effort low (Pi
        # mapThinkingLevelToEffort).
        (
            "claude-fable-5",
            "minimal",
            {"type": "adaptive", "display": "summarized"},
            "low",
        ),
        # off:null rows: Pi's `map.off !== null` gate sends no thinking field.
        ("claude-fable-5", "off", None, None),
        ("claude-opus-5-5", "off", None, None),
        # sonnet-5 omits `off` -> explicit disabled.
        ("claude-sonnet-5", "off", {"type": "disabled"}, None),
        ("claude-opus-4-8", "off", {"type": "disabled"}, None),
    ],
)
def test_anthropic_thinking_wire(
    tmp_path: Path,
    model_id: str,
    level: str,
    thinking: dict[str, str] | None,
    effort: str | None,
) -> None:
    body = _sent_body(tmp_path, "anthropic", model_id, level)
    assert body.get("thinking") == thinking
    if effort is None:
        assert "output_config" not in body
    else:
        assert body["output_config"] == {"effort": effort}


@pytest.mark.parametrize(
    ("model_id", "level", "effort"),
    [
        ("us.anthropic.claude-opus-5-5", "xhigh", "xhigh"),
        ("us.anthropic.claude-sonnet-5", "max", "max"),
        ("us.anthropic.claude-fable-5", "max", "max"),
        ("us.anthropic.claude-opus-4-8", "minimal", "low"),
    ],
)
def test_bedrock_adaptive_thinking_wire(
    tmp_path: Path, model_id: str, level: str, effort: str
) -> None:
    from pipy_harness.native.providers.bedrock import AmazonBedrockProvider

    resolved = _resolve(tmp_path, "amazon-bedrock", model_id, level)
    http = _CapturingHTTPClient()
    AmazonBedrockProvider(
        model_id=model_id,
        region="us-east-1",
        access_key="AKIDEXAMPLE",
        secret_key="secret",
        reasoning_effort=resolved.reasoning_effort,
        http_client=http,
    ).complete(_request(tmp_path))
    body = http.requests[-1]["body"]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": effort}


def test_bedrock_adaptive_markers_match_pi_runtime_list() -> None:
    from pipy_harness.native.providers.bedrock import (
        bedrock_supports_adaptive_thinking,
    )

    for model_id in (
        "us.anthropic.claude-opus-5-5",
        "us.anthropic.claude-sonnet-5",
        "anthropic.claude-fable-5",
        "us.anthropic.claude-opus-4-6-v1",
        # normalized candidate: separators -> "-"
        "arn:aws:bedrock:us-east-1::claude.opus.5",
    ):
        assert bedrock_supports_adaptive_thinking(model_id), model_id
    for model_id in (
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        # Pi's bedrock runtime list has no Mythos marker
        "us.anthropic.claude-mythos-5",
    ):
        assert not bedrock_supports_adaptive_thinking(model_id), model_id


@pytest.mark.parametrize(
    ("provider", "model_id", "level", "reasoning"),
    [
        # off-state: Pi `map.off ?? "none"` without summary
        ("openai", "gpt-6-sol", "off", {"effort": "none"}),
        ("openai", "gpt-5.5", "off", {"effort": "none"}),
        # Astra maps off to null: no reasoning field
        ("openai", "gpt-6-astra", "off", None),
        ("openai", "gpt-6.1-sol", "off", None),
        # minimal: null clamps forward to low (Pi request-time clamp); an
        # on-state effort also sends summary "auto" (openai-responses.ts:343-352)
        ("openai", "gpt-6-sol", "minimal", {"effort": "low", "summary": "auto"}),
        ("openai", "gpt-6-luna", "max", {"effort": "max", "summary": "auto"}),
        ("openai", "gpt-6.1-sol", "minimal", {"effort": "low", "summary": "auto"}),
        ("openai", "gpt-6.1-sol", "max", {"effort": "max", "summary": "auto"}),
        # gpt-5.5 maps max to null: clamps back to xhigh
        ("openai", "gpt-5.5", "max", {"effort": "xhigh", "summary": "auto"}),
        # unset keeps the provider default
        ("openai", "gpt-6-sol", None, None),
        # Azure gpt-5.4 maps off to null
        ("azure-openai", "gpt-5.4", "off", None),
        ("azure-openai", "gpt-5.4", "xhigh", {"effort": "xhigh"}),
    ],
)
def test_responses_thinking_wire(
    tmp_path: Path,
    provider: str,
    model_id: str,
    level: str | None,
    reasoning: dict[str, str] | None,
) -> None:
    body = _sent_body(tmp_path, provider, model_id, level)
    assert body.get("reasoning") == reasoning
    # Pi requests the encrypted reasoning item with every on-state effort on
    # the Responses API; Azure's adapter is separate and unchanged.
    on_state = provider == "openai" and reasoning is not None and "summary" in reasoning
    assert ("include" in body) is on_state
    if on_state:
        assert body["include"] == ["reasoning.encrypted_content"]


@pytest.mark.parametrize(
    ("model_id", "level", "reasoning"),
    [
        # Pi off-state: effort without summary
        ("gpt-6-sol", "off", {"effort": "none"}),
        ("gpt-5.5", "off", {"effort": "none"}),
        ("gpt-5.6-sol", "off", {"effort": "none"}),
        # Astra maps off to null: the field is omitted
        ("gpt-6-astra", "off", None),
        # GPT-6.1 Sol rejects "none" (Pi 12c416e1a): the field is omitted
        ("gpt-6.1-sol", "off", None),
        # Codex GPT-6 maps minimal -> low (non-identity Pi mapping)
        ("gpt-6-sol", "minimal", {"summary": "auto", "effort": "low"}),
        ("gpt-6-astra", "max", {"summary": "auto", "effort": "max"}),
        ("gpt-6.1-sol", "minimal", {"summary": "auto", "effort": "low"}),
        ("gpt-6.1-sol", "medium", {"summary": "auto", "effort": "medium"}),
        ("gpt-6.1-sol", "max", {"summary": "auto", "effort": "max"}),
        # gpt-5.5 has no max: clamps to xhigh
        ("gpt-5.5", "max", {"summary": "auto", "effort": "xhigh"}),
        # unset keeps the pre-existing summary-only shape
        ("gpt-6-sol", None, {"summary": "auto"}),
    ],
)
def test_codex_thinking_wire(
    tmp_path: Path, model_id: str, level: str | None, reasoning: dict | None
) -> None:
    provider = build_openai_codex_provider(
        _row("openai-codex", model_id), level, ConstructionOptions()
    )
    body = _codex_request_body(provider, _request(tmp_path))  # type: ignore[arg-type]
    assert body.get("reasoning") == reasoning


@pytest.mark.parametrize(
    ("provider", "model_id", "level", "config"),
    [
        # gemini-3.1-pro maps minimal to null: clamps to low -> LOW
        (
            "google",
            "gemini-3.1-pro-preview",
            "minimal",
            {"includeThoughts": True, "thinkingLevel": "LOW"},
        ),
        (
            "google",
            "gemini-3.5-flash",
            "medium",
            {"includeThoughts": True, "thinkingLevel": "MEDIUM"},
        ),
        # off keeps the per-model disabled config (Pi getDisabledGoogleThinkingConfig)
        ("google", "gemini-3.5-flash", "off", {"thinkingLevel": "MINIMAL"}),
        ("google", "gemini-3.1-pro-preview", "off", {"thinkingLevel": "LOW"}),
        # xhigh maps to null on Gemini 3: clamps back to high
        (
            "google-vertex",
            "gemini-3.1-flash-lite",
            "xhigh",
            {"includeThoughts": True, "thinkingLevel": "HIGH"},
        ),
    ],
)
def test_google_thinking_wire(
    tmp_path: Path, provider: str, model_id: str, level: str, config: dict
) -> None:
    resolved = _resolve(tmp_path, provider, model_id, level)
    assert (
        _build_thinking_config(
            model_id, resolved.reasoning_effort, resolved.thinking_disabled
        )
        == config
    )


# ---- chrome meter ------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id", ["gpt-5.6-sol", "gpt-6-sol", "gpt-6.1-sol", "gpt-5.5"]
)
def test_codex_context_meter_is_272k(model_id: str) -> None:
    from pipy_harness.native.chrome import _context_budget_for

    budget = _context_budget_for("openai-codex", model_id)
    assert (budget.token_budget, budget.budget_label) == (272_000, "272k")


def test_footer_meter_uses_the_resolved_models_json_row(tmp_path: Path) -> None:
    import io
    import json

    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.chrome import _ChromeFooterEffects, _context_budget_for
    from pipy_harness.native.repl_state import (
        ModelRuntime,
        NativeModelSelection,
        NativeReplProviderState,
    )

    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "anthropic": {
                        "modelOverrides": {
                            "claude-sonnet-4-5": {"contextWindow": 200_000}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection("anthropic", "claude-sonnet-4-5"),
        model_runtime=ModelRuntime(
            catalog=ProviderCatalogState(
                models_json_path=models_json,
                auth_store=AuthStore(path=tmp_path / "auth.json"),
                env={"ANTHROPIC_API_KEY": "ak"},
                openai_codex_auth_path=tmp_path / "no-codex.json",
            )
        ),
        persist_defaults=False,
    )

    class _Runtime:
        runtime_label = "plain"

    effects = _ChromeFooterEffects(
        cwd=tmp_path,
        coding_state=None,  # type: ignore[arg-type]
        provider_state=state,
        error_stream=io.StringIO(),
        footer=None,
        repl_runtime=_Runtime(),
        session_tree=lambda: NativeSessionTree.create(tmp_path, persist=False),
    )
    declared = effects._declared_context_window("anthropic", "claude-sonnet-4-5")
    assert declared == 200_000
    budget = _context_budget_for(
        "anthropic", "claude-sonnet-4-5", declared_window=declared
    )
    assert (budget.token_budget, budget.budget_label) == (200_000, "200k")


@pytest.mark.parametrize(
    ("provider", "model_id", "window", "label"),
    [
        # the meter reads the catalog row, formatted like Pi's footer
        # formatTokens (footer.ts:25-31)
        ("anthropic", "claude-sonnet-5-5", 1_000_000, "1.0M"),
        ("anthropic", "claude-sonnet-4-5", 1_000_000, "1.0M"),
        ("anthropic", "claude-haiku-4-5", 200_000, "200k"),
        ("google", "gemini-3.5-flash", 1_048_576, "1.0M"),
        ("azure-openai", "gpt-5.4", 1_050_000, "1.1M"),
        # no built-in row: the safe 128k fallback
        ("custom", "my-model", 128_000, "128k"),
    ],
)
def test_context_meter_reads_the_catalog_row(
    provider: str, model_id: str, window: int, label: str
) -> None:
    from pipy_harness.native.chrome import _context_budget_for

    budget = _context_budget_for(provider, model_id)
    assert (budget.token_budget, budget.budget_label) == (window, label)


# ---- thinking level carried across a model switch (Pi setModel) -----------


@pytest.mark.parametrize(
    ("start", "level", "reference", "expected"),
    [
        # a stored max clamps back to xhigh on openai gpt-5.5 (max: null)
        (("openai-codex", "gpt-5.6-sol"), "max", "openai/gpt-5.5", "xhigh"),
        # off on a model that cannot switch thinking off clamps forward
        (
            ("anthropic", "claude-opus-4-8"),
            "off",
            "anthropic/claude-fable-5",
            "minimal",
        ),
        (("openai", "gpt-5.5"), "off", "google/gemini-3.5-flash", "minimal"),
        # a level the target supports is kept
        (("openai", "gpt-5.5"), "high", "anthropic/claude-opus-5-5", "high"),
        # an explicit :level suffix keeps pipy's pass-through
        (("openai", "gpt-5.5"), "high", "anthropic/claude-fable-5:off", "off"),
    ],
)
def test_model_switch_clamps_the_carried_level(
    tmp_path: Path,
    start: tuple[str, str],
    level: str,
    reference: str,
    expected: str,
) -> None:
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import (
        ModelRuntime,
        NativeModelSelection,
        NativeReplProviderState,
    )

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"ANTHROPIC_API_KEY": "ak", "OPENAI_API_KEY": "ok", "GEMINI_API_KEY": "g"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection(*start),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
        thinking_level=level,
    )
    prepared, message = state.prepare_model_mutation(
        state.capture_model_mutation_state(), reference
    )
    assert prepared is not None, message
    assert prepared.replacement.thinking_level == expected


# ---- startup thinking level (Pi sdk.ts:238-263 subset) -----------------------


def _catalog_state(tmp_path: Path) -> Any:
    from pipy_harness.native.catalog_state import ProviderCatalogState

    return ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"ANTHROPIC_API_KEY": "ak", "OPENAI_API_KEY": "ok"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )


def _settings(tmp_path: Path, **values: Any) -> Any:
    import json

    from pipy_harness.native.settings import SettingsManager

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return SettingsManager(global_path=path, env={})


def test_startup_thinking_level_precedence(tmp_path: Path) -> None:
    from pipy_harness.cli import _startup_thinking_level

    # Pi DEFAULT_THINKING_LEVEL when nothing names a level
    assert _startup_thinking_level(None, None) == "medium"
    assert _startup_thinking_level(None, _settings(tmp_path)) == "medium"
    configured = _settings(tmp_path, defaultThinkingLevel="high")
    # the settings default wins over Pi's default
    assert _startup_thinking_level(None, configured) == "high"
    # --thinking wins over the setting
    assert _startup_thinking_level("low", configured) == "low"


@pytest.mark.parametrize(
    ("provider", "model_id", "thinking", "effort"),
    [
        # fresh session on Pi's anthropic default: medium adaptive thinking
        ("anthropic", "claude-opus-4-8", None, "medium"),
        # Fable cannot switch thinking off: off clamps forward to minimal
        # (sent as adaptive effort low by the adapter)
        ("anthropic", "claude-fable-5", "off", "minimal"),
        # a non-reasoning model clamps to off: no thinking
        ("openai", "gpt-4o", "high", None),
    ],
)
def test_startup_level_is_clamped_to_the_startup_model(
    tmp_path: Path,
    provider: str,
    model_id: str,
    thinking: str | None,
    effort: str | None,
) -> None:
    from pipy_harness.cli import _run_provider_for_selection
    from pipy_harness.native.repl_state import NativeModelSelection

    built = _run_provider_for_selection(
        NativeModelSelection(provider, model_id),
        thinking=thinking,
        catalog_state=_catalog_state(tmp_path),
    )
    assert getattr(built, "reasoning_effort", None) == effort
    if provider == "anthropic":
        assert getattr(built, "force_adaptive_thinking", None) is True
        assert getattr(built, "thinking_disabled", None) is False


@pytest.mark.parametrize(
    ("provider", "model_id", "thinking", "expected"),
    [
        ("anthropic", "claude-opus-4-8", None, "medium"),
        ("anthropic", "claude-fable-5", "off", "minimal"),
        ("openai-codex", "gpt-6-astra", "off", "minimal"),
        # the deterministic fake is non-reasoning: clamps to off
        ("fake", "fake-tools", None, "off"),
    ],
)
def test_repl_startup_level_is_seeded_and_clamped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model_id: str,
    thinking: str | None,
    expected: str,
) -> None:
    from pipy_harness.cli import _tool_repl_adapter_for

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    adapter = _tool_repl_adapter_for(
        provider,
        model_id,
        cwd=tmp_path,
        tool_budget=5,
        thinking=thinking,
        catalog_state=_catalog_state(tmp_path),
    )
    state = adapter.provider_state
    assert state is not None
    assert state.current_thinking_level() == expected
