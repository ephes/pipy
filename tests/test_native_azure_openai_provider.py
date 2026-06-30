from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.azure_openai_provider import (
    AzureOpenAIHTTPStatusError,
    AzureOpenAIResponsesProvider,
    JsonResponse,
    _parse_deployment_name_map,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


@pytest.fixture(autouse=True)
def _clear_azure_env(monkeypatch) -> None:
    """Keep config resolution hermetic against the host environment.

    The adapter resolves the base URL and deployment from process env at request
    time (``AZURE_OPENAI_BASE_URL`` / ``AZURE_OPENAI_RESOURCE_NAME`` /
    ``AZURE_OPENAI_DEPLOYMENT_NAME_MAP``); clear them so each test controls its
    own inputs.
    """

    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_RESOURCE_NAME",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
    ):
        monkeypatch.delenv(name, raising=False)


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
    api_version: str = "v1",
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
            "https://my-resource.openai.azure.com/openai/v1/responses"
            "?api-version=v1"
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


def test_missing_base_url_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _build_provider(client, endpoint_url=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIConfigurationError"
    assert "base URL is required" in (result.error_message or "")
    assert "AZURE_OPENAI_BASE_URL" in (result.error_message or "")
    assert client.requests == []


def test_env_base_url_overrides_catalog_endpoint(tmp_path, monkeypatch):
    # Pi precedence: AZURE_OPENAI_BASE_URL (env) wins over model.baseUrl
    # (the catalog/endpoint_url field).
    monkeypatch.setenv(
        "AZURE_OPENAI_BASE_URL", "https://env-resource.openai.azure.com"
    )
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = _build_provider(
        client, endpoint_url="https://catalog-resource.openai.azure.com"
    )

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["url"] == (
        "https://env-resource.openai.azure.com/openai/v1/responses?api-version=v1"
    )


def test_resource_name_env_builds_default_base(tmp_path, monkeypatch):
    # buildDefaultBaseUrl: https://{name}.openai.azure.com/openai/v1, used when
    # no AZURE_OPENAI_BASE_URL is set; outranks the catalog endpoint_url.
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE_NAME", "myacct")
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = _build_provider(
        client, endpoint_url="https://catalog-resource.openai.azure.com"
    )

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["url"] == (
        "https://myacct.openai.azure.com/openai/v1/responses?api-version=v1"
    )


def test_resource_name_field_outranks_endpoint_but_not_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://wins.openai.azure.com")
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = AzureOpenAIResponsesProvider(
        model_id="gpt-4o",
        resource_name="fieldname",
        endpoint_url="https://catalog.openai.azure.com",
        api_key="k",
        http_client=client,
    )

    provider.complete(_provider_request(tmp_path))

    # env AZURE_OPENAI_BASE_URL still wins over the resource_name field.
    assert client.requests[0]["url"] == (
        "https://wins.openai.azure.com/openai/v1/responses?api-version=v1"
    )


def test_deployment_name_map_env_resolves_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
        "other=nope,gpt-4o-deployment=prod-deploy",
    )
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = _build_provider(client, model_id="gpt-4o-deployment")

    provider.complete(_provider_request(tmp_path))

    # Mapped deployment becomes the body ``model`` field.
    assert client.requests[0]["body"]["model"] == "prod-deploy"


def test_explicit_deployment_field_outranks_map(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "gpt-4o-deployment=from-map"
    )
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = _build_provider(
        client, model_id="gpt-4o-deployment", deployment="explicit"
    )

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["body"]["model"] == "explicit"


def test_deployment_name_map_unmapped_model_falls_back_to_model_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "someone-else=x")
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "ok"})
    )
    provider = _build_provider(client, model_id="gpt-4o-deployment")

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["body"]["model"] == "gpt-4o-deployment"


def test_parse_deployment_name_map_edge_cases():
    # JS split("=", 2) discards the remainder: a=b=c -> a:b.
    assert _parse_deployment_name_map("a=b=c") == {"a": "b"}
    # No '=' -> skipped; empty deployment / model -> skipped.
    assert _parse_deployment_name_map("abc") == {}
    assert _parse_deployment_name_map("a=") == {}
    assert _parse_deployment_name_map("=x") == {}
    # Whitespace around entries and sides is trimmed on store.
    assert _parse_deployment_name_map(" m = d ") == {"m": "d"}
    # Empty entries between commas are skipped.
    assert _parse_deployment_name_map("a=b,,c=d") == {"a": "b", "c": "d"}
    # Later duplicates win (Map.set semantics).
    assert _parse_deployment_name_map("a=b,a=c") == {"a": "c"}
    # Empty / None env -> empty map.
    assert _parse_deployment_name_map("") == {}
    assert _parse_deployment_name_map(None) == {}


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


def test_url_uses_openai_v1_surface_and_api_version(tmp_path):
    # Pi parity: the AzureOpenAI SDK normalizes an Azure host to a /openai/v1
    # base and posts to <base>/responses?api-version=v1; the deployment is the
    # body ``model`` field, not a URL path segment.
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
        api_version="v1",
    )

    provider.complete(_provider_request(tmp_path))

    posted_url = client.requests[0]["url"]
    assert posted_url == (
        "https://my-resource.openai.azure.com/openai/v1/responses?api-version=v1"
    )
    assert client.requests[0]["body"]["model"] == "gpt-4o-deployment"


def test_url_uses_explicit_deployment_as_body_model(tmp_path):
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

    posted = client.requests[0]
    assert posted["url"] == (
        "https://my-resource.openai.azure.com/openai/v1/responses?api-version=v1"
    )
    # The deployment moves to the body ``model`` field (Pi buildParams).
    assert posted["body"]["model"] == "prod-gpt4"


def test_non_azure_host_base_url_is_left_unnormalized(tmp_path):
    # A custom gateway base URL is respected verbatim; only /responses and the
    # api-version query are appended (mirrors Pi normalizeAzureBaseUrl).
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "ok"},
        )
    )
    provider = _build_provider(
        client,
        endpoint_url="https://gateway.example.com/azure-proxy/",
        api_version="v1",
    )

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["url"] == (
        "https://gateway.example.com/azure-proxy/responses?api-version=v1"
    )


def test_azure_host_with_existing_path_is_left_unnormalized(tmp_path):
    # An Azure host that already carries a non-trivial path is not rewritten.
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "ok"},
        )
    )
    provider = _build_provider(
        client,
        endpoint_url="https://my-resource.openai.azure.com/custom/base",
        api_version="v1",
    )

    provider.complete(_provider_request(tmp_path))

    assert client.requests[0]["url"] == (
        "https://my-resource.openai.azure.com/custom/base/responses?api-version=v1"
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
