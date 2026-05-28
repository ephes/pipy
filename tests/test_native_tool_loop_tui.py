"""Focused tests for the pipy-owned tool-loop terminal UI shell."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderToolCall
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
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

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "Working..." not in frame
    assert frame.count("hello world") == 1


def test_tui_renderer_keeps_tool_blocks_in_history_region(tmp_path: Path):
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

    frame = "\n".join(ui.render_lines(width=72, height=14))
    assert "$ read docs/backlog.md:1-5" in frame
    assert "line one" in frame
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

    frame = ui.render_lines(width=72, height=14, pad=False)
    input_index = next(index for index, line in enumerate(frame) if line == " ")

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

    frame = ui.render_lines(width=100, height=30, pad=False)
    prompt_index = next(index for index, line in enumerate(frame) if "Use the ls" in line)
    input_index = next(index for index, line in enumerate(frame) if line == " ")

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


def test_tui_start_uses_alternate_screen_and_close_restores(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.start()
    ui.close()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "\x1b[?1049h" in output
    assert "\x1b[?1049l" in output


def test_tui_paint_uses_explicit_carriage_returns_for_raw_mode(tmp_path: Path):
    ui = _ui(tmp_path)

    ui.paint()

    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    assert "\x1b[K\r\n" in output
    assert "\x1b[K\n" not in output


def test_tui_paint_places_live_cursor_on_input_row(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.submit_user_message("hello world!")
    ui.set_working("⠋ Working...")
    ui.input_text = "next"

    ui.paint()

    width, height = ui._dimensions()
    frame = ui._frame_lines(width=width, height=height, pad=False)
    expected_row = next(
        index + 1 for index, line in enumerate(frame) if line.kind == "input"
    )
    output = cast(_TtyBuffer, ui.terminal_stream).getvalue()
    cursor_moves = re.findall(r"\x1b\[(\d+);(\d+)H", output)
    assert cursor_moves[-1] == (str(expected_row), "5")


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
