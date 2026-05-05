"""Privacy-safe provider usage metadata normalization."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypeGuard

ProviderUsage = dict[str, int | float]

NORMALIZED_PROVIDER_USAGE_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


def normalize_provider_usage(value: Mapping[str, Any] | None) -> ProviderUsage:
    """Return only allowlisted provider usage counters.

    Unknown, unavailable, negative, and non-finite counters are omitted rather
    than guessed. Boolean values are rejected because bool is an int subclass
    but not a counter.
    """

    if not value:
        return {}

    usage: ProviderUsage = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        item = value.get(key)
        if _is_counter(item):
            usage[key] = item
    return usage


def _is_counter(value: object) -> TypeGuard[int | float]:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, float) and math.isfinite(value) and value >= 0.0
