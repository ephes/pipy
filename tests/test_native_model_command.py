"""Pi ``/model`` in the TUI: exact reference match, else the search selector.

Pins pi-mono ``1b347794e`` ``interactive-mode.ts`` ``handleModelCommand`` /
``findExactModelMatch`` / ``showModelSelector``: ``/model <ref>`` switches on an
exact reference over the scoped (else available) models without saving the
default; otherwise the selector opens with the text as its search. Enter
switches for the session (``Model: <id>``), the save key also saves the
default (``Default model: <provider>/<id>``), and a failure is an
``Error: …`` line.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from test_native_coding_effects import _provider_mutation_fixture
from test_native_thinking_command import _Modals, _settings, _Transcript

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
)
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.repl.provider_config_commands import (
    ProviderConfigurationCommandEffects,
)
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.ui.components.search_selectors import ModelSearchSelector


def _model(argument: str) -> CodingCommandOutcome:
    return CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.MODEL,
        CodingCommandFooterPolicy.USAGE_AWARE,
        ProductContent(argument),
    )


def _fixture(
    tmp_path: Path,
    *,
    keys: list[str] | None = None,
    enabled_models: list[str] | None = None,
    with_tui: bool = True,
) -> tuple[
    ProviderConfigurationCommandEffects,
    NativeReplProviderState,
    SettingsManager,
    _Transcript,
    _Modals,
    io.StringIO,
]:
    effects, state, _tools, _ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(tmp_path, persist_defaults=True)
    )
    settings = _settings(tmp_path)
    if enabled_models is not None:
        settings.set_enabled_models(enabled_models)
    transcript = _Transcript()
    modals = _Modals(keys)
    terminal_ui = (
        SimpleNamespace(
            components=SimpleNamespace(transcript=transcript, modals=modals)
        )
        if with_tui
        else None
    )
    error_stream = io.StringIO()
    command = ProviderConfigurationCommandEffects(
        provider_state=state,
        clipboard_copy=cast(Any, None),
        ctl=effects.ctl,
        coding_state=effects.coding_state,
        terminal_ui=cast(Any, terminal_ui),
        error_stream=error_stream,
        keybindings=KeybindingsManager(),
        settings=settings,
        cwd=tmp_path,
        prompt_history_store=cast(Any, None),
        provider_mutation=effects,
    )
    return command, state, settings, transcript, modals, error_stream


def _selection(state: NativeReplProviderState) -> str:
    return state.current_selection().reference


def test_exact_reference_switches_without_saving_the_default(tmp_path: Path) -> None:
    command, state, _settings_value, transcript, modals, _err = _fixture(tmp_path)
    command.execute(_model("OpenAI/GPT-4o"))

    assert _selection(state) == "openai/gpt-4o"
    assert transcript.notices == ["Model: gpt-4o"]
    assert modals.selectors == []
    assert state.saved_default_selection() is None


def test_bare_model_id_matches_exactly(tmp_path: Path) -> None:
    command, state, _settings_value, transcript, _modals, _err = _fixture(tmp_path)
    command.execute(_model("gpt-4.1"))

    assert _selection(state) == "openai-completions/gpt-4.1"
    assert transcript.notices == ["Model: gpt-4.1"]


def test_ambiguous_bare_id_opens_the_selector(tmp_path: Path) -> None:
    # `gpt-4o` is offered by `openai` and `openai-completions`: Pi's
    # findExactModelReferenceMatch returns nothing for two id matches.
    command, state, _settings_value, _transcript, modals, _err = _fixture(
        tmp_path, keys=["esc"]
    )
    command.execute(_model("gpt-4o"))

    (selector,) = modals.selectors
    assert isinstance(selector, ModelSearchSelector)
    assert selector.query == "gpt-4o"
    assert _selection(state) == "openai/gpt-5.5"


def test_unavailable_or_partial_reference_opens_the_selector(tmp_path: Path) -> None:
    # `anthropic/...` has no auth: not available, so no exact match; Pi opens
    # the selector with the text as its search. Escape leaves the model.
    command, state, _settings_value, transcript, modals, _err = _fixture(
        tmp_path, keys=["esc"]
    )
    command.execute(_model("anthropic/claude-sonnet-4-5"))

    (selector,) = modals.selectors
    assert isinstance(selector, ModelSearchSelector)
    assert selector.query == "anthropic/claude-sonnet-4-5"
    assert _selection(state) == "openai/gpt-5.5"
    assert transcript.notices == []


def test_selector_lists_only_available_models(tmp_path: Path) -> None:
    command, state, _settings_value, _transcript, modals, _err = _fixture(
        tmp_path, keys=["esc"]
    )
    command.execute(_model(""))

    (selector,) = modals.selectors
    assert isinstance(selector, ModelSearchSelector)
    available = {spec.reference for spec in state.available_model_specs()}
    listed = {choice.reference for choice in selector.filtered()}
    # Available models the tool loop can run: the fake bootstrap (no tool
    # calls) would be refused, so it is not offered.
    assert listed == available - {"fake/fake-native-bootstrap"}
    assert "fake/fake-native-bootstrap" in available
    assert not any(ref.startswith("anthropic/") for ref in listed)
    # The current model comes first and is preselected.
    assert selector.filtered()[0].reference == "openai/gpt-5.5"


def test_selector_enter_switches_for_the_session(tmp_path: Path) -> None:
    command, state, _settings_value, transcript, _modals, _err = _fixture(
        tmp_path, keys=["enter"]
    )
    command.execute(_model("gpt-4o-min"))

    assert _selection(state) == "openai/gpt-4o-mini"
    assert transcript.notices == ["Model: gpt-4o-mini"]
    assert state.saved_default_selection() is None


def test_selector_save_key_saves_the_default(tmp_path: Path) -> None:
    command, state, _settings_value, transcript, _modals, _err = _fixture(
        tmp_path, keys=["ctrl-s"]
    )
    command.execute(_model("gpt-4o-min"))

    assert _selection(state) == "openai/gpt-4o-mini"
    assert transcript.notices == ["Default model: openai/gpt-4o-mini"]
    assert state.saved_default_selection() == NativeModelSelection(
        "openai", "gpt-4o-mini"
    )


def test_scoped_models_restrict_the_exact_match(tmp_path: Path) -> None:
    command, state, _settings_value, transcript, modals, _err = _fixture(
        tmp_path,
        keys=["esc"],
        enabled_models=["openai-completions/gpt-4.1", "openai/gpt-4o"],
    )
    command.execute(_model("gpt-4o-mini"))

    # Not in the scope: Pi opens the selector instead of switching.
    (selector,) = modals.selectors
    assert isinstance(selector, ModelSearchSelector)
    assert selector.scope == "scoped"
    assert _selection(state) == "openai/gpt-5.5"
    assert transcript.notices == []

    # `gpt-4o` is ambiguous over all available models but unique in the scope.
    command.execute(_model("gpt-4o"))
    assert _selection(state) == "openai/gpt-4o"


def test_scoped_selector_keeps_the_pattern_order(tmp_path: Path) -> None:
    command, _state, _settings_value, _transcript, modals, _err = _fixture(
        tmp_path,
        keys=["esc"],
        enabled_models=["openai/gpt-4o", "openai-completions/gpt-4.1"],
    )
    command.execute(_model(""))

    (selector,) = modals.selectors
    assert isinstance(selector, ModelSearchSelector)
    assert [choice.reference for choice in selector.filtered()] == [
        "openai/gpt-4o",
        "openai-completions/gpt-4.1",
    ]


def test_plain_repl_keeps_the_pipy_resolver(tmp_path: Path) -> None:
    command, state, _settings_value, _transcript, _modals, err = _fixture(
        tmp_path, with_tui=False
    )
    command.execute(_model("openai/gpt-4o"))

    assert _selection(state) == "openai/gpt-4o"
    assert "selected model openai/gpt-4o" in err.getvalue()
