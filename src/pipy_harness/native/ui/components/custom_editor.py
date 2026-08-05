"""Extension editor components and the live custom-editor effect owner.

``ExtensionEditorComponent`` backs the modal ``ctx.ui.editor`` overlay.  The
separate ``CustomEditorOwner`` owns the live duck-typed editor installed by
``ctx.ui.setEditorComponent``: its seven-field record, factory/wiring lifecycle,
key and action dispatch, text mirror, and frame projection.

The live owner receives the shared :class:`EditorState`, :class:`PaintLock`, and
repaint callback used by the other input owners. Every related record transition
is completed in one lock section. Factory/component methods, attribute setters,
sibling effects, external editing, autocomplete forwarding, and repainting all
run after the lock is released.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.frame_renderer import (
    ResolvedCustomEditorLine,
    clip_text,
    display_input_text,
    sanitize_custom_text,
)
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.ui.components.extension_prompts import clip_plain
from pipy_harness.native.ui.key_specs import matches_key_specs, resolved_key_specs
from pipy_harness.native.ui.paint_lock import PaintLock

HOTKEY_THINKING_CYCLE = "\x00pipy-hotkey:thinking-cycle"
HOTKEY_MODEL_CYCLE_NEXT = "\x00pipy-hotkey:model-cycle-next"
HOTKEY_MODEL_CYCLE_PREV = "\x00pipy-hotkey:model-cycle-prev"
HOTKEY_MODEL_SELECT = "\x00pipy-hotkey:model-select"
HOTKEY_TOGGLE_TOOLS = "\x00pipy-hotkey:toggle-tools"
HOTKEY_TOGGLE_THINKING = "\x00pipy-hotkey:toggle-thinking"
HOTKEY_EXTENSION_SHORTCUT_PREFIX = "\x00pipy-hotkey:ext-shortcut:"

_HOTKEY_ACTIONS = {
    "app.model.cycleForward": HOTKEY_MODEL_CYCLE_NEXT,
    "app.model.cycleBackward": HOTKEY_MODEL_CYCLE_PREV,
    "app.model.select": HOTKEY_MODEL_SELECT,
    "app.thinking.cycle": HOTKEY_THINKING_CYCLE,
    "app.tools.expand": HOTKEY_TOGGLE_TOOLS,
    "app.thinking.toggle": HOTKEY_TOGGLE_THINKING,
}


@dataclass(slots=True)
class CustomEditorState:
    """The complete mutable state of the live custom editor."""

    factory: object | None = None
    component: object | None = None
    active: bool = False
    submitted: str | None = None
    changed_text: str | None = None
    action: str | None = None
    exit_requested: bool = False


@dataclass(frozen=True, slots=True)
class CustomEditorEffects:
    """Sibling and terminal effects invoked only outside the paint lock."""

    restore_input_text: Callable[[str], None]
    clear_initial_text: Callable[[], None]
    enqueue_follow_up: Callable[[str], None]
    restore_pending: Callable[[], None]
    paste_clipboard_image: Callable[[], None]
    external_editor: Callable[[str], str | None]
    autocomplete_provider: Callable[[], object | None]


class _CustomEditorKeybindings:
    """Small Pi-shaped keybinding/action adapter for custom editors."""

    HANDLER_ACTIONS: tuple[str, ...] = (
        "app.interrupt",
        "app.exit",
        "app.thinking.cycle",
        "app.model.cycleForward",
        "app.model.cycleBackward",
        "app.model.select",
        "app.tools.expand",
        "app.thinking.toggle",
        "app.editor.external",
        "app.message.followUp",
        "app.message.dequeue",
    )

    def __init__(
        self,
        owner: CustomEditorOwner,
        keybindings_manager: KeybindingsManager | None = None,
    ) -> None:
        self._owner = owner
        self._keybindings_manager = keybindings_manager
        self.action_handlers = {
            action: self._handler_for(action) for action in self.HANDLER_ACTIONS
        }
        self.actionHandlers = self.action_handlers

    def _handler_for(self, action: str) -> Callable[[], object]:
        def handler() -> object:
            self._owner.queue_action(action)
            return None

        return handler

    def keys_for(self, action: str) -> list[str]:
        return resolved_key_specs(action, self._keybindings_manager)

    def matches(self, key: str, action: str) -> bool:
        return matches_key_specs(key, self.keys_for(action))

    def matches_action(self, key: str, action: str) -> bool:
        return self.matches(key, action)

    def matchesAction(self, key: str, action: str) -> bool:  # noqa: N802 - Pi API
        return self.matches(key, action)


class CustomEditorOwner:
    """Own the installed custom editor and its interaction lifecycle."""

    def __init__(
        self,
        record: CustomEditorState,
        editor: EditorState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        host: object,
        theme: Callable[[], object],
        keybindings_manager: Callable[[], KeybindingsManager | None],
        effects: CustomEditorEffects,
    ) -> None:
        self._record = record
        self._editor = editor
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._host = host
        self._theme = theme
        self._keybindings_manager = keybindings_manager
        self._effects = effects

    @property
    def state(self) -> CustomEditorState:
        return self._record

    @property
    def editor_state(self) -> EditorState:
        return self._editor

    @property
    def active(self) -> bool:
        with self._paint_lock:
            return self._record.active

    @property
    def component(self) -> object | None:
        with self._paint_lock:
            return self._record.component

    @property
    def factory(self) -> object | None:
        with self._paint_lock:
            return self._record.factory

    @property
    def action(self) -> str | None:
        with self._paint_lock:
            return self._record.action

    @property
    def submitted(self) -> str | None:
        with self._paint_lock:
            return self._record.submitted

    def consume_exit_requested(self) -> bool:
        with self._paint_lock:
            requested = self._record.exit_requested
            self._record.exit_requested = False
            return requested

    def input_text(self) -> str:
        if self.active:
            return self.text()
        with self._paint_lock:
            pending = self._editor.pending_initial_text
            return self._editor.text if pending is None else pending

    def prepare_line(self, text: str) -> None:
        if self.active:
            self.set_text(text)

    def set_editor_component(self, factory: object | None) -> None:
        current_text = self.input_text()
        if factory is None:
            self.clear_generation_state()
            self._effects.restore_input_text(current_text)
            self._repaint()
            return
        if not callable(factory):
            self._reset_transient_state()
            return
        with self._paint_lock:
            self._record.factory = factory
            self._record.submitted = None
            self._record.changed_text = None
            self._record.action = None
            self._record.exit_requested = False
        component = self._build_component(factory)
        with self._paint_lock:
            self._record.component = component
            self._record.active = component is not None
        if component is not None:
            self._wire_component(component)
            self.set_text(current_text)
            provider = self._effects.autocomplete_provider()
            if provider is not None:
                self.forward_autocomplete_provider(provider)
        self._repaint()

    def _build_component(self, factory: Callable[..., object]) -> object | None:
        try:
            return factory(
                self._host,
                self._theme(),
                _CustomEditorKeybindings(self, self._keybindings_manager()),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - extension factory fails closed
            return None

    def _reset_transient_state(self) -> None:
        with self._paint_lock:
            self._record.submitted = None
            self._record.changed_text = None
            self._record.action = None
            self._record.exit_requested = False

    def clear_generation_state(self) -> None:
        """Atomically detach all live custom-editor state without callbacks."""

        with self._paint_lock:
            self._record.factory = None
            self._record.component = None
            self._record.active = False
            self._record.submitted = None
            self._record.changed_text = None
            self._record.action = None
            self._record.exit_requested = False

    def queue_action(self, action: str) -> None:
        with self._paint_lock:
            self._record.action = action

    def text(self) -> str:
        with self._paint_lock:
            component = self._record.component
            changed_text = self._record.changed_text
            fallback = self._editor.text
        if component is not None:
            resolved = self._component_text(component)
            if resolved is not None:
                return resolved
        return fallback if changed_text is None else changed_text

    @staticmethod
    def _component_text(component: object) -> str | None:
        for name in ("get_text", "getText"):
            try:
                getter = getattr(component, name, None)
                if callable(getter):
                    return str(getter())
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - trusted editor fails soft
                break
        return None

    def set_text(self, text: str) -> None:
        value = str(text)
        with self._paint_lock:
            self._editor.set_buffer(value)
            component = self._record.component
        self._set_component_text(component, value)

    @staticmethod
    def _set_component_text(component: object | None, text: str) -> None:
        if component is None:
            return
        for name in ("set_text", "setText"):
            try:
                setter = getattr(component, name, None)
                if callable(setter):
                    setter(text)
                    return
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - keep built-in mirror
                return

    def forward_autocomplete_provider(self, provider: object) -> None:
        component = self.component
        if component is None:
            return
        try:
            setter = getattr(component, "set_autocomplete_provider", None) or getattr(
                component, "setAutocompleteProvider", None
            )
            if callable(setter):
                setter(provider)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - extension UI adapter is fail-soft
            return

    def _wire_component(self, component: object) -> None:
        for name in ("on_submit", "onSubmit"):
            self._set_attr(component, name, self._submit)
        for name in ("on_change", "onChange"):
            self._set_attr(component, name, self._change)
        self._wire_extension_shortcuts(component)
        self._wire_special_callbacks(component)
        self._wire_action_handlers(component)

    def _wire_extension_shortcuts(self, component: object) -> None:
        def shortcut(key: object) -> None:
            self.queue_action(f"app.extensionShortcut:{key}")

        self._set_attr_if_absent(component, "on_extension_shortcut", shortcut)
        self._set_attr_if_absent(component, "onExtensionShortcut", shortcut)

    def _wire_special_callbacks(self, component: object) -> None:
        callbacks = (
            (("on_escape", "onEscape"), lambda: self.queue_action("app.interrupt")),
            (("on_ctrl_d", "onCtrlD"), lambda: self.queue_action("app.exit")),
            (
                ("on_paste_image", "onPasteImage"),
                lambda: self.queue_action("app.clipboard.pasteImage"),
            ),
        )
        for names, callback in callbacks:
            self._set_attr_pair_if_absent(component, names, callback)

    def _wire_action_handlers(self, component: object) -> None:
        try:
            handlers = getattr(component, "action_handlers", None)
            if handlers is None:
                handlers = getattr(component, "actionHandlers", None)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - hostile component is fail-soft
            return
        if handlers is None:
            return
        for action in _CustomEditorKeybindings.HANDLER_ACTIONS:
            self._set_handler_if_absent(handlers, action)

    def _set_handler_if_absent(self, handlers: object, action: str) -> None:
        try:
            if action not in handlers:  # type: ignore[operator]
                handlers[action] = lambda action=action: self.queue_action(action)  # type: ignore[index]
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - duck-typed mapping may be immutable
            return

    def _submit(self, value: object | None = None) -> None:
        text = self.text() if value is None else str(value)
        with self._paint_lock:
            self._record.submitted = text
            self._editor.set_buffer(text)

    def _change(self, value: object | None = None) -> None:
        text = self.text() if value is None else str(value)
        with self._paint_lock:
            self._record.changed_text = text
            self._editor.set_buffer(text)

    @staticmethod
    def _set_attr(component: object, name: str, value: object) -> None:
        try:
            setattr(component, name, value)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - duck-typed object may forbid attrs
            return

    @classmethod
    def _set_attr_if_absent(cls, component: object, name: str, value: object) -> None:
        try:
            if getattr(component, name, None) is not None:
                return
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - still attempt the setter
            pass
        cls._set_attr(component, name, value)

    @classmethod
    def _set_attr_pair_if_absent(
        cls, component: object, names: tuple[str, str], value: object
    ) -> None:
        existing = cls._first_attr(component, names)
        shared = value if existing is None else existing
        for name in names:
            cls._set_attr_if_absent(component, name, shared)

    @staticmethod
    def _first_attr(component: object, names: tuple[str, str]) -> object | None:
        for name in names:
            try:
                candidate = getattr(component, name, None)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - hostile component is fail-soft
                continue
            if candidate is not None:
                return cast(object, candidate)
        return None

    def handle_key(self, key: str | None) -> str | None:
        with self._paint_lock:
            self._record.exit_requested = False
            component = self._record.component
        if key is None:
            self._repaint()
            return None
        if component is None:
            return None
        with self._paint_lock:
            self._record.submitted = None
            self._record.action = None
        if not self._invoke_input_handler(component, key):
            return None
        action = self._take_action()
        if action is not None:
            self._mirror_text()
            return self._dispatch_action(action)
        return self._finish_key()

    def _invoke_input_handler(self, component: object, key: str) -> bool:
        try:
            handler = getattr(component, "handle_input", None) or getattr(
                component, "handleInput", None
            )
            if callable(handler):
                result = handler(key)
                if isinstance(result, str):
                    with self._paint_lock:
                        self._record.submitted = result
            elif key == "enter":
                text = self.text()
                with self._paint_lock:
                    self._record.submitted = text
            return True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - bad custom editor falls back
            self.set_editor_component(None)
            return False

    def _take_action(self) -> str | None:
        with self._paint_lock:
            action = self._record.action
            self._record.action = None
            return action

    def _mirror_text(self) -> None:
        text = self.text()
        with self._paint_lock:
            self._editor.set_buffer(text)

    def _dispatch_action(self, action: str) -> str | None:
        hotkey = _HOTKEY_ACTIONS.get(action)
        if hotkey is not None:
            return self._handle_hotkey(hotkey)
        if action in {"app.editor.external", "app.clipboard.pasteImage"}:
            self._handle_editor_action(action)
            return None
        if action in {"app.message.followUp", "app.message.dequeue"}:
            self._handle_message_action(action)
            return None
        return self._handle_control_action(action)

    def _handle_hotkey(self, hotkey: str) -> str:
        with self._paint_lock:
            if self._editor.text:
                self._editor.pending_initial_text = self._editor.text
            self._editor.set_buffer("")
            component = self._record.component
        self._set_component_text(component, "")
        return hotkey

    def _handle_editor_action(self, action: str) -> None:
        if action == "app.clipboard.pasteImage":
            self._effects.paste_clipboard_image()
            return None
        edited = self._effects.external_editor(self.text())
        if edited is not None:
            self.set_text(edited)
        self._repaint()
        return None

    def _handle_message_action(self, action: str) -> None:
        if action == "app.message.dequeue":
            self._effects.restore_pending()
            return None
        text = self.text()
        if text.strip():
            self._effects.enqueue_follow_up(text)
        self._effects.clear_initial_text()
        self.set_text("")
        return None

    def _handle_control_action(self, action: str) -> str | None:
        if action == "app.interrupt":
            self._effects.clear_initial_text()
            self.set_text("")
        elif action == "app.exit" and not self.text():
            with self._paint_lock:
                self._record.exit_requested = True
            return ""
        elif action.startswith("app.extensionShortcut:"):
            key = action.removeprefix("app.extensionShortcut:")
            return f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key}"
        return None

    def _finish_key(self) -> str | None:
        with self._paint_lock:
            submitted = self._record.submitted
            self._record.submitted = None
        if submitted is not None:
            self._effects.clear_initial_text()
            self.set_text("")
            return submitted
        self._mirror_text()
        self._repaint()
        return None

    def frame_lines(
        self, width: int, *, max_rows: int | None = None
    ) -> list[ResolvedCustomEditorLine]:
        raw = self._render_component(width)
        if raw is None:
            raw = [display_input_text(self.text()) or " "]
        raw_lines = self._coerce_lines(raw)
        if max_rows is not None and max_rows > 0 and len(raw_lines) > max_rows:
            raw_lines = raw_lines[-max_rows:]
        lines = [
            clip_text(sanitize_custom_text(line or " "), width) for line in raw_lines
        ]
        if not lines:
            lines = [" "]
        meta = {"cursor_col": min(len(lines[-1]), max(0, width - 1))}
        return [
            ResolvedCustomEditorLine(
                line, "input", meta if index == len(lines) - 1 else None
            )
            for index, line in enumerate(lines)
        ]

    def _render_component(self, width: int) -> object | None:
        component = self.component
        if component is None:
            return None
        try:
            renderer = getattr(component, "render", None)
            return renderer(width) if callable(renderer) else None
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail-soft render
            return ["(custom editor render error)"]

    @staticmethod
    def _coerce_lines(raw: object) -> list[str]:
        if isinstance(raw, str):
            return raw.splitlines() or [raw]
        if isinstance(raw, Iterable):
            return [str(line) for line in raw]
        return [str(raw)]


class ExtensionEditorComponent:
    """Multi-line editor overlay used by extension ``ctx.ui.editor``."""

    _MAX_VISIBLE_LINES = 10

    def __init__(
        self,
        title: str,
        prefill: str | None,
        done: Callable[..., None],
        external_editor: Callable[[str], str | None] | None = None,
        external_editor_keys: Sequence[str] = ("ctrl+g",),
    ) -> None:
        self.title = title
        self.text = "" if prefill is None else str(prefill)
        self.cursor = len(self.text)
        self._done = done
        self._external_editor = external_editor
        self._external_editor_keys = tuple(external_editor_keys)

    def render(self, width: int) -> list[str]:
        hint = "enter submit, shift/alt-enter newline, esc cancel"
        if self._external_editor is not None and self._external_editor_keys:
            key_hint = self._external_editor_keys[0].replace("+", "-")
            hint += f", {key_hint} external edit"
        lines = [clip_plain(f" {sanitize_label_text(self.title)} - {hint}", width)]
        rows, cursor_row, cursor_col = self._rows()
        start = max(
            0,
            min(
                cursor_row - (self._MAX_VISIBLE_LINES // 2),
                max(0, len(rows) - self._MAX_VISIBLE_LINES),
            ),
        )
        end = start + self._MAX_VISIBLE_LINES
        for row_index, row in enumerate(rows[start:end], start=start):
            marker = ">" if row_index == cursor_row else " "
            safe = sanitize_label_text(row)
            if row_index == cursor_row:
                safe = safe[:cursor_col] + "▏" + safe[cursor_col:]
            lines.append(clip_plain(f"{marker} {safe}", width))
        if start > 0 or end < len(rows):
            lines.append(clip_plain(f"  ({cursor_row + 1}/{len(rows)})", width))
        return lines

    def handle_input(self, key: str) -> None:
        if key in {"esc", "ctrl-c"}:
            self._done(None)
            return
        if key == "enter":
            self._done(self.text)
            return
        if key in {"shift-enter", "alt-enter"}:
            self._insert("\n")
            return
        if self._external_editor is not None and matches_key_specs(
            key, self._external_editor_keys
        ):
            self._apply_external_edit(self._external_editor)
            return
        if self._apply_motion_key(key):
            return
        self._apply_editing_key(key)

    def _apply_external_edit(self, editor: Callable[[str], str | None]) -> None:
        edited = editor(self.text)
        if edited is not None:
            self.text = edited
            self.cursor = len(self.text)

    def _apply_motion_key(self, key: str) -> bool:
        if key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "home":
            self.cursor = self._index_for_row_col(self._cursor_row_col()[0], 0)
        elif key == "end":
            row, _col = self._cursor_row_col()
            self.cursor = self._index_for_row_col(row, len(self._rows()[0][row]))
        elif key in {"up", "down"}:
            row, col = self._cursor_row_col()
            target_row = row - 1 if key == "up" else row + 1
            self.cursor = self._index_for_row_col(target_row, col)
        else:
            return False
        return True

    def _apply_editing_key(self, key: str) -> None:
        if key == "backspace":
            if self.cursor > 0:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
            return
        if len(key) == 1 and key.isprintable():
            self._insert(key)

    def _insert(self, value: str) -> None:
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)

    def _rows(self) -> tuple[list[str], int, int]:
        rows = self.text.split("\n")
        if not rows:
            rows = [""]
        row, col = self._cursor_row_col(rows)
        return rows, row, col

    def _cursor_row_col(self, rows: list[str] | None = None) -> tuple[int, int]:
        if rows is None:
            rows = self.text.split("\n") or [""]
        remaining = max(0, min(self.cursor, len(self.text)))
        for index, row in enumerate(rows):
            if remaining <= len(row):
                return index, remaining
            remaining -= len(row) + 1
        return len(rows) - 1, len(rows[-1])

    def _index_for_row_col(self, target_row: int, target_col: int) -> int:
        rows = self.text.split("\n") or [""]
        row = min(max(0, target_row), len(rows) - 1)
        index = sum(len(item) + 1 for item in rows[:row])
        return min(index + max(0, min(target_col, len(rows[row]))), len(self.text))
