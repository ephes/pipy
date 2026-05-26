"""Reusable retry-with-backoff helper for native provider HTTP calls.

This module is intentionally minimal: it wraps any callable that may raise
a provider HTTP error and retries on transient HTTP status codes. It uses
only the Python standard library (``time`` / ``random``) and avoids any
provider-specific knowledge so it can be shared by the native OpenAI and
OpenRouter providers (and any future JSON-over-HTTP provider that follows
the ``OpenAIProviderError`` / ``OpenRouterProviderError`` pattern of
exposing ``self.metadata['http_status']``).

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
) -> T:
    """Run ``operation`` with exponential backoff on retriable statuses.

    The callable is invoked up to ``policy.max_attempts`` times. Between
    attempts the helper sleeps for
    ``min(max_delay, initial_delay * multiplier ** (attempt - 1)) +
    jitter() * jitter_seconds`` seconds.

    Non-status exceptions and exceptions with an ``http_status`` outside
    ``policy.retriable_statuses`` are re-raised immediately without
    sleeping. After exhausting all attempts the last retriable exception
    is re-raised unchanged.
    """

    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001 — we re-raise unrecognized errors
            status = _extract_http_status(exc)
            if status is None or status not in policy.retriable_statuses:
                raise
            last_exc = exc
            if attempt >= policy.max_attempts:
                break
            base_delay = policy.initial_delay_seconds * (
                policy.multiplier ** (attempt - 1)
            )
            delay = min(policy.max_delay_seconds, base_delay) + jitter() * policy.jitter_seconds
            sleep(delay)
    assert last_exc is not None  # noqa: S101 — loop guarantees this is set when we exit via break
    raise last_exc
