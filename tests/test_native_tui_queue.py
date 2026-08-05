"""Focused tests for the pending steering/follow-up UI owner."""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from threading import Event
from types import TracebackType

import pytest

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.tui import TURN_STEERED, ToolLoopTerminalUi
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.pending_messages import PendingMessages


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )


def _frame_text(ui: ToolLoopTerminalUi) -> str:
    return "\n".join(ui.render_lines(width=88, height=24))


class _InterleavingPaintLock(PaintLock):
    """Run one simulated owner operation immediately after an outer release."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.entries = 0
        self.on_release: Callable[[], None] | None = None

    def __enter__(self) -> bool:
        entered = super().__enter__()
        self.depth += 1
        self.entries += 1
        return entered

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.depth -= 1
        super().__exit__(exc_type, exc, traceback)
        if self.depth == 0 and self.on_release is not None:
            callback = self.on_release
            self.on_release = None
            callback()


def test_terminal_wires_pending_owner_to_shared_state_and_lock(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    owner = ui.pending_messages

    assert isinstance(owner, PendingMessages)
    assert owner._editor is ui._editor  # noqa: SLF001
    assert owner._paint_lock is ui._paint_lock  # noqa: SLF001


def test_mid_turn_steering_is_published_before_abort_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FilenoInput(io.StringIO):
        def fileno(self) -> int:
            return 0

    class _SingleEnterDriver:
        def raw_mode(self) -> AbstractContextManager[None]:
            return nullcontext()

        def read_key_if_available(self, _fd: int, _timeout: float) -> str:
            return "enter"

    ui = _ui(tmp_path)
    ui.input_stream = _FilenoInput()
    ui._driver = _SingleEnterDriver()  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(ToolLoopTerminalUi, "_poll_resize_repaint", lambda _self: False)
    ui.input_text = "redirect here"
    owner = ui.pending_messages
    events: list[str] = []
    owner._repaint = lambda: events.append("repaint")  # noqa: SLF001
    enqueue_steering = owner.enqueue_steering

    def record_owner_enqueue(text: str) -> None:
        events.append("owner-enter")
        enqueue_steering(text)
        events.append("owner-return")

    monkeypatch.setattr(owner, "enqueue_steering", record_owner_enqueue)

    class _InspectingAbortEvent(Event):
        def set(self) -> None:
            assert events == ["owner-enter", "repaint", "owner-return"]
            assert [
                (message.kind, message.content)
                for message in ui._editor.pending_messages()  # noqa: SLF001
            ] == [("steering", "redirect here")]
            events.append("abort")
            super().set()

    abort_event = _InspectingAbortEvent()
    result = ui.wait_for_active_turn_interrupt(
        Event(), abort_event, poll_seconds=0, accept_queue=True
    )

    assert result == TURN_STEERED
    assert abort_event.is_set()
    assert events == ["owner-enter", "repaint", "owner-return", "abort"]


def test_enqueue_renders_pending_region(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.pending_messages.enqueue_steering("redirect here")
    ui.pending_messages.enqueue_follow_up("and then this")
    text = _frame_text(ui)
    assert "Steering: redirect here" in text
    assert "Follow-up: and then this" in text
    assert "alt+up to restore" in text


def test_blank_messages_are_not_queued(tmp_path: Path) -> None:
    owner = _ui(tmp_path).pending_messages
    owner.enqueue_steering("   ")
    owner.enqueue_follow_up("")
    assert not owner.has_pending_messages()


def test_promote_drains_steering_before_follow_up(tmp_path: Path) -> None:
    owner = _ui(tmp_path).pending_messages
    owner.enqueue_follow_up("F1")
    owner.enqueue_steering("S1")
    owner.enqueue_follow_up("F2")
    owner.promote_pending_to_drain()
    assert not owner.has_pending_messages()
    drained = []
    while (item := owner.take_next_drain()) is not None:
        drained.append(item)
    assert drained == ["S1", "F1", "F2"]


def test_restore_to_editor_joins_with_blank_lines(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = ""
    ui.pending_messages.enqueue_steering("first")
    ui.pending_messages.enqueue_follow_up("second")
    ui.pending_messages.restore_pending_to_editor()
    assert ui.input_text == "first\n\nsecond"
    assert not ui.pending_messages.has_pending_messages()


@pytest.mark.parametrize("custom_active", [False, True])
def test_restore_is_one_atomic_transition_with_callbacks_outside_lock(
    custom_active: bool,
) -> None:
    editor = EditorState()
    editor.enqueue_steering("initial")
    lock = _InterleavingPaintLock()
    events: list[str] = []

    def outside_lock(event: str) -> None:
        assert lock.depth == 0
        events.append(event)

    def active() -> bool:
        outside_lock("active")
        return custom_active

    def custom_text() -> str:
        outside_lock("get-custom-text")
        return "draft"

    def set_custom_text(text: str) -> None:
        outside_lock(f"set-custom-text:{text}")

    owner = PendingMessages(
        editor,
        lock,
        lambda: outside_lock("repaint"),
        custom_editor_active=active,
        custom_editor_text=custom_text,
        set_custom_editor_text=set_custom_text,
        refresh_slash_menu=lambda: outside_lock("refresh"),
    )
    lock.on_release = lambda: owner.enqueue_follow_up("concurrent")
    entries_before = lock.entries

    owner.restore_pending_to_editor()

    assert lock.entries - entries_before == 2  # restore plus simulated owner operation
    assert editor.text == ("initial\n\ndraft" if custom_active else "initial")
    assert [message.content for message in editor.pending_messages()] == ["concurrent"]
    if custom_active:
        assert events == [
            "active",
            "get-custom-text",
            "repaint",
            f"set-custom-text:{editor.text}",
            "repaint",
        ]
    else:
        assert events == ["active", "repaint", "refresh", "repaint"]


def test_pending_region_keeps_input_footer_in_frame(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    for n in range(60):
        ui.submit_user_message(f"history line {n:02d}")
    ui.pending_messages.enqueue_steering("steer one")
    ui.pending_messages.enqueue_follow_up("follow one")
    frame = ui.render_lines(width=88, height=24)
    assert len(frame) == 24
    joined = "\n".join(frame)
    assert "Steering: steer one" in joined
    assert "Follow-up: follow one" in joined
    separator_rows = [i for i, line in enumerate(frame) if set(line.strip()) == {"─"}]
    assert separator_rows and max(separator_rows) <= 23


def test_restore_survives_next_read_line_reset(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.pending_messages.enqueue_steering("redirect")
    ui.pending_messages.enqueue_follow_up("later")
    ui.pending_messages.restore_pending_to_editor()
    assert ui._pending_initial_text == "redirect\n\nlater"


def test_restore_prepends_to_existing_editor_text(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = "typed so far"
    ui.pending_messages.enqueue_steering("queued")
    ui.pending_messages.restore_pending_to_editor()
    assert ui.input_text == "queued\n\ntyped so far"


def test_abort_restores_remaining_drain_to_editor(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    owner = ui.pending_messages
    owner.enqueue_steering("S1")
    owner.enqueue_follow_up("F1")
    owner.promote_pending_to_drain()
    assert owner.take_next_drain() == "S1"
    owner.restore_pending_to_editor()
    assert owner.take_next_drain() is None
    assert not owner.has_pending_messages()
    assert ui.input_text == "F1"


def test_abort_restores_drain_before_unpromoted_lanes(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    owner = ui.pending_messages
    owner.enqueue_follow_up("F1")
    owner.promote_pending_to_drain()
    owner.enqueue_steering("S2")
    owner.restore_pending_to_editor()
    assert ui.input_text == "F1\n\nS2"
    assert owner.take_next_drain() is None
    assert not owner.has_pending_messages()


def test_pending_region_is_capped(tmp_path: Path) -> None:
    owner = _ui(tmp_path).pending_messages
    for n in range(20):
        owner.enqueue_follow_up(f"msg {n:02d}")
    lines = owner.region_lines(width=88)
    message_rows = [line.text for line in lines if "Follow-up:" in line.text]
    assert len(message_rows) <= 6
    assert any("more queued" in line.text for line in lines)
