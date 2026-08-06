"""Outer startup adapters for selectors that construct the terminal UI."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from pipy_harness.native.overlay_state import SettingsRow
from pipy_harness.native.project_trust import ProjectTrustEntry, ProjectTrustOption
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    sanitize_label_text,
)
from pipy_harness.native.tui import TerminalUi


def run_project_trust_selector(
    ui: TerminalUi,
    *,
    cwd: Path,
    options: Sequence[ProjectTrustOption],
    saved_decision: ProjectTrustEntry | None = None,
    current_trusted: bool | None = None,
    startup: bool = False,
) -> ProjectTrustOption | None:
    """Drive the shared startup/``/trust`` selector in a live product TUI."""

    canonical_cwd = cwd.expanduser().resolve()
    display_cwd = sanitize_label_text(str(canonical_cwd))
    rows = [
        SettingsRow(label=display_cwd, kind="status"),
    ]
    if startup:
        rows.extend(
            (
                SettingsRow(
                    label="Trust enables project settings/resources and packages.",
                    kind="status",
                ),
                SettingsRow(
                    label="Trusted projects may execute project extensions.",
                    kind="status",
                ),
            )
        )
    else:
        if saved_decision is None:
            saved_label = "none"
        else:
            decision_label = "trusted" if saved_decision.decision else "untrusted"
            display_saved_path = sanitize_label_text(str(saved_decision.path))
            if saved_decision.path != canonical_cwd:
                saved_label = f"{decision_label} (inherited from {display_saved_path})"
            else:
                saved_label = f"{decision_label} ({display_saved_path})"
        rows.extend(
            (
                SettingsRow(label=f"Saved decision: {saved_label}", kind="status"),
                SettingsRow(
                    label=(
                        "Current session: "
                        f"{'trusted' if current_trusted else 'untrusted'}"
                    ),
                    kind="status",
                ),
            )
        )
    action_to_option: dict[str, ProjectTrustOption] = {}
    saved_row_index: int | None = None
    for index, option in enumerate(options):
        action = f"trust-option-{index}"
        action_to_option[action] = option
        rows.append(
            SettingsRow(
                label=sanitize_label_text(option.label),
                kind="action",
                action=action,
            )
        )
        if (
            saved_decision is not None
            and option.saved_path == saved_decision.path
            and option.trusted == saved_decision.decision
        ):
            saved_row_index = len(rows) - 1
    chosen = ui.components.modals.run_settings_dialog(
        rows,
        on_local_action=lambda _action: rows,
        exit_actions=frozenset(action_to_option),
        current_index=saved_row_index,
        title="Trust project folder?" if startup else "Project trust",
        overlay_kind="project_trust",
    )
    return action_to_option.get(chosen) if chosen is not None else None


def run_startup_project_trust_selector(
    *, cwd: Path, options: Sequence[ProjectTrustOption]
) -> ProjectTrustOption | None:
    """Open the pre-runtime project-trust selector on a real TTY."""

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (ValueError, OSError):
        return None
    ui = TerminalUi(
        input_stream=sys.stdin,
        terminal_stream=sys.stdout,
        cwd=cwd,
    )
    try:
        return run_project_trust_selector(
            ui,
            cwd=cwd,
            options=options,
            startup=True,
        )
    finally:
        ui.components.screen.close()


def run_startup_session_picker(
    *,
    project_sessions: Sequence[SessionListEntry],
    all_sessions: Sequence[SessionListEntry],
    current_cwd: str,
) -> Path | None:
    """Open the ``-r`` startup session picker on a real TTY.

    Constructs a standalone inline picker bound to ``sys.stdin``/``sys.stdout``
    and returns the chosen native session file (or ``None`` when there is no TTY
    or the user cancels). Rename/delete actions run through the same native
    boundaries as the in-session ``/resume`` picker; no provider turn runs.
    """

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (ValueError, OSError):
        return None

    from pipy_harness.native.session_tree_commands import delete_native_session

    def on_rename(path: Path, new_name: str) -> None:
        NativeSessionTree.open(path).append_session_info(new_name)

    def on_delete(path: Path) -> tuple[bool, str]:
        return delete_native_session(path)

    ui = TerminalUi(
        input_stream=sys.stdin,
        terminal_stream=sys.stdout,
        cwd=Path(current_cwd),
    )
    try:
        return ui.components.modals.run_session_picker(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=None,
            on_rename=on_rename,
            on_delete=on_delete,
        )
    finally:
        ui.components.screen.close()
