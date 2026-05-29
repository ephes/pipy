"""Focused tests for the pipy-owned tool-loop terminal UI shell."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderToolCall
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
from pipy_harness.native.tui import ToolLoopTerminalUi


class _TtyBuffer:
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )


class _ExitOnlyUi:
    runtime_label = "tool-loop-tui"

    def __init__(self) -> None:
        self.closed = False
        self.started = False

    def set_footer_text(self, text: str) -> None:
        del text

    def start(self) -> None:
        self.started = True

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        del prompt_label, footer
        return ""

    def close(self) -> None:
        self.closed = True


def test_tui_frame_owns_distinct_regions(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message("hello world!")
    ui.set_working("⠋ Working...")
    ui.input_text = "next"

    frame = ui.render_lines(width=72, height=14, pad=False)

    assert len(frame) == 14
    assert sum("hello world!" in line for line in frame) == 1
    assert sum("Working..." in line for line in frame) == 1
    input_index = next(index for index, line in enumerate(frame) if "next" in line)
    assert frame[input_index - 1].strip("─") == ""
    assert frame[input_index + 1].strip("─") == ""
    assert "~/projects/pipy" in frame[input_index + 2]
    assert "$0.000" in frame[input_index + 3]


def test_tui_keeps_working_region_below_assistant_stream(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.submit_user_message("hello world!")
    ui.set_working("⠋ Working...")
    active = ui.render_lines(width=72, height=14)
    assert sum("Working..." in line for line in active) == 1

    ui.append_assistant("Hello from pipy.")
    streamed = ui.render_lines(width=72, height=14)

    assert sum("Working..." in line for line in streamed) == 1
    assert sum("Hello from pipy." in line for line in streamed) == 1
    assert next(
        index for index, line in enumerate(streamed) if "Working..." in line
    ) > next(index for index, line in enumerate(streamed) if "Hello from pipy." in line)


def test_tui_renderer_settles_without_stale_working_line(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.begin_provider_turn()
    renderer.show_working()
    renderer.stream_sink("hello ")
    active = "\n".join(ui.render_lines(width=72, height=14))
    assert "Working..." in active
    assert "hello" in active

    renderer.stream_sink("world")
    renderer.end_provider_turn(final_text="hello world", has_tool_calls=False)

    frame = "\n".join(ui.render_lines(width=72, height=20))
    assert "Working..." not in frame
    assert frame.count("hello world") == 1


def test_tui_renderer_abort_shows_operation_aborted(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.begin_provider_turn()
    renderer.show_working()
    renderer.abort_provider_turn()

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "Working..." not in frame
    assert "Operation aborted" in frame


def test_tui_renderer_collapses_read_tool_result_like_pi(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="call_read",
            tool_name="read",
            arguments_json='{"path": "docs/backlog.md", "limit": 5}',
        )
    )
    renderer.render_tool_result(
        output_text="line one\nline two",
        is_error=False,
        duration_seconds=0.2,
    )

    frame = "\n".join(ui.render_lines(width=72, height=20))
    assert "read docs/backlog.md" in frame
    assert "$ read" not in frame
    assert ":1-5" not in frame
    assert "line one" not in frame
    assert "Took 0.2s" not in frame


def test_tui_renderer_keeps_non_read_tool_results_in_history_region(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="call_ls",
            tool_name="ls",
            arguments_json='{"path": "."}',
        )
    )
    renderer.render_tool_result(
        output_text="file one\nfile two",
        is_error=False,
        duration_seconds=0.2,
    )

    frame = "\n".join(ui.render_lines(width=72, height=20))
    assert "$ ls" in frame
    assert "one" in frame
    assert "two" in frame
    assert "Took 0.2s" in frame


def test_tui_preserves_input_and_footer_when_history_overflows(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message("use a tool")
    ui.add_tool_call("ls")
    ui.add_tool_result(
        lines=[f"file {index}" for index in range(30)],
        is_error=False,
        duration_seconds=0.1,
    )

    frame_lines = ui._frame_lines(width=72, height=14, pad=False)
    frame = [line.text for line in frame_lines]
    input_index = next(
        index for index, line in enumerate(frame_lines) if line.kind == "input"
    )

    text = "\n".join(frame)
    assert "use a tool" in text
    assert "file 29" in text
    assert frame[input_index - 1].strip("─") == ""
    assert frame[input_index + 1].strip("─") == ""
    assert "~/projects/pipy" in frame[input_index + 2]
    assert "$0.000" in frame[input_index + 3]


def test_tui_keeps_context_above_prompt_when_history_overflows(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._history_blocks = [
        ("section", ("[Skills]",)),
        ("resource", ("  commit-ready, review-handoff", "", "")),
    ]
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message(
        "Use the ls tool on the current directory, then reply exactly: TOOL SMOKE DONE"
    )
    ui.add_tool_call("ls .")
    ui.add_tool_result(
        lines=[f"file {index}" for index in range(24)],
        is_error=False,
        duration_seconds=0.1,
    )
    ui.append_assistant("TOOL SMOKE DONE")

    frame_lines = ui._frame_lines(width=100, height=30, pad=False)
    frame = [line.text for line in frame_lines]
    prompt_index = next(index for index, line in enumerate(frame) if "Use the ls" in line)
    input_index = next(
        index for index, line in enumerate(frame_lines) if line.kind == "input"
    )

    assert prompt_index > 0
    assert "[Skills]" in "\n".join(frame[:prompt_index])
    assert "TOOL SMOKE DONE" in "\n".join(frame[prompt_index:input_index])
    assert frame[input_index - 1].strip("─") == ""
    assert "~/projects/pipy" in frame[input_index + 2]


def test_tui_short_height_retains_startup_chrome_before_prompt(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._history_blocks = [
        ("normal", ("",)),
        ("title", (" pipy v0.1.0",)),
        (
            "controls",
            (
                " escape interrupt · ctrl+c/ctrl+d clear/exit · / commands · "
                "! bash · ctrl+o more",
            ),
        ),
        ("dim", (" Press ctrl+o to show full startup help and loaded resources.",)),
        ("normal", ("",)),
        (
            "dim",
            (
                " Pipy can explain its own features and look up its docs. "
                "Ask it how to use or extend pipy.",
            ),
        ),
        ("normal", ("", "")),
        ("section", ("[Context]",)),
        ("resource", ("  ~/.pipy/AGENTS.md, ~/projects/AGENTS.md, AGENTS.md", "")),
        ("section", ("[Skills]",)),
        ("resource", ("  commit-ready, commit-workflow, review-handoff", "", "")),
    ]
    ui.footer_lines = (
        "~/projects/pipy (main)",
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high",
    )
    ui.submit_user_message("hello world")
    ui.append_assistant("Hello!")

    frame = ui.render_lines(width=100, height=24, pad=False)
    prompt_index = next(index for index, line in enumerate(frame) if "hello world" in line)
    output_index = next(index for index, line in enumerate(frame) if "Hello!" in line)
    input_index = next(index for index, line in enumerate(frame) if line == " ")

    assert frame[0] == ""
    assert "Pipy can explain" in frame[1]
    assert "[Context]" in "\n".join(frame[:prompt_index])
    assert "[Skills]" in "\n".join(frame[:prompt_index])
    assert prompt_index == 12
    assert output_index == 15
    assert input_index == 18
    assert "~/projects/pipy" in frame[20]
    assert "(openai-codex) gpt-5.5" in frame[21]


def test_tui_user_message_background_matches_pi_three_row_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)
    ui.footer_lines = (
        "~/projects/pipy (main)",
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high",
    )
    ui.submit_user_message("hello world")
    ui.append_assistant("Hello!")

    ui.paint()

    snapshot = parse_ansi_screen(
        cast(_TtyBuffer, ui.terminal_stream).getvalue(),
        columns=88,
        rows=24,
    )
    prompt = snapshot.find("hello world")[0]
    background_rows = [
        row_index
        for row_index, row in enumerate(snapshot.cells)
        if sum(1 for cell in row if cell.attr.bg == prompt.attr.bg)
        >= snapshot.columns - 1
    ]

    assert background_rows == [prompt.row - 1, prompt.row, prompt.row + 1]
    assert snapshot.cells[prompt.row + 2][0].attr.bg is None


def test_tui_drops_tail_when_context_and_prompt_fill_history_region(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._history_blocks = [("normal", ("ctx1", "ctx2", "ctx3", "ctx4"))]
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message("prompt")
    ui.append_assistant("tail1\ntail2\ntail3")

    frame = ui.render_lines(width=72, height=13, pad=False)
    input_index = next(index for index, line in enumerate(frame) if line == " ")

    assert "tail1" not in "\n".join(frame)
    assert frame[input_index - 1].strip("─") == ""
    assert frame[input_index + 1].strip("─") == ""
    assert "~/projects/pipy" in frame[input_index + 2]
    assert "$0.000" in frame[input_index + 3]


def test_tui_renderer_accumulates_reasoning_chunks_without_token_lines(
    tmp_path: Path,
):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)
    renderer.begin_provider_turn()

    renderer.reasoning_sink("Thinking ")
    renderer.reasoning_sink("about ")
    renderer.reasoning_sink("this.")

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "Thinking about this." in frame
    assert "Thinking \n" not in frame


def test_tui_settles_reasoning_before_turn_reset(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.append_reasoning("Thinking ")
    ui.append_reasoning("through it.")
    ui.begin_assistant_turn()

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "Thinking through it." in frame
    assert ui.reasoning_text == ""


def test_tui_reasoning_row_emits_italic_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # parse_ansi_screen does not track the italic SGR (code 3), so pin the
    # raw escape: reasoning text must be preceded by the italic-prefixed
    # secondary-dim color, matching the captured-stream fallback renderer.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)

    ui.append_reasoning("Thinking about this.")
    ui.paint()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "\x1b[3;38;2;128;128;128m Thinking about this.\x1b[0m" in output


def test_tui_reasoning_row_drops_italic_under_no_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)

    ui.append_reasoning("Thinking about this.")
    ui.paint()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    # The italic SGR is `\x1b[3;…m` / `\x1b[3m`; assert that specific sequence is
    # absent rather than the bare `\x1b[3` prefix, which now also appears in
    # relative cursor moves (e.g. `\x1b[3A`) in the inline renderer.
    assert "\x1b[3;" not in output
    assert "\x1b[3m" not in output
    assert "Thinking about this." in output


def test_tui_tool_call_uses_pi_command_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)

    ui.add_tool_call("ls")
    ui.paint()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "\x1b[48;2;40;50;40m" in output


def test_tui_tool_result_uses_pi_command_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)

    ui.add_tool_result(lines=["result line"], is_error=False, duration_seconds=0.1)
    ui.paint()

    snapshot = parse_ansi_screen(
        cast(_TtyBuffer, ui.terminal_stream).getvalue(),
        columns=88,
        rows=24,
    )
    result = snapshot.find("result line")[0]
    assert result.attr.bg == "40;50;40"
    assert result.attr.fg == "128;128;128"


def test_tui_tool_panel_matches_pi_spacing_and_text_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = _ui(tmp_path)

    ui.add_tool_call("ls")
    ui.add_tool_result(lines=["alpha"], is_error=False, duration_seconds=0.1)
    ui.paint()

    snapshot = parse_ansi_screen(
        cast(_TtyBuffer, ui.terminal_stream).getvalue(),
        columns=88,
        rows=24,
    )
    call = snapshot.find("$ ls")[0]

    assert snapshot.viewport[call.row - 1] == ""
    assert snapshot.cells[call.row - 1][0].attr.bg == "40;50;40"
    assert snapshot.viewport[call.row + 1] == ""
    assert snapshot.cells[call.row + 1][0].attr.bg == "40;50;40"
    assert sum(1 for cell in snapshot.cells[call.row] if cell.attr.bold) == 4


def test_tui_settings_overlay_renders_through_frame(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.show_settings(
        [
            "pipy native REPL settings:",
            "  active: fake/fake-native-bootstrap",
            "  registered providers:",
            "    fake/fake-native-bootstrap [available]",
            "    openai/gpt-5.5 [unavailable (env-missing)]",
            "  read-only view; /model, /login, and /logout are not yet "
            "available in tool-loop mode.",
        ]
    )

    rendered = "\n".join(ui.render_lines(width=88, height=40, pad=False))
    assert "pipy native REPL settings:" in rendered
    assert "active: fake/fake-native-bootstrap" in rendered
    assert "openai/gpt-5.5 [unavailable (env-missing)]" in rendered
    assert "read-only view; /model, /login, and /logout are not yet" in rendered

    # The same content must reach the real terminal stream via paint().
    ui.paint()
    painted = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "pipy native REPL settings:" in painted


def test_tui_slash_menu_lists_only_executable_commands(tmp_path: Path):
    from pipy_harness.native.tui import TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS

    assert TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS == (
        "/help",
        "/model",
        "/settings",
        "/copy",
        "/exit",
        "/quit",
    )
    # /model is now an executable interactive selector; /login and /logout
    # remain not-yet-executable in tool-loop mode and stay out of the menu.
    for not_yet_executable in ("/login", "/logout"):
        assert not_yet_executable not in TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS

    ui = _ui(tmp_path)
    assert ui.command_names == TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
    assert ui.command_descriptions.get("/copy")


def test_tui_slash_menu_shows_copy_command(tmp_path: Path):
    ui = _ui(tmp_path)

    ui._insert_input_text("/co")

    frame = ui._frame_lines(width=88, height=24, pad=False)
    rendered = "\n".join(line.text for line in frame)

    assert ui.slash_menu_open is True
    assert "copy" in rendered


def test_tui_slash_keystroke_opens_command_menu(tmp_path: Path):
    ui = _ui(tmp_path)

    ui._insert_input_text("/")

    frame = ui._frame_lines(width=88, height=24, pad=False)
    rendered = "\n".join(line.text for line in frame)

    assert ui.slash_menu_open is True
    assert "→ help" in rendered
    assert "Show pipy command reference" in rendered
    assert "  exit" in rendered
    # The read-only settings overlay is executable in tool-loop mode, so the
    # menu now advertises it alongside help/exit/quit.
    assert "  settings" in rendered
    assert "Show provider settings (read-only)" in rendered
    assert any(line.kind == "slash_menu_selected" for line in frame)
    input_index = next(index for index, line in enumerate(frame) if line.kind == "input")
    menu_index = next(
        index for index, line in enumerate(frame) if line.kind == "slash_menu_selected"
    )
    assert frame[input_index + 1].kind == "separator"
    assert menu_index == input_index + 2
    assert "(1/3)" not in rendered


def test_tui_slash_menu_navigation_accept_and_escape(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._insert_input_text("/")

    ui._navigate_slash_menu("down")
    assert ui.slash_menu_selection == 1

    ui._accept_slash_menu_selection()
    # Menu order is help(0), model(1), settings(2), ...; one step down lands
    # on the now-executable /model selector command.
    assert ui.input_text == "/model"
    assert ui.input_cursor == len("/model")
    assert ui.slash_menu_open is False

    ui.input_text = "/"
    ui.input_cursor = 1
    ui._refresh_slash_menu_state()
    assert ui.slash_menu_open is True

    ui.slash_menu_open = False
    frame = "\n".join(ui.render_lines(width=88, height=24, pad=False))
    assert "→ help" not in frame
    assert ui.input_text == "/"


def test_tui_model_selector_renders_rows_with_highlight_and_reasons(
    tmp_path: Path,
):
    from pipy_harness.native.tui import ModelSelectorOption

    ui = _ui(tmp_path)
    ui.model_selector_open = True
    ui.model_selector_options = (
        ModelSelectorOption("openrouter/openai/gpt  [available] (current)", True),
        ModelSelectorOption("openai/gpt-5.5  [available]", True),
        ModelSelectorOption("fake/fake  [unavailable: no tool-call support]", False),
    )
    ui.model_selector_selection = 1

    frame = ui._frame_lines(width=88, height=24, pad=False)
    rendered = "\n".join(line.text for line in frame)

    # The selector overlay (title + rows) replaces the normal input/menu area.
    assert "Select provider/model" in rendered
    # The highlighted row carries the cursor marker; others do not.
    assert "→ openai/gpt-5.5  [available]" in rendered
    assert "  openrouter/openai/gpt  [available] (current)" in rendered
    # Unavailable rows stay visible with their reason.
    assert "fake/fake  [unavailable: no tool-call support]" in rendered


def test_tui_model_selector_navigation_wraps(tmp_path: Path):
    from pipy_harness.native.tui import ModelSelectorOption

    ui = _ui(tmp_path)
    ui.model_selector_open = True
    ui.model_selector_options = (
        ModelSelectorOption("a", True),
        ModelSelectorOption("b", True),
        ModelSelectorOption("c", False),
    )
    ui.model_selector_selection = 0

    ui._navigate_model_selector("down")
    assert ui.model_selector_selection == 1
    ui._navigate_model_selector("up")
    ui._navigate_model_selector("up")
    # Wrapping: up from index 0 lands on the last row.
    assert ui.model_selector_selection == 2


def test_tui_model_selector_keeps_cursor_hidden(tmp_path: Path):
    from pipy_harness.native.tui import ModelSelectorOption

    ui = _ui(tmp_path)
    ui.model_selector_open = True
    ui.model_selector_options = (ModelSelectorOption("a  [available]", True),)
    ui.model_selector_selection = 0

    ui.paint()
    painted = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    # The selector has no editable input cell: the paint hides the cursor and
    # never re-shows it (unlike the normal input frame, which parks + shows it).
    assert "\x1b[?25l" in painted
    assert "\x1b[?25h" not in painted


def test_tui_input_cursor_can_move_within_typed_text(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._insert_input_text("ab")

    ui._move_input_cursor("left")
    ui._insert_input_text("X")

    assert ui.input_text == "aXb"
    assert ui.input_cursor == 2


def test_tui_start_is_inline_and_close_restores_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    ui = _ui(tmp_path)
    ui.set_footer_text("~/projects/pipy (main)\n$0.000 (sub) status")

    ui.start()
    ui.close()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    # Inline scrollback model: no alternate screen, so native terminal
    # scrollback in Ghostty/zellij can review prior committed content.
    assert "\x1b[?1049h" not in output
    assert "\x1b[?1049l" not in output
    # Startup chrome is printed into the normal buffer.
    assert "escape interrupt" in output
    # The cursor is shown again when the session ends.
    assert "\x1b[?25h" in output


def test_tui_paint_uses_explicit_carriage_returns_for_raw_mode(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.paint()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "\x1b[K\r\n" in output
    assert "\x1b[K\n" not in output


def test_tui_paint_places_live_cursor_on_input_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) status")
    ui.submit_user_message("hello world!")
    ui.input_text = "next"

    ui.paint()

    snapshot = parse_ansi_screen(
        cast(_TtyBuffer, ui.terminal_stream).getvalue(), columns=80, rows=24
    )
    input_row = next(
        index for index, line in enumerate(snapshot.viewport) if line.startswith("next")
    )
    assert snapshot.cursor_x == 4
    assert snapshot.cursor_y == input_row


def test_tui_paint_does_not_reprint_committed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "40")
    ui = _ui(tmp_path)
    ui.start()
    ui.submit_user_message("UNIQUE_MARKER_X")
    terminal = cast(_TtyBuffer, ui.terminal_stream)
    boundary = len(terminal.getvalue())

    # A later paint that adds no new history must not reprint the committed
    # block: it lives in the terminal's native scrollback, not in the frame.
    ui.set_footer_text("~/projects/pipy (main)\n$0.000 (sub) status")
    ui.paint()

    delta = terminal.getvalue()[boundary:]
    assert "UNIQUE_MARKER_X" not in delta


def test_tui_paint_uses_full_height_and_scrolls_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "20")
    ui = _ui(tmp_path)
    ui.set_footer_text("~/projects/pipy (main)\n$0.000 (sub) status")
    ui.start()
    for index in range(8):
        ui.submit_user_message(f"message number {index}")
    ui.input_text = "typing"
    ui.paint()

    snapshot = parse_ansi_screen(
        cast(_TtyBuffer, ui.terminal_stream).getvalue(), columns=80, rows=20
    )
    joined = "\n".join(snapshot.viewport)

    # Full height: content overflowed and scrolled into native scrollback
    # instead of being capped to the upper half of the window.
    assert snapshot.viewport_y > 0
    assert "message number 0" not in joined
    assert "message number 7" in joined
    # The input/footer frame stays pinned at the bottom of the window.
    assert "~/projects/pipy" in "\n".join(snapshot.viewport[-3:])


class _CountingProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.completions = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:  # pragma: no cover
        self.completions += 1
        raise AssertionError("read-only /settings must not create a provider turn")


def _read_only_provider_state(tmp_path: Path, provider: ProviderPort):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState

    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda selection: provider,
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )


class _RecordingProvider:
    """Tool-capable provider that records the (provider, model) of each turn."""

    def __init__(
        self,
        provider_name: str,
        model_id: str,
        seen: list[tuple[str, str]],
        *,
        supports_tool_calls: bool = True,
    ) -> None:
        self._provider_name = provider_name
        self.model_id = model_id
        self._seen = seen
        self.supports_tool_calls = supports_tool_calls

    @property
    def name(self) -> str:
        return self._provider_name

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        self._seen.append((request.provider_name, request.model_id))
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
            tool_calls=(),
        )


def _recording_provider_state(
    tmp_path: Path,
    seen: list[tuple[str, str]],
    *,
    provider_name: str,
    model_id: str,
    env: dict[str, str],
):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState

    def factory(selection):
        # `fake` mirrors production (no tool-call support); everything else is
        # a tool-capable recording provider.
        return _RecordingProvider(
            selection.provider_name,
            selection.model_id,
            seen,
            supports_tool_calls=selection.provider_name != "fake",
        )

    return NativeReplProviderState(
        selection=NativeModelSelection(provider_name, model_id),
        provider_factory=factory,
        env=env,
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )


def test_model_selector_rows_gate_unavailable_and_non_tool_capable(tmp_path: Path):
    seen: list[tuple[str, str]] = []
    # Use the registered default model so the current selection matches the
    # option row and is marked "(current)".
    provider_state = _recording_provider_state(
        tmp_path,
        seen,
        provider_name="openrouter",
        model_id="openai/gpt-5.1-codex",
        env={"OPENROUTER_API_KEY": "k"},
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )

    ui_options, selections = session._model_selector_rows(provider_state)

    by_provider = {
        sel.provider_name: option for sel, option in zip(selections, ui_options)
    }
    # `fake` is credential-available but not tool-capable → visible, not choosable.
    assert by_provider["fake"].selectable is False
    assert "no tool-call support" in by_provider["fake"].label
    # An env-credentialed, tool-capable provider is choosable and marked current.
    assert by_provider["openrouter"].selectable is True
    assert "(current)" in by_provider["openrouter"].label
    # A provider without credentials is visible but not choosable.
    assert by_provider["openai"].selectable is False
    assert "unavailable" in by_provider["openai"].label


def test_model_command_direct_reference_rebinds_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen: list[tuple[str, str]] = []
    provider_state = _recording_provider_state(
        tmp_path,
        seen,
        provider_name="openrouter",
        model_id="openai/gpt",
        env={"OPENROUTER_API_KEY": "k", "OPENAI_API_KEY": "k2"},
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    # Captured-stream path (no TUI): switch via `/model <ref>`, then one turn.
    input_stream = io.StringIO("/model openai/gpt-5.5\nhello\n")

    result = session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, input_stream),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    # The /model command ran no provider turn; only the post-switch prompt did,
    # and it was constructed with the newly selected provider/model.
    assert seen == [("openai", "gpt-5.5")]
    assert result.provider_name == "openai"
    assert result.model_id == "gpt-5.5"
    assert result.user_turn_count == 1


def test_model_command_refuses_non_tool_capable_selection(
    tmp_path: Path,
):
    seen: list[tuple[str, str]] = []
    provider_state = _recording_provider_state(
        tmp_path,
        seen,
        provider_name="openrouter",
        model_id="openai/gpt",
        env={"OPENROUTER_API_KEY": "k"},
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    error_stream = io.StringIO()
    # `fake` is available but not tool-capable: the switch must be refused and
    # the previous selection preserved.
    input_stream = io.StringIO("/model fake/fake-native-bootstrap\nhello\n")

    result = session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, input_stream),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert "does not support tool calls" in error_stream.getvalue()
    # Selection unchanged: the turn still ran on the original provider/model.
    assert seen == [("openrouter", "openai/gpt")]
    assert result.provider_name == "openrouter"
    assert provider_state.current_selection().provider_name == "openrouter"


def test_model_command_refusal_restores_unavailable_previous_selection(
    tmp_path: Path,
):
    # The active provider is explicit/tool-capable but NOT env-available
    # (no OPENAI_API_KEY), so a naive revert via select_model() — which
    # re-checks availability — would fail and leave the rejected selection in
    # place. The refusal must restore the previous selection regardless.
    seen: list[tuple[str, str]] = []
    provider_state = _recording_provider_state(
        tmp_path,
        seen,
        provider_name="openai",
        model_id="gpt-5.5",
        env={},  # openai is unavailable per env checks; fake is always available
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    error_stream = io.StringIO()
    input_stream = io.StringIO("/model fake/fake-native-bootstrap\nhello\n")

    result = session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, input_stream),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert "does not support tool calls" in error_stream.getvalue()
    # Selection restored to the original provider/model, not left on fake.
    assert provider_state.current_selection().provider_name == "openai"
    assert provider_state.current_selection().model_id == "gpt-5.5"
    # The turn still ran on the original provider/model.
    assert seen == [("openai", "gpt-5.5")]
    assert result.provider_name == "openai"


def test_model_selector_rows_mark_current_non_default_model(tmp_path: Path):
    # A current selection on a non-default model is not present in
    # model_options(); the selector must still surface it, mark it "(current)",
    # and keep it selectable so the highlight can start on the active row.
    seen: list[tuple[str, str]] = []
    provider_state = _recording_provider_state(
        tmp_path,
        seen,
        provider_name="openrouter",
        model_id="openai/custom-model",
        env={"OPENROUTER_API_KEY": "k"},
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )

    ui_options, selections = session._model_selector_rows(provider_state)

    current_rows = [option for option in ui_options if "(current)" in option.label]
    assert len(current_rows) == 1
    assert "openrouter/openai/custom-model" in current_rows[0].label
    assert current_rows[0].selectable is True
    # The current selection is represented in the parallel selections list so
    # the dispatcher can resolve a correct initial highlight index.
    assert any(
        sel.provider_name == "openrouter" and sel.model_id == "openai/custom-model"
        for sel in selections
    )


def test_tui_settings_command_renders_read_only_overlay_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = _CountingProvider()
    provider_state = _read_only_provider_state(tmp_path, provider)
    ui = _ui(tmp_path)
    scripted = iter(["/settings\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )
    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    # Read-only: no provider turn, no tool invocation, no state mutation.
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert provider.completions == 0
    assert any(kind == "settings" for kind, _lines in ui._history_blocks)

    rendered = "\n".join(ui.render_lines(width=88, height=44, pad=False))
    assert "pipy native REPL settings:" in rendered
    assert "active: fake/fake-native-bootstrap" in rendered
    assert "openai-codex/gpt-5.5 [unavailable (login-required)]" in rendered
    # The TUI footer is honest: /model is now executable here; /login and
    # /logout remain not-yet-executable in tool-loop mode. (The long line wraps
    # in the frame, so assert on the stable fragments rather than the whole.)
    assert "use /model to switch provider/model" in rendered
    assert "/login" in rendered
    assert "/logout" in rendered


class _ClipboardRecorder:
    def __init__(self) -> None:
        self.copies: list[str] = []

    def __call__(self, text: str, **kwargs: object) -> ClipboardResult:
        self.copies.append(text)
        return ClipboardResult(
            copied=True,
            method="pbcopy",
            byte_count=len(text.encode("utf-8")),
            detail="copied",
        )


class _AnswerProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.completions = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        del reasoning_sink
        self.completions += 1
        if stream_sink is not None:
            stream_sink(self.answer)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.answer,
            tool_calls=(),
        )


def test_tool_loop_help_text_lists_copy_command():
    help_text = NativeToolReplSession._help_text()
    for command in ("/help", "/settings", "/copy", "/exit", "/quit"):
        assert command in help_text, f"help text omits {command}"


def test_tui_copy_command_is_local_only_when_nothing_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = _CountingProvider()
    provider_state = _read_only_provider_state(tmp_path, provider)
    ui = _ui(tmp_path)
    recorder = _ClipboardRecorder()
    scripted = iter(["/copy\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )
    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        clipboard_copy=recorder,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    # Local command only: no provider turn, no tool invocation, no copy.
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert provider.completions == 0
    assert recorder.copies == []
    notices = [lines for kind, lines in ui._history_blocks if kind == "notice"]
    assert any("nothing to copy" in " ".join(lines).lower() for lines in notices)


def test_tui_copy_command_copies_last_answer_without_extra_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = _AnswerProvider("Final answer ABC")
    provider_state = _read_only_provider_state(tmp_path, provider)
    ui = _ui(tmp_path)
    recorder = _ClipboardRecorder()
    scripted = iter(["hello\n", "/copy\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )
    # The active-turn Escape watcher needs a real fd; this in-process test
    # drives StringIO, so wait for the worker and report "not aborted". Real
    # cancellation is covered by the PTY tests.
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "wait_for_active_turn_interrupt",
        lambda self, done_event, abort_event, **kwargs: (
            done_event.wait(5),
            False,
        )[1],
    )
    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        clipboard_copy=recorder,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    # Exactly one provider turn (the answer); /copy creates no further turn.
    assert provider.completions == 1
    assert result.user_turn_count == 1
    assert result.tool_invocation_count == 0
    assert recorder.copies == ["Final answer ABC"]
    notices = [lines for kind, lines in ui._history_blocks if kind == "notice"]
    assert any("copied" in " ".join(lines).lower() for lines in notices)


def test_tool_loop_plain_settings_command_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NO_COLOR", "1")
    provider = _CountingProvider()
    provider_state = _read_only_provider_state(tmp_path, provider)
    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        input_runtime="plain",
    )
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/settings\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 0
    assert provider.completions == 0
    stderr = error_stream.getvalue()
    assert "pipy native REPL settings:" in stderr
    assert "active: fake/fake-native-bootstrap" in stderr
    assert "openai/gpt-5.5 [unavailable (env-missing)]" in stderr


def test_tui_session_does_not_print_legacy_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ui = _ExitOnlyUi()
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace: ui,
    )
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert ui.started is True
    assert ui.closed is True
    assert "─" not in error_stream.getvalue()
