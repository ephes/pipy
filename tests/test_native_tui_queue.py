"""Unit tests for the TUI steering/follow-up message queue (Pi parity)."""

from __future__ import annotations

import io
from pathlib import Path

from pipy_harness.native.tui import ToolLoopTerminalUi


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def _frame_text(ui: ToolLoopTerminalUi) -> str:
    return "\n".join(ui.render_lines(width=88, height=24))


def test_enqueue_renders_pending_region(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.enqueue_steering("redirect here")
    ui.enqueue_follow_up("and then this")
    text = _frame_text(ui)
    assert "Steering: redirect here" in text
    assert "Follow-up: and then this" in text
    assert "alt+up to restore" in text


def test_blank_messages_are_not_queued(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.enqueue_steering("   ")
    ui.enqueue_follow_up("")
    assert not ui.has_pending_messages()


def test_promote_drains_steering_before_follow_up(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.enqueue_follow_up("F1")
    ui.enqueue_steering("S1")
    ui.enqueue_follow_up("F2")
    ui.promote_pending_to_drain()
    assert not ui.has_pending_messages()
    drained = []
    while (item := ui.take_next_drain()) is not None:
        drained.append(item)
    assert drained == ["S1", "F1", "F2"]


def test_restore_to_editor_joins_with_blank_lines(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = ""
    ui.enqueue_steering("first")
    ui.enqueue_follow_up("second")
    ui.restore_pending_to_editor()
    assert ui.input_text == "first\n\nsecond"
    assert not ui.has_pending_messages()


def test_pending_region_keeps_input_footer_in_frame(tmp_path: Path) -> None:
    # With history filling the viewport AND queued messages pending, the input
    # row and footer must stay within the returned frame (the pending region is
    # reserved in the history budget, not added on top of it).
    ui = _ui(tmp_path)
    for n in range(60):
        ui.submit_user_message(f"history line {n:02d}")
    ui.enqueue_steering("steer one")
    ui.enqueue_follow_up("follow one")
    frame = ui.render_lines(width=88, height=24)
    assert len(frame) == 24
    joined = "\n".join(frame)
    assert "Steering: steer one" in joined
    assert "Follow-up: follow one" in joined
    # The two footer rows are the last rendered rows (not pushed out of frame).
    separator_rows = [i for i, line in enumerate(frame) if set(line.strip()) == {"─"}]
    assert separator_rows and max(separator_rows) <= 23


def test_restore_survives_next_read_line_reset(tmp_path: Path) -> None:
    # Escape-abort restores the queue, then the outer loop's next read_line
    # resets input_text unless _pending_initial_text is set — so the restored
    # text must be stashed there or it is silently lost.
    ui = _ui(tmp_path)
    ui.enqueue_steering("redirect")
    ui.enqueue_follow_up("later")
    ui.restore_pending_to_editor()
    assert ui._pending_initial_text == "redirect\n\nlater"
    # Simulate read_line's reset preamble: it honors _pending_initial_text.
    assert ui._pending_initial_text is not None


def test_restore_prepends_to_existing_editor_text(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = "typed so far"
    ui.enqueue_steering("queued")
    ui.restore_pending_to_editor()
    assert ui.input_text == "queued\n\ntyped so far"


def test_pending_region_is_capped(tmp_path: Path) -> None:
    # A large queue must not grow the pinned chrome unbounded; the pending
    # region caps message rows and summarizes the rest.
    ui = _ui(tmp_path)
    for n in range(20):
        ui.enqueue_follow_up(f"msg {n:02d}")
    lines = ui._pending_region_lines(width=88)
    message_rows = [line.text for line in lines if "Follow-up:" in line.text]
    assert len(message_rows) <= ui._PENDING_REGION_MAX_ROWS
    assert any("more queued" in line.text for line in lines)
