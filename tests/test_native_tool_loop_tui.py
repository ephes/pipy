"""Focused tests for the pipy-owned tool-loop terminal UI shell."""

from __future__ import annotations

import io
import os
import time
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
from pipy_harness.native.repl_state import NativeModelOption
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
from pipy_harness.native.tui import SettingsRow, ToolLoopTerminalUi


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


def _wait_for_frame_text(
    ui: ToolLoopTerminalUi, text: str, *, width: int = 72, height: int = 14
) -> str:
    deadline = time.monotonic() + 1.0
    frame = ""
    while time.monotonic() < deadline:
        frame = "\n".join(ui.render_lines(width=width, height=height))
        if text in frame:
            return frame
        time.sleep(0.01)
    return frame


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

    def take_next_drain(self) -> str | None:
        return None

    def take_pending_command(self) -> str | None:
        return None

    def close(self) -> None:
        self.closed = True


class _RawCustomComponent:
    def render(self, width: int) -> list[str]:
        del width
        return ["custom\x1b[31mred\x1b[0m\rreturn\x1b]0;bad\x07"]

    def handle_input(self, key: str) -> None:
        del key


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


def test_tui_styles_only_working_spinner_with_accent(tmp_path: Path):
    ui = _ui(tmp_path)
    frame_line = ui._block_frame_lines(
        "working", ("⠋ Working...",), width=40
    )[0]
    styled = ui._styled_line(
        frame_line,
        style=ChromeStyle(enabled=True),
        width=40,
    )

    assert styled.startswith("\x1b[2m \x1b[0m\x1b[36m⠋\x1b[0m")
    assert "\x1b[2m Working...\x1b[0m" in styled


def test_tui_keeps_input_row_stable_when_working_line_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    stream = _TtyBuffer()
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, stream),
        cwd=tmp_path,
    )
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) status")
    ui.input_text = "next prompt"

    ui.set_working("⠋ Working...")
    ui.append_assistant("line one\nline two")
    active = parse_ansi_screen(stream.getvalue(), columns=80, rows=24)
    active_input = next(
        index for index, line in enumerate(active.viewport) if "next prompt" in line
    )

    ui.settle_assistant()
    settled = parse_ansi_screen(stream.getvalue(), columns=80, rows=24)
    settled_input = next(
        index for index, line in enumerate(settled.viewport) if "next prompt" in line
    )

    assert settled_input == active_input


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


def test_tui_renders_bounded_extension_status_rows(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("workspace", "model")
    ui.set_extension_status("build", "green")
    ui.set_extension_status("lint", "run\rning")
    ui.set_extension_status("zeta", "queued")
    ui.set_extension_status("alpha", "\x1b[31mred")
    ui.input_text = "next"

    frame = ui.render_lines(width=72, height=14, pad=False)
    text = "\n".join(frame)

    assert any("build: green" in line for line in frame)
    assert any("lint: run ning" in line for line in frame)
    assert "... +1 extension status rows" in text
    assert "\x1b" not in text
    assert "\r" not in text
    input_index = next(index for index, line in enumerate(frame) if "next" in line)
    assert any("build: green" in line for line in frame[input_index + 2 :])
    assert frame[-2].startswith("workspace")
    assert frame[-1].startswith("model")


def test_tui_custom_entry_sanitizes_and_renders(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.add_custom_entry("card\x1b[31m", ["line one", "bad\rreturn"])

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "[card [31m]" in frame
    assert "line one" in frame
    assert "bad return" in frame
    assert "\x1b" not in frame
    assert "\r" not in frame


def test_tui_notice_sanitizes_control_characters(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.add_notice("bad\x1b[31mred\rreturn")

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "\x1b" not in frame
    assert "\r" not in frame
    assert "bad [31mred" in frame
    assert "return" in frame


def test_tui_tool_blocks_sanitize_control_characters(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.add_tool_call("ext-tool\x1b[31mred\rreturn")
    ui.add_tool_result(lines=["result\x1b[31mred\rreturn"], is_error=False)

    frame = "\n".join(ui.render_lines(width=72, height=20))
    assert "\x1b" not in frame
    assert "\r" not in frame
    assert "ext-tool [31mred" in frame
    assert "result [31mred" in frame


def test_tui_custom_overlay_sanitizes_control_characters(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._custom_component = _RawCustomComponent()
    ui.custom_overlay_open = True

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "\x1b[31m" in frame
    assert "\r" not in frame
    assert "\x07" not in frame
    assert "\x1b]" not in frame
    plain = frame.replace("\x1b[31m", "").replace("\x1b[0m", "")
    assert "customred" in plain
    assert "]0;bad" in plain


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


def test_tui_renderer_uses_extension_working_controls(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    ui.set_extension_working_message("Checking")
    renderer.show_working()
    frame = _wait_for_frame_text(ui, "Checking")
    assert "Checking" in frame
    assert "Working..." not in frame

    renderer.end_provider_turn(final_text="", has_tool_calls=False)
    ui.set_extension_working_visible(False)
    renderer.show_working()
    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "Checking" not in frame
    assert "Working..." not in frame


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


def test_tui_streams_tool_output_into_live_region(tmp_path: Path):
    # Pi-style live streaming: while a tool runs, incremental output (e.g.
    # pytest dots) shows in the live region before the result settles.
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="call_bash",
            tool_name="bash",
            arguments_json='{"command": "just test"}',
        )
    )
    ui.start()
    renderer.tool_output_sink("........ [ 25%]\n")
    renderer.tool_output_sink("........ [ 50%]\n")

    # Assert against the painted terminal output (the real `_live_region_lines`
    # path), not just `render_lines`, so a regression in the live paint path is
    # caught — that is exactly the path that initially failed to stream.
    painted = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "[ 25%]" in painted
    assert "[ 50%]" in painted
    live = "\n".join(ui.render_lines(width=72, height=20))
    assert "[ 50%]" in live


def test_tui_settled_tool_result_replaces_live_stream(tmp_path: Path):
    ui = _ui(tmp_path)
    renderer = _TuiToolLoopRenderer(ui=ui)

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="call_bash",
            tool_name="bash",
            arguments_json='{"command": "just test"}',
        )
    )
    renderer.tool_output_sink("streaming-dots\n")
    renderer.render_tool_result(
        output_text="exit code: 0\n[output]\n1346 passed",
        is_error=False,
        duration_seconds=53.0,
    )

    frame = "\n".join(ui.render_lines(width=72, height=20))
    # The live stream buffer is cleared once the bounded result is committed.
    assert ui.tool_output_text == ""
    assert "1346 passed" in frame


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
            "  read-only view; use /model to switch provider/model and "
            "/login or /logout to manage openai-codex OAuth.",
        ]
    )

    rendered = "\n".join(ui.render_lines(width=88, height=40, pad=False))
    assert "pipy native REPL settings:" in rendered
    assert "active: fake/fake-native-bootstrap" in rendered
    assert "openai/gpt-5.5 [unavailable (env-missing)]" in rendered
    assert "read-only view; use /model to switch" in rendered

    # The same content must reach the real terminal stream via paint().
    ui.paint()
    painted = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "pipy native REPL settings:" in painted


def test_tui_slash_menu_lists_only_executable_commands(tmp_path: Path):
    from pipy_harness.native.tui import TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS

    assert TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS == (
        "/help",
        "/hotkeys",
        "/model",
        "/scoped-models",
        "/settings",
        "/login",
        "/logout",
        "/copy",
        "/compact",
        "/export",
        "/import",
        "/share",
        "/reload",
        "/changelog",
        "/exit",
        "/quit",
    )
    # /login and /logout are now executable in tool-loop mode, so the menu
    # advertises them alongside the rest of the executable command set.
    for executable in ("/login", "/logout", "/compact", "/export", "/import", "/share"):
        assert executable in TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS

    ui = _ui(tmp_path)
    assert ui.command_names == TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
    assert ui.command_descriptions.get("/copy")
    assert ui.command_descriptions.get("/login")
    assert ui.command_descriptions.get("/logout")
    assert ui.command_descriptions.get("/compact")
    assert ui.command_descriptions.get("/export")
    assert ui.command_descriptions.get("/import")
    assert ui.command_descriptions.get("/share")


def test_tui_slash_menu_filters_login_and_logout(tmp_path: Path):
    ui = _ui(tmp_path)

    ui._insert_input_text("/log")

    frame = ui._frame_lines(width=88, height=24, pad=False)
    rendered = "\n".join(line.text for line in frame)

    assert ui.slash_menu_open is True
    # Both auth commands match the /log prefix and render together.
    assert "login" in rendered
    assert "logout" in rendered
    assert "Log in (openai-codex OAuth)" in rendered
    assert "Log out (openai-codex OAuth)" in rendered


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
    assert "Show keyboard shortcuts (alias of /hotkeys)" in rendered
    # The interactive settings dialog is executable in tool-loop mode, so the
    # menu advertises it in the leading rows.
    assert "  settings" in rendered
    assert "Settings and status" in rendered
    assert "  scoped-models" in rendered
    assert any(line.kind == "slash_menu_selected" for line in frame)
    input_index = next(index for index, line in enumerate(frame) if line.kind == "input")
    menu_index = next(
        index for index, line in enumerate(frame) if line.kind == "slash_menu_selected"
    )
    assert frame[input_index + 1].kind == "separator"
    assert menu_index == input_index + 2
    # Sixteen commands match the bare "/" prefix but the menu windows to the
    # autocompleteMaxVisible default (5) rows, so a scroll indicator appears and
    # /login scrolls behind the "… N more" tail.
    assert "(1/16)" in rendered
    assert "  login" not in rendered


def test_tui_slash_menu_honors_autocomplete_max_visible(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.autocomplete_max_visible = 3
    ui._insert_input_text("/")
    frame = ui._frame_lines(width=88, height=24, pad=False)
    menu_rows = [
        line for line in frame if line.kind in {"slash_menu", "slash_menu_selected"}
    ]
    assert len(menu_rows) == 3
    rendered = "\n".join(line.text for line in frame)
    # 16 commands match, only 3 shown -> overflow indicator present.
    assert "(1/16)" in rendered


def test_tui_slash_menu_navigation_accept_and_escape(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._insert_input_text("/")

    ui._navigate_slash_menu("down")
    assert ui.slash_menu_selection == 1

    ui._accept_slash_menu_selection()
    # Menu order is help(0), hotkeys(1), model(2), ...; one step down lands on
    # the /hotkeys command (auto-completed into the editor).
    assert ui.input_text == "/hotkeys"
    assert ui.input_cursor == len("/hotkeys")
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
        cancel_token: object = None,
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
        cancel_token: object = None,
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


def test_tui_settings_command_opens_interactive_dialog_without_provider_turn(
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

    captured_rows: list[tuple[SettingsRow, ...]] = []

    def _fake_dialog(self, rows, *, on_local_action, exit_actions=frozenset(), current_index=None):
        del on_local_action, current_index
        captured_rows.append(tuple(rows))
        # Immediately cancel (Esc) — proving /settings opens an interactive
        # dialog rather than committing a static text block.
        return None

    monkeypatch.setattr(ToolLoopTerminalUi, "run_settings_dialog", _fake_dialog)

    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
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
    # It is an interactive overlay, NOT a committed settings text block.
    assert not any(kind == "settings" for kind, _lines in ui._history_blocks)

    assert captured_rows, "/settings did not open the interactive dialog"
    labels = [row.label for row in captured_rows[0]]
    actions = {row.action for row in captured_rows[0] if row.action}
    # The dialog exposes provider/model, auth, and prompt-history actions plus
    # safe read-only status rows.
    assert any("active: fake/fake-native-bootstrap" in label for label in labels)
    assert "model" in actions
    assert "login" in actions  # openai-codex is logged out in this fixture
    assert "toggle_history" in actions
    assert "clear_history" in actions
    assert any("persistent prompt history: off" in label for label in labels)


def _settings_dialog_rows() -> tuple[SettingsRow, ...]:
    return (
        SettingsRow(label="Provider / model", kind="header"),
        SettingsRow(label="active: fake/fake-native-bootstrap", kind="status"),
        SettingsRow(label="change provider/model…", kind="action", action="model"),
        SettingsRow(label="Prompt history", kind="header"),
        SettingsRow(
            label="persistent prompt history: off — toggle",
            kind="action",
            action="toggle_history",
        ),
        SettingsRow(
            label="clear persisted history (0 saved)",
            kind="action",
            action="clear_history",
        ),
    )


def test_tui_settings_dialog_renders_rows_with_highlight_and_affordances(
    tmp_path: Path,
):
    ui = _ui(tmp_path)
    ui.settings_dialog_open = True
    ui.settings_dialog_rows = _settings_dialog_rows()
    # Highlight the toggle action row (index 4).
    ui.settings_dialog_selection = 4

    frame = ui._frame_lines(width=88, height=24, pad=False)
    rendered = "\n".join(line.text for line in frame)

    # The dialog overlay (title + rows) replaces the normal input/menu area and
    # advertises its key affordances at the top.
    assert "Settings" in rendered
    assert "esc" in rendered
    assert "enter" in rendered
    # Section headers are shown for grouping.
    assert "Provider / model" in rendered
    assert "Prompt history" in rendered
    # The highlighted actionable row carries the cursor marker; others do not.
    assert "→ persistent prompt history: off — toggle" in rendered
    assert "  change provider/model…" in rendered
    # Read-only status rows stay visible without a marker.
    assert "active: fake/fake-native-bootstrap" in rendered


def test_tui_settings_dialog_navigation_skips_non_actionable_rows(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.settings_dialog_open = True
    ui.settings_dialog_rows = _settings_dialog_rows()
    ui.settings_dialog_selection = ui._initial_settings_selection(None)

    # Initial selection lands on the first actionable row (the model action).
    assert ui.settings_dialog_selection == 2

    ui._navigate_settings_dialog("down")
    # Skips the "Prompt history" header to the toggle action.
    assert ui.settings_dialog_selection == 4
    ui._navigate_settings_dialog("down")
    assert ui.settings_dialog_selection == 5
    ui._navigate_settings_dialog("down")
    # Wraps back to the first actionable row.
    assert ui.settings_dialog_selection == 2
    ui._navigate_settings_dialog("up")
    # Wraps backward to the last actionable row.
    assert ui.settings_dialog_selection == 5


def test_tui_settings_dialog_windows_long_list_with_scroll_indicator(tmp_path: Path):
    from pipy_harness.native.tui import SettingsRow

    rows = [SettingsRow(label="header", kind="header")]
    rows.extend(
        SettingsRow(label=f"action {index}", kind="action", action=f"a{index}")
        for index in range(20)
    )
    ui = _ui(tmp_path)
    ui.settings_dialog_open = True
    ui.settings_dialog_rows = tuple(rows)
    ui.settings_dialog_selection = 10

    frame = ui._frame_lines(width=88, height=14, pad=False)
    rendered = "\n".join(line.text for line in frame)

    # The list is windowed to fit the short frame and shows a scroll indicator.
    assert f"/{len(rows)})" in rendered
    # The frame never exceeds the requested height.
    assert len(frame) <= 14


def test_tui_settings_dialog_keeps_cursor_hidden(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.settings_dialog_open = True
    ui.settings_dialog_rows = _settings_dialog_rows()
    ui.settings_dialog_selection = 2

    ui.paint()
    painted = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    # Like the model selector, the settings dialog has no editable input cell:
    # the cursor is hidden and never re-shown while the overlay is open.
    assert "\x1b[?25l" in painted
    assert "\x1b[?25h" not in painted


def _history_recording_provider(seen: list[tuple[str, str]]) -> ProviderPort:
    return cast(ProviderPort, _RecordingProvider("openai", "gpt-5.5", seen))


def test_persistent_history_records_prompt_when_enabled(tmp_path: Path):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    seen: list[tuple[str, str]] = []
    session = NativeToolReplSession(
        provider=_history_recording_provider(seen),
        tool_registry={},
        prompt_history_store=store,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO("remember me\n")),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert store.entries() == ["remember me"]
    # A fresh store instance recalls the persisted prompt across sessions.
    assert PromptHistoryStore(tmp_path / "history.json").entries() == ["remember me"]


def test_persistent_history_skips_recording_when_disabled(tmp_path: Path):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    store = PromptHistoryStore(tmp_path / "history.json")  # disabled by default
    seen: list[tuple[str, str]] = []
    session = NativeToolReplSession(
        provider=_history_recording_provider(seen),
        tool_registry={},
        prompt_history_store=store,
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO("do not remember\n")),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert store.entries() == []
    assert not (tmp_path / "history.json").exists()


def test_persistent_history_seeds_tui_recall_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    store.record("earlier prompt")
    store.record("later prompt")

    ui = _ui(tmp_path)
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: "",  # immediate EOF
    )
    seen: list[tuple[str, str]] = []
    session = NativeToolReplSession(
        provider=_history_recording_provider(seen),
        tool_registry={},
        prompt_history_store=store,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The fresh TUI session seeds its in-memory recall buffer from disk.
    assert ui.input_history == ["earlier prompt", "later prompt"]


def test_disabled_store_does_not_seed_tui_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    store.record("saved")
    store.set_enabled(False)

    ui = _ui(tmp_path)
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: "",
    )
    seen: list[tuple[str, str]] = []
    session = NativeToolReplSession(
        provider=_history_recording_provider(seen),
        tool_registry={},
        prompt_history_store=store,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # Disabled persistence: the fresh session does not recall the saved prompt.
    assert ui.input_history == []


def test_settings_dialog_toggle_and_clear_mutate_store_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    store.record("old prompt")

    provider = _CountingProvider()
    provider_state = _read_only_provider_state(tmp_path, provider)
    ui = _ui(tmp_path)
    # Seed the live in-memory recall as a fresh enabled session would.
    ui.input_history = list(store.entries())
    scripted = iter(["/settings\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )

    def _fake_dialog(self, rows, *, on_local_action, exit_actions=frozenset(), current_index=None):
        del rows, exit_actions, current_index
        # Simulate the user toggling persistence off, then clearing history.
        on_local_action("toggle_history")
        on_local_action("clear_history")
        return None

    monkeypatch.setattr(ToolLoopTerminalUi, "run_settings_dialog", _fake_dialog)

    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        prompt_history_store=store,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert provider.completions == 0  # no provider turn from /settings actions
    # Toggle disabled persistence; clear wiped the disk store.
    assert store.enabled is False
    assert store.entries() == []
    # The current session's in-memory recall keeps working (clear only wipes the
    # persisted store, not the live recall buffer).
    assert ui.input_history == ["old prompt"]


def test_settings_dialog_theme_row_applies_and_persists_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Selecting the /settings Theme row applies + persists the chosen theme.

    Mirrors the model-selector dialog path: the dialog returns the ``theme``
    action, the session opens the theme selector, and the chosen theme is
    applied (PIPY_THEME repaint) and persisted through settings (the source of
    truth that reload re-reads), running no provider turn.
    """

    from pipy_harness.native.settings import SettingsManager
    from pipy_harness.native.themes import (
        DEFAULT_THEME_NAME,
        NativeThemeStore,
        available_theme_names,
    )

    # Pick the first non-default registered theme so the assertion is meaningful.
    target_theme = next(
        name for name in available_theme_names() if name != DEFAULT_THEME_NAME
    )

    settings = SettingsManager(
        global_path=tmp_path / "settings.json",
        project_path=tmp_path / "project-settings.json",
        env={},
    )
    assert settings.get_theme() is None  # nothing persisted yet

    # Isolate the theme store + PIPY_THEME so the test does not touch real state.
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "native-theme.json"))
    monkeypatch.delenv("PIPY_THEME", raising=False)

    provider = _CountingProvider()
    provider_state = _read_only_provider_state(tmp_path, provider)
    ui = _ui(tmp_path)
    scripted = iter(["/settings\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )

    captured_selector_titles: list[str | None] = []
    captured_exit_actions: list[frozenset[str]] = []

    def _fake_dialog(self, rows, *, on_local_action, exit_actions=frozenset(), current_index=None):
        del current_index
        captured_exit_actions.append(frozenset(exit_actions))
        # The dialog offers a "Theme" action row.
        theme_row = next((row for row in rows if row.action == "theme"), None)
        assert theme_row is not None, "settings dialog exposes no theme row"
        if getattr(_fake_dialog, "fired", False):
            return None
        _fake_dialog.fired = True  # type: ignore[attr-defined]
        # Faithfully mirror the real dialog's routing: an exit action closes the
        # dialog and is returned for the caller's post-return branch; a non-exit
        # action is handled locally via on_local_action and the dialog stays
        # open. If "theme" were NOT wired as an exit action, it would route
        # through on_local_action (a no-op) and the selector would never open —
        # so this branch is what makes the test guard the routing bug.
        if "theme" in exit_actions:
            return "theme"
        on_local_action("theme")  # no-op for a non-exit action: selector unopened
        return None

    monkeypatch.setattr(ToolLoopTerminalUi, "run_settings_dialog", _fake_dialog)

    def _fake_model_selector(self, options, *, current_index=0, title=None):
        captured_selector_titles.append(title)
        # The selector lists every registered theme; pick the target one.
        labels = [option.label for option in options]
        return next(i for i, label in enumerate(labels) if target_theme in label)

    monkeypatch.setattr(ToolLoopTerminalUi, "run_model_selector", _fake_model_selector)

    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        settings_manager=settings,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert provider.completions == 0  # theme selection runs no provider turn
    # Routing guard: "theme" must be wired as an exit action so the dialog
    # returns it (rather than handling it locally) and the post-return branch
    # opens the selector. Without this the theme picker never opens.
    assert captured_exit_actions
    assert all("theme" in actions for actions in captured_exit_actions)
    # Settings is the source of truth: the chosen theme is persisted there so a
    # /reload re-reads it.
    assert settings.get_theme() == target_theme
    # The live frame repaints with the new palette because PIPY_THEME is set.
    assert os.environ["PIPY_THEME"] == target_theme
    # The chrome theme store also carries the choice (legacy persistence path).
    assert NativeThemeStore(tmp_path / "native-theme.json").load() == target_theme
    # The overlay is labelled for themes, not "provider/model".
    assert captured_selector_titles
    assert all(
        title is not None and "theme" in title.lower()
        for title in captured_selector_titles
    )


def test_settings_dialog_theme_row_works_for_static_provider_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The Theme row surfaces and routes for a static (non-native) state too.

    Theme is a display concern independent of provider state: the row lives in
    the provider-agnostic Display section (gated only on a live TUI), and
    ``"theme"`` is an exit action for every state. A session built with only a
    ``provider`` (no ``provider_state``) drives the ``StaticNativeReplProviderState``
    fallback, which has no /model//login//logout — so without a provider-agnostic
    theme row + exit action it would have NO way to reach the theme picker.
    """

    from pipy_harness.native.settings import SettingsManager
    from pipy_harness.native.themes import (
        DEFAULT_THEME_NAME,
        NativeThemeStore,
        available_theme_names,
    )

    target_theme = next(
        name for name in available_theme_names() if name != DEFAULT_THEME_NAME
    )

    settings = SettingsManager(
        global_path=tmp_path / "settings.json",
        project_path=tmp_path / "project-settings.json",
        env={},
    )
    assert settings.get_theme() is None

    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "native-theme.json"))
    monkeypatch.delenv("PIPY_THEME", raising=False)

    provider = _CountingProvider()
    ui = _ui(tmp_path)
    scripted = iter(["/settings\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )

    captured_exit_actions: list[frozenset[str]] = []
    captured_theme_row_labels: list[str] = []

    def _fake_dialog(self, rows, *, on_local_action, exit_actions=frozenset(), current_index=None):
        del current_index
        captured_exit_actions.append(frozenset(exit_actions))
        theme_row = next((row for row in rows if row.action == "theme"), None)
        assert theme_row is not None, (
            "static-state settings dialog exposes no theme row"
        )
        captured_theme_row_labels.append(theme_row.label)
        if getattr(_fake_dialog, "fired", False):
            return None
        _fake_dialog.fired = True  # type: ignore[attr-defined]
        # Mirror the real routing: only an exit action reaches the post-return
        # branch that opens the selector.
        if "theme" in exit_actions:
            return "theme"
        on_local_action("theme")
        return None

    monkeypatch.setattr(ToolLoopTerminalUi, "run_settings_dialog", _fake_dialog)

    def _fake_model_selector(self, options, *, current_index=0, title=None):
        labels = [option.label for option in options]
        return next(i for i, label in enumerate(labels) if target_theme in label)

    monkeypatch.setattr(ToolLoopTerminalUi, "run_model_selector", _fake_model_selector)

    # Build the session with no provider_state, forcing the static fallback.
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        settings_manager=settings,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert provider.completions == 0
    # The static-state dialog must surface the theme row AND wire it as an exit
    # action, so the picker is reachable even with no /model//login//logout.
    assert captured_theme_row_labels
    assert captured_exit_actions
    assert all("theme" in actions for actions in captured_exit_actions)
    # End-to-end: the chosen theme applied and persisted via the static path too.
    assert settings.get_theme() == target_theme
    assert os.environ["PIPY_THEME"] == target_theme
    assert NativeThemeStore(tmp_path / "native-theme.json").load() == target_theme


def test_persistent_history_contents_stay_out_of_session_archive(tmp_path: Path):
    from pipy_harness.native.prompt_history import PromptHistoryStore

    history_path = tmp_path / "state" / "history.json"
    store = PromptHistoryStore(history_path)
    store.set_enabled(True)
    seen: list[tuple[str, str]] = []
    session = NativeToolReplSession(
        provider=_history_recording_provider(seen),
        tool_registry={},
        prompt_history_store=store,
    )

    secret_prompt = "my-secret-prompt-body-XYZZY"
    result = session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO(f"{secret_prompt}\n")),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The metadata-first result carries only counters/labels — never the prompt.
    assert secret_prompt not in repr(result)
    # The prompt lives only in the dedicated local prompt-history file, which is
    # not part of the session archive.
    files_with_prompt = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and secret_prompt in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert files_with_prompt == [history_path]


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
        cancel_token: object = None,
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


def test_tool_loop_help_is_alias_of_hotkeys():
    """`/help` renders the same keyboard-shortcut table as `/hotkeys`."""

    from pipy_harness.native.keybindings import KeybindingsManager, render_hotkeys

    provider = _AnswerProvider("ignored")
    session = NativeToolReplSession(provider=provider, tool_registry={})
    error_stream = io.StringIO()
    session.run(
        workspace_root=Path("."),
        input_stream=io.StringIO("/help\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    hotkeys_text = render_hotkeys(KeybindingsManager.create())
    assert hotkeys_text in error_stream.getvalue()


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
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
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
    from pipy_harness.native.tui import TURN_SETTLED

    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "wait_for_active_turn_interrupt",
        lambda self, done_event, abort_event, **kwargs: (
            done_event.wait(5),
            TURN_SETTLED,
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
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
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
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
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


# --------------------------------------------------------------------------- #
# Editor ergonomics: prompt history, bracketed paste, undo/redo, resize.
# --------------------------------------------------------------------------- #


def _decode_key(ui: ToolLoopTerminalUi, data: bytes) -> str | None:
    """Feed raw bytes through the real key decoder over an OS pipe."""

    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    try:
        return ui._read_key(read_fd)
    finally:
        os.close(read_fd)


def test_tui_key_decoder_reads_complete_utf8_character(tmp_path: Path):
    ui = _ui(tmp_path)

    assert _decode_key(ui, "ö".encode("utf-8")) == "ö"


def test_tui_key_decoder_handles_malformed_utf8_without_crashing(tmp_path: Path):
    ui = _ui(tmp_path)

    assert _decode_key(ui, b"\xff") == "�"


def test_tui_key_if_available_reads_pending_byte_without_fd_activity(tmp_path: Path):
    ui = _ui(tmp_path)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\xc3(")
    try:
        assert ui._read_key(read_fd) == "�"

        assert ui._read_key_if_available(read_fd, 0.0) == "("
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_tui_prompt_history_up_down_recall(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._record_history("first prompt")
    ui._record_history("second prompt")

    # A half-typed draft is preserved when history navigation begins.
    ui.input_text = "draft"
    ui.input_cursor = len("draft")

    ui._navigate_history("up")
    assert ui.input_text == "second prompt"
    assert ui.input_cursor == len("second prompt")

    ui._navigate_history("up")
    assert ui.input_text == "first prompt"

    ui._navigate_history("down")
    assert ui.input_text == "second prompt"

    # Stepping past the newest entry restores the preserved draft.
    ui._navigate_history("down")
    assert ui.input_text == "draft"
    assert ui._history_nav_index is None


def test_tui_history_dedupes_and_skips_blank(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._record_history("   ")  # blank-after-strip: ignored
    ui._record_history("alpha")
    ui._record_history("alpha")  # consecutive duplicate: ignored
    ui._record_history("beta")
    assert ui.input_history == ["alpha", "beta"]


def test_tui_history_is_in_memory_only(tmp_path: Path):
    """History is a plain in-process list — never persisted to disk.

    The metadata-first archive contract forbids persisting prompt bodies by
    default, so recall state must live only in memory.
    """

    ui = _ui(tmp_path)
    before = {entry.name for entry in tmp_path.iterdir()}
    ui._record_history("super secret prompt")
    after = {entry.name for entry in tmp_path.iterdir()}
    assert before == after  # nothing written
    assert isinstance(ui.input_history, list)
    assert not hasattr(ui, "history_path")


def test_tui_navigate_history_noop_without_entries(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.input_text = "kept"
    ui._navigate_history("up")
    assert ui.input_text == "kept"
    ui._navigate_history("down")
    assert ui.input_text == "kept"


def test_tui_bracketed_paste_decodes_as_literal_multiline(tmp_path: Path):
    ui = _ui(tmp_path)
    key = _decode_key(ui, b"\x1b[200~line one\nline two\x1b[201~")
    assert key == "paste"
    assert ui._pending_paste == "line one\nline two"


def test_tui_bracketed_paste_normalizes_crlf(tmp_path: Path):
    ui = _ui(tmp_path)
    key = _decode_key(ui, b"\x1b[200~a\r\nb\rc\x1b[201~")
    assert key == "paste"
    assert ui._pending_paste == "a\nb\nc"


def test_tui_paste_inserts_without_submission_or_menu(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._insert_paste("/not-a-command and more\nsecond line")
    # The whole paste is inserted literally, including the newline.
    assert ui.input_text == "/not-a-command and more\nsecond line"
    # A paste with whitespace never opens the slash menu (so it cannot be
    # mistaken for / command completion), and never submits on its own.
    assert ui.slash_menu_open is False


def test_tui_multiline_paste_renders_as_single_input_row(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 status")
    ui._insert_paste("line one\nline two")

    frame = ui._frame_lines(width=72, height=16, pad=False)
    texts = [line.text for line in frame]

    # No raw newline ever leaks into a frame line (which would spill the input
    # cell onto extra physical rows and desync the live-height/erase math).
    assert all("\n" not in text for text in texts)
    # The embedded newline renders as exactly one visible glyph on one row.
    input_rows = [index for index, line in enumerate(frame) if line.kind == "input"]
    assert len(input_rows) == 1
    input_index = input_rows[0]
    assert "line one⏎line two" in texts[input_index]
    # The input row stays framed by separators with the footer directly below.
    assert set(texts[input_index - 1].strip()) == {"─"}
    assert set(texts[input_index + 1].strip()) == {"─"}
    assert "~/projects/pipy" in texts[input_index + 2]
    # The literal buffer is preserved verbatim for submission.
    assert ui.input_text == "line one\nline two"

    # After another keypress the buffer still holds the literal newline and the
    # frame still renders exactly one input row with no leaked newline.
    ui._insert_input_text("!")
    assert ui.input_text == "line one\nline two!"
    texts_after = [line.text for line in ui._frame_lines(width=72, height=16, pad=False)]
    assert all("\n" not in text for text in texts_after)
    assert sum(line.kind == "input" for line in ui._frame_lines(width=72, height=16))

    # After undoing back to the bare paste, still a single coherent input row.
    ui._undo_edit()
    assert ui.input_text == "line one\nline two"
    undo_frame = ui._frame_lines(width=72, height=16, pad=False)
    undo_rows = [i for i, line in enumerate(undo_frame) if line.kind == "input"]
    assert len(undo_rows) == 1
    assert all("\n" not in line.text for line in undo_frame)


def test_tui_long_multiline_input_wraps_with_literal_newline_projection(
    tmp_path: Path,
):
    ui = _ui(tmp_path)
    pasted = "abc\ndefghijklmnopqrstuvwxyz"
    ui._insert_paste(pasted)

    width = 20
    input_rows = ui._input_frame_lines(width)
    rendered = "".join(row.text.strip() for row in input_rows)

    assert len(input_rows) >= 2
    assert "⏎" in rendered
    assert "\n" not in rendered
    assert rendered == ui._display_input_text(pasted)
    assert ui.input_text == pasted

    cursor_rows = [row for row in input_rows if row.meta is not None]
    assert len(cursor_rows) == 1
    assert cursor_rows[0].meta == {"cursor_col": len(pasted) % (width - 1)}


def test_tui_long_input_soft_wraps_inside_input_frame(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 status")
    ui.input_text = "".join(str(i % 10) for i in range(120))
    ui.input_cursor = len(ui.input_text)

    width = 88
    frame = ui._frame_lines(width=width, height=16, pad=False)
    texts = [line.text for line in frame]

    input_rows = [i for i, line in enumerate(frame) if line.kind == "input"]
    assert len(input_rows) == 2
    assert all(len(text) <= width for text in texts)
    assert texts[input_rows[0]] == ui.input_text[: width - 1]
    assert texts[input_rows[1]] == ui.input_text[width - 1 :]
    assert set(texts[input_rows[0] - 1].strip()) == {"─"}
    assert set(texts[input_rows[-1] + 1].strip()) == {"─"}
    assert "~/projects/pipy" in texts[input_rows[-1] + 2]

    cursor_rows = [
        line for line in frame if line.kind == "input" and line.meta is not None
    ]
    assert len(cursor_rows) == 1
    assert cursor_rows[0].meta == {"cursor_col": len(ui.input_text) % (width - 1)}


def test_tui_wrapped_input_window_keeps_cursor_visible(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.input_text = "x" * 200
    width = 80

    ui.input_cursor = 50
    rows = ui._input_frame_lines(width, max_rows=2)
    assert len(rows) == 2
    assert rows[0].meta == {"cursor_col": 50}
    assert rows[1].meta is None

    ui.input_cursor = 0
    rows = ui._input_frame_lines(width, max_rows=2)
    assert rows[0].meta == {"cursor_col": 0}

    ui.input_cursor = len(ui.input_text)
    rows = ui._input_frame_lines(width, max_rows=2)
    assert len(rows) == 2
    cursor_rows = [line for line in rows if line.meta is not None]
    assert len(cursor_rows) == 1
    assert cursor_rows[0].meta == {"cursor_col": len(ui.input_text) % (width - 1)}
    assert rows[-1].meta == cursor_rows[0].meta


def test_tui_display_input_text_projects_control_chars_one_to_one(tmp_path: Path):
    ui = _ui(tmp_path)
    projected = ui._display_input_text("a\nb\tc")
    # 1:1 projection keeps the cursor column aligned with the logical index.
    assert len(projected) == len("a\nb\tc")
    assert projected == "a⏎b c"
    # Plain text is returned unchanged (no allocation on the common path).
    assert ui._display_input_text("plain text") == "plain text"


def test_tui_arrow_and_ctrl_keys_decode(tmp_path: Path):
    ui = _ui(tmp_path)
    assert _decode_key(ui, b"\x1b[A") == "up"
    assert _decode_key(ui, b"\x1b[B") == "down"
    assert _decode_key(ui, b"\x1b[C") == "right"
    assert _decode_key(ui, b"\x1b[D") == "left"
    assert _decode_key(ui, b"\x1a") == "ctrl-z"
    assert _decode_key(ui, b"\x19") == "ctrl-y"


def test_tui_undo_redo_restores_line_state(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._reset_line_editor_state()
    ui._insert_input_text("a")
    ui._insert_input_text("b")
    ui._insert_input_text("c")
    assert ui.input_text == "abc"

    ui._undo_edit()
    assert ui.input_text == "ab"
    ui._undo_edit()
    assert ui.input_text == "a"

    ui._redo_edit()
    assert ui.input_text == "ab"

    # A fresh edit clears the redo stack.
    ui._insert_input_text("x")
    assert ui.input_text == "abx"
    ui._redo_edit()
    assert ui.input_text == "abx"


def test_tui_undo_treats_paste_as_single_step(tmp_path: Path):
    ui = _ui(tmp_path)
    ui._reset_line_editor_state()
    ui._insert_paste("hello world")
    assert ui.input_text == "hello world"
    ui._undo_edit()
    assert ui.input_text == ""


def _pin_terminal_size(
    monkeypatch: pytest.MonkeyPatch, columns: int, rows: int
) -> None:
    # Clear COLUMNS/LINES so the shutil fallback (which _TtyBuffer lacks a
    # fileno for) is what resolves the size, then pin that.
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    size = os.terminal_size((columns, rows))
    monkeypatch.setattr(
        "pipy_harness.native.tui.shutil.get_terminal_size", lambda *a, **k: size
    )


def test_tui_resize_poll_repaints_on_size_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ui = _ui(tmp_path)
    _pin_terminal_size(monkeypatch, 100, 40)
    ui.paint()  # establishes the painted size at the current dimensions
    assert ui._last_painted_size == (100, 40)
    buffer = cast(_TtyBuffer, ui.terminal_stream)
    before = len(buffer.getvalue())

    _pin_terminal_size(monkeypatch, 80, 24)
    assert ui._poll_resize_repaint() is True
    # A real repaint happened at the new size (no alternate screen involved).
    assert ui._last_painted_size == (80, 24)
    assert len(buffer.getvalue()) > before
    assert "\x1b[?1049h" not in buffer.getvalue()

    # No further repaint when the size is unchanged and no SIGWINCH is pending.
    assert ui._poll_resize_repaint() is False


def test_tui_resize_poll_repaints_on_pending_signal_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ui = _ui(tmp_path)
    _pin_terminal_size(monkeypatch, 80, 24)
    ui.paint()
    assert ui._poll_resize_repaint() is False  # steady state: no repaint

    ui._on_resize_signal(28, None)  # SIGWINCH-style flag flip
    assert ui._resize_pending is True
    assert ui._poll_resize_repaint() is True
    assert ui._resize_pending is False


# --------------------------------------------------------------------------- #
# /login and /logout in the tool-loop TUI (auth boundary, no provider turn).
# --------------------------------------------------------------------------- #


class _FakeOpenAICodexAuthManager:
    """Records login/logout against a credentials file, no real OAuth."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.logins = 0
        self.logouts = 0

    def login_interactive(self, *, input_stream, output_stream, open_browser=True):
        del input_stream, output_stream, open_browser
        self.logins += 1
        self._path.write_text("{}", encoding="utf-8")
        return None

    def logout(self) -> bool:
        self.logouts += 1
        if self._path.exists():
            self._path.unlink()
            return True
        return False


def _auth_provider_state(tmp_path: Path, provider: ProviderPort, auth_path: Path):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState
    from pipy_harness.native.openai_codex_provider import OpenAICodexAuthManager

    manager = _FakeOpenAICodexAuthManager(auth_path)
    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda selection: provider,
        auth_manager_factory=lambda: cast(OpenAICodexAuthManager, manager),
        env={},
        openai_codex_auth_path=auth_path,
        persist_defaults=False,
    )
    return state, manager


def _codex_option(state: object) -> NativeModelOption:
    return next(
        option
        for option in state.model_options()  # type: ignore[attr-defined]
        if option.selection.provider_name == "openai-codex"
    )


def test_tui_login_refreshes_availability_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    auth_path = tmp_path / "openai-codex.json"
    provider = _CountingProvider()
    provider_state, manager = _auth_provider_state(tmp_path, provider, auth_path)
    ui = _ui(tmp_path)
    scripted = iter(["/login\n", ""])
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
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    # Before login, openai-codex is unavailable (no credentials).
    assert _codex_option(provider_state).available is False

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    # Auth-only command: no provider turn, no tool invocation.
    assert provider.completions == 0
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    # The login ran through the auth boundary and availability refreshed.
    assert manager.logins == 1
    assert _codex_option(provider_state).available is True
    notices = [lines for kind, lines in ui._history_blocks if kind == "notice"]
    assert any("login stored" in " ".join(lines).lower() for lines in notices)


def test_tui_logout_removes_credentials_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    auth_path = tmp_path / "openai-codex.json"
    auth_path.write_text("{}", encoding="utf-8")  # start logged in
    provider = _CountingProvider()
    provider_state, manager = _auth_provider_state(tmp_path, provider, auth_path)
    ui = _ui(tmp_path)
    scripted = iter(["/logout\n", ""])
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
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    assert _codex_option(provider_state).available is True

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert provider.completions == 0
    assert result.user_turn_count == 0
    assert manager.logouts == 1
    assert not auth_path.exists()
    assert _codex_option(provider_state).available is False
    notices = [lines for kind, lines in ui._history_blocks if kind == "notice"]
    assert any("removed" in " ".join(lines).lower() for lines in notices)


def test_aborted_turn_appends_no_assistant_observation_to_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An aborted turn must not leave a misleading assistant/tool observation.

    The first turn is cancelled mid-flight; the second turn records the
    provider-visible message history it receives. That history must contain the
    two user messages but no AssistantMessage from the aborted turn, so the
    session never reflects a successful response that did not happen.
    """

    from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError

    seen_message_types: list[list[str]] = []

    class _AbortThenAnswerProvider:
        name = "fake"
        model_id = "fake-native-bootstrap"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> ProviderResult:
            del stream_sink, reasoning_sink
            self.calls += 1
            if self.calls == 1:
                # Block until the tool loop cancels this turn at the boundary.
                assert cancel_token is not None
                assert cancel_token.event.wait(timeout=5)
                raise ProviderCancelledError("native provider turn cancelled")
            seen_message_types.append(
                [type(message).__name__ for message in request.messages]
            )
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="answer after abort",
                tool_calls=(),
            )

    provider = _AbortThenAnswerProvider()
    provider_state = _read_only_provider_state(tmp_path, cast(ProviderPort, provider))
    ui = _ui(tmp_path)
    scripted = iter(["first prompt\n", "second prompt\n", ""])
    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "read_line",
        lambda self, prompt_label, *, footer=None: next(scripted),
    )
    # Abort only the first active turn; later turns run to completion.
    interrupt_calls = {"n": 0}

    def _fake_interrupt(self, done_event, abort_event, **kwargs):
        from pipy_harness.native.tui import TURN_ABORTED, TURN_SETTLED

        interrupt_calls["n"] += 1
        if interrupt_calls["n"] == 1:
            abort_event.set()
            return TURN_ABORTED
        done_event.wait(5)
        return TURN_SETTLED

    monkeypatch.setattr(
        ToolLoopTerminalUi, "wait_for_active_turn_interrupt", _fake_interrupt
    )
    session = NativeToolReplSession(
        provider=cast(ProviderPort, provider),
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert provider.calls == 2
    # The second turn saw both user messages but NO assistant message from the
    # aborted first turn — the abort recorded no successful observation.
    assert seen_message_types == [["UserMessage", "UserMessage"]]
    assert "AssistantMessage" not in seen_message_types[0]
    # The aborted state was rendered to the user.
    errors = [lines for kind, lines in ui._history_blocks if kind == "error"]
    assert any("Operation aborted" in " ".join(lines) for lines in errors)
