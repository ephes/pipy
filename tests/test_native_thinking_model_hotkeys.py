"""Unit tests for Shift+Tab thinking-level cycling and the footer thinking label.

These drive the production :class:`ProviderMutationEffects` cycle (the same
callable the Shift+Tab hotkey and the ``/settings`` row use) with a
catalog-backed provider state (no PTY) to pin the cycle order, the
reasoning-support clamp, the ``thinking_level_change`` native-tree entry, that
the bound provider is rebuilt with the new level, and that no provider turn
runs. The observable footer/status behavior over a real PTY is covered
separately.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from test_native_coding_effects import _provider_mutation_fixture

from pipy_harness.native import FakeNativeProvider
from pipy_harness.native.chrome import (
    BottomStatusFields,
    _ChromeFooterEffects,
    format_bottom_status_line,
    thinking_footer_label,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.view_actions import cycle_thinking_level_action
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree


def _effects(
    tmp_path: Path, provider_name: str, model_id: str
) -> tuple[ProviderMutationEffects, NativeReplProviderState, NativeSessionTree]:
    effects, state, _tools, _ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(
            tmp_path, selection=NativeModelSelection(provider_name, model_id)
        )
    )
    return effects, state, tree


def _cycle(
    effects: ProviderMutationEffects, state: NativeReplProviderState, count: int
) -> list[str | None]:
    seen = []
    for _ in range(count):
        cycle_thinking_level_action(
            state,
            terminal_ui=None,
            error_stream=cast(TextIO, io.StringIO()),
            cycle_thinking_level=effects.cycle_thinking_level,
        )
        seen.append(state.current_thinking_level())
    return seen


def _thinking_entries(tree: NativeSessionTree) -> list[str]:
    return [
        getattr(entry, "thinking_level", "")
        for entry in tree.entries
        if getattr(entry, "type", "") == "thinking_level_change"
    ]


class _Runtime:
    runtime_label = "plain"


def _footer(
    session: CodingSession,
    tmp_path: Path,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
) -> _ChromeFooterEffects:
    return _ChromeFooterEffects(
        cwd=tmp_path,
        coding_state=session._coding_state,
        provider_state=provider_state,
        error_stream=io.StringIO(),
        footer=None,
        repl_runtime=_Runtime(),
    )


class TestThinkingCycle:
    def test_cycles_through_pi_levels(self, tmp_path: Path) -> None:
        # Pi's openai gpt-5.5 row maps xhigh and maps minimal/max to null, so
        # the model-aware cycle (Pi's getSupportedThinkingLevels) skips minimal,
        # includes xhigh, then wraps back to off.
        effects, state, _tree = _effects(tmp_path, "openai", "gpt-5.5")
        seen = _cycle(effects, state, 5)
        assert seen == ["low", "medium", "high", "xhigh", "off"]

    def test_sol_cycle_reaches_xhigh_then_max(self, tmp_path: Path) -> None:
        effects, state, _tree = _effects(tmp_path, "openai-codex", "gpt-5.6-sol")
        seen = _cycle(effects, state, 7)
        assert seen == ["minimal", "low", "medium", "high", "xhigh", "max", "off"]

    def test_model_without_extended_levels_stops_at_high(self, tmp_path: Path) -> None:
        # gpt-5.1-codex maps only the ordinary tier — no xhigh/max appended —
        # and maps off to None, so (Pi getSupportedThinkingLevels) off is not
        # offered either and the cycle wraps high -> minimal.
        effects, state, _tree = _effects(tmp_path, "openai-codex", "gpt-5.1-codex")
        seen = _cycle(effects, state, 5)
        assert seen == ["minimal", "low", "medium", "high", "minimal"]

    def test_appends_thinking_level_change_entry(self, tmp_path: Path) -> None:
        effects, state, tree = _effects(tmp_path, "openai", "gpt-5.5")
        _cycle(effects, state, 2)
        assert _thinking_entries(tree) == ["low", "medium"]

    def test_cycle_rebuilds_the_bound_provider_with_the_new_level(
        self, tmp_path: Path
    ) -> None:
        # Regression: the cycle used to assign only the state's level, so the
        # footer showed the new level while the bound provider kept sending
        # the effort it was constructed with.
        effects, state, _tree = _effects(tmp_path, "openai", "gpt-5.5")
        history_before = effects.coding_state.messages
        before = effects.coding_state.provider
        assert getattr(before, "reasoning_effort") is None
        _cycle(effects, state, 3)
        after = effects.coding_state.provider
        assert state.current_thinking_level() == "high"
        assert after is not before
        assert getattr(after, "reasoning_effort") == "high"
        assert effects.coding_state.messages == history_before

    def test_support_follows_the_resolved_row(self, tmp_path: Path) -> None:
        # The REPL fake runs as ``fake/fake-tools``, a row synthesized from the
        # fake provider's catalog base; a ``models.json`` override that makes
        # that base reasoning-capable must make the cycle (Pi
        # ``supportsThinking``: the resolved model's ``reasoning``) agree with
        # the levels and the footer label.
        (tmp_path / "models.json").write_text(
            '{"providers": {"fake": {"modelOverrides": '
            '{"fake-native-bootstrap": {"reasoning": true}}}}}',
            encoding="utf-8",
        )
        effects, state, tree = _effects(tmp_path, "fake", "fake-tools")
        seen = _cycle(effects, state, 2)
        assert seen == ["minimal", "low"]
        assert _thinking_entries(tree) == ["minimal", "low"]

    def test_non_reasoning_model_reports_unsupported(self, tmp_path: Path) -> None:
        effects, state, tree = _effects(tmp_path, "openai", "gpt-4o")
        before = effects.coding_state.provider
        err = io.StringIO()
        cycle_thinking_level_action(
            state,
            terminal_ui=None,
            error_stream=cast(TextIO, err),
            cycle_thinking_level=effects.cycle_thinking_level,
        )
        assert state.current_thinking_level() is None
        assert "does not support thinking" in err.getvalue()
        assert _thinking_entries(tree) == []
        assert effects.coding_state.provider is before


class TestFooterThinkingLabel:
    def test_label_rule_matches_pi_footer(self) -> None:
        assert thinking_footer_label(reasoning=False, level="high") == ""
        assert thinking_footer_label(reasoning=True, level=None) == "thinking off"
        assert thinking_footer_label(reasoning=True, level="off") == "thinking off"
        assert thinking_footer_label(reasoning=True, level="xhigh") == "xhigh"

    def test_footer_reflects_live_level_for_reasoning_row(self, tmp_path: Path) -> None:
        effects, state, _tree = _effects(tmp_path, "openai", "gpt-5.5")
        session = CodingSession(
            provider=FakeNativeProvider(supports_tool_calls=True),
            tool_registry={},
            provider_state=state,
        )
        footer = _footer(session, tmp_path, state)
        assert footer._effort_label("openai", "gpt-5.5") == "thinking off"
        state.assign_thinking_level("low")
        assert footer._effort_label("openai", "gpt-5.5") == "low"

    def test_non_reasoning_row_has_no_segment(self, tmp_path: Path) -> None:
        _effects_value, state, _tree = _effects(tmp_path, "openai", "gpt-4o")
        session = CodingSession(
            provider=FakeNativeProvider(supports_tool_calls=True),
            tool_registry={},
            provider_state=state,
        )
        state.assign_thinking_level("high")
        footer = _footer(session, tmp_path, state)
        assert footer._effort_label("openai", "gpt-4o") == ""

    def test_injected_provider_falls_back_to_builtin_row(self, tmp_path: Path) -> None:
        session = CodingSession(provider=FakeNativeProvider(supports_tool_calls=True))
        static = StaticNativeReplProviderState(FakeNativeProvider())
        footer = _footer(session, tmp_path, static)
        assert footer._effort_label("fake", "fake-native-bootstrap") == ""
        assert footer._effort_label("openai-codex", "gpt-5.5") == "thinking off"

    def test_status_line_omits_empty_thinking_segment(self) -> None:
        fields = BottomStatusFields(
            cwd_label="",
            cost_label="$0.000",
            plan_label="api",
            context_used_pct=0.0,
            context_budget_label="128k",
            context_budget_suffix="auto",
            provider_name="fake",
            model_id="fake-native-bootstrap",
            effort_label="",
        )
        line = format_bottom_status_line(80, fields)
        assert line.endswith("(fake) fake-native-bootstrap")
        assert "•" not in line
