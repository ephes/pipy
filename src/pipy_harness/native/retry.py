"""Reusable retry-with-backoff helper for native provider HTTP calls.

This module is intentionally minimal: by default it retries transient HTTP
status exceptions, while an optional decision hook lets a provider own a
narrower transport/progress taxonomy. It uses only the Python standard library
(``time`` / ``random``) and avoids provider-specific knowledge so it remains
shared by native providers.

The helper takes the policy as a dataclass and accepts injectable
``sleep`` / ``jitter`` callables so the tests can run hermetically with a
fake clock and deterministic jitter.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

DEFAULT_RETRIABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


@runtime_checkable
class RetryableStatusError(Protocol):
    """Structural protocol matched by provider HTTP-status errors.

    Any exception whose ``metadata`` mapping carries an ``http_status``
    integer field is treated as retry-aware. ``OpenAIHTTPStatusError``,
    ``OpenRouterHTTPStatusError`` and similar provider errors satisfy
    this shape because ``OpenAIProviderError`` /
    ``OpenRouterProviderError`` store ``metadata = dict(metadata or {})``
    and the HTTP-status constructors put ``http_status`` in there.
    """

    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Static configuration for retry-with-backoff.

    All fields are validated in ``__post_init__`` so a misconfigured
    policy fails loudly at construction time rather than producing odd
    behavior at retry time.
    """

    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 10.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.25
    retriable_statuses: frozenset[int] = field(default=DEFAULT_RETRIABLE_STATUSES)

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ValueError("max_attempts must be an int between 1 and 10.")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10.")
        if not (0 < self.initial_delay_seconds <= 30):
            raise ValueError("initial_delay_seconds must be > 0 and <= 30.")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds.")
        if self.max_delay_seconds > 120:
            raise ValueError("max_delay_seconds must be <= 120.")
        if not (1.0 <= self.multiplier <= 10.0):
            raise ValueError("multiplier must be between 1.0 and 10.0.")
        if not (0 <= self.jitter_seconds <= 5):
            raise ValueError("jitter_seconds must be between 0 and 5.")


def _extract_http_status(exc: BaseException) -> int | None:
    """Return ``metadata['http_status']`` if the exception exposes one."""

    metadata = getattr(exc, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    status = metadata.get("http_status")
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    should_retry: Callable[[BaseException], bool] | None = None,
    retry_after_seconds: Callable[[BaseException], float | None] | None = None,
) -> T:
    """Run ``operation`` with bounded exponential backoff.

    The callable is invoked up to ``policy.max_attempts`` times. Between
    attempts the helper sleeps for
    ``min(max_delay, initial_delay * multiplier ** (attempt - 1) +
    jitter() * jitter_seconds)`` seconds before applying any larger bounded
    server-requested delay.

    By default, only exceptions carrying an HTTP status in
    ``policy.retriable_statuses`` are retried, preserving the historical
    shared-provider behavior. A caller may inject ``should_retry`` to own a
    provider-specific exception/progress taxonomy. ``retry_after_seconds`` may
    request a minimum server delay; malformed/negative values are ignored and
    the final exponential+jitter/server delay is always capped by
    ``policy.max_delay_seconds``. After exhausting all attempts the last
    retriable exception is re-raised unchanged.
    """

    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001 — we re-raise unrecognized errors
            if should_retry is None:
                status = _extract_http_status(exc)
                retry = status is not None and status in policy.retriable_statuses
            else:
                retry = should_retry(exc)
            if not retry:
                raise
            last_exc = exc
            if attempt >= policy.max_attempts:
                break
            base_delay = policy.initial_delay_seconds * (
                policy.multiplier ** (attempt - 1)
            )
            delay = min(
                policy.max_delay_seconds,
                base_delay + jitter() * policy.jitter_seconds,
            )
            if retry_after_seconds is not None:
                requested_delay = retry_after_seconds(exc)
                if (
                    isinstance(requested_delay, int | float)
                    and not isinstance(requested_delay, bool)
                    and requested_delay >= 0
                ):
                    delay = min(
                        policy.max_delay_seconds,
                        max(delay, float(requested_delay)),
                    )
            sleep(delay)
    assert last_exc is not None  # noqa: S101 — loop guarantees this is set when we exit via break
    raise last_exc
