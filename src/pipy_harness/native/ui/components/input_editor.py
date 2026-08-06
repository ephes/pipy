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
from enum import Enum
from typing import Literal

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.frame_renderer import (
    FrameLine,
    InputSnapshot,
    ResolvedCustomEditorLine,
    display_input_text,
    input_lines,
)
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_CYCLE_NEXT,
    HOTKEY_MODEL_CYCLE_PREV,
    HOTKEY_THINKING_CYCLE,
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
    CustomEditorOwner,
)
from pipy_harness.native.ui.paint_lock import PaintLock


class EditingMode(Enum):
    """Terminal drive mode for one decoded editing key."""

    LINE = "line"
    ACTIVE_QUEUE = "active_queue"
    ACTIVE_COMMAND = "active_command"
    ACTIVE_WATCH = "active_watch"


class EditingAction(Enum):
    """Explicit terminal-owned outcome of shared key dispatch."""

    CONTINUE = "continue"
    SUBMIT = "submit"
    EOF = "eof"
    INTERRUPT = "interrupt"
    APP_COMMAND = "app_command"
    ABORT = "abort"
    LOCAL_COMMAND = "local_command"
    STEER = "steer"
    FOLLOW_UP = "follow_up"
    RESTORE_PENDING = "restore_pending"


@dataclass(frozen=True, slots=True)
class EditingKeyOutcome:
    """Value returned to the terminal loop after shared key dispatch."""

    action: EditingAction
    text: str | None = None


@dataclass(frozen=True, slots=True)
class LineEditingEffects:
    """Narrow line-mode effects that stay outside the editor state owner."""

    matches_external_editor: Callable[[str], bool]
    run_external_editor: Callable[[str], str | None]
    paste_clipboard_image: Callable[[], None]
    shortcut_keys: Callable[[], tuple[str, ...]]
    custom_editor_active: Callable[[], bool]
    handle_custom_editor: Callable[[str | None], str | None]
    consume_custom_exit: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class EditingKeyContext:
    """Mode-specific popup and key-loop effects used by ``apply_editing_key``."""

    mode: EditingMode
    slash_menu_open: Callable[[], bool]
    slash_menu_has_matches: Callable[[], bool]
    slash_menu_exact_match: Callable[[str], bool]
    autocomplete_open: Callable[[], bool]
    navigate_slash_menu: Callable[[str], None]
    navigate_autocomplete: Callable[[str], None]
    accept_slash_menu: Callable[[], None]
    accept_autocomplete: Callable[[], None]
    dismiss_slash_menu: Callable[[], None]
    close_autocomplete: Callable[[], None]
    attempt_path_completion: Callable[[], bool]
    consume_paste: Callable[[], str]
    insert_paste: Callable[[str], None]
    repaint: Callable[[], None]
    is_local_command: Callable[[str], bool]
    allow_history: bool
    allow_path_completion: bool
    line_effects: LineEditingEffects | None = None
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
        custom_editor: CustomEditorOwner,
        insert_paste: Callable[[str], None],
    ) -> None:
        self._editor = editor
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._command_names = command_names
        self._refresh_autocomplete = refresh_autocomplete
        self._custom_editor = custom_editor
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
        return self._custom_editor.input_text()

    def set_input_text(self, text: str) -> None:
        value = str(text)
        if self._custom_editor.active:
            self._custom_editor.set_text(value)
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

    def take_mid_turn_input(self) -> str:
        """Atomically take and reset the mid-turn editor buffer."""

        with self._paint_lock:
            text = self._editor.text
            self._editor.reset_mid_turn_input()
            return text

    def finish_custom_line(self, submitted: str, *, record_history: bool) -> None:
        """Atomically record a custom submission and reset line state."""

        with self._paint_lock:
            if record_history:
                self._editor.record_history(submitted)
            self._editor.reset_line_editor_state()

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

    def restore_generation_text(self, text: str) -> None:
        """Atomically restore the built-in buffer and next-line prefill."""

        with self._paint_lock:
            self._editor.set_buffer(text)
            self._editor.pending_initial_text = text

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


_CONTINUE = EditingKeyOutcome(EditingAction.CONTINUE)


def apply_editing_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome:
    """Classify and apply one decoded key for either product input loop."""

    if context.mode is EditingMode.LINE:
        outcome = _apply_line_key(editor, key, context)
    else:
        outcome = _apply_active_key(editor, key, context)
    if outcome is not None:
        return outcome
    if key is not None:
        _apply_common_editing_key(editor, key, context)
    return _CONTINUE


def _apply_line_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    for handler in (
        _apply_custom_editor_key,
        _apply_line_missing_key,
        _apply_line_enter,
        _apply_line_control_key,
        _apply_line_external_editor,
        _apply_line_app_hotkey,
        _apply_line_clipboard_key,
        _apply_line_extension_shortcut,
        _apply_line_escape,
    ):
        outcome = handler(editor, key, context)
        if outcome is not None:
            return outcome
    return None


def _line_effects(context: EditingKeyContext) -> LineEditingEffects:
    effects = context.line_effects
    if effects is None:  # pragma: no cover - construction invariant
        raise ValueError("line editing mode requires line effects")
    return effects


def _apply_custom_editor_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    effects = _line_effects(context)
    if not effects.custom_editor_active():
        return None
    submitted = effects.handle_custom_editor(key)
    if submitted is None:
        return _CONTINUE
    exited = effects.consume_custom_exit()
    editor.finish_custom_line(submitted, record_history=not exited)
    context.repaint()
    action = EditingAction.EOF if exited else EditingAction.SUBMIT
    return EditingKeyOutcome(action, submitted)


def _apply_line_missing_key(
    _editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key is not None:
        return None
    context.repaint()
    return _CONTINUE


def _apply_line_enter(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key != "enter":
        return None
    if context.autocomplete_open():
        context.accept_autocomplete()
        return _CONTINUE
    if _should_accept_slash_menu(editor, context):
        context.accept_slash_menu()
    submitted = editor.submit_line()
    context.repaint()
    return EditingKeyOutcome(EditingAction.SUBMIT, submitted)


def _should_accept_slash_menu(editor: InputEditor, context: EditingKeyContext) -> bool:
    return (
        context.slash_menu_open()
        and context.slash_menu_has_matches()
        and not context.slash_menu_exact_match(editor.text)
    )


def _apply_line_control_key(
    editor: InputEditor,
    key: str | None,
    _context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key == "ctrl-c":
        return EditingKeyOutcome(EditingAction.INTERRUPT)
    if key == "ctrl-d":
        action = EditingAction.EOF if not editor.text else EditingAction.CONTINUE
        return EditingKeyOutcome(action)
    return None


def _apply_line_external_editor(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    effects = _line_effects(context)
    if key is None or not effects.matches_external_editor(key):
        return None
    edited = effects.run_external_editor(editor.text)
    if edited is None:
        context.repaint()
    else:
        editor.replace_after_external_edit(edited)
    return _CONTINUE


def _apply_line_app_hotkey(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    sentinels = {
        "ctrl-p": HOTKEY_MODEL_CYCLE_NEXT,
        "shift-ctrl-p": HOTKEY_MODEL_CYCLE_PREV,
        "shift-tab": HOTKEY_THINKING_CYCLE,
        "ctrl-o": HOTKEY_TOGGLE_TOOLS,
        "ctrl-t": HOTKEY_TOGGLE_THINKING,
    }
    sentinel = None if key is None else sentinels.get(key)
    if sentinel is None:
        return None
    editor.preserve_for_next_line()
    return EditingKeyOutcome(EditingAction.APP_COMMAND, sentinel)


def _apply_line_clipboard_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key == "paste":
        context.insert_paste(context.consume_paste())
        return _CONTINUE
    if key == "ctrl-v":
        _line_effects(context).paste_clipboard_image()
        return _CONTINUE
    return None


def _apply_line_extension_shortcut(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    effects = _line_effects(context)
    if key is None or key not in effects.shortcut_keys():
        return None
    editor.preserve_for_next_line()
    sentinel = f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key}"
    return EditingKeyOutcome(EditingAction.APP_COMMAND, sentinel)


def _apply_line_escape(
    _editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key != "esc":
        return None
    if context.slash_menu_open():
        context.dismiss_slash_menu()
    elif context.autocomplete_open():
        context.close_autocomplete()
        context.repaint()
    return _CONTINUE


def _apply_active_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    for handler in (
        _apply_active_control_key,
        _apply_active_mode_gate,
        _apply_active_enter,
        _apply_active_alt_key,
        _apply_active_paste,
    ):
        outcome = handler(editor, key, context)
        if outcome is not None:
            return outcome
    return None


def _apply_active_control_key(
    _editor: InputEditor,
    key: str | None,
    _context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    actions = {
        "esc": EditingAction.ABORT,
        "ctrl-c": EditingAction.INTERRUPT,
    }
    action = None if key is None else actions.get(key)
    return None if action is None else EditingKeyOutcome(action)


def _apply_active_mode_gate(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if context.mode is EditingMode.ACTIVE_WATCH:
        if key == "paste":
            context.consume_paste()
        return _CONTINUE
    if context.mode is not EditingMode.ACTIVE_COMMAND or editor.text:
        return None
    if key in {"/", "!"}:
        return None
    if key == "paste":
        context.consume_paste()
    return _CONTINUE


def _apply_active_enter(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key != "enter":
        return None
    if context.autocomplete_open():
        context.accept_autocomplete()
        return _CONTINUE
    if _should_accept_slash_menu(editor, context):
        context.accept_slash_menu()
    text = editor.take_mid_turn_input()
    if not text.strip():
        context.repaint()
        return _CONTINUE
    if context.is_local_command(text):
        return EditingKeyOutcome(EditingAction.LOCAL_COMMAND, text)
    if context.mode is EditingMode.ACTIVE_COMMAND:
        context.repaint()
        return _CONTINUE
    return EditingKeyOutcome(EditingAction.STEER, text)


def _apply_active_alt_key(
    editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key not in {"alt-enter", "alt-up"}:
        return None
    if context.mode is EditingMode.ACTIVE_COMMAND:
        return _CONTINUE
    if key == "alt-up":
        return EditingKeyOutcome(EditingAction.RESTORE_PENDING)
    text = editor.take_mid_turn_input()
    return EditingKeyOutcome(EditingAction.FOLLOW_UP, text)


def _apply_active_paste(
    _editor: InputEditor,
    key: str | None,
    context: EditingKeyContext,
) -> EditingKeyOutcome | None:
    if key != "paste":
        return None
    pasted = context.consume_paste()
    if context.mode is not EditingMode.ACTIVE_COMMAND:
        context.insert_paste(pasted)
    return _CONTINUE


def _apply_common_editing_key(
    editor: InputEditor,
    key: str,
    context: EditingKeyContext,
) -> bool:
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
