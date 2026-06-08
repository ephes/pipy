"""Tests for Ctrl+O tool-output expansion and Ctrl+T thinking-block folding."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tui import (
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
    ToolLoopTerminalUi,
)


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def _frame_text(ui: ToolLoopTerminalUi) -> str:
    return "\n".join(ui.render_lines(width=88, height=24))


class TestThinkingFold:
    def test_hidden_reasoning_not_rendered_live(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.thinking_hidden = True
        ui.reasoning_text = "SECRET-THOUGHT"
        assert "SECRET-THOUGHT" not in _frame_text(ui)

    def test_visible_reasoning_rendered_live(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.thinking_hidden = False
        ui.reasoning_text = "VISIBLE-THOUGHT"
        assert "VISIBLE-THOUGHT" in _frame_text(ui)

    def test_settle_defers_reasoning_when_hidden(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.thinking_hidden = True
        ui.reasoning_text = "DEFER-ME"
        ui._settle_reasoning()
        # Not committed to scrollback while hidden, but retained (not dropped).
        assert all("DEFER-ME" not in "".join(block) for _kind, block in ui._history_blocks)
        assert ui._deferred_reasoning == ["DEFER-ME"]

    def test_unhiding_reveals_deferred_reasoning(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.set_thinking_hidden(True)
        ui.reasoning_text = "WAS-HIDDEN"
        ui._settle_reasoning()
        assert "WAS-HIDDEN" not in _frame_text(ui)
        # Toggling visibility back commits the deferred reasoning into history.
        ui.set_thinking_hidden(False)
        assert "WAS-HIDDEN" in _frame_text(ui)
        assert ui._deferred_reasoning == []


class TestToolExpansion:
    def test_expanded_shows_more_live_output(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        # 16 lines: more than the 12-line collapsed live tail, but few enough
        # that they all fit a tall frame when expanded.
        ui.tool_output_text = "\n".join(f"line{n:02d}" for n in range(16))
        ui.tools_expanded = False
        collapsed = "\n".join(ui.render_lines(width=88, height=40))
        ui.tools_expanded = True
        expanded = "\n".join(ui.render_lines(width=88, height=40))
        # The earliest line is hidden in the collapsed live tail but shown when
        # expanded.
        assert "line00" not in collapsed
        assert "line00" in expanded


class TestToggleDispatch:
    def test_toggle_tools_flips_flag_and_reports(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        session = NativeToolReplSession(
            provider=FakeNativeProvider(supports_tool_calls=True), tool_registry={}
        )
        settings = SettingsManager.for_workspace(tmp_path)
        err = io.StringIO()
        session._toggle_view_fold(
            HOTKEY_TOGGLE_TOOLS,
            terminal_ui=ui,
            error_stream=cast(TextIO, err),
            settings=settings,
        )
        assert ui.tools_expanded is True

    def test_toggle_thinking_persists_to_settings(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        session = NativeToolReplSession(
            provider=FakeNativeProvider(supports_tool_calls=True), tool_registry={}
        )
        settings = SettingsManager.for_workspace(tmp_path)
        err = io.StringIO()
        session._toggle_view_fold(
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
