"""Pending steering/follow-up effects over the shared editor state.

The dependency-neutral :class:`EditorState` remains the queue-data owner. This
component owns queue UI transitions and rendering. Each related record change
runs in one shared :class:`PaintLock` section; custom-editor/autocomplete ports
and repainting run outside it.
"""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.editor_state import EditorState, QueuedInputKind
from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.ui.components.custom_editor import CustomEditorOwner
from pipy_harness.native.ui.paint_lock import PaintLock

_PENDING_REGION_MAX_ROWS = 6


class PendingMessages:
    """Own steering/follow-up queue effects for the terminal editor."""

    def __init__(
        self,
        editor: EditorState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        custom_editor: CustomEditorOwner,
        refresh_slash_menu: Callable[[], None],
    ) -> None:
        self._editor = editor
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._custom_editor = custom_editor
        self._refresh_slash_menu = refresh_slash_menu

    def enqueue_steering(self, text: str) -> None:
        with self._paint_lock:
            self._editor.enqueue_steering(text)
        self._repaint()

    def enqueue_follow_up(self, text: str) -> None:
        with self._paint_lock:
            self._editor.enqueue_follow_up(text)
        self._repaint()

    def has_pending_messages(self) -> bool:
        with self._paint_lock:
            return self._editor.has_pending_messages()

    def promote_pending_to_drain(self) -> None:
        """Move queued messages into the sequential drain, steering first."""

        with self._paint_lock:
            self._editor.promote_pending_to_drain()
        self._repaint()

    def restore_pending_to_editor(self) -> None:
        """Restore drain then queued lanes ahead of the current editor draft."""

        custom_active = self._custom_editor.active
        custom_text = self._custom_editor.text() if custom_active else None
        with self._paint_lock:
            restored = self._editor.restore_pending_to_editor(custom_text=custom_text)
            restored_text = self._editor.text
        if not restored:
            return
        if custom_active:
            self._custom_editor.set_text(restored_text)
        else:
            self._refresh_slash_menu()
        self._repaint()

    def take_next_drain(self) -> str | None:
        with self._paint_lock:
            return self._editor.take_next_drain()

    def take_last_drain_kind(self) -> QueuedInputKind | None:
        with self._paint_lock:
            return self._editor.take_last_drain_kind()

    def region_lines(self, width: int) -> list[FrameLine]:
        """Render one locked, bounded snapshot of the pending lanes."""

        with self._paint_lock:
            queued = self._editor.pending_messages()
        if not queued:
            return []
        visible = queued[:_PENDING_REGION_MAX_ROWS]
        lines = [
            FrameLine(
                clip_text(
                    f"  {'Steering' if row.kind == 'steering' else 'Follow-up'}: "
                    f"{row.content.replace(chr(10), ' ')}",
                    width,
                ),
                "notice",
            )
            for row in visible
        ]
        hidden = len(queued) - len(visible)
        if hidden:
            lines.append(
                FrameLine(
                    clip_text(f"  … +{hidden} more queued", width),
                    "slash_menu_scroll",
                )
            )
        lines.append(
            FrameLine(
                clip_text("  (alt+up to restore queued messages to the editor)", width),
                "slash_menu_scroll",
            )
        )
        return lines
