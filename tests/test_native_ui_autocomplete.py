"""Unit tests for the autocomplete owner component and its command surface.

These drive ``AutocompleteComponent`` directly against a plain ``EditorState``
(no terminal shell, no PTY) to prove the frozen ``CommandSurface`` replace
verb, the settings-driven ``max_visible`` cap, provider-registry effects with
the injected custom-editor forward, popup transitions, and slash-menu
priority. Facade-level behavior (typing, key dispatch, frames) stays covered
by ``test_native_tui_completion.py`` and the extension-provider suite.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pipy_harness.native.editor_state import CompletionItem, EditorState
from pipy_harness.native.ui.autocomplete import (
    AutocompleteComponent,
    CommandSurface,
)


class _Harness:
    """A component wired to a bare editor record, counting effects."""

    def __init__(self, tmp_path: Path, *, custom_editor: object | None = None) -> None:
        self.editor = EditorState()
        self.repaints = 0
        self.custom_editor = custom_editor
        self.component = AutocompleteComponent(
            self.editor,
            cwd=tmp_path,
            repaint=self._repaint,
            custom_editor_component=lambda: self.custom_editor,
            surface=CommandSurface(
                names=("/hotkeys", "/model", "/settings"),
                descriptions={"/model": "Pick a model"},
            ),
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def type_text(self, text: str) -> None:
        for char in text:
            self.editor.insert(char, self.component.command_names)
            self.component.refresh()


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("x\n")
    (tmp_path / "README.md").write_text("y\n")
    return tmp_path


def test_replace_command_surface_swaps_all_three_parts_together(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)

    harness.component.replace_command_surface(
        CommandSurface(
            names=("/reload",),
            descriptions={"/reload": "Reload extensions"},
            shortcut_keys=frozenset({"ctrl-k"}),
        )
    )

    assert harness.component.command_names == ("/reload",)
    assert harness.component.command_descriptions == {"/reload": "Reload extensions"}
    assert harness.component.shortcut_keys == frozenset({"ctrl-k"})
    # The published record is frozen: writers build a new surface instead of
    # mutating the live one under a concurrent reader.
    surface = harness.component.command_surface
    assert replace(surface, names=()).names == ()
    assert surface.names == ("/reload",)


def test_default_surface_is_empty_and_max_visible_defaults_to_five(
    tmp_path: Path,
) -> None:
    component = AutocompleteComponent(
        EditorState(),
        cwd=tmp_path,
        repaint=lambda: None,
        custom_editor_component=lambda: None,
    )

    assert component.command_names == ()
    assert component.shortcut_keys == frozenset()
    assert component.max_visible == 5


def test_set_max_visible_caps_both_popup_frames(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.component.replace_command_surface(
        CommandSurface(names=tuple(f"/cmd{i}" for i in range(10)))
    )
    harness.editor.set_buffer("/cmd")
    harness.editor.refresh_slash_menu(harness.component.command_names)
    harness.component.set_max_visible(3)

    slash_rows = [
        line
        for line in harness.component.popup_menu_frame_lines(width=80, max_rows=20)
        if line.kind in {"slash_menu", "slash_menu_selected"}
    ]
    assert len(slash_rows) == 3

    harness.editor.close_slash_menu()
    harness.editor.open_autocomplete(
        items=tuple(CompletionItem(f"item{i}", f"item{i}") for i in range(10)),
        mode="at",
        token_start=0,
        prefix="",
    )
    popup_rows = [
        line
        for line in harness.component.popup_menu_frame_lines(width=80, max_rows=20)
        if line.kind in {"slash_menu", "slash_menu_selected"}
    ]
    assert len(popup_rows) == 3


def test_slash_menu_keeps_popup_priority_and_descriptions(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.editor.set_buffer("/mo")
    harness.editor.refresh_slash_menu(harness.component.command_names)
    harness.editor.autocomplete_open = True

    lines = harness.component.popup_menu_frame_lines(width=80, max_rows=10)

    rendered = "\n".join(line.text for line in lines)
    assert "model" in rendered
    assert "Pick a model" in rendered


def test_refresh_opens_at_picker_and_slash_menu_keeps_priority(
    tmp_path: Path,
) -> None:
    harness = _Harness(_workspace(tmp_path))

    harness.type_text("see @config")
    assert harness.editor.autocomplete_open
    assert harness.editor.autocomplete_mode == "at"

    # A leading slash opens the menu instead; refresh closes the popup.
    harness.editor.set_buffer("/mo")
    harness.editor.refresh_slash_menu(harness.component.command_names)
    harness.component.refresh()
    assert harness.editor.slash_menu_open
    assert not harness.editor.autocomplete_open


def test_attempt_path_completion_refuses_while_slash_menu_open(
    tmp_path: Path,
) -> None:
    harness = _Harness(_workspace(tmp_path))
    harness.editor.set_buffer("/mo")
    harness.editor.refresh_slash_menu(harness.component.command_names)

    assert harness.component.attempt_path_completion() is False
    assert not harness.editor.autocomplete_open


def test_navigate_repaints_only_when_selection_moves(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    harness.component.navigate("down")
    assert harness.repaints == 0

    harness.editor.open_autocomplete(
        items=(CompletionItem("one", "One"), CompletionItem("two", "Two")),
        mode="at",
        token_start=0,
        prefix="",
    )
    harness.component.navigate("down")
    assert harness.repaints == 1


def test_add_extension_provider_forwards_to_live_custom_editor(
    tmp_path: Path,
) -> None:
    forwarded: list[object] = []

    class _CustomEditor:
        def set_autocomplete_provider(self, provider: object) -> None:
            forwarded.append(provider)

    harness = _Harness(tmp_path, custom_editor=_CustomEditor())

    class _Provider:
        def __init__(self, base: object) -> None:
            self.base = base

    harness.component.add_extension_provider(lambda base: _Provider(base))

    assert harness.editor.autocomplete_provider_factories
    assert len(forwarded) == 1
    assert isinstance(forwarded[0], _Provider)


def test_add_extension_provider_ignores_non_callables(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    harness.component.add_extension_provider(object())

    assert harness.editor.autocomplete_provider_factories == []


def test_forward_without_factories_or_setter_is_a_no_op(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    # No factories registered: nothing to forward even with a valid setter.
    calls: list[object] = []

    class _CustomEditor:
        def set_autocomplete_provider(self, provider: object) -> None:
            calls.append(provider)

    harness.component.forward_to_custom_editor(_CustomEditor())
    assert calls == []

    # A component without a setter never raises.
    harness.editor.autocomplete_provider_factories.append(lambda base: base)
    harness.component.forward_to_custom_editor(object())
