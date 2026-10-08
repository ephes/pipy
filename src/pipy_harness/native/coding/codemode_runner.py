"""Internal CPython-on-WASI composition; no public tool registration (CM1 T6b)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_calls import NestedToolCallService
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.codemode.host import run_script
from pipy_harness.native.codemode.outcome import (
    CallResult,
    ErrorKind,
    ScriptError,
    ScriptLimits,
    ScriptOutcome,
)
from pipy_harness.native.codemode.result import format_result
from pipy_harness.native.codemode.runtime import CodemodePaths
from pipy_harness.native.tools.base import (
    make_tool_request_id,
    validate_arguments,
)

# Internal argument contract only. T8 owns the public definition and spilling.
_CODE_SCHEMA = {
    "type": "object",
    "properties": {"code": {"type": "string"}},
    "required": ["code"],
    "additionalProperties": False,
}


class _RunControl:
    """Fresh per parent. Session state stays here; monitor only signals events."""

    def __init__(self, waiter: ToolInterruptWaiter | None) -> None:
        self.waiter = waiter
        self.cancel = threading.Event()
        self.stop = threading.Event()
        self.interruption = ToolExecutionInterruption.SETTLED

    def activity(self, done: threading.Event, cancel: threading.Event) -> None:
        interruption = self._wait(done, cancel)
        if interruption in {
            ToolExecutionInterruption.OPERATOR_ABORT,
            ToolExecutionInterruption.LOCAL_COMMAND,
        }:
            self.interruption = interruption
            self.cancel.set()

    def _wait(
        self, done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        try:
            if self.waiter is None:
                done.wait()
                return ToolExecutionInterruption.SETTLED
            interruption = self.waiter(done, cancel)
            if type(interruption) is not ToolExecutionInterruption:
                raise TypeError(
                    "tool interrupt waiter must return ToolExecutionInterruption"
                )
            return interruption
        except KeyboardInterrupt:
            cancel.set()
            return ToolExecutionInterruption.OPERATOR_ABORT

    def nested(
        self, done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        # The existing waiter must wake for either tool completion or backend
        # termination. This monitor reads no service/canonical/session state.
        wake = threading.Event()
        monitor_stop = threading.Event()

        def monitor() -> None:
            while not monitor_stop.wait(0.01):
                if self.stop.is_set():
                    cancel.set()
                    wake.set()
                    return
                if done.is_set():
                    wake.set()
                    return

        thread = threading.Thread(
            target=monitor, name="codemode-tool-stop", daemon=True
        )
        thread.start()
        try:
            interruption = self._wait(wake, cancel)
            if interruption in {
                ToolExecutionInterruption.OPERATOR_ABORT,
                ToolExecutionInterruption.LOCAL_COMMAND,
            }:
                self.interruption = interruption
                self.cancel.set()
                return interruption
            if self.stop.is_set():
                cancel.set()
                return ToolExecutionInterruption.SCRIPT_STOP
            return ToolExecutionInterruption.SETTLED
        finally:
            monitor_stop.set()
            thread.join(timeout=1)


@dataclass(frozen=True, slots=True)
class CodemodeCompositeRunner:
    """Optional product runner, using the canonical admitted-child service."""

    limits: ScriptLimits = ScriptLimits()
    paths: CodemodePaths | None = None

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        def result(
            text: str,
            *,
            error: bool,
            malformed: bool = False,
            interruption: ToolExecutionInterruption = ToolExecutionInterruption.SETTLED,
        ) -> ToolExecutionOutcome:
            return ToolExecutionOutcome(
                AgentToolResultMessage(
                    tool_request_id=make_tool_request_id(),
                    tool_name=call.tool_name,
                    content=ProductContent(text),
                    is_error=error,
                    provider_correlation_id=call.provider_correlation_id,
                ),
                malformed_arguments=malformed,
                interruption=interruption,
            )

        try:
            arguments = validate_arguments(
                tool_name=call.tool_name,
                schema=_CODE_SCHEMA,
                arguments=json.loads(call.arguments_json.value),
            )
        except ValueError as exc:
            return result(str(exc), error=True, malformed=True)
        control = _RunControl(tool_waiter)

        def child(name: str, arguments_json: str) -> CallResult:
            outcome = service.call(name, arguments_json, waiter=control.nested)
            if outcome.interruption is not None:
                control.interruption = outcome.interruption
                control.cancel.set()
                service.close()
            return CallResult(
                ok=not outcome.result.is_error,
                text=outcome.result.content.value,
                cancelled=outcome.interruption is not None,
            )

        try:
            outcome = run_script(
                arguments["code"],
                call_tool=child,
                tool_names=service.eligible_names(),
                limits=self.limits,
                paths=self.paths,
                cancel=control.cancel,
                tool_stop=control.stop,
                activity_waiter=control.activity,
            )
            formatted = format_result(outcome)
            return result(
                formatted.text,
                error=formatted.is_error,
                interruption=control.interruption,
            )
        except Exception as exc:  # noqa: BLE001 - canonical failures are retained by the service
            return result(
                format_result(
                    ScriptOutcome(
                        ScriptError(
                            ErrorKind.SANDBOX,
                            f"{type(exc).__name__}: {exc}",
                        )
                    )
                ).text,
                error=True,
                interruption=control.interruption,
            )
        finally:
            service.close()
