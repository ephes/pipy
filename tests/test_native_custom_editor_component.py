from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.tui import ToolLoopTerminalUi


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


class _CustomEditor:
    def __init__(self) -> None:
        self.text = ""
        self.keys: list[str] = []
        self.on_submit = None
        self.on_change = None
        self.autocomplete_provider: object | None = None
        self.autocomplete_results: list[object] = []

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


def test_custom_editor_read_line_routes_keys_only_to_custom_component(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    keys: Iterator[str] = iter(("x", "enter"))
    monkeypatch.setattr(ToolLoopTerminalUi, "_enter_raw_mode", lambda self: None)
    monkeypatch.setattr(ToolLoopTerminalUi, "_restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(ui.input_stream, "fileno", lambda: 0)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda self, fd: next(keys)
    )
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert ui.read_line("> ") == "x\n"

    assert component.keys == ["x", "enter"]
    assert ui.input_text == ""
    assert component.get_text() == ""

    keys = iter(("y", "enter"))
    assert ui.read_line("> ") == "y\n"
    assert component.keys == ["x", "enter", "y", "enter"]


def test_custom_editor_read_line_forwards_resolved_autocomplete_provider(
    tmp_path: Path, monkeypatch
) -> None:
    ui = _ui(tmp_path)
    component = _CustomEditor()
    provider = _IdentityAutocompleteProvider()
    keys: Iterator[str] = iter(("tab", "enter"))
    monkeypatch.setattr(ToolLoopTerminalUi, "_enter_raw_mode", lambda self: None)
    monkeypatch.setattr(ToolLoopTerminalUi, "_restore_terminal_mode", lambda self: None)
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

    assert ui.get_editor_component() is None
    assert ui.get_input_text() == "kept"


def test_custom_editor_receives_latest_autocomplete_provider(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    provider = object()
    component = _CustomEditor()

    ui.add_extension_autocomplete_provider(lambda base: provider)
    ui.set_editor_component(lambda tui, theme, keybindings: component)

    assert component.autocomplete_provider is provider


def test_custom_editor_component_ignores_non_callable_factory(tmp_path: Path) -> None:
    ui = _ui(tmp_path)

    ui.set_editor_component(object())

    assert ui.get_editor_component() is None
