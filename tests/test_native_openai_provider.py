from __future__ import annotations

import json
import io
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.openai_provider import (
    JsonResponse,
    OpenAIHTTPStatusError,
    OpenAIResponsesProvider,
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
        provider_name="openai",
        model_id="gpt-test",
        cwd=tmp_path,
    )


def test_openai_provider_posts_responses_request_and_parses_output(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "model": "gpt-test-2026-01-01",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "hello"},
                            {"type": "output_text", "text": " world"},
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens": 2,
                    "output_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 12,
                },
            },
        )
    )
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key="sk-test", http_client=client)

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "openai"
    assert result.model_id == "gpt-test"
    assert result.final_text == "hello world"
    assert result.usage == {
        "cached_tokens": 3,
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 12,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }
    posted = client.requests[0]
    assert posted["url"] == "https://api.openai.com/v1/responses"
    assert posted["headers"]["Authorization"] == "Bearer sk-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "gpt-test",
        "instructions": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        "input": "SAFE_GOAL_METADATA",
        "store": False,
    }


def test_openai_provider_accepts_top_level_output_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "short text", "usage": {}},
        )
    )
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key="sk-test", http_client=client)

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "short text"


def test_openai_provider_rejects_empty_output_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "", "usage": {}},
        )
    )
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key="sk-test", http_client=client)

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAIResponseParseError"
    assert result.error_message == "OpenAI response did not include final output text."
    assert result.final_text is None


def test_openai_provider_missing_api_key_fails_without_http(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key=None, http_client=client)

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAIAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_openai_provider_http_error_keeps_message_conservative(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/responses",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=FakeJsonHTTPClient(error=OpenAIHTTPStatusError.from_http_error(http_error)),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAIHTTPStatusError"
    assert result.error_message == "OpenAI API request failed with HTTP status 400."
    assert result.metadata == {
        "api_error_code": "bad_request",
        "api_error_type": "invalid_request_error",
        "http_status": 400,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_openai_provider_non_success_boundary_status_fails_safely(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=503,
            body={"status": "completed", "output_text": "MODEL_OUTPUT_SHOULD_NOT_PRINT"},
        )
    )
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key="sk-test", http_client=client)

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAIHTTPStatusError"
    assert result.error_message == "OpenAI API request failed with HTTP status 503."
    assert result.metadata == {"http_status": 503}
    assert result.final_text is None


def test_urllib_json_http_client_translates_http_error_without_raw_body(monkeypatch):
    error_body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/responses",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_method() == "POST"
        assert timeout == 12.0
        assert request.headers["Content-type"] == "application/json"
        assert request.data == b'{"model": "gpt-test"}'
        raise http_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        UrllibJsonHTTPClient().post_json(
            "https://api.openai.com/v1/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=12.0,
        )
    except OpenAIHTTPStatusError as exc:
        assert str(exc) == "OpenAI API request failed with HTTP status 400."
        assert exc.metadata == {
            "api_error_code": "bad_request",
            "api_error_type": "invalid_request_error",
            "http_status": 400,
        }
        assert "SYSTEM_PROMPT" not in str(exc)
    else:
        raise AssertionError("expected OpenAIHTTPStatusError")
