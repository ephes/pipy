"""Canonical managed retries for private semantic compaction summaries."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from test_native_semantic_compaction import _fixture, _published, _result

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.product_session import CodingProductSessionCoordinator
from pipy_harness.native.coding.state import CodingContextChangedError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    ProviderAttemptAllowance,
    StreamChunkSink,
)
from pipy_harness.native.repl import provider_selection
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects


def _transient(**changes: Any) -> ProviderResult:
    return replace(
        _result(
            "unused failure text",
            status=HarnessStatus.FAILED,
            error_type="TransientError",
            error_message="try later",
            metadata={"retryable": True, "progress": "none"},
        ),
        **({"final_text": None} | changes),
    )


@dataclass(slots=True)
class _PreparedSummaryProvider:
    results: list[ProviderResult]
    name: str = "fake"
    model_id: str = "summary-model"
    supports_tool_calls: bool = True
    requests: list[ProviderRequest] = field(default_factory=list)
    allowances: list[ProviderAttemptAllowance] = field(default_factory=list)
    sinks: list[tuple[StreamChunkSink | None, StreamChunkSink | None]] = field(
        default_factory=list
    )
    ordinary_calls: int = 0
    on_attempt: Callable[[int], None] | None = None

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        del request
        self.ordinary_calls += 1
        raise AssertionError("managed semantic summary must use prepared completion")

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> object:
        self.requests.append(request)
        self.sinks.append((stream_sink, reasoning_sink))
        owner = self

        class _Handle:
            def complete_attempt(
                self, allowance: ProviderAttemptAllowance
            ) -> ProviderResult:
                owner.allowances.append(allowance)
                if owner.on_attempt is not None:
                    owner.on_attempt(allowance.attempt)
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled()
                return owner.results[allowance.attempt - 1]

        return _Handle()


def _prepared_fixture(
    tmp_path: Path, results: list[ProviderResult]
) -> tuple[ProviderMutationEffects, _PreparedSummaryProvider]:
    effects, _provider = _fixture(tmp_path)
    provider = _PreparedSummaryProvider(results)
    effects.coding_state.refresh_provider(provider)
    return effects, provider


def test_transient_summary_recovers_with_one_frozen_request_and_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, provider = _prepared_fixture(
        tmp_path,
        [
            _transient(),
            _result("RECOVERED", usage={"input_tokens": 7, "output_tokens": 3}),
        ],
    )
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)

    def change_settings(attempt: int) -> None:
        if attempt == 1:
            effects.settings.set_value("retry.maxRetries", 0)

    provider.on_attempt = change_settings
    before_entries = effects.ctl.session_tree.get_entries()
    before_usage = effects.coding_state.usage_snapshot()
    header_calls = 0
    estimate_calls = 0
    original_header = effects.extension_operations.provider_header_callback
    original_estimate = getattr(provider_selection, "estimate_request")

    def header(_owner: object, tree: object) -> object:
        nonlocal header_calls
        header_calls += 1
        return original_header(tree)  # type: ignore[arg-type]

    def estimate(
        request: ProviderRequest, *, image_count: int, output_reserve: int
    ) -> object:
        nonlocal estimate_calls
        estimate_calls += 1
        return original_estimate(
            request, image_count=image_count, output_reserve=output_reserve
        )

    monkeypatch.setattr(
        type(effects.extension_operations), "provider_header_callback", header
    )
    monkeypatch.setattr(provider_selection, "estimate_request", estimate)

    outcome = effects.compact_context("manual")

    assert "compacted conversation" in outcome.notice
    assert provider.ordinary_calls == 0
    assert len(provider.requests) == 1
    assert provider.sinks == [(None, None)]
    assert effects.coding_state.usage_snapshot() == before_usage
    assert header_calls == estimate_calls == 1
    assert [(item.attempt, item.max_attempts) for item in provider.allowances] == [
        (1, 2),
        (2, 2),
    ]
    entries = effects.ctl.session_tree.get_entries()
    assert len(entries) == len(before_entries) + 1
    assert sum(entry.type == "compaction" for entry in entries) == 1


def test_exhausted_summary_retry_publishes_nothing(tmp_path: Path) -> None:
    effects, provider = _prepared_fixture(tmp_path, [_transient(), _transient()])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    before = _published(effects)

    outcome = effects.compact_context("manual")

    assert outcome.notice == "pipy: compaction failed; context unchanged."
    assert [(item.attempt, item.max_attempts) for item in provider.allowances] == [
        (1, 2),
        (2, 2),
    ]
    assert _published(effects) == before


def test_recovered_summary_acceptance_survives_persistence_failure(
    tmp_path: Path,
) -> None:
    effects, provider = _prepared_fixture(
        tmp_path, [_transient(), _result("RECOVERED")]
    )
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    original_port = effects.product_session._port

    def fail_persistence(_action: object) -> None:
        raise OSError("durable append failed")

    product = CodingProductSessionCoordinator(
        state=effects.coding_state,
        port=replace(
            original_port,
            apply_compaction_callback=fail_persistence,  # type: ignore[type-var]
        ),
    )
    product.rebuild_active_history()
    effects = replace(effects, product_session=product)
    before_messages = effects.coding_state.messages

    with pytest.raises(OSError, match="durable append failed"):
        effects.compact_context("manual")

    assert len(provider.allowances) == 2
    assert effects.coding_state.messages != before_messages
    assert effects.coding_state.compaction_count == 1


@pytest.mark.parametrize(
    "blocked",
    [
        _transient(final_text="partial"),
        _transient(usage={"input_tokens": 1}),
        _transient(metadata={"retryable": True, "progress": "accepted"}),
    ],
)
def test_progress_payload_or_usage_prevents_private_summary_retry(
    tmp_path: Path, blocked: ProviderResult
) -> None:
    effects, provider = _prepared_fixture(tmp_path, [blocked, _result("unused")])
    effects.settings.set_value("retry.maxRetries", 3)
    before = _published(effects)

    outcome = effects.compact_context("manual")

    assert outcome.notice == "pipy: compaction failed; context unchanged."
    assert [(item.attempt, item.max_attempts) for item in provider.allowances] == [
        (1, 4)
    ]
    assert _published(effects) == before


@pytest.mark.parametrize("trigger", ["manual", "auto"])
def test_original_compaction_witness_blocks_reissue(
    tmp_path: Path, trigger: str
) -> None:
    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("unused")])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    before: list[tuple[object, ...]] = []

    def mutate_after_first_attempt(attempt: int) -> None:
        if attempt == 1:
            effects.coding_state.mirror_history(effects.coding_state.messages)
            before.append(_published(effects))

    provider.on_attempt = mutate_after_first_attempt
    if trigger == "auto":
        with pytest.raises(CodingContextChangedError):
            effects.compact_context(trigger)
    else:
        assert (
            effects.compact_context(trigger).notice
            == "pipy: compact refused: context changed."
        )
    assert len(provider.allowances) == 1
    assert _published(effects) == before[0]


def test_external_abort_between_attempts_cancels_private_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("unused")])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 50)
    abort = threading.Event()
    effects = replace(
        effects,
        abort_event=abort,
        provider_turn_executor=ProviderTurnExecutor(cancel_join_timeout_seconds=0.01),
    )
    original_inputs = getattr(provider_selection, "provider_turn_inputs")
    wait_count = 0

    def inputs(*args: object, **kwargs: object) -> object:
        nonlocal wait_count
        wrapped, waiter = original_inputs(*args, **kwargs)  # type: ignore[arg-type]
        assert waiter is not None

        def counting_waiter(*wait_args: object) -> object:
            nonlocal wait_count
            wait_count += 1
            if wait_count == 2:
                abort.set()
            return waiter(*wait_args)  # type: ignore[arg-type]

        return wrapped, counting_waiter

    monkeypatch.setattr(provider_selection, "provider_turn_inputs", inputs)
    before = _published(effects)

    outcome = effects.compact_context("manual")

    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert len(provider.allowances) == 1
    assert wait_count == 2
    assert _published(effects) == before


def test_external_abort_during_reissued_worker_rejects_late_summary(
    tmp_path: Path,
) -> None:
    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("unused")])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    abort = threading.Event()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    effects = replace(
        effects,
        abort_event=abort,
        provider_turn_executor=ProviderTurnExecutor(cancel_join_timeout_seconds=0.01),
    )

    def block_reissue(attempt: int) -> None:
        if attempt == 2:
            started.set()
            assert release.wait(2)
            finished.set()

    provider.on_attempt = block_reissue
    before = _published(effects)
    outcomes: list[object] = []
    worker = threading.Thread(
        target=lambda: outcomes.append(effects.compact_context("manual"))
    )
    worker.start()
    assert started.wait(2)
    abort.set()
    worker.join(1)
    try:
        assert not worker.is_alive()
        outcome = outcomes[0]
        assert (
            getattr(outcome, "cancellation_reason")
            is AgentCancellationReason.OPERATOR_ABORT
        )
        assert len(provider.allowances) == 2
        assert _published(effects) == before
    finally:
        release.set()
        assert finished.wait(2)
    assert _published(effects) == before


def test_callback_abort_signal_gates_prepared_retry_and_settles_cancellation(
    tmp_path: Path,
) -> None:
    class Signal:
        def __init__(self) -> None:
            self._set = False
            self._callback: Callable[[], None] | None = None
            self.registrations = 0
            self.removals = 0

        def is_set(self) -> bool:
            return self._set

        def register_cancel_callback(
            self, callback: Callable[[], None]
        ) -> Callable[[], None]:
            assert self._callback is None
            self.registrations += 1
            self._callback = callback

            def remove() -> None:
                if self._callback is callback:
                    self._callback = None
                    self.removals += 1

            return remove

        def set(self) -> None:
            self._set = True
            callback = self._callback
            assert callback is not None
            callback()

    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("unused")])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    signal = Signal()
    effects = replace(
        effects,
        abort_event=signal,
        provider_turn_executor=ProviderTurnExecutor(cancel_join_timeout_seconds=0.01),
    )
    before = _published(effects)

    def cancel_reissue(attempt: int) -> None:
        assert signal._callback is not None
        if attempt == 2:
            signal.set()

    provider.on_attempt = cancel_reissue

    outcome = effects.compact_context("manual")

    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert [(item.attempt, item.max_attempts) for item in provider.allowances] == [
        (1, 2),
        (2, 2),
    ]
    assert signal.registrations == signal.removals == 3
    assert signal._callback is None
    assert provider.ordinary_calls == 0
    assert _published(effects) == before
