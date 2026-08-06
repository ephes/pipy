"""Provider, model, auth and view configuration: the commands that change setup.

`/model`, `/scoped-models`, `/login`, `/logout`, `/settings`, `/trust`,
`/hotkeys`, `/changelog`, `/copy`. What unites them is what they do *not* do:
none runs a provider or tool turn. They change what the next turn will use, or
they report the current setup, and then the loop continues.

`/copy` is the odd one out only in appearance -- it reads the in-memory
conversation and writes to the OS clipboard through an injected path, which is
still a local operation with no turn behind it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pipy_harness.native.agent import AgentMessage, ProductContent
from pipy_harness.native.changelog import read_changelog_entries, render_changelog
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandOutcome,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.diagnostics import emit_diagnostic, last_assistant_answer
from pipy_harness.native.keybindings import KeybindingsManager, render_hotkeys
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.selector_actions import (
    handle_trust_command,
    model_selector_rows,
    open_scoped_models_overlay,
)
from pipy_harness.native.repl.settings_actions import (
    drive_settings_dialog,
    tool_loop_settings_overlay_lines,
)
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.scoped_models import filter_scoped_references, next_reference
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tui import TerminalUi


def copy_last_answer(
    messages: Sequence[AgentMessage],
    *,
    error_stream: TextIO,
    clipboard_copy: Callable[..., ClipboardResult],
) -> str:
    """Copy the most recent assistant answer; return a local status line.

    This is a purely local operation: it reads the in-memory conversation,
    copies through the injected clipboard path, and reports what happened.
    It never invokes the provider, tools, login/logout, or model switching.
    """

    answer = last_assistant_answer(messages)
    if not answer:
        return "pipy: nothing to copy yet (no assistant answer in this session)."
    result = clipboard_copy(answer, terminal_stream=error_stream)
    if result.copied:
        return f"pipy: copied last answer to clipboard ({result.detail})."
    return f"pipy: could not copy last answer — {result.detail}."


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderConfigurationCommandEffects:
    """Execute provider/configuration built-ins without a provider or tool turn.

    Presentation stays split between the live terminal and captured diagnostic
    stream. Model and authentication mutations compose with the single
    :class:`ProviderMutationEffects` owner so context clearing, provider/usage
    rebinding, footer refresh, and persistence ordering are not duplicated here.
    """

    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    clipboard_copy: Callable[..., ClipboardResult]
    ctl: RunControlState
    coding_state: CodingSessionState
    terminal_ui: TerminalUi | None
    error_stream: TextIO
    keybindings: KeybindingsManager
    settings: SettingsManager
    cwd: Path
    prompt_history_store: PromptHistoryStore
    provider_mutation: ProviderMutationEffects

    def execute(self, command_outcome: CodingCommandOutcome) -> None:
        handlers: dict[CodingCommandAction, Callable[[CodingCommandOutcome], None]] = {
            CodingCommandAction.SHOW_HOTKEYS: self._show_hotkeys,
            CodingCommandAction.SHOW_CHANGELOG: self._show_changelog,
            CodingCommandAction.COPY_LAST_ANSWER: self._copy_last_answer,
            CodingCommandAction.SETTINGS: self._settings,
            CodingCommandAction.TRUST_PROJECT: self._trust_project,
            CodingCommandAction.MODEL: self._model,
            CodingCommandAction.SCOPED_MODELS: self._scoped_models,
            CodingCommandAction.LOGIN: self._auth,
            CodingCommandAction.LOGOUT: self._auth,
        }
        action = command_outcome.action
        if action not in handlers:
            raise AssertionError(
                "provider/configuration executor received wrong action"
            )
        handlers[action](command_outcome)

    def _show_hotkeys(self, _command_outcome: CodingCommandOutcome) -> None:
        # Render from the resolved keybinding manager so user
        # keybindings.json overrides remain reflected.
        hotkeys_text = render_hotkeys(self.keybindings)
        if self.terminal_ui is not None:
            self.terminal_ui.components.transcript.add_notice(hotkeys_text)
        else:
            print(hotkeys_text, file=self.error_stream)

    def _show_changelog(self, _command_outcome: CodingCommandOutcome) -> None:
        changelog_text = render_changelog(read_changelog_entries())
        if self.terminal_ui is not None:
            self.terminal_ui.components.transcript.add_notice(changelog_text)
        else:
            print(changelog_text, file=self.error_stream)

    def _copy_last_answer(self, _command_outcome: CodingCommandOutcome) -> None:
        emit_diagnostic(
            self.terminal_ui.components.transcript
            if self.terminal_ui is not None
            else None,
            self.error_stream,
            copy_last_answer(
                self.coding_state.messages,
                error_stream=self.error_stream,
                clipboard_copy=self.clipboard_copy,
            ),
        )

    def _settings(self, _command_outcome: CodingCommandOutcome) -> None:
        if self.terminal_ui is not None:
            drive_settings_dialog(
                self.terminal_ui,
                self.prompt_history_store,
                provider=self.coding_state.provider,
                provider_state=self.provider_state,
                apply_model_selection=self.provider_mutation.apply_model_selection,
                apply_auth_change=self.provider_mutation.apply_auth_change,
                cycle_thinking_level=self.provider_mutation.cycle_thinking_level,
                settings=self.settings,
                error_stream=self.error_stream,
            )
        else:
            for overlay_line in tool_loop_settings_overlay_lines(
                self.settings,
                provider=self.coding_state.provider,
                provider_state=self.provider_state,
            ):
                print(overlay_line, file=self.error_stream)

    def _trust_project(self, _command_outcome: CodingCommandOutcome) -> None:
        handle_trust_command(
            terminal_ui=self.terminal_ui,
            error_stream=self.error_stream,
            cwd=self.cwd,
            settings=self.settings,
        )

    @staticmethod
    def _argument(command_outcome: CodingCommandOutcome) -> str:
        command_argument = command_outcome.argument
        if type(command_argument) is not ProductContent:
            action = command_outcome.action
            if action is None:
                raise AssertionError(
                    "provider/configuration command requires a concrete action"
                )
            raise TypeError(f"{action.name} requires an exact ProductContent argument")
        return command_argument.value

    def _model(self, command_outcome: CodingCommandOutcome) -> None:
        argument = self._argument(command_outcome)
        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                "pipy: /model is unavailable for this REPL provider state.",
            )
        elif argument:
            _ok, message = self.provider_mutation.apply_model_selection(argument)
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                message,
            )
        elif self.terminal_ui is not None:
            ui_options, selections = model_selector_rows(state)
            current = state.current_selection()
            current_index = next(
                (
                    index
                    for index, selection in enumerate(selections)
                    if selection.provider_name == current.provider_name
                    and selection.model_id == current.model_id
                ),
                0,
            )
            chosen = self.terminal_ui.components.modals.run_model_selector(
                ui_options, current_index=current_index
            )
            if chosen is not None:
                _ok, message = self.provider_mutation.apply_model_selection(
                    selections[chosen].reference
                )
                self.terminal_ui.components.transcript.add_notice(message)
        else:
            for overlay_line in tool_loop_settings_overlay_lines(
                self.settings,
                provider=self.coding_state.provider,
                provider_state=self.provider_state,
            ):
                print(overlay_line, file=self.error_stream)

    def _scoped_models(self, command_outcome: CodingCommandOutcome) -> None:
        # Local-only: view/set/clear the enabledModels patterns constraining
        # model cycling, or cycle over the scoped set without a provider/tool turn.
        argument = self._argument(command_outcome)
        state = self.provider_state
        available_refs = (
            [
                option.selection.reference
                for option in state.model_options()
                if option.available
            ]
            if isinstance(state, NativeReplProviderState)
            else []
        )
        patterns = self.settings.get_enabled_models()
        scoped = filter_scoped_references(available_refs, patterns)
        if (
            not argument
            and self.terminal_ui is not None
            and isinstance(state, NativeReplProviderState)
            and available_refs
        ):
            open_scoped_models_overlay(
                self.terminal_ui, state=state, settings=self.settings
            )
        elif not argument:
            pattern_text = ", ".join(patterns) if patterns else "(none — full catalog)"
            cycle_text = ", ".join(scoped) if scoped else "(none available)"
            for self.ctl.line in (
                "pipy: scoped models:",
                f"  patterns: {pattern_text}",
                f"  cycle set: {cycle_text}",
            ):
                emit_diagnostic(
                    self.terminal_ui.components.transcript
                    if self.terminal_ui is not None
                    else None,
                    self.error_stream,
                    self.ctl.line,
                )
        elif argument == "clear":
            try:
                self.settings.set_enabled_models([])
                message = "pipy: scoped models cleared (cycle uses the full catalog)."
            except RuntimeError as exc:
                message = f"pipy: could not update scoped models: {exc}"
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                message,
            )
        elif argument in {"next", "prev"}:
            current_ref = (
                state.current_selection().reference
                if isinstance(state, NativeReplProviderState)
                else ""
            )
            cycle_target = next_reference(
                scoped,
                current_ref,
                forward=argument == "next",
            )
            if cycle_target is None:
                emit_diagnostic(
                    self.terminal_ui.components.transcript
                    if self.terminal_ui is not None
                    else None,
                    self.error_stream,
                    "pipy: no models available to cycle.",
                )
            else:
                _ok, message = self.provider_mutation.apply_model_selection(
                    cycle_target
                )
                emit_diagnostic(
                    self.terminal_ui.components.transcript
                    if self.terminal_ui is not None
                    else None,
                    self.error_stream,
                    message,
                )
        else:
            new_patterns = argument.split()
            try:
                self.settings.set_enabled_models(new_patterns)
                message = "pipy: scoped models set: " + ", ".join(new_patterns)
            except RuntimeError as exc:
                message = f"pipy: could not update scoped models: {exc}"
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                message,
            )

    def _auth(self, command_outcome: CodingCommandOutcome) -> None:
        argument = self._argument(command_outcome)
        auth_action = (
            "login" if command_outcome.action is CodingCommandAction.LOGIN else "logout"
        )
        message = self.provider_mutation.apply_auth_change(auth_action, argument)
        emit_diagnostic(
            self.terminal_ui.components.transcript
            if self.terminal_ui is not None
            else None,
            self.error_stream,
            message,
        )
