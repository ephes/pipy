"""Moving a native session across the process boundary: export, import, share.

Three verbs that leave the running conversation exactly where it is. `/export`
writes the tree out as HTML or JSONL; `/import` reads one back and *replaces*
the active tree, which is the only one of the three that mutates anything;
`/share` uploads to a gist and hands back a viewer URL.

`/share` is the one with real concurrency: with a live TUI it runs the upload on
a worker thread while the active-turn interrupt watcher reads stdin, so Escape
cancels the upload through a `CancelToken` rather than abandoning a thread. With
no TUI there is no cancel key, so it falls back to the caller's abort event --
which is why that arrives as a value rather than being read off a session.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.agent.provider_turn import _AbortCallbackSignal
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandOutcome,
)
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.export_distribution import (
    NativeExportError,
    ShareCancelled,
    ShareResult,
    default_html_export_path,
    export_native_branch_to_jsonl,
    export_native_session_to_html,
    import_native_session_jsonl,
    parse_command_path_argument,
    resolve_github_token,
    share_native_session,
)
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.turn_leaves import CANCEL_JOIN_TIMEOUT_SECONDS
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.tui import TURN_ABORTED, ToolLoopTerminalUi


def export_session(
    argument: ProductContent | None,
    *,
    session_tree: NativeSessionTree,
    cwd: Path,
    system_prompt: str,
    diagnostic: Callable[[str], None],
) -> None:
    """Export the active product session through the typed command effect."""

    if type(argument) is not ProductContent:
        raise TypeError("SESSION_EXPORT requires an exact ProductContent argument")
    path_arg = parse_command_path_argument(argument.value)
    try:
        if path_arg and Path(path_arg).suffix.lower() == ".jsonl":
            output_path = Path(path_arg).expanduser()
            if not output_path.is_absolute():
                output_path = cwd / output_path
            exported = export_native_branch_to_jsonl(session_tree, output_path)
            diagnostic(f"pipy: exported native session JSONL to {exported}.")
        else:
            output_path = (
                Path(path_arg).expanduser()
                if path_arg
                else default_html_export_path(session_tree, cwd=cwd)
            )
            if not output_path.is_absolute():
                output_path = cwd / output_path
            exported = export_native_session_to_html(
                session_tree,
                output_path,
                system_prompt=system_prompt,
            )
            diagnostic(f"pipy: exported native session HTML to {exported}.")
    except NativeExportError as exc:
        diagnostic(f"pipy: {exc}")


def _confirm_import_prompt(
    prompt: str,
    *,
    input_stream: TextIO,
    error_stream: TextIO,
) -> bool:
    """Read one direct import confirmation without changing failure policy."""

    print(prompt, end="", file=error_stream, flush=True)
    try:
        return input_stream.readline().strip().lower() in ("y", "yes")
    except (OSError, ValueError):
        return False


def _resolve_import_source_path(argument: str, *, cwd: Path) -> Path | None:
    """Parse and expand the first import path without resolving symlinks."""

    path_arg = parse_command_path_argument(argument)
    if not path_arg:
        return None
    source_path = Path(path_arg).expanduser()
    if source_path.is_absolute():
        return source_path
    return cwd / source_path


def import_session(
    argument: ProductContent | None,
    *,
    cwd: Path,
    input_stream: TextIO,
    error_stream: TextIO,
    current_session_dir: Callable[[], Path],
    session_switch_allows: Callable[[str], bool],
    diagnostic: Callable[[str], None],
) -> NativeSessionTree | None:
    """Import a product session through the typed command effect."""

    if type(argument) is not ProductContent:
        raise TypeError("SESSION_IMPORT requires an exact ProductContent argument")
    source_path = _resolve_import_source_path(argument.value, cwd=cwd)
    if source_path is None:
        diagnostic("pipy: Usage: /import <path.jsonl>")
        return None
    confirm = "--yes" in argument.value.split()
    if not confirm:
        confirm = _confirm_import_prompt(
            f"Replace current session with {source_path}? [y/N] ",
            input_stream=input_stream,
            error_stream=error_stream,
        )
    if not confirm:
        diagnostic("pipy: /import cancelled.")
        return None
    if not session_switch_allows(str(source_path)):
        return None
    try:
        return import_native_session_jsonl(
            source_path,
            session_dir=current_session_dir(),
        )
    except NativeExportError as exc:
        if "imported session cwd does not exist:" not in str(exc):
            diagnostic(f"pipy: {exc}")
            return None
        use_current = _confirm_import_prompt(
            f"{exc} Use current workspace {cwd}? [y/N] ",
            input_stream=input_stream,
            error_stream=error_stream,
        )
        if not use_current:
            diagnostic("pipy: /import cancelled.")
            return None
        try:
            return import_native_session_jsonl(
                source_path,
                session_dir=current_session_dir(),
                missing_cwd=cwd,
            )
        except NativeExportError as second_exc:
            diagnostic(f"pipy: {second_exc}")
            return None


def share_native_session_command(
    *,
    session_tree: NativeSessionTree,
    token: str,
    abort_event: threading.Event | _AbortCallbackSignal | None,
    terminal_ui: ToolLoopTerminalUi | None,
    error_stream: TextIO,
) -> ShareResult | None:
    """Run ``/share`` with product cancellation when the TUI is active."""

    if terminal_ui is None:
        return share_native_session(
            session_tree,
            token=token,
            cancelled=(abort_event.is_set if abort_event is not None else None),
        )

    cancel_token = CancelToken()
    done_event = threading.Event()
    result_holder: list[ShareResult] = []
    error_holder: list[BaseException] = []

    def _run_share() -> None:
        try:
            result_holder.append(
                share_native_session(
                    session_tree,
                    token=token,
                    cancelled=cancel_token.event.is_set,
                    cancel_token=cancel_token,
                )
            )
        # re-raised by the caller
        except BaseException as exc:  # pragma: no cover  # noqa: BLE001
            error_holder.append(exc)
        finally:
            done_event.set()

    emit_diagnostic(
        terminal_ui,
        error_stream,
        "pipy: sharing native session... press Escape to cancel.",
    )
    worker = threading.Thread(target=_run_share, name="pipy-share-gist", daemon=True)
    worker.start()
    try:
        outcome = terminal_ui.wait_for_active_turn_interrupt(
            done_event, cancel_token.event, accept_queue=False
        )
    except KeyboardInterrupt:
        cancel_token.cancel()
        worker.join(timeout=CANCEL_JOIN_TIMEOUT_SECONDS)
        emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
        return None
    if outcome == TURN_ABORTED:
        cancel_token.cancel()
        worker.join(timeout=CANCEL_JOIN_TIMEOUT_SECONDS)
        emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
        return None
    worker.join(timeout=CANCEL_JOIN_TIMEOUT_SECONDS)
    if error_holder:
        error = error_holder[0]
        if isinstance(error, ShareCancelled):
            emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
            return None
        if isinstance(error, NativeExportError):
            raise error
        raise error
    return result_holder[0] if result_holder else None


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferCommandEffects:
    """Execute native session export, import, and share effects."""

    abort_event: threading.Event | _AbortCallbackSignal | None
    ctl: RunControlState
    cwd: Path
    system_prompt: str
    input_stream: TextIO
    error_stream: TextIO
    terminal_ui: ToolLoopTerminalUi | None
    diag: Callable[[str], None]
    current_session_dir: Callable[[], Path]
    session_switch_allows: Callable[[str], bool]
    rebuild_messages_from_tree: Callable[[], None]

    def execute(self, command_outcome: CodingCommandOutcome) -> None:
        """Execute one outcome from the closed transfer-command family."""

        action = command_outcome.action
        if action is CodingCommandAction.SESSION_EXPORT:
            export_session(
                command_outcome.argument,
                session_tree=self.ctl.session_tree,
                cwd=self.cwd,
                system_prompt=self.system_prompt,
                diagnostic=self.diag,
            )
        elif action is CodingCommandAction.SESSION_IMPORT:
            self._execute_import(command_outcome)
        elif action is CodingCommandAction.SESSION_SHARE:
            self._execute_share()
        else:
            raise AssertionError("transfer command executor received another action")

    def _execute_import(self, command_outcome: CodingCommandOutcome) -> None:
        imported_tree = import_session(
            command_outcome.argument,
            cwd=self.cwd,
            input_stream=self.input_stream,
            error_stream=self.error_stream,
            current_session_dir=self.current_session_dir,
            session_switch_allows=self.session_switch_allows,
            diagnostic=self.diag,
        )
        if imported_tree is None:
            return
        self.ctl.session_tree = imported_tree
        self.rebuild_messages_from_tree()
        self.diag(
            "pipy: imported native session "
            f"{sanitize_label_text(self.ctl.session_tree.session_id[:8])}."
        )

    def _execute_share(self) -> None:
        token = resolve_github_token()
        if not token:
            self.diag(
                "pipy: No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`."
            )
            return
        try:
            result = share_native_session_command(
                session_tree=self.ctl.session_tree,
                token=token,
                abort_event=self.abort_event,
                terminal_ui=self.terminal_ui,
                error_stream=self.error_stream,
            )
        except NativeExportError as exc:
            self.diag(f"pipy: {exc}")
            return
        if result is None:
            return
        if result.viewer_url:
            self.diag(
                f"pipy: share URL: {result.viewer_url}\n"
                f"pipy: gist URL: {result.gist_url}"
            )
        else:
            self.diag(f"pipy: gist URL: {result.gist_url}")
