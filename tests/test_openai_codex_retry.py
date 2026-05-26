"""OpenAI Codex provider retries transient HTTP 5xx/429 statuses.

A single 503 from the Codex Responses endpoint used to bubble up as
`OpenAICodexHTTPStatusError` and end the REPL turn. The provider now
wraps the `post_sse` call in `retry_with_backoff` so transient
failures recover before the tool loop sees the exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
    OpenAICodexResponsesProvider,
    SseResponse,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.retry import RetryPolicy


def _credentials() -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token="test-token",
        refresh_token="refresh",
        expires_at=10**12,
        account_id="acct",
    )


class _InMemoryCredentialStore:
    def __init__(self, credentials: OpenAICodexCredentials) -> None:
        self._credentials = credentials

    def load(self) -> OpenAICodexCredentials | None:
        return self._credentials

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self._credentials = credentials

    def delete(self) -> bool:
        return False


@dataclass
class _RetryHTTPClient:
    """SSE stub that returns 503 a configurable number of times then 200."""

    failures: int
    successful_body: str = (
        'data: {"type": "response.output_text.delta", "delta": "ok"}\n\n'
        'data: {"type": "response.completed", "response": {"status": "completed"}}\n\n'
    )
    calls: list[Mapping[str, Any]] = field(default_factory=list)

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> SseResponse:
        del url, headers, body, timeout_seconds
        self.calls.append({"index": len(self.calls)})
        if len(self.calls) <= self.failures:
            return SseResponse(status_code=503, body="")
        return SseResponse(status_code=200, body=self.successful_body)


def _zero_sleep(_seconds: float) -> None:
    return None


def _zero_jitter() -> float:
    return 0.0


def test_codex_retries_503_until_success(monkeypatch: pytest.MonkeyPatch):
    client = _RetryHTTPClient(failures=2)
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
        retry_policy=RetryPolicy(max_attempts=4, initial_delay_seconds=0.001),
    )
    # Use the helper module's `time.sleep` injection by monkeypatching
    # the retry module so the test stays hermetic and fast.
    monkeypatch.setattr(
        "pipy_harness.native.retry.time.sleep", _zero_sleep, raising=False
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="sys",
            user_prompt="hi",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=Path("."),
        )
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert len(client.calls) == 3  # two 503s then one success


def test_codex_stops_after_max_attempts(monkeypatch: pytest.MonkeyPatch):
    client = _RetryHTTPClient(failures=10)
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.001),
    )
    monkeypatch.setattr(
        "pipy_harness.native.retry.time.sleep", _zero_sleep, raising=False
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="sys",
            user_prompt="hi",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=Path("."),
        )
    )

    # After max_attempts the provider returns a failed result with the
    # 503 metadata; the tool loop now keeps the REPL alive on this
    # surface instead of tearing down the whole session.
    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexHTTPStatusError"
    assert len(client.calls) == 3
