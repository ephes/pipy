from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.azure_openai_provider import (
    AzureOpenAIHTTPStatusError,
    AzureOpenAIResponsesProvider,
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


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="azure-openai",
        model_id="gpt-4o-deployment",
        cwd=tmp_path,
    )


def _build_provider(
    client: FakeJsonHTTPClient,
    *,
    model_id: str = "gpt-4o-deployment",
    endpoint_url: str | None = "https://my-resource.openai.azure.com",
    api_key: str | None = "azure-key-test",
    api_version: str = "2024-12-01-preview",
    deployment: str | None = None,
) -> AzureOpenAIResponsesProvider:
    return AzureOpenAIResponsesProvider(
        model_id=model_id,
        endpoint_url=endpoint_url,
        api_key=api_key,
        api_version=api_version,
        deployment=deployment,
        http_client=client,
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "model": "gpt-4o-2024-08-06",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "hello"},
                            {"type": "output_text", "text": " from azure"},
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )
    )
    provider = _build_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "azure-openai"
    assert result.model_id == "gpt-4o-deployment"
    assert result.final_text == "hello from azure"
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
    assert posted["body"] == {
        "model": "gpt-4o-deployment",
        "instructions": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        "input": "SAFE_GOAL_METADATA",
        "store": False,
    }


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_azure_123",
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    }
                ],
            },
        )
    )
    provider = _build_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "call_azure_123"
    assert call.tool_name == "read_file"
    assert call.arguments_json == '{"path":"README.md"}'


def test_tool_result_round_trip(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "done"},
        )
    )
    provider = _build_provider(client)

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="ignored when messages are set",
        provider_name="azure-openai",
        model_id="gpt-4o-deployment",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please read README"),
            AssistantMessage(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="call_azure_123",
                        tool_name="read_file",
                        arguments_json='{"path":"README.md"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id="pipy-tool-0001",
                output_text="file contents",
                provider_correlation_id="call_azure_123",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "done"
    posted = client.requests[0]
    assert posted["body"]["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "please read README"}],
        },
        {
            "type": "function_call",
            "call_id": "call_azure_123",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_azure_123",
            "output": "file contents",
        },
    ]


def test_http_429_returns_failed_result(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "type": "rate_limit_error",
                "code": "429",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url=(
            "https://my-resource.openai.azure.com/openai/deployments/"
            "gpt-4o-deployment/responses?api-version=2024-12-01-preview"
        ),
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = _build_provider(
        FakeJsonHTTPClient(
            error=AzureOpenAIHTTPStatusError.from_http_error(http_error),
        ),
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIHTTPStatusError"
    assert (
        result.error_message
        == "Azure OpenAI API request failed with HTTP status 429."
    )
    assert result.metadata == {
        "api_error_code": "429",
        "api_error_type": "rate_limit_error",
        "http_status": 429,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_missing_endpoint_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _build_provider(client, endpoint_url=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIConfigurationError"
    assert "endpoint URL is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _build_provider(client, api_key=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _build_provider(client, model_id="")

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hi",
        provider_name="azure-openai",
        model_id="",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_uses_api_key_header_not_authorization(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "ok"},
        )
    )
    provider = _build_provider(client)

    provider.complete(_provider_request(tmp_path))

    headers = client.requests[0]["headers"]
    assert headers["api-key"] == "azure-key-test"
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_url_includes_deployment_and_api_version(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "ok"},
        )
    )
    provider = _build_provider(
        client,
        model_id="gpt-4o-deployment",
        endpoint_url="https://my-resource.openai.azure.com/",
        api_version="2024-12-01-preview",
    )

    provider.complete(_provider_request(tmp_path))

    posted_url = client.requests[0]["url"]
    assert posted_url == (
        "https://my-resource.openai.azure.com/openai/deployments/"
        "gpt-4o-deployment/responses?api-version=2024-12-01-preview"
    )


def test_url_uses_explicit_deployment_when_supplied(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "ok"},
        )
    )
    provider = _build_provider(
        client,
        model_id="gpt-4o",
        deployment="prod-gpt4",
        endpoint_url="https://my-resource.openai.azure.com",
    )

    provider.complete(_provider_request(tmp_path))

    posted_url = client.requests[0]["url"]
    assert (
        "/openai/deployments/prod-gpt4/responses?api-version=" in posted_url
    )


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "", "usage": {}},
        )
    )
    provider = _build_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIResponseParseError"
    assert (
        result.error_message
        == "Azure OpenAI response did not include final output text or tool calls."
    )
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }
