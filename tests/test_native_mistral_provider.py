from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.mistral_provider import (
    JsonResponse,
    MistralHTTPStatusError,
    MistralProvider,
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


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="mistral",
        model_id="mistral-large-latest",
        cwd=tmp_path,
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "gen-provider-id-should-not-store",
                "object": "chat.completion",
                "model": "mistral-large-latest",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello from mistral"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-mistral-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "mistral"
    assert result.model_id == "mistral-large-latest"
    assert result.final_text == "hello from mistral"
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }
    posted = client.requests[0]
    assert posted["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert posted["headers"]["Authorization"] == "Bearer sk-mistral-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "mistral-large-latest",
        "messages": [
            {"role": "system", "content": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"},
            {"role": "user", "content": "SAFE_GOAL_METADATA"},
        ],
    }


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\":\"README.md\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-mistral-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert not result.final_text
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "call_abc123"
    assert call.tool_name == "read_file"
    assert call.arguments_json == '{"path":"README.md"}'


def test_tool_result_round_trip(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-mistral-test",
        http_client=client,
    )

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="ignored when messages are set",
        provider_name="mistral",
        model_id="mistral-large-latest",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please read README"),
            AssistantMessage(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="call_abc123",
                        tool_name="read_file",
                        arguments_json='{"path":"README.md"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id="pipy-tool-0001",
                output_text="file contents",
                provider_correlation_id="call_abc123",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "done"
    posted = client.requests[0]
    assert posted["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "please read README"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": "file contents",
        },
    ]


def test_http_429_returns_failed_result(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "code": 429,
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.mistral.ai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-mistral-test",
        http_client=FakeJsonHTTPClient(
            error=MistralHTTPStatusError.from_http_error(http_error),
        ),
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralHTTPStatusError"
    assert result.error_message == "Mistral API request failed with HTTP status 429."
    assert result.metadata == {
        "api_error_code": "429",
        "http_status": 429,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key=None,
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = MistralProvider(
        model_id="",
        api_key="sk-mistral-test",
        http_client=client,
    )

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hi",
        provider_name="mistral",
        model_id="",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [],
            },
        )
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-mistral-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralResponseParseError"
    assert (
        result.error_message
        == "Mistral response did not include a completion choice."
    )
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
    }
