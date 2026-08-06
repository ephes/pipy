"""Hotkey actions that change only what the operator sees.

Pi's Ctrl+O, Ctrl+T and Shift+Tab. None of these runs a provider turn: they flip
renderer view flags, persist one non-secret setting, or move the reasoning level
that the *next* turn will use. That is the whole reason they can live outside the
session -- they touch view state and the provider's advertised level, never the
conversation.

Free functions rather than methods, because none of them needed the session for
anything except reaching the terminal and the settings store, both of which are
already parameters.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.repl_state import NativeReplProviderState
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.custom_editor import HOTKEY_TOGGLE_TOOLS


def toggle_view_fold(
    hotkey: str,
    *,
    terminal_ui: TerminalUi | None,
    error_stream: TextIO,
    settings: "SettingsManager",
) -> None:
    """Toggle a renderer view fold (Pi Ctrl+O tool output / Ctrl+T thinking).

    Ctrl+O flips tool-output expansion (a pure live-render view flag); Ctrl+T
    flips thinking-block visibility and persists it to the non-secret
    settings store. Both run no provider turn and only mutate renderer view
    state (plus, for thinking, the settings file). A status is shown.
    """

    if hotkey == HOTKEY_TOGGLE_TOOLS:
        new_value = not (
            terminal_ui.components.transcript.tools_expanded if terminal_ui else False
        )
        if terminal_ui is not None:
            # The transcript owner's verb bundles the retained rich-row
            # rerender with the flag write.
            terminal_ui.components.transcript.set_tools_expanded(new_value)
        label = "expanded" if new_value else "collapsed"
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            f"pipy: tool output: {label}",
        )
        return
    # HOTKEY_TOGGLE_THINKING
    current = (
        terminal_ui.components.transcript.thinking_hidden
        if terminal_ui is not None
        else settings.get_hide_thinking_block()
    )
    new_hidden = not current
    if terminal_ui is not None:
        # Route through set_thinking_hidden so unfolding reveals any
        # reasoning that settled while folded (deferred, not dropped).
        terminal_ui.components.transcript.set_thinking_hidden(new_hidden)
    try:
        settings.set_value("hideThinkingBlock", new_hidden)
    except RuntimeError:
        # A read-only/locked settings file must not break the live toggle.
        pass
    label = "hidden" if new_hidden else "visible"
    emit_diagnostic(
        terminal_ui.components.transcript if terminal_ui is not None else None,
        error_stream,
        f"pipy: thinking blocks: {label}",
    )


def cycle_thinking_level_action(
    provider_state: object,
    *,
    terminal_ui: TerminalUi | None,
    error_stream: TextIO,
    cycle_thinking_level: Callable[[], str | None],
) -> None:
    """Cycle the reasoning level (Pi's Shift+Tab ``cycleThinkingLevel``).

    Cycles off→minimal→low→medium→high (wrapping), clamped to whether the
    active model advertises reasoning support, sets the runtime level on the
    provider state (so the footer effort label reflects it), appends a
    ``thinking_level_change`` native-tree entry, and shows a status. Runs no
    provider turn; the new level applies to the next turn.
    """

    state = provider_state
    if not isinstance(state, NativeReplProviderState):
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            "pipy: thinking-level cycling is unavailable for this REPL state.",
        )
        return
    next_level = cycle_thinking_level()
    if next_level is None:
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            "pipy: current model does not support thinking.",
        )
        return
    emit_diagnostic(
        terminal_ui.components.transcript if terminal_ui is not None else None,
        error_stream,
        f"pipy: thinking level: {next_level}",
    )
