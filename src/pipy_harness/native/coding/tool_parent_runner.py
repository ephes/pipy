"""Running a tool that may call other tools (Pi ``ctx.executeTool``).

The agent loop runs such a tool as a composite parent, the way it runs
``codemode``: after the parent's admission and ``tool_call`` hooks it takes
the callable snapshot, reserves the parent's place in the tool budget and
hands a ``NestedToolCallService`` to a runner. Every call the service runs
passes the same admission, hooks and budget as a call the model made, and
the service belongs to the session thread.

A tool's handler runs on a worker thread whenever the session can be
interrupted, and it asks for a nested call there. ``NestedToolBridge``
carries the request to the session thread: while the handler runs, the
session thread is inside the waiter the executor was given, which here is
``serve``. It waits with the session's own waiter for whichever comes first:
the handler ending, an interruption, or a request, which it then runs through
the service and answers.

A session that cannot be interrupted has no waiter, and the executor then
runs the handler on the session thread itself. There the handler reaches the
service directly, and nothing can serve another thread while the handler has
the session thread, so a call from a thread the handler started is answered
with an error result at once.

An interruption of a nested call ends the parent like an interruption of the
parent itself. When the parent ended, by returning or because the session
stopped waiting for it, the bridge is closed and answers every later request
with an error result.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Protocol

from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.agent.nested_calls import NestedToolCallService
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.tools.base import (
    NESTED_CALLS_UNAVAILABLE,
    NestedToolCalls,
    NestedToolResult,
    ToolInfo,
    tool_info,
)

# How often the thread that watches for the parent's end looks whether the
# bridge was closed without it.
_WATCH_SECONDS: Final = 0.05


def unavailable_result(caller_id: str = "") -> NestedToolResult:
    """What a call gets where no nested call can run (Pi's text and its id
    ``<calling id>/0``)."""

    return NestedToolResult(
        NESTED_CALLS_UNAVAILABLE,
        is_error=True,
        tool_call_id=f"{caller_id}/0" if caller_id else "",
    )


class _Request:
    __slots__ = ("arguments_json", "ready", "result", "tool_name")

    def __init__(
        self, tool_name: str, arguments_json: str, result: NestedToolResult
    ) -> None:
        self.tool_name = tool_name
        self.arguments_json = arguments_json
        self.ready = threading.Event()
        self.result = result


class NestedToolBridge:
    """The nested calls of one running parent (``NestedToolCalls``).

    Created on the session thread, which owns ``service``. ``execute`` may
    be called from any thread. The lock guards the requests, the wake event
    and ``_closed``; the service is used by the session thread only.
    """

    def __init__(
        self, service: NestedToolCallService, *, serves: bool, caller_id: str = ""
    ) -> None:
        """``serves`` says whether ``serve`` will run: only then can a call
        from another thread be carried to the session thread. ``caller_id``
        is the id of the calling tool call."""
        self._service = service
        self._caller_id = caller_id
        self._serves = serves
        self._thread = threading.current_thread()
        self._definitions = service.callable_tools()
        self._lock = threading.Lock()
        self._requests: deque[_Request] = deque()
        self._wake = threading.Event()
        self._closed = False

    def tools(self) -> tuple[ToolInfo, ...]:
        return tuple(tool_info(definition) for definition in self._definitions)

    def execute(self, tool_name: str, arguments: object, /) -> NestedToolResult:
        name = str(tool_name)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return self._refused(f"Tool {name} arguments must be an object")
        try:
            arguments_json = json.dumps(dict(arguments))
        except (TypeError, ValueError) as error:
            return self._refused(f"Tool {name} arguments must be JSON: {error}")
        if threading.current_thread() is self._thread:
            with self._lock:
                if self._closed:
                    return unavailable_result(self._caller_id)
            return self._run(name, arguments_json)[0]
        request = _Request(name, arguments_json, unavailable_result(self._caller_id))
        with self._lock:
            if self._closed or not self._serves:
                return unavailable_result(self._caller_id)
            self._requests.append(request)
            self._wake.set()
        request.ready.wait()
        return request.result

    def _run(
        self, tool_name: str, arguments_json: str
    ) -> tuple[NestedToolResult, ToolExecutionInterruption | None]:
        """Session thread: one call through the service."""

        outcome = self._service.call(
            tool_name,
            arguments_json,
            unknown_tool_error=f"Tool {tool_name} not found",
        )
        result = outcome.result
        call = outcome.nested_call
        return (
            NestedToolResult(
                result.content.value,
                is_error=result.is_error,
                details=result.details,
                structured_content=result.structured_content,
                blocks=result.blocks,
                tool_call_id=call.provider_correlation_id,
                arguments=_arguments(call.arguments_json.value),
                duration_ms=None
                if outcome.status is NestedCallStatus.REFUSED
                else outcome.duration_seconds * 1000,
            ),
            outcome.interruption,
        )

    def _refused(self, text: str) -> NestedToolResult:
        """A call refused before it got an id of its own (Pi ``<id>/0``)."""
        return replace(unavailable_result(self._caller_id), content=text)

    def serve(
        self,
        waiter: ToolInterruptWaiter,
        done_event: threading.Event,
        cancel_event: threading.Event,
        /,
    ) -> ToolExecutionInterruption:
        """Session thread: wait for the parent as ``waiter`` would, and run
        the calls it asks for meanwhile. Returns what ended the wait; the
        bridge is closed then."""

        threading.Thread(
            target=self._watch, args=(done_event,), name="pipy-nested-wake", daemon=True
        ).start()
        try:
            while True:
                interruption = waiter(self._wake, cancel_event)
                if interruption is not ToolExecutionInterruption.SETTLED:
                    return interruption
                with self._lock:
                    if done_event.is_set():
                        return ToolExecutionInterruption.SETTLED
                    if not self._requests:
                        self._wake.clear()
                        continue
                    request = self._requests.popleft()
                    if not self._requests:
                        self._wake.clear()
                try:
                    request.result, interrupted = self._run(
                        request.tool_name, request.arguments_json
                    )
                finally:
                    request.ready.set()
                if interrupted is not None:
                    return interrupted
        finally:
            self.close()

    def _watch(self, done_event: threading.Event) -> None:
        """Wake the session thread when the parent ended."""

        while not done_event.wait(_WATCH_SECONDS):
            with self._lock:
                if self._closed:
                    return
        with self._lock:
            self._wake.set()

    def close(self) -> None:
        """Refuse the requests nobody ran, and every later one."""

        with self._lock:
            self._closed = True
            pending = tuple(self._requests)
            self._requests.clear()
        for request in pending:
            request.ready.set()


def _arguments(arguments_json: str) -> dict[str, object]:
    """The arguments of a call as a fresh object; ``{}`` when they are not one."""
    try:
        value = json.loads(arguments_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


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
    made one (Pi ``nestedCalls``).
    """

    execute_parent: ParentToolExecution
    records_always: bool = False

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        bridge = NestedToolBridge(
            service,
            serves=tool_waiter is not None,
            caller_id=call.provider_correlation_id,
        )
        waiter: ToolInterruptWaiter | None = None
        if tool_waiter is not None:
            session_waiter = tool_waiter

            def waiter(
                done_event: threading.Event, cancel_event: threading.Event, /
            ) -> ToolExecutionInterruption:
                return bridge.serve(session_waiter, done_event, cancel_event)

        try:
            return self.execute_parent(
                call, wait_for_interrupt=waiter, nested_tools=bridge
            )
        finally:
            bridge.close()


def parent_runner_for(
    is_parent: Callable[[str], bool], execute_parent: ParentToolExecution
) -> Callable[[AgentToolCall], ToolParentRunner | None]:
    """What the agent loop asks for each admitted call (``parent_runner``)."""

    runner = ToolParentRunner(execute_parent)

    def select(call: AgentToolCall) -> ToolParentRunner | None:
        return runner if is_parent(call.tool_name) else None

    return select
