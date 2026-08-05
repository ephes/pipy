"""Overlay-backed selectors: pick a model, a scope, or a trust decision.

`/model`, `/scoped-models` and `/trust` each present a list and act on one
choice. What they share is not the list -- it is the shape of the decision:
build rows that are honest about what is *selectable*, run an overlay when the
terminal can host one, and fall back to a printed list otherwise.

"Honest about what is selectable" is the part worth stating. A model row is
marked unavailable when its provider has no credentials, and non-tool-capable
when it cannot run the agent loop at all; both stay visible so the operator can
see why a model is not offered rather than wondering where it went.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.project_trust import (
    DefaultProjectTrust,
    ProjectTrustError,
    ProjectTrustStore,
    get_project_trust_options,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.scoped_models import filter_scoped_references
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.startup_selectors import run_project_trust_selector
from pipy_harness.native.tui import (
    ModelSelectorOption,
    ScopedModelRow,
    ToolLoopTerminalUi,
)


def handle_trust_command(
    *,
    terminal_ui: ToolLoopTerminalUi | None,
    error_stream: TextIO,
    cwd: Path,
    settings: "SettingsManager",
) -> None:
    """Show and persist a next-start trust decision without hot loading."""

    if terminal_ui is None:
        emit_diagnostic(
            terminal_ui,
            error_stream,
            "pipy: /trust requires the interactive product TUI; use "
            "--approve for this run.",
        )
        return
    store = ProjectTrustStore()
    try:
        saved = store.get_entry(cwd)
    except ProjectTrustError as exc:
        terminal_ui.add_notice(f"pipy: could not read project trust: {exc}")
        return
    selected = run_project_trust_selector(
        terminal_ui,
        cwd=cwd,
        options=get_project_trust_options(cwd),
        saved_decision=saved,
        current_trusted=settings.project_trusted,
    )
    if selected is None:
        return
    try:
        store.set_many(selected.updates)
    except ProjectTrustError as exc:
        terminal_ui.add_notice(f"pipy: could not save project trust: {exc}")
        return
    terminal_ui.add_notice(
        "pipy: saved trust decision: "
        f"{'trusted' if selected.trusted else 'untrusted'}. "
        "Restart pipy for this to take effect."
    )


def open_scoped_models_overlay(
    terminal_ui: ToolLoopTerminalUi,
    *,
    state: NativeReplProviderState,
    settings: "SettingsManager",
) -> None:
    """Open the multi-select scope overlay and persist the chosen scope.

    Builds a checklist of available models, pre-checks those matching the
    current ``enabledModels`` patterns, and on save writes the chosen
    ``provider/model`` references back as the patterns the Ctrl+P cycle uses.
    Runs no provider turn.
    """

    available_refs = [
        option.selection.reference
        for option in state.model_options()
        if option.available
    ]
    if not available_refs:
        terminal_ui.add_notice("pipy: no available models to scope.")
        return
    scoped = filter_scoped_references(available_refs, settings.get_enabled_models())
    rows = [ScopedModelRow(reference=ref, available=True) for ref in available_refs]
    pre_checked = [index for index, ref in enumerate(available_refs) if ref in scoped]
    chosen = terminal_ui.run_scoped_models_selector(rows, checked=pre_checked)
    if chosen is None:
        return
    try:
        settings.set_enabled_models(sorted(chosen))
        message = (
            "pipy: scoped models set: " + ", ".join(sorted(chosen))
            if chosen
            else "pipy: scoped models cleared (cycle uses the full catalog)."
        )
    except RuntimeError as exc:
        message = f"pipy: could not update scoped models: {exc}"
    terminal_ui.add_notice(message)


def open_default_project_trust_selector(
    terminal_ui: ToolLoopTerminalUi,
    *,
    settings: "SettingsManager",
) -> None:
    """Select Pi's global-only trust fallback for future startups."""

    values: tuple[DefaultProjectTrust, ...] = (
        "ask",
        "always",
        "never",
    )
    labels = {
        "ask": "Ask",
        "always": "Trust",
        "never": "Do not trust",
    }
    current = settings.get_default_project_trust()
    options = [
        ModelSelectorOption(
            label=(f"{labels[value]} (current)" if value == current else labels[value]),
            selectable=True,
        )
        for value in values
    ]
    chosen = terminal_ui.run_model_selector(
        options,
        current_index=values.index(current),
        title="Default project trust",
    )
    if chosen is None:
        return
    value = values[chosen]
    try:
        settings.set_default_project_trust(value)
    except (OSError, RuntimeError, ValueError) as exc:
        terminal_ui.add_notice(f"pipy: could not update default project trust: {exc}")
        return
    terminal_ui.add_notice(
        f"pipy: default project trust set to {labels[value]}; "
        "the current session is unchanged."
    )


def model_selector_rows(
    state: NativeReplProviderState,
) -> tuple[list[ModelSelectorOption], list[NativeModelSelection]]:
    """Build the interactive selector rows from the provider-state options.

    Returns the display rows (parallel to ``selections``) and the matching
    ``NativeModelSelection`` list so the caller can map a chosen index back
    to a provider/model reference. A row is selectable only when the
    provider is locally available *and* the built provider advertises
    tool-call support, which tool-loop mode requires. Unavailable or
    non-tool-capable rows stay visible with a reason but are not choosable,
    so the selector never lets a user pick a provider as if it were usable.
    """

    current = state.current_selection()

    def _matches_current(selection: NativeModelSelection) -> bool:
        return (
            selection.provider_name == current.provider_name
            and selection.model_id == current.model_id
        )

    ui_options: list[ModelSelectorOption] = []
    selections: list[NativeModelSelection] = []
    # The active selection may use a non-default model (explicit
    # --native-model or a prior /model <provider>/<custom-model>), which is
    # not present in model_options(). Surface it as the first row so the
    # selector can mark it "(current)" and start the highlight on it. The
    # active provider is tool-capable by the tool-loop invariant, so the row
    # is selectable.
    if not any(_matches_current(option.selection) for option in state.model_options()):
        selections.append(current)
        ui_options.append(
            ModelSelectorOption(
                label=f"{current.reference}  [available] (current)",
                selectable=True,
            )
        )
    for option in state.model_options():
        selection = option.selection
        selectable = option.available
        reason = option.reason
        if selectable and not _selection_supports_tool_calls(state, selection):
            selectable = False
            reason = "no tool-call support"
        if selectable:
            status = "available"
        else:
            status = f"unavailable: {reason or 'unknown'}"
        label = f"{selection.reference}  [{status}]"
        if _matches_current(selection):
            label = f"{label} (current)"
        ui_options.append(ModelSelectorOption(label=label, selectable=selectable))
        selections.append(selection)
    return ui_options, selections


def _selection_supports_tool_calls(
    state: NativeReplProviderState, selection: NativeModelSelection
) -> bool:
    """Return whether the provider for ``selection`` advertises tool calls.

    Builds the provider through the state's catalog-aware construction
    boundary (cheap, side-effect-free construction) only to read
    ``supports_tool_calls`` — so a models.json custom provider/model (api:
    openai-completions) is probed the way it will be used. Any construction
    failure is treated as "not tool-capable" so a broken selection is never
    offered as choosable.
    """

    try:
        provider = state.provider_for(selection)
    except Exception:  # noqa: BLE001 - construct() is total; reads as no tool support
        return False
    return bool(getattr(provider, "supports_tool_calls", False))
