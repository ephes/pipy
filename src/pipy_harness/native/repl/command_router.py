"""Closed built-in command routing for one coding session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
)
from pipy_harness.native.repl.provider_config_commands import (
    ProviderConfigurationCommandEffects,
)
from pipy_harness.native.repl.reload import ReloadCommandEffects
from pipy_harness.native.repl.session_commands import SessionCommandEffects
from pipy_harness.native.repl.session_transfer import TransferCommandEffects

_SESSION_COMMAND_ACTIONS = frozenset(
    {
        CodingCommandAction.SHOW_SESSION_STATUS,
        CodingCommandAction.COMPACT,
        CodingCommandAction.SESSION_NAME,
        CodingCommandAction.NEW_SESSION,
        CodingCommandAction.SESSION_TREE,
        CodingCommandAction.SESSION_RESUME,
        CodingCommandAction.SESSION_FORK,
        CodingCommandAction.SESSION_CLONE,
    }
)

_PROVIDER_CONFIGURATION_COMMAND_ACTIONS = frozenset(
    {
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandAction.SHOW_CHANGELOG,
        CodingCommandAction.COPY_LAST_ANSWER,
        CodingCommandAction.SETTINGS,
        CodingCommandAction.TRUST_PROJECT,
        CodingCommandAction.MODEL,
        CodingCommandAction.SCOPED_MODELS,
        CodingCommandAction.LOGIN,
        CodingCommandAction.LOGOUT,
    }
)

_TRANSFER_COMMAND_ACTIONS = frozenset(
    {
        CodingCommandAction.SESSION_EXPORT,
        CodingCommandAction.SESSION_IMPORT,
        CodingCommandAction.SESSION_SHARE,
    }
)

_RELOAD_COMMAND_ACTIONS = frozenset({CodingCommandAction.RELOAD})


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinCommandInterpreter:
    """Composition-root handler that owns the built-in command effect chain.

    The controller classifies the built-in>resource>extension precedence and, for
    a continuing built-in, invokes this handler through the already-wired
    :meth:`CodingCommandEffects.interpret_builtin` port (symmetric with the
    resource and extension dispatch ports). :meth:`interpret` receives the run's
    four closed effect-family ports. Footer policy stays here so every family
    retains the same success/exception timing.
    """

    session_effects: SessionCommandEffects
    provider_configuration_effects: ProviderConfigurationCommandEffects
    transfer_effects: TransferCommandEffects
    reload_effects: ReloadCommandEffects
    refresh_legacy_footer: Callable[[], None]
    refresh_legacy_footer_with_usage: Callable[[], None]

    def interpret(
        self,
        command_outcome: CodingCommandOutcome,
    ) -> None:
        if command_outcome.kind is not CodingCommandOutcomeKind.CONTINUE:
            return
        action = command_outcome.action
        if action in _PROVIDER_CONFIGURATION_COMMAND_ACTIONS:
            self.provider_configuration_effects.execute(command_outcome)
        elif action in _SESSION_COMMAND_ACTIONS:
            self.session_effects.execute(command_outcome)
        elif action in _TRANSFER_COMMAND_ACTIONS:
            self.transfer_effects.execute(command_outcome)
        elif action in _RELOAD_COMMAND_ACTIONS:
            self.reload_effects.execute(command_outcome)
        elif action is None:
            # Empty input is a classified continuing local no-op whose footer
            # still refreshes through the same closed policy below.
            pass
        else:
            raise AssertionError("continuing built-in has no effect-family owner")
        if command_outcome.footer_policy is CodingCommandFooterPolicy.STANDARD:
            self.refresh_legacy_footer()
        elif command_outcome.footer_policy is CodingCommandFooterPolicy.USAGE_AWARE:
            self.refresh_legacy_footer_with_usage()
        else:
            raise AssertionError("handled command requires a closed footer policy")
