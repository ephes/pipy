"""TUI-level tests for the editor @ file picker and Tab path completion.

These drive ``ToolLoopTerminalUi`` state transitions and ``render_lines`` (the
inspectable frame the real paint path also composes) to prove the popup opens,
ranks, navigates, and accepts. The observable live-region behavior over a real
PTY is covered in ``tests/test_native_tool_loop_tui_pty.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

from pipy_harness.native.tui import ToolLoopTerminalUi


def _ui(workspace: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=workspace,
    )


def _frame_text(ui: ToolLoopTerminalUi) -> str:
    return "\n".join(ui.render_lines(width=88, height=24))


def _type(ui: ToolLoopTerminalUi, text: str) -> None:
    for char in text:
        ui._insert_input_text(char)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src" / "tui").mkdir(parents=True)
    (tmp_path / "src" / "tui" / "config.py").write_text("x\n")
    (tmp_path / "src" / "config.py").write_text("y\n")
    (tmp_path / "README.md").write_text("z\n")
    (tmp_path / "scripts").mkdir()
    return tmp_path


class TestAtPicker:
    def test_typing_at_query_opens_ranked_popup(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "look @config")
        assert ui.autocomplete_open
        assert ui.autocomplete_mode == "at"
        assert any(item.label == "config.py" for item in ui.autocomplete_items)
        assert "config.py" in _frame_text(ui)

    def test_non_substring_query_does_not_open(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "@srctuiconfig")
        assert not ui.autocomplete_open

    def test_slash_menu_keeps_priority_over_at(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "/he")
        assert ui.slash_menu_open
        assert not ui.autocomplete_open

    def test_accept_replaces_token_with_at_path(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "see @tui")
        assert ui.autocomplete_open
        ui._accept_autocomplete_selection()
        assert "@src/tui" in ui.input_text
        assert ui.input_text.startswith("see @")
        assert not ui.autocomplete_open

    def test_navigation_wraps(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "@config")
        count = len(ui.autocomplete_items)
        assert count >= 2
        ui._navigate_autocomplete("up")
        assert ui.autocomplete_selection == count - 1

    def test_backspace_closes_when_token_gone(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "@config")
        assert ui.autocomplete_open
        for _ in range("@config".__len__()):
            ui._delete_before_cursor()
        assert not ui.autocomplete_open


class TestBashModeAffordance:
    def test_bang_buffer_marks_bash_mode(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "!ls")
        assert ui._is_bash_mode()
        assert "! bash" in _frame_text(ui)

    def test_plain_buffer_is_not_bash_mode(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "hello")
        assert not ui._is_bash_mode()
        assert "! bash" not in _frame_text(ui)


class TestPathCompletion:
    def test_tab_completes_directory_listing(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "./src/")
        assert ui._attempt_path_completion()
        assert ui.autocomplete_open
        assert ui.autocomplete_mode == "path"
        labels = {item.label for item in ui.autocomplete_items}
        assert "config.py" in labels
        assert "tui/" in labels

    def test_tab_in_prose_is_a_no_op(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "hello world")
        assert ui._attempt_path_completion() is False
        assert not ui.autocomplete_open
        assert ui.input_text == "hello world"

    def test_single_candidate_completes_inline(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "./scr")
        assert ui._attempt_path_completion()
        assert ui.input_text == "./scripts/"
        assert not ui.autocomplete_open

    def test_bare_workspace_prefix_completes(self, tmp_path: Path) -> None:
        # A bare (non-path-like) workspace prefix completes via forced Tab, not
        # just paths starting with ./ or ~/.
        ui = _ui(_workspace(tmp_path))
        _type(ui, "scr")
        assert ui._attempt_path_completion()
        assert ui.input_text == "scripts/"

    def test_bare_prose_word_with_no_match_is_a_no_op(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        _type(ui, "zzznomatch")
        assert ui._attempt_path_completion() is False
        assert ui.input_text == "zzznomatch"
        assert not ui.autocomplete_open

    def test_empty_buffer_tab_is_a_no_op(self, tmp_path: Path) -> None:
        ui = _ui(_workspace(tmp_path))
        assert ui._attempt_path_completion() is False
        assert not ui.autocomplete_open
