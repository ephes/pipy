"""Direct contracts for provider-neutral agent usage accumulation."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentTokenPricingTier,
    AgentUsageAccumulator,
    AgentUsageAccumulatorValue,
    AgentUsageFallbackValue,
    AgentUsageRefreshValue,
    AgentUsageReloadValue,
)

_RATE_FIELDS = (
    "input_per_million",
    "output_per_million",
    "cache_read_per_million",
    "cache_write_per_million",
)


def _pricing(**overrides: object) -> AgentTokenPricing:
    values: dict[str, object] = {
        "input_per_million": 1,
        "output_per_million": 2,
        "cache_read_per_million": 4,
        "cache_write_per_million": 5,
        **overrides,
    }
    return AgentTokenPricing(**values)  # type: ignore[arg-type]


def _sample(**usage: object) -> AgentProviderUsageSample:
    return AgentProviderUsageSample.from_mapping(usage)


def test_provider_usage_sample_is_frozen_slotted_and_runtime_validated() -> None:
    sample = AgentProviderUsageSample(input_tokens=3, total_tokens=3)

    assert not hasattr(sample, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(sample, "input_tokens", 4)
    with pytest.raises(TypeError, match="input_tokens must be an integer"):
        AgentProviderUsageSample(input_tokens=cast(int, True))


def test_provider_usage_sample_preserves_mapping_coercion_and_effective_total() -> None:
    sample = AgentProviderUsageSample.from_mapping(
        {
            "input_tokens": True,
            "output_tokens": 7,
            "reasoning_tokens": 3.9,
            "cached_tokens": "4",
            "cache_write_tokens": None,
            "total_tokens": 9.8,
        }
    )

    assert sample == AgentProviderUsageSample(
        output_tokens=7,
        reasoning_tokens=3,
        total_tokens=9,
        # Pi leaves ``Usage.reasoning`` undefined unless it was reported.
        reports_reasoning=True,
    )
    assert sample.effective_total_tokens == 9
    assert AgentProviderUsageSample.from_mapping(None).effective_total_tokens == 0
    assert _sample(input_tokens=4, output_tokens=2).effective_total_tokens == 6
    with pytest.raises(TypeError, match="usage must be a mapping or None"):
        AgentProviderUsageSample.from_mapping(cast(Mapping[str, object], []))


def test_token_pricing_is_normalized_immutable_and_has_zero_cache_defaults() -> None:
    pricing = AgentTokenPricing(input_per_million=1, output_per_million=2.5)

    assert pricing == AgentTokenPricing(1.0, 2.5, 0.0, 0.0, ())
    assert all(
        isinstance(getattr(pricing, field_name), float) for field_name in _RATE_FIELDS
    )
    assert pricing.tiers == ()
    assert "reasoning_per_million" not in AgentTokenPricing.__dataclass_fields__
    with pytest.raises(FrozenInstanceError):
        setattr(pricing, "input_per_million", 99.0)


@pytest.mark.parametrize("field_name", _RATE_FIELDS)
@pytest.mark.parametrize("invalid", [True, "1"])
def test_token_pricing_rejects_non_numeric_fields(
    field_name: str, invalid: object
) -> None:
    with pytest.raises(TypeError, match="must be numeric"):
        _pricing(**{field_name: invalid})
    with pytest.raises(TypeError, match="must be numeric"):
        replace(_tier(), **{field_name: cast(float, invalid)})


@pytest.mark.parametrize("field_name", _RATE_FIELDS)
@pytest.mark.parametrize("invalid", [-0.1, float("nan"), float("inf")])
def test_token_pricing_rejects_negative_or_nonfinite_fields(
    field_name: str, invalid: float
) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        _pricing(**{field_name: invalid})
    with pytest.raises(ValueError, match="finite and nonnegative"):
        replace(_tier(), **{field_name: invalid})


def test_token_pricing_tiers_must_be_a_tuple_of_tiers() -> None:
    with pytest.raises(TypeError, match="tuple of AgentTokenPricingTier"):
        _pricing(tiers=[_tier()])
    with pytest.raises(TypeError, match="tuple of AgentTokenPricingTier"):
        _pricing(tiers=({"input_tokens_above": 1},))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        replace(_tier(), input_tokens_above=-1)


def _tier(above: float = 100, input_rate: float = 10) -> AgentTokenPricingTier:
    return AgentTokenPricingTier(
        input_tokens_above=above,
        input_per_million=input_rate,
        output_per_million=20,
        cache_read_per_million=40,
        cache_write_per_million=50,
    )


def test_usage_coercion_accepts_int_and_float_but_not_bool_or_non_number() -> None:
    usage = AgentUsageAccumulator()

    usage.absorb(
        _sample(
            input_tokens=True,
            output_tokens=7,
            reasoning_tokens=3.9,
            cached_tokens="4",
            cache_write_tokens=None,
            total_tokens=9.8,
        )
    )

    assert usage.agent_usage() == AgentUsage(output_tokens=7, reasoning_tokens=3)
    assert usage.last_total_tokens == 9


def test_empty_missing_and_unrecognized_usage_keep_the_last_context_total() -> None:
    # Pi ``getAssistantUsage`` skips usage whose context tokens are zero.
    usage = AgentUsageAccumulator()
    usage.absorb(_sample(input_tokens=8, output_tokens=2))
    assert usage.last_total_tokens == 10

    for payload in (None, {}, {"provider_specific": 99}):
        usage.absorb(AgentProviderUsageSample.from_mapping(payload))
        assert usage.last_total_tokens == 10
        assert usage.agent_usage() == AgentUsage(input_tokens=8, output_tokens=2)


def test_failed_response_usage_counts_cost_but_not_context() -> None:
    # Pi never takes context usage from an ``error`` assistant message.
    usage = AgentUsageAccumulator()
    usage.absorb(_sample(input_tokens=8, output_tokens=2))
    usage.absorb(_sample(input_tokens=5, output_tokens=1), counts_for_context=False)
    assert usage.last_total_tokens == 10
    assert usage.agent_usage() == AgentUsage(input_tokens=13, output_tokens=3)


def test_accumulator_accepts_only_typed_samples_without_partial_mutation() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(_sample(input_tokens=8, output_tokens=2))

    with pytest.raises(TypeError, match="sample must be AgentProviderUsageSample"):
        usage.absorb(cast(AgentProviderUsageSample, {"input_tokens": 99}))

    assert usage.agent_usage() == AgentUsage(input_tokens=8, output_tokens=2)
    assert usage.last_total_tokens == 10


def test_usage_accumulates_canonical_counters_across_turns() -> None:
    usage = AgentUsageAccumulator()

    usage.absorb(
        _sample(
            input_tokens=10,
            output_tokens=2,
            reasoning_tokens=1,
            cached_tokens=3,
            cache_write_tokens=4,
        )
    )
    usage.absorb(
        _sample(
            input_tokens=7,
            output_tokens=5,
            reasoning_tokens=2,
            cached_tokens=1,
            cache_write_tokens=6,
        )
    )

    assert usage.agent_usage() == AgentUsage(
        input_tokens=17,
        output_tokens=7,
        reasoning_tokens=3,
        cache_read_tokens=4,
        cache_write_tokens=10,
    )


def test_last_total_prefers_positive_explicit_total_and_otherwise_falls_back() -> None:
    usage = AgentUsageAccumulator()

    usage.absorb(
        _sample(input_tokens=5, output_tokens=3, reasoning_tokens=2, total_tokens=42)
    )
    assert usage.last_total_tokens == 42
    usage.absorb(
        _sample(input_tokens=4, output_tokens=2, reasoning_tokens=1, total_tokens=0)
    )
    assert usage.last_total_tokens == 7
    usage.absorb(_sample(input_tokens=3, output_tokens=2))
    assert usage.last_total_tokens == 5


def test_openai_subset_cache_uses_input_tokens_as_denominator() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(
        _sample(
            input_tokens=100,
            output_tokens=20,
            cached_tokens=80,
            total_tokens=120,
        )
    )

    assert usage.cache_hit_percent == 80.0


@pytest.mark.parametrize(
    ("payload", "expected_percent"),
    [
        (
            {
                "input_tokens": 7,
                "cached_tokens": 2,
                "cache_write_tokens": 4,
                "total_tokens": 13,
            },
            100.0 * 2 / 13,
        ),
        (
            {"input_tokens": 100, "cached_tokens": 80, "total_tokens": 180},
            100.0 * 80 / 180,
        ),
        (
            {"input_tokens": 0, "cached_tokens": 20, "total_tokens": 20},
            100.0,
        ),
    ],
)
def test_separate_cache_counters_use_anthropic_style_denominator(
    payload: dict[str, int], expected_percent: float
) -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(AgentProviderUsageSample.from_mapping(payload))

    assert usage.cache_hit_percent == pytest.approx(expected_percent)


def test_injected_token_pricing_accumulates_each_counter_cost() -> None:
    """Anthropic-style sample: cache counters sit beside input in the total."""

    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(
        _sample(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cached_tokens=1_000_000,
            cache_write_tokens=1_000_000,
            total_tokens=4_000_000,
        )
    )

    # 1 input + 2 output + 4 cache read + 5 cache write.
    assert usage.cost_usd == 12.0
    assert usage.agent_usage().cost_usd == 12.0
    assert usage.uncached_input_tokens == 1_000_000


def test_inclusive_cache_counters_are_not_charged_twice() -> None:
    """OpenAI-style sample: input_tokens already contains the cache counters.

    Pi subtracts cache reads and writes from input before pricing
    (``openai-responses-shared.ts``); reasoning is part of output and has no
    rate of its own.
    """

    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(
        _sample(
            input_tokens=3_000_000,
            output_tokens=1_000_000,
            reasoning_tokens=600_000,
            cached_tokens=1_500_000,
            cache_write_tokens=500_000,
            total_tokens=4_000_000,
        )
    )

    assert usage.uncached_input_tokens == 1_000_000
    # 1M uncached * 1 + 1M output * 2 + 1.5M read * 4 + 0.5M write * 5.
    assert usage.cost_usd == pytest.approx(1.0 + 2.0 + 6.0 + 2.5)


def test_separate_cache_sample_with_reasoning_stays_separate() -> None:
    """Reasoning is inside output, so it must not push a sample to inclusive."""

    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(
        _sample(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            reasoning_tokens=500_000,
            cached_tokens=1_000_000,
            total_tokens=3_000_000,
        )
    )

    assert usage.uncached_input_tokens == 1_000_000
    assert usage.separate_cache_read_tokens == 1_000_000
    assert usage.cost_usd == pytest.approx(1.0 + 2.0 + 4.0)


def test_missing_total_counts_cache_as_inside_input() -> None:
    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(_sample(input_tokens=500, output_tokens=0, cached_tokens=800))

    assert usage.uncached_input_tokens == 0
    assert usage.cost_usd == pytest.approx(800 * 4 / 1_000_000)


@pytest.mark.parametrize(
    ("prompt_tokens", "expected_input_rate"),
    [(100, 1.0), (101, 10.0), (1_001, 30.0)],
)
def test_tiers_price_the_whole_request_at_the_highest_exceeded_threshold(
    prompt_tokens: int, expected_input_rate: float
) -> None:
    """Pi ``calculateCost``: strict ``>`` on input+cacheRead+cacheWrite."""

    pricing = _pricing(tiers=(_tier(1_000, 30), _tier(100, 10)))
    usage = AgentUsageAccumulator(pricing=pricing)
    cached = prompt_tokens // 2

    usage.absorb(
        _sample(
            input_tokens=prompt_tokens - cached,
            cached_tokens=cached,
            total_tokens=prompt_tokens,
        )
    )

    read_rate = 4.0 if expected_input_rate == 1.0 else 40.0
    expected = (
        (prompt_tokens - cached) * expected_input_rate + cached * read_rate
    ) / 1_000_000
    assert usage.cost_usd == pytest.approx(expected)


def test_one_hour_cache_writes_use_the_tier_input_rate() -> None:
    pricing = _pricing(tiers=(_tier(100, 10),))
    usage = AgentUsageAccumulator(pricing=pricing)

    usage.absorb(
        AgentProviderUsageSample(
            input_tokens=50,
            cache_write_tokens=100,
            cache_write_1h_tokens=60,
            total_tokens=150,
        )
    )

    # 50 input * 10 + 40 short writes * 50 + 60 long writes * 2 * 10.
    assert usage.cost_usd == pytest.approx((500 + 2_000 + 1_200) / 1_000_000)


def test_reload_values_are_frozen_detached_and_preserve_exact_refresh_usage() -> None:
    usage = AgentUsageAccumulator(pricing=_pricing())
    usage.absorb(
        _sample(
            input_tokens=7,
            output_tokens=3,
            reasoning_tokens=2,
            cached_tokens=5,
            cache_write_tokens=1,
            total_tokens=18,
        )
    )

    prepared = usage.prepare_reload_value_refresh()

    assert isinstance(prepared, AgentUsageReloadValue)
    assert type(prepared) is AgentUsageRefreshValue
    assert [field.name for field in fields(prepared)] == ["retained"]
    assert type(prepared.retained) is AgentUsageAccumulatorValue
    before = prepared.retained
    usage.absorb(_sample(input_tokens=11, total_tokens=11))
    assert prepared.retained is before
    assert prepared.retained.input_tokens == 7
    assert usage.reload_value_matches_expected(prepared)
    usage.publish_reload_value_refresh(prepared)
    assert usage.agent_usage().input_tokens == 18
    with pytest.raises(FrozenInstanceError):
        setattr(prepared.retained, "input_tokens", 99)


def test_reload_fallback_detaches_exact_cleared_replacement() -> None:
    live = AgentUsageAccumulator(pricing=_pricing())
    live.absorb(_sample(input_tokens=4, output_tokens=2, total_tokens=6))
    replacement_pricing = AgentTokenPricing(2.0, 3.0, 4.0)
    replacement = AgentUsageAccumulator(replacement_pricing)

    prepared = live.prepare_reload_value_fallback(replacement)
    replacement.absorb(_sample(input_tokens=99, total_tokens=99))

    assert type(prepared) is AgentUsageFallbackValue
    assert type(prepared.expected_owner_token) is object
    assert prepared.expected_owner_token is not live
    assert prepared.replacement is not replacement
    assert prepared.replacement.agent_usage() == AgentUsage()
    assert prepared.replacement.last_total_tokens == 0
    assert prepared.replacement.cache_hit_percent is None
    assert live.reload_value_matches_expected(prepared)
    assert not AgentUsageAccumulator().reload_value_matches_expected(prepared)
    assert prepared.replacement.prepare_reload_value_refresh().retained.pricing is (
        replacement_pricing
    )
    with pytest.raises(TypeError, match="expected_owner_token must be an exact object"):
        AgentUsageFallbackValue(
            expected_owner_token=live,
            replacement=AgentUsageAccumulator(),
        )


def test_reload_fallback_rejects_noncleared_replacement_without_live_mutation() -> None:
    live = AgentUsageAccumulator(pricing=_pricing())
    live.absorb(_sample(input_tokens=4, total_tokens=4))
    before = live.prepare_reload_value_refresh().retained
    replacement = AgentUsageAccumulator()
    replacement.absorb(_sample(output_tokens=1, total_tokens=1))

    with pytest.raises(ValueError, match="replacement prototype usage must be cleared"):
        live.prepare_reload_value_fallback(replacement)

    assert live.prepare_reload_value_refresh().retained == before
    assert live.agent_usage() == AgentUsage(input_tokens=4, cost_usd=0.000004)
    assert live.last_total_tokens == 4


def test_reload_fallback_check_refuses_a_mutated_prepared_replacement() -> None:
    live = AgentUsageAccumulator()
    prepared = live.prepare_reload_value_fallback(AgentUsageAccumulator())

    prepared.replacement.absorb(_sample(input_tokens=1, total_tokens=1))

    assert not live.reload_value_matches_expected(prepared)


def test_reload_fallback_check_is_total_for_corrupted_replacement_state() -> None:
    live = AgentUsageAccumulator()
    prepared = live.prepare_reload_value_fallback(AgentUsageAccumulator())
    prepared.replacement.cost_usd = cast(float, "corrupt")

    assert not live.reload_value_matches_expected(prepared)


def test_reload_refresh_publisher_is_an_exact_no_op() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/agent/usage.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    refresh_publisher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "publish_reload_value_refresh"
    )
    assert [type(node) for node in refresh_publisher.body] == [ast.Expr]
    assert not any(
        isinstance(node, (ast.Call, ast.Assign, ast.AnnAssign, ast.AugAssign))
        for node in ast.walk(refresh_publisher)
    )


def test_reload_value_covers_every_accumulator_slot() -> None:
    value_fields = {field.name for field in fields(AgentUsageAccumulatorValue)}
    accumulator_fields = set(AgentUsageAccumulator.__slots__)

    assert value_fields - {"pricing"} == accumulator_fields - {
        "_pricing",
        "_reload_identity",
    }
    assert "pricing" in value_fields
    assert "_pricing" in accumulator_fields
    assert "_reload_identity" in accumulator_fields


def test_reload_expected_check_is_total_for_unknown_family_members() -> None:
    usage = AgentUsageAccumulator()

    assert not usage.reload_value_matches_expected(AgentUsageReloadValue())


def test_run_accumulators_are_independent() -> None:
    first_run = AgentUsageAccumulator()
    second_run = AgentUsageAccumulator()

    first_run.absorb(_sample(input_tokens=11, output_tokens=2))
    second_run.absorb(_sample(input_tokens=3, reasoning_tokens=5))

    assert first_run.agent_usage() == AgentUsage(input_tokens=11, output_tokens=2)
    assert second_run.agent_usage() == AgentUsage(input_tokens=3, reasoning_tokens=5)
    assert first_run.last_total_tokens == 13
    assert second_run.last_total_tokens == 8
