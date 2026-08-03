"""Synchronous, UI-free execution for one native provider turn."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent._validation import require_non_negative_int
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import AssistantReasoningDelta, AssistantTextDelta
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink


class ProviderTurnInterruption(StrEnum):
    """Closed results returned by the caller-owned provider wait policy."""

    SETTLED = "settled"
    OPERATOR_ABORT = "operator_abort"
    STEERING = "steering"
    LOCAL_COMMAND = "local_command"


@dataclass(frozen=True, slots=True)
class ProviderTurnDeltaPolicy:
    """Select which provider delta channels receive canonical sinks.

    The canonical agent loop uses both channels. Compatibility runtimes may
    disable a channel only when their established provider contract was
    buffered or text-only; disabled channels are passed to the provider as
    ``None`` rather than as no-op callables.
    """

    text: bool = True
    reasoning: bool = True

    def __post_init__(self) -> None:
        if type(self.text) is not bool:
            raise TypeError("ProviderTurnDeltaPolicy.text must be an exact bool")
        if type(self.reasoning) is not bool:
            raise TypeError("ProviderTurnDeltaPolicy.reasoning must be an exact bool")


_DEFAULT_PROVIDER_TURN_DELTA_POLICY: ProviderTurnDeltaPolicy = ProviderTurnDeltaPolicy()


@dataclass(frozen=True, slots=True)
class ProviderTurnOutcome:
    """Exactly one completed provider result or typed cancellation reason."""

    result: ProviderResult | None = None
    cancellation_reason: AgentCancellationReason | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.cancellation_reason is None):
            raise ValueError(
                "provider turn outcome requires exactly one result or cancellation"
            )
        if self.result is not None and not isinstance(self.result, ProviderResult):
            raise TypeError("ProviderTurnOutcome.result must be ProviderResult")
        if self.cancellation_reason is not None and not isinstance(
            self.cancellation_reason, AgentCancellationReason
        ):
            raise TypeError(
                "ProviderTurnOutcome.cancellation_reason must be "
                "AgentCancellationReason"
            )


class ProviderTurnWaiter(Protocol):
    """Caller-owned wait policy for an interruptible provider worker."""

    def __call__(
        self,
        done_event: threading.Event,
        cancel_event: threading.Event,
        /,
    ) -> ProviderTurnInterruption: ...


@runtime_checkable
class _AbortCallbackSignal(Protocol):
    """External abort signal that can synchronously bridge acceptance."""

    def is_set(self) -> bool: ...

    def register_cancel_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]: ...


class _StartGatedProvider:
    """Start a callback-capable RPC provider after abort registration."""

    def __init__(self, provider: ProviderPort, start_event: threading.Event) -> None:
        self._provider = provider
        self._start_event = start_event

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def model_id(self) -> str:
        return self._provider.model_id

    @property
    def supports_tool_calls(self) -> bool:
        return self._provider.supports_tool_calls

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        self._start_event.wait()
        return self._provider.complete(
            request,
            stream_sink=stream_sink,
            reasoning_sink=reasoning_sink,
            cancel_token=cancel_token,
        )


def _wait_for_external_abort(
    abort_event: threading.Event | _AbortCallbackSignal,
    provider_start_event: threading.Event | None,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ProviderTurnInterruption:
    """Bridge accepted RPC aborts into executor ordering before polling."""

    def _noop_unregister() -> None:
        return None

    accepted_abort = threading.Event()

    def _accept_abort() -> None:
        accepted_abort.set()
        cancel_event.set()

    unregister = _noop_unregister
    try:
        if isinstance(abort_event, _AbortCallbackSignal):
            unregister = abort_event.register_cancel_callback(_accept_abort)
        if abort_event.is_set():
            _accept_abort()
    finally:
        if provider_start_event is not None:
            provider_start_event.set()
    try:
        while True:
            if accepted_abort.is_set() or abort_event.is_set():
                _accept_abort()
                return ProviderTurnInterruption.OPERATOR_ABORT
            if done_event.wait(timeout=0.05):
                if accepted_abort.is_set() or abort_event.is_set():
                    _accept_abort()
                    return ProviderTurnInterruption.OPERATOR_ABORT
                return ProviderTurnInterruption.SETTLED
    finally:
        unregister()


class _ExecutionOrder:
    """Record worker completion relative to the first cancellation signal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._completion: int | None = None
        self._cancellation: int | None = None

    def record_completion(self) -> None:
        with self._lock:
            if self._completion is None:
                self._completion = self._next
                self._next += 1

    def record_cancellation(self) -> None:
        with self._lock:
            if self._cancellation is None:
                self._cancellation = self._next
                self._next += 1

    def cancellation_precedes_completion(self) -> bool:
        with self._lock:
            return self._cancellation is not None and (
                self._completion is None or self._cancellation < self._completion
            )

    def completion_precedes_cancellation(self) -> bool:
        with self._lock:
            return self._completion is not None and (
                self._cancellation is None or self._completion < self._cancellation
            )

    def cancellation_started(self) -> bool:
        with self._lock:
            return self._cancellation is not None


class _OrderedCancellationEvent(threading.Event):
    """Event whose first signal participates in provider-turn ordering."""

    def __init__(
        self,
        order: _ExecutionOrder,
        cancel_token: CancelToken,
    ) -> None:
        super().__init__()
        self._order = order
        self._cancel_token = cancel_token
        self._delegate = cancel_token.event

    def set(self) -> None:
        self._order.record_cancellation()
        self._cancel_token.cancel()

    def is_set(self) -> bool:
        return self._delegate.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._delegate.wait(timeout)

    def clear(self) -> None:
        self._delegate.clear()


class _DeltaAdmissionGate:
    """Reject new deltas after a turn ends without blocking admitted sinks."""

    def __init__(self, order: _ExecutionOrder) -> None:
        self._order = order
        self._lock = threading.Lock()
        self._active = True

    def emit(self, sink: StreamChunkSink, chunk: str) -> None:
        with self._lock:
            if not self._active or self._order.cancellation_started():
                return
        sink(chunk)

    def close(self) -> None:
        with self._lock:
            self._active = False


class ProviderTurnExecutor:
    """Execute one provider completion with canonical delta publication."""

    def __init__(self, *, cancel_join_timeout_seconds: float = 2.0) -> None:
        if isinstance(cancel_join_timeout_seconds, bool) or not isinstance(
            cancel_join_timeout_seconds, (int, float)
        ):
            raise TypeError("cancel_join_timeout_seconds must be a number")
        if not isfinite(cancel_join_timeout_seconds) or cancel_join_timeout_seconds < 0:
            raise ValueError(
                "cancel_join_timeout_seconds must be a finite nonnegative number"
            )
        self._cancel_join_timeout_seconds = float(cancel_join_timeout_seconds)

    def complete(
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        *,
        turn_index: int,
        waiter: ProviderTurnWaiter | None = None,
        delta_policy: ProviderTurnDeltaPolicy = _DEFAULT_PROVIDER_TURN_DELTA_POLICY,
    ) -> ProviderTurnOutcome:
        """Complete one turn synchronously or through the supplied wait policy."""

        if not isinstance(provider, ProviderPort):
            raise TypeError("provider must implement ProviderPort")
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")
        if not isinstance(event_sink, AgentEventSink):
            raise TypeError("event_sink must implement AgentEventSink")
        require_non_negative_int(turn_index, "turn_index")
        if waiter is not None and not callable(waiter):
            raise TypeError("waiter must be callable or None")
        if type(delta_policy) is not ProviderTurnDeltaPolicy:
            raise TypeError("delta_policy must be an exact ProviderTurnDeltaPolicy")
        if waiter is None:
            return self._complete_synchronously(
                provider, request, event_sink, turn_index, delta_policy
            )
        return self._complete_interruptibly(
            provider, request, event_sink, turn_index, waiter, delta_policy
        )

    @staticmethod
    def _delta_sinks(
        event_sink: AgentEventSink,
        turn_index: int,
        policy: ProviderTurnDeltaPolicy,
        gate: _DeltaAdmissionGate | None = None,
    ) -> tuple[StreamChunkSink | None, StreamChunkSink | None]:
        def _text(chunk: str) -> None:
            event_sink.emit(AssistantTextDelta(turn_index, ProductContent(chunk)))

        def _reasoning(chunk: str) -> None:
            event_sink.emit(AssistantReasoningDelta(turn_index, ProductContent(chunk)))

        text_sink: StreamChunkSink | None = _text if policy.text else None
        reasoning_sink: StreamChunkSink | None = (
            _reasoning if policy.reasoning else None
        )
        if gate is None:
            return text_sink, reasoning_sink
        return (
            (lambda chunk: gate.emit(_text, chunk)) if policy.text else None,
            (lambda chunk: gate.emit(_reasoning, chunk)) if policy.reasoning else None,
        )

    def _complete_synchronously(
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        turn_index: int,
        delta_policy: ProviderTurnDeltaPolicy,
    ) -> ProviderTurnOutcome:
        gate = _DeltaAdmissionGate(_ExecutionOrder())
        text_sink, reasoning_sink = self._delta_sinks(
            event_sink, turn_index, delta_policy, gate
        )
        try:
            result = provider.complete(
                request,
                stream_sink=text_sink,
                reasoning_sink=reasoning_sink,
            )
        except ProviderCancelledError:
            return ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        finally:
            gate.close()
        return ProviderTurnOutcome(result=result)

    def _complete_interruptibly(
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        turn_index: int,
        waiter: ProviderTurnWaiter,
        delta_policy: ProviderTurnDeltaPolicy,
    ) -> ProviderTurnOutcome:
        order = _ExecutionOrder()
        cancel_token = CancelToken()
        cancel_event = _OrderedCancellationEvent(order, cancel_token)
        gate = _DeltaAdmissionGate(order)
        done_event = threading.Event()
        results: list[ProviderResult] = []
        errors: list[BaseException] = []
        provider_cancelled = threading.Event()
        text_sink, reasoning_sink = self._delta_sinks(
            event_sink, turn_index, delta_policy, gate
        )

        def _worker() -> None:
            try:
                results.append(
                    provider.complete(
                        request,
                        stream_sink=text_sink,
                        reasoning_sink=reasoning_sink,
                        cancel_token=cancel_token,
                    )
                )
            except ProviderCancelledError:
                provider_cancelled.set()
            # re-raised by the caller
            except BaseException as exc:  # pragma: no cover  # noqa: BLE001
                errors.append(exc)
            finally:
                order.record_completion()
                done_event.set()

        worker = threading.Thread(
            target=_worker, name="pipy-provider-turn", daemon=True
        )
        worker.start()
        try:
            interruption = waiter(done_event, cancel_event)
            if not isinstance(interruption, ProviderTurnInterruption):
                raise TypeError("provider turn waiter returned an invalid outcome")
        except BaseException:
            gate.close()
            cancel_event.set()
            worker.join(timeout=self._cancel_join_timeout_seconds)
            raise

        if interruption is not ProviderTurnInterruption.SETTLED:
            gate.close()
            cancel_event.set()
            worker.join(timeout=self._cancel_join_timeout_seconds)
            if order.completion_precedes_cancellation():
                return _completed_outcome(results, errors, provider_cancelled)
            return ProviderTurnOutcome(
                cancellation_reason=_cancellation_reason(interruption)
            )

        worker.join(timeout=self._cancel_join_timeout_seconds)
        if worker.is_alive():
            gate.close()
            cancel_event.set()
            worker.join(timeout=self._cancel_join_timeout_seconds)
        gate.close()
        if order.cancellation_precedes_completion():
            return ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        return _completed_outcome(results, errors, provider_cancelled)


def _cancellation_reason(
    interruption: ProviderTurnInterruption,
) -> AgentCancellationReason:
    if interruption is ProviderTurnInterruption.OPERATOR_ABORT:
        return AgentCancellationReason.OPERATOR_ABORT
    if interruption is ProviderTurnInterruption.STEERING:
        return AgentCancellationReason.STEERING
    if interruption is ProviderTurnInterruption.LOCAL_COMMAND:
        return AgentCancellationReason.LOCAL_COMMAND
    raise ValueError(
        f"settled provider turn has no cancellation reason: {interruption}"
    )


def _completed_outcome(
    results: list[ProviderResult],
    errors: list[BaseException],
    provider_cancelled: threading.Event,
) -> ProviderTurnOutcome:
    if errors:
        raise errors[0]
    if provider_cancelled.is_set() or not results:
        return ProviderTurnOutcome(
            cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
        )
    return ProviderTurnOutcome(result=results[0])
