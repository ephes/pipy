"""Tests for thinking-level validation + per-model mapping (M5)."""

from __future__ import annotations

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.thinking import (
    available_thinking_levels,
    clamp_thinking_level,
    map_thinking_level,
    resolve_codex_effort,
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


def test_validate_accepts_seven_levels():
    for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
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


# ---- available_thinking_levels / clamp (Pi getSupportedThinkingLevels) -------

_SOL_MAP = {
    "off": None, "minimal": "low", "low": "low", "medium": "medium",
    "high": "high", "xhigh": "xhigh", "max": "max",
}
_XHIGH_MAP = {
    "off": None, "minimal": "minimal", "low": "low", "medium": "medium",
    "high": "high", "xhigh": "xhigh",
}


def test_available_levels_non_reasoning_is_off_only():
    assert available_thinking_levels(_model(False, None)) == ["off"]


def test_available_levels_ordinary_tier_for_partial_map():
    # A model mapping only xhigh still offers the ordinary tier (identity),
    # matching Pi — pipy must not report xhigh-only.
    levels = available_thinking_levels(_model(True, {"xhigh": "xhigh"}))
    assert levels == ["off", "minimal", "low", "medium", "high", "xhigh"]


def test_available_levels_appends_max_only_when_mapped():
    assert available_thinking_levels(_model(True, _SOL_MAP)) == [
        "off", "minimal", "low", "medium", "high", "xhigh", "max",
    ]
    assert "max" not in available_thinking_levels(_model(True, _XHIGH_MAP))


def test_clamp_walks_forward_then_backward():
    xhigh_model = _model(True, _XHIGH_MAP)
    # max unsupported -> nearest available walking back is xhigh
    assert clamp_thinking_level(xhigh_model, "max") == "xhigh"
    ordinary = _model(True, {"off": None, "minimal": "minimal", "low": "low",
                             "medium": "medium", "high": "high"})
    assert clamp_thinking_level(ordinary, "xhigh") == "high"
    assert clamp_thinking_level(ordinary, "max") == "high"
    assert clamp_thinking_level(ordinary, "off") == "off"
    assert clamp_thinking_level(ordinary, "low") == "low"


def test_resolve_codex_effort_clamp_then_map():
    sol = _model(True, _SOL_MAP)
    assert resolve_codex_effort(sol, "max") == "max"
    assert resolve_codex_effort(sol, "minimal") == "low"  # non-identity Pi map
    assert resolve_codex_effort(sol, "off") is None
    assert resolve_codex_effort(sol, None) is None
    # stored max on an xhigh-only model clamps to xhigh (Pi request-path clamp)
    assert resolve_codex_effort(_model(True, _XHIGH_MAP), "max") == "xhigh"
