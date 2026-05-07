from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.openrouter_provider import (
    JsonResponse,
    OpenRouterChatCompletionsProvider,
    OpenRouterHTTPStatusError,
    UrllibJsonHTTPClient,
)


class FakeJsonHTTPClient:
    def __init__(self, response: JsonResponse | None = None, error: Exception | None = None) -> None:
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
        provider_name="openrouter",
        model_id="openai/gpt-test",
        cwd=tmp_path,
    )


def test_openrouter_provider_posts_chat_completion_request_and_parses_output(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "gen-provider-id-should-not-store",
                "object": "chat.completion",
                "model": "openai/gpt-test-2026-01-01",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello from openrouter"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "unknown_provider_counter": 99,
                },
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "openrouter"
    assert result.model_id == "openai/gpt-test"
    assert result.final_text == "hello from openrouter"
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
    assert posted["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert posted["headers"]["Authorization"] == "Bearer sk-or-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "openai/gpt-test",
        "messages": [
            {"role": "system", "content": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"},
            {"role": "user", "content": "SAFE_GOAL_METADATA"},
        ],
        "stream": False,
    }


def test_openrouter_provider_accepts_text_content_parts(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "part one"},
                                {"type": "text", "text": " and two"},
                                {"type": "image_url", "image_url": {"url": "ignored"}},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "part one and two"


def test_openrouter_provider_omits_unknown_unavailable_and_non_counter_usage(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"content": "short text"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": True,
                    "completion_tokens": 2,
                    "total_tokens": float("inf"),
                    "cached_tokens": -1,
                    "reasoning_tokens": 1.5,
                    "native_unlisted": 42,
                },
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.usage == {
        "output_tokens": 2,
        "reasoning_tokens": 1.5,
    }


def test_openrouter_provider_missing_api_key_fails_without_http(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key=None,
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_openrouter_provider_empty_api_key_fails_without_http(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="  ",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_openrouter_provider_http_error_keeps_message_conservative(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "code": 400,
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
                "metadata": {"raw": "MODEL_OUTPUT_SHOULD_NOT_BE_STORED"},
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=FakeJsonHTTPClient(error=OpenRouterHTTPStatusError.from_http_error(http_error)),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterHTTPStatusError"
    assert result.error_message == "OpenRouter API request failed with HTTP status 400."
    assert result.metadata == {
        "api_error_code": "400",
        "http_status": 400,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "MODEL_OUTPUT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_openrouter_provider_non_success_boundary_status_fails_safely(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=503,
            body={
                "object": "chat.completion",
                "choices": [{"message": {"content": "MODEL_OUTPUT_SHOULD_NOT_PRINT"}}],
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterHTTPStatusError"
    assert result.error_message == "OpenRouter API request failed with HTTP status 503."
    assert result.metadata == {"http_status": 503}
    assert result.final_text is None


def test_openrouter_provider_top_level_error_fails_without_raw_message(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "error": {
                    "code": "provider_error",
                    "message": "MODEL_OUTPUT_SHOULD_NOT_BE_STORED",
                }
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterResponseParseError"
    assert result.error_message == "OpenRouter response included an error."
    assert result.metadata == {
        "api_error_code": "provider_error",
        "provider_response_store_requested": False,
    }
    assert "MODEL_OUTPUT" not in json.dumps(result.metadata, sort_keys=True)


def test_openrouter_provider_rejects_empty_message_content(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterResponseParseError"
    assert result.error_message == "OpenRouter response did not include final message content."
    assert result.metadata == {
        "finish_reason": "stop",
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
    }
    assert result.final_text is None


def test_openrouter_provider_rejects_malformed_choices(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"object": "chat.completion", "choices": []})
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenRouterResponseParseError"
    assert result.error_message == "OpenRouter response did not include a completion choice."
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
    }


def test_urllib_json_http_client_translates_http_error_without_raw_body(monkeypatch):
    error_body = json.dumps(
        {
            "error": {
                "code": 401,
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
                "metadata": {"raw": "MODEL_OUTPUT_SHOULD_NOT_BE_STORED"},
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.get_method() == "POST"
        assert timeout == 12.0
        assert request.headers["Content-type"] == "application/json"
        assert request.data == b'{"model": "openai/gpt-test"}'
        raise http_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        UrllibJsonHTTPClient().post_json(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body={"model": "openai/gpt-test"},
            timeout_seconds=12.0,
        )
    except OpenRouterHTTPStatusError as exc:
        assert str(exc) == "OpenRouter API request failed with HTTP status 401."
        assert exc.metadata == {
            "api_error_code": "401",
            "http_status": 401,
        }
        assert "SYSTEM_PROMPT" not in str(exc)
    else:
        raise AssertionError("expected OpenRouterHTTPStatusError")
