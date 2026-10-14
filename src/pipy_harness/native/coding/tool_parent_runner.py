"""Running a tool that may call other tools (Pi ``ctx.executeTool``).

The agent loop runs such a tool as a composite parent, the way it runs
``codemode``: after the parent's admission and ``tool_call`` hooks it takes
the callable snapshot, reserves the parent's place in the tool budget and
hands a ``NestedToolCallService`` to a runner. Every call the service runs
passes the same admission, hooks and budget as a call the model made, and
the service belongs to the session thread.

The parent's handler runs on a worker thread of the executor, and it asks
for nested calls there, from that thread or from threads it starts. While it
runs, the session thread waits in the service's ``serve``, which is the
waiter the executor is given: it waits with the session's own waiter for
whichever comes first, the handler ending, an interruption, or a request,
and runs each requested call's tool on a thread of its own, so calls from
several threads run at the same time. A session that cannot be interrupted
has no waiter of its own; ``serve`` then waits for the handler alone.

A nested call gets the same ``nested_tools`` for its own id, so a nested
extension tool can call tools too, at any depth, also itself, as in Pi. An
interruption of a nested call ends the parent like an interruption of the
parent itself. When the parent ended, the nested calls still running are
cancelled, and every later request is answered with an error result.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.agent.nested_calls import NestedToolCallService
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.tools.base import NestedToolCalls


class _UntilDone:
    """The waiter of a session that has none: wait for the handler alone.

    Ctrl-C ends such a session: the parent is cancelled, and the interrupt is
    raised again once the executor returned.
    """

    def __init__(self) -> None:
        self.interrupted = False

    def __call__(
        self, done_event: threading.Event, cancel_event: threading.Event, /
    ) -> ToolExecutionInterruption:
        try:
            done_event.wait()
        except KeyboardInterrupt:
            self.interrupted = True
            return ToolExecutionInterruption.OPERATOR_ABORT
        return ToolExecutionInterruption.SETTLED


class ParentToolExecution(Protocol):
    """Runs the parent itself, with what it calls other tools through."""

    def __call__(
        self,
        call: AgentToolCall,
        /,
        *,
        wait_for_interrupt: ToolInterruptWaiter | None,
        nested_tools: NestedToolCalls,
    ) -> ToolExecutionOutcome: ...


@dataclass(frozen=True, slots=True)
class ToolParentRunner:
    """Runs one tool as a composite parent (``AgentCompositeToolRunner``).

    The result carries the record of the nested calls only when the tool
    made one (Pi ``nestedCalls``), and the tool may call itself (Pi
    ``_getCallableTools`` does not leave the caller out).
    """

    execute_parent: ParentToolExecution
    records_always: bool = False
    reaches_itself: bool = True

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        until_done: _UntilDone | None = None
        base: ToolInterruptWaiter
        if tool_waiter is None:
            base = until_done = _UntilDone()
        else:
            base = tool_waiter
        caller_id = call.provider_correlation_id
        nested_tools = service.nested_tools(caller_id)

        def waiter(
            done_event: threading.Event, cancel_event: threading.Event, /
        ) -> ToolExecutionInterruption:
            return service.serve(base, done_event, cancel_event)

        try:
            outcome = self.execute_parent(
                call, wait_for_interrupt=waiter, nested_tools=nested_tools
            )
        finally:
            service.end_caller(caller_id)
        if until_done is not None and until_done.interrupted:
            raise KeyboardInterrupt
        return outcome


def parent_runner_for(
    is_parent: Callable[[str], bool], execute_parent: ParentToolExecution
) -> Callable[[AgentToolCall], ToolParentRunner | None]:
    """What the agent loop asks for each admitted call (``parent_runner``)."""

    runner = ToolParentRunner(execute_parent)

    def select(call: AgentToolCall) -> ToolParentRunner | None:
        return runner if is_parent(call.tool_name) else None

    return select
