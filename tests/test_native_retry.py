"""Hermetic tests for the native retry-with-backoff helper."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from pipy_harness.native.retry import (
    DEFAULT_RETRIABLE_STATUSES,
    RetryPolicy,
    retry_with_backoff,
)


class FakeHTTPStatusError(Exception):
    """Tiny stand-in for OpenAIHTTPStatusError / OpenRouterHTTPStatusError."""

    def __init__(self, status: int, message: str = "boom") -> None:
        super().__init__(message)
        self.metadata: Mapping[str, Any] = {"http_status": status}


class FakeClock:
    """Records sleep calls without actually sleeping."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _zero_jitter() -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"max_attempts": -1},
        {"initial_delay_seconds": 0.0},
        {"initial_delay_seconds": -0.1},
        {"initial_delay_seconds": 31.0},
        {"max_delay_seconds": 0.1},  # below initial_delay_seconds=0.5 default
        {"max_delay_seconds": 121.0},
        {"multiplier": 0.5},
        {"multiplier": 10.5},
        {"jitter_seconds": -0.1},
        {"jitter_seconds": 5.1},
    ],
)
def test_retry_policy_validates_bounds(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_retry_policy_defaults_match_invariants() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.initial_delay_seconds == 0.5
    assert policy.max_delay_seconds == 10.0
    assert policy.multiplier == 2.0
    assert policy.jitter_seconds == 0.25
    assert policy.retriable_statuses == DEFAULT_RETRIABLE_STATUSES


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_success_on_first_attempt_no_sleep() -> None:
    clock = FakeClock()
    calls = {"count": 0}

    def op() -> str:
        calls["count"] += 1
        return "ok"

    result = retry_with_backoff(
        op,
        policy=RetryPolicy(),
        sleep=clock,
        jitter=_zero_jitter,
    )

    assert result == "ok"
    assert calls["count"] == 1
    assert clock.sleeps == []


def test_retry_on_429_then_success() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def op() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise FakeHTTPStatusError(429)
        return "ok"

    result = retry_with_backoff(
        op,
        policy=RetryPolicy(),
        sleep=clock,
        jitter=_zero_jitter,
    )

    assert result == "ok"
    assert attempts == [1, 2]
    assert len(clock.sleeps) == 1


def test_retry_on_500_then_success() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def op() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise FakeHTTPStatusError(500)
        return "fine"

    result = retry_with_backoff(
        op,
        policy=RetryPolicy(),
        sleep=clock,
        jitter=_zero_jitter,
    )

    assert result == "fine"
    assert attempts == [1, 2]
    assert len(clock.sleeps) == 1


def test_exhausts_max_attempts_raises_last() -> None:
    clock = FakeClock()
    calls = {"count": 0}
    raised: list[FakeHTTPStatusError] = []

    def op() -> str:
        calls["count"] += 1
        err = FakeHTTPStatusError(503, f"attempt-{calls['count']}")
        raised.append(err)
        raise err

    with pytest.raises(FakeHTTPStatusError) as excinfo:
        retry_with_backoff(
            op,
            policy=RetryPolicy(max_attempts=3),
            sleep=clock,
            jitter=_zero_jitter,
        )

    assert calls["count"] == 3
    # The final raised exception is the one that propagates out.
    assert excinfo.value is raised[-1]
    # Two sleeps: between attempts 1-2 and 2-3. No sleep after the final attempt.
    assert len(clock.sleeps) == 2


def test_non_retriable_400_re_raises_immediately() -> None:
    clock = FakeClock()
    calls = {"count": 0}

    def op() -> str:
        calls["count"] += 1
        raise FakeHTTPStatusError(400)

    with pytest.raises(FakeHTTPStatusError):
        retry_with_backoff(
            op,
            policy=RetryPolicy(),
            sleep=clock,
            jitter=_zero_jitter,
        )

    assert calls["count"] == 1
    assert clock.sleeps == []


def test_non_status_exception_re_raises_immediately() -> None:
    clock = FakeClock()
    calls = {"count": 0}

    def op() -> str:
        calls["count"] += 1
        raise ValueError("not an http error")

    with pytest.raises(ValueError):
        retry_with_backoff(
            op,
            policy=RetryPolicy(),
            sleep=clock,
            jitter=_zero_jitter,
        )

    assert calls["count"] == 1
    assert clock.sleeps == []


def test_exception_with_metadata_but_no_http_status_is_not_retried() -> None:
    """metadata without http_status must not be treated as retriable."""

    clock = FakeClock()
    calls = {"count": 0}

    class WeirdError(Exception):
        def __init__(self) -> None:
            super().__init__("weird")
            self.metadata: Mapping[str, Any] = {"api_error_code": "rate_limit"}

    def op() -> str:
        calls["count"] += 1
        raise WeirdError()

    with pytest.raises(WeirdError):
        retry_with_backoff(
            op,
            policy=RetryPolicy(),
            sleep=clock,
            jitter=_zero_jitter,
        )

    assert calls["count"] == 1
    assert clock.sleeps == []


def test_backoff_grows_exponentially() -> None:
    clock = FakeClock()
    calls = {"count": 0}

    def op() -> str:
        calls["count"] += 1
        raise FakeHTTPStatusError(503)

    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=1.0,
        max_delay_seconds=120.0,
        multiplier=2.0,
        jitter_seconds=0.0,
    )

    with pytest.raises(FakeHTTPStatusError):
        retry_with_backoff(op, policy=policy, sleep=clock, jitter=_zero_jitter)

    # Four sleeps between five attempts: 1, 2, 4, 8 seconds (no jitter).
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]
    # Strictly monotonically increasing.
    assert clock.sleeps == sorted(clock.sleeps)
    assert all(b > a for a, b in zip(clock.sleeps[:-1], clock.sleeps[1:], strict=True))


def test_max_delay_caps_growth() -> None:
    clock = FakeClock()

    def op() -> str:
        raise FakeHTTPStatusError(503)

    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=1.0,
        max_delay_seconds=3.0,
        multiplier=2.0,
        jitter_seconds=0.0,
    )

    with pytest.raises(FakeHTTPStatusError):
        retry_with_backoff(op, policy=policy, sleep=clock, jitter=_zero_jitter)

    # Uncapped sequence would be 1, 2, 4, 8 — capped at 3.0.
    assert clock.sleeps == [1.0, 2.0, 3.0, 3.0]


def test_jitter_is_added_on_top_of_base_delay() -> None:
    clock = FakeClock()

    def op() -> str:
        raise FakeHTTPStatusError(503)

    policy = RetryPolicy(
        max_attempts=2,
        initial_delay_seconds=1.0,
        max_delay_seconds=10.0,
        multiplier=2.0,
        jitter_seconds=0.5,
    )

    def fixed_jitter() -> float:
        return 0.4

    with pytest.raises(FakeHTTPStatusError):
        retry_with_backoff(op, policy=policy, sleep=clock, jitter=fixed_jitter)

    # 1.0 (base) + 0.4 * 0.5 (jitter) = 1.2
    assert clock.sleeps == pytest.approx([1.2])


def test_custom_retriable_statuses_overrides_defaults() -> None:
    clock = FakeClock()
    calls = {"count": 0}

    def op() -> str:
        calls["count"] += 1
        raise FakeHTTPStatusError(429)

    # 429 removed from the retriable set — should re-raise immediately.
    policy = RetryPolicy(retriable_statuses=frozenset({500, 502, 503, 504}))

    with pytest.raises(FakeHTTPStatusError):
        retry_with_backoff(op, policy=policy, sleep=clock, jitter=_zero_jitter)

    assert calls["count"] == 1
    assert clock.sleeps == []
