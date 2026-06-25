from __future__ import annotations

import io
from pathlib import Path
from typing import cast

from pipy_harness.native.autocomplete_provider import AutocompleteSuggestion
from pipy_harness.native.editor_completion import CompletionItem
from pipy_harness.native.extension_runtime import (
    ExtensionUiDriver,
    RegisteredCommand,
    _CollectingUi,
    dispatch_extension_command,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


class _Driver:
    def __init__(self) -> None:
        self.factories: list[object] = []

    def add_autocomplete_provider(self, factory: object) -> None:
        self.factories.append(factory)


def _ui(workspace: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=workspace,
    )


def _type(ui: ToolLoopTerminalUi, text: str) -> None:
    for char in text:
        ui._insert_input_text(char)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("x\n")
    return tmp_path


def test_collecting_ui_autocomplete_aliases_delegate_in_order() -> None:
    driver = _Driver()
    ui = _CollectingUi(has_ui=True, ui_driver=cast(ExtensionUiDriver, driver))
    def first(current):
        return current

    def second(current):
        return current

    ui.add_autocomplete_provider(first)
    ui.addAutocompleteProvider(second)

    assert ui.autocomplete_providers == [first, second]
    assert driver.factories == [first, second]


def test_extension_command_can_register_autocomplete_provider(tmp_path: Path) -> None:
    driver = _Driver()
    def factory(current):
        return current

    def handler(ctx, _args):
        ctx.ui.addAutocompleteProvider(factory)

    command = RegisteredCommand("probe", "probe", handler, "ext")
    dispatch = dispatch_extension_command(
        "/probe",
        {"probe": command},
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=cast(ExtensionUiDriver, driver),
    )

    assert dispatch is not None and dispatch.ran
    assert driver.factories == [factory]


def test_builtin_at_acceptance_replaces_the_whole_at_token(tmp_path: Path) -> None:
    ui = _ui(_workspace(tmp_path))
    _type(ui, "see @config")

    assert ui.autocomplete_open
    ui._accept_autocomplete_selection()

    assert ui.input_text.startswith("see @src/config.py")
    assert "@@" not in ui.input_text


def test_extension_autocomplete_provider_can_append_at_suggestion(tmp_path: Path) -> None:
    ui = _ui(_workspace(tmp_path))

    class Wrapper:
        def __init__(self, current):
            self.current = current

        def get_suggestions(self, lines, cursor_line, cursor_col, context):
            base = self.current.get_suggestions(lines, cursor_line, cursor_col, context)
            assert context.force is False
            assert base is not None
            return {
                "items": [*base.items, {"value": "@virtual.py", "label": "virtual.py"}],
                "prefix": base.prefix,
                "token_start": base.token_start,
                "mode": base.mode,
            }

        def apply_completion(self, *args):
            return self.current.apply_completion(*args)

    ui.add_extension_autocomplete_provider(lambda current: Wrapper(current))
    _type(ui, "see @config")

    assert ui.autocomplete_open
    assert any(item.label == "virtual.py" for item in ui.autocomplete_items)


def test_path_accept_after_common_prefix_expansion_replaces_full_inserted_prefix(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scraps").mkdir()
    _type(ui, "./sc")

    assert ui._attempt_path_completion()
    assert ui.input_text == "./scr"
    assert ui.autocomplete_open
    ui.autocomplete_selection = next(
        index for index, item in enumerate(ui.autocomplete_items) if item.label == "scripts/"
    )
    ui._accept_autocomplete_selection()

    assert ui.input_text == "./scripts/"


def test_extension_autocomplete_provider_custom_apply_controls_insertion(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        def __init__(self, current):
            self.current = current

        def get_suggestions(self, lines, cursor_line, cursor_col, context):
            return AutocompleteSuggestion(
                (CompletionItem("ignored", "Custom"),), "@x", 4, "at"
            )

        def apply_completion(self, lines, cursor_line, cursor_col, item, prefix):
            return {"text": "set by provider", "cursor": 3}

    ui.add_extension_autocomplete_provider(lambda current: Provider(current))
    _type(ui, "ask @x")
    assert ui.autocomplete_open
    ui._accept_autocomplete_selection()

    assert ui.input_text == "set by provider"
    assert ui.input_cursor == 3


def test_extension_autocomplete_provider_can_veto_forced_path_completion(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        def __init__(self, current):
            self.current = current

        def should_trigger_file_completion(self, lines, cursor_line, cursor_col):
            return False

        def get_suggestions(self, *args):  # pragma: no cover - veto prevents call
            raise AssertionError("must not call suggestions")

    ui.add_extension_autocomplete_provider(lambda current: Provider(current))
    _type(ui, "./s")

    assert ui._attempt_path_completion() is False
    assert ui.input_text == "./s"


def test_broken_extension_autocomplete_provider_falls_back(tmp_path: Path) -> None:
    ui = _ui(_workspace(tmp_path))

    class Broken:
        def get_suggestions(self, *args):
            raise RuntimeError("boom")

    ui.add_extension_autocomplete_provider(lambda _current: Broken())
    _type(ui, "@config")

    assert ui.autocomplete_open
    assert any(item.label == "config.py" for item in ui.autocomplete_items)
