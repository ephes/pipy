"""The session-management commands: what conversation you are in, and where.

`/status`, `/compact`, `/name`, `/new`, `/tree`, `/resume`, `/fork`, `/clone`.
Together they answer two questions -- which branch of which session is active,
and what history the next provider turn will see -- and they are the only
commands that rebind the active tree.

The two interactive pickers here are the overlay-driven halves of `/resume` and
`/tree`. Each falls back to a printed list when the terminal cannot host an
overlay, so a piped or captured session gets the same command with the same
outcome rather than an error.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from pipy_harness.native.agent import AgentMessage, ProductContent
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandOutcome,
)
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl_input import NativeReplInput
from pipy_harness.native.session_tree import (
    NativeSessionTree,
    default_native_session_dir,
)
from pipy_harness.native.session_tree_commands import (
    FILTER_MODES,
    TreeCommandOutcome,
    apply_tree_selection,
    delete_native_session,
    entry_preview,
    format_session_status,
    handle_tree_command,
    list_all_native_sessions,
    list_native_sessions,
    resolve_entry_ref,
    sanitize_label_text,
    visible_tree_entries,
)
from pipy_harness.native.tui import TerminalUi


def run_interactive_session_picker(
    *,
    session_tree: NativeSessionTree,
    terminal_ui: "TerminalUi",
) -> Path | None:
    """Drive the live-TTY ``/resume`` picker over native product sessions.

    Lists the current project's sessions (Tab toggles to all projects),
    offers in-overlay rename/delete (the active session cannot be deleted),
    and returns the chosen native session file or ``None`` on cancel. Runs
    no provider turn and no model-visible tool call.
    """

    session_dir = (
        session_tree.path.parent
        if session_tree.path is not None
        else default_native_session_dir(Path(session_tree.get_header().cwd))
    )
    sessions_root = session_dir.parent
    project_sessions = list_native_sessions(session_dir)
    all_sessions = list_all_native_sessions(sessions_root)

    def on_rename(path: Path, name: str) -> None:
        # Renaming the currently active session must update the live tree so
        # `/session` and the footer reflect the new name immediately; other
        # sessions are renamed through a separately opened tree.
        if session_tree.path is not None and path == session_tree.path:
            session_tree.append_session_info(name)
        else:
            NativeSessionTree.open(path).append_session_info(name)

    def on_delete(path: Path) -> tuple[bool, str]:
        return delete_native_session(path)

    return terminal_ui.components.modals.run_session_picker(
        project_sessions=project_sessions,
        all_sessions=all_sessions,
        current_path=session_tree.path,
        on_rename=on_rename,
        on_delete=on_delete,
    )


def run_interactive_tree_selector(
    *,
    session_tree: NativeSessionTree,
    terminal_ui: "TerminalUi",
    error_stream: TextIO,
    filter_mode: str,
    rebuild_messages: Callable[[], None],
) -> TreeCommandOutcome:
    """Drive the live-TTY ``/tree`` selector and apply the chosen entry.

    Builds filtered rows for the selector, toggles labels on demand, and on
    Enter applies Pi selection semantics: a user message rehydrates the
    editor for a new branch; any other entry sets the leaf with an empty
    editor. Escape cancels with the tree and leaf unchanged.
    """

    from pipy_harness.native.overlay_state import TreeSelectorRow

    def build_rows(mode: str) -> list[TreeSelectorRow]:
        active_ids = {e.id for e in session_tree.get_branch()}
        rows: list[TreeSelectorRow] = []
        for entry in visible_tree_entries(session_tree, filter_mode=mode):
            rows.append(
                TreeSelectorRow(
                    entry_id=entry.id,
                    label=entry_preview(session_tree, entry),
                    active=entry.id in active_ids,
                    labeled=session_tree.get_label(entry.id) is not None,
                )
            )
        return rows

    def on_label_toggle(entry_id: str) -> None:
        existing = session_tree.get_label(entry_id)
        session_tree.append_label_change(entry_id, None if existing else "marked")

    closed = terminal_ui.components.modals.run_tree_selector(
        build_rows=build_rows,
        filter_modes=FILTER_MODES,
        initial_filter=filter_mode if filter_mode in FILTER_MODES else "default",
        on_label_toggle=on_label_toggle,
    )
    new_filter = closed.filter_mode
    chosen = closed.entry_id
    if chosen is None:
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            "pipy: /tree cancelled.",
        )
        return TreeCommandOutcome(filter_mode=new_filter)
    selection = apply_tree_selection(session_tree, chosen)
    rebuild_messages()
    if selection.is_noop:
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            "pipy: already at the selected point (no change).",
        )
        return TreeCommandOutcome(filter_mode=new_filter)
    if selection.is_user_selection:
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            "pipy: selected user message; rehydrating editor for a new branch.",
        )
        return TreeCommandOutcome(prefill=selection.editor_text, filter_mode=new_filter)
    emit_diagnostic(
        terminal_ui.components.transcript if terminal_ui is not None else None,
        error_stream,
        f"pipy: continuing from entry {sanitize_label_text(chosen[:8])}.",
    )
    return TreeCommandOutcome(filter_mode=new_filter)


def run_tree_command(
    argument: str,
    *,
    session_tree: NativeSessionTree,
    terminal_ui: "TerminalUi | None",
    error_stream: TextIO,
    repl_input: object,
    filter_mode: str,
    rebuild_messages: Callable[[], None],
    summarizer: Callable[[list[AgentMessage], str | None], str | None] | None = None,
) -> TreeCommandOutcome:
    """Adapt the owner command handler to optional terminal interaction."""

    del repl_input
    interactive_selector: Callable[[], TreeCommandOutcome] | None = None
    if terminal_ui is not None:
        interactive_selector = partial(
            run_interactive_tree_selector,
            session_tree=session_tree,
            terminal_ui=terminal_ui,
            error_stream=error_stream,
            filter_mode=filter_mode,
            rebuild_messages=rebuild_messages,
        )
    return handle_tree_command(
        argument,
        session_tree=session_tree,
        filter_mode=filter_mode,
        rebuild_messages=rebuild_messages,
        diagnostic=lambda message: emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            message,
        ),
        summarizer=summarizer,
        interactive_selector=interactive_selector,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionCommandEffects:
    """Execute the built-in command family owned by the product session.

    The frozen collaborator bundle is constructed once in ``run()`` and reads
    the rebindable session tree and extension generation through ``ctl`` on
    every command. It deliberately owns no provider, settings, package, or
    unrelated presentation dependency.
    """

    ctl: RunControlState
    cwd: Path
    terminal_ui: TerminalUi | None
    error_stream: TextIO
    repl_input: "TerminalUi | NativeReplInput"
    diag: Callable[[str], None]
    apply_compaction: Callable[[str], str]
    extension_session_allows: Callable[..., bool]
    rebuild_messages_from_tree: Callable[[], None]
    redraw_custom_entries_for_active_branch: Callable[[], None]
    current_session_dir: Callable[[], Path]
    resolve_session_file: Callable[[str], Path | None]
    summarize_branch: Callable[[list[AgentMessage], str | None], str | None]

    def execute(self, command_outcome: CodingCommandOutcome) -> None:
        """Execute one outcome from the closed session-command family."""

        action = command_outcome.action
        if action is CodingCommandAction.SHOW_SESSION_STATUS:
            self.diag(format_session_status(self.ctl.session_tree))
        elif action is CodingCommandAction.COMPACT:
            # Local-only: reduce provider-visible history while preserving the
            # shared manual/automatic compaction policy, extension gate, and
            # durable write ordering.
            self.diag(self.apply_compaction("manual"))
        elif action is CodingCommandAction.SESSION_NAME:
            self._execute_name(command_outcome)
        elif action is CodingCommandAction.NEW_SESSION:
            self._execute_new()
        elif action is CodingCommandAction.SESSION_TREE:
            self._execute_tree(command_outcome)
        elif action is CodingCommandAction.SESSION_RESUME:
            self._execute_resume(command_outcome)
        elif action in {
            CodingCommandAction.SESSION_FORK,
            CodingCommandAction.SESSION_CLONE,
        }:
            self._execute_fork_or_clone(command_outcome)
        else:
            raise AssertionError("session command executor received another action")

    def _execute_name(self, command_outcome: CodingCommandOutcome) -> None:
        session_name_argument = command_outcome.argument
        if type(session_name_argument) is not ProductContent:
            raise TypeError("SESSION_NAME requires an exact ProductContent argument")
        if not session_name_argument.value:
            self.diag(
                "pipy: current session name: "
                + (
                    sanitize_label_text(self.ctl.session_tree.name)
                    if self.ctl.session_tree.name
                    else "(unnamed)"
                )
            )
        else:
            self.ctl.session_tree.append_session_info(session_name_argument.value)
            self.diag(f"pipy: session named {session_name_argument.value!r}.")

    def _execute_new(self) -> None:
        # Start a fresh native product session in the same store.
        if self.extension_session_allows("switch", operation="switch", target="new"):
            session_dir = (
                self.ctl.session_tree.path.parent
                if self.ctl.session_tree.path is not None
                else None
            )
            self.ctl.session_tree = NativeSessionTree.create(
                self.cwd,
                session_dir=session_dir,
                persist=self.ctl.session_tree.persist,
            )
            self.rebuild_messages_from_tree()
            self.diag(
                "pipy: started a new native session "
                f"({sanitize_label_text(self.ctl.session_tree.session_id[:8])})."
            )

    def _execute_tree(self, command_outcome: CodingCommandOutcome) -> None:
        tree_argument = command_outcome.argument
        if type(tree_argument) is not ProductContent:
            raise TypeError("SESSION_TREE requires an exact ProductContent argument")
        argument = tree_argument.value
        tree_sub = argument.split(maxsplit=1)[0].lower() if argument else ""
        tree_may_change = (
            not argument and self.terminal_ui is not None
        ) or tree_sub in {"select", "label", "filter"}
        tree_allowed = not tree_may_change or self.extension_session_allows(
            "tree", operation="tree", target=argument or None
        )
        if tree_allowed:
            tree_outcome = run_tree_command(
                argument,
                session_tree=self.ctl.session_tree,
                terminal_ui=self.terminal_ui,
                error_stream=self.error_stream,
                repl_input=self.repl_input,
                filter_mode=self.ctl.tree_filter_mode,
                rebuild_messages=self.rebuild_messages_from_tree,
                summarizer=self.summarize_branch,
            )
            if tree_outcome.filter_mode is not None:
                self.ctl.tree_filter_mode = tree_outcome.filter_mode
            if tree_outcome.prefill is not None:
                self.ctl.pending_prefill = tree_outcome.prefill

    def _execute_resume(self, command_outcome: CodingCommandOutcome) -> None:
        resume_argument = command_outcome.argument
        if type(resume_argument) is not ProductContent:
            raise TypeError("SESSION_RESUME requires an exact ProductContent argument")
        argument = resume_argument.value
        resume_tokens = argument.split()
        resume_sub = resume_tokens[0].lower() if resume_tokens else ""

        if not argument and self.terminal_ui is not None:
            self._resume_from_picker()
        elif not argument:
            self._list_sessions()
        elif resume_sub == "named":
            self._list_sessions(named_only=True)
        elif resume_sub == "rename":
            self._rename_session(resume_tokens)
        elif resume_sub == "delete":
            self._delete_session(resume_tokens)
        else:
            self._resume_target(argument)

    def _list_sessions(self, named_only: bool = False) -> None:
        sessions = list_native_sessions(self.current_session_dir())
        sessions = (
            [session for session in sessions if session.name]
            if named_only
            else sessions
        )
        if not sessions:
            self.diag("pipy: no native sessions found for this workspace.")
            return
        scope = "named " if named_only else ""
        self.diag(f"pipy: {scope}native sessions (newest first):")
        for index, entry in enumerate(sessions, start=1):
            label = sanitize_label_text(entry.name) if entry.name else "(unnamed)"
            self.diag(
                f"  {index}. "
                f"{sanitize_label_text(entry.session_id[:8])} "
                f"{label} "
                f"messages={entry.message_count} "
                f"file={sanitize_label_text(entry.path.name)}"
            )
        self.diag("pipy: use '/resume <number|id>' to open a session.")

    def _resume_from_picker(self) -> None:
        terminal_ui = self.terminal_ui
        if terminal_ui is None:
            raise AssertionError("interactive session picker requires a terminal UI")
        picked_session = run_interactive_session_picker(
            session_tree=self.ctl.session_tree,
            terminal_ui=terminal_ui,
        )
        if picked_session is None:
            self.diag("pipy: /resume cancelled.")
        elif (
            self.ctl.session_tree.path is not None
            and picked_session == self.ctl.session_tree.path
        ):
            self.diag("pipy: already on the selected native session.")
        elif self.extension_session_allows(
            "switch", operation="switch", target=str(picked_session)
        ):
            self._open_session(picked_session)

    def _rename_session(self, resume_tokens: list[str]) -> None:
        if len(resume_tokens) < 3:
            self.diag("pipy: usage: /resume rename <number|id> <name>")
            return
        target = self.resolve_session_file(resume_tokens[1])
        if target is None:
            self.diag(f"pipy: no native session matched {resume_tokens[1]!r}.")
            return
        renamed = NativeSessionTree.open(target)
        new_name = " ".join(resume_tokens[2:])
        renamed.append_session_info(new_name)
        self.diag(
            "pipy: renamed session "
            f"{sanitize_label_text(renamed.session_id[:8])} "
            f"to {new_name!r}."
        )

    def _delete_session(self, resume_tokens: list[str]) -> None:
        confirm = "--yes" in resume_tokens[1:]
        refs = [token for token in resume_tokens[1:] if token != "--yes"]
        if not refs:
            self.diag("pipy: usage: /resume delete <number|id> --yes")
            return
        target = self.resolve_session_file(refs[0])
        if target is None:
            self.diag(f"pipy: no native session matched {refs[0]!r}.")
        elif (
            self.ctl.session_tree.path is not None
            and target == self.ctl.session_tree.path
        ):
            self.diag("pipy: cannot delete the active native session.")
        elif not confirm:
            self.diag(
                "pipy: deletion needs confirmation; "
                "re-run "
                f"'/resume delete {refs[0]} --yes'. This "
                "removes only the native session file, "
                "never pipy-session archive records."
            )
        else:
            _ok, detail = delete_native_session(target)
            self.diag(f"pipy: {detail}")

    def _resume_target(self, argument: str) -> None:
        target = self.resolve_session_file(argument)
        if target is None:
            self.diag(f"pipy: no native session matched {argument!r}.")
        elif self.extension_session_allows(
            "switch", operation="switch", target=str(target)
        ):
            self._open_session(target)

    def _open_session(self, target: Path) -> None:
        self.ctl.session_tree = NativeSessionTree.open(target)
        self.rebuild_messages_from_tree()
        self.redraw_custom_entries_for_active_branch()
        self.diag(
            "pipy: resumed native session "
            f"{sanitize_label_text(self.ctl.session_tree.session_id[:8])} "
            f"({sanitize_label_text(self.ctl.session_tree.name) if self.ctl.session_tree.name else 'unnamed'})."
        )

    def _execute_fork_or_clone(self, command_outcome: CodingCommandOutcome) -> None:
        action = command_outcome.action
        if action not in {
            CodingCommandAction.SESSION_FORK,
            CodingCommandAction.SESSION_CLONE,
        }:
            raise AssertionError("fork/clone handler received another action")
        if action is CodingCommandAction.SESSION_FORK:
            fork_argument = command_outcome.argument
            if type(fork_argument) is not ProductContent:
                raise TypeError(
                    "SESSION_FORK requires an exact ProductContent argument"
                )
            argument = fork_argument.value
        else:
            argument = ""
        if self.ctl.session_tree.path is None:
            command_name = {
                CodingCommandAction.SESSION_FORK: "/fork",
                CodingCommandAction.SESSION_CLONE: "/clone",
            }[action]
            self.diag(f"pipy: {command_name} requires a persistent native session.")
            return
        fork_leaf: str | None = None
        fork_target_resolved = True
        if argument:
            target_entry = resolve_entry_ref(
                self.ctl.session_tree,
                argument,
                filter_mode=self.ctl.tree_filter_mode,
            )
            if target_entry is None:
                self.diag(f"pipy: no tree entry matched {argument!r}.")
                fork_target_resolved = False
            else:
                fork_leaf = target_entry.id
        else:
            fork_leaf = self.ctl.session_tree.get_leaf_id()
        if fork_target_resolved and self.extension_session_allows(
            "fork", operation="fork", target=fork_leaf
        ):
            forked_tree = NativeSessionTree.fork_from(
                self.ctl.session_tree.path,
                self.cwd,
                leaf_id=fork_leaf,
                session_dir=self.ctl.session_tree.path.parent,
            )
            self.ctl.session_tree = forked_tree
            self.rebuild_messages_from_tree()
            success_text = {
                CodingCommandAction.SESSION_FORK: "forked into new native session ",
                CodingCommandAction.SESSION_CLONE: (
                    "cloned active branch into new native session "
                ),
            }[action]
            self.diag(
                f"pipy: {success_text}"
                f"{sanitize_label_text(self.ctl.session_tree.session_id[:8])}."
            )
