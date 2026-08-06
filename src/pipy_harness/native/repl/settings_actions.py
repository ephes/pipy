"""The `/settings` dialog: everything adjustable without leaving the session.

One dialog, two kinds of action. Local ones -- prompt-history on/off, clearing
persisted history, folding tool or thinking output, cycling the reasoning level
-- are applied in place and the dialog stays open with rebuilt rows. The rest
need the terminal for themselves (a selector overlay or an OAuth flow), so they
close the dialog, run, and the caller re-opens it.

Nothing here runs a provider or tool turn. That is why `/settings` is safe to
open mid-conversation: the worst it does is change what the *next* turn uses.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.selector_actions import (
    model_selector_rows,
    open_default_project_trust_selector,
    open_scoped_models_overlay,
)
from pipy_harness.native.repl.view_actions import (
    cycle_thinking_level_action,
    toggle_view_fold,
)
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
    settings_overlay_lines,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.themes import (
    NativeThemeStore,
    available_theme_names,
    resolve_active_theme_name,
    select_theme,
)
from pipy_harness.native.tui import (
    ModelSelectorOption,
    SettingsRow,
    TerminalUi,
)
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
)


def tool_loop_settings_overlay_lines(
    settings_manager: "SettingsManager | None" = None,
    *,
    provider: ProviderPort,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
) -> list[str]:
    """Build the read-only settings/status overlay content.

    Reuses the shared no-tool ``/settings`` builder so the tool-loop TUI
    shows the same safe provider/model/status information and availability
    reasons, then appends a footer honest for the tool-loop surface (where
    ``/model``, ``/login``, and ``/logout`` are all executable). When no
    provider state is wired, a single-provider static view is shown and the
    footer says those commands are unavailable for that state.
    """

    state = provider_state or StaticNativeReplProviderState(provider)
    lines = settings_overlay_lines(state, settings_manager)
    if isinstance(state, NativeReplProviderState):
        lines.append(
            "  read-only view; use /model to switch provider/model and "
            "/login or /logout to manage openai-codex OAuth."
        )
    else:
        lines.append(
            "  read-only view; /model, /login, and /logout are not "
            "available for this REPL provider state."
        )
    return lines


def open_theme_selector(
    terminal_ui: TerminalUi,
    *,
    settings: "SettingsManager",
) -> None:
    """Open the theme picker and apply + persist the chosen chrome theme.

    Mirrors the ``action == "model"`` path: it builds one selectable row per
    registered theme (the active theme starts highlighted), opens the shared
    label/selectable selector with a theme-specific heading, and on a choice
    applies the theme via ``select_theme`` (which sets ``PIPY_THEME`` so the
    next rendered frame repaints and persists the non-secret name to the
    chrome store) and persists it through ``settings`` — the source of truth
    a later ``/reload`` re-reads. Runs no provider turn, tool call, or
    archive write; ``Esc`` leaves the theme unchanged.
    """

    names = available_theme_names()
    if not names:
        terminal_ui.add_notice("pipy: no themes available to select.")
        return
    active = resolve_active_theme_name(env=os.environ, store=NativeThemeStore())
    options = [
        ModelSelectorOption(
            label=f"{name} (active)" if name == active else name,
            selectable=True,
        )
        for name in names
    ]
    current_index = next(
        (index for index, name in enumerate(names) if name == active), 0
    )
    chosen = terminal_ui.run_model_selector(
        options, current_index=current_index, title="Select theme"
    )
    if chosen is None:
        return
    name = names[chosen]
    ok, message = select_theme(name, environ=os.environ, store=NativeThemeStore())
    if ok:
        # Settings is the source of truth (a later /reload re-applies
        # settings.get_theme() over the chrome store), so persist the choice
        # there too. A write failure keeps the live selection.
        try:
            settings.set_theme(name)
        except (OSError, RuntimeError):
            pass
    terminal_ui.add_notice(message)


def settings_dialog_rows(
    state: "NativeReplProviderState | StaticNativeReplProviderState",
    prompt_history_store: PromptHistoryStore,
    *,
    in_memory_depth: int,
    terminal_ui: TerminalUi | None = None,
    settings: "SettingsManager | None" = None,
) -> list[SettingsRow]:
    """Build the interactive ``/settings`` dialog rows.

    Strictly local/read-only construction: it probes the current
    selection, openai-codex auth availability, and prompt-history state but
    runs no provider turn, tool call, or auth/model mutation. Actionable
    rows carry an identifier the dialog hands back when activated; headers
    and read-only status rows stay visible for context but are not
    choosable.
    """

    current = state.current_selection()
    rows: list[SettingsRow] = [
        SettingsRow(label="Provider / model", kind="header"),
        SettingsRow(label=f"active: {sanitize_text(current.reference)}", kind="status"),
    ]
    if isinstance(state, NativeReplProviderState):
        rows.append(
            SettingsRow(label="change provider/model…", kind="action", action="model")
        )
        rows.append(SettingsRow(label="Authentication", kind="header"))
        if state.provider_available("openai-codex"):
            rows.append(
                SettingsRow(
                    label="openai-codex: logged in — log out",
                    kind="action",
                    action="logout",
                )
            )
        else:
            rows.append(
                SettingsRow(
                    label="openai-codex: logged out — log in",
                    kind="action",
                    action="login",
                )
            )
    rows.append(SettingsRow(label="Prompt history", kind="header"))
    enabled = prompt_history_store.enabled
    rows.append(
        SettingsRow(
            label=(f"persistent prompt history: {'on' if enabled else 'off'} — toggle"),
            kind="action",
            action="toggle_history",
        )
    )
    rows.append(
        SettingsRow(
            label=(
                f"clear persisted history ({len(prompt_history_store.entries())} saved)"
            ),
            kind="action",
            action="clear_history",
        )
    )
    rows.append(
        SettingsRow(
            label=f"in-memory recall this session: {in_memory_depth} prompts",
            kind="status",
        )
    )
    # Display / folding view flags and the thinking-level cycle (Ctrl+O /
    # Ctrl+T / Shift+Tab also drive these). Only meaningful with a live TUI.
    if terminal_ui is not None:
        rows.append(SettingsRow(label="Display", kind="header"))
        rows.append(
            SettingsRow(
                label=(
                    "tool output: "
                    f"{'expanded' if terminal_ui.tools_expanded else 'collapsed'}"
                    " — toggle (ctrl+o)"
                ),
                kind="action",
                action="toggle_tools",
            )
        )
        rows.append(
            SettingsRow(
                label=(
                    "thinking blocks: "
                    f"{'hidden' if terminal_ui.thinking_hidden else 'visible'}"
                    " — toggle (ctrl+t)"
                ),
                kind="action",
                action="toggle_thinking",
            )
        )
        level = (
            state.current_thinking_level()
            if isinstance(state, NativeReplProviderState)
            else None
        ) or "off"
        rows.append(
            SettingsRow(
                label=f"thinking level: {level} — cycle (shift+tab)",
                kind="action",
                action="cycle_thinking",
            )
        )
        active_theme = resolve_active_theme_name(
            env=os.environ, store=NativeThemeStore()
        )
        rows.append(
            SettingsRow(
                label=f"theme: {active_theme} — change…",
                kind="action",
                action="theme",
            )
        )
    if isinstance(state, NativeReplProviderState):
        rows.append(SettingsRow(label="Model cycle", kind="header"))
        rows.append(
            SettingsRow(
                label="scoped models (Ctrl+P cycle set)…",
                kind="action",
                action="scoped_models",
            )
        )
    rows.append(SettingsRow(label="Project trust", kind="header"))
    trust_labels = {
        "ask": "Ask",
        "always": "Trust",
        "never": "Do not trust",
    }
    trust_default = (
        settings.get_default_project_trust() if settings is not None else "ask"
    )
    rows.append(
        SettingsRow(
            label=(f"default project trust: {trust_labels[trust_default]} — change…"),
            kind="action",
            action="project_trust_default",
        )
    )
    rows.append(SettingsRow(label="Providers (read-only)", kind="header"))
    for option in state.model_options():
        availability = (
            "available"
            if option.available
            else f"unavailable ({option.reason or 'unknown'})"
        )
        rows.append(
            SettingsRow(
                label=(f"{sanitize_text(option.selection.reference)} [{availability}]"),
                kind="status",
            )
        )
    return rows


def drive_settings_dialog(
    terminal_ui: TerminalUi,
    prompt_history_store: PromptHistoryStore,
    *,
    provider: ProviderPort,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    apply_model_selection: Callable[[str], tuple[bool, str]],
    apply_auth_change: Callable[[str, str], str],
    cycle_thinking_level: Callable[[], str | None],
    settings: "SettingsManager",
    error_stream: TextIO,
) -> None:
    """Open the live ``/settings`` dialog and act on the user's choices.

    Local toggles (persistent prompt-history on/off, clear persisted
    history) are handled in place by the dialog without leaving it.
    Provider/model and auth actions reuse the existing
    ``NativeReplProviderState`` boundaries (``apply_model_selection`` /
    ``apply_auth_change``) and run **no** provider or tool turn; afterward
    the dialog re-opens so the user can keep adjusting settings. The dialog
    closes on Esc/Ctrl-C/Ctrl-D.
    """

    state = provider_state or StaticNativeReplProviderState(provider)
    is_native = isinstance(state, NativeReplProviderState)
    # Actions that need the terminal themselves (an interactive selector or
    # auth flow) close the dialog and are returned for the caller's
    # post-return branch to drive; everything else is handled locally by
    # ``on_local_action`` while the dialog stays open. The theme picker is
    # available for any provider state with a live TUI, so it is always an
    # exit action; the provider/model, auth, and scoped-models flows are
    # native-only (scoped models builds model patterns from the native
    # provider state, and its row is shown only for that state).
    exit_actions = frozenset({"theme", "project_trust_default"}) | (
        frozenset({"model", "login", "logout", "scoped_models"})
        if is_native
        else frozenset()
    )

    def _rows() -> list[SettingsRow]:
        return settings_dialog_rows(
            state,
            prompt_history_store,
            in_memory_depth=len(terminal_ui.input_editor.input_history),
            terminal_ui=terminal_ui,
            settings=settings,
        )

    def _local_action(action: str) -> list[SettingsRow]:
        _apply_local_settings_action(
            action,
            terminal_ui=terminal_ui,
            prompt_history_store=prompt_history_store,
            provider_state=provider_state,
            cycle_thinking_level=cycle_thinking_level,
            settings=settings,
            error_stream=error_stream,
        )
        return _rows()

    while True:
        action = terminal_ui.run_settings_dialog(
            _rows(),
            on_local_action=_local_action,
            exit_actions=exit_actions,
        )
        if action is None:
            return
        _run_settings_exit_action(
            action,
            terminal_ui,
            state=state,
            apply_model_selection=apply_model_selection,
            apply_auth_change=apply_auth_change,
            settings=settings,
        )


def _apply_local_settings_action(
    action: str,
    *,
    terminal_ui: TerminalUi,
    prompt_history_store: PromptHistoryStore,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    cycle_thinking_level: Callable[[], str | None],
    settings: "SettingsManager",
    error_stream: TextIO,
) -> None:
    """Apply one action the dialog handles without closing."""

    if action == "toggle_history":
        prompt_history_store.set_enabled(not prompt_history_store.enabled)
    elif action == "clear_history":
        # Wipe only the persisted store; the current session's in-memory
        # Up/Down recall keeps working (the goal only requires that a
        # *fresh* session not recall cleared prompts, and record() never
        # re-persists the existing recall buffer — only new prompts).
        prompt_history_store.clear()
    elif action == "toggle_tools":
        toggle_view_fold(
            HOTKEY_TOGGLE_TOOLS,
            terminal_ui=terminal_ui,
            error_stream=error_stream,
            settings=settings,
        )
    elif action == "toggle_thinking":
        toggle_view_fold(
            HOTKEY_TOGGLE_THINKING,
            terminal_ui=terminal_ui,
            error_stream=error_stream,
            settings=settings,
        )
    elif action == "cycle_thinking":
        cycle_thinking_level_action(
            provider_state,
            terminal_ui=terminal_ui,
            error_stream=error_stream,
            cycle_thinking_level=cycle_thinking_level,
        )


def _run_settings_exit_action(
    action: str,
    terminal_ui: TerminalUi,
    *,
    state: NativeReplProviderState | StaticNativeReplProviderState,
    apply_model_selection: Callable[[str], tuple[bool, str]],
    apply_auth_change: Callable[[str, str], str],
    settings: "SettingsManager",
) -> None:
    """Run one action that had to close the dialog to own the terminal."""

    if action == "model" and isinstance(state, NativeReplProviderState):
        _run_model_selection(terminal_ui, state, apply_model_selection)
    elif action in {"login", "logout"}:
        terminal_ui.add_notice(apply_auth_change(action, ""))
    elif action == "scoped_models" and isinstance(state, NativeReplProviderState):
        open_scoped_models_overlay(terminal_ui, state=state, settings=settings)
    elif action == "theme":
        open_theme_selector(terminal_ui, settings=settings)
    elif action == "project_trust_default":
        open_default_project_trust_selector(terminal_ui, settings=settings)


def _run_model_selection(
    terminal_ui: TerminalUi,
    state: NativeReplProviderState,
    apply_model_selection: Callable[[str], tuple[bool, str]],
) -> None:
    """Offer the model rows and apply the chosen reference, if any."""

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
    chosen = terminal_ui.run_model_selector(ui_options, current_index=current_index)
    if chosen is not None:
        _ok, message = apply_model_selection(selections[chosen].reference)
        terminal_ui.add_notice(message)
