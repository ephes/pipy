"""Golden wire-byte fixtures for the Azure OpenAI Responses adapter.

These characterization fixtures pin the exact request the
``native.providers.azure_openai_responses`` adapter serializes onto
``native.http`` and the wire-level bytes that are specific to Azure: the
``/openai/v1`` base-URL normalization, the ``AZURE_OPENAI_DEPLOYMENT_NAME_MAP``
model->deployment resolution, the deployment carried as the body ``model``
field, the ``api-key`` header authentication (not ``Authorization: Bearer``),
and the ``api-version`` query. They exist so the Phase 5.2 provider-family move
cannot silently alter the bytes on the wire: the URL, headers, and request body
are captured straight off a recording ``RecordingJsonHTTPClient`` and compared
to a checked-in golden, both structurally and as the exact serialized wire
payload. The parsed success response and the sanitized HTTP-status error
metadata are pinned the same way.
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.providers.azure_openai_responses import (
    AzureOpenAIHTTPStatusError,
    AzureOpenAIResponsesProvider,
    JsonResponse,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "azure_openai_responses"


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_RESOURCE_NAME",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
    ):
        monkeypatch.delenv(name, raising=False)


class RecordingJsonHTTPClient:
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
        self.requests.append({"url": url, "headers": dict(headers), "body": dict(body)})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _read_tool() -> ToolDefinition:
    return ToolDefinition(
        name="read",
        description="Read a file from the workspace.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


def _tool_history_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="You are the pipy native assistant.",
        user_prompt="Read config.toml and summarize it.",
        provider_name="azure-openai",
        model_id="gpt-4o-deployment",
        cwd=tmp_path,
        messages=(
            AgentUserMessage(
                content=ProductContent("Read config.toml and summarize it.")
            ),
            AgentAssistantMessage(
                content=ProductContent("I'll read the file first."),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_read_1",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "config.toml"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-read-1",
                tool_name="read",
                content=ProductContent("port = 8080"),
                provider_correlation_id="call_read_1",
                added_tool_names=(),
            ),
        ),
        available_tools=(_read_tool(),),
    )


def _build_provider(client: RecordingJsonHTTPClient) -> AzureOpenAIResponsesProvider:
    return AzureOpenAIResponsesProvider(
        model_id="gpt-4o-deployment",
        endpoint_url="https://my-resource.openai.azure.com",
        api_key="azure-key-test",
        api_version="v1",
        http_client=client,
        reasoning_effort="high",
    )


def test_azure_request_matches_golden_wire_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deployment-name map resolves ``gpt-4o-deployment`` -> ``prod-deploy``,
    # which becomes the body ``model`` field.
    monkeypatch.setenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
        "other=nope,gpt-4o-deployment=prod-deploy",
    )
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _build_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    recorded = client.requests[0]
    golden = _load("request.json")
    # Structural parity: /openai/v1 normalization + api-version query (url),
    # api-key auth (headers), deployment-as-body-model + tools + reasoning (body).
    assert recorded == golden
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded["body"]) == json.dumps(golden["body"])
    assert recorded["url"] == golden["url"]
    assert recorded["headers"] == golden["headers"]
    assert "Authorization" not in recorded["headers"]


def test_azure_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _build_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.final_text == "config.toml sets port 8080."
    assert result.usage == {
        "cached_tokens": 12,
        "input_tokens": 42,
        "output_tokens": 8,
        "reasoning_tokens": 4,
        "total_tokens": 50,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }


def test_azure_error_metadata_matches_golden(tmp_path: Path) -> None:
    error_body = json.dumps(
        {
            "error": {
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
                "message": "SECRET_PROMPT_SHOULD_NOT_LEAK",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://my-resource.openai.azure.com/openai/v1/responses?api-version=v1",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=AzureOpenAIHTTPStatusError.from_http_error(http_error)
    )
    provider = _build_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AzureOpenAIHTTPStatusError"
    assert result.metadata == _load("error_metadata.json")
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in json.dumps(
        result.metadata, sort_keys=True
    )
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in (result.error_message or "")
