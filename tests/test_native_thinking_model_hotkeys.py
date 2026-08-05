"""Unit tests for Shift+Tab thinking-level cycling and model cycling logic.

These drive ``NativeToolReplSession`` helpers directly with a catalog-backed
provider state (no PTY) to pin the cycle order, the reasoning-support clamp, the
``thinking_level_change`` native-tree entry, and that no provider turn runs. The
observable footer/status behavior over a real PTY is covered separately.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.chrome import _ChromeFooterEffects
from pipy_harness.native.repl.view_actions import cycle_thinking_level_action
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree


def _state(tmp_path: Path, model_id: str) -> NativeReplProviderState:
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("openai", model_id),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )


def _session(state: NativeReplProviderState) -> NativeToolReplSession:
    return NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={},
        provider_state=state,
    )


def _tree(tmp_path: Path) -> NativeSessionTree:
    return NativeSessionTree.create(tmp_path, persist=False)


class _Runtime:
    runtime_label = "plain"


def _footer(session: NativeToolReplSession, tmp_path: Path) -> _ChromeFooterEffects:
    return _ChromeFooterEffects(
        cwd=tmp_path,
        coding_state=session._coding_state,
        provider_state=session.provider_state,
        error_stream=io.StringIO(),
        terminal_ui=None,
        repl_runtime=_Runtime(),
    )


def _codex_state(tmp_path: Path, model_id: str) -> NativeReplProviderState:
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("openai-codex", model_id),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )


def _cycle_mutation(
    state: NativeReplProviderState, tree: NativeSessionTree
) -> str | None:
    level = state.cycle_thinking_level()
    if level is not None:
        tree.append_thinking_level_change(level)
    return level


def _cycle(session, state, tree, count) -> list[str | None]:
    seen = []
    for _ in range(count):
        cycle_thinking_level_action(
            session.provider_state,
            terminal_ui=None,
            error_stream=cast(TextIO, io.StringIO()),
            cycle_thinking_level=lambda: _cycle_mutation(state, tree),
        )
        seen.append(state.current_thinking_level())
    return seen


class TestThinkingCycle:
    def test_cycles_through_pi_levels(self, tmp_path: Path) -> None:
        # gpt-5.5 maps xhigh, so the model-aware cycle now includes it (Pi's
        # getSupportedThinkingLevels), then wraps back to off.
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        tree = _tree(tmp_path)
        seen = _cycle(session, state, tree, 6)
        assert seen == ["minimal", "low", "medium", "high", "xhigh", "off"]

    def test_sol_cycle_reaches_xhigh_then_max(self, tmp_path: Path) -> None:
        state = _codex_state(tmp_path, "gpt-5.6-sol")
        session = _session(state)
        tree = _tree(tmp_path)
        seen = _cycle(session, state, tree, 7)
        assert seen == ["minimal", "low", "medium", "high", "xhigh", "max", "off"]

    def test_model_without_extended_levels_stops_at_high(self, tmp_path: Path) -> None:
        # gpt-5.1-codex maps only the ordinary tier — no xhigh/max appended.
        state = _codex_state(tmp_path, "gpt-5.1-codex")
        session = _session(state)
        tree = _tree(tmp_path)
        seen = _cycle(session, state, tree, 6)
        assert seen == ["minimal", "low", "medium", "high", "off", "minimal"]

    def test_appends_thinking_level_change_entry(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        tree = _tree(tmp_path)
        cycle_thinking_level_action(
            session.provider_state,
            terminal_ui=None,
            error_stream=cast(TextIO, io.StringIO()),
            cycle_thinking_level=lambda: _cycle_mutation(state, tree),
        )
        entries = [
            entry
            for entry in tree.entries
            if getattr(entry, "type", "") == "thinking_level_change"
        ]
        assert entries
        assert getattr(entries[-1], "thinking_level", None) == "minimal"

    def test_non_reasoning_model_reports_unsupported(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-4o")  # gpt-4o has reasoning=False
        session = _session(state)
        tree = _tree(tmp_path)
        err = io.StringIO()
        cycle_thinking_level_action(
            session.provider_state,
            terminal_ui=None,
            error_stream=cast(TextIO, err),
            cycle_thinking_level=lambda: _cycle_mutation(state, tree),
        )
        assert state.current_thinking_level() is None
        assert "does not support thinking" in err.getvalue()
        assert not [
            entry
            for entry in tree.entries
            if getattr(entry, "type", "") == "thinking_level_change"
        ]

    def test_footer_effort_label_reflects_runtime_level(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        footer = _footer(session, tmp_path)
        assert footer._effort_label("openai", "gpt-5.5") in {"high", "default"}
        state.assign_thinking_level("low")
        assert footer._effort_label("openai", "gpt-5.5") == "low"
