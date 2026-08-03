"""Synchronous, UI-free execution for one canonical agent tool call."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
    validate_arguments,
)


class ToolExecutionInterruption(StrEnum):
    """Closed reasons why a caller stopped waiting for a tool invocation."""

    SETTLED = "settled"
    OPERATOR_ABORT = "operator_abort"
    LOCAL_COMMAND = "local_command"


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """Normalized result and execution status for one tool call."""

    result: AgentToolResultMessage
    malformed_arguments: bool = False
    interruption: ToolExecutionInterruption = ToolExecutionInterruption.SETTLED

    def __post_init__(self) -> None:
        if not isinstance(self.result, AgentToolResultMessage):
            raise TypeError(
                "ToolExecutionOutcome.result must be AgentToolResultMessage"
            )
        if not isinstance(self.malformed_arguments, bool):
            raise TypeError("ToolExecutionOutcome.malformed_arguments must be a bool")
        if not isinstance(self.interruption, ToolExecutionInterruption):
            raise TypeError(
                "ToolExecutionOutcome.interruption must be ToolExecutionInterruption"
            )


class ToolInterruptWaiter(Protocol):
    """Caller-owned wait policy for an interruptible tool worker."""

    def __call__(
        self,
        done_event: threading.Event,
        cancel_event: threading.Event,
        /,
    ) -> ToolExecutionInterruption: ...


@runtime_checkable
class AgentToolCapabilities(Protocol):
    """Canonical tool surface consumed by the reusable agent loop."""

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]: ...

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome: ...

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage: ...


class _InvocationOutputGate:
    """Keep one invocation's live-output callback within its execution lifetime."""

    def __init__(self, sink: Callable[[str], None] | None) -> None:
        self._sink = sink
        self._active = True
        self._lock = threading.Lock()

    @property
    def sink(self) -> Callable[[str], None] | None:
        return self.emit if self._sink is not None else None

    def emit(self, chunk: str) -> None:
        with self._lock:
            if not self._active or self._sink is None:
                return
            sink = self._sink
        sink(chunk)

    def deactivate(self) -> None:
        with self._lock:
            self._active = False


class _ExecutionOrder:
    """Record the first cancellation signal relative to worker completion."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._cancellation: int | None = None
        self._completion: int | None = None

    def record_cancellation(self) -> None:
        with self._lock:
            if self._cancellation is None:
                self._cancellation = self._next
                self._next += 1

    def record_completion(self) -> None:
        with self._lock:
            self._completion = self._next
            self._next += 1

    def completion_preceded_cancellation(self) -> bool:
        with self._lock:
            return self._completion is not None and (
                self._cancellation is None or self._completion < self._cancellation
            )


class _OrderedCancelEvent(threading.Event):
    """Cancellation event that records the first signal's execution order."""

    def __init__(self, execution_order: _ExecutionOrder) -> None:
        super().__init__()
        self._execution_order = execution_order

    def set(self) -> None:
        self._execution_order.record_cancellation()
        super().set()


class ToolExecutor:
    """Validate and synchronously execute one tool against a supplied registry."""

    DEFAULT_CANCEL_JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(
        self,
        registry: Mapping[str, ToolPort],
        cancel_join_timeout_seconds: float = DEFAULT_CANCEL_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(cancel_join_timeout_seconds, bool) or not isinstance(
            cancel_join_timeout_seconds, (int, float)
        ):
            raise TypeError("cancel_join_timeout_seconds must be a number")
        if cancel_join_timeout_seconds < 0:
            raise ValueError("cancel_join_timeout_seconds must not be negative")
        self._registry = registry
        self._cancel_join_timeout_seconds = float(cancel_join_timeout_seconds)

    def execute(
        self,
        call: AgentToolCall,
        context: ToolContext,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        """Execute ``call``, optionally on a worker observed by ``wait_for_interrupt``."""

        output_gate = _InvocationOutputGate(context.output_sink)
        invocation_context = replace(context, output_sink=output_gate.sink)
        try:
            if wait_for_interrupt is None:
                return self._execute_once(call, invocation_context)
            return self._execute_interruptibly(
                call,
                invocation_context,
                wait_for_interrupt,
                output_gate,
            )
        finally:
            output_gate.deactivate()

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
    ) -> AgentToolResultMessage:
        """Build a canonical pipy-owned error observation for ``call``."""

        return AgentToolResultMessage(
            tool_request_id=make_tool_request_id(),
            tool_name=call.tool_name,
            content=ProductContent(output_text),
            is_error=True,
            provider_correlation_id=call.provider_correlation_id,
        )

    def _execute_interruptibly(
        self,
        call: AgentToolCall,
        context: ToolContext,
        wait_for_interrupt: ToolInterruptWaiter,
        output_gate: _InvocationOutputGate,
    ) -> ToolExecutionOutcome:
        execution_order = _ExecutionOrder()
        cancel_event = _OrderedCancelEvent(execution_order)
        done_event = threading.Event()
        result_holder: list[ToolExecutionOutcome] = []
        error_holder: list[BaseException] = []
        cancellable_context = replace(context, cancel_event=cancel_event)

        def _worker() -> None:
            try:
                result_holder.append(self._execute_once(call, cancellable_context))
            # re-raised by the caller
            except BaseException as exc:  # pragma: no cover  # noqa: BLE001
                error_holder.append(exc)
            finally:
                execution_order.record_completion()
                done_event.set()

        worker = threading.Thread(target=_worker, name="pipy-tool-call", daemon=True)
        worker.start()
        interruption = self._wait_for_interruption(
            wait_for_interrupt,
            done_event,
            cancel_event,
            worker,
            output_gate,
        )
        if interruption is not ToolExecutionInterruption.SETTLED:
            cancel_event.set()
            completion_preceded_interruption = (
                execution_order.completion_preceded_cancellation()
            )
            output_gate.deactivate()
            if not completion_preceded_interruption:
                worker.join(timeout=self._cancel_join_timeout_seconds)
                return self._cancellation_outcome(call, interruption)
            worker.join(timeout=self._cancel_join_timeout_seconds)
        worker.join()
        if error_holder:
            raise error_holder[0]
        if result_holder:
            outcome = result_holder[0]
            return ToolExecutionOutcome(
                outcome.result,
                malformed_arguments=outcome.malformed_arguments,
                interruption=interruption,
            )
        if interruption is ToolExecutionInterruption.SETTLED:
            return ToolExecutionOutcome(self.error_result(call, "tool cancelled"))
        return self._cancellation_outcome(call, interruption)

    def _wait_for_interruption(
        self,
        waiter: ToolInterruptWaiter,
        done_event: threading.Event,
        cancel_event: _OrderedCancelEvent,
        worker: threading.Thread,
        output_gate: _InvocationOutputGate,
    ) -> ToolExecutionInterruption:
        try:
            interruption = waiter(done_event, cancel_event)
        except KeyboardInterrupt:
            cancel_event.set()
            return ToolExecutionInterruption.OPERATOR_ABORT
        except BaseException:  # cancel and re-raise waiter failures
            cancel_event.set()
            output_gate.deactivate()
            worker.join(timeout=self._cancel_join_timeout_seconds)
            raise
        if not isinstance(interruption, ToolExecutionInterruption):
            cancel_event.set()
            output_gate.deactivate()
            worker.join(timeout=self._cancel_join_timeout_seconds)
            raise TypeError(
                "tool interrupt waiter must return ToolExecutionInterruption"
            )
        return interruption

    def _cancellation_outcome(
        self,
        call: AgentToolCall,
        interruption: ToolExecutionInterruption,
    ) -> ToolExecutionOutcome:
        label = (
            "local command"
            if interruption is ToolExecutionInterruption.LOCAL_COMMAND
            else "escape"
        )
        return ToolExecutionOutcome(
            self.error_result(call, f"tool cancelled by {label}"),
            interruption=interruption,
        )

    def _execute_once(
        self,
        call: AgentToolCall,
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        tool = self._registry.get(call.tool_name)
        if tool is None:
            return ToolExecutionOutcome(
                self.error_result(call, f"unknown tool: {call.tool_name}"),
                malformed_arguments=True,
            )
        try:
            raw_arguments = json.loads(call.arguments_json.value)
        except json.JSONDecodeError as exc:
            return ToolExecutionOutcome(
                self.error_result(call, f"invalid arguments JSON: {exc.msg}"),
                malformed_arguments=True,
            )
        try:
            arguments = validate_arguments(
                tool_name=call.tool_name,
                schema=tool.definition.input_schema,
                arguments=raw_arguments,
            )
            request = ToolRequest(
                tool_request_id=make_tool_request_id(),
                tool_name=call.tool_name,
                arguments=arguments,
                provider_correlation_id=call.provider_correlation_id,
            )
            execution_result = tool.invoke(request, context)
        except ToolArgumentError as exc:
            return ToolExecutionOutcome(
                self.error_result(call, str(exc)),
                malformed_arguments=True,
            )
        if not isinstance(execution_result, ToolExecutionResult):
            raise TypeError(
                f"tool {call.tool_name!r} returned non-ToolExecutionResult value"
            )
        return ToolExecutionOutcome(
            AgentToolResultMessage(
                tool_request_id=execution_result.tool_request_id,
                tool_name=call.tool_name,
                content=ProductContent(execution_result.output_text),
                is_error=execution_result.is_error,
                provider_correlation_id=(
                    execution_result.provider_correlation_id
                    or call.provider_correlation_id
                ),
            )
        )
