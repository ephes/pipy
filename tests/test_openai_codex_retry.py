"""OpenAI Codex provider retries only transient pre-progress failures.

A single 503 from the Codex Responses endpoint used to bubble up as
`OpenAICodexHTTPStatusError` and end the REPL turn. The provider now
wraps the `post_sse` call in `retry_with_backoff` so transient
failures recover before the tool loop sees the exception.
"""

from __future__ import annotations

import errno
import http.client
import io
import json
import urllib.error
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from email.message import Message
from email.utils import formatdate
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
    OpenAICodexResponsesProvider,
    OpenAICodexStreamInterruptedError,
    OpenAICodexTransportError,
    SseResponse,
    UrllibSseHTTPClient,
)
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    ProductContent,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.retry import RetryPolicy
from pipy_harness.native.tools.base import ToolDefinition


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
        timeout_seconds: float | None,
        cancel_token: object = None,
    ) -> SseResponse:
        del url, body, timeout_seconds
        self.calls.append({"index": len(self.calls), "headers": dict(headers)})
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
        retry_sleep=_zero_sleep,
        retry_jitter=_zero_jitter,
    )
    del monkeypatch

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


def test_codex_header_hook_runs_once_and_reuses_snapshot_across_retries() -> None:
    client = _RetryHTTPClient(failures=2)
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
        transport="sse",
        retry_policy=RetryPolicy(max_attempts=4, initial_delay_seconds=0.001),
        retry_sleep=_zero_sleep,
        retry_jitter=_zero_jitter,
    )
    hook_calls = 0

    def mutate(headers):
        nonlocal hook_calls
        hook_calls += 1
        headers["X-Trace"] = "trace-once"

    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="hi",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=Path("."),
        provider_header_callback=mutate,
    )

    result = provider.complete(request)

    assert result.status is HarnessStatus.SUCCEEDED
    assert hook_calls == 1
    assert len(client.calls) == 3
    assert all(call["headers"]["X-Trace"] == "trace-once" for call in client.calls)


def test_codex_stops_after_max_attempts(monkeypatch: pytest.MonkeyPatch):
    client = _RetryHTTPClient(failures=10)
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.001),
        retry_sleep=_zero_sleep,
        retry_jitter=_zero_jitter,
    )
    del monkeypatch

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
    assert result.metadata == {
        "attempt": 3,
        "exhausted": True,
        "http_status": 503,
        "max_attempts": 3,
        "progress": "none",
        "retryable": True,
    }


def _request() -> ProviderRequest:
    return ProviderRequest(
        system_prompt="sys",
        user_prompt="hi",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=Path("."),
    )


def _success_response(text: str = "ok") -> SseResponse:
    return SseResponse(
        status_code=200,
        body=(
            f'data: {{"type": "response.output_text.delta", "delta": "{text}"}}\n\n'
            'data: {"type": "response.completed", "response": '
            '{"status": "completed"}}\n\n'
        ),
    )


@dataclass
class _SequenceHTTPClient:
    outcomes: list[SseResponse | BaseException]
    calls: int = 0
    bodies: list[dict[str, Any]] = field(default_factory=list)

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float | None,
        cancel_token: object = None,
    ) -> SseResponse:
        del url, headers, timeout_seconds, cancel_token
        self.bodies.append(dict(body))
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _provider(
    client: _SequenceHTTPClient,
    *,
    max_attempts: int = 3,
    sleeps: list[float] | None = None,
    retry_sleep: object | None = None,
    retry_clock: object | None = None,
    supports_tool_search: bool = False,
) -> OpenAICodexResponsesProvider:
    sleep_log = sleeps if sleeps is not None else []
    kwargs: dict[str, Any] = {}
    if callable(retry_clock):
        kwargs["retry_clock"] = retry_clock
    return OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
        retry_policy=RetryPolicy(
            max_attempts=max_attempts,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            jitter_seconds=0.0,
        ),
        retry_sleep=retry_sleep if callable(retry_sleep) else sleep_log.append,
        retry_jitter=_zero_jitter,
        supports_tool_search=supports_tool_search,
        **kwargs,
    )


def _stream_failure() -> OpenAICodexStreamInterruptedError:
    return OpenAICodexStreamInterruptedError(
        "OpenAI Codex stream was interrupted before completion.",
        metadata={"phase": "stream", "retryable": True, "transport": "sse"},
    )


def _events_then_error(
    events: list[Mapping[str, Any]], error: BaseException
) -> Iterator[Mapping[str, Any]]:
    yield from events
    raise error


class _RawSseResponse:
    def __init__(
        self, lines: list[bytes] | None = None, *, error: BaseException | None = None
    ) -> None:
        self._lines = iter(lines or [])
        self._error = error
        self.closed = False

    def getcode(self) -> int:
        return 200

    def __iter__(self) -> _RawSseResponse:
        return self

    def __next__(self) -> bytes:
        if self._error is not None:
            raise self._error
        return next(self._lines)

    def close(self) -> None:
        self.closed = True


class _CloseAwareEvents:
    def __init__(
        self,
        events: list[Mapping[str, Any]],
        *,
        error_after: BaseException | None = None,
    ) -> None:
        self._events = iter(events)
        self._error_after = error_after
        self.closed = False

    def __iter__(self) -> _CloseAwareEvents:
        return self

    def __next__(self) -> Mapping[str, Any]:
        try:
            return next(self._events)
        except StopIteration:
            if self._error_after is not None:
                raise self._error_after
            raise

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "raw_error",
    [
        TimeoutError("The read operation timed out"),
        OSError(errno.ECONNRESET, "PRIVATE_RESET_DETAIL"),
        http.client.IncompleteRead(b"partial", 20),
        http.client.RemoteDisconnected("PRIVATE_DISCONNECT_DETAIL"),
    ],
    ids=["timeout", "reset", "truncated", "disconnect"],
)
def test_real_urllib_stream_failures_retry_before_first_event(
    monkeypatch: pytest.MonkeyPatch, raw_error: BaseException
) -> None:
    failed = _RawSseResponse(error=raw_error)
    recovered = _RawSseResponse(
        [
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n',
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"\n",
        ]
    )
    responses = iter([failed, recovered])
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *_args, **_kwargs: next(responses),
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=UrllibSseHTTPClient(),
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            jitter_seconds=0.0,
        ),
        retry_sleep=_zero_sleep,
        retry_jitter=_zero_jitter,
    )

    result = provider.complete(_request())

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert failed.closed is True
    assert recovered.closed is True


@pytest.mark.parametrize(
    "first_outcome",
    [
        OpenAICodexTransportError(
            "OpenAI Codex transport failed while waiting for response headers.",
            metadata={"phase": "headers", "retryable": True, "transport": "sse"},
        ),
        SseResponse(
            status_code=200,
            body="",
            event_stream=_events_then_error([], _stream_failure()),
        ),
        SseResponse(status_code=200, body=""),
    ],
    ids=["headers", "stream-read", "missing-terminal"],
)
def test_pre_event_transport_failures_retry_without_duplicate_output(
    first_outcome: SseResponse | BaseException,
) -> None:
    sleeps: list[float] = []
    client = _SequenceHTTPClient([first_outcome, _success_response()])
    chunks: list[str] = []

    result = _provider(client, sleeps=sleeps).complete(
        _request(), stream_sink=chunks.append
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert client.calls == 2
    assert sleeps == [1.0]
    assert chunks == ["ok"]


def test_tool_search_body_and_derived_id_are_stable_across_retry() -> None:
    first = OpenAICodexTransportError(
        "OpenAI Codex transport failed while waiting for response headers.",
        metadata={"phase": "headers", "retryable": True, "transport": "sse"},
    )
    client = _SequenceHTTPClient([first, _success_response()])
    late_tool = ToolDefinition(
        name="late_tool",
        description="late tool",
        input_schema={"type": "object", "properties": {}},
    )
    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="load",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=Path("."),
        messages=(
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        "call_loader|fc_loader", "loader", ProductContent("{}")
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-load",
                tool_name="loader",
                content=ProductContent("loaded"),
                provider_correlation_id="call_loader|fc_loader",
                added_tool_names=("late_tool",),
            ),
        ),
        available_tools=(late_tool,),
    )

    result = _provider(client, supports_tool_search=True).complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert client.bodies[0] == client.bodies[1]
    search_ids = [
        item["call_id"]
        for item in client.bodies[0]["input"]
        if item.get("type") == "tool_search_call"
    ]
    assert search_ids == ["pi_tool_load_frjjneqko5wi"]


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "response.created", "response": {"id": "safe"}}],
        [{"type": "response.reasoning_summary_text.delta", "delta": "think"}],
        [{"type": "response.output_text.delta", "delta": "partial"}],
        [
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "item-1",
                    "call_id": "call-1",
                    "name": "read",
                },
            }
        ],
        [
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": "item-1",
                    "call_id": "call-1",
                    "name": "read",
                    "arguments": '{"path":"notes.txt"}',
                },
            }
        ],
    ],
    ids=["metadata", "reasoning", "text", "partial-tool", "complete-tool"],
)
def test_post_event_stream_failure_is_never_replayed(
    events: list[Mapping[str, Any]],
) -> None:
    client = _SequenceHTTPClient(
        [
            SseResponse(
                status_code=200,
                body="",
                event_stream=_events_then_error(events, _stream_failure()),
            ),
            _success_response("duplicate"),
        ]
    )
    chunks: list[str] = []
    reasoning: list[str] = []

    result = _provider(client).complete(
        _request(),
        stream_sink=chunks.append,
        reasoning_sink=reasoning.append,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexStreamInterruptedError"
    assert result.tool_calls == ()
    assert result.metadata is not None
    assert result.metadata["progress"] == "event"
    assert result.metadata["retryable"] is True
    assert result.metadata["exhausted"] is False
    assert client.calls == 1
    assert "duplicate" not in chunks


def test_missing_terminal_after_text_is_not_replayed() -> None:
    client = _SequenceHTTPClient(
        [
            SseResponse(
                status_code=200,
                body='data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
            ),
            _success_response("duplicate"),
        ]
    )
    chunks: list[str] = []

    result = _provider(client).complete(_request(), stream_sink=chunks.append)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexStreamInterruptedError"
    assert result.metadata is not None
    assert result.metadata["progress"] == "event"
    assert client.calls == 1
    assert chunks == ["partial"]


def test_incomplete_terminal_discards_assembled_tool_call_without_retry() -> None:
    events = [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-1",
                "name": "read",
                "arguments": '{"path":"notes.txt"}',
            },
        },
        {
            "type": "response.incomplete",
            "response": {"status": "incomplete"},
        },
    ]
    response = SseResponse(
        status_code=200,
        body="".join(f"data: {json.dumps(event)}\n\n" for event in events),
    )
    client = _SequenceHTTPClient([response, _success_response("duplicate")])

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.FAILED
    assert result.error_message == (
        "OpenAI Codex response did not complete successfully."
    )
    assert result.metadata is not None
    assert result.metadata["response_status"] == "incomplete"
    assert result.tool_calls == ()
    assert client.calls == 1


@pytest.mark.parametrize("terminal", ["incomplete", "failed", "cancelled"])
def test_first_unsuccessful_terminal_cannot_be_overwritten_by_completed(
    terminal: str,
) -> None:
    events = [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-1",
                "name": "write",
                "arguments": '{"path":"must-not-run"}',
            },
        },
        {
            "type": f"response.{terminal}",
            "response": {"status": terminal},
        },
        {
            "type": "response.completed",
            "response": {"status": "completed"},
        },
    ]
    client = _SequenceHTTPClient(
        [
            SseResponse(
                status_code=200,
                body="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )
        ]
    )

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.FAILED
    assert result.metadata is not None
    assert result.metadata["response_status"] == terminal
    assert result.tool_calls == ()
    assert client.calls == 1


def test_completed_terminal_ignores_late_transport_failure_and_closes_iterator() -> (
    None
):
    events = _CloseAwareEvents(
        [
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {"status": "completed"},
            },
        ],
        error_after=_stream_failure(),
    )
    client = _SequenceHTTPClient(
        [SseResponse(status_code=200, body="", event_stream=events)]
    )

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert result.tool_calls == ()
    assert events.closed is True
    assert client.calls == 1


def test_tool_and_text_events_after_terminal_are_ignored() -> None:
    events = [
        {"type": "response.output_text.delta", "delta": "ok"},
        {
            "type": "response.completed",
            "response": {"status": "completed"},
        },
        {"type": "response.output_text.delta", "delta": "duplicate"},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "late-item",
                "call_id": "late-call",
                "name": "write",
                "arguments": '{"path":"must-not-run"}',
            },
        },
    ]
    client = _SequenceHTTPClient(
        [
            SseResponse(
                status_code=200,
                body="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )
        ]
    )
    chunks: list[str] = []

    result = _provider(client).complete(_request(), stream_sink=chunks.append)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert result.tool_calls == ()
    assert chunks == ["ok"]


def test_body_fixture_completed_terminal_ignores_later_malformed_json() -> None:
    body = (
        'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        "data: {MALFORMED_AFTER_TERMINAL\n\n"
    )
    client = _SequenceHTTPClient([SseResponse(status_code=200, body=body)])

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    assert result.tool_calls == ()


@pytest.mark.parametrize("terminal", ["failed", "incomplete", "cancelled"])
def test_body_fixture_failure_terminal_ignores_later_malformed_json(
    terminal: str,
) -> None:
    body = (
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": "item-1",
                    "call_id": "call-1",
                    "name": "write",
                    "arguments": '{"path":"must-not-run"}',
                },
            }
        )
        + "\n\ndata: "
        + json.dumps(
            {
                "type": f"response.{terminal}",
                "response": {"status": terminal},
            }
        )
        + "\n\ndata: {MALFORMED_AFTER_TERMINAL\n\n"
    )
    client = _SequenceHTTPClient([SseResponse(status_code=200, body=body)])

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.FAILED
    assert result.metadata is not None
    assert result.metadata["response_status"] == terminal
    assert result.tool_calls == ()


@pytest.mark.parametrize("payload", [[], None, "text", 1, True])
def test_body_fixture_non_object_event_is_terminal_protocol_failure(
    payload: object,
) -> None:
    client = _SequenceHTTPClient(
        [
            SseResponse(
                status_code=200,
                body=f"data: {json.dumps(payload)}\n\n",
            ),
            _success_response("must-not-retry"),
        ]
    )
    chunks: list[str] = []
    reasoning: list[str] = []

    result = _provider(client).complete(
        _request(), stream_sink=chunks.append, reasoning_sink=reasoning.append
    )

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexResponseParseError"
    assert result.error_message == "OpenAI Codex stream included a non-object event."
    assert result.metadata is not None
    assert result.metadata["progress"] == "none"
    assert result.metadata["retryable"] is False
    assert result.metadata["attempt"] == 1
    assert result.tool_calls == ()
    assert chunks == []
    assert reasoning == []
    assert client.calls == 1


@pytest.mark.parametrize(
    "lines",
    [
        [b"data: []\n", b"\n"],
        [b"data: []\n"],
    ],
    ids=["blank-line-terminated", "eof-terminated"],
)
def test_real_urllib_non_object_event_is_non_retryable_and_closes_response(
    monkeypatch: pytest.MonkeyPatch, lines: list[bytes]
) -> None:
    failed = _RawSseResponse(lines)
    recovered = _RawSseResponse(
        [
            b'data: {"type":"response.output_text.delta","delta":"must-not-retry"}\n',
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"\n",
        ]
    )
    responses = iter([failed, recovered])
    calls = 0

    def fake_open_url(*_args: object, **_kwargs: object) -> _RawSseResponse:
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        fake_open_url,
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=UrllibSseHTTPClient(),
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            jitter_seconds=0.0,
        ),
        retry_sleep=_zero_sleep,
        retry_jitter=_zero_jitter,
    )
    chunks: list[str] = []

    result = provider.complete(_request(), stream_sink=chunks.append)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexResponseParseError"
    assert result.metadata is not None
    assert result.metadata["retryable"] is False
    assert result.metadata["attempt"] == 1
    assert result.tool_calls == ()
    assert chunks == []
    assert calls == 1
    assert failed.closed is True
    assert recovered.closed is False


def _raw_http_error(
    status: int,
    *,
    headers: Message | None = None,
    api_error: Mapping[str, Any] | None = None,
) -> urllib.error.HTTPError:
    payload = json.dumps({"error": dict(api_error or {})}).encode("utf-8")
    return urllib.error.HTTPError(
        url="https://chatgpt.com/backend-api/codex/responses",
        code=status,
        msg="provider status",
        hdrs=Message() if headers is None else headers,
        fp=io.BytesIO(payload),
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_terminal_http_statuses_are_not_retried(status: int) -> None:
    client = _SequenceHTTPClient([_raw_http_error(status), _success_response()])

    result = _provider(client).complete(_request())

    assert result.status == HarnessStatus.FAILED
    assert result.metadata is not None
    assert result.metadata["retryable"] is False
    assert client.calls == 1


@pytest.mark.parametrize(
    "api_label",
    [
        "insufficient_quota",
        "billing_hard_limit_reached",
        "usage_limit_reached",
        "quota_exceeded",
    ],
)
def test_terminal_quota_429_is_not_retried(api_label: str) -> None:
    client = _SequenceHTTPClient(
        [
            _raw_http_error(429, api_error={"code": api_label}),
            _success_response(),
        ]
    )

    result = _provider(client).complete(_request())

    assert result.metadata is not None
    assert result.metadata["api_error_code"] == api_label
    assert result.metadata["retryable"] is False
    assert client.calls == 1


@pytest.mark.parametrize(
    ("header_name", "header_value", "now_seconds", "expected_sleep"),
    [
        ("retry-after-ms", "90000", 0.0, 10.0),
        ("Retry-After", "7", 0.0, 7.0),
        ("Retry-After", formatdate(1_010, usegmt=True), 1_000.0, 10.0),
        ("Retry-After", formatdate(990, usegmt=True), 1_000.0, 1.0),
        ("Retry-After", "not-a-date", 0.0, 1.0),
    ],
)
def test_retry_after_headers_raise_delay_within_policy_cap(
    header_name: str,
    header_value: str,
    now_seconds: float,
    expected_sleep: float,
) -> None:
    headers = Message()
    headers[header_name] = header_value
    sleeps: list[float] = []
    client = _SequenceHTTPClient(
        [_raw_http_error(503, headers=headers), _success_response()]
    )

    result = _provider(
        client,
        sleeps=sleeps,
        retry_clock=lambda: now_seconds,
    ).complete(_request())

    assert result.status == HarnessStatus.SUCCEEDED
    assert sleeps == [expected_sleep]
    assert client.calls == 2


def test_provider_clock_reaches_real_urllib_retry_after_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = formatdate(1_007, usegmt=True)
    http_error = _raw_http_error(503, headers=headers)
    recovered = _RawSseResponse(
        [
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n',
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"\n",
        ]
    )
    outcomes: Iterator[BaseException | _RawSseResponse] = iter([http_error, recovered])

    def fake_open_url(*_args: object, **_kwargs: object) -> _RawSseResponse:
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        fake_open_url,
    )
    sleeps: list[float] = []
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=UrllibSseHTTPClient(),
        retry_policy=RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=1.0,
            max_delay_seconds=10.0,
            jitter_seconds=0.0,
        ),
        retry_sleep=sleeps.append,
        retry_jitter=_zero_jitter,
        retry_clock=lambda: 1_000.0,
    )

    result = provider.complete(_request())

    assert result.status == HarnessStatus.SUCCEEDED
    assert sleeps == [7.0]


def test_cancellation_during_backoff_aborts_without_second_attempt() -> None:
    token = CancelToken()
    client = _SequenceHTTPClient(
        [
            OpenAICodexTransportError(
                "OpenAI Codex transport failed while waiting for response headers.",
                metadata={
                    "phase": "headers",
                    "retryable": True,
                    "transport": "sse",
                },
            ),
            _success_response("must-not-run"),
        ]
    )

    def cancel_sleep(_delay: float) -> None:
        token.cancel()

    provider = _provider(client, retry_sleep=cancel_sleep)

    with pytest.raises(ProviderCancelledError):
        provider.complete(_request(), cancel_token=token)

    assert client.calls == 1
