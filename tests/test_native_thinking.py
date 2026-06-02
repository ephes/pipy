"""Tests for thinking-level validation + per-model mapping (M5)."""

from __future__ import annotations

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.thinking import (
    map_thinking_level,
    supported_thinking_levels,
    validate_thinking_level,
)


def _model(reasoning: bool, thinking: dict | None) -> NativeModelSpec:
    return NativeModelSpec(
        provider_name="openai",
        model_id="m",
        display_name="m",
        api="openai-responses",
        reasoning=reasoning,
        thinking_level_map=thinking or {},
    )


def test_validate_accepts_six_levels():
    for level in ("off", "minimal", "low", "medium", "high", "xhigh"):
        value, warning = validate_thinking_level(level)
        assert value == level and warning is None


def test_validate_warns_on_invalid():
    value, warning = validate_thinking_level("turbo")
    assert value is None
    assert warning is not None and "turbo" in warning


def test_map_off_returns_none():
    model = _model(True, {"high": "high"})
    assert map_thinking_level(model, "off") is None
    assert map_thinking_level(model, None) is None


def test_non_reasoning_model_ignores_level():
    model = _model(False, None)
    assert map_thinking_level(model, "high") is None


def test_maps_through_thinking_level_map():
    model = _model(True, {"low": "low", "high": "xhigh"})
    assert map_thinking_level(model, "high") == "xhigh"


def test_xhigh_only_when_mapped():
    no_xhigh = _model(True, {"high": "high"})
    assert map_thinking_level(no_xhigh, "xhigh") is None
    with_xhigh = _model(True, {"xhigh": "xhigh"})
    assert map_thinking_level(with_xhigh, "xhigh") == "xhigh"


def test_reasoning_model_without_map_passes_standard_levels_through():
    model = _model(True, None)
    assert map_thinking_level(model, "high") == "high"
    # xhigh not available without an explicit map
    assert map_thinking_level(model, "xhigh") is None


def test_supported_levels_derived_from_map():
    model = _model(True, {"low": "low", "high": "xhigh", "off": None})
    assert supported_thinking_levels(model) == {"low", "high"}
