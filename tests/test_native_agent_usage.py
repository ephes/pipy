"""Direct contracts for provider-neutral agent usage accumulation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.usage import AgentTokenPricing, AgentUsageAccumulator


def _pricing() -> AgentTokenPricing:
    return AgentTokenPricing(
        input_per_million=1,
        output_per_million=2,
        reasoning_per_million=3,
        cache_read_per_million=4,
        cache_write_per_million=5,
    )


def test_token_pricing_is_normalized_immutable_and_has_zero_cache_defaults() -> None:
    pricing = AgentTokenPricing(
        input_per_million=1,
        output_per_million=2.5,
        reasoning_per_million=3,
    )

    assert pricing == AgentTokenPricing(1.0, 2.5, 3.0, 0.0, 0.0)
    assert all(
        isinstance(value, float)
        for value in (
            pricing.input_per_million,
            pricing.output_per_million,
            pricing.reasoning_per_million,
            pricing.cache_read_per_million,
            pricing.cache_write_per_million,
        )
    )
    with pytest.raises(FrozenInstanceError):
        setattr(pricing, "input_per_million", 99.0)


@pytest.mark.parametrize("field_name", AgentTokenPricing.__dataclass_fields__)
@pytest.mark.parametrize("invalid", [True, "1"])
def test_token_pricing_rejects_non_numeric_fields(
    field_name: str, invalid: object
) -> None:
    with pytest.raises(TypeError, match="must be numeric"):
        replace(_pricing(), **{field_name: cast(float, invalid)})


@pytest.mark.parametrize("field_name", AgentTokenPricing.__dataclass_fields__)
@pytest.mark.parametrize("invalid", [-0.1, float("nan"), float("inf")])
def test_token_pricing_rejects_negative_or_nonfinite_fields(
    field_name: str, invalid: float
) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        replace(_pricing(), **{field_name: invalid})


def test_usage_coercion_accepts_int_and_float_but_not_bool_or_non_number() -> None:
    usage = AgentUsageAccumulator()

    usage.absorb(
        {
            "input_tokens": True,
            "output_tokens": 7,
            "reasoning_tokens": 3.9,
            "cached_tokens": "4",
            "cache_write_tokens": None,
            "total_tokens": 9.8,
        }
    )

    assert usage.agent_usage() == AgentUsage(output_tokens=7, reasoning_tokens=3)
    assert usage.last_total_tokens == 9


def test_empty_missing_and_unrecognized_usage_reset_only_last_total() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb({"input_tokens": 8, "output_tokens": 2})
    assert usage.last_total_tokens == 10

    for payload in (None, {}, {"provider_specific": 99}):
        usage.absorb(payload)
        assert usage.last_total_tokens == 0
        assert usage.agent_usage() == AgentUsage(input_tokens=8, output_tokens=2)


def test_usage_accumulates_canonical_counters_across_turns() -> None:
    usage = AgentUsageAccumulator()

    usage.absorb(
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "reasoning_tokens": 1,
            "cached_tokens": 3,
            "cache_write_tokens": 4,
        }
    )
    usage.absorb(
        {
            "input_tokens": 7,
            "output_tokens": 5,
            "reasoning_tokens": 2,
            "cached_tokens": 1,
            "cache_write_tokens": 6,
        }
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
        {
            "input_tokens": 5,
            "output_tokens": 3,
            "reasoning_tokens": 2,
            "total_tokens": 42,
        }
    )
    assert usage.last_total_tokens == 42
    usage.absorb(
        {
            "input_tokens": 4,
            "output_tokens": 2,
            "reasoning_tokens": 1,
            "total_tokens": 0,
        }
    )
    assert usage.last_total_tokens == 7
    usage.absorb({"input_tokens": 3, "output_tokens": 2})
    assert usage.last_total_tokens == 5


def test_openai_subset_cache_uses_input_tokens_as_denominator() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_tokens": 80,
            "total_tokens": 120,
        }
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
    usage.absorb(payload)

    assert usage.cache_hit_percent == pytest.approx(expected_percent)


def test_injected_token_pricing_accumulates_each_counter_cost() -> None:
    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(
        {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "reasoning_tokens": 1_000_000,
            "cached_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000,
        }
    )

    assert usage.cost_usd == 15.0
    assert usage.agent_usage().cost_usd == 15.0


def test_run_accumulators_are_independent() -> None:
    first_run = AgentUsageAccumulator()
    second_run = AgentUsageAccumulator()

    first_run.absorb({"input_tokens": 11, "output_tokens": 2})
    second_run.absorb({"input_tokens": 3, "reasoning_tokens": 5})

    assert first_run.agent_usage() == AgentUsage(input_tokens=11, output_tokens=2)
    assert second_run.agent_usage() == AgentUsage(input_tokens=3, reasoning_tokens=5)
    assert first_run.last_total_tokens == 13
    assert second_run.last_total_tokens == 8
