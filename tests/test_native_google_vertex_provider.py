"""Tests for the Google Vertex AI native provider."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.google_vertex_provider import (
    GoogleVertexProvider,
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
        provider_name="google-vertex",
        model_id="gemini-2.0-flash-001",
        cwd=tmp_path,
    )


def _make_provider(
    client: FakeJsonHTTPClient, **overrides: Any
) -> GoogleVertexProvider:
    defaults: dict[str, Any] = {
        "model_id": "gemini-2.0-flash-001",
        "project_id": "my-gcp-project",
        "location": "us-central1",
        "access_token": "ya29.EXAMPLE_ACCESS_TOKEN",
        # Keep ADC tests hermetic regardless of a developer's ambient
        # GOOGLE_CLOUD_API_KEY (which would otherwise flip on express mode).
        "api_key": None,
        "http_client": client,
    }
    defaults.update(overrides)
    return GoogleVertexProvider(**defaults)


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
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "google-vertex"
    assert result.model_id == "gemini-2.0-flash-001"
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
        "vertex_auth_mode": "adc",
        "google_cloud_location": "us-central1",
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
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, ProviderToolCall)
    assert call.tool_name == "read_file"
    assert call.provider_correlation_id == "google-vertex-tool-0"
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
    provider = _make_provider(client)

    tool_call = ProviderToolCall(
        provider_correlation_id="google-vertex-tool-0",
        tool_name="read_file",
        arguments_json=json.dumps({"path": "README.md"}, sort_keys=True),
    )
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT",
        user_prompt="UNUSED_FALLBACK",
        provider_name="google-vertex",
        model_id="gemini-2.0-flash-001",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please read it"),
            AssistantMessage(content="thinking", tool_calls=(tool_call,)),
            ToolResultMessage(
                tool_request_id="pipy-tool-aaaa",
                output_text="file contents go here",
                provider_correlation_id="google-vertex-tool-0",
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
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexHTTPStatusError"
    assert result.error_message == (
        "Google Vertex AI request failed with HTTP status 429."
    )
    assert result.metadata == {"http_status": 429}
    assert result.final_text is None


def test_missing_access_token_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _make_provider(client, access_token=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexAuthError"
    assert "bearer access value must be set" in (result.error_message or "")
    assert client.requests == []


def test_missing_project_id_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _make_provider(client, project_id=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexConfigurationError"
    assert "project id is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _make_provider(client, model_id="")

    request = ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="SAFE_GOAL",
        provider_name="google-vertex",
        model_id="",
        cwd=tmp_path,
    )
    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_url_includes_project_location_and_model(tmp_path):
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
    provider = _make_provider(
        client,
        project_id="my-gcp-project",
        location="europe-west4",
        model_id="gemini-2.0-flash-001",
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["url"] == (
        "https://europe-west4-aiplatform.googleapis.com/v1/projects/"
        "my-gcp-project/locations/europe-west4/publishers/google/models/"
        "gemini-2.0-flash-001:generateContent"
    )


def test_uses_bearer_authorization_header(tmp_path):
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
    provider = _make_provider(
        client,
        access_token="SECRET_BEARER_TOKEN_VALUE",
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["headers"]["Authorization"] == "Bearer SECRET_BEARER_TOKEN_VALUE"
    assert posted["headers"]["Content-Type"] == "application/json"
    # Bearer token must ride in the header, never in the URL or body.
    assert "SECRET_BEARER_TOKEN_VALUE" not in posted["url"]
    body_text = json.dumps(posted["body"], sort_keys=True)
    assert "SECRET_BEARER_TOKEN_VALUE" not in body_text


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"candidates": "not-a-list"},
        )
    )
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexResponseParseError"
    assert result.final_text is None


def _ok_response() -> JsonResponse:
    return JsonResponse(
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


def test_express_api_key_uses_global_endpoint_and_header(tmp_path):
    # Vertex Express mode: an API key routes to the global aiplatform host with
    # no project/location path segment and the x-goog-api-key header. No access
    # token or project is required.
    client = FakeJsonHTTPClient(_ok_response())
    provider = _make_provider(
        client,
        api_key="EXPRESS_API_KEY_VALUE",
        access_token=None,
        project_id=None,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ok"
    posted = client.requests[0]
    assert posted["url"] == (
        "https://aiplatform.googleapis.com/v1/publishers/google/models/"
        "gemini-2.0-flash-001:generateContent"
    )
    assert posted["headers"]["x-goog-api-key"] == "EXPRESS_API_KEY_VALUE"
    assert "Authorization" not in posted["headers"]
    # The api key must ride in the header, never in the URL or body.
    assert "EXPRESS_API_KEY_VALUE" not in posted["url"]
    body_text = json.dumps(posted["body"], sort_keys=True)
    assert "EXPRESS_API_KEY_VALUE" not in body_text
    # Auth-mode metadata reflects express; no location is sent in this mode.
    assert result.metadata["vertex_auth_mode"] == "api-key"
    assert "google_cloud_location" not in result.metadata


def test_express_api_key_serializes_body_like_adc(tmp_path):
    # The request body (contents/systemInstruction/tools) is identical between
    # express and ADC mode; only host/auth differ.
    client = FakeJsonHTTPClient(_ok_response())
    provider = _make_provider(
        client,
        api_key="EXPRESS_API_KEY_VALUE",
        access_token=None,
        project_id=None,
    )
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello there",
        provider_name="google-vertex",
        model_id="gemini-2.0-flash-001",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["body"]["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert posted["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "hello there"}]}
    ]


def test_placeholder_api_key_falls_back_to_adc(tmp_path):
    # The ambient <authenticated> sentinel matches Pi's placeholder pattern, so
    # it must NOT be used as an express key — fall back to the ADC bearer path.
    client = FakeJsonHTTPClient(_ok_response())
    provider = _make_provider(client, api_key="<authenticated>")

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["headers"]["Authorization"] == "Bearer ya29.EXAMPLE_ACCESS_TOKEN"
    assert "x-goog-api-key" not in posted["headers"]
    assert "us-central1-aiplatform.googleapis.com" in posted["url"]
    assert result.metadata["vertex_auth_mode"] == "adc"


def test_marker_api_key_falls_back_to_adc(tmp_path):
    # Pi's gcp-vertex-credentials marker signals "use ADC", not an express key.
    client = FakeJsonHTTPClient(_ok_response())
    provider = _make_provider(client, api_key="gcp-vertex-credentials")

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["headers"]["Authorization"] == "Bearer ya29.EXAMPLE_ACCESS_TOKEN"
    assert "x-goog-api-key" not in posted["headers"]
    assert result.metadata["vertex_auth_mode"] == "adc"


def test_express_api_key_merges_extra_headers(tmp_path):
    client = FakeJsonHTTPClient(_ok_response())
    provider = _make_provider(
        client,
        api_key="EXPRESS_API_KEY_VALUE",
        access_token=None,
        project_id=None,
        extra_headers={"x-custom": "v1"},
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["headers"]["x-goog-api-key"] == "EXPRESS_API_KEY_VALUE"
    assert posted["headers"]["x-custom"] == "v1"
