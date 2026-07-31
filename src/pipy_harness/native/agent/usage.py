"""Dependency-neutral accumulation of canonical provider usage telemetry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from pipy_harness.native.agent.results import AgentUsage


@dataclass(frozen=True, slots=True)
class AgentProviderUsageSample:
    """One provider turn's normalized token counters."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(
                    f"AgentProviderUsageSample.{field_name} must be an integer"
                )

    @classmethod
    def from_mapping(
        cls, usage: Mapping[str, object] | None
    ) -> AgentProviderUsageSample:
        """Normalize provider telemetry using the established coercion rules."""

        if usage is not None and not isinstance(usage, Mapping):
            raise TypeError("usage must be a mapping or None")
        if not usage:
            return cls()
        return cls(
            input_tokens=_coerce_int(usage.get("input_tokens")),
            output_tokens=_coerce_int(usage.get("output_tokens")),
            reasoning_tokens=_coerce_int(usage.get("reasoning_tokens")),
            cache_read_tokens=_coerce_int(usage.get("cached_tokens")),
            cache_write_tokens=_coerce_int(usage.get("cache_write_tokens")),
            total_tokens=_coerce_int(usage.get("total_tokens")),
        )

    @property
    def effective_total_tokens(self) -> int:
        """Return the explicit positive total or the established fallback."""

        if self.total_tokens > 0:
            return self.total_tokens
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


@dataclass(frozen=True, slots=True)
class AgentTokenPricing:
    """Per-million-token rates injected by the product composition layer."""

    input_per_million: float
    output_per_million: float
    reasoning_per_million: float
    cache_read_per_million: float = 0.0
    cache_write_per_million: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "input_per_million",
            "output_per_million",
            "reasoning_per_million",
            "cache_read_per_million",
            "cache_write_per_million",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"AgentTokenPricing.{field_name} must be numeric")
            if not isfinite(value) or value < 0:
                raise ValueError(
                    f"AgentTokenPricing.{field_name} must be finite and nonnegative"
                )
            object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True, slots=True)
class AgentUsageAccumulatorValue:
    """Detached immutable value for every accumulator-owned field."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    separate_cache_read_tokens: int
    separate_cache_write_tokens: int
    last_total_tokens: int
    cost_usd: float
    pricing: AgentTokenPricing | None

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "separate_cache_read_tokens",
            "separate_cache_write_tokens",
            "last_total_tokens",
        ):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an exact integer")
            if value < 0:
                raise ValueError(f"{field_name} must be nonnegative")
        if type(self.cost_usd) is not float:
            raise TypeError("cost_usd must be an exact float")
        if not isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("cost_usd must be finite and nonnegative")
        if self.pricing is not None and type(self.pricing) is not AgentTokenPricing:
            raise TypeError("pricing must be an exact AgentTokenPricing or None")


@dataclass(frozen=True, slots=True)
class AgentUsageReloadValue:
    """Immutable base for one prepared usage reload path."""


@dataclass(frozen=True, slots=True)
class AgentUsageRefreshValue(AgentUsageReloadValue):
    """Exact detached characterization retained by a refresh no-op."""

    retained: AgentUsageAccumulatorValue

    def __post_init__(self) -> None:
        if type(self.retained) is not AgentUsageAccumulatorValue:
            raise TypeError("retained must be an exact AgentUsageAccumulatorValue")


@dataclass(frozen=True, slots=True)
class AgentUsageFallbackValue(AgentUsageReloadValue):
    """Expected owner identity and detached accumulator installed by fallback."""

    expected_owner_token: object
    replacement: AgentUsageAccumulator

    def __post_init__(self) -> None:
        if type(self.expected_owner_token) is not object:
            raise TypeError("expected_owner_token must be an exact object")
        if type(self.replacement) is not AgentUsageAccumulator:
            raise TypeError("replacement must be an exact AgentUsageAccumulator")


class AgentUsageAccumulator:
    """Accumulate provider usage and project it into canonical agent usage."""

    __slots__ = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "separate_cache_read_tokens",
        "separate_cache_write_tokens",
        "last_total_tokens",
        "cost_usd",
        "_pricing",
        "_reload_identity",
    )

    def __init__(self, pricing: AgentTokenPricing | None = None) -> None:
        if pricing is not None and not isinstance(pricing, AgentTokenPricing):
            raise TypeError("pricing must be AgentTokenPricing or None")
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.separate_cache_read_tokens = 0
        self.separate_cache_write_tokens = 0
        self.last_total_tokens = 0
        self.cost_usd = 0.0
        self._pricing = pricing
        self._reload_identity = object()

    @property
    def cache_hit_percent(self) -> float | None:
        """Return cache reads as a percentage of effective input tokens."""

        denominator = float(
            self.input_tokens
            + self.separate_cache_read_tokens
            + self.separate_cache_write_tokens
        )
        if denominator <= 0:
            return None
        return 100.0 * self.cache_read_tokens / denominator

    def absorb(self, sample: AgentProviderUsageSample) -> None:
        """Accumulate one provider turn without changing its telemetry heuristic."""

        if not isinstance(sample, AgentProviderUsageSample):
            raise TypeError("sample must be AgentProviderUsageSample")
        input_tokens = sample.input_tokens
        output_tokens = sample.output_tokens
        reasoning_tokens = sample.reasoning_tokens
        cache_read_tokens = sample.cache_read_tokens
        cache_write_tokens = sample.cache_write_tokens
        total_tokens = sample.total_tokens
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.reasoning_tokens += reasoning_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens
        if _cache_counters_are_separate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            total_tokens=total_tokens,
        ):
            self.separate_cache_read_tokens += cache_read_tokens
            self.separate_cache_write_tokens += cache_write_tokens
        self.last_total_tokens = sample.effective_total_tokens
        if self._pricing is not None:
            self.cost_usd += _turn_cost(
                self._pricing,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )

    def agent_usage(self) -> AgentUsage:
        """Return the current counters in the canonical immutable shape."""

        return AgentUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cost_usd=self.cost_usd,
        )

    def prepare_reload_value_refresh(self) -> AgentUsageRefreshValue:
        """Detach exact usage while making refresh publication an explicit no-op."""

        return AgentUsageRefreshValue(retained=self._reload_value())

    def prepare_reload_value_fallback(
        self, replacement_prototype: AgentUsageAccumulator
    ) -> AgentUsageFallbackValue:
        """Build the authoritative cleared replacement from a cleared prototype.

        Live usage is intentionally irrelevant. Rejecting a pre-warmed prototype
        prevents silently discarding counters while retaining only its pricing.
        """

        if type(replacement_prototype) is not AgentUsageAccumulator:
            raise TypeError(
                "replacement_prototype must be an exact AgentUsageAccumulator"
            )
        replacement_value = replacement_prototype._reload_value()
        if not _usage_value_is_cleared(replacement_value):
            raise ValueError("fallback replacement prototype usage must be cleared")
        return AgentUsageFallbackValue(
            expected_owner_token=self._reload_identity,
            replacement=AgentUsageAccumulator(replacement_value.pricing),
        )

    def reload_value_matches_expected(self, value: AgentUsageReloadValue) -> bool:
        """Check fallback owner identity and the prepared cleared invariant."""

        if type(value) is AgentUsageRefreshValue:
            return True
        if type(value) is AgentUsageFallbackValue:
            if value.expected_owner_token is not self._reload_identity:
                return False
            try:
                return _usage_value_is_cleared(value.replacement._reload_value())
            except (AttributeError, TypeError, ValueError):
                return False
        return False

    def publish_reload_value_refresh(self, value: AgentUsageRefreshValue) -> None:
        """Retain live usage; the accepted value exists for family call symmetry."""

    def _reload_value(self) -> AgentUsageAccumulatorValue:
        return AgentUsageAccumulatorValue(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            separate_cache_read_tokens=self.separate_cache_read_tokens,
            separate_cache_write_tokens=self.separate_cache_write_tokens,
            last_total_tokens=self.last_total_tokens,
            cost_usd=self.cost_usd,
            pricing=self._pricing,
        )


def _usage_value_is_cleared(value: AgentUsageAccumulatorValue) -> bool:
    return (
        value.input_tokens == 0
        and value.output_tokens == 0
        and value.reasoning_tokens == 0
        and value.cache_read_tokens == 0
        and value.cache_write_tokens == 0
        and value.separate_cache_read_tokens == 0
        and value.separate_cache_write_tokens == 0
        and value.last_total_tokens == 0
        and value.cost_usd == 0.0
    )


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _cache_counters_are_separate(
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    total_tokens: int,
) -> bool:
    if cache_read_tokens <= 0 and cache_write_tokens <= 0:
        return False
    if total_tokens <= 0:
        return False
    minimum_total_if_separate = (
        input_tokens
        + output_tokens
        + reasoning_tokens
        + cache_read_tokens
        + cache_write_tokens
    )
    return total_tokens >= minimum_total_if_separate


def _turn_cost(
    pricing: AgentTokenPricing,
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    return (
        input_tokens * pricing.input_per_million
        + output_tokens * pricing.output_per_million
        + reasoning_tokens * pricing.reasoning_per_million
        + cache_read_tokens * pricing.cache_read_per_million
        + cache_write_tokens * pricing.cache_write_per_million
    ) / 1_000_000.0
