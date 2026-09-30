"""Pi's slash-command completion: fuzzy command names and argument completion.

Pins pi-mono ``1b347794e`` ``CombinedAutocompleteProvider.getSuggestions`` /
``applyCompletion`` and the editor's best-match highlight: command names are
fuzzy-filtered (``skill:`` stripped first), an accepted command becomes
``/<name> ``, and after ``/<command> `` the command's argument completions
(``/model``, ``/thinking``) replace the argument without submitting.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_native_coding_effects import _provider_mutation_fixture
from test_native_thinking_command import _settings
from test_native_ui_autocomplete import _Harness

from pipy_harness.native.editor_state import CompletionItem, prefix_command_filter
from pipy_harness.native.repl.selector_actions import slash_argument_completer
from pipy_harness.native.repl_state import NativeReplProviderState
from pipy_harness.native.ui.autocomplete import (
    ArgumentCompleter,
    CommandSurface,
    best_match_index,
    filter_slash_commands,
)

_NAMES = (
    "/settings",
    "/model",
    "/scoped-models",
    "/thinking",
    "/resume",
    "/skill:review-code",
    "/skill:model-card",
)


def test_command_names_are_fuzzy_filtered_like_pi() -> None:
    # Bare names first (`skill:` stripped), best score first.
    assert filter_slash_commands(_NAMES, "mo") == (
        "/model",
        "/skill:model-card",
        "/scoped-models",
    )
    # A `skill:` name matching only by its full name comes after the others.
    assert filter_slash_commands(_NAMES, "skre") == ("/skill:review-code",)
    # An empty prefix keeps the published order.
    assert filter_slash_commands(_NAMES, "") == _NAMES
    # The editor state keeps a dependency-free prefix default.
    assert prefix_command_filter(_NAMES, "mo") == ("/model",)


def test_best_match_prefers_exact_then_prefix() -> None:
    items = (
        CompletionItem("xhigh", "xhigh"),
        CompletionItem("high", "high"),
        CompletionItem("highest", "highest"),
    )
    assert best_match_index(items, "high") == 1
    assert best_match_index(items, "hig") == 1
    assert best_match_index(items, "x") == 0
    assert best_match_index(items, "z") == -1
    assert best_match_index(items, "") == -1


def _completer(
    tmp_path: Path, enabled_models: list[str] | None = None
) -> tuple[ArgumentCompleter, NativeReplProviderState]:
    _effects, state, *_rest = _provider_mutation_fixture(tmp_path)
    settings = _settings(tmp_path)
    if enabled_models:
        settings.set_enabled_models(enabled_models)
    return slash_argument_completer(state, settings), state


def test_model_argument_completion_lists_available_models(tmp_path: Path) -> None:
    complete, state = _completer(tmp_path)
    items = complete("model", "")
    assert items is not None
    references = {item.value for item in items}
    # Pi `getAvailableSnapshot` (no tool-loop probe on this per-edit path).
    assert references == {spec.reference for spec in state.available_model_specs()}
    gpt4o = complete("model", "openai/gpt-4o-mini")
    assert gpt4o is not None
    assert gpt4o[0] == CompletionItem("openai/gpt-4o-mini", "gpt-4o-mini", "openai")
    assert complete("model", "zzzz-nothing") is None


def test_model_argument_completion_constructs_no_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Construction can run credential helpers; completion runs on every edit.
    complete, state = _completer(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("completion constructed a provider")

    monkeypatch.setattr(NativeReplProviderState, "provider_for", refuse)
    assert complete("model", "gpt") is not None


def test_model_argument_completion_uses_the_scope(tmp_path: Path) -> None:
    complete, _state = _completer(
        tmp_path, enabled_models=["openai/gpt-4o", "openai-completions/gpt-4.1"]
    )
    items = complete("model", "")
    assert items is not None
    assert [item.value for item in items] == [
        "openai/gpt-4o",
        "openai-completions/gpt-4.1",
    ]


def test_thinking_argument_completion_and_other_commands(tmp_path: Path) -> None:
    complete, _state = _completer(tmp_path)
    items = complete("thinking", "hi")
    assert items is not None
    assert [item.value for item in items] == ["high", "xhigh"]
    assert complete("settings", "") is None
    assert complete("login", "") is None


def _harness(tmp_path: Path) -> _Harness:
    harness = _Harness(tmp_path)
    harness.component.replace_command_surface(CommandSurface(names=_NAMES))
    complete, _state = _completer(tmp_path / "state")
    harness.component.set_argument_completer(complete)
    return harness


def test_typing_after_a_command_opens_argument_completion(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    harness = _harness(tmp_path)
    harness.type_text("/thinking h")
    editor = harness.editor
    assert editor.slash_menu_open is False
    assert editor.autocomplete_open is True
    assert [item.value for item in editor.autocomplete_items] == ["high", "xhigh"]
    assert editor.autocomplete_prefix == "h"
    # Pi highlights the first value starting with the argument.
    assert editor.autocomplete_selection == 0
    harness.component.accept_selection()
    assert editor.text == "/thinking high"
    assert editor.autocomplete_open is False


def test_best_match_is_highlighted_after_every_edit(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    harness = _harness(tmp_path)
    harness.type_text("/thinking x")
    assert [item.value for item in harness.editor.autocomplete_items] == ["xhigh"]
    harness.editor.delete_before_cursor(harness.component.command_names)
    harness.component.refresh()
    # Empty argument: every level, highlight back on the first row.
    assert harness.editor.autocomplete_selection == 0
    assert len(harness.editor.autocomplete_items) > 1


def test_accepting_a_command_inserts_it_with_a_space(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    harness = _harness(tmp_path)
    harness.type_text("/thnk")
    assert harness.component.filtered_commands() == ("/thinking",)
    harness.component.accept_slash_menu_selection()
    assert harness.editor.text == "/thinking "
    assert harness.editor.effective_cursor() == len("/thinking ")


def test_argument_popup_rows_show_the_description(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    harness = _harness(tmp_path)
    harness.type_text("/model openai/gpt-4o-mini")
    lines = harness.component.popup_menu_frame_lines(width=80, max_rows=10)
    first = lines[0].text
    assert first.startswith("→ gpt-4o-mini")
    # Pi's default SelectList layout: the description at column 2 + 32.
    assert first[34:].startswith("openai")
