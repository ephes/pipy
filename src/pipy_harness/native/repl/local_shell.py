"""The ``!``/``!!`` editor shell shortcut: run a command, record its context.

Pi's inline shell. ``!ls`` runs the command in the workspace and hands the
provider the command, its exit status and its output as conversation context;
``!!ls`` runs it and records nothing. Neither runs a provider turn, which is
why this lives outside the session: it needs the terminal, the workspace root
and the extension user-bash hooks, and nothing else the composition root holds.

Cancellation is the interesting part. Without a live TUI the command runs
synchronously under a deadline. With one, it runs on a worker thread while the
same active-turn interrupt watcher a provider turn uses reads stdin, so Escape
kills the child *process group* and leaves the session standing.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_hooks import dispatch_user_bash_hooks
from pipy_harness.native.extension_types import (
    ExtensionModelRuntimeControl,
    ExtensionUiDriver,
)
from pipy_harness.native.extensions.contracts import (
    HookHandler,
)
from pipy_harness.native.repl.turn_leaves import CANCEL_JOIN_TIMEOUT_SECONDS
from pipy_harness.native.tools.bash import LocalShellResult, run_local_command
from pipy_harness.native.tui import (
    TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    ToolLoopTerminalUi,
)

# Bound on a ``!``/``!!`` editor shell command so it cannot hang the session
# indefinitely (Escape cancels earlier in a live TTY; a non-TTY script has no
# cancel key, so the deadline is the only bound there). Generous so ordinary
# builds/tests finish well within it.
_LOCAL_SHELL_TIMEOUT_SECONDS = 600


def run_local_shell_shortcut(
    command_line: str,
    *,
    terminal_ui: ToolLoopTerminalUi | None,
    error_stream: TextIO,
    cwd: Path,
    user_bash_hooks: Sequence[HookHandler] = (),
    model_runtime: ExtensionModelRuntimeControl | None = None,
    ui_driver: ExtensionUiDriver | None = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> str | None:
    """Run a ``!``/``!!`` editor shell shortcut; return context text or None.

    ``!!`` excludes the command from provider context (returns ``None``);
    ``!`` returns the command/output text to record into the conversation
    and native session tree. Output streams live into a shaded shell block,
    and Escape cancels a running command (terminating its process group)
    without tearing down the session. Runs no provider turn.
    """

    exclude_from_context = command_line.startswith("!!")
    command = (command_line[2:] if exclude_from_context else command_line[1:]).strip()
    if not command:
        emit_diagnostic(
            terminal_ui,
            error_stream,
            "pipy: ! needs a command, e.g. !ls (use !! to skip recording).",
        )
        return None

    decision = dispatch_user_bash_hooks(
        user_bash_hooks,
        command=command,
        exclude_from_context=exclude_from_context,
        cwd=str(cwd),
        has_ui=terminal_ui is not None,
        notify_sink=lambda kind, message: emit_diagnostic(
            terminal_ui, error_stream, message
        ),
        ui_driver=ui_driver,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    if not decision.allowed:
        emit_diagnostic(
            terminal_ui,
            error_stream,
            f"pipy: shell command blocked by extension: {decision.reason}",
        )
        return None
    command = decision.command
    exclude_from_context = decision.exclude_from_context

    if terminal_ui is not None:
        terminal_ui.add_tool_call(f"$ {command}")
        sink: Callable[[str], None] = terminal_ui.append_tool_output
    else:
        print(f"$ {command}", file=error_stream)

        def sink(chunk: str) -> None:
            print(chunk, end="", file=error_stream, flush=True)

    if decision.result is not None:
        result = LocalShellResult(
            output=decision.result,
            exit_code=decision.exit_code,
            truncated=False,
            timed_out=False,
            cancelled=False,
            started=True,
        )
        sink(decision.result)
    else:
        result = _execute_local_shell(
            command, sink=sink, terminal_ui=terminal_ui, cwd=cwd
        )

    output_text = result.output or "(no output)"
    # Status line mirrors the bash tool's _shape: a timeout, the exit code,
    # or cancellation. A non-zero exit (e.g. !false) is an error the model
    # should see, matching the real bash execution boundary.
    if result.cancelled:
        reason = result.cancel_reason or "escape"
        status_line = f"(cancelled by {reason})"
    elif result.timed_out:
        status_line = "(timed out)"
    else:
        status_line = f"exit code: {result.exit_code}"
    is_error = (
        result.timed_out
        or not result.started
        or (
            not result.cancelled
            and result.exit_code is not None
            and result.exit_code != 0
        )
    )
    if terminal_ui is not None:
        rendered = [status_line, *(output_text.splitlines() or [""])]
        terminal_ui.add_tool_result(lines=rendered, is_error=is_error)
    else:
        # Captured-stream path: the body already streamed through the sink,
        # so print only the status line (never re-print the output — that
        # duplicated every command's output).
        print(status_line, file=error_stream)

    if exclude_from_context or not result.started:
        return None
    return (
        "I ran a shell command in the workspace (not a tool call):\n\n"
        f"$ {command}\n{status_line}\n\n{output_text}"
    )


def _execute_local_shell(
    command: str,
    *,
    sink: Callable[[str], None],
    terminal_ui: ToolLoopTerminalUi | None,
    cwd: Path,
) -> LocalShellResult:
    """Execute ``command`` locally, watching stdin for Escape cancellation.

    With no live TUI (captured streams), runs synchronously. With a live
    TUI, runs the command on a worker thread while the same active-turn
    interrupt watcher used for provider turns reads stdin; Escape/Ctrl-C set
    the cancel event so the runner kills the child process group, then the
    worker is best-effort joined.
    """

    if terminal_ui is None:
        return run_local_command(
            command,
            workspace_root=cwd,
            output_sink=sink,
            timeout=_LOCAL_SHELL_TIMEOUT_SECONDS,
        )

    cancel_event = threading.Event()
    done_event = threading.Event()
    holder: list[LocalShellResult] = []

    def _worker() -> None:
        try:
            holder.append(
                run_local_command(
                    command,
                    workspace_root=cwd,
                    output_sink=sink,
                    cancel_event=cancel_event,
                    timeout=_LOCAL_SHELL_TIMEOUT_SECONDS,
                )
            )
        finally:
            done_event.set()

    worker = threading.Thread(target=_worker, name="pipy-local-shell", daemon=True)
    worker.start()
    outcome = TURN_SETTLED
    try:
        outcome = terminal_ui.wait_for_active_turn_interrupt(
            done_event, cancel_event, accept_commands=True
        )
    except KeyboardInterrupt:
        cancel_event.set()
        outcome = TURN_ABORTED
    worker.join(timeout=CANCEL_JOIN_TIMEOUT_SECONDS)
    cancel_reason = "local command" if outcome == TURN_LOCAL_COMMAND else "escape"
    if holder:
        result = holder[0]
        if result.cancelled:
            result.cancel_reason = cancel_reason
        return result
    return LocalShellResult(
        output="",
        exit_code=None,
        truncated=False,
        timed_out=False,
        cancelled=True,
        started=True,
        cancel_reason=cancel_reason,
    )
