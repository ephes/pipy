"""Pi ``/thinking`` command, selector and the shared thinking mutation path.

Pins the Pi `4df157433` behavior (``interactive-mode.ts``
``handleThinkingCommand``/``selectThinkingLevel``/``showThinkingSelector``,
``thinking-selector.ts``, ``AgentSession.setThinkingLevel``): a case-insensitive
exact match against the model's available levels, the unknown-level error text,
session-scoped selection, ``app.thinking.save`` persisting the default only after
the session change succeeds, and the durable ``thinking_level_change`` order.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_native_coding_effects import _provider_mutation_fixture

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
)
from pipy_harness.native.keybindings import DEFAULT_KEYBINDINGS, KeybindingsManager
from pipy_harness.native.overlay_state import ModelSelectorOption, OverlayState
from pipy_harness.native.repl.provider_config_commands import (
    ProviderConfigurationCommandEffects,
)
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.selector_actions import thinking_selector_rows
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.session_tree import NativeSessionTree, SessionEntry
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.ui.components.model_selector import (
    ModelSelectorClose,
    ModelSelectorComponent,
    model_selector_region_lines,
)
from pipy_harness.native.ui.key_specs import resolved_key_specs
from pipy_harness.native.ui.paint_lock import PaintLock


def _thinking_levels(tree: NativeSessionTree) -> list[str]:
    return [
        getattr(entry, "thinking_level", "")
        for entry in tree.get_entries()
        if getattr(entry, "type", "") == "thinking_level_change"
    ]


class _Transcript:
    def __init__(self) -> None:
        self.notices: list[str] = []

    def add_notice(self, message: str) -> None:
        self.notices.append(message)


class _Modals:
    def __init__(self, result: ModelSelectorClose | None) -> None:
        self.result = result
        self.calls: list[tuple[list[ModelSelectorOption], int]] = []

    def run_thinking_selector(
        self, options: list[ModelSelectorOption], *, current_index: int = 0
    ) -> ModelSelectorClose | None:
        self.calls.append((list(options), current_index))
        return self.result


def _settings(tmp_path: Path, *, read_only: bool = False) -> SettingsManager:
    global_path = tmp_path / "config" / "settings.json"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    settings = SettingsManager(global_path=global_path, env={})
    if read_only:

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("settings are read-only")

        settings.set_value = refuse  # type: ignore[method-assign]
    return settings


def _command_fixture(
    tmp_path: Path,
    *,
    selection: NativeModelSelection | None = None,
    selector_result: ModelSelectorClose | None = None,
    with_tui: bool = True,
    read_only_settings: bool = False,
) -> tuple[
    ProviderConfigurationCommandEffects,
    ProviderMutationEffects,
    NativeReplProviderState,
    NativeSessionTree,
    SettingsManager,
    _Transcript,
    _Modals,
    io.StringIO,
]:
    effects, state, _tools, _ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(tmp_path, selection=selection)
    )
    settings = _settings(tmp_path, read_only=read_only_settings)
    transcript = _Transcript()
    modals = _Modals(selector_result)
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
    return command, effects, state, tree, settings, transcript, modals, error_stream


def _thinking(argument: str) -> CodingCommandOutcome:
    return CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.THINKING,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(argument),
    )


def _saved_default(settings: SettingsManager) -> object:
    path = settings.global_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("defaultThinkingLevel")


class TestSharedThinkingMutation:
    def test_set_level_rebinds_provider_and_appends_once(self, tmp_path: Path) -> None:
        effects, state, _tools, _ref, _coordinator, tree, footers = (
            _provider_mutation_fixture(tmp_path)
        )
        before = effects.coding_state.provider
        result = effects.set_thinking_level("high")

        assert result.success and result.thinking_changed
        assert result.snapshot is not None
        assert result.snapshot.thinking_level == "high"
        assert state.current_thinking_level() == "high"
        assert effects.coding_state.provider is not before
        assert getattr(effects.coding_state.provider, "reasoning_effort") == "high"
        assert _thinking_levels(tree) == ["high"]
        assert footers == ["footer"]

        again = effects.set_thinking_level("high")
        assert again.success and not again.thinking_changed
        assert _thinking_levels(tree) == ["high"]

    def test_unavailable_level_is_rejected_without_effect(self, tmp_path: Path) -> None:
        effects, state, _tools, _ref, _coordinator, tree, _footers = (
            _provider_mutation_fixture(tmp_path)
        )
        before = effects.coding_state.provider
        result = effects.set_thinking_level("max")  # gpt-5.5 maps max to null

        assert not result.success
        assert state.current_thinking_level() is None
        assert effects.coding_state.provider is before
        assert _thinking_levels(tree) == []

    def test_delayed_append_keeps_commit_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The shared path appends after its commit gate. A second change that
        # commits while the first one's append is still pending must not make
        # the first operation persist the second level or reorder the two.
        effects, state, _tools, _ref, _coordinator, tree, _footers = (
            _provider_mutation_fixture(tmp_path, persist_tree=True)
        )
        first_committed = threading.Event()
        release_first = threading.Event()
        results: list[bool] = []

        def gated_commit(commit: Any) -> bool:
            commit()
            first_committed.set()
            assert release_first.wait(2)
            return True

        first = threading.Thread(
            target=lambda: results.append(
                effects._set_thinking_level("low", gated_commit).success
            )
        )
        first.start()
        assert first_committed.wait(2)
        second = effects.set_thinking_level("high")
        release_first.set()
        first.join(2)

        assert results == [True] and second.success
        assert state.current_thinking_level() == "high"
        assert _thinking_levels(tree) == ["low", "high"]
        assert tree.path is not None
        durable = [
            json.loads(line)["thinkingLevel"]
            for line in tree.path.read_text().splitlines()[1:]
        ]
        assert durable == ["low", "high"]
        assert effects.pending_session_appends == []

    def test_pending_level_is_appended_before_a_model_clamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        effects, state, _tools, _ref, _coordinator, tree, _footers = (
            _provider_mutation_fixture(tmp_path)
        )

        def commit_without_drain(commit: Any) -> bool:
            commit()
            return True

        # Commit "xhigh" but leave its durable append pending, as if its
        # caller were still between the gate and the drain.
        with monkeypatch.context() as patch:
            patch.setattr(
                ProviderMutationEffects, "_drain_session_appends", lambda _self: []
            )
            assert effects._set_thinking_level("xhigh", commit_without_drain).success
        assert effects.pending_session_appends == ["xhigh"]
        assert _thinking_levels(tree) == []

        # An RPC model switch to a row without xhigh clamps the level; its own
        # transition is queued behind the pending one and drained in order.
        port = effects.rpc_configuration_port(commit_without_drain)
        result = port.set_model(NativeModelSelection("openai", "gpt-5.1-codex"))
        assert result.success
        levels = _thinking_levels(tree)
        assert levels[0] == "xhigh"
        assert levels[1:] == [state.current_thinking_level() or "off"]
        assert effects.pending_session_appends == []


class TestThinkingCommand:
    def test_argument_matches_available_levels_case_insensitively(
        self, tmp_path: Path
    ) -> None:
        command, effects, state, tree, settings, transcript, modals, _err = (
            _command_fixture(tmp_path)
        )
        command.execute(_thinking("HIGH"))

        assert state.current_thinking_level() == "high"
        assert getattr(effects.coding_state.provider, "reasoning_effort") == "high"
        assert transcript.notices == ["Thinking level: high"]
        assert _thinking_levels(tree) == ["high"]
        assert modals.calls == []
        assert _saved_default(settings) is None

    def test_unknown_level_lists_available_levels(self, tmp_path: Path) -> None:
        command, _effects, state, tree, _settings_value, transcript, _modals, _err = (
            _command_fixture(tmp_path)
        )
        command.execute(_thinking("max"))

        assert transcript.notices == [
            'pipy: Unknown thinking level "max". '
            "Available levels: off, low, medium, high, xhigh."
        ]
        assert state.current_thinking_level() is None
        assert _thinking_levels(tree) == []

    def test_non_reasoning_model_offers_only_off(self, tmp_path: Path) -> None:
        command, _effects, _state, _tree, _settings_value, transcript, _modals, _err = (
            _command_fixture(
                tmp_path, selection=NativeModelSelection("openai", "gpt-4o")
            )
        )
        command.execute(_thinking("low"))

        assert transcript.notices == [
            'pipy: Unknown thinking level "low". Available levels: off.'
        ]

    def test_selector_enter_is_session_scoped(self, tmp_path: Path) -> None:
        command, _effects, state, tree, settings, transcript, modals, _err = (
            _command_fixture(tmp_path, selector_result=ModelSelectorClose(3))
        )
        state.assign_thinking_level("medium")
        command.execute(_thinking(""))

        options, current_index = modals.calls[0]
        assert [option.label.split()[-1] for option in options][:1] == ["reasoning"]
        assert current_index == 2  # off, low, medium
        assert state.current_thinking_level() == "high"
        assert transcript.notices == ["Thinking level: high"]
        assert _thinking_levels(tree) == ["high"]
        assert _saved_default(settings) is None

    def test_selector_save_key_persists_default_after_applying(
        self, tmp_path: Path
    ) -> None:
        command, _effects, state, _tree, settings, transcript, _modals, _err = (
            _command_fixture(tmp_path, selector_result=ModelSelectorClose(1, save=True))
        )
        command.execute(_thinking(""))

        assert state.current_thinking_level() == "low"
        assert _saved_default(settings) == "low"
        assert settings.get_default_thinking_level() == "low"
        assert transcript.notices == ["Default thinking level: low"]

    def test_selector_cancel_changes_nothing(self, tmp_path: Path) -> None:
        command, effects, state, tree, settings, transcript, _modals, _err = (
            _command_fixture(tmp_path, selector_result=None)
        )
        before = effects.coding_state.provider
        command.execute(_thinking(""))

        assert state.current_thinking_level() is None
        assert effects.coding_state.provider is before
        assert _thinking_levels(tree) == []
        assert transcript.notices == []
        assert _saved_default(settings) is None

    def test_failed_session_change_does_not_persist_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command, effects, state, _tree, settings, transcript, _modals, _err = (
            _command_fixture(tmp_path, selector_result=ModelSelectorClose(1, save=True))
        )

        def refuse(_commit: Any) -> bool:
            return False

        monkeypatch.setattr(
            ProviderMutationEffects,
            "set_thinking_level",
            lambda self, level: self._set_thinking_level(level, refuse),
        )
        command.execute(_thinking(""))

        assert state.current_thinking_level() is None
        assert _saved_default(settings) is None
        assert transcript.notices == [
            "pipy: thinking level unchanged (session is not idle)."
        ]

    def test_read_only_settings_still_apply_the_session_level(
        self, tmp_path: Path
    ) -> None:
        command, _effects, state, _tree, _settings_value, transcript, _modals, _err = (
            _command_fixture(
                tmp_path,
                selector_result=ModelSelectorClose(1, save=True),
                read_only_settings=True,
            )
        )
        command.execute(_thinking(""))

        assert state.current_thinking_level() == "low"
        assert transcript.notices == [
            "pipy: thinking level: low (could not save default: settings are read-only)"
        ]

    def test_bare_command_without_tui_lists_levels(self, tmp_path: Path) -> None:
        command, _effects, state, _tree, _settings_value, _transcript, _modals, err = (
            _command_fixture(tmp_path, with_tui=False)
        )
        state.assign_thinking_level("low")
        command.execute(_thinking(""))

        assert err.getvalue() == (
            "pipy: thinking level: low (available: off, low, medium, high, xhigh)\n"
        )


class TestThinkingSelector:
    def test_rows_follow_pi_thinking_selector(self) -> None:
        rows, index = thinking_selector_rows(
            ["off", "low", "medium", "high"],
            current_level="high",
            default_level="medium",
        )
        assert [row.label for row in rows] == [
            "  off        No reasoning",
            "  low        Light reasoning (~2k tokens)",
            "  medium     Moderate reasoning (~8k tokens) · default",
            "✓ high       Deep reasoning (~16k tokens)",
        ]
        assert all(row.selectable for row in rows)
        assert index == 3

    def test_unset_level_preselects_off(self) -> None:
        _rows, index = thinking_selector_rows(
            ["off", "low"], current_level=None, default_level="medium"
        )
        assert index == 0

    def _component(
        self, save_keys: list[str]
    ) -> tuple[ModelSelectorComponent, OverlayState]:
        overlays = OverlayState()
        component = ModelSelectorComponent(
            overlays, PaintLock(RLock()), lambda: None, save_keys=save_keys
        )
        options = [
            ModelSelectorOption(label="  off", selectable=True),
            ModelSelectorOption(label="✓ low", selectable=True),
        ]
        assert component.open(
            options, current_index=1, title="Thinking Level", hint="custom hint"
        )
        return component, overlays

    def test_save_key_closes_with_save(self) -> None:
        component, overlays = self._component(["ctrl+s"])
        lines = model_selector_region_lines(
            overlays, width=80, height=10, footer_lines=("a", "b")
        )
        assert lines[0].text == " Thinking Level — custom hint"
        assert component.handle_key("up") is None
        assert component.handle_key("ctrl-s") == ModelSelectorClose(0, save=True)
        assert overlays.model_hint is None

    def test_enter_and_escape(self) -> None:
        component, _overlays = self._component(["ctrl+s"])
        assert component.handle_key("enter") == ModelSelectorClose(1, save=False)
        component, _overlays = self._component(["ctrl+s"])
        assert component.handle_key("esc") == ModelSelectorClose(None)

    def test_model_selector_without_save_keys_ignores_ctrl_s(self) -> None:
        component, _overlays = self._component([])
        assert component.handle_key("ctrl-s") is None

    def test_save_binding_wins_over_enter_and_escape(self) -> None:
        component, _overlays = self._component(["enter"])
        assert component.handle_key("enter") == ModelSelectorClose(1, save=True)
        component, _overlays = self._component(["escape"])
        assert component.handle_key("esc") == ModelSelectorClose(1, save=True)

    def test_save_key_follows_user_keybindings(self) -> None:
        assert DEFAULT_KEYBINDINGS["app.thinking.save"].default_keys == ["ctrl+s"]
        assert resolved_key_specs("app.thinking.save", None) == ["ctrl+s"]
        remapped = KeybindingsManager({"app.thinking.save": "ctrl+x"})
        specs = resolved_key_specs("app.thinking.save", remapped)
        assert specs == ["ctrl+x"]
        component, _overlays = self._component(specs)
        assert component.handle_key("ctrl-s") is None
        assert component.handle_key("ctrl-x") == ModelSelectorClose(1, save=True)


def test_session_entries_are_the_imported_type() -> None:
    # Guard the helper above against silently matching nothing.
    assert SessionEntry is not None
