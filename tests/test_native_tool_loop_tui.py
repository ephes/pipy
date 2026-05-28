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
    assert "tool  read docs/backlog.md:1-5" in frame
    assert "line one" in frame
    assert "Took 0.2s" in frame


def test_tui_preserves_input_and_footer_when_history_overflows(tmp_path: Path):
    ui = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message("use a tool")
    ui.add_tool_call("tool  ls .")
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
