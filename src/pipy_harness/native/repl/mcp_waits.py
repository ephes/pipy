"""Running the MCP readiness waits on the session thread.

``McpReadiness`` (``native/mcp/readiness.py``) decides what a prompt or a tool
call waits for and hands out a function that does the waiting. This module
runs that function so the session stays interruptible: the wait itself goes
to a short-lived thread while the session thread watches for Escape or an
external abort through the same waiter a running tool gets.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from pipy_harness.native.agent import AgentToolCall
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.mcp.readiness import (
    STILL_CONNECTING_NOTICE,
    McpReadiness,
    ReadinessWait,
)


def run_readiness_wait(
    wait: ReadinessWait, waiter: ToolInterruptWaiter
) -> tuple[bool, ToolExecutionInterruption]:
    """Run ``wait`` while ``waiter`` watches for an interruption.

    Returns whether the servers settled, and what the waiter saw. The wait is
    over when this returns, also after an interruption or an error.
    """

    done = threading.Event()
    cancel = threading.Event()
    settled: list[bool] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            settled.append(wait(cancel))
        except BaseException as error:  # noqa: BLE001 - raised again by the caller
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=run, name="mcp-wait", daemon=True)
    thread.start()
    try:
        interruption = waiter(done, cancel)
    finally:
        cancel.set()
        thread.join()
    if errors:
        raise errors[0]
    if interruption is not ToolExecutionInterruption.SETTLED:
        return False, interruption
    return bool(settled and settled[0]), interruption


class McpSessionWaits:
    """The waits of one session for MCP servers that are still connecting."""

    def __init__(
        self,
        readiness: McpReadiness,
        *,
        notify: Callable[[str, str], None],
        prompt_waiter: ToolInterruptWaiter | None,
    ) -> None:
        """``prompt_waiter`` watches for an interruption while the first
        prompt waits; without one that wait only ends by its time limit."""

        self._readiness = readiness
        self._notify = notify
        self._prompt_waiter = prompt_waiter

    def before_first_prompt(self) -> None:
        """Hold the session's first prompt for servers with ``direct`` tools.

        Returns at once for every later prompt. An interruption ends the
        wait like its time limit does: the prompt goes on, and the tools
        follow when their server connected.
        """

        wait = self._readiness.first_prompt_wait()
        if wait is None:
            return
        if self._prompt_waiter is None:
            settled = wait(None)
        else:
            settled, _ = run_readiness_wait(wait, self._prompt_waiter)
        if not settled:
            self._notify("info", STILL_CONNECTING_NOTICE)

    def before_tool_call(
        self, call: AgentToolCall, waiter: ToolInterruptWaiter | None, /
    ) -> ToolExecutionInterruption:
        """Wait for the servers ``call`` may reach (``ToolReadinessWaiter``)."""

        wait = self._readiness.call_wait(call.tool_name, call.arguments_json.value)
        if wait is None:
            return ToolExecutionInterruption.SETTLED
        if waiter is None:
            wait(None)
            return ToolExecutionInterruption.SETTLED
        try:
            return run_readiness_wait(wait, waiter)[1]
        except KeyboardInterrupt:
            # As for a running tool: Ctrl-C while waiting aborts the call.
            return ToolExecutionInterruption.OPERATOR_ABORT
