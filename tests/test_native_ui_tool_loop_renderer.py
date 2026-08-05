"""Unit tests for the tool-loop renderer component.

These drive ``TuiToolLoopRenderer`` directly against a real transcript
component and a plain :class:`ExtensionChromeState` record (no terminal
shell, no PTY) to prove the collapsed port: spinner and working chrome read
straight off the chrome record, every commit lands on the transcript, and the
frame width / styling stream arrive as injected values. Frame-level and
extension-renderer coverage lives in ``test_native_tool_loop_tui.py``,
``test_native_extension_tool_renderer.py`` and its PTY sibling.
"""

from __future__ import annotations

import io
import time

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
    ProductContent,
)
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock


class _Harness:
    """A renderer wired to a real transcript and a plain chrome record."""

    def __init__(self) -> None:
        self.repaints = 0
        self.chrome = ExtensionChromeState()
        self.transcript = TranscriptComponent(
            PaintLock(),
            self._repaint,
            reset_scrollback=lambda: None,
            frame_width=lambda: 80,
            render_theme=lambda: None,
        )
        self.renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=self.chrome,
            terminal_stream=io.StringIO(),
            frame_width=lambda: 80,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def wait_for_working_text(self, deadline_seconds: float = 1.0) -> str:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if self.transcript.working_text:
                return self.transcript.working_text
            time.sleep(0.01)
        return self.transcript.working_text


def _tool_call(name: str) -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id=f"corr-{name}",
        tool_name=name,
        arguments_json=ProductContent("{}"),
    )


def test_spinner_defaults_come_from_the_plain_renderer() -> None:
    h = _Harness()
    frames, interval = h.renderer._effective_spinner()
    assert frames == TuiToolLoopRenderer._SPINNER_FRAMES
    assert interval == TuiToolLoopRenderer._SPINNER_INTERVAL_SECONDS


def test_spinner_reads_override_off_the_chrome_record() -> None:
    h = _Harness()
    h.chrome.indicator_frames = ("★",)
    h.chrome.indicator_interval_ms = 50.0
    frames, interval = h.renderer._effective_spinner()
    assert frames == ("★",)
    assert interval == 0.05


def test_spinner_empty_frames_hide_the_glyph_but_keep_the_message() -> None:
    h = _Harness()
    h.chrome.indicator_frames = ()
    frames, _interval = h.renderer._effective_spinner()
    assert frames == ("",)


def test_show_working_writes_the_chrome_message_to_the_transcript() -> None:
    h = _Harness()
    h.chrome.working_message = "Checking"
    h.renderer.show_working()
    try:
        assert "Checking" in h.wait_for_working_text()
    finally:
        h.renderer._stop_working(clear=True)
    assert h.transcript.working_text == ""


def test_show_working_respects_the_chrome_visibility_gate() -> None:
    h = _Harness()
    h.chrome.working_visible = False
    h.renderer.show_working()
    assert h.renderer._working_thread is None
    assert h.transcript.working_text == ""


def test_streamed_any_tracks_assistant_output() -> None:
    h = _Harness()
    h.renderer.begin_provider_turn()
    assert h.renderer.streamed_any is False
    h.renderer.stream_sink("hello")
    assert h.renderer.streamed_any is True
    assert h.transcript.assistant_text == "hello"
    h.renderer.begin_provider_turn()
    assert h.renderer.streamed_any is False
    h.renderer.render_buffered_assistant_text("world", has_tool_calls=False)
    assert h.renderer.streamed_any is True


def test_operator_abort_commits_the_aborted_notice() -> None:
    h = _Harness()
    h.renderer.cancel_assistant_message(AgentCancellationReason.OPERATOR_ABORT)
    assert ("error", ("Operation aborted",)) in h.transcript.history_blocks


def test_read_result_is_collapsed_and_errors_are_not() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_tool_call("read"))
    h.renderer.render_tool_result(output_text="line one\nline two", is_error=False)
    kinds = [kind for kind, _lines in h.transcript.history_blocks]
    assert "tool_result" not in kinds  # successful read collapses to its header

    h.renderer.render_tool_call(_tool_call("read"))
    h.renderer.render_tool_result(output_text="boom", is_error=True)
    kinds = [kind for kind, _lines in h.transcript.history_blocks]
    assert kinds.count("tool_result") == 1  # a failed read still shows output


def test_ls_result_lines_drop_the_kind_prefixes() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_tool_call("ls"))
    h.renderer.render_tool_result(
        output_text="file a.txt\ndirectory sub\nother pipe\nplain",
        is_error=False,
    )
    result = [
        lines for kind, lines in h.transcript.history_blocks if kind == "tool_result"
    ]
    assert result == [("a.txt", "sub", "pipe", "plain")]


def test_long_result_previews_unless_the_transcript_is_expanded() -> None:
    h = _Harness()
    output = "\n".join(f"line {n}" for n in range(1, 9))
    h.renderer.render_tool_call(_tool_call("bash"))
    h.renderer.render_tool_result(output_text=output, is_error=False)
    collapsed = next(
        lines for kind, lines in h.transcript.history_blocks if kind == "tool_result"
    )
    assert collapsed[0] == "... (3 earlier lines, ctrl+o to expand)"
    assert collapsed[1:] == ("line 4", "line 5", "line 6", "line 7", "line 8")

    h.transcript.set_tools_expanded(True)
    h.renderer.render_tool_call(_tool_call("bash"))
    h.renderer.render_tool_result(output_text=output, is_error=False)
    expanded = [
        lines for kind, lines in h.transcript.history_blocks if kind == "tool_result"
    ][-1]
    assert expanded == tuple(f"line {n}" for n in range(1, 9))


def test_tool_output_streams_into_the_live_region() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_tool_call("bash"))
    h.renderer.tool_output_sink("...... [ 50%]\n")
    assert "[ 50%]" in h.transcript.tool_output_text
    h.renderer.render_tool_result(output_text="done", is_error=False)
    assert h.transcript.tool_output_text == ""
