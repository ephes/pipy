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


def test_restore_prepends_to_existing_editor_text(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = "typed so far"
    ui.enqueue_steering("queued")
    ui.restore_pending_to_editor()
    assert ui.input_text == "queued\n\ntyped so far"
