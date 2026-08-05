"""Built-in input-editor effects over the shared :class:`EditorState` record.

The component owns buffer, cursor, prompt-history, undo/redo, paste hand-off,
and built-in input-frame projection.  It receives the one shared record and
paint lock used by the queue and clipboard owners; it never copies either.
Every record transition is completed in one lock section, while autocomplete,
custom-component, clipboard, and repaint callbacks run outside the lock.

``apply_editing_key`` is the common decoded-key ladder used by both product
input loops.  Its context keeps mode-specific popup, history, path-completion,
listener-replacement, and repaint semantics explicit rather than folding those
sibling responsibilities into this owner.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.frame_renderer import (
    FrameLine,
    InputSnapshot,
    ResolvedCustomEditorLine,
    display_input_text,
    input_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class EditingKeyContext:
    """Mode-specific popup and key-loop effects used by ``apply_editing_key``."""

    slash_menu_open: Callable[[], bool]
    slash_menu_has_matches: Callable[[], bool]
    autocomplete_open: Callable[[], bool]
    navigate_slash_menu: Callable[[str], None]
    navigate_autocomplete: Callable[[str], None]
    accept_slash_menu: Callable[[], None]
    accept_autocomplete: Callable[[], None]
    attempt_path_completion: Callable[[], bool]
    allow_history: bool
    allow_path_completion: bool
    allow_listener_replacement: bool = False
    listener_replaced: Callable[[], bool] = lambda: False
    tab_repaint: Literal["path", "always"] = "path"


class InputEditor:
    """Effect owner for the built-in editable buffer and input frame."""

    def __init__(
        self,
        editor: EditorState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        command_names: Callable[[], tuple[str, ...]],
        refresh_autocomplete: Callable[[], None],
        custom_editor_active: Callable[[], bool],
        custom_editor_text: Callable[[], str],
        set_custom_editor_text: Callable[[str], None],
        insert_paste: Callable[[str], None],
    ) -> None:
        self._editor = editor
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._command_names = command_names
        self._refresh_autocomplete = refresh_autocomplete
        self._custom_editor_active = custom_editor_active
        self._custom_editor_text = custom_editor_text
        self._set_custom_editor_text = set_custom_editor_text
        self._insert_paste = insert_paste

    @property
    def editor_state(self) -> EditorState:
        """Return the exact shared state record, never a snapshot or copy."""

        return self._editor

    # -- pure record projections --------------------------------------------

    @property
    def text(self) -> str:
        with self._paint_lock:
            return self._editor.text

    @text.setter
    def text(self, value: str) -> None:
        with self._paint_lock:
            self._editor.text = value

    @property
    def cursor(self) -> int | None:
        with self._paint_lock:
            return self._editor.cursor

    @cursor.setter
    def cursor(self, value: int | None) -> None:
        with self._paint_lock:
            self._editor.cursor = value

    @property
    def input_history(self) -> list[str]:
        with self._paint_lock:
            return self._editor.input_history

    @input_history.setter
    def input_history(self, value: list[str]) -> None:
        with self._paint_lock:
            self._editor.input_history = value

    @property
    def history_nav_index(self) -> int | None:
        with self._paint_lock:
            return self._editor.history_nav_index

    @history_nav_index.setter
    def history_nav_index(self, value: int | None) -> None:
        with self._paint_lock:
            self._editor.history_nav_index = value

    @property
    def history_draft(self) -> str:
        with self._paint_lock:
            return self._editor.history_draft

    @history_draft.setter
    def history_draft(self, value: str) -> None:
        with self._paint_lock:
            self._editor.history_draft = value

    @property
    def undo_stack(self) -> list[tuple[str, int]]:
        with self._paint_lock:
            return self._editor.undo_stack

    @property
    def redo_stack(self) -> list[tuple[str, int]]:
        with self._paint_lock:
            return self._editor.redo_stack

    @property
    def pending_paste(self) -> str:
        with self._paint_lock:
            return self._editor.pending_paste

    @pending_paste.setter
    def pending_paste(self, value: str) -> None:
        with self._paint_lock:
            self._editor.pending_paste = value

    @property
    def pending_initial_text(self) -> str | None:
        with self._paint_lock:
            return self._editor.pending_initial_text

    @pending_initial_text.setter
    def pending_initial_text(self, value: str | None) -> None:
        with self._paint_lock:
            self._editor.pending_initial_text = value

    # -- public editor boundary ---------------------------------------------

    def get_input_text(self) -> str:
        if self._custom_editor_active():
            return self._custom_editor_text()
        with self._paint_lock:
            pending = self._editor.pending_initial_text
            return self._editor.text if pending is None else pending

    def set_input_text(self, text: str) -> None:
        value = str(text)
        if self._custom_editor_active():
            self._set_custom_editor_text(value)
        with self._paint_lock:
            self._editor.stage_initial_text(value)

    def paste_input_text(self, text: str) -> None:
        with self._paint_lock:
            self._editor.clear_initial_text()
        self._insert_paste(str(text))

    # -- line lifecycle ------------------------------------------------------

    def begin_line(self) -> str:
        with self._paint_lock:
            return self._editor.begin_line()

    def submit_line(self) -> str:
        with self._paint_lock:
            return self._editor.submit_line()

    def preserve_for_next_line(self) -> None:
        with self._paint_lock:
            self._editor.preserve_for_next_line()

    def reset_mid_turn_input(self) -> None:
        with self._paint_lock:
            self._editor.reset_mid_turn_input()

    def reset_line_editor_state(self) -> None:
        with self._paint_lock:
            self._editor.reset_line_editor_state()

    def reset_history_nav(self) -> None:
        with self._paint_lock:
            self._editor.reset_history_nav()

    def snapshot_for_undo(self) -> None:
        with self._paint_lock:
            self._editor.snapshot_for_undo()

    def record_history(self, submitted: str) -> None:
        with self._paint_lock:
            self._editor.record_history(submitted)

    def load_history(self, entries: Iterable[str]) -> None:
        with self._paint_lock:
            self._editor.input_history = list(entries)

    def replace_after_external_edit(self, text: str) -> None:
        with self._paint_lock:
            self._editor.snapshot_for_undo()
            self._editor.reset_history_nav()
            self._editor.set_buffer(text)
            self._editor.close_slash_menu()
            self._editor.close_autocomplete()
        self._repaint()

    def set_buffer(self, text: str, *, cursor: int | None = None) -> None:
        with self._paint_lock:
            self._editor.set_buffer(text, cursor=cursor)

    def clear_initial_text(self) -> None:
        with self._paint_lock:
            self._editor.clear_initial_text()

    # -- paste hand-off ------------------------------------------------------

    def stage_paste(self, text: str) -> None:
        with self._paint_lock:
            self._editor.stage_paste(text)

    def consume_paste(self) -> str:
        with self._paint_lock:
            return self._editor.consume_paste()

    # -- editing operations --------------------------------------------------

    def insert_text(self, text: str) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            self._editor.insert(text, command_names)
        self._refresh_autocomplete()
        self._repaint()

    def delete_before_cursor(self) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            changed = self._editor.delete_before_cursor(command_names)
        if changed:
            self._refresh_autocomplete()
        self._repaint()

    def kill_to_line_start(self) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            changed = self._editor.kill_to_line_start(command_names)
        if changed:
            self._refresh_autocomplete()
        self._repaint()

    def undo(self) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            changed = self._editor.undo(command_names)
        if changed:
            self._refresh_autocomplete()
        self._repaint()

    def redo(self) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            changed = self._editor.redo(command_names)
        if changed:
            self._refresh_autocomplete()
        self._repaint()

    def navigate_history(self, key: str) -> None:
        with self._paint_lock:
            changed = self._editor.navigate_history(key)
        if changed:
            self._repaint()

    def load_history_entry(self, text: str) -> None:
        with self._paint_lock:
            self._editor.load_history_entry(text)
        self._repaint()

    def move_cursor(self, key: str) -> None:
        with self._paint_lock:
            self._editor.move_cursor(key)
        self._repaint()

    def request_repaint(self) -> None:
        self._repaint()

    # -- rendering -----------------------------------------------------------

    def snapshot(
        self,
        custom_rows: tuple[ResolvedCustomEditorLine, ...] | None = None,
    ) -> InputSnapshot:
        with self._paint_lock:
            return InputSnapshot(
                text=self._editor.text,
                cursor=self._editor.effective_cursor(),
                custom_rows=custom_rows,
            )

    def input_frame_lines(
        self,
        width: int,
        *,
        max_rows: int | None = None,
        custom_rows: tuple[ResolvedCustomEditorLine, ...] | None = None,
    ) -> list[FrameLine]:
        row_limit = 10**9 if max_rows is None else max_rows
        return list(
            input_lines(
                self.snapshot(custom_rows),
                width,
                max_rows=row_limit,
            )
        )

    @staticmethod
    def display_input_text(text: str) -> str:
        return display_input_text(text)

    def effective_cursor(self) -> int:
        with self._paint_lock:
            return self._editor.effective_cursor()

    def take_pending_command(self) -> str | None:
        with self._paint_lock:
            return self._editor.take_pending_command()

    def set_pending_command(self, text: str) -> None:
        with self._paint_lock:
            self._editor.set_pending_command(text)


def apply_editing_key(
    editor: InputEditor,
    key: str,
    context: EditingKeyContext,
) -> bool:
    """Apply one shared built-in editing key; return whether it was handled."""

    if key == "backspace":
        editor.delete_before_cursor()
        return True
    if key in {"up", "down"}:
        _apply_navigation_key(editor, key, context)
        return True
    if key == "tab":
        _apply_tab_key(editor, context)
        return True
    actions: dict[str, Callable[[], None]] = {
        "left": lambda: editor.move_cursor("left"),
        "right": lambda: editor.move_cursor("right"),
        "home": lambda: editor.move_cursor("home"),
        "end": lambda: editor.move_cursor("end"),
        "ctrl-u": editor.kill_to_line_start,
        "ctrl-z": editor.undo,
        "ctrl-y": editor.redo,
    }
    action = actions.get(key)
    if action is not None:
        action()
        return True
    if _is_insertable(key, context):
        editor.insert_text(key)
        return True
    return False


def _apply_navigation_key(
    editor: InputEditor,
    key: str,
    context: EditingKeyContext,
) -> None:
    if context.slash_menu_open():
        context.navigate_slash_menu(key)
    elif context.autocomplete_open():
        context.navigate_autocomplete(key)
    elif context.allow_history:
        editor.navigate_history(key)


def _apply_tab_key(editor: InputEditor, context: EditingKeyContext) -> None:
    path_attempted = False
    if context.slash_menu_open() and context.slash_menu_has_matches():
        context.accept_slash_menu()
    elif context.autocomplete_open():
        context.accept_autocomplete()
    elif context.allow_path_completion:
        context.attempt_path_completion()
        path_attempted = True
    if context.tab_repaint == "always" or path_attempted:
        editor.request_repaint()


def _is_insertable(key: str, context: EditingKeyContext) -> bool:
    if not key.isprintable():
        return False
    return len(key) == 1 or (
        context.allow_listener_replacement and context.listener_replaced()
    )
