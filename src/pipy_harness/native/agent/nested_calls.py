"""Transient, session-thread-owned calls under one admitted composite parent."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from time import monotonic
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_record import bounded_arguments, finite_duration
from pipy_harness.native.agent.nested_status import NestedCallStatus as NestedCallStatus
from pipy_harness.native.agent.request import (
    validate_agent_tool_call,
    validate_agent_tool_result_message,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)

BUILTIN_NESTED_NAMES = frozenset(
    {"read", "ls", "grep", "find", "write", "edit", "bash"}
)


@dataclass(frozen=True, slots=True)
class NestedToolCallOutcome:
    nested_call: AgentToolCall
    result: AgentToolResultMessage
    status: NestedCallStatus
    interruption: ToolExecutionInterruption | None = None
    duration_seconds: float = 0.0
    arguments_bytes: int | None = None

    def __post_init__(self) -> None:
        if not finite_duration(self.duration_seconds):
            raise ValueError("nested duration must be finite and nonnegative")
        validate_agent_tool_call(self.nested_call)
        validate_agent_tool_result_message(self.result)
        AgentToolResultMessage.__post_init__(self.result)
        if (
            self.result.provider_correlation_id
            != self.nested_call.provider_correlation_id
            or self.result.tool_name != self.nested_call.tool_name
        ):
            raise ValueError("nested result must match its call")
        if type(self.status) is not NestedCallStatus:
            raise TypeError("status must be NestedCallStatus")
        if (
            self.interruption is not None
            and type(self.interruption) is not ToolExecutionInterruption
        ):
            raise TypeError("interruption must be ToolExecutionInterruption or None")
        if self.status is NestedCallStatus.INTERRUPTED:
            if self.interruption not in {
                ToolExecutionInterruption.OPERATOR_ABORT,
                ToolExecutionInterruption.LOCAL_COMMAND,
            }:
                raise ValueError("interrupted outcome requires an interruption")
        elif self.interruption is not None:
            raise ValueError("only interrupted outcomes carry interruption")


class NestedToolEligibility(Protocol):
    """Read builtin identities from the already frozen execution projection."""

    def eligible_names(self) -> frozenset[str]: ...


@runtime_checkable
class CompositeToolEligibility(Protocol):
    """Optional frozen product identity guard; separate from tool capabilities."""

    def composite_enabled(self) -> bool: ...


class AgentCompositeToolRunner(Protocol):
    """Optional internal port; invoked only after parent admission and hooks."""

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome: ...


class NestedToolCallService:
    """Sequential parent capability. Only its creating thread reads/writes state.

    Records are transient observations, capped at 256 calls, 8 KiB arguments
    per call and 32 KiB total. Oversized arguments are replaced by their byte count; excess calls are dropped.
    Wrong-thread refusal reads no mutable fields and retains no evidence.
    """

    def __init__(
        self,
        parent: AgentToolCall,
        eligible_names: frozenset[str],
        settle: Callable[[AgentToolCall], NestedToolCallOutcome],
        error_result: Callable[[AgentToolCall, str], AgentToolResultMessage],
        advertised_names: frozenset[str] | None = None,
        settle_with_waiter: Callable[
            [AgentToolCall, ToolInterruptWaiter], NestedToolCallOutcome
        ]
        | None = None,
    ) -> None:
        self._parent_id = parent.provider_correlation_id
        self._thread = threading.current_thread()
        self._eligible = eligible_names & BUILTIN_NESTED_NAMES
        self._advertised = (
            self._eligible
            if advertised_names is None
            else self._eligible & advertised_names
        )
        self._settle = settle
        self._settle_with_waiter = settle_with_waiter
        self._error_result = error_result
        self._closed = False
        self._busy = False
        self._next = 0
        self._records: list[NestedToolCallOutcome] = []
        self._argument_bytes = 0
        self._complete = True
        self._failure: BaseException | None = None
        self._interruption: ToolExecutionInterruption | None = None

    def _require_thread(self) -> None:
        if threading.current_thread() is not self._thread:
            raise RuntimeError("nested service belongs to the session thread")

    def close(self) -> None:
        self._require_thread()
        self._closed = True

    def records(self) -> tuple[NestedToolCallOutcome, ...]:
        self._require_thread()
        return tuple(self._records)

    @property
    def interruption(self) -> ToolExecutionInterruption | None:
        """First child interruption, independent of bounded evidence retention."""
        self._require_thread()
        return self._interruption

    def raise_pipeline_failure(self) -> None:
        """Preserve canonical callback/executor failures across the runner boundary."""
        self._require_thread()
        if self._failure is not None:
            raise self._failure

    def eligible_names(self) -> frozenset[str]:
        self._require_thread()
        return self._advertised

    def call(
        self,
        tool_name: str,
        arguments_json: str,
        /,
        *,
        waiter: ToolInterruptWaiter | None = None,
    ) -> NestedToolCallOutcome:
        if threading.current_thread() is not self._thread:
            call = AgentToolCall(
                f"{self._parent_id}/0", tool_name, ProductContent(arguments_json)
            )
            # Build locally: the product error factory may itself access session state.
            result = AgentToolResultMessage(
                tool_request_id="pipy-tool-thread-refused",
                tool_name=tool_name,
                content=ProductContent("nested call refused: wrong thread"),
                is_error=True,
                provider_correlation_id=call.provider_correlation_id,
            )
            return NestedToolCallOutcome(call, result, NestedCallStatus.REFUSED)
        self._next += 1
        call = AgentToolCall(
            f"{self._parent_id}/{self._next}", tool_name, ProductContent(arguments_json)
        )
        if self._closed or self._busy or tool_name not in self._eligible:
            return NestedToolCallOutcome(
                call,
                self._error_result(call, "nested call refused"),
                NestedCallStatus.REFUSED,
            )
        self._busy = True
        started = monotonic()
        try:
            if waiter is not None:
                if self._settle_with_waiter is None:
                    raise RuntimeError("nested waiter override is unavailable")
                outcome = self._settle_with_waiter(call, waiter)
            else:
                outcome = self._settle(call)
        except BaseException as exc:
            self._failure = exc
            self._closed = True
            try:
                self._retain(
                    NestedToolCallOutcome(
                        call,
                        self._error_result(call, "nested pipeline did not finish"),
                        NestedCallStatus.UNFINISHED,
                        duration_seconds=monotonic() - started,
                    )
                )
            except BaseException:  # noqa: BLE001 - preserve canonical failure even during evidence capture
                # Secondary evidence failures must not replace the original.
                self._complete = False
            raise
        finally:
            self._busy = False
        if outcome.status is NestedCallStatus.INTERRUPTED:
            if self._interruption is None:
                self._interruption = outcome.interruption
            self.close()
        self._retain(outcome)
        return outcome

    def _retain(self, outcome: NestedToolCallOutcome) -> None:
        if len(self._records) >= 256:
            self._complete = False
            return
        raw = outcome.nested_call.arguments_json.value
        size = len(raw.encode("utf-8", errors="surrogatepass"))
        arguments = bounded_arguments(raw)
        if arguments is None or self._argument_bytes + size > 32768:
            self._complete = False
            call = replace(outcome.nested_call, arguments_json=ProductContent("{}"))
            omitted = size
        else:
            self._argument_bytes += size
            call = outcome.nested_call
            omitted = None
        error = outcome.result.content.value if outcome.result.is_error else ""
        if len(error) > 500:
            self._complete = False
        # Retain no nested result output/details, only the bounded error field.
        self._records.append(
            replace(
                outcome,
                nested_call=call,
                arguments_bytes=omitted,
                result=replace(
                    outcome.result, content=ProductContent(error[:500]), details=None
                ),
            )
        )

    def durable_record(self) -> dict[str, object]:
        """Fresh independent snapshot; canonical state never leaves this thread."""
        self._require_thread()
        calls: list[dict[str, object]] = []
        for outcome in self._records:
            status = (
                "unfinished"
                if outcome.status is NestedCallStatus.UNFINISHED
                else "cancelled"
                if outcome.status
                in {NestedCallStatus.CANCELLED, NestedCallStatus.INTERRUPTED}
                else "error"
                if outcome.result.is_error
                else "ok"
            )
            row: dict[str, object] = {
                "id": outcome.nested_call.provider_correlation_id,
                "name": outcome.nested_call.tool_name,
                "status": status,
                "durationMs": outcome.duration_seconds * 1000,
            }
            if outcome.arguments_bytes is None:
                row["arguments"] = json.loads(outcome.nested_call.arguments_json.value)
            else:
                row["argumentsBytes"] = outcome.arguments_bytes
            if outcome.result.is_error:
                row["error"] = outcome.result.content.value
            calls.append(row)
        return {"calls": calls, "complete": self._complete}
