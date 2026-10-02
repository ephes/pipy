"""Pure policy and evidence checks for caller-managed provider attempts."""

from __future__ import annotations

import re
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


# Pi ``ai/src/utils/retry.ts`` (@4df157433), copied verbatim: quota and
# billing exhaustion is never retried, and the retryable pattern covers
# provider load, HTTP status, transport, stream-ending, and explicit
# retry-guidance failures.
_NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN = re.compile(
    "|".join(
        (
            "GoUsageLimitError",
            "FreeUsageLimitError",
            "Monthly usage limit reached",
            "available balance",
            "insufficient_quota",
            "out of budget",
            "quota exceeded",
            "billing",
            # Sign in with ChatGPT: the subscription's shared usage limit,
            # which resets after hours rather than seconds (Pi ``02eed88fd``).
            "subscription_sharing_usage_limit_exceeded",
        )
    ),
    re.IGNORECASE,
)
_RETRYABLE_PROVIDER_ERROR_PATTERN = re.compile(
    "|".join(
        (
            "overloaded",
            "Selected model is at capacity",
            "currently experiencing high demand",
            "rate.?limit",
            "too many requests",
            "429",
            "500",
            "502",
            "503",
            "504",
            "520",
            "524",
            "service.?unavailable",
            "server.?error",
            "internal.?error",
            "provider.?returned.?error",
            "exceeded request buffer limit while retrying upstream",
            "network.?error",
            "connection.?error",
            "connection.?refused",
            "connection.?lost",
            "other side closed",
            "fetch failed",
            "getaddrinfo",
            "ENOTFOUND",
            "EAI_AGAIN",
            "upstream.?connect",
            "reset before headers",
            "socket hang up",
            "socket connection was closed",
            "timed? out",
            "timeout",
            "terminated",
            "websocket.?closed",
            "websocket.?error",
            "ended without",
            "stream ended before message_stop",
            "stream ended before a terminal response event",
            "http2 request did not get a response",
            "retry delay",
            "you can retry your request",
            "try your request again",
            "please retry your request",
            "ResourceExhausted",
            # Sign in with ChatGPT: usage or user data temporarily unavailable.
            "subscription_sharing_usage_unavailable",
            "subscription_sharing_user_unavailable",
        )
    ),
    re.IGNORECASE,
)


# Pi ``ai/src/utils/overflow.ts`` (@4df157433) error-message case, copied
# verbatim. Pi's ``_isRetryableError`` vetoes context overflow before the
# retry patterns, because overflow prose often carries token counts that match
# the unanchored status digits (``50000`` matches ``500``).
_OVERFLOW_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"prompt (?:is )?too long",
        r"request_too_large",
        r"input is too long for requested model",
        r"exceeds the context window",
        r"exceeds (?:the )?(?:model'?s )?maximum context length"
        r"(?: of [\d,]+ tokens?|\s*\([\d,]+\))",
        r"input token count.*exceeds the maximum",
        r"maximum prompt length is \d+",
        r"reduce the length of the messages",
        r"maximum context length is \d+ tokens",
        r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",
        r"input \(\d+ tokens\) is longer than the model'?s context length"
        r" \(\d+ tokens\)",
        r"exceeds the limit of \d+",
        r"exceeds the available context size",
        r"greater than the context length",
        r"context window exceeds limit",
        r"exceeded model token limit",
        r"too large for model with \d+ maximum context length",
        r"prompt has [\d,]+ tokens?, but the configured context size is"
        r" [\d,]+ tokens?",
        r"model_context_window_exceeded",
        r"prompt too long; exceeded (?:max )?context length",
        r"range of input length should be",
        r"context[_ ]length[_ ]exceeded",
        r"too many tokens",
        r"token limit exceeded",
    )
)
_CEREBRAS_BODYLESS_OVERFLOW_PATTERN = re.compile(
    r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.IGNORECASE
)
_NON_OVERFLOW_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(Throttling error|Service unavailable):",
        r"rate limit",
        r"too many requests",
    )
)


def is_context_overflow_error_text(text: str, provider_name: str) -> bool:
    """Pi ``isContextOverflow`` for an error message (its first case)."""

    if not text or any(pattern.search(text) for pattern in _NON_OVERFLOW_PATTERNS):
        return False
    if any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS):
        return True
    return (
        provider_name == "cerebras"
        and _CEREBRAS_BODYLESS_OVERFLOW_PATTERN.search(text) is not None
    )


def is_retryable_error_text(text: str) -> bool:
    """Pi ``isRetryableAssistantError`` over one error text."""

    if not text or _NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN.search(text):
        return False
    return _RETRYABLE_PROVIDER_ERROR_PATTERN.search(text) is not None


def retry_error_text(result: ProviderResult) -> str:
    """The text Pi's classifier would see for one failed provider result.

    Pi matches the provider's error message, which usually carries the error
    body. pipy's sanitized messages do not, so the API error labels its HTTP
    errors lift from the body (``api_error_type``/``api_error_code``, e.g. a
    529 ``overloaded_error``) are appended.
    """

    parts = [result.error_message or ""]
    metadata = result.metadata
    if isinstance(metadata, dict):
        for key in ("api_error_type", "api_error_code"):
            value = metadata.get(key)
            if isinstance(value, str | int) and not isinstance(value, bool):
                parts.append(str(value))
    return " ".join(part for part in parts if part)


def is_managed_retry_eligible(result: ProviderResult) -> bool:
    """Pi's agent-level retry decision for one failed provider attempt.

    Like Pi there is no progress condition: an attempt that streamed text
    before failing is retried, and the retried attempt starts over. Context
    overflow (Pi leaves it to compaction) and quota or billing errors are
    never retried. pipy's structured ``retryable`` flag stands in for the
    transport error text Pi matches, which pipy's sanitized messages do not
    carry.
    """

    if result.status is not HarnessStatus.FAILED:
        return False
    text = retry_error_text(result)
    if is_context_overflow_error_text(text, result.provider_name):
        return False
    if _NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN.search(text):
        return False
    metadata = result.metadata
    if isinstance(metadata, dict) and metadata.get("retryable") is True:
        return True
    return is_retryable_error_text(text)
