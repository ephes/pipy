"""``/mcp`` in a session: the command of ``native/mcp/commands.py`` behind
the ports of the session.

Pi registers ``/mcp`` as a command of its built-in MCP extension. pipy
dispatches it where extension commands are dispatched, after them: an
extension's own ``mcp`` command takes its place, and with MCP turned off the
command does not exist. It also runs for a prompt an RPC client sends, like
an extension command.

A step of the command that can take long runs on a short-lived thread while
the session thread watches for Escape or an abort, as the readiness waits do
(``mcp_waits.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping

from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.editor_state import CompletionItem
from pipy_harness.native.mcp.commands import (
    MCP_COMMAND_DESCRIPTION,
    MCP_COMMAND_NAME,
    McpCommand,
    RunStep,
)

ArgumentCompleter = Callable[[str, str], tuple[CompletionItem, ...] | None]

MCP_SLASH_NAME = f"/{MCP_COMMAND_NAME}"


def mcp_session_commands(*, enabled: bool) -> Mapping[str, str]:
    """The slash commands MCP adds to a session, with their descriptions."""

    return {MCP_SLASH_NAME: MCP_COMMAND_DESCRIPTION} if enabled else {}


def interruptible_steps(waiter: ToolInterruptWaiter) -> RunStep:
    """A ``RunStep`` that runs each step while ``waiter`` watches for an
    interruption.

    When the step finished, its error is raised here and the result is
    ``True``. After an interruption the cancel event is set and the result is
    ``False`` unless the step had finished anyway; a step that does not look
    at the event goes on in the background and what it raises is dropped.
    """

    def run_step(step: Callable[[threading.Event], object]) -> bool:
        done = threading.Event()
        cancel = threading.Event()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                step(cancel)
            except BaseException as error:  # noqa: BLE001 - raised again by the caller
                errors.append(error)
            finally:
                done.set()

        threading.Thread(target=run, name="mcp-command", daemon=True).start()
        try:
            interruption = waiter(done, cancel)
        finally:
            cancel.set()
        if interruption is ToolExecutionInterruption.SETTLED:
            done.wait()
        if not done.is_set():
            return False
        if errors:
            raise errors[0]
        return True

    return run_step


class McpSessionCommand:
    """Dispatch and argument completion of ``/mcp`` for one session."""

    def __init__(self, command: Callable[[], McpCommand | None]) -> None:
        """``command`` gives the command for the servers as they are now,
        or ``None`` while MCP is turned off. It is asked every time, because
        a ``/reload`` replaces the servers and may turn MCP on or off."""

        self._command = command

    def dispatch(self, command_text: str) -> bool:
        """Run ``command_text`` when it is ``/mcp`` with or without
        arguments; returns whether it was. With MCP off it never is."""

        name, _, args = command_text.partition(" ")
        if name != MCP_SLASH_NAME:
            return False
        command = self._command()
        if command is None:
            return False
        command.run(args)
        return True

    def completer(self, fallback: ArgumentCompleter) -> ArgumentCompleter:
        """``fallback`` extended by the arguments of ``/mcp``."""

        def complete(command: str, argument: str) -> tuple[CompletionItem, ...] | None:
            mcp = self._command() if command == MCP_COMMAND_NAME else None
            if mcp is None:
                return fallback(command, argument)
            items = mcp.complete(argument)
            if items is None:
                return None
            return tuple(
                CompletionItem(item.value, item.label, item.description)
                for item in items
            )

        return complete
