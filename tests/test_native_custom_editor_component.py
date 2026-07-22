from __future__ import annotations

import io
import os
import shlex
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.keybindings import DEFAULT_KEYBINDINGS, KeybindingsManager
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_SELECT,
    HOTKEY_THINKING_CYCLE,
    ToolLoopTerminalUi,
    _CustomEditorKeybindings,
)


class _TtyBuffer:
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )


def _decode_key(ui: ToolLoopTerminalUi, data: bytes) -> str | None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    try:
        return ui._read_key(read_fd)
    finally:
        os.close(read_fd)


class _CustomEditor:
    def __init__(self) -> None:
        self.text = ""
        self.keys: list[str] = []
        self.on_submit = None
        self.on_change = None
        self.autocomplete_provider: object | None = None
        self.autocomplete_results: list[object] = []
        self.action_handlers: dict[str, object] = {}
        self.keybindings: object | None = None
        self.on_extension_shortcut = None
        self.on_escape: Callable[[], object] | None = None
        self.on_ctrl_d: Callable[[], object] | None = None
        self.on_paste_image: Callable[[], object] | None = None
        self.onEscape: Callable[[], object] | None = None
        self.onCtrlD: Callable[[], object] | None = None
        self.onPasteImage: Callable[[], object] | None = None

    def set_text(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def render(self, width: int) -> list[str]:
        return [f"custom editor: {self.text}"[:width]]

    def handle_input(self, key: str) -> None:
        self.keys.append(key)
        if key == "tab" and self.autocomplete_provider is not None:
            get_suggestions = getattr(self.autocomplete_provider, "get_suggestions")
            self.autocomplete_results.append(
                get_suggestions([self.text], 0, len(self.text), object())
            )
            return
        if key == "enter":
            assert self.on_submit is not None
            self.on_submit(self.text)
            return
        self.text += key
        if self.on_change is not None:
            self.on_change(self.text)

    def set_autocomplete_provider(self, provider: object) -> None:
        self.autocomplete_provider = provider


class _IdentityAutocompleteProvider:
    def get_suggestions(self, lines: object, line: int, col: int, context: object) -> str:
        del line, context
        assert lines == ["seed"]
        return f"provider:{col}"


class _CamelActionCustomEditor(_CustomEditor):
    def __init__(self) -> None:
        super().__init__()
        del self.action_handlers
        self.actionHandlers: dict[str, object] = {}


def test_custom_editor_read_line_routes_keys_only_to_custom_component(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    keys: Iterator[str] = iter(("x", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(factory)

    assert ui.read_line("> ") == "x\n"

    assert component.keybindings is not None
    assert getattr(component.keybindings, "matches")("shift-tab", "app.thinking.cycle")
    assert getattr(component.keybindings, "matches")("ctrl-p", "app.model.cycleForward")
    assert getattr(component.keybindings, "matches")("ctrl+p", "app.model.cycleForward")
    assert getattr(component.keybindings, "matches")("ctrl-o", "app.tools.expand")
    assert getattr(component.keybindings, "matches")("ctrl+o", "app.tools.expand")
    assert component.keys == ["x", "enter"]
    assert ui.input_text == ""
    assert component.get_text() == ""

    keys = iter(("y", "enter"))
    assert ui.read_line("> ") == "y\n"
    assert component.keys == ["x", "enter", "y", "enter"]


def test_custom_editor_read_line_wires_camel_action_handlers_with_pi_key_specs(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CamelActionCustomEditor()

    def handle_input(key: str) -> None:
        assert component.keybindings is not None
        if getattr(component.keybindings, "matches")(
            key, "app.model.cycleForward"
        ):
            handler = component.actionHandlers["app.model.cycleForward"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)
            return
        component.text += key
        if component.on_change is not None:
            component.on_change(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("draft", "ctrl+p"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    def factory(tui: object, theme: object, keybindings: object) -> _CamelActionCustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    assert ui.read_line("> ") == "\x00pipy-hotkey:model-cycle-next\n"
    assert ui._pending_initial_text == "draft"

    keys = iter(("enter",))
    assert ui.read_line("> ") == "draft\n"
    assert component.get_text() == ""


def test_custom_editor_read_line_forwards_resolved_autocomplete_provider(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    provider = _IdentityAutocompleteProvider()
    keys: Iterator[str] = iter(("tab", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    ui.set_input_text("seed")
    ui.add_extension_autocomplete_provider(lambda base: provider)
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == "seed\n"

    assert component.autocomplete_provider is provider
    assert component.autocomplete_results == ["provider:4"]
    assert component.keys == ["tab", "enter"]


def test_custom_editor_component_renders_routes_keys_and_submits(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.set_input_text("seed")
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.get_editor_component() is not None
    assert "custom editor: seed" in "\n".join(ui.render_lines(width=72, height=12))

    assert ui._handle_custom_editor_key("!") is None
    assert component.keys == ["!"]
    assert ui.get_input_text() == "seed!"
    assert ui._handle_custom_editor_key("enter") == "seed!"


def test_custom_editor_component_clear_preserves_text(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.set_input_text("before")
    ui.set_editor_component(lambda tui, theme, keybindings: component)
    assert ui._handle_custom_editor_key("+") is None

    ui.set_editor_component(None)

    assert ui.get_editor_component() is None
    assert ui.get_input_text() == "before+"
    assert "before+" in "\n".join(ui.render_lines(width=72, height=12))


def test_custom_editor_component_factory_failure_falls_back(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.set_input_text("kept")

    def bad_factory(tui: object, theme: object, keybindings: object) -> object:
        raise RuntimeError("boom")

    ui.set_editor_component(bad_factory)

    assert ui.get_editor_component() is bad_factory
    assert ui.get_input_text() == "kept"


def test_custom_editor_component_none_return_still_reports_factory(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    ui.set_input_text("kept")

    def empty_factory(tui: object, theme: object, keybindings: object) -> object | None:
        return None

    ui.set_editor_component(empty_factory)

    assert ui.get_editor_component() is empty_factory
    assert ui.get_input_text() == "kept"


def test_custom_editor_receives_latest_autocomplete_provider(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    provider = object()
    component = _CustomEditor()

    ui.add_extension_autocomplete_provider(lambda base: provider)
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert component.autocomplete_provider is provider


def test_custom_editor_component_app_action_preserves_text_for_next_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "shift-tab":
            handler = component.action_handlers["app.thinking.cycle"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)
            return
        component.text += key
        if component.on_change is not None:
            component.on_change(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("a", "shift-tab"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == f"{HOTKEY_THINKING_CYCLE}\n"
    assert ui._pending_initial_text == "a"

    keys = iter(("enter",))
    assert ui.read_line("> ") == "a\n"
    assert component.get_text() == ""


def test_custom_editor_keybindings_expose_pi_style_bindings(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    keybindings = component.keybindings
    assert keybindings is not None
    assert getattr(keybindings, "keys_for")("app.model.cycleForward") == ["ctrl+p"]
    assert getattr(keybindings, "matches")("ctrl-p", "app.model.cycleForward")
    assert getattr(keybindings, "matches")("ctrl+p", "app.model.cycleForward")
    assert getattr(keybindings, "keys_for")("app.clipboard.pasteImage") == ["ctrl+v"]
    assert "app.clear" not in component.action_handlers
    assert "app.clipboard.pasteImage" not in component.action_handlers
    assert callable(getattr(component, "on_paste_image"))


def test_custom_editor_keybindings_match_real_decoded_keys(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    keybindings = component.keybindings
    assert keybindings is not None
    assert getattr(keybindings, "matches")(
        _decode_key(ui, b"\x1b"), "app.interrupt"
    )
    assert getattr(keybindings, "matches")(
        _decode_key(ui, b"\x0c"), "app.model.select"
    )
    assert getattr(keybindings, "matches")(
        _decode_key(ui, b"\x1b\r"), "app.message.followUp"
    )
    assert getattr(keybindings, "matches")(
        _decode_key(ui, b"\x16"), "app.clipboard.pasteImage"
    )


def test_custom_editor_component_model_select_preserves_draft(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    def handle_input(key: str) -> None:
        assert component.keybindings is not None
        if getattr(component.keybindings, "matches")(key, "app.model.select"):
            handler = component.action_handlers["app.model.select"]
            assert callable(handler)
            handler()
            return
        component.text += key
        if component.on_change is not None:
            component.on_change(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("draft", "ctrl-l"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(factory)

    assert ui.read_line("> ") == f"{HOTKEY_MODEL_SELECT}\n"
    assert ui._pending_initial_text == "draft"


def test_custom_editor_component_follow_up_queues_and_clears_draft(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "alt-enter":
            handler = component.action_handlers["app.message.followUp"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)
            return
        component.text += key
        if component.on_change is not None:
            component.on_change(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("later", "alt-enter", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == "\n"
    assert ui.has_pending_messages()
    ui.restore_pending_to_editor()
    assert ui.get_input_text() == "later"


def test_custom_editor_component_empty_follow_up_does_not_queue(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "alt-enter":
            handler = component.action_handlers["app.message.followUp"]
            assert callable(handler)
            handler()

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui._handle_custom_editor_key("alt-enter") is None
    assert not ui.has_pending_messages()


def test_custom_editor_component_dequeue_preserves_current_draft(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.enqueue_follow_up("queued")

    def handle_input(key: str) -> None:
        if key == "alt-up":
            handler = component.action_handlers["app.message.dequeue"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)
    component.set_text("draft")

    assert ui._handle_custom_editor_key("alt-up") is None
    assert ui.get_input_text() == "queued\n\ndraft"
    assert ui._pending_initial_text == "queued\n\ndraft"
    assert ui._handle_custom_editor_key("enter") == "queued\n\ndraft"
    assert ui._pending_initial_text is None


def test_custom_editor_component_follow_up_clears_restored_prefill(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.enqueue_follow_up("queued")

    def handle_input(key: str) -> None:
        if key == "alt-up":
            handler = component.action_handlers["app.message.dequeue"]
            assert callable(handler)
            handler()
            return
        if key == "alt-enter":
            handler = component.action_handlers["app.message.followUp"]
            assert callable(handler)
            handler()

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui._handle_custom_editor_key("alt-up") is None
    assert ui._pending_initial_text == "queued"
    assert ui._handle_custom_editor_key("alt-enter") is None
    assert ui._pending_initial_text is None
    assert ui.get_input_text() == ""


def test_custom_editor_component_interrupt_clears_restored_prefill(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.enqueue_follow_up("queued")

    def handle_input(key: str) -> None:
        if key == "alt-up":
            handler = component.action_handlers["app.message.dequeue"]
            assert callable(handler)
            handler()
            return
        if key == "escape":
            assert component.on_escape is not None
            component.on_escape()

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui._handle_custom_editor_key("alt-up") is None
    assert ui._pending_initial_text == "queued"
    assert ui._handle_custom_editor_key("escape") is None
    assert ui._pending_initial_text is None
    assert ui.get_input_text() == ""


def test_custom_editor_component_restore_survives_next_read_line(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    ui.enqueue_follow_up("queued")
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    ui.restore_pending_to_editor()

    keys: Iterator[str] = iter(("enter",))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    assert ui.read_line("> ") == "queued\n"


def test_custom_editor_component_exit_only_when_empty(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "ctrl-d":
            handler = component.action_handlers["app.exit"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)
            return
        component.text += key
        if component.on_change is not None:
            component.on_change(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("x", "ctrl-d", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == "x\n"

    component = _CustomEditor()
    component.handle_input = handle_input  # type: ignore[method-assign]
    keys = iter(("ctrl-d",))
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == ""


def test_custom_editor_component_exit_flag_does_not_leak(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "ctrl-d":
            handler = component.action_handlers["app.exit"]
            assert callable(handler)
            handler()
            return
        if key == "enter":
            assert component.on_submit is not None
            component.on_submit(component.text)

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui._handle_custom_editor_key("ctrl-d") == ""
    component.set_text("next")
    assert ui._handle_custom_editor_key("enter") == "next"


def test_custom_editor_component_preserves_existing_escape_callback(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    seen: list[str] = []
    component.onEscape = lambda: seen.append("custom")

    def handle_input(key: str) -> None:
        if key == "escape":
            assert component.on_escape is not None
            component.on_escape()

    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert component.on_escape is component.onEscape
    assert ui._handle_custom_editor_key("escape") is None
    assert seen == ["custom"]
    assert ui._custom_editor_action is None


def test_custom_editor_component_paste_image_inserts_into_component(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    def handle_input(key: str) -> None:
        assert component.keybindings is not None
        if getattr(component.keybindings, "matches")(key, "app.clipboard.pasteImage"):
            paste = getattr(component, "on_paste_image")
            assert callable(paste)
            paste()

    ui.clipboard_temp_dir = tmp_path / "clip"
    ui.clipboard_image_read = lambda: ImageClipboardResult(
        found=True,
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
        media_type="image/png",
        detail="ok",
    )
    component.handle_input = handle_input  # type: ignore[method-assign]
    ui.set_editor_component(factory)

    assert ui._handle_custom_editor_key("ctrl-v") is None
    assert component.get_text().startswith("@image:")
    assert ui.get_input_text() == component.get_text()


def test_custom_editor_component_extension_shortcut_routes_to_session(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()

    def handle_input(key: str) -> None:
        if key == "ctrl+x":
            assert component.on_extension_shortcut is not None
            component.on_extension_shortcut(key)

    component.handle_input = handle_input  # type: ignore[method-assign]
    keys: Iterator[str] = iter(("ctrl+x",))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.extension_shortcut_keys = frozenset({"ctrl+x"})
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}ctrl+x\n"


def test_custom_editor_component_ignores_non_callable_factory(tmp_path: Path) -> None:
    ui = _ui(tmp_path)

    ui.set_editor_component(object())

    assert ui.get_editor_component() is None


def test_custom_editor_keybindings_include_external_editor_and_overrides(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    default = ui.keybindings_manager = KeybindingsManager()
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    assert component.keybindings is not None
    assert "app.editor.external" in component.action_handlers
    assert getattr(component.keybindings, "keys_for")("app.editor.external") == [
        "ctrl+g"
    ]
    assert getattr(component.keybindings, "matches")("ctrl+g", "app.editor.external")
    assert getattr(component.keybindings, "matches")("ctrl-g", "app.editor.external")

    ui.keybindings_manager = KeybindingsManager({"app.editor.external": "Ctrl+X"})
    component = _CustomEditor()

    def override_factory(
        tui: object, theme: object, keybindings: object
    ) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(override_factory)

    assert component.keybindings is not None
    assert default.keys_for("app.editor.external") == ["ctrl+g"]
    assert getattr(component.keybindings, "keys_for")("app.editor.external") == [
        "ctrl+x"
    ]
    assert getattr(component.keybindings, "matches")("ctrl+x", "app.editor.external")
    assert getattr(component.keybindings, "matches")("ctrl-x", "app.editor.external")
    assert not getattr(component.keybindings, "matches")(
        "ctrl-g", "app.editor.external"
    )

    ui.keybindings_manager = KeybindingsManager({"app.editor.external": []})
    component = _CustomEditor()
    ui.set_editor_component(override_factory)
    assert component.keybindings is not None
    assert getattr(component.keybindings, "keys_for")("app.editor.external") == []
    assert not getattr(component.keybindings, "matches")(
        "ctrl-g", "app.editor.external"
    )


def test_custom_editor_keybindings_manager_preserves_other_default_actions(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    ui.keybindings_manager = KeybindingsManager(
        {
            "app.editor.external": "ctrl+x",
            "app.model.cycleForward": "ctrl+y",
        }
    )
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    assert component.keybindings is not None
    assert getattr(component.keybindings, "keys_for")("app.model.cycleForward") == [
        "ctrl+p"
    ]
    assert getattr(component.keybindings, "matches")(
        "ctrl-p", "app.model.cycleForward"
    )
    assert not getattr(component.keybindings, "matches")(
        "ctrl-y", "app.model.cycleForward"
    )
    assert getattr(component.keybindings, "keys_for")("app.thinking.cycle") == [
        "shift+tab"
    ]
    assert getattr(component.keybindings, "matches")("shift-tab", "app.thinking.cycle")
    paste_key = "alt+v" if sys.platform == "win32" else "ctrl+v"
    paste_decoded = "alt-v" if sys.platform == "win32" else "ctrl-v"
    assert getattr(component.keybindings, "keys_for")("app.clipboard.pasteImage") == [
        paste_key
    ]
    assert getattr(component.keybindings, "matches")(
        paste_decoded, "app.clipboard.pasteImage"
    )


def test_custom_editor_keybindings_manager_matches_all_default_action_aliases(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    ui.keybindings_manager = KeybindingsManager()
    component = _CustomEditor()

    def factory(tui: object, theme: object, keybindings: object) -> _CustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    ui.set_editor_component(factory)

    assert component.keybindings is not None
    expected = {
        "app.interrupt": ("esc", "escape"),
        "app.exit": ("ctrl-d", "ctrl+d"),
        "app.thinking.cycle": ("shift-tab", "shift+tab"),
        "app.model.cycleForward": ("ctrl-p", "ctrl+p"),
        "app.model.cycleBackward": ("shift-ctrl-p", "shift+ctrl+p"),
        "app.model.select": ("ctrl-l", "ctrl+l"),
        "app.tools.expand": ("ctrl-o", "ctrl+o"),
        "app.thinking.toggle": ("ctrl-t", "ctrl+t"),
        "app.editor.external": ("ctrl-g", "ctrl+g"),
        "app.message.followUp": ("alt-enter", "alt+enter"),
        "app.message.dequeue": ("alt-up", "alt+up"),
    }
    for action, (decoded_key, spec) in expected.items():
        assert getattr(component.keybindings, "keys_for")(action) == [spec], action
        assert getattr(component.keybindings, "matches")(decoded_key, action), action
    assert getattr(component.keybindings, "matches")(
        "ctrl+shift+p", "app.model.cycleBackward"
    )


def test_custom_editor_keybinding_actions_exist_in_default_table() -> None:
    actions = (*_CustomEditorKeybindings._HANDLER_ACTIONS, "app.clipboard.pasteImage")

    assert all(action in DEFAULT_KEYBINDINGS for action in actions)


def test_custom_editor_external_editor_action_updates_draft_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    input_file: TextIO
    terminal_file: TextIO
    component = _CamelActionCustomEditor()

    def handle_input(key: str) -> None:
        assert component.keybindings is not None
        if getattr(component.keybindings, "matches")(key, "app.editor.external"):
            handler = component.actionHandlers["app.editor.external"]
            assert callable(handler)
            handler()

    editor = tmp_path / "fake-editor.py"
    editor.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text('edited:' + text, encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv(
        "EDITOR", f"{shlex.quote(sys.executable)} {shlex.quote(str(editor))}"
    )
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    paints: list[str] = []
    monkeypatch.setattr(ToolLoopTerminalUi, "paint", lambda self: paints.append("paint"))

    def factory(
        tui: object, theme: object, keybindings: object
    ) -> _CamelActionCustomEditor:
        del tui, theme
        component.keybindings = keybindings
        return component

    with (
        open(os.devnull, "r", encoding="utf-8") as input_file,
        open(os.devnull, "w", encoding="utf-8") as terminal_file,
    ):
        ui = ToolLoopTerminalUi(
            input_stream=cast(TextIO, input_file),
            terminal_stream=cast(TextIO, terminal_file),
            cwd=tmp_path,
        )
        ui.set_input_text("seed")
        component.handle_input = handle_input  # type: ignore[method-assign]
        ui.set_editor_component(factory)
        assert ui._handle_custom_editor_key("ctrl-g") is None
        assert component.get_text() == "edited:seed"
        assert ui.get_input_text() == "edited:seed"
        assert ui._custom_editor_submitted is None
        assert paints


def test_custom_editor_external_editor_action_preserves_draft_without_editor(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CamelActionCustomEditor()

    def handle_input(key: str) -> None:
        if key == "ctrl-g":
            handler = component.actionHandlers["app.editor.external"]
            assert callable(handler)
            handler()

    ui.set_input_text("seed")
    component.handle_input = handle_input  # type: ignore[method-assign]
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui._handle_custom_editor_key("ctrl-g") is None
    assert component.get_text() == "seed"
    assert ui.get_input_text() == "seed"
    assert ui._custom_editor_submitted is None


def test_custom_editor_external_editor_action_preserves_draft_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    input_file: TextIO
    terminal_file: TextIO
    component = _CamelActionCustomEditor()

    def handle_input(key: str) -> None:
        if key == "ctrl-g":
            handler = component.actionHandlers["app.editor.external"]
            assert callable(handler)
            handler()

    editor = tmp_path / "failing-editor.py"
    editor.write_text(
        "#!/usr/bin/env python3\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "VISUAL", f"{shlex.quote(sys.executable)} {shlex.quote(str(editor))}"
    )
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(ToolLoopTerminalUi, "paint", lambda self: None)
    with (
        open(os.devnull, "r", encoding="utf-8") as input_file,
        open(os.devnull, "w", encoding="utf-8") as terminal_file,
    ):
        ui = ToolLoopTerminalUi(
            input_stream=cast(TextIO, input_file),
            terminal_stream=cast(TextIO, terminal_file),
            cwd=tmp_path,
        )
        ui.set_input_text("seed")
        component.handle_input = handle_input  # type: ignore[method-assign]
        ui.set_editor_component(lambda tui, theme, keybindings: component)
        assert ui._handle_custom_editor_key("ctrl-g") is None
        assert component.get_text() == "seed"
        assert ui.get_input_text() == "seed"
        assert ui._custom_editor_submitted is None


def test_default_editor_external_editor_action_updates_draft_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    editor = tmp_path / "fake-editor.py"
    editor.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text('edited:' + text, encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv(
        "EDITOR", f"{shlex.quote(sys.executable)} {shlex.quote(str(editor))}"
    )
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    keys: Iterator[str] = iter(("ctrl-g", "enter"))
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    with (
        open(os.devnull, "r", encoding="utf-8") as input_file,
        open(os.devnull, "w", encoding="utf-8") as terminal_file,
    ):
        ui = ToolLoopTerminalUi(
            input_stream=cast(TextIO, input_file),
            terminal_stream=cast(TextIO, terminal_file),
            cwd=tmp_path,
        )
        ui._pending_initial_text = "seed"

        assert ui.read_line("> ") == "edited:seed\n"


def test_default_editor_external_editor_action_honors_empty_user_binding(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []
    keys: Iterator[str] = iter(("ctrl-g", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    def run_editor(self: object, text: str) -> str:
        del self
        calls.append(text)
        return "edited"

    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "_run_configured_external_editor",
        run_editor,
    )

    ui = _ui(tmp_path)
    ui.keybindings_manager = KeybindingsManager({"app.editor.external": []})
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    ui._pending_initial_text = "seed"

    assert ui.read_line("> ") == "seed\n"
    assert calls == []


def test_default_editor_external_editor_action_is_undoable(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []
    keys: Iterator[str] = iter(("ctrl-g", "ctrl-z", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    def run_editor(self: object, text: str) -> str:
        del self
        calls.append(text)
        return "edited"

    monkeypatch.setattr(ToolLoopTerminalUi, "_run_configured_external_editor", run_editor)

    ui = _ui(tmp_path)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    ui._pending_initial_text = "seed"

    assert ui.read_line("> ") == "seed\n"
    assert calls == ["seed"]


def test_default_editor_external_editor_action_honors_user_override(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []
    keys: Iterator[str] = iter(("ctrl-x", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    def run_editor(self: object, text: str) -> str:
        del self
        calls.append(text)
        return "edited"

    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "_run_configured_external_editor",
        run_editor,
    )

    ui = _ui(tmp_path)
    ui.keybindings_manager = KeybindingsManager({"app.editor.external": "Ctrl+X"})
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    ui._pending_initial_text = "seed"

    assert ui.read_line("> ") == "edited\n"
    assert calls == ["seed"]


def test_default_editor_external_editor_action_precedes_extension_shortcut(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []
    keys: Iterator[str] = iter(("ctrl-x", "enter"))
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )

    def run_editor(self: object, text: str) -> str:
        del self
        calls.append(text)
        return "edited"

    monkeypatch.setattr(
        ToolLoopTerminalUi,
        "_run_configured_external_editor",
        run_editor,
    )

    ui = _ui(tmp_path)
    ui.keybindings_manager = KeybindingsManager({"app.editor.external": "Ctrl+X"})
    ui.extension_shortcut_keys = frozenset({"ctrl-x"})
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    ui._pending_initial_text = "seed"

    assert ui.read_line("> ") == "edited\n"
    assert calls == ["seed"]
