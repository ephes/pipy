"""Direct contracts for the UI-free provider-turn executor."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AgentEvent,
    AssistantReasoningDelta,
    AssistantTextDelta,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    ProviderTurnInterruption,
    ProviderTurnOutcome,
    ProviderTurnWaiter,
    _wait_for_external_abort,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.automation.rpc import _AcceptedAbortSignal
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink


@dataclass(slots=True)
class _CollectingSink:
    events: list[AgentEvent] = field(default_factory=list)
    order: list[str] | None = None

    def emit(self, event: AgentEvent) -> None:
        if self.order is not None:
            self.order.append("sink")
        self.events.append(event)


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="hello",
        provider_name="fixture",
        model_id="fixture-model",
        cwd=tmp_path,
    )


def _result(*, text: str = "complete") -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="fixture",
        model_id="fixture-model",
        started_at=now,
        ended_at=now,
        final_text=text,
        usage={"input_tokens": 2, "output_tokens": 3},
    )


class _AbortBeforeDoneWaitEvent(threading.Event):
    """Accept an external abort immediately before completion is visible."""

    def __init__(
        self,
        abort_signal: _AcceptedAbortSignal,
        provider_start_event: threading.Event,
    ) -> None:
        super().__init__()
        self._abort_signal = abort_signal
        self._provider_start_event = provider_start_event
        self.abort_preceded_completion = False

    def wait(self, timeout: float | None = None) -> bool:
        if not self.is_set():
            assert self._provider_start_event.is_set()
            self._abort_signal.set()
            self.abort_preceded_completion = self._abort_signal.is_set()
            self.set()
        return super().wait(timeout)


def test_external_abort_post_done_recheck_preserves_accepted_abort() -> None:
    abort_signal = _AcceptedAbortSignal()
    provider_start_event = threading.Event()
    done_event = _AbortBeforeDoneWaitEvent(abort_signal, provider_start_event)
    cancel_event = threading.Event()

    interruption = _wait_for_external_abort(
        abort_signal,
        provider_start_event,
        done_event,
        cancel_event,
    )

    assert interruption is ProviderTurnInterruption.OPERATOR_ABORT
    assert provider_start_event.is_set()
    assert done_event.is_set()
    assert done_event.abort_preceded_completion
    assert cancel_event.is_set()
    cancel_event.clear()
    abort_signal.clear()
    abort_signal.set()
    assert not cancel_event.is_set()  # callback was unregistered on return


def test_provider_turn_outcome_enforces_exactly_one_typed_value() -> None:
    result = _result()
    cancellation = AgentCancellationReason.OPERATOR_ABORT

    assert ProviderTurnOutcome(result=result).result is result
    assert (
        ProviderTurnOutcome(cancellation_reason=cancellation).cancellation_reason
        is cancellation
    )
    with pytest.raises(ValueError, match="exactly one"):
        ProviderTurnOutcome()
    with pytest.raises(ValueError, match="exactly one"):
        ProviderTurnOutcome(result=result, cancellation_reason=cancellation)
    with pytest.raises(TypeError, match="result must be ProviderResult"):
        ProviderTurnOutcome(result=cast(ProviderResult, object()))
    with pytest.raises(TypeError, match="cancellation_reason must be"):
        ProviderTurnOutcome(cancellation_reason=cast(AgentCancellationReason, object()))


@pytest.mark.parametrize("timeout", [True, "1"])
def test_provider_turn_executor_rejects_non_numeric_timeout(timeout: object) -> None:
    with pytest.raises(TypeError, match="must be a number"):
        ProviderTurnExecutor(cancel_join_timeout_seconds=cast(float, timeout))


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -0.1])
def test_provider_turn_executor_rejects_invalid_numeric_timeout(
    timeout: float,
) -> None:
    with pytest.raises(ValueError, match="finite nonnegative"):
        ProviderTurnExecutor(cancel_join_timeout_seconds=timeout)


@dataclass(slots=True)
class _SynchronousProvider:
    supports_tool_calls: bool = True
    name: str = "fixture"
    model_id: str = "fixture-model"
    order: list[str] = field(default_factory=list)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, cancel_token
        assert stream_sink is not None
        assert reasoning_sink is not None
        self.order.append("before-text")
        stream_sink("text-1")
        self.order.append("after-text")
        reasoning_sink("reasoning-1")
        stream_sink("text-2")
        return _result()


def test_complete_validates_inputs_before_invoking_provider(tmp_path: Path) -> None:
    provider = _SynchronousProvider()
    request = _request(tmp_path)
    sink = _CollectingSink()
    executor = ProviderTurnExecutor()

    with pytest.raises(TypeError, match="provider must implement ProviderPort"):
        executor.complete(
            cast(_SynchronousProvider, object()), request, sink, turn_index=0
        )
    with pytest.raises(TypeError, match="request must be ProviderRequest"):
        executor.complete(provider, cast(ProviderRequest, object()), sink, turn_index=0)
    with pytest.raises(TypeError, match="event_sink must implement AgentEventSink"):
        executor.complete(
            provider, request, cast(_CollectingSink, object()), turn_index=0
        )
    with pytest.raises(TypeError, match="turn_index must be an int"):
        executor.complete(provider, request, sink, turn_index=cast(int, True))
    with pytest.raises(ValueError, match="turn_index must not be negative"):
        executor.complete(provider, request, sink, turn_index=-1)
    with pytest.raises(TypeError, match="waiter must be callable or None"):
        executor.complete(
            provider,
            request,
            sink,
            turn_index=0,
            waiter=cast(ProviderTurnWaiter, object()),
        )

    assert provider.order == []
    assert sink.events == []


def test_synchronous_turn_emits_exact_canonical_deltas_with_backpressure(
    tmp_path: Path,
) -> None:
    provider = _SynchronousProvider()
    sink = _CollectingSink(order=provider.order)
    outcome = ProviderTurnExecutor().complete(
        provider, _request(tmp_path), sink, turn_index=7
    )

    assert outcome.result is not None
    assert outcome.result.final_text == "complete"
    assert outcome.cancellation_reason is None
    assert provider.order == [
        "before-text",
        "sink",
        "after-text",
        "sink",
        "sink",
    ]
    assert sink.events == [
        AssistantTextDelta(7, ProductContent("text-1")),
        AssistantReasoningDelta(7, ProductContent("reasoning-1")),
        AssistantTextDelta(7, ProductContent("text-2")),
    ]


def test_synchronous_sink_failure_propagates_before_provider_continues(
    tmp_path: Path,
) -> None:
    provider = _SynchronousProvider()

    class _FailingSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            raise RuntimeError("sink refused delta")

    with pytest.raises(RuntimeError, match="sink refused delta"):
        ProviderTurnExecutor().complete(
            provider, _request(tmp_path), _FailingSink(), turn_index=0
        )

    assert provider.order == ["before-text"]


@dataclass(slots=True)
class _CancelledProvider:
    supports_tool_calls: bool = True
    name: str = "fixture"
    model_id: str = "fixture-model"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        raise ProviderCancelledError("provider cancelled")


def test_synchronous_provider_cancellation_maps_to_closed_reason(
    tmp_path: Path,
) -> None:
    outcome = ProviderTurnExecutor().complete(
        _CancelledProvider(), _request(tmp_path), _CollectingSink(), turn_index=0
    )

    assert outcome.result is None
    assert outcome.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED


@dataclass(slots=True)
class _CancelObservingProvider:
    supports_tool_calls: bool = True
    name: str = "fixture"
    model_id: str = "fixture-model"
    started: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink
        assert cancel_token is not None
        self.started.set()
        assert cancel_token.event.wait(timeout=2)
        self.cancelled.set()
        self.finished.set()
        raise ProviderCancelledError("provider cancelled")


@pytest.mark.parametrize(
    ("interruption", "expected_reason"),
    [
        (
            ProviderTurnInterruption.OPERATOR_ABORT,
            AgentCancellationReason.OPERATOR_ABORT,
        ),
        (ProviderTurnInterruption.STEERING, AgentCancellationReason.STEERING),
        (
            ProviderTurnInterruption.LOCAL_COMMAND,
            AgentCancellationReason.LOCAL_COMMAND,
        ),
    ],
)
def test_interrupt_waiter_maps_each_typed_reason_and_cancels_provider(
    tmp_path: Path,
    interruption: ProviderTurnInterruption,
    expected_reason: AgentCancellationReason,
) -> None:
    provider = _CancelObservingProvider()

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        assert not done_event.is_set()
        assert not cancel_event.is_set()
        return interruption

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        _CollectingSink(),
        turn_index=1,
        waiter=waiter,
    )

    assert outcome.result is None
    assert outcome.cancellation_reason is expected_reason
    assert provider.cancelled.wait(timeout=2)
    assert provider.finished.wait(timeout=2)


@dataclass(slots=True)
class _ReleasedProvider:
    action: str
    emit_late_deltas: bool = False
    supports_tool_calls: bool = True
    name: str = "fixture"
    model_id: str = "fixture-model"
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, cancel_token
        self.started.set()
        assert self.release.wait(timeout=2)
        if self.emit_late_deltas:
            assert stream_sink is not None
            assert reasoning_sink is not None
            stream_sink("late text")
            reasoning_sink("late reasoning")
        self.finished.set()
        if self.action == "error":
            raise RuntimeError("late provider error")
        return _result(text="raced result")


@pytest.mark.parametrize("action", ["result", "error"])
def test_cancellation_signal_precedes_and_discards_late_completion(
    tmp_path: Path, action: str
) -> None:
    provider = _ReleasedProvider(action=action, emit_late_deltas=True)
    sink = _CollectingSink()

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        assert not done_event.is_set()
        cancel_event.set()
        provider.release.set()
        assert provider.finished.wait(timeout=2)
        return ProviderTurnInterruption.OPERATOR_ABORT

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=4,
        waiter=waiter,
    )

    assert outcome.result is None
    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert sink.events == []


def test_completion_precedes_later_cancel_signal_and_result_is_retained(
    tmp_path: Path,
) -> None:
    provider = _ReleasedProvider(action="result")

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        provider.release.set()
        assert done_event.wait(timeout=2)
        cancel_event.set()
        return ProviderTurnInterruption.OPERATOR_ABORT

    outcome = ProviderTurnExecutor().complete(
        provider,
        _request(tmp_path),
        _CollectingSink(),
        turn_index=0,
        waiter=waiter,
    )

    assert outcome.result is not None
    assert outcome.result.final_text == "raced result"
    assert outcome.cancellation_reason is None


def test_completion_precedes_later_cancel_signal_and_error_propagates(
    tmp_path: Path,
) -> None:
    provider = _ReleasedProvider(action="error")

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        provider.release.set()
        assert done_event.wait(timeout=2)
        cancel_event.set()
        return ProviderTurnInterruption.STEERING

    with pytest.raises(RuntimeError, match="late provider error"):
        ProviderTurnExecutor().complete(
            provider,
            _request(tmp_path),
            _CollectingSink(),
            turn_index=0,
            waiter=waiter,
        )


def test_abandoned_worker_cannot_admit_late_text_or_reasoning(
    tmp_path: Path,
) -> None:
    provider = _ReleasedProvider(action="result", emit_late_deltas=True)
    sink = _CollectingSink()

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        assert not done_event.is_set()
        cancel_event.set()
        return ProviderTurnInterruption.LOCAL_COMMAND

    outcome = ProviderTurnExecutor(cancel_join_timeout_seconds=0.0).complete(
        provider,
        _request(tmp_path),
        sink,
        turn_index=8,
        waiter=waiter,
    )
    provider.release.set()

    assert outcome.cancellation_reason is AgentCancellationReason.LOCAL_COMMAND
    assert provider.finished.wait(timeout=2)
    assert sink.events == []


@dataclass(slots=True)
class _BackpressuredProvider:
    supports_tool_calls: bool = True
    name: str = "fixture"
    model_id: str = "fixture-model"
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, cancel_token
        assert stream_sink is not None
        assert reasoning_sink is not None
        self.started.set()
        assert self.release.wait(timeout=2)
        stream_sink("admitted text")
        reasoning_sink("rejected reasoning")
        self.finished.set()
        return _result(text="late result")


def test_cancellation_does_not_block_on_admitted_delta_and_drops_later_delta(
    tmp_path: Path,
) -> None:
    provider = _BackpressuredProvider()
    entered_sink = threading.Event()
    release_sink = threading.Event()
    events: list[AgentEvent] = []

    class _BlockingSink:
        def emit(self, event: AgentEvent) -> None:
            entered_sink.set()
            assert release_sink.wait(timeout=2)
            events.append(event)

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        provider.release.set()
        assert entered_sink.wait(timeout=2)
        assert not done_event.is_set()
        cancel_event.set()
        return ProviderTurnInterruption.OPERATOR_ABORT

    outcome = ProviderTurnExecutor(cancel_join_timeout_seconds=0.0).complete(
        provider,
        _request(tmp_path),
        _BlockingSink(),
        turn_index=12,
        waiter=waiter,
    )
    release_sink.set()

    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert provider.finished.wait(timeout=2)
    assert events == [AssistantTextDelta(12, ProductContent("admitted text"))]


@pytest.mark.parametrize("failure", ["keyboard", "base", "invalid"])
def test_waiter_failures_cancel_and_reap_provider(tmp_path: Path, failure: str) -> None:
    provider = _CancelObservingProvider()

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ProviderTurnInterruption:
        assert provider.started.wait(timeout=2)
        assert not done_event.is_set()
        assert not cancel_event.is_set()
        if failure == "keyboard":
            raise KeyboardInterrupt
        if failure == "base":
            raise SystemExit(17)
        return cast(ProviderTurnInterruption, "invalid")

    expected: type[BaseException] = {
        "keyboard": KeyboardInterrupt,
        "base": SystemExit,
        "invalid": TypeError,
    }[failure]
    with pytest.raises(expected):
        ProviderTurnExecutor().complete(
            provider,
            _request(tmp_path),
            _CollectingSink(),
            turn_index=0,
            waiter=waiter,
        )

    assert provider.cancelled.wait(timeout=2)
    assert provider.finished.wait(timeout=2)
