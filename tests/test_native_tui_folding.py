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
    return "\n".join(ui._screen.render_lines(width=88, height=24))


class TestThinkingFold:
    def test_hidden_reasoning_renders_default_label_not_body(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(True)
        ui.append_reasoning("SECRET-THOUGHT")
        frame = _frame_text(ui)
        assert "SECRET-THOUGHT" not in frame
        assert "Thinking..." in frame

    def test_hidden_reasoning_uses_custom_label_and_resets(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(True)
        ui.append_reasoning("SECRET-THOUGHT")
        ui._transcript.set_hidden_thinking_label("Still thinking")
        assert "Still thinking" in _frame_text(ui)
        assert "SECRET-THOUGHT" not in _frame_text(ui)
        ui._transcript.set_hidden_thinking_label()
        assert "Thinking..." in _frame_text(ui)

    def test_visible_reasoning_rendered_live(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(False)
        ui.append_reasoning("VISIBLE-THOUGHT")
        assert "VISIBLE-THOUGHT" in _frame_text(ui)

    def test_settle_defers_reasoning_when_hidden(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(True)
        ui.append_reasoning("DEFER-ME")
        ui._transcript.settle_reasoning()
        # Not committed to scrollback while hidden, but retained (not dropped).
        assert all(
            "DEFER-ME" not in "".join(block)
            for _kind, block in ui._transcript.history_blocks
        )
        assert ui._transcript.deferred_reasoning == ["DEFER-ME"]

    def test_unhiding_reveals_deferred_reasoning(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(True)
        ui.append_reasoning("WAS-HIDDEN")
        ui._transcript.settle_reasoning()
        assert "WAS-HIDDEN" not in _frame_text(ui)
        # Toggling visibility back commits the deferred reasoning into history.
        ui.set_thinking_hidden(False)
        assert "WAS-HIDDEN" in _frame_text(ui)
        assert ui._transcript.deferred_reasoning == []


class TestToolExpansion:
    def test_expanded_shows_more_live_output(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        # 16 lines: more than the 12-line collapsed live tail, but few enough
        # that they all fit a tall frame when expanded.
        ui.append_tool_output("\n".join(f"line{n:02d}" for n in range(16)))
        ui.set_tools_expanded(False)
        collapsed = "\n".join(ui._screen.render_lines(width=88, height=40))
        ui.set_tools_expanded(True)
        expanded = "\n".join(ui._screen.render_lines(width=88, height=40))
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
        assert ui.tools_expanded is True

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
        assert ui.thinking_hidden is True
        # The persisted setting survives into a freshly loaded manager (so a new
        # session seeds the fold), proving cross-session persistence.
        fresh = SettingsManager.for_workspace(tmp_path)
        assert fresh.get_hide_thinking_block() is True
