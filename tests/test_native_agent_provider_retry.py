"""Behavioral contracts for opt-in canonical provider retry."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.events import AgentEvent, RetryCompleted, RetryScheduled
from pipy_harness.native.agent.provider_retry import (
    ProviderManagedRetryPolicy,
    is_managed_retry_eligible,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    ProviderTurnInterruption,
    _StartGatedProvider,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderAttemptAllowance, StreamChunkSink


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest("system", "user", "fixture", "model", tmp_path)


def _result(
    status: HarnessStatus,
    *,
    metadata: dict[str, Any] | None = None,
    text: str | None = None,
    usage: dict[str, int] | None = None,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=status,
        provider_name="fixture",
        model_id="model",
        started_at=now,
        ended_at=now,
        final_text=text,
        usage=usage,
        metadata=metadata,
        error_type="TransientError" if status is HarnessStatus.FAILED else None,
        error_message="try later" if status is HarnessStatus.FAILED else None,
    )


def _failure(**changes: Any) -> ProviderResult:
    result = _result(
        HarnessStatus.FAILED,
        metadata={"retryable": True, "progress": "none"},
    )
    return replace(result, **changes)


@dataclass(slots=True)
class _Sink:
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class _PreparedProvider:
    results: list[ProviderResult | BaseException]
    name: str = "fixture"
    model_id: str = "model"
    supports_tool_calls: bool = True
    prepared: int = 0
    ordinary: int = 0
    allowances: list[ProviderAttemptAllowance] = field(default_factory=list)
    requests: list[ProviderRequest] = field(default_factory=list)
    sink_ids: list[tuple[int | None, int | None, int | None]] = field(
        default_factory=list
    )

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.ordinary += 1
        outcome = self.results[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> object:
        self.prepared += 1
        self.requests.append(request)
        self.sink_ids.append(
            (
                id(stream_sink) if stream_sink is not None else None,
                id(reasoning_sink) if reasoning_sink is not None else None,
                id(cancel_token) if cancel_token is not None else None,
            )
        )
        provider = self

        class _Handle:
            def complete_attempt(
                self, allowance: ProviderAttemptAllowance
            ) -> ProviderResult:
                provider.allowances.append(allowance)
                outcome = provider.results[len(provider.allowances) - 1]
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        return _Handle()


def _policy(max_attempts: int = 3) -> ProviderManagedRetryPolicy:
    return ProviderManagedRetryPolicy(max_attempts, 0.01, 1.0, 2.0, 0.0)


def test_prepared_request_and_handle_are_reused_and_trace_balances(
    tmp_path: Path,
) -> None:
    provider = _PreparedProvider(
        [_failure(), _failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    sink = _Sink()
    admissions: list[int] = []
    caller = threading.get_ident()
    request = _request(tmp_path)

    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        request,
        sink,
        turn_index=0,
        retry_policy=_policy(),
        before_reissue=lambda: admissions.append(threading.get_ident()),
    )

    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert provider.prepared == 1
    assert provider.ordinary == 0
    assert provider.requests == [request]
    assert provider.requests[0] is request
    assert provider.allowances == [
        ProviderAttemptAllowance(1, 3),
        ProviderAttemptAllowance(2, 3),
        ProviderAttemptAllowance(3, 3),
    ]
    assert admissions == [caller, caller]
    assert [type(event) for event in sink.events] == [
        RetryScheduled,
        RetryCompleted,
        RetryScheduled,
        RetryCompleted,
    ]
    assert [
        (event.attempt, event.max_attempts, event.delay_ms)
        for event in sink.events
        if isinstance(event, RetryScheduled)
    ] == [(1, 2, 10), (2, 2, 20)]
    assert [
        event.succeeded for event in sink.events if isinstance(event, RetryCompleted)
    ] == [False, True]


def test_admission_runs_after_delay_and_rejection_closes_trace(tmp_path: Path) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="unused")]
    )
    sink = _Sink()
    order: list[str] = []

    def reject() -> None:
        order.append("admission")
        raise RuntimeError("stale")

    with pytest.raises(RuntimeError, match="stale"):
        ProviderTurnExecutor(retry_sleep=lambda _delay: order.append("delay")).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            retry_policy=_policy(2),
            before_reissue=reject,
        )

    assert order == ["delay", "admission"]
    assert len(provider.allowances) == 1
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and not end.succeeded
    assert (
        end.failure is not None and end.failure.error_type == "retry_admission_rejected"
    )


@pytest.mark.parametrize(
    "result",
    [
        _failure(metadata=None),
        _failure(metadata={"retryable": 1, "progress": "none"}),
        _failure(metadata={"retryable": True, "progress": "unknown"}),
        _failure(final_text="partial"),
        _failure(usage={}),
        _result(
            HarnessStatus.SUCCEEDED, metadata={"retryable": True, "progress": "none"}
        ),
    ],
)
def test_eligibility_is_conservative(result: ProviderResult) -> None:
    assert not is_managed_retry_eligible(result, observed_delta=False)
    assert not is_managed_retry_eligible(_failure(), observed_delta=True)


def test_delay_uses_larger_server_hint_and_policy_cap() -> None:
    policy = ProviderManagedRetryPolicy(2, 0.5, 3.0, 2.0, 0.25)
    assert (
        policy.delay_seconds(
            1,
            _failure(
                metadata={
                    "retryable": True,
                    "progress": "none",
                    "retry_after_seconds": 90.0,
                }
            ),
            1.0,
        )
        == 3.0
    )
    for malformed in (True, "2", float("nan"), float("inf"), -1.0):
        result = _failure(
            metadata={
                "retryable": True,
                "progress": "none",
                "retry_after_seconds": malformed,
            }
        )
        assert policy.delay_seconds(1, result, 0.0) == 0.5


def test_abort_during_backoff_emits_failed_end_without_reissue(tmp_path: Path) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="unused")]
    )
    sink = _Sink()
    calls = 0

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert done.wait(1)
            return ProviderTurnInterruption.SETTLED
        cancel.set()
        return ProviderTurnInterruption.OPERATOR_ABORT

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=waiter,
        retry_policy=_policy(2),
        before_reissue=lambda: None,
    )

    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert len(provider.allowances) == 1
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    assert isinstance(sink.events[-1], RetryCompleted) and not sink.events[-1].succeeded


def test_disabled_delta_channels_do_not_prevent_affirmative_retry(
    tmp_path: Path,
) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    from pipy_harness.native.agent.provider_turn import ProviderTurnDeltaPolicy

    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        _request(tmp_path),
        _Sink(),
        turn_index=0,
        retry_policy=_policy(2),
        before_reissue=lambda: None,
        delta_policy=ProviderTurnDeltaPolicy(text=False, reasoning=False),
    )
    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert provider.sink_ids == [(None, None, None)]


@pytest.mark.parametrize("fail_on", [RetryScheduled, RetryCompleted])
def test_retry_callback_failure_prevents_another_attempt(
    tmp_path: Path, fail_on: type[AgentEvent]
) -> None:
    provider = _PreparedProvider(
        [_failure(), _failure(), _result(HarnessStatus.SUCCEEDED, text="unused")]
    )

    class _FailingSink:
        def emit(self, event: AgentEvent) -> None:
            if isinstance(event, fail_on):
                raise RuntimeError("sink failed")

    with pytest.raises(RuntimeError, match="sink failed"):
        ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
            provider,
            _request(tmp_path),
            _FailingSink(),
            turn_index=0,
            retry_policy=_policy(3),
            before_reissue=lambda: None,
        )

    expected_attempts = 1 if fail_on is RetryScheduled else 2
    assert len(provider.allowances) == expected_attempts


@pytest.mark.parametrize(
    "changes,exception",
    [
        ({"max_attempts": True}, TypeError),
        ({"max_attempts": 11}, ValueError),
        ({"max_delay_seconds": float("nan")}, ValueError),
        ({"multiplier": float("inf")}, ValueError),
        ({"jitter_seconds": -1.0}, ValueError),
    ],
)
def test_managed_policy_rejects_malformed_values(
    changes: dict[str, object], exception: type[Exception]
) -> None:
    values: dict[str, object] = {
        "max_attempts": 2,
        "initial_delay_seconds": 0.1,
        "max_delay_seconds": 1.0,
        "multiplier": 2.0,
        "jitter_seconds": 0.0,
    }
    values.update(changes)
    with pytest.raises(exception):
        ProviderManagedRetryPolicy(**values)  # type: ignore[arg-type]


def test_unsupported_provider_keeps_one_ordinary_call_with_policy(
    tmp_path: Path,
) -> None:
    @dataclass
    class _OrdinaryProvider:
        name: str = "fixture"
        model_id: str = "model"
        supports_tool_calls: bool = True
        calls: int = 0

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            self.calls += 1
            return _failure()

    provider = _OrdinaryProvider()
    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        _Sink(),
        turn_index=0,
        retry_policy=_policy(3),
        before_reissue=lambda: None,
    )
    assert outcome.result is not None and outcome.result.status is HarnessStatus.FAILED
    assert provider.calls == 1


@pytest.mark.parametrize(
    "exception,error_type",
    [
        (ProviderCancelledError("cancelled"), "retry_cancelled"),
        (RuntimeError("broken worker"), "RuntimeError"),
    ],
)
def test_reissue_exception_balances_trace_and_retires_deltas(
    tmp_path: Path, exception: BaseException, error_type: str
) -> None:
    retained: list[StreamChunkSink | None] = []

    @dataclass
    class _RetainingProvider(_PreparedProvider):
        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            retained.append(stream_sink)
            return super().prepare_completion(
                request,
                stream_sink=stream_sink,
                reasoning_sink=reasoning_sink,
                cancel_token=cancel_token,
            )

    provider = _RetainingProvider([_failure(), exception])
    sink = _Sink()
    if isinstance(exception, ProviderCancelledError):
        outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            retry_policy=_policy(2),
            before_reissue=lambda: None,
        )
        assert outcome.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED
    else:
        with pytest.raises(RuntimeError, match="broken worker"):
            ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
                provider,
                _request(tmp_path),
                sink,
                turn_index=0,
                retry_policy=_policy(2),
                before_reissue=lambda: None,
            )
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and end.failure is not None
    assert end.failure.error_type == error_type
    assert retained[0] is not None
    retained[0]("late")
    assert len(sink.events) == 2


def test_interruptible_retry_events_and_admission_stay_on_caller_thread(
    tmp_path: Path,
) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    threads: list[int] = []

    class _ThreadSink(_Sink):
        def emit(self, event: AgentEvent) -> None:
            threads.append(threading.get_ident())
            super().emit(event)

    sink = _ThreadSink()
    caller = threading.get_ident()

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=settled,
        retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
        before_reissue=lambda: threads.append(threading.get_ident()),
    )
    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert threads == [caller, caller, caller]


def test_callback_start_gate_is_rearmed_for_prepared_reissue(tmp_path: Path) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    start = threading.Event()
    gated = _StartGatedProvider(provider, start)
    waiter_calls = 0

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal waiter_calls
        waiter_calls += 1
        if waiter_calls in (1, 3):
            assert not start.is_set()
        start.set()
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    outcome = ProviderTurnExecutor().complete(
        gated,
        _request(tmp_path),
        _Sink(),
        turn_index=0,
        waiter=settled,
        retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
        before_reissue=lambda: None,
    )
    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert waiter_calls == 3  # initial phase, delay, and reissued phase
    assert len(provider.allowances) == 2


def test_retry_completion_precedes_late_abort_and_preserves_success(
    tmp_path: Path,
) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    sink = _Sink()
    calls = 0

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        assert done.wait(1)
        if calls == 3:
            cancel.set()
            return ProviderTurnInterruption.OPERATOR_ABORT
        return ProviderTurnInterruption.SETTLED

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=waiter,
        retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
        before_reissue=lambda: None,
    )
    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert isinstance(sink.events[-1], RetryCompleted) and sink.events[-1].succeeded


def test_cancelled_abandoned_reissue_cannot_publish_late_delta_or_retry(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    @dataclass
    class _BlockingProvider(_PreparedProvider):
        saved_sink: StreamChunkSink | None = None

        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            self.saved_sink = stream_sink
            provider = self

            class _Handle:
                attempt = 0

                def complete_attempt(
                    self, allowance: ProviderAttemptAllowance
                ) -> ProviderResult:
                    self.attempt += 1
                    provider.allowances.append(allowance)
                    if self.attempt == 1:
                        return _failure()
                    started.set()
                    try:
                        assert release.wait(2)
                        assert provider.saved_sink is not None
                        provider.saved_sink("late")
                        return _result(HarnessStatus.SUCCEEDED, text="late")
                    finally:
                        finished.set()

            return _Handle()

    provider = _BlockingProvider([])
    sink = _Sink()
    calls = 0

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        if calls < 3:
            assert done.wait(1)
            return ProviderTurnInterruption.SETTLED
        assert started.wait(1)
        cancel.set()
        return ProviderTurnInterruption.OPERATOR_ABORT

    outcome = ProviderTurnExecutor(cancel_join_timeout_seconds=0.0).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=waiter,
        retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
        before_reissue=lambda: None,
    )
    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    before_release = list(sink.events)
    release.set()
    assert finished.wait(1)
    assert sink.events == before_release
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]


def test_observed_delta_contradicts_no_progress_and_prevents_retry(
    tmp_path: Path,
) -> None:
    @dataclass
    class _DeltaProvider(_PreparedProvider):
        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            provider = self

            class _Handle:
                def complete_attempt(
                    self, allowance: ProviderAttemptAllowance
                ) -> ProviderResult:
                    provider.allowances.append(allowance)
                    assert stream_sink is not None
                    stream_sink("progress")
                    return _failure()

            return _Handle()

    provider = _DeltaProvider([_failure()])
    sink = _Sink()
    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        retry_policy=_policy(3),
        before_reissue=lambda: None,
    )
    assert outcome.result is not None and outcome.result.status is HarnessStatus.FAILED
    assert len(provider.allowances) == 1
    assert not any(isinstance(event, RetryScheduled) for event in sink.events)


def test_exhaustion_uses_exact_logical_attempt_limit(tmp_path: Path) -> None:
    provider = _PreparedProvider([_failure(), _failure(), _failure()])
    sink = _Sink()
    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        retry_policy=_policy(3),
        before_reissue=lambda: None,
    )
    assert outcome.result is not None and outcome.result.status is HarnessStatus.FAILED
    assert [allowance.attempt for allowance in provider.allowances] == [1, 2, 3]
    assert [
        event.succeeded for event in sink.events if isinstance(event, RetryCompleted)
    ] == [False, False]


@pytest.mark.parametrize("max_attempts", [1, 2])
def test_cancellation_before_initial_failure_prevents_managed_reissue(
    tmp_path: Path, max_attempts: int
) -> None:
    started = threading.Event()
    release = threading.Event()

    @dataclass
    class _SlowPreparedProvider(_PreparedProvider):
        def prepare_completion(
            self, request: ProviderRequest, **_kwargs: object
        ) -> object:
            provider = self

            class _Handle:
                def complete_attempt(
                    self, allowance: ProviderAttemptAllowance
                ) -> ProviderResult:
                    provider.allowances.append(allowance)
                    started.set()
                    assert release.wait(1)
                    return _failure()

            return _Handle()

    provider = _SlowPreparedProvider([_failure()])

    def cancelled_but_settled(
        done: threading.Event, cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert started.wait(1)
        cancel.set()
        release.set()
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    sink = _Sink()
    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=cancelled_but_settled,
        retry_policy=ProviderManagedRetryPolicy(max_attempts, 0.0, 0.0),
        before_reissue=lambda: None,
    )
    assert outcome.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED
    assert len(provider.allowances) == 1
    assert sink.events == []


def test_interruptible_throwing_jitter_unconditionally_retires_delta_gate(
    tmp_path: Path,
) -> None:
    retained: list[StreamChunkSink | None] = []

    @dataclass
    class _RetainingProvider(_PreparedProvider):
        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            retained.append(stream_sink)
            return super().prepare_completion(
                request,
                stream_sink=stream_sink,
                reasoning_sink=reasoning_sink,
                cancel_token=cancel_token,
            )

    provider = _RetainingProvider([_failure()])
    sink = _Sink()

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    def broken_jitter() -> float:
        raise RuntimeError("jitter failed")

    with pytest.raises(RuntimeError, match="jitter failed"):
        ProviderTurnExecutor(retry_jitter=broken_jitter).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=settled,
            retry_policy=_policy(2),
            before_reissue=lambda: None,
        )
    assert retained[0] is not None
    retained[0]("late")
    assert sink.events == []


@pytest.mark.parametrize(
    "exception,error_type",
    [
        (KeyboardInterrupt(), "retry_cancelled"),
        (RuntimeError("wait failed"), "RuntimeError"),
    ],
)
def test_synchronous_retry_sleep_failure_balances_trace(
    tmp_path: Path, exception: BaseException, error_type: str
) -> None:
    provider = _PreparedProvider([_failure()])
    sink = _Sink()

    def fail_sleep(_delay: float) -> None:
        raise exception

    with pytest.raises(type(exception)):
        ProviderTurnExecutor(retry_sleep=fail_sleep).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            retry_policy=_policy(2),
            before_reissue=lambda: None,
        )
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and end.failure is not None
    assert end.failure.error_type == error_type


@pytest.mark.parametrize("phase", ["delay", "reissue"])
@pytest.mark.parametrize(
    "outcome,error_type",
    [
        (RuntimeError("wait failed"), "RuntimeError"),
        (KeyboardInterrupt(), "retry_cancelled"),
        ("bad", "TypeError"),
    ],
)
def test_interruptible_wait_failure_balances_trace_and_preserves_exception(
    tmp_path: Path,
    phase: str,
    outcome: BaseException | str,
    error_type: str,
) -> None:
    provider = _PreparedProvider(
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="unused")]
    )
    sink = _Sink()
    calls = 0

    def waiter(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        if calls == 1 or (phase == "reissue" and calls == 2):
            assert done.wait(1)
            return ProviderTurnInterruption.SETTLED
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

    expected = type(outcome) if isinstance(outcome, BaseException) else TypeError
    with pytest.raises(expected):
        ProviderTurnExecutor().complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=waiter,
            retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
            before_reissue=lambda: None,
        )
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and end.failure is not None
    assert end.failure.error_type == error_type
    assert not end.failure.retryable
    expected_attempts = 1 if phase == "delay" else 2
    assert len(provider.allowances) == expected_attempts


@pytest.mark.parametrize(
    "exception,error_type",
    [
        (ProviderCancelledError("cancelled"), "retry_cancelled"),
        (RuntimeError("worker failed"), "RuntimeError"),
    ],
)
@pytest.mark.parametrize("late_abort", [False, True])
def test_interruptible_reissue_worker_exception_closes_retry(
    tmp_path: Path,
    exception: BaseException,
    error_type: str,
    late_abort: bool,
) -> None:
    provider = _PreparedProvider([_failure(), exception])
    sink = _Sink()
    calls = 0

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        assert done.wait(1)
        if late_abort and calls == 3:
            cancel.set()
            return ProviderTurnInterruption.OPERATOR_ABORT
        return ProviderTurnInterruption.SETTLED

    def complete():
        return ProviderTurnExecutor().complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=waiter,
            retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
            before_reissue=lambda: None,
        )

    if isinstance(exception, ProviderCancelledError):
        completed = complete()
        assert (
            completed.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED
        )
    else:
        with pytest.raises(RuntimeError, match="worker failed"):
            complete()
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and end.failure is not None
    assert end.failure.error_type == error_type


def test_completed_non_retryable_failure_reports_truthful_retryability(
    tmp_path: Path,
) -> None:
    final = _failure(metadata={"retryable": True, "progress": "event"})
    provider = _PreparedProvider([_failure(), final])
    sink = _Sink()
    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        retry_policy=_policy(3),
        before_reissue=lambda: None,
    )
    assert outcome.result is final
    end = sink.events[-1]
    assert isinstance(end, RetryCompleted) and end.failure is not None
    assert not end.failure.retryable


@pytest.mark.parametrize("lag_phase", [1, 2])
def test_settled_worker_epilogue_lag_does_not_cancel_remaining_attempts(
    tmp_path: Path, lag_phase: int
) -> None:
    results: list[ProviderResult | BaseException] = (
        [_failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
        if lag_phase == 1
        else [_failure(), _failure(), _result(HarnessStatus.SUCCEEDED, text="ok")]
    )
    provider = _PreparedProvider(results)
    sink = _Sink()
    provider_thread_count = 0
    lag_started = threading.Event()
    release_lag = threading.Event()

    def lag_thread_factory(
        *, target: Callable[[], None], name: str, daemon: bool
    ) -> threading.Thread:
        nonlocal provider_thread_count
        provider_thread_count += 1
        phase = provider_thread_count

        def wrapped_target() -> None:
            target()
            if phase == lag_phase:
                lag_started.set()
                assert release_lag.wait(2)

        return threading.Thread(target=wrapped_target, name=name, daemon=daemon)

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    try:
        outcome = ProviderTurnExecutor(
            cancel_join_timeout_seconds=0.0,
            provider_thread_factory=lag_thread_factory,
        ).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=settled,
            retry_policy=ProviderManagedRetryPolicy(len(results), 0.0, 0.0),
            before_reissue=lambda: None,
        )
        assert lag_started.wait(1)
        assert outcome.result is not None and outcome.result.final_text == "ok"
        assert [allowance.attempt for allowance in provider.allowances] == list(
            range(1, len(results) + 1)
        )
    finally:
        release_lag.set()


def test_timer_start_failure_closes_scheduled_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _PreparedProvider([_failure()])
    sink = _Sink()

    class _FailingTimer(threading.Timer):
        def start(self) -> None:
            raise RuntimeError("timer start failed")

    monkeypatch.setattr(threading, "Timer", _FailingTimer)

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    with pytest.raises(RuntimeError, match="timer start failed"):
        ProviderTurnExecutor().complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=settled,
            retry_policy=_policy(2),
            before_reissue=lambda: None,
        )
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    assert len(provider.allowances) == 1


def test_reissue_thread_start_failure_closes_retry_and_retires_delta(
    tmp_path: Path,
) -> None:
    retained: list[StreamChunkSink | None] = []

    @dataclass
    class _RetainingProvider(_PreparedProvider):
        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            retained.append(stream_sink)
            return super().prepare_completion(
                request,
                stream_sink=stream_sink,
                reasoning_sink=reasoning_sink,
                cancel_token=cancel_token,
            )

    provider = _RetainingProvider([_failure()])
    sink = _Sink()
    starts = 0

    class _FailingStartThread(threading.Thread):
        fail_start = False

        def start(self) -> None:
            if self.fail_start:
                raise RuntimeError("provider thread start failed")
            super().start()

    def failing_thread_factory(
        *, target: Callable[[], None], name: str, daemon: bool
    ) -> threading.Thread:
        nonlocal starts
        starts += 1
        thread = _FailingStartThread(target=target, name=name, daemon=daemon)
        thread.fail_start = starts == 2
        return thread

    def settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    with pytest.raises(RuntimeError, match="provider thread start failed"):
        ProviderTurnExecutor(provider_thread_factory=failing_thread_factory).complete(
            provider,
            _request(tmp_path),
            sink,
            turn_index=0,
            waiter=settled,
            retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
            before_reissue=lambda: None,
        )
    assert [type(event) for event in sink.events] == [RetryScheduled, RetryCompleted]
    assert len(provider.allowances) == 1
    assert retained[0] is not None
    retained[0]("late")
    assert len(sink.events) == 2


@pytest.mark.parametrize("blocked_phase", ["initial", "reissue"])
def test_premature_settled_wait_cancels_unpublished_blocked_phase(
    tmp_path: Path, blocked_phase: str
) -> None:
    started = threading.Event()
    finished = threading.Event()
    captured_tokens: list[CancelToken] = []

    @dataclass
    class _BlockingPreparedProvider(_PreparedProvider):
        def prepare_completion(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> object:
            assert cancel_token is not None
            token = cancel_token
            captured_tokens.append(token)
            provider = self

            class _Handle:
                attempt = 0

                def complete_attempt(
                    self, allowance: ProviderAttemptAllowance
                ) -> ProviderResult:
                    self.attempt += 1
                    provider.allowances.append(allowance)
                    should_block = blocked_phase == "initial" or self.attempt == 2
                    if not should_block:
                        return _failure()
                    started.set()
                    assert token.event.wait(1)
                    try:
                        raise ProviderCancelledError("cancelled")
                    finally:
                        finished.set()

            return _Handle()

    provider = _BlockingPreparedProvider([])
    sink = _Sink()
    calls = 0

    def premature_settled(
        done: threading.Event, _cancel: threading.Event
    ) -> ProviderTurnInterruption:
        nonlocal calls
        calls += 1
        blocking_call = calls == 1 if blocked_phase == "initial" else calls == 3
        if blocking_call:
            assert started.wait(1)
            assert not done.is_set()
            return ProviderTurnInterruption.SETTLED
        assert done.wait(1)
        return ProviderTurnInterruption.SETTLED

    outcome = ProviderTurnExecutor(cancel_join_timeout_seconds=0.0).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=0,
        waiter=premature_settled,
        retry_policy=ProviderManagedRetryPolicy(2, 0.0, 0.0),
        before_reissue=lambda: None,
    )
    assert outcome.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED
    assert captured_tokens[0].cancelled
    assert finished.wait(1)
    expected_events: list[type[AgentEvent]] = (
        [] if blocked_phase == "initial" else [RetryScheduled, RetryCompleted]
    )
    assert [type(event) for event in sink.events] == expected_events
