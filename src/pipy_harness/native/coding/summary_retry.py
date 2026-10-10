"""Bounded private-summary retry status, independent of assistant lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from pipy_harness.native.agent.events import AgentEvent, RetryCompleted, RetryScheduled
from pipy_harness.native.models import ProviderResult


class SummaryRetrySource(StrEnum):
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branchSummary"


class SummaryRetryOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


def _validate(source: SummaryRetrySource, attempt: int, maximum: int) -> None:
    if type(source) is not SummaryRetrySource:
        raise TypeError("summary source must be SummaryRetrySource")
    if type(attempt) is not int or type(maximum) is not int:
        raise TypeError("summary retry counts must be exact integers")
    if not 1 <= attempt <= maximum <= 9:
        raise ValueError("summary retry counts must be bounded reissue ordinals")


@dataclass(frozen=True, slots=True)
class SummaryRetryScheduled:
    source: SummaryRetrySource
    attempt: int
    max_attempts: int
    delay_ms: int

    def __post_init__(self) -> None:
        _validate(self.source, self.attempt, self.max_attempts)
        if type(self.delay_ms) is not int:
            raise TypeError("summary delay must be an exact integer")
        if not 0 <= self.delay_ms <= 120_000:
            raise ValueError("summary delay must be bounded milliseconds")


@dataclass(frozen=True, slots=True)
class SummaryRetryAttemptStarted:
    source: SummaryRetrySource
    attempt: int
    max_attempts: int

    def __post_init__(self) -> None:
        _validate(self.source, self.attempt, self.max_attempts)


@dataclass(frozen=True, slots=True)
class SummaryRetryFinished:
    source: SummaryRetrySource
    attempt: int
    max_attempts: int
    outcome: SummaryRetryOutcome

    def __post_init__(self) -> None:
        _validate(self.source, self.attempt, self.max_attempts)
        if type(self.outcome) is not SummaryRetryOutcome:
            raise TypeError("summary outcome must be SummaryRetryOutcome")


SummaryRetryStatus: TypeAlias = (
    SummaryRetryScheduled | SummaryRetryAttemptStarted | SummaryRetryFinished
)
SummaryRetryObserver: TypeAlias = Callable[[SummaryRetryStatus], None]
SummaryRetryLoader: TypeAlias = Callable[[SummaryRetrySource | None], None]


def summary_retry_event(status: SummaryRetryStatus) -> dict[str, object]:
    event: dict[str, object] = {
        "source": status.source.value,
        "attempt": status.attempt,
        "maxAttempts": status.max_attempts,
    }
    if isinstance(status, SummaryRetryScheduled):
        event.update(
            type="summarization_retry_scheduled",
            delayMs=status.delay_ms,
            errorMessage="Summarization failed",
        )
    elif isinstance(status, SummaryRetryAttemptStarted):
        event["type"] = "summarization_retry_attempt_start"
    else:
        event.update(type="summarization_retry_finished", outcome=status.outcome.value)
        if status.outcome is SummaryRetryOutcome.FAILED:
            event["errorMessage"] = "Summarization failed"
    return event


class PrivateSummaryRetryEvents:
    """Translate neutral executor boundaries without retaining private results."""

    def __init__(
        self, source: SummaryRetrySource, observer: SummaryRetryObserver | None
    ) -> None:
        self.source = source
        self.observer = observer
        self.observer_error: BaseException | None = None
        self.projected_persistence_error: Exception | None = None
        self._attempt = 0
        self._maximum = 0
        self._active = False
        self._override: SummaryRetryOutcome | None = None

    def _publish(self, status: SummaryRetryStatus) -> None:
        if self.observer is not None:
            try:
                self.observer(status)
            except BaseException as exc:
                if self.observer_error is not None:
                    if exc is not self.observer_error:
                        self.observer_error.add_note(
                            f"summary retry observer also failed: {type(exc).__name__}"
                        )
                    return
                self.observer_error = exc
                raise

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, RetryScheduled):
            self._attempt, self._maximum = event.attempt, event.max_attempts
            self._active = True
            self._publish(
                SummaryRetryScheduled(
                    self.source, event.attempt, event.max_attempts, event.delay_ms
                )
            )
        elif isinstance(event, RetryCompleted) and self._active:
            outcome = (
                SummaryRetryOutcome.SUCCEEDED
                if event.succeeded
                else SummaryRetryOutcome.FAILED
            )
            if (
                event.failure is not None
                and event.failure.error_type == "retry_cancelled"
            ):
                outcome = SummaryRetryOutcome.CANCELLED
            self.finish(self._override or outcome)

    def retry_attempt_failed(self, result: ProviderResult) -> None:
        del result

    def retry_attempt_started(self) -> None:
        self._publish(
            SummaryRetryAttemptStarted(self.source, self._attempt, self._maximum)
        )

    def arm_loader(self, loader: SummaryRetryLoader | None) -> None:
        if loader is not None:
            try:
                loader(self.source)
            except BaseException as error:
                self.observer_error = error
                raise

    def mark_stale(self) -> None:
        self._override = SummaryRetryOutcome.STALE

    def finish(self, outcome: SummaryRetryOutcome) -> None:
        if self._active:
            self._active = False
            self._publish(
                SummaryRetryFinished(self.source, self._attempt, self._maximum, outcome)
            )

    def cleanup(
        self, primary: BaseException | None, loader: SummaryRetryLoader | None
    ) -> None:
        callbacks: list[Callable[[], None]] = [
            lambda: self.finish(self._override or SummaryRetryOutcome.FAILED)
        ]
        if loader is not None:
            callbacks.append(lambda: loader(None))
        for callback in callbacks:
            try:
                callback()
            except BaseException as secondary:  # noqa: BLE001 - preserve primary and clear chrome
                if primary is None:
                    primary = secondary
                elif secondary is not primary:
                    primary.add_note(
                        f"summary retry cleanup failed: {type(secondary).__name__}"
                    )
        if primary is not None:
            raise primary


@contextmanager
def summary_retry_scope(
    source: SummaryRetrySource,
    observer: SummaryRetryObserver | None,
    loader: SummaryRetryLoader | None,
) -> Iterator[PrivateSummaryRetryEvents]:
    events = PrivateSummaryRetryEvents(source, observer)
    try:
        yield events
    except BaseException as primary:
        events.cleanup(primary, loader)
        raise
    else:
        try:
            events.cleanup(events.projected_persistence_error, loader)
        except BaseException as error:
            if error is not events.projected_persistence_error:
                raise
