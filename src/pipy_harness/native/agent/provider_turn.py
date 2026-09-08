"""Synchronous, UI-free execution for one native provider turn."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent._validation import require_non_negative_int
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AssistantReasoningDelta,
    AssistantTextDelta,
    RetryCompleted,
    RetryScheduled,
)
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.provider_retry import (
    ProviderManagedRetryPolicy,
    is_managed_retry_eligible,
)
from pipy_harness.native.agent.results import AgentCancellationReason, AgentFailure
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    PreparedProviderCompletion,
    PreparedProviderPort,
    ProviderAttemptAllowance,
    ProviderPort,
    StreamChunkSink,
)
from pipy_harness.status import HarnessStatus


class ProviderTurnInterruption(StrEnum):
    """Closed results returned by the caller-owned provider wait policy."""

    SETTLED = "settled"
    OPERATOR_ABORT = "operator_abort"
    STEERING = "steering"
    LOCAL_COMMAND = "local_command"


@dataclass(frozen=True, slots=True)
class ProviderTurnDeltaPolicy:
    """Select which provider delta channels receive canonical sinks.

    The canonical agent loop uses both channels. Compatibility runtimes preserve
    their buffered or text-only contracts; private auxiliary summaries disable
    both channels. Disabled channels are passed to the provider as ``None``
    rather than as no-op callables.
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

    def rearm_start_gate(self) -> None:
        """Require the caller's abort admission again for the next phase."""

        self._start_event.clear()

    def wait_for_start(self, cancel_token: CancelToken) -> None:
        self._start_event.wait()
        cancel_token.raise_if_cancelled()

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> PreparedProviderCompletion | None:
        self._start_event.wait()
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        if not isinstance(self._provider, PreparedProviderPort):
            return None
        return self._provider.prepare_completion(
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
        self._observed = False

    def emit(self, sink: StreamChunkSink, chunk: str) -> None:
        with self._lock:
            if not self._active or self._order.cancellation_started():
                return
            self._observed = True
        sink(chunk)

    @property
    def observed(self) -> bool:
        with self._lock:
            return self._observed

    def close(self) -> None:
        with self._lock:
            self._active = False


class ProviderTurnExecutor:
    """Execute one provider completion with canonical delta publication."""

    def __init__(
        self,
        *,
        cancel_join_timeout_seconds: float = 2.0,
        retry_jitter: Callable[[], float] = random.random,
        retry_sleep: Callable[[float], None] = time.sleep,
        provider_thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if isinstance(cancel_join_timeout_seconds, bool) or not isinstance(
            cancel_join_timeout_seconds, (int, float)
        ):
            raise TypeError("cancel_join_timeout_seconds must be a number")
        if not isfinite(cancel_join_timeout_seconds) or cancel_join_timeout_seconds < 0:
            raise ValueError(
                "cancel_join_timeout_seconds must be a finite nonnegative number"
            )
        self._cancel_join_timeout_seconds = float(cancel_join_timeout_seconds)
        if not callable(retry_jitter):
            raise TypeError("retry_jitter must be callable")
        if not callable(retry_sleep):
            raise TypeError("retry_sleep must be callable")
        if not callable(provider_thread_factory):
            raise TypeError("provider_thread_factory must be callable")
        self._retry_jitter = retry_jitter
        self._retry_sleep = retry_sleep
        self._provider_thread_factory = provider_thread_factory

    def complete(
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        *,
        turn_index: int,
        waiter: ProviderTurnWaiter | None = None,
        delta_policy: ProviderTurnDeltaPolicy = _DEFAULT_PROVIDER_TURN_DELTA_POLICY,
        retry_policy: ProviderManagedRetryPolicy | None = None,
        before_reissue: Callable[[], None] | None = None,
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
        if (
            retry_policy is not None
            and type(retry_policy) is not ProviderManagedRetryPolicy
        ):
            raise TypeError(
                "retry_policy must be an exact ProviderManagedRetryPolicy or None"
            )
        if before_reissue is not None and not callable(before_reissue):
            raise TypeError("before_reissue must be callable or None")
        if retry_policy is not None and before_reissue is None:
            raise ValueError("before_reissue is required with retry_policy")
        if waiter is None:
            return self._complete_synchronously(
                provider,
                request,
                event_sink,
                turn_index,
                delta_policy,
                retry_policy,
                before_reissue,
            )
        return self._complete_interruptibly(
            provider,
            request,
            event_sink,
            turn_index,
            waiter,
            delta_policy,
            retry_policy,
            before_reissue,
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
        retry_policy: ProviderManagedRetryPolicy | None,
        before_reissue: Callable[[], None] | None,
    ) -> ProviderTurnOutcome:
        gate = _DeltaAdmissionGate(_ExecutionOrder())
        text_sink, reasoning_sink = self._delta_sinks(
            event_sink, turn_index, delta_policy, gate
        )
        try:
            prepared = None
            if retry_policy is not None and isinstance(provider, PreparedProviderPort):
                prepared = provider.prepare_completion(
                    request, stream_sink=text_sink, reasoning_sink=reasoning_sink
                )
            if prepared is None:
                result = provider.complete(
                    request, stream_sink=text_sink, reasoning_sink=reasoning_sink
                )
            else:
                assert retry_policy is not None
                result = prepared.complete_attempt(
                    ProviderAttemptAllowance(1, retry_policy.max_attempts)
                )
                result = self._retry_synchronously(
                    prepared, result, retry_policy, before_reissue, event_sink, gate
                )
        except ProviderCancelledError:
            return ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        finally:
            gate.close()
        return ProviderTurnOutcome(result=result)

    def _retry_synchronously(
        self,
        prepared: PreparedProviderCompletion,
        result: ProviderResult,
        policy: ProviderManagedRetryPolicy,
        before_reissue: Callable[[], None] | None,
        event_sink: AgentEventSink,
        gate: _DeltaAdmissionGate,
    ) -> ProviderResult:
        for attempt in range(2, policy.max_attempts + 1):
            if not is_managed_retry_eligible(result, observed_delta=gate.observed):
                break
            ordinal = attempt - 1
            failure = _retry_failure(result)
            delay = policy.delay_seconds(ordinal, result, self._retry_jitter())
            event_sink.emit(
                RetryScheduled(
                    ordinal, policy.max_attempts - 1, round(delay * 1000), failure
                )
            )
            try:
                self._retry_sleep(delay)
            except BaseException as exc:
                event_sink.emit(
                    RetryCompleted(ordinal, False, _failure_for_exception(exc))
                )
                raise
            try:
                assert before_reissue is not None
                before_reissue()
            except BaseException:
                event_sink.emit(RetryCompleted(ordinal, False, _admission_failure()))
                raise
            try:
                result = prepared.complete_attempt(
                    ProviderAttemptAllowance(attempt, policy.max_attempts)
                )
            except ProviderCancelledError:
                event_sink.emit(RetryCompleted(ordinal, False, _cancellation_failure()))
                raise
            except BaseException as exc:
                event_sink.emit(RetryCompleted(ordinal, False, _exception_failure(exc)))
                raise
            event_sink.emit(_retry_completed(ordinal, result, gate.observed))
        return result

    def _complete_interruptibly(
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        turn_index: int,
        waiter: ProviderTurnWaiter,
        delta_policy: ProviderTurnDeltaPolicy,
        retry_policy: ProviderManagedRetryPolicy | None,
        before_reissue: Callable[[], None] | None,
    ) -> ProviderTurnOutcome:
        order = _ExecutionOrder()
        cancel_token = CancelToken()
        cancel_event = _OrderedCancellationEvent(order, cancel_token)
        gate = _DeltaAdmissionGate(order)
        try:
            return self._complete_interruptibly_phases(
                provider,
                request,
                event_sink,
                turn_index,
                waiter,
                delta_policy,
                retry_policy,
                before_reissue,
                order,
                cancel_token,
                cancel_event,
                gate,
            )
        finally:
            gate.close()

    def _complete_interruptibly_phases(  # noqa: C901 - explicit phase matrix
        self,
        provider: ProviderPort,
        request: ProviderRequest,
        event_sink: AgentEventSink,
        turn_index: int,
        waiter: ProviderTurnWaiter,
        delta_policy: ProviderTurnDeltaPolicy,
        retry_policy: ProviderManagedRetryPolicy | None,
        before_reissue: Callable[[], None] | None,
        order: _ExecutionOrder,
        cancel_token: CancelToken,
        cancel_event: _OrderedCancellationEvent,
        gate: _DeltaAdmissionGate,
    ) -> ProviderTurnOutcome:
        done_event = threading.Event()
        results: list[ProviderResult] = []
        errors: list[BaseException] = []
        provider_cancelled = threading.Event()
        text_sink, reasoning_sink = self._delta_sinks(
            event_sink, turn_index, delta_policy, gate
        )

        def _emit_retry_event(event: RetryScheduled | RetryCompleted) -> None:
            try:
                event_sink.emit(event)
            except BaseException:
                gate.close()
                cancel_event.set()
                raise

        prepared: list[PreparedProviderCompletion] = []
        attempt = 1

        def _worker() -> None:
            try:
                if attempt > 1 and isinstance(provider, _StartGatedProvider):
                    provider.wait_for_start(cancel_token)
                if attempt == 1:
                    handle = None
                    if retry_policy is not None and isinstance(
                        provider, PreparedProviderPort
                    ):
                        handle = provider.prepare_completion(
                            request,
                            stream_sink=text_sink,
                            reasoning_sink=reasoning_sink,
                            cancel_token=cancel_token,
                        )
                    if handle is None:
                        results.append(
                            provider.complete(
                                request,
                                stream_sink=text_sink,
                                reasoning_sink=reasoning_sink,
                                cancel_token=cancel_token,
                            )
                        )
                    else:
                        assert retry_policy is not None
                        prepared.append(handle)
                        results.append(
                            handle.complete_attempt(
                                ProviderAttemptAllowance(1, retry_policy.max_attempts)
                            )
                        )
                else:
                    assert retry_policy is not None
                    results.append(
                        prepared[0].complete_attempt(
                            ProviderAttemptAllowance(attempt, retry_policy.max_attempts)
                        )
                    )
            except ProviderCancelledError:
                provider_cancelled.set()
            # re-raised by the caller
            except BaseException as exc:  # noqa: BLE001 - re-raised by caller
                errors.append(exc)
            finally:
                terminal = bool(errors) or provider_cancelled.is_set()
                if results:
                    terminal = (
                        retry_policy is None
                        or not prepared
                        or attempt >= retry_policy.max_attempts
                        or not is_managed_retry_eligible(
                            results[0], observed_delta=gate.observed
                        )
                    )
                if terminal:
                    order.record_completion()
                done_event.set()

        def _start_worker() -> threading.Thread:
            done_event.clear()
            results.clear()
            errors.clear()
            provider_cancelled.clear()
            if isinstance(provider, _StartGatedProvider):
                provider.rearm_start_gate()
            phase_worker = self._provider_thread_factory(
                target=_worker, name="pipy-provider-turn", daemon=True
            )
            phase_worker.start()
            return phase_worker

        worker = _start_worker()
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
        if not done_event.is_set() and worker.is_alive():
            gate.close()
            cancel_event.set()
            worker.join(timeout=self._cancel_join_timeout_seconds)
        if order.cancellation_precedes_completion():
            gate.close()
            return ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        if (
            retry_policy is not None
            and prepared
            and results
            and is_managed_retry_eligible(results[0], observed_delta=gate.observed)
        ):
            result = results[0]
            for attempt in range(2, retry_policy.max_attempts + 1):
                if not is_managed_retry_eligible(result, observed_delta=gate.observed):
                    break
                ordinal = attempt - 1
                failure = _retry_failure(result)
                delay = retry_policy.delay_seconds(
                    ordinal, result, self._retry_jitter()
                )
                _emit_retry_event(
                    RetryScheduled(
                        ordinal,
                        retry_policy.max_attempts - 1,
                        round(delay * 1000),
                        failure,
                    )
                )
                delay_done = threading.Event()
                timer = threading.Timer(delay, delay_done.set)
                timer.daemon = True
                try:
                    timer.start()
                    delay_interruption = waiter(delay_done, cancel_event)
                    _validate_waiter_result(delay_interruption)
                except BaseException as exc:
                    timer.cancel()
                    gate.close()
                    cancel_event.set()
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _failure_for_exception(exc))
                    )
                    raise
                timer.cancel()
                if delay_interruption is not ProviderTurnInterruption.SETTLED:
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    cancel_event.set()
                    return ProviderTurnOutcome(
                        cancellation_reason=_cancellation_reason(delay_interruption)
                    )
                if cancel_event.is_set():
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    return ProviderTurnOutcome(
                        cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
                    )
                try:
                    assert before_reissue is not None
                    before_reissue()
                except BaseException:
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _admission_failure())
                    )
                    gate.close()
                    raise
                if cancel_event.is_set():
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    return ProviderTurnOutcome(
                        cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
                    )
                try:
                    worker = _start_worker()
                except BaseException as exc:
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _failure_for_exception(exc))
                    )
                    raise
                try:
                    phase_interruption = waiter(done_event, cancel_event)
                    _validate_waiter_result(phase_interruption)
                except BaseException as exc:
                    gate.close()
                    cancel_event.set()
                    worker.join(timeout=self._cancel_join_timeout_seconds)
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _failure_for_exception(exc))
                    )
                    raise
                if phase_interruption is not ProviderTurnInterruption.SETTLED:
                    cancel_event.set()
                    worker.join(timeout=self._cancel_join_timeout_seconds)
                    if order.completion_precedes_cancellation():
                        gate.close()
                        try:
                            outcome = _completed_outcome(
                                results, errors, provider_cancelled
                            )
                        except BaseException as exc:
                            gate.close()
                            _emit_retry_event(
                                RetryCompleted(ordinal, False, _exception_failure(exc))
                            )
                            raise
                        if outcome.result is not None:
                            _emit_retry_event(
                                _retry_completed(ordinal, outcome.result, gate.observed)
                            )
                        else:
                            _emit_retry_event(
                                RetryCompleted(ordinal, False, _cancellation_failure())
                            )
                        return outcome
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    return ProviderTurnOutcome(
                        cancellation_reason=_cancellation_reason(phase_interruption)
                    )
                worker.join(timeout=self._cancel_join_timeout_seconds)
                if not done_event.is_set() and worker.is_alive():
                    gate.close()
                    cancel_event.set()
                    worker.join(timeout=self._cancel_join_timeout_seconds)
                if order.cancellation_precedes_completion():
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    return ProviderTurnOutcome(
                        cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
                    )
                try:
                    outcome = _completed_outcome(results, errors, provider_cancelled)
                except BaseException as exc:
                    gate.close()
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _exception_failure(exc))
                    )
                    raise
                if outcome.result is None:
                    _emit_retry_event(
                        RetryCompleted(ordinal, False, _cancellation_failure())
                    )
                    gate.close()
                    return outcome
                result = outcome.result
                _emit_retry_event(_retry_completed(ordinal, result, gate.observed))
            gate.close()
            return ProviderTurnOutcome(result=result)
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


def _retry_failure(result: ProviderResult, *, retryable: bool = True) -> AgentFailure:
    return AgentFailure(
        result.error_type or "provider_error",
        ProductContent(result.error_message or "Provider request failed."),
        retryable=retryable,
    )


def _retry_completed(
    attempt: int, result: ProviderResult, observed_delta: bool
) -> RetryCompleted:
    if result.status is HarnessStatus.SUCCEEDED:
        return RetryCompleted(attempt, True)
    return RetryCompleted(
        attempt,
        False,
        _retry_failure(
            result,
            retryable=is_managed_retry_eligible(result, observed_delta=observed_delta),
        ),
    )


def _cancellation_failure() -> AgentFailure:
    return AgentFailure("retry_cancelled", ProductContent("Retry was cancelled."))


def _admission_failure() -> AgentFailure:
    return AgentFailure(
        "retry_admission_rejected", ProductContent("Retry admission was rejected.")
    )


def _exception_failure(exc: BaseException) -> AgentFailure:
    return AgentFailure(type(exc).__name__, ProductContent(str(exc) or "Retry failed."))


def _failure_for_exception(exc: BaseException) -> AgentFailure:
    if isinstance(exc, (KeyboardInterrupt, ProviderCancelledError)):
        return _cancellation_failure()
    return _exception_failure(exc)


def _validate_waiter_result(interruption: object) -> None:
    if not isinstance(interruption, ProviderTurnInterruption):
        raise TypeError("provider turn waiter returned an invalid outcome")
