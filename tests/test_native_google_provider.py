"""Tests for the Google Generative AI native provider."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.google_provider import (
    GoogleGenerativeAIProvider,
    JsonResponse,
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


def provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="google",
        model_id="gemini-test",
        cwd=tmp_path,
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hello"},
                                {"text": " world"},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 12,
                },
            },
        )
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key="key-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "google"
    assert result.model_id == "gemini-test"
    assert result.final_text == "hello world"
    assert result.tool_calls == ()
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "finish_reason": "STOP",
    }


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "read_file",
                                        "args": {"path": "README.md"},
                                    }
                                }
                            ]
                        },
                        "finishReason": "TOOL_USE",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 6,
                    "totalTokenCount": 11,
                },
            },
        )
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key="key-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, ProviderToolCall)
    assert call.tool_name == "read_file"
    assert call.provider_correlation_id == "google-tool-0"
    assert json.loads(call.arguments_json) == {"path": "README.md"}


def test_tool_result_round_trip(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "done"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            },
        )
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key="key-test",
        http_client=client,
    )

    tool_call = ProviderToolCall(
        provider_correlation_id="google-tool-0",
        tool_name="read_file",
        arguments_json=json.dumps({"path": "README.md"}, sort_keys=True),
    )
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT",
        user_prompt="UNUSED_FALLBACK",
        provider_name="google",
        model_id="gemini-test",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please read it"),
            AssistantMessage(content="thinking", tool_calls=(tool_call,)),
            ToolResultMessage(
                tool_request_id="pipy-tool-aaaa",
                output_text="file contents go here",
                provider_correlation_id="google-tool-0",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "done"

    posted = client.requests[0]
    assert posted["body"]["systemInstruction"] == {
        "parts": [{"text": "SYSTEM_PROMPT"}],
    }
    assert posted["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "please read it"}]},
        {
            "role": "model",
            "parts": [
                {"text": "thinking"},
                {
                    "functionCall": {
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    }
                },
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "read_file",
                        "response": {"result": "file contents go here"},
                    }
                }
            ],
        },
    ]


def test_http_429_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=429, body={"error": {"message": "rate limit"}})
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key="key-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleHTTPStatusError"
    assert result.error_message == "Google API request failed with HTTP status 429."
    assert result.metadata == {"http_status": 429}
    assert result.final_text is None


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key=None,
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = GoogleGenerativeAIProvider(
        model_id="",
        api_key="key-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"candidates": "not-a-list"},
        )
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-test",
        api_key="key-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleResponseParseError"
    assert result.final_text is None


def test_api_key_in_url_not_headers(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            },
        )
    )
    provider = GoogleGenerativeAIProvider(
        model_id="gemini-2.0-flash",
        api_key="SECRET_KEY_VALUE",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert "SECRET_KEY_VALUE" in posted["url"]
    assert "gemini-2.0-flash" in posted["url"]
    assert posted["headers"] == {"Content-Type": "application/json"}
    assert "Authorization" not in posted["headers"]
    # Ensure the request body does not also carry the key.
    body_text = json.dumps(posted["body"], sort_keys=True)
    assert "SECRET_KEY_VALUE" not in body_text
