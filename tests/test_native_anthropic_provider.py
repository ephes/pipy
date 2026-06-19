from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.anthropic_provider import (
    AnthropicHTTPStatusError,
    AnthropicProvider,
    JsonResponse,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


class FakeJsonHTTPClient:
    def __init__(
        self,
        response: JsonResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: object = None,
    ) -> JsonResponse:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="anthropic",
        model_id="claude-test",
        cwd=tmp_path,
    )


def provider_request_with_tool_round_trip(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="FOLLOWUP_PROMPT",
        provider_name="anthropic",
        model_id="claude-test",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please run the inspector"),
            AssistantMessage(
                content="working on it",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="toolu_01ABC",
                        tool_name="read_only_repo_inspection",
                        arguments_json='{"path": "src/main.py"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id="pipy-tool-0001",
                output_text="STATUS=succeeded bytes=120",
                provider_correlation_id="toolu_01ABC",
            ),
        ),
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-test-2026",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": " world"},
                ],
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "cache_creation_input_tokens": 4,
                    "cache_read_input_tokens": 2,
                },
            },
        )
    )
    provider = AnthropicProvider(
        model_id="claude-test", api_key="sk-test", http_client=client
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "anthropic"
    assert result.model_id == "claude-test"
    assert result.final_text == "hello world"
    assert result.usage == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 16,
        "cached_tokens": 2,
        "cache_write_tokens": 4,
    }
    assert result.metadata == {"stop_reason": "end_turn"}
    assert result.tool_calls == ()
    posted = client.requests[0]
    assert posted["url"] == "https://api.anthropic.com/v1/messages"
    assert posted["headers"]["x-api-key"] == "sk-test"
    assert posted["headers"]["anthropic-version"] == "2023-06-01"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"]["model"] == "claude-test"
    assert posted["body"]["max_tokens"] == 4096
    assert posted["body"]["system"] == "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"
    assert posted["body"]["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "SAFE_GOAL_METADATA"}],
        }
    ]
    assert "tools" not in posted["body"]


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "msg_tool",
                "type": "message",
                "role": "assistant",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "calling tool"},
                    {
                        "type": "tool_use",
                        "id": "toolu_42",
                        "name": "read_only_repo_inspection",
                        "input": {"path": "README.md", "max_lines": 80},
                    },
                ],
                "usage": {"input_tokens": 9, "output_tokens": 4},
            },
        )
    )
    provider = AnthropicProvider(
        model_id="claude-test", api_key="sk-test", http_client=client
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "calling tool"
    assert result.metadata == {"stop_reason": "tool_use"}
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "toolu_42"
    assert call.tool_name == "read_only_repo_inspection"
    assert json.loads(call.arguments_json) == {
        "path": "README.md",
        "max_lines": 80,
    }
    # The arguments must be deterministically serialized (sorted keys).
    assert call.arguments_json == json.dumps(
        {"max_lines": 80, "path": "README.md"}, sort_keys=True
    )


def test_tool_result_round_trip(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "msg_followup",
                "type": "message",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "all set"}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )
    )
    provider = AnthropicProvider(
        model_id="claude-test", api_key="sk-test", http_client=client
    )

    result = provider.complete(provider_request_with_tool_round_trip(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["body"]["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "please run the inspector"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "working on it"},
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC",
                    "name": "read_only_repo_inspection",
                    "input": {"path": "src/main.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01ABC",
                    "content": "STATUS=succeeded bytes=120",
                }
            ],
        },
    ]


def test_http_429_returns_failed_result(tmp_path):
    error_body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "SECRET_RATE_LIMIT_DETAIL_SHOULD_NOT_LEAK",
            },
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = AnthropicProvider(
        model_id="claude-test",
        api_key="sk-test",
        http_client=FakeJsonHTTPClient(
            error=AnthropicHTTPStatusError.from_http_error(http_error)
        ),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AnthropicHTTPStatusError"
    assert result.error_message == "Anthropic API request failed with HTTP status 429."
    assert result.metadata == {
        "api_error_type": "rate_limit_error",
        "http_status": 429,
    }
    assert "SECRET" not in json.dumps(result.metadata, sort_keys=True)
    assert "SECRET" not in (result.error_message or "")


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = AnthropicProvider(
        model_id="claude-test", api_key=None, http_client=client
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AnthropicAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = AnthropicProvider(
        model_id="", api_key="sk-test", http_client=client
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AnthropicConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "msg_empty",
                "type": "message",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )
    )
    provider = AnthropicProvider(
        model_id="claude-test", api_key="sk-test", http_client=client
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AnthropicResponseParseError"
    assert (
        result.error_message
        == "Anthropic response did not include final output text or tool calls."
    )
    assert result.final_text is None


def test_urllib_json_http_client_translates_http_error_without_raw_body(monkeypatch):
    error_body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            },
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        assert request.full_url == "https://api.anthropic.com/v1/messages"
        assert request.get_method() == "POST"
        assert timeout == 12.0
        assert request.headers["Content-type"] == "application/json"
        raise http_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        UrllibJsonHTTPClient().post_json(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            body={"model": "claude-test"},
            timeout_seconds=12.0,
        )
    except AnthropicHTTPStatusError as exc:
        assert str(exc) == "Anthropic API request failed with HTTP status 400."
        assert exc.metadata == {
            "api_error_type": "invalid_request_error",
            "http_status": 400,
        }
        assert "SYSTEM_PROMPT" not in str(exc)
    else:
        raise AssertionError("expected AnthropicHTTPStatusError")
