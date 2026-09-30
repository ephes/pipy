"""Unit tests for the tool-loop renderer component.

These drive ``TuiToolLoopRenderer`` directly against a real transcript
component and a plain :class:`ExtensionChromeState` record (no terminal
shell, no PTY) to prove the collapsed port: spinner and working chrome read
straight off the chrome record, every commit lands on the transcript, and the
frame width / styling stream arrive as injected values. Frame-level and
extension-renderer coverage lives in ``test_native_coding_session_terminal.py``,
``test_native_extension_tool_renderer.py`` and its PTY sibling.
"""

from __future__ import annotations

import io
import json
import re
import threading
import time
from collections.abc import Callable

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
    ProductContent,
)
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.frame_renderer import decode_tool_box_line
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs

_SGR = re.compile(r"\x1b\[[0-9;]*m")


class _Harness:
    """A renderer wired to a real transcript and a plain chrome record."""

    def __init__(self) -> None:
        self.repaints = 0
        self.chrome = ExtensionChromeState()
        self.render_inputs = ScreenRenderInputs(
            lambda: 80, io.StringIO(), self._expanded
        )
        # A fake clock and a ticker the test drives (Pi's 1 s `setInterval`).
        self.now = 100.0
        self.ticks: list[Callable[[], None]] = []
        self.cancelled = 0
        self.transcript = TranscriptComponent(
            PaintLock(threading.RLock()),
            self._repaint,
            reset_scrollback=lambda: None,
            render_inputs=self.render_inputs,
            clock=lambda: self.now,
            schedule_ticker=self._schedule_ticker,
        )
        self.renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=self.chrome,
            render_inputs=self.render_inputs,
        )

    def _schedule_ticker(self, tick: Callable[[], None]) -> Callable[[], None]:
        self.ticks.append(tick)

        def cancel() -> None:
            self.cancelled += 1
            self.ticks.remove(tick)

        return cancel

    def pending_rows(self) -> list[tuple[str, str]]:
        return [
            (bg, _SGR.sub("", text))
            for bg, _wrap, text in map(
                decode_tool_box_line, self.transcript.pending_tool_lines
            )
            if text
        ]

    def _expanded(self) -> bool:
        return self.transcript.tools_expanded

    def _repaint(self) -> None:
        self.repaints += 1

    def wait_for_working_text(self, deadline_seconds: float = 1.0) -> str:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if self.transcript.working_text:
                return self.transcript.working_text
            time.sleep(0.01)
        return self.transcript.working_text


def _call(name: str, args: dict[str, object]) -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id=f"corr-{name}",
        tool_name=name,
        arguments_json=ProductContent(json.dumps(args)),
    )


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


def _rows(h: _Harness) -> list[list[tuple[str, str]]]:
    """``(background, text)`` of each committed tool row's non-blank lines."""

    rows = []
    for kind, lines in h.transcript.history_blocks:
        if kind != "tool_box":
            continue
        decoded = (decode_tool_box_line(line) for line in lines)
        rows.append([(bg, _SGR.sub("", text)) for bg, _wrap, text in decoded if text])
    return rows


def test_read_result_is_collapsed_and_errors_are_not() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_call("read", {"path": "a.txt"}))
    h.renderer.render_tool_result(output_text="line one\nline two", is_error=False)
    h.renderer.render_tool_call(_call("read", {"path": "b.txt"}))
    h.renderer.render_tool_result(output_text="boom", is_error=True)

    # Pi read: a collapsed success shows only its header; an error its text.
    assert _rows(h) == [
        [("success", "read a.txt")],
        [("error", "read b.txt"), ("error", "boom")],
    ]


def test_ls_rows_show_the_output_under_the_header() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_call("ls", {"path": "src"}))
    h.renderer.render_tool_result(output_text="a.txt\nsub/", is_error=False)
    assert _rows(h) == [
        [("success", "ls src"), ("success", "a.txt"), ("success", "sub/")]
    ]


def test_bash_previews_the_last_lines_unless_the_transcript_is_expanded() -> None:
    h = _Harness()
    output = "\n".join(f"line {n}" for n in range(1, 9))
    h.renderer.render_tool_call(_call("bash", {"command": "seq"}))
    h.renderer.render_tool_result(output_text=output, is_error=False)
    assert _rows(h)[0] == [
        ("success", "$ seq"),
        ("success", "... (3 earlier lines, ctrl+o to expand)"),
        *[("success", f"line {n}") for n in range(4, 9)],
    ]

    h.transcript.set_tools_expanded(True)
    h.renderer.render_tool_call(_call("bash", {"command": "seq"}))
    h.renderer.render_tool_result(output_text=output, is_error=False)
    assert _rows(h)[-1] == [
        ("success", "$ seq"),
        *[("success", f"line {n}") for n in range(1, 9)],
    ]


def test_tool_output_streams_into_the_pending_row() -> None:
    """A running bash call shows its output in its pending box (Pi partial)."""

    h = _Harness()
    h.renderer.render_tool_call(_call("bash", {"command": "just test"}))
    h.renderer.tool_output_sink("...... [ 50%]\n")
    assert h.pending_rows() == [
        ("pending", "$ just test"),
        ("pending", "...... [ 50%]"),
        ("pending", "Elapsed 0.0s"),
    ]
    assert h.transcript.tool_output_text == ""
    h.renderer.render_tool_result(output_text="done", is_error=False)
    assert h.transcript.pending_tool_lines == ()
    assert _rows(h) == [[("success", "$ just test"), ("success", "done")]]


def test_running_bash_row_ticks_elapsed_until_the_result() -> None:
    """Pi's bash renderer: `Elapsed` from the start, re-rendered every second."""

    h = _Harness()
    h.renderer.render_tool_call(_call("bash", {"command": "sleep 3"}))
    # Pi's bash tool sends an empty update first: the row shows `Elapsed`
    # before any output.
    assert h.pending_rows() == [
        ("pending", "$ sleep 3"),
        ("pending", "Elapsed 0.0s"),
    ]
    assert len(h.ticks) == 1
    repaints = h.repaints
    h.now += 1.04
    h.ticks[0]()
    assert h.pending_rows()[-1] == ("pending", "Elapsed 1.0s")
    assert h.repaints == repaints + 1
    h.now += 62
    h.ticks[0]()
    assert h.pending_rows()[-1] == ("pending", "Elapsed 1m 3s")
    h.renderer.render_tool_result(output_text="", is_error=False, duration_seconds=63.2)
    # The result stops the ticker; the row shows `Took`.
    assert h.ticks == [] and h.cancelled == 1
    assert _rows(h) == [[("success", "$ sleep 3"), ("success", "Took 1m 3s")]]


def test_flushed_bash_row_freezes_elapsed_and_stops_ticking() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_call("bash", {"command": "sleep 9"}))
    h.now += 2.5
    h.transcript.flush_pending_tool()
    assert h.ticks == [] and h.cancelled == 1
    h.now += 30
    h.transcript.set_tools_expanded(True)
    assert _rows(h) == [[("pending", "$ sleep 9"), ("pending", "Elapsed 2.5s")]]


def test_other_tools_and_replayed_bash_rows_do_not_tick() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_call("read", {"path": "a.txt"}))
    assert h.ticks == []
    assert h.pending_rows() == [("pending", "read a.txt")]
    h.renderer.render_tool_result(output_text="x", is_error=False)
    replay = h.renderer.detached(h.transcript)
    replay.render_tool_call(_call("bash", {"command": "seq 3"}))
    # A restored call never started here: no `Elapsed`, no ticker (Pi has no
    # `startedAt` without `executionStarted`).
    assert h.ticks == []
    assert h.pending_rows() == [("pending", "$ seq 3")]


def test_a_new_session_render_stops_the_ticker() -> None:
    h = _Harness()
    h.renderer.render_tool_call(_call("bash", {"command": "sleep 9"}))
    h.transcript.replace_conversation([])
    assert h.ticks == [] and h.cancelled == 1
    assert h.transcript.pending_tool is None
