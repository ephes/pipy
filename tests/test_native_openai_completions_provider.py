from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.openai_completions_provider import (
    JsonResponse,
    OpenAIChatCompletionsProvider,
    OpenAICompletionsHTTPStatusError,
    OpenAICompletionsResponseParseError,
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


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="openai-completions",
        model_id="gpt-4o-mini",
        cwd=tmp_path,
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "chatcmpl-provider-id-should-not-store",
                "object": "chat.completion",
                "model": "gpt-4o-mini-2024-07-18",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "hello from openai-completions",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 5,
                    "total_tokens": 16,
                    "unknown_provider_counter": 99,
                },
            },
        )
    )
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "openai-completions"
    assert result.model_id == "gpt-4o-mini"
    assert result.final_text == "hello from openai-completions"
    assert result.usage == {
        "input_tokens": 11,
        "output_tokens": 5,
        "total_tokens": 16,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }
    posted = client.requests[0]
    assert posted["url"] == "https://api.openai.com/v1/chat/completions"
    assert posted["headers"]["Authorization"] == "Bearer sk-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
            },
            {"role": "user", "content": "SAFE_GOAL_METADATA"},
        ],
        "stream": False,
    }


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":"docs"}',
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
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "call_abc123"
    assert call.tool_name == "lookup"
    assert call.arguments_json == '{"query":"docs"}'
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "tool_calls",
    }


def test_tool_result_round_trip(tmp_path):
    """A loop iteration with prior assistant tool_calls + tool result serializes
    to the chat-completions wire shape with `tool_calls` and a `tool` role.
    """

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "final answer after tool",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key="sk-test",
        http_client=client,
    )
    request = ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="",
        provider_name="openai-completions",
        model_id="gpt-4o-mini",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please lookup"),
            AssistantMessage(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="call_abc123",
                        tool_name="lookup",
                        arguments_json='{"query":"docs"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id="pipy-tool-0001",
                output_text="lookup result text",
                provider_correlation_id="call_abc123",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "final answer after tool"
    posted_messages = client.requests[0]["body"]["messages"]
    assert posted_messages == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "please lookup"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"query":"docs"}',
                    },
                }
            ],
            "content": "",
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": "lookup result text",
        },
    ]


def test_http_429_returns_failed_result(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "type": "rate_limit_exceeded",
                "code": "rate_limited",
                "message": "MODEL_OUTPUT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key="sk-test",
        http_client=FakeJsonHTTPClient(
            error=OpenAICompletionsHTTPStatusError.from_http_error(http_error)
        ),
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICompletionsHTTPStatusError"
    assert (
        result.error_message
        == "OpenAI API request failed with HTTP status 429."
    )
    assert result.metadata == {
        "http_status": 429,
        "api_error_type": "rate_limit_exceeded",
        "api_error_code": "rate_limited",
    }
    assert "MODEL_OUTPUT" not in json.dumps(result.metadata, sort_keys=True)
    assert "MODEL_OUTPUT" not in (result.error_message or "")


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key=None,
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICompletionsAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenAIChatCompletionsProvider(
        model_id="",
        api_key="sk-test",
        http_client=client,
    )
    request = ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="USER",
        provider_name="openai-completions",
        model_id="",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICompletionsConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"object": "chat.completion", "choices": []},
        )
    )
    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-4o-mini",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == OpenAICompletionsResponseParseError.__name__
    assert (
        result.error_message
        == "OpenAI response did not include a completion choice."
    )
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
    }
    assert result.final_text is None
