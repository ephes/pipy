"""Pure policy and evidence checks for caller-managed provider attempts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from pipy_harness.native.models import ProviderResult
from pipy_harness.status import HarnessStatus


@dataclass(frozen=True, slots=True)
class ProviderManagedRetryPolicy:
    """Validated request-local bounds for canonical provider reissue."""

    max_attempts: int
    initial_delay_seconds: float
    max_delay_seconds: float
    multiplier: float = 2.0
    jitter_seconds: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise TypeError("max_attempts must be an int")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        _validate_finite_numbers(self)
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be nonnegative")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be at least initial_delay_seconds")
        if self.max_delay_seconds > 120:
            raise ValueError("max_delay_seconds must be at most 120")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.multiplier > 10:
            raise ValueError("multiplier must be at most 10")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds must be nonnegative")
        if self.jitter_seconds > 5:
            raise ValueError("jitter_seconds must be at most 5")

    def delay_seconds(
        self, retry_ordinal: int, result: ProviderResult, jitter: float
    ) -> float:
        """Choose one finite bounded exponential/jitter/server delay."""

        if isinstance(retry_ordinal, bool) or not isinstance(retry_ordinal, int):
            raise TypeError("retry_ordinal must be an int")
        if retry_ordinal < 1:
            raise ValueError("retry_ordinal must be positive")
        if isinstance(jitter, bool) or not isinstance(jitter, int | float):
            raise TypeError("jitter must be a number")
        if not isfinite(jitter):
            raise ValueError("jitter must be finite")
        bounded_jitter = min(1.0, max(0.0, float(jitter)))
        base = min(
            self.max_delay_seconds,
            self.initial_delay_seconds * self.multiplier ** (retry_ordinal - 1),
        )
        chosen = base + bounded_jitter * self.jitter_seconds
        metadata = result.metadata
        server: Any = (
            metadata.get("retry_after_seconds") if isinstance(metadata, dict) else None
        )
        if (
            isinstance(server, int | float)
            and not isinstance(server, bool)
            and isfinite(server)
            and server >= 0
        ):
            chosen = max(chosen, float(server))
        return min(self.max_delay_seconds, chosen)


def _validate_finite_numbers(policy: ProviderManagedRetryPolicy) -> None:
    for name in (
        "initial_delay_seconds",
        "max_delay_seconds",
        "multiplier",
        "jitter_seconds",
    ):
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"{name} must be a number")
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")


def is_managed_retry_eligible(result: ProviderResult, *, observed_delta: bool) -> bool:
    """Accept only explicit transient, no-progress, payload-free failures."""

    metadata = result.metadata
    return (
        result.status is HarnessStatus.FAILED
        and not observed_delta
        and result.final_text is None
        and result.tool_calls == ()
        and result.usage is None
        and isinstance(metadata, dict)
        and metadata.get("retryable") is True
        and metadata.get("progress") == "none"
    )
