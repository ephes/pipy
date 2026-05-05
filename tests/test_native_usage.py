from __future__ import annotations

from pipy_harness.native import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage


def test_normalize_provider_usage_keeps_only_finite_allowlisted_counters():
    usage = normalize_provider_usage(
        {
            "input_tokens": 10,
            "output_tokens": 2.5,
            "total_tokens": float("nan"),
            "cached_tokens": -1,
            "reasoning_tokens": 1,
            "extra_bool": True,
            "input_characters": 999,
            "raw_usage": "SHOULD_NOT_PERSIST",
        }
    )

    assert usage == {
        "input_tokens": 10,
        "output_tokens": 2.5,
        "reasoning_tokens": 1,
    }


def test_normalized_provider_usage_key_order_is_stable():
    assert NORMALIZED_PROVIDER_USAGE_KEYS == (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    )
