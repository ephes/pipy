"""COST1: pricing from the resolved catalog row with Pi ``calculateCost``.

Pi prices every response from the request's model object (built-in row plus
``models.json`` overrides, ``packages/ai/src/models.ts`` ``calculateCost``) and
its footer shows ``$x.xxx`` only when the cost is non-zero or the provider is
used through a subscription, marked `` (sub)`` (``footer.ts:189-196``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native import FakeNativeProvider
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentTokenPricingTier,
    AgentUsageAccumulator,
)
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.chrome import (
    BottomStatusFields,
    _ChromeFooterEffects,
    format_bottom_status_line,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_types import (
    ExtensionOAuthConfig,
    ExtensionProvider,
    RegisteredProvider,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.providers.chat_completions_wire import (
    extract_chat_completions_usage,
    extract_mistral_usage,
)
from pipy_harness.native.providers.google_generate_content_wire import (
    extract_gemini_usage,
)
from pipy_harness.native.repl.turn_leaves import pricing_for
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree


def _catalog(
    tmp_path: Path,
    *,
    models_json: dict[str, object] | None = None,
    codex_logged_in: bool = False,
) -> ProviderCatalogState:
    tmp_path.mkdir(parents=True, exist_ok=True)
    models_path = tmp_path / "models.json"
    if models_json is not None:
        models_path.write_text(json.dumps(models_json), encoding="utf-8")
    codex_path = tmp_path / "openai-codex.json"
    if codex_logged_in:
        codex_path.write_text("{}", encoding="utf-8")
    return ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"ANTHROPIC_API_KEY": "test-only"},
        openai_codex_auth_path=codex_path,
    )


def _provider_state(
    tmp_path: Path,
    selection: NativeModelSelection,
    **catalog_options: object,
) -> NativeReplProviderState:
    return NativeReplProviderState(
        selection=selection,
        model_runtime=ModelRuntime(_catalog(tmp_path, **catalog_options)),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Row -> pricing
# --------------------------------------------------------------------------- #


def test_every_builtin_row_prices_from_its_catalog_cost(tmp_path: Path) -> None:
    state = _provider_state(
        tmp_path, NativeModelSelection("anthropic", "claude-opus-5-5")
    )
    for row in build_builtin_catalog().get_all():
        pricing = pricing_for(state, row.provider_name, row.model_id)
        assert pricing is not None, row
        assert pricing.input_per_million == row.cost.input
        assert pricing.output_per_million == row.cost.output
        assert pricing.cache_read_per_million == row.cost.cache_read
        assert pricing.cache_write_per_million == row.cost.cache_write


def test_claude_turn_has_a_pi_cost(tmp_path: Path) -> None:
    state = _provider_state(
        tmp_path, NativeModelSelection("anthropic", "claude-opus-5-5")
    )
    pricing = pricing_for(state, "anthropic", "claude-opus-5-5")
    usage = AgentUsageAccumulator(pricing)

    # extract_anthropic_usage shape: cache counters beside input in the total.
    usage.absorb(
        AgentProviderUsageSample(
            input_tokens=1_000,
            output_tokens=500,
            cache_read_tokens=10_000,
            cache_write_tokens=2_000,
            cache_write_1h_tokens=1_000,
            total_tokens=13_500,
        )
    )

    row = build_builtin_catalog().find("anthropic", "claude-opus-5-5")
    assert row is not None
    cost = row.cost
    expected = (
        1_000 * cost.input
        + 500 * cost.output
        + 10_000 * cost.cache_read
        + 1_000 * cost.cache_write
        + 1_000 * cost.input * 2
    ) / 1_000_000
    assert usage.cost_usd == pytest.approx(expected)
    assert usage.cost_usd > 0


def test_models_json_override_cost_and_tiers_price_the_session(tmp_path: Path) -> None:
    state = _provider_state(
        tmp_path,
        NativeModelSelection("anthropic", "claude-opus-5-5"),
        models_json={
            "providers": {
                "anthropic": {
                    "modelOverrides": {
                        "claude-opus-5-5": {
                            "cost": {
                                "input": 7,
                                "tiers": [
                                    {
                                        "inputTokensAbove": 200_000,
                                        "input": 14,
                                        "output": 40,
                                        "cacheRead": 1,
                                        "cacheWrite": 9,
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        },
    )

    pricing = pricing_for(state, "anthropic", "claude-opus-5-5")

    row = build_builtin_catalog().find("anthropic", "claude-opus-5-5")
    assert row is not None
    assert pricing == AgentTokenPricing(
        input_per_million=7,
        output_per_million=row.cost.output,
        cache_read_per_million=row.cost.cache_read,
        cache_write_per_million=row.cost.cache_write,
        tiers=(AgentTokenPricingTier(200_000, 14, 40, 1, 9),),
    )


def test_unknown_selection_on_static_state_prices_at_zero() -> None:
    static = StaticNativeReplProviderState(FakeNativeProvider())

    assert pricing_for(static, "nope", "missing") is None
    builtin = pricing_for(static, "anthropic", "claude-opus-5-5")
    assert builtin is not None and builtin.input_per_million > 0


def test_turn_leaves_has_no_hard_coded_pricing_table() -> None:
    import pipy_harness.native.repl.turn_leaves as turn_leaves

    assert not hasattr(turn_leaves, "PRICING_TABLE")


# --------------------------------------------------------------------------- #
# Subscription predicate and footer shape
# --------------------------------------------------------------------------- #


def test_subscription_is_a_stored_oauth_login_of_a_subscription_provider(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    assert not catalog.is_using_subscription("anthropic")
    catalog.auth_store.set("anthropic", {"type": "api_key", "key": "k"})  # type: ignore[union-attr]
    assert not catalog.is_using_subscription("anthropic")
    catalog.auth_store.set("anthropic", {"type": "oauth", "access": "a"})  # type: ignore[union-attr]
    assert catalog.is_using_subscription("anthropic")
    catalog.auth_store.set("github-copilot", {"type": "oauth", "access": "a"})  # type: ignore[union-attr]
    assert catalog.is_using_subscription("github-copilot")
    # An OAuth credential for a provider without a subscription login is not one.
    catalog.auth_store.set("openai", {"type": "oauth", "access": "a"})  # type: ignore[union-attr]
    assert not catalog.is_using_subscription("openai")


def test_codex_login_file_is_a_subscription(tmp_path: Path) -> None:
    assert not _catalog(tmp_path / "out").is_using_subscription("openai-codex")
    assert _catalog(tmp_path / "in", codex_logged_in=True).is_using_subscription(
        "openai-codex"
    )


def _registered(name: str, *, is_subscription: bool) -> RegisteredProvider:
    oauth = ExtensionOAuthConfig(
        name="SSO",
        login=lambda *_args: {},
        refresh_token=lambda credentials: credentials,
        get_api_key=lambda _credentials: "k",
        is_subscription=is_subscription,
    )
    return RegisteredProvider(
        ExtensionProvider(name, "m", ("m",), lambda _context: None, oauth),
        f"{name}.py",
    )


@pytest.mark.parametrize("is_subscription", [True, False])
def test_extension_oauth_provider_declares_subscription(
    tmp_path: Path, is_subscription: bool
) -> None:
    catalog = _catalog(tmp_path)
    catalog.set_extension_provider_contributions(
        (_registered("corp", is_subscription=is_subscription),), ()
    )
    assert not catalog.is_using_subscription("corp")
    catalog.auth_store.set("corp", {"type": "oauth", "access": "a"})  # type: ignore[union-attr]
    assert catalog.is_using_subscription("corp") is is_subscription


def _fields(cost_usd: float, using_subscription: bool) -> BottomStatusFields:
    return BottomStatusFields(
        cwd_label="",
        cost_usd=cost_usd,
        using_subscription=using_subscription,
        context_used_pct=1.0,
        context_budget_label="200k",
        context_budget_suffix="auto",
        provider_name="anthropic",
        model_id="claude-opus-5-5",
        effort_label="",
    )


@pytest.mark.parametrize(
    ("cost_usd", "using_subscription", "prefix"),
    [
        (0.0, False, "1.0%/200k (auto)"),
        (0.1234, False, "$0.123 1.0%/200k (auto)"),
        (0.0, True, "$0.000 (sub) 1.0%/200k (auto)"),
        (0.1234, True, "$0.123 (sub) 1.0%/200k (auto)"),
    ],
)
def test_footer_cost_segment_follows_pi(
    cost_usd: float, using_subscription: bool, prefix: str
) -> None:
    line = format_bottom_status_line(100, _fields(cost_usd, using_subscription))

    assert line.startswith(prefix)
    assert "(api)" not in line


class _Runtime:
    runtime_label = "plain"


def _footer(tmp_path: Path, provider_state: object) -> _ChromeFooterEffects:
    session = CodingSession(provider=FakeNativeProvider(supports_tool_calls=True))
    return _ChromeFooterEffects(
        cwd=tmp_path,
        coding_state=session._coding_state,
        provider_state=provider_state,  # type: ignore[arg-type]
        error_stream=None,  # type: ignore[arg-type]
        footer=None,
        repl_runtime=_Runtime(),  # type: ignore[arg-type]
        session_tree=lambda: NativeSessionTree.create(tmp_path, persist=False),
    )


def test_footer_subscription_follows_the_provider_state(tmp_path: Path) -> None:
    logged_in = _provider_state(
        tmp_path / "in",
        NativeModelSelection("openai-codex", "gpt-6-sol"),
        codex_logged_in=True,
    )
    logged_out = _provider_state(
        tmp_path / "out", NativeModelSelection("openai-codex", "gpt-6-sol")
    )
    static = StaticNativeReplProviderState(FakeNativeProvider())

    assert _footer(tmp_path, logged_in)._using_subscription("openai-codex")
    assert not _footer(tmp_path, logged_out)._using_subscription("openai-codex")
    assert not _footer(tmp_path, static)._using_subscription("openai-codex")
    # Pi: Kimi Coding is subscription-backed despite API-key auth.
    assert _footer(tmp_path, static)._using_subscription("kimi-coding")


# --------------------------------------------------------------------------- #
# RPC get_session_stats
# --------------------------------------------------------------------------- #


def test_rpc_session_stats_report_catalog_priced_tokens_and_cost(
    tmp_path: Path,
) -> None:
    """Pi ``SessionStats`` tokens/cost from the live session usage.

    ``tokens.input`` is Pi's uncached input (the cached part of an inclusive
    OpenAI-style prompt is a cache read) and ``cost`` prices the row's catalog
    rates with Pi ``calculateCost``.
    """

    from test_native_automation_rpc import _RpcClient
    from test_native_coding_session import _UsageScriptProvider

    usage = {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "reasoning_tokens": 50,
        "cached_tokens": 600,
        "total_tokens": 1_200,
    }
    provider = _UsageScriptProvider(
        ((usage, ()),), name="openai-codex", model_id="gpt-6-sol"
    )
    client = _RpcClient(tmp_path, provider=cast(ProviderPort, provider))
    try:
        client.send({"id": "prompt", "type": "prompt", "message": "price me"})
        client.wait_for(lambda record: record.get("id") == "prompt")
        client.wait_for(lambda record: record.get("type") == "agent_settled")
        client.send({"id": "stats", "type": "get_session_stats"})
        stats = client.wait_for(lambda record: record.get("id") == "stats")["data"]
    finally:
        client.close()

    row = build_builtin_catalog().find("openai-codex", "gpt-6-sol")
    assert row is not None
    assert stats["tokens"] == {
        "input": 400,
        "output": 200,
        "cacheRead": 600,
        "cacheWrite": 0,
        "total": 1_200,
    }
    assert stats["cost"] == pytest.approx(
        (400 * row.cost.input + 200 * row.cost.output + 600 * row.cost.cache_read)
        / 1_000_000
    )
    assert stats["cost"] > 0


# --------------------------------------------------------------------------- #
# Adapter usage normalization (the buckets Pi prices)
# --------------------------------------------------------------------------- #


def _cost(usage: dict[str, int | float], pricing: AgentTokenPricing) -> float:
    accumulator = AgentUsageAccumulator(pricing)
    accumulator.absorb(AgentProviderUsageSample.from_mapping(usage))
    return accumulator.cost_usd


_PRICING = AgentTokenPricing(
    input_per_million=1,
    output_per_million=10,
    cache_read_per_million=0.1,
    cache_write_per_million=2,
)


def test_gemini_thoughts_are_output_and_cached_content_is_a_cache_read() -> None:
    usage = extract_gemini_usage(
        {
            "promptTokenCount": 1_000,
            "candidatesTokenCount": 200,
            "thoughtsTokenCount": 300,
            "cachedContentTokenCount": 600,
            "totalTokenCount": 1_500,
        }
    )

    assert usage == {
        "input_tokens": 1_000,
        "output_tokens": 500,
        "reasoning_tokens": 300,
        "cached_tokens": 600,
        "total_tokens": 1_500,
    }
    # Pi: input 400 uncached, output 500, cacheRead 600.
    assert _cost(usage, _PRICING) == pytest.approx(
        (400 * 1 + 500 * 10 + 600 * 0.1) / 1_000_000
    )


def test_gemini_without_thoughts_or_cache_keeps_plain_counters() -> None:
    assert extract_gemini_usage(
        {"promptTokenCount": 31, "candidatesTokenCount": 9, "totalTokenCount": 40}
    ) == {"input_tokens": 31, "output_tokens": 9, "total_tokens": 40}
    assert extract_gemini_usage(None) == {}


@pytest.mark.parametrize(
    ("usage", "cached"),
    [
        ({"prompt_tokens_details": {"cached_tokens": 600}}, 600),
        ({"prompt_cache_hit_tokens": 500}, 500),
        ({"cached_tokens": 400}, 400),
        (
            {
                "prompt_tokens_details": {"cached_tokens": 600},
                "prompt_cache_hit_tokens": 1,
                "cached_tokens": 2,
            },
            600,
        ),
    ],
)
def test_chat_completions_cache_reads_follow_pi_precedence(
    usage: dict[str, object], cached: int
) -> None:
    normalized = extract_chat_completions_usage(
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 100,
            "total_tokens": 1_100,
            **usage,
        }
    )

    assert normalized["cached_tokens"] == cached
    assert _cost(normalized, _PRICING) == pytest.approx(
        ((1_000 - cached) * 1 + 100 * 10 + cached * 0.1) / 1_000_000
    )


def test_chat_completions_cache_writes_and_reasoning() -> None:
    normalized = extract_chat_completions_usage(
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 100,
            "total_tokens": 1_100,
            "prompt_tokens_details": {"cached_tokens": 300, "cache_write_tokens": 200},
            "completion_tokens_details": {"reasoning_tokens": 40},
        }
    )

    assert normalized == {
        "input_tokens": 1_000,
        "output_tokens": 100,
        "total_tokens": 1_100,
        "cached_tokens": 300,
        "cache_write_tokens": 200,
        "reasoning_tokens": 40,
    }
    # Pi: input = 1000 - 300 - 200; reasoning is already inside output.
    assert _cost(normalized, _PRICING) == pytest.approx(
        (500 * 1 + 100 * 10 + 300 * 0.1 + 200 * 2) / 1_000_000
    )


def test_chat_completions_top_level_reasoning_is_a_display_fallback() -> None:
    normalized = extract_chat_completions_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 3}
    )

    assert normalized["reasoning_tokens"] == 3
    # Reasoning is inside output and never priced on its own.
    assert _cost(normalized, _PRICING) == pytest.approx((10 * 1 + 5 * 10) / 1e6)


@pytest.mark.parametrize(
    ("usage", "cached"),
    [
        ({"promptTokensDetails": {"cachedTokens": 11}}, 11),
        ({"prompt_tokens_details": {"cached_tokens": 12}}, 12),
        ({"promptTokenDetails": {"cachedTokens": 13}}, 13),
        ({"prompt_token_details": {"cached_tokens": 14}}, 14),
        ({"numCachedTokens": 15}, 15),
        ({"num_cached_tokens": 16}, 16),
        (
            {
                "promptTokensDetails": {"cachedTokens": 11},
                "prompt_tokens_details": {"cached_tokens": 12},
                "num_cached_tokens": 16,
            },
            11,
        ),
    ],
)
def test_mistral_cached_prompt_tokens_follow_pi_order(
    usage: dict[str, object], cached: int
) -> None:
    assert extract_mistral_usage({"prompt_tokens": 100, **usage})["cached_tokens"] == (
        cached
    )


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_mistral_non_finite_cached_tokens_are_discarded(bad: float) -> None:
    usage = extract_mistral_usage(
        {"prompt_tokens": 100, "completion_tokens": 1, "numCachedTokens": bad}
    )

    assert usage == {"input_tokens": 100, "output_tokens": 1}


def test_mistral_cached_prompt_tokens_are_clamped_to_the_prompt() -> None:
    assert extract_mistral_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "num_cached_tokens": 500,
        }
    ) == {
        "input_tokens": 100,
        "output_tokens": 10,
        "total_tokens": 110,
        "cached_tokens": 100,
    }
    assert (
        extract_mistral_usage(
            {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 40},
                "num_cached_tokens": 90,
            }
        )["cached_tokens"]
        == 40
    )
