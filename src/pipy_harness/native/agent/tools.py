"""Synchronous, UI-free execution for one canonical agent tool call."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Any, Literal, Protocol, runtime_checkable

from pipy_harness.native.agent.content import (
    ProductContent,
    TextContent,
    ToolOutput,
    ToolPartialResult,
)
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS, admit_tool_details
from pipy_harness.native.tools.base import (
    RETAINED_OUTPUT,
    RetainedOutputSnapshot,
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolOutputSink,
    ToolPort,
    ToolRequest,
    _RetainedOutputSink,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.foreign_schema import validate_foreign_arguments


class ToolExecutionInterruption(StrEnum):
    """Closed reasons why a caller stopped waiting for a tool invocation.

    SCRIPT_STOP and CALLER_STOP are waiter-only: the executor consumes them as
    an ordinary error observation with typed cancelled evidence, never an
    operator interruption in canonical policy state. CALLER_STOP ends a call
    another tool made: its ``signal``, or the end of the calling tool.
    """

    SETTLED = "settled"
    OPERATOR_ABORT = "operator_abort"
    LOCAL_COMMAND = "local_command"
    SCRIPT_STOP = "script_stop"
    CALLER_STOP = "caller_stop"


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """Normalized result and execution status for one tool call."""

    result: AgentToolResultMessage
    malformed_arguments: bool = False
    interruption: ToolExecutionInterruption = ToolExecutionInterruption.SETTLED
    # Execution evidence, independent of policy/operator interruption ownership.
    cancelled: bool = False
    # How long the tool itself ran (Pi ``durationMs`` around ``execute()``),
    # on a monotonic clock; None when the tool never started (unknown tool,
    # invalid arguments, cancelled before it ran). Timing evidence, so it
    # takes no part in equality.
    execution_seconds: float | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.result, AgentToolResultMessage):
            raise TypeError(
                "ToolExecutionOutcome.result must be AgentToolResultMessage"
            )
        if type(self.cancelled) is not bool:
            raise TypeError("ToolExecutionOutcome.cancelled must be bool")
        if self.cancelled and not self.result.is_error:
            raise ValueError("cancelled execution requires an error result")
        if not isinstance(self.malformed_arguments, bool):
            raise TypeError("ToolExecutionOutcome.malformed_arguments must be a bool")
        if not isinstance(self.interruption, ToolExecutionInterruption):
            raise TypeError(
                "ToolExecutionOutcome.interruption must be ToolExecutionInterruption"
            )
        seconds = self.execution_seconds
        if seconds is not None and (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError(
                "ToolExecutionOutcome.execution_seconds must be finite, "
                "nonnegative or None"
            )


class ToolInterruptWaiter(Protocol):
    """Caller-owned wait policy for an interruptible tool worker."""

    def __call__(
        self,
        done_event: threading.Event,
        cancel_event: threading.Event,
        /,
    ) -> ToolExecutionInterruption: ...


class ToolReadinessWaiter(Protocol):
    """Product-owned wait that precedes an admitted tool call.

    Called on the loop's thread after the ``tool_call`` policy admitted the
    call and before it runs, with the waiter the call itself would get. It
    returns ``SETTLED`` when the call may run; any other value cancels the
    call the way an interruption of the running tool would.
    """

    def __call__(
        self,
        call: AgentToolCall,
        wait_for_interrupt: ToolInterruptWaiter | None,
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
        output_sink: ToolOutputSink | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        """Run ``call``; with ``arguments``, run those instead of its own.

        See :meth:`ToolExecutor.execute`.
        """
        ...

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage: ...

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        """The call's arguments as its tool will run them, before any hook.

        See :meth:`ToolExecutor.validated_arguments`.
        """
        ...


def _seconds_since(started: list[float]) -> float | None:
    """How long ago the tool started, or None when it did not."""
    return monotonic() - started[0] if started else None


def cancelled_tool_outcome(
    error_result: Callable[[AgentToolCall, str], AgentToolResultMessage],
    call: AgentToolCall,
    interruption: ToolExecutionInterruption,
) -> ToolExecutionOutcome:
    """The outcome of a call that ``interruption`` cancelled before it finished."""

    if interruption is ToolExecutionInterruption.SCRIPT_STOP:
        return ToolExecutionOutcome(
            error_result(call, "tool stopped by script backend"),
            cancelled=True,
        )
    if interruption is ToolExecutionInterruption.CALLER_STOP:
        return ToolExecutionOutcome(
            error_result(call, "tool cancelled by its caller"),
            cancelled=True,
        )
    label = (
        "local command"
        if interruption is ToolExecutionInterruption.LOCAL_COMMAND
        else "escape"
    )
    return ToolExecutionOutcome(
        error_result(call, f"tool cancelled by {label}"),
        interruption=interruption,
        cancelled=True,
    )


class _InvocationOutputGate(_RetainedOutputSink):
    """Keep one invocation's live-output callback within its execution lifetime."""

    def __init__(
        self, sink: ToolOutputSink | None, *, retain: bool = False, permits: int = 16
    ) -> None:
        self._sink = sink
        self._active = True
        self._condition = threading.Condition()
        self._retain = retain
        self._permits = permits
        self._active_callbacks = 0
        self._callback_threads: dict[int, int] = {}
        self._capture_failed = False

    @property
    def sink(self) -> ToolOutputSink | None:
        with self._condition:
            present, retained = self._sink is not None, self._retain
        return (self if retained else self.emit) if present else None

    def __call__(self, output: ToolOutput) -> None:
        self.dispatch(lambda: output)

    def dispatch(self, capture: Callable[[], ToolOutput]) -> None:
        """Admit before capture; a void return is never a persistence ACK."""
        try:
            self._dispatch(capture)
        except Exception as error:  # noqa: BLE001 - also contain admission/bridge failures
            self._capture_failure(error)

    def _dispatch(self, capture: Callable[[], ToolOutput]) -> None:
        self._run_admitted(lambda sink: sink(capture()))

    def _run_admitted(self, callback: Callable[[ToolOutputSink], object]) -> bool:
        """Keep explicit reporter barriers in the same callback ownership."""
        thread = threading.get_ident()
        with self._condition:
            if not self._active or self._sink is None:
                return False
            refused = self._active_callbacks >= self._permits
            if not refused:
                self._active_callbacks += 1
                self._callback_threads[thread] = (
                    self._callback_threads.get(thread, 0) + 1
                )
                sink = self._sink
        if refused:
            self._refused_capacity()
            return False
        try:
            callback(sink)
        finally:
            with self._condition:
                self._active_callbacks -= 1
                depth = self._callback_threads[thread] - 1
                if depth:
                    self._callback_threads[thread] = depth
                else:
                    del self._callback_threads[thread]
                self._condition.notify_all()
        return True

    def _capture_failure(self, error: Exception) -> None:
        # The durable subclass retains fixed categories, never exception text.
        with self._condition:
            self._capture_failed = True

    def _refused_capacity(self) -> None:
        with self._condition:
            self._capture_failed = True

    def emit(self, output: ToolOutput) -> None:
        with self._condition:
            if not self._active or self._sink is None:
                return
            sink = self._sink
        sink(output)

    def deactivate(self) -> None:
        retired = None
        with self._condition:
            if self._retain and self._callback_threads.get(threading.get_ident(), 0):
                raise RuntimeError("Cannot drain output from its admitted callback")
            self._active = False
            if self._retain:
                while self._active_callbacks:
                    self._condition.wait()
                retired, self._sink = self._sink, None
        del retired  # Foreign finalizers must never run under the Condition.

    @property
    def retaining(self) -> bool:
        with self._condition:
            return self._retain

    def retained_output(self) -> RetainedOutputSnapshot:
        """No retained window: the empty snapshot."""
        return RetainedOutputSnapshot()

    def report_details(self, value: object) -> None:
        """No reporter: reported details are dropped."""

    def report_diagnostic(
        self, *, severity: Any, message: Any, code: Any = None
    ) -> None:
        """No reporter: reported diagnostics are dropped."""

    def settle(self, result: ToolExecutionResult) -> ToolExecutionResult:
        """The tool's final result in this gate's host (P16).

        A gate without a retained window resolves an omitted output to empty
        content; ``RetainedOutputWindow`` and the durable reporter apply the
        retained output, details and diagnostics.
        """
        if result.output_text is RETAINED_OUTPUT:
            return replace(result, output_text="")
        return result


DIAGNOSTIC_SEVERITIES = ("info", "warning", "error")
# Reported diagnostics become model text; keep them within what a result holds
# beside the 50 KB window, counted as rendered lines, exactly as the durable
# host counts them (``tool_progress_values.DIAGNOSTIC_CHARACTERS``).
WINDOW_BYTES = 50 * 1024
DIAGNOSTIC_CHARACTERS = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH - WINDOW_BYTES - 1024


def render_diagnostic(severity: str, message: str, code: str | None) -> str:
    """One diagnostic as model text, as the durable projection renders it."""
    return "[" + severity + ("" if code is None else " (" + code + ")") + "] " + message


def truncated_diagnostic(
    dropped_lines: int, dropped_bytes: int
) -> tuple[Literal["warning"], str, str]:
    """Pi's ``truncated`` harness diagnostic for a window that keeps the end."""
    return (
        "warning",
        f"Output truncated to its end: {dropped_lines} lines, "
        f"{dropped_bytes} bytes dropped",
        "truncated",
    )


# The durable projection's notice for an output it bounded (``project_tool_output``).
TRUNCATED_NOTICE = "[tool output truncated]"


def _harness(notes: Sequence[tuple[str, str, str | None]], bounded: bool) -> str:
    """Diagnostics as the durable projection renders them after the output."""
    lines = [render_diagnostic(*note) for note in notes]
    if bounded:
        lines.insert(0, TRUNCATED_NOTICE)
    return "<harness>\n" + "\n".join(lines) + "\n</harness>"


class RetainedOutputWindow(_InvocationOutputGate):
    """The ordinary host's non-committing retained output of one invocation (P16).

    It keeps what the durable reporter keeps, without committing it: the last
    50 KB / 2000 lines of the live output (a ``ToolPartialResult`` replaces it,
    a string appends to it), the details last given to ``report_details`` and
    the reported diagnostics. Unlike the durable reporter, a partial result's
    details are not reported details here, as in Pi's agent. Live output still reaches the context's sink, if any. The tool
    always gets a sink, so its output is retained without a live consumer.
    """

    def __init__(self, sink: ToolOutputSink | None) -> None:
        # Imported here so a headless import of the agent stays tool-free.
        from pipy_harness.native.tools.truncate import OutputTail

        super().__init__(sink)
        self._tail = OutputTail()
        self._details: Any = NO_TOOL_DETAILS
        self._diagnostics: list[tuple[str, str, str | None]] = []
        self._diagnostic_characters = 0

    @property
    def sink(self) -> ToolOutputSink:
        return self

    def __call__(self, output: ToolOutput) -> None:
        self._observe(output)

    def dispatch(self, capture: Callable[[], ToolOutput]) -> None:
        with self._condition:
            if not self._active:
                return
        self._observe(capture())

    def _observe(self, output: ToolOutput) -> None:
        with self._condition:
            if not self._active:
                return
            try:
                if type(output) is ToolPartialResult:
                    # A partial result replaces the window. Its details are not
                    # reported details: Pi's agent never falls back to them.
                    self._tail = type(self._tail)().append(output.content)
                elif type(output) is str:
                    self._tail = self._tail.append(output)
            except ValueError:
                pass  # not retainable text; the live sink still gets it
            sink = self._sink
        if sink is not None:
            sink(output)

    def retained_output(self) -> RetainedOutputSnapshot:
        with self._condition:
            tail = self._tail
        return RetainedOutputSnapshot(tail.text, tail.dropped_bytes > 0)

    def report_details(self, value: object) -> None:
        # Owned now, so the tool may go on mutating its own value; invalid
        # details raise to the tool (finite JSON only).
        details = admit_tool_details(value)
        with self._condition:
            if self._active:
                self._details = details

    def report_diagnostic(
        self, *, severity: str, message: str, code: str | None = None
    ) -> None:
        if severity not in DIAGNOSTIC_SEVERITIES or type(message) is not str:
            raise ValueError("Invalid tool diagnostic")
        if code is not None and type(code) is not str:
            raise ValueError("Invalid tool diagnostic code")
        size = len(render_diagnostic(severity, message, code)) + 1
        if size > DIAGNOSTIC_CHARACTERS:
            raise ValueError("Tool diagnostics exceed their budget")
        try:  # model text: refuse lone surrogates, as the durable host does
            for value in (message, code or ""):
                for start in range(0, len(value), 1024):
                    value[start : start + 1024].encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid tool diagnostic") from None
        with self._condition:
            if not self._active:
                return
            if self._diagnostic_characters + size > DIAGNOSTIC_CHARACTERS:
                raise ValueError("Tool diagnostics exceed their budget")
            self._diagnostic_characters += size
            self._diagnostics.append((severity, message, code))

    def settle(self, result: ToolExecutionResult) -> ToolExecutionResult:
        """Pi durable ``finalResult`` in the ordinary host.

        An omitted output is the retained text (empty when nothing was
        retained), with the ``truncated`` diagnostic when the window dropped
        bytes; omitted details are the last reported ones; the reported
        diagnostics come first. Diagnostics are appended to the model text as
        the durable projection renders them.
        """
        with self._condition:
            tail, details = self._tail, self._details
            notes = list(self._diagnostics)
        text = result.output_text
        if text is RETAINED_OUTPUT:
            text = tail.text
            if tail.dropped_bytes > 0:
                notes.append(
                    truncated_diagnostic(tail.dropped_lines, tail.dropped_bytes)
                )
        changes: dict[str, Any] = {"output_text": text}
        if result.details is NO_TOOL_DETAILS and details is not NO_TOOL_DETAILS:
            changes["details"] = details
        if not notes:
            return replace(result, **changes)
        blocks = result.blocks
        bounded = len(text) + 1 + len(_harness(notes, False)) > (
            ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        )
        if bounded:
            # Only a tool that reports diagnostics gets here: its explicit
            # output is bounded to the window's end with the notice the
            # durable projection gives such an output, so both hosts agree.
            from pipy_harness.native.tools.truncate import truncate_tail

            text = truncate_tail(text, max_bytes=WINDOW_BYTES).content
            if blocks:  # images first, then the bounded text
                blocks = (
                    *(block for block in blocks if type(block) is not TextContent),
                    TextContent(text),
                )
        suffix = _harness(notes, bounded)
        changes["output_text"] = text + ("\n" if text else "") + suffix
        if blocks:
            changes["blocks"] = (*blocks, TextContent(suffix))
        return replace(result, **changes)


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
        *,
        arguments: dict[str, Any] | None = None,
        retained_output: _InvocationOutputGate | None = None,
    ) -> ToolExecutionOutcome:
        """Execute ``call``, optionally on a worker observed by ``wait_for_interrupt``.

        Without ``arguments`` the call's own arguments are parsed, prepared
        and validated (:meth:`validated_arguments`). With them, the call runs
        those arguments, which :meth:`validated_arguments` produced and the
        ``tool_call`` hooks may have changed: they are validated again but
        not prepared again, so the tool's ``prepare_arguments`` runs once per
        call (Pi runs ``prepareArguments`` once, before the hooks).
        """

        if retained_output is not None and (
            wait_for_interrupt is not None or not retained_output.retaining
        ):
            raise ValueError("Retained output requires the physically owned executor")
        output_gate = retained_output or RetainedOutputWindow(context.output_sink)
        invocation_context = replace(
            context,
            output_sink=output_gate.sink,
            progress=output_gate,
        )
        try:
            if wait_for_interrupt is None:
                return self._execute_once(call, invocation_context, arguments)
            return self._execute_interruptibly(
                call,
                invocation_context,
                arguments,
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

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        """Pi ``prepareToolCall`` up to the hooks: the call's arguments
        parsed, put through the tool's ``prepare_arguments`` and validated
        against its schema, as a detached copy the ``tool_call`` hooks may
        change. An unknown tool, arguments that are not JSON, a
        ``prepare_arguments`` that raises ``ToolArgumentError`` and arguments
        the schema refuses give the malformed outcome the call settles with
        instead.
        """

        tool = self._registry.get(call.tool_name)
        if tool is None:
            return ToolExecutionOutcome(
                self.error_result(call, f"Tool {call.tool_name} not found"),
                malformed_arguments=True,
            )
        try:
            raw_arguments = json.loads(call.arguments_json.value)
        except json.JSONDecodeError as exc:
            return ToolExecutionOutcome(
                self.error_result(call, f"invalid arguments JSON: {exc.msg}"),
                malformed_arguments=True,
            )
        # Pi's `prepareArguments` runs before schema validation; what it
        # raises settles the call as an error, as validation errors do.
        prepare = getattr(tool, "prepare_arguments", None)
        if callable(prepare):
            try:
                raw_arguments = prepare(raw_arguments)
            except ToolArgumentError as exc:
                return ToolExecutionOutcome(
                    self.error_result(call, str(exc)),
                    malformed_arguments=True,
                )
        return self._checked_arguments(call, tool, raw_arguments)

    def _checked_arguments(
        self, call: AgentToolCall, tool: ToolPort, arguments: object
    ) -> dict[str, Any] | ToolExecutionOutcome:
        definition = tool.definition
        validate = (
            validate_foreign_arguments
            if definition.schema_mode == "foreign"
            else validate_arguments
        )
        try:
            return validate(
                tool_name=call.tool_name,
                schema=definition.input_schema,
                arguments=arguments,
            )
        except ToolArgumentError as exc:
            return ToolExecutionOutcome(
                self.error_result(call, str(exc)),
                malformed_arguments=True,
            )

    def _execute_interruptibly(
        self,
        call: AgentToolCall,
        context: ToolContext,
        arguments: dict[str, Any] | None,
        wait_for_interrupt: ToolInterruptWaiter,
        output_gate: _InvocationOutputGate,
    ) -> ToolExecutionOutcome:
        execution_order = _ExecutionOrder()
        cancel_event = _OrderedCancelEvent(execution_order)
        done_event = threading.Event()
        result_holder: list[ToolExecutionOutcome] = []
        error_holder: list[BaseException] = []
        # When the tool itself started, so a call cancelled while it runs
        # still reports how long it ran.
        started: list[float] = []
        cancellable_context = replace(context, cancel_event=cancel_event)

        def _worker() -> None:
            try:
                result_holder.append(
                    self._execute_once(call, cancellable_context, arguments, started)
                )
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
                # A tool that finished its cleanup during the join ran that
                # long (Pi times execute() until it settles); one still
                # running ran at least until now.
                ran = (
                    result_holder[0].execution_seconds
                    if result_holder
                    else _seconds_since(started)
                )
                return replace(
                    self._cancellation_outcome(call, interruption),
                    execution_seconds=ran,
                )
            worker.join(timeout=self._cancel_join_timeout_seconds)
        worker.join()
        if error_holder:
            raise error_holder[0]
        if result_holder:
            outcome = result_holder[0]
            return ToolExecutionOutcome(
                outcome.result,
                malformed_arguments=outcome.malformed_arguments,
                interruption=(
                    ToolExecutionInterruption.SETTLED
                    if interruption
                    in {
                        ToolExecutionInterruption.SCRIPT_STOP,
                        ToolExecutionInterruption.CALLER_STOP,
                    }
                    else interruption
                ),
                execution_seconds=outcome.execution_seconds,
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

    @staticmethod
    def _settle(
        context: ToolContext, result: ToolExecutionResult
    ) -> ToolExecutionResult:
        """Apply the invocation reporter's final-result rules (P16)."""
        settle = getattr(context.progress, "settle", None)
        if callable(settle):
            settled = settle(result)
            if isinstance(settled, ToolExecutionResult):
                return settled
        if result.output_text is RETAINED_OUTPUT:
            return replace(result, output_text="")
        return result

    def _cancellation_outcome(
        self,
        call: AgentToolCall,
        interruption: ToolExecutionInterruption,
    ) -> ToolExecutionOutcome:
        return cancelled_tool_outcome(self.error_result, call, interruption)

    def _execute_once(
        self,
        call: AgentToolCall,
        context: ToolContext,
        given: dict[str, Any] | None = None,
        started: list[float] | None = None,
    ) -> ToolExecutionOutcome:
        tool = self._registry.get(call.tool_name)
        if given is None or tool is None:
            arguments = self.validated_arguments(call)
        else:
            arguments = self._checked_arguments(call, tool, given)
        if isinstance(arguments, ToolExecutionOutcome):
            return arguments
        assert tool is not None  # an unknown tool is a malformed outcome
        request = ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name=call.tool_name,
            arguments=arguments,
            provider_correlation_id=call.provider_correlation_id,
        )
        began = monotonic()
        if started is not None:
            started.append(began)
        try:
            execution_result = tool.invoke(request, context)
        except ToolArgumentError as exc:
            # The tool ran and refused its arguments (Pi: `execute()` threw),
            # so the outcome keeps how long it ran.
            return ToolExecutionOutcome(
                self.error_result(call, str(exc)),
                malformed_arguments=True,
                execution_seconds=monotonic() - began,
            )
        ran = monotonic() - began
        if not isinstance(execution_result, ToolExecutionResult):
            raise TypeError(
                f"tool {call.tool_name!r} returned non-ToolExecutionResult value"
            )
        execution_result = self._settle(context, execution_result)
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
                details=execution_result.details,
                nested_call_evidence=execution_result.nested_call_evidence,
                structured_content=execution_result.structured_content,
                blocks=execution_result.blocks,
                terminate=execution_result.terminate,
            ),
            execution_seconds=ran,
        )
