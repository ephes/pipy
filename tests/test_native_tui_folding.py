"""Tests for Ctrl+O tool-output expansion and Ctrl+T thinking-block folding."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.repl.view_actions import toggle_view_fold
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
)


def _ui(tmp_path: Path) -> TerminalUi:
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def _frame_text(ui: TerminalUi) -> str:
    return "\n".join(ui.components.screen.render_lines(width=88, height=24))


class TestThinkingFold:
    def test_hidden_reasoning_renders_default_label_not_body(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        ui.components.transcript.set_thinking_hidden(True)
        ui.components.transcript.append_reasoning("SECRET-THOUGHT")
        frame = _frame_text(ui)
        assert "SECRET-THOUGHT" not in frame
        assert "Thinking..." in frame

    def test_hidden_reasoning_uses_custom_label_and_resets(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        ui.components.transcript.set_thinking_hidden(True)
        ui.components.transcript.append_reasoning("SECRET-THOUGHT")
        ui.components.transcript.set_hidden_thinking_label("Still thinking")
        assert "Still thinking" in _frame_text(ui)
        assert "SECRET-THOUGHT" not in _frame_text(ui)
        ui.components.transcript.set_hidden_thinking_label()
        assert "Thinking..." in _frame_text(ui)

    def test_visible_reasoning_rendered_live(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.components.transcript.set_thinking_hidden(False)
        ui.components.transcript.append_reasoning("VISIBLE-THOUGHT")
        assert "VISIBLE-THOUGHT" in _frame_text(ui)

    def test_settled_reasoning_shows_the_label_when_hidden(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        ui.components.transcript.set_thinking_hidden(True)
        ui.components.transcript.append_reasoning("FOLDED-THOUGHT")
        ui.components.transcript.settle_reasoning()
        # Pi draws a hidden thinking block as its label, kept in the history.
        assert "FOLDED-THOUGHT" not in _frame_text(ui)
        assert "Thinking..." in _frame_text(ui)

    def test_unhiding_rerenders_settled_reasoning(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.components.transcript.set_thinking_hidden(True)
        ui.components.transcript.append_reasoning("WAS-HIDDEN")
        ui.components.transcript.settle_reasoning()
        assert "WAS-HIDDEN" not in _frame_text(ui)
        # Pi updateThinkingBlockVisibility re-renders the row in place.
        ui.components.transcript.set_thinking_hidden(False)
        assert "WAS-HIDDEN" in _frame_text(ui)
        assert "Thinking..." not in _frame_text(ui)


class TestToolExpansion:
    def test_expanded_shows_more_live_output(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        # 16 lines: more than the 12-line collapsed live tail, but few enough
        # that they all fit a tall frame when expanded.
        ui.components.transcript.append_tool_output(
            "\n".join(f"line{n:02d}" for n in range(16))
        )
        ui.components.transcript.set_tools_expanded(False)
        collapsed = "\n".join(ui.components.screen.render_lines(width=88, height=40))
        ui.components.transcript.set_tools_expanded(True)
        expanded = "\n".join(ui.components.screen.render_lines(width=88, height=40))
        # The earliest line is hidden in the collapsed live tail but shown when
        # expanded.
        assert "line00" not in collapsed
        assert "line00" in expanded


class TestToggleDispatch:
    def test_toggle_tools_flips_flag_and_reports(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        settings = SettingsManager.for_workspace(tmp_path)
        err = io.StringIO()
        toggle_view_fold(
            HOTKEY_TOGGLE_TOOLS,
            terminal_ui=ui,
            error_stream=cast(TextIO, err),
            settings=settings,
        )
        assert ui.components.transcript.tools_expanded is True

    def test_toggle_thinking_persists_to_settings(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        settings = SettingsManager.for_workspace(tmp_path)
        err = io.StringIO()
        toggle_view_fold(
            HOTKEY_TOGGLE_THINKING,
            terminal_ui=ui,
            error_stream=cast(TextIO, err),
            settings=settings,
        )
        assert ui.components.transcript.thinking_hidden is True
        # The persisted setting survives into a freshly loaded manager (so a new
        # session seeds the fold), proving cross-session persistence.
        fresh = SettingsManager.for_workspace(tmp_path)
        assert fresh.get_hide_thinking_block() is True
