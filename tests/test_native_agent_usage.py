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
    AgentUsageAccumulator,
    AgentUsageAccumulatorValue,
    AgentUsageFallbackValue,
    AgentUsageRefreshValue,
    AgentUsageReloadValue,
)


def _pricing() -> AgentTokenPricing:
    return AgentTokenPricing(
        input_per_million=1,
        output_per_million=2,
        reasoning_per_million=3,
        cache_read_per_million=4,
        cache_write_per_million=5,
    )


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
    )
    assert sample.effective_total_tokens == 9
    assert AgentProviderUsageSample.from_mapping(None).effective_total_tokens == 0
    assert _sample(input_tokens=4, output_tokens=2).effective_total_tokens == 6
    with pytest.raises(TypeError, match="usage must be a mapping or None"):
        AgentProviderUsageSample.from_mapping(cast(Mapping[str, object], []))


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


def test_empty_missing_and_unrecognized_usage_reset_only_last_total() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(_sample(input_tokens=8, output_tokens=2))
    assert usage.last_total_tokens == 10

    for payload in (None, {}, {"provider_specific": 99}):
        usage.absorb(AgentProviderUsageSample.from_mapping(payload))
        assert usage.last_total_tokens == 0
        assert usage.agent_usage() == AgentUsage(input_tokens=8, output_tokens=2)


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
    usage = AgentUsageAccumulator(pricing=_pricing())

    usage.absorb(
        _sample(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            reasoning_tokens=1_000_000,
            cached_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
    )

    assert usage.cost_usd == 15.0
    assert usage.agent_usage().cost_usd == 15.0


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
