from __future__ import annotations

import io
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.autocomplete_provider import (
    AutocompleteSuggestion,
    call_provider_method,
    coerce_suggestion,
)
from pipy_harness.native.editor_completion import CompletionItem
from pipy_harness.native.extension_types import ExtensionUiDriver
from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.extensions.contracts import (
    RegisteredCommand,
)
from pipy_harness.native.extensions.dispatch import dispatch_extension_command
from pipy_harness.native.tui import TerminalUi


class _Driver:
    def __init__(self) -> None:
        self.factories: list[object] = []

    def add_autocomplete_provider(self, factory: object) -> None:
        self.factories.append(factory)


def _ui(workspace: Path) -> TerminalUi:
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=workspace,
    )


def _type(ui: TerminalUi, text: str) -> None:
    for char in text:
        ui.input_editor.insert_text(char)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("x\n")
    return tmp_path


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    (("path", "path"), ("extension-defined", "at")),
)
def test_suggestion_mode_coercion_uses_the_supported_domain(
    raw_mode: str, expected: str
) -> None:
    suggestion = coerce_suggestion(
        {
            "items": [{"value": "value", "label": "label"}],
            "prefix": "",
            "tokenStart": 0,
            "mode": raw_mode,
        }
    )

    assert suggestion is not None
    assert suggestion.mode == expected


def test_provider_method_prefers_snake_case_and_forwards_arguments_unchanged() -> None:
    marker = object()

    class Provider:
        def get_suggestions(self, *args: object) -> tuple[str, tuple[object, ...]]:
            return "snake", args

        def getSuggestions(self, *args: object) -> tuple[str, tuple[object, ...]]:
            return "camel", args

    assert call_provider_method(
        Provider(),
        "get_suggestions",
        "getSuggestions",
        marker,
        3,
    ) == ("snake", (marker, 3))


def test_provider_method_falls_back_to_camel_case_only_when_snake_is_missing() -> None:
    class Provider:
        def getSuggestions(self, value: object) -> tuple[str, object]:
            return "camel", value

    marker = object()

    assert call_provider_method(
        Provider(),
        "get_suggestions",
        "getSuggestions",
        marker,
    ) == ("camel", marker)


@pytest.mark.parametrize("snake_value", [None, 0, "not callable"])
def test_provider_method_non_callable_snake_case_does_not_fall_through(
    snake_value: object,
) -> None:
    class Provider:
        def __init__(self, value: object) -> None:
            self.get_suggestions = value

        def getSuggestions(self) -> str:
            return "camel"

    with pytest.raises(AttributeError) as exc_info:
        call_provider_method(
            Provider(snake_value),
            "get_suggestions",
            "getSuggestions",
        )

    assert exc_info.value.args == ("get_suggestions",)


def test_provider_method_falsey_callable_snake_case_does_not_fall_through() -> None:
    class FalseyCallable:
        def __bool__(self) -> bool:
            return False

        def __call__(self, value: object) -> tuple[str, object]:
            return "snake", value

    class Provider:
        get_suggestions = FalseyCallable()

        def getSuggestions(self, value: object) -> tuple[str, object]:
            return "camel", value

    marker = object()

    assert call_provider_method(
        Provider(),
        "get_suggestions",
        "getSuggestions",
        marker,
    ) == ("snake", marker)


def test_provider_method_preserves_provider_exception() -> None:
    failure = RuntimeError("provider failure")

    class Provider:
        def get_suggestions(self) -> object:
            raise failure

    with pytest.raises(RuntimeError) as exc_info:
        call_provider_method(Provider(), "get_suggestions", "getSuggestions")

    assert exc_info.value is failure


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

    assert ui.components.autocomplete.autocomplete_open
    ui.components.autocomplete.accept_selection()

    assert ui.input_editor.text.startswith("see @src/config.py")
    assert "@@" not in ui.input_editor.text


def test_extension_autocomplete_provider_can_append_at_suggestion(
    tmp_path: Path,
) -> None:
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

    ui._autocomplete.add_extension_provider(lambda current: Wrapper(current))
    _type(ui, "see @config")

    assert ui.components.autocomplete.autocomplete_open
    assert any(
        item.label == "virtual.py"
        for item in ui.components.autocomplete.autocomplete_items
    )


def test_path_accept_after_common_prefix_expansion_replaces_full_inserted_prefix(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scraps").mkdir()
    _type(ui, "./sc")

    assert ui.components.autocomplete.attempt_path_completion()
    assert ui.input_editor.text == "./scr"
    assert ui.components.autocomplete.autocomplete_open
    ui.components.autocomplete.autocomplete_selection = next(
        index
        for index, item in enumerate(ui.components.autocomplete.autocomplete_items)
        if item.label == "scripts/"
    )
    ui.components.autocomplete.accept_selection()

    assert ui.input_editor.text == "./scripts/"


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

    ui._autocomplete.add_extension_provider(lambda current: Provider(current))
    _type(ui, "ask @x")
    assert ui.components.autocomplete.autocomplete_open
    ui.components.autocomplete.accept_selection()

    assert ui.input_editor.text == "set by provider"
    assert ui.input_editor.cursor == 3


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

    ui._autocomplete.add_extension_provider(lambda current: Provider(current))
    _type(ui, "./s")

    assert ui.components.autocomplete.attempt_path_completion() is False
    assert ui.input_editor.text == "./s"


def test_broken_extension_autocomplete_provider_falls_back(tmp_path: Path) -> None:
    ui = _ui(_workspace(tmp_path))

    class Broken:
        def get_suggestions(self, *args):
            raise RuntimeError("boom")

    ui._autocomplete.add_extension_provider(lambda _current: Broken())
    _type(ui, "@config")

    assert ui.components.autocomplete.autocomplete_open
    assert any(
        item.label == "config.py"
        for item in ui.components.autocomplete.autocomplete_items
    )


@pytest.mark.parametrize(
    "operation",
    ("delete_before_cursor", "kill_to_line_start", "undo", "redo"),
)
def test_no_op_edit_keys_do_not_execute_extension_completion_lookup(
    tmp_path: Path, operation: str
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Factory:
        calls = 0

        def __call__(self, current: object) -> object:
            self.calls += 1
            return current

    factory = Factory()
    ui._autocomplete.add_extension_provider(factory)
    provider = object()
    ui.input_editor.editor_state.open_autocomplete(
        items=(CompletionItem("kept", "Kept"),),
        mode="at",
        token_start=0,
        prefix="",
        active_provider=provider,
    )

    getattr(ui.input_editor, operation)()

    assert factory.calls == 0
    assert ui.components.autocomplete.autocomplete_open
    assert ui.input_editor.editor_state.autocomplete_active_provider is provider


def test_empty_character_insert_keeps_edit_boundary_and_completion_refresh(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        calls = 0

        def get_suggestions(self, *args: object) -> None:
            self.calls += 1
            return None

    provider = Provider()
    ui._autocomplete.add_extension_provider(lambda _current: provider)
    ui.input_editor.editor_state.set_buffer("draft", cursor=2)
    ui.input_editor.editor_state.history_nav_index = 1
    ui.input_editor.editor_state.history_draft = "old"
    ui.input_editor.editor_state.redo_stack.append(("redo", 4))

    ui.input_editor.insert_text("")

    assert (ui.input_editor.text, ui.input_editor.cursor) == ("draft", 2)
    assert ui.input_editor.undo_stack == [("draft", 2)]
    assert ui.input_editor.redo_stack == []
    assert ui.input_editor.history_nav_index is None
    assert provider.calls == 1


def test_empty_paste_keeps_pre_extraction_no_op_semantics(tmp_path: Path) -> None:
    ui = _ui(_workspace(tmp_path))
    ui.input_editor.editor_state.set_buffer("draft", cursor=2)
    ui.input_editor.editor_state.redo_stack.append(("redo", 4))

    ui.clipboard_images.insert_paste("")

    assert (ui.input_editor.text, ui.input_editor.cursor) == ("draft", 2)
    assert ui.input_editor.undo_stack == []
    assert ui.input_editor.redo_stack == [("redo", 4)]


def test_empty_extension_suggestion_closes_popup_and_provider_binding(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        def get_suggestions(self, *args: object) -> AutocompleteSuggestion:
            return AutocompleteSuggestion((), "x", 0, "at")

    provider = Provider()
    ui._autocomplete.add_extension_provider(lambda _current: provider)
    _type(ui, "@x")

    assert not ui.components.autocomplete.autocomplete_open
    assert ui.components.autocomplete.autocomplete_items == ()
    assert ui.input_editor.editor_state.autocomplete_active_provider is None
    before = (ui.input_editor.text, ui.input_editor.cursor)

    ui.components.autocomplete.accept_selection()

    assert (ui.input_editor.text, ui.input_editor.cursor) == before
    assert not ui.components.autocomplete.autocomplete_open
    assert ui.input_editor.editor_state.autocomplete_active_provider is None


def test_acceptance_uses_one_snapshot_when_provider_mutates_editor(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        received_prefix = ""

        def apply_completion(
            self,
            lines: tuple[str, ...],
            cursor_line: int,
            cursor_col: int,
            item: CompletionItem,
            prefix: str,
        ) -> None:
            assert lines == ("ask @x",)
            assert (cursor_line, cursor_col) == (0, 6)
            assert item == CompletionItem("@selected", "Selected")
            self.received_prefix = prefix
            ui.input_editor.editor_state.set_buffer("mutated by extension")
            ui.components.autocomplete.autocomplete_mode = "path"
            ui.components.autocomplete.autocomplete_token_start = 10
            ui.components.autocomplete.autocomplete_items = (
                CompletionItem("wrong", "Wrong"),
            )
            return None

    provider = Provider()
    ui.input_editor.editor_state.set_buffer("ask @x")
    ui.input_editor.editor_state.open_autocomplete(
        items=(CompletionItem("@selected", "Selected"),),
        mode="at",
        token_start=4,
        prefix="x",
        active_provider=provider,
    )

    ui.components.autocomplete.accept_selection()

    assert provider.received_prefix == "x"
    assert ui.input_editor.text == "ask @selected"
    assert ui.input_editor.cursor == len("ask @selected")
    assert not ui.components.autocomplete.autocomplete_open


def test_directory_reopen_uses_accepted_snapshot_after_provider_mutation(
    tmp_path: Path,
) -> None:
    ui = _ui(_workspace(tmp_path))

    class Provider:
        suggestion_calls = 0

        def should_trigger_file_completion(self, *args: object) -> bool:
            return True

        def get_suggestions(self, *args: object) -> None:
            self.suggestion_calls += 1
            return None

        def apply_completion(self, *args: object) -> None:
            ui.input_editor.editor_state.set_buffer("mutated by extension")
            ui.components.autocomplete.autocomplete_mode = "at"
            ui.components.autocomplete.autocomplete_token_start = 9
            ui.components.autocomplete.autocomplete_items = (
                CompletionItem("wrong", "Wrong"),
            )
            return None

    provider = Provider()
    ui._autocomplete.add_extension_provider(lambda _current: provider)
    ui.input_editor.editor_state.set_buffer("./scr")
    ui.input_editor.editor_state.open_autocomplete(
        items=(CompletionItem("./scripts/", "scripts/"),),
        mode="path",
        token_start=0,
        prefix="./scr",
        active_provider=provider,
    )

    ui.components.autocomplete.accept_selection()

    assert ui.input_editor.text == "./scripts/"
    assert provider.suggestion_calls == 1
