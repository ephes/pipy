"""Golden wire-byte fixtures for the Google Vertex generateContent adapter.

These characterization fixtures pin the exact request the
``native.providers.google_vertex`` adapter serializes onto ``native.http`` in
both auth modes. The request *body* is identical across modes (the Gemini
``contents`` envelope with ``functionCall``/``functionResponse`` parts, the
``systemInstruction`` block, the flat ``tools.functionDeclarations`` shape, and
the per-model ``generationConfig.thinkingConfig`` budget shape); only the
endpoint and the auth header differ:

- **Vertex Express (API key)**: the global ``aiplatform.googleapis.com`` host
  with no project/location path segment and the ``x-goog-api-key`` header.
- **ADC (OAuth bearer)**: the regional endpoint built from ``project_id`` +
  ``location`` and the ``Authorization: Bearer`` header.

They exist so the Phase 5.2 provider-family move cannot silently alter the bytes
on the wire: the request body is captured straight off a recording
``RecordingJsonHTTPClient`` and compared to checked-in goldens both structurally
and as the ``json.dumps`` payload, alongside the parsed success usage/output
(with the per-mode ``vertex_auth_mode``/``google_cloud_location`` metadata) and
the sanitized nested ``error`` metadata.
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.providers.google_vertex import (
    GOOGLE_VERTEX_USAGE_FIELDS,
    GoogleVertexHTTPStatusError,
    GoogleVertexProvider,
    JsonResponse,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "google_vertex"

MODEL_ID = "gemini-2.5-pro"
PROJECT_ID = "my-gcp-project"
LOCATION = "us-central1"
ACCESS_TOKEN = "ya29.EXAMPLE_ACCESS_TOKEN"
EXPRESS_API_KEY = "AIzaSyEXAMPLE-VERTEX-EXPRESS-KEY"


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class RecordingJsonHTTPClient:
    def __init__(
        self, response: JsonResponse | None = None, error: Exception | None = None
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
        provider_name="google-vertex",
        model_id=MODEL_ID,
        cwd=tmp_path,
        messages=(
            AgentUserMessage(
                content=ProductContent("Read config.toml and summarize it.")
            ),
            AgentAssistantMessage(
                content=ProductContent("I'll read the file first."),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="google-vertex-tool-0",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "config.toml"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-read-1",
                tool_name="read",
                content=ProductContent("port = 8080"),
                provider_correlation_id="google-vertex-tool-0",
                added_tool_names=(),
            ),
        ),
        available_tools=(_read_tool(),),
    )


def _adc_provider(client: RecordingJsonHTTPClient) -> GoogleVertexProvider:
    return GoogleVertexProvider(
        model_id=MODEL_ID,
        project_id=PROJECT_ID,
        location=LOCATION,
        access_token=ACCESS_TOKEN,
        # Force ADC regardless of a developer's ambient GOOGLE_CLOUD_API_KEY.
        api_key=None,
        http_client=client,
        reasoning_effort="high",
    )


def _express_provider(client: RecordingJsonHTTPClient) -> GoogleVertexProvider:
    return GoogleVertexProvider(
        model_id=MODEL_ID,
        project_id=PROJECT_ID,
        location=LOCATION,
        access_token=None,
        api_key=EXPRESS_API_KEY,
        http_client=client,
        reasoning_effort="high",
    )


def test_vertex_adc_request_matches_golden_wire_bytes(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _adc_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    # ADC: regional endpoint built from project_id + location, Bearer auth.
    assert posted["url"] == _load("request_url_adc.json")["url"]
    assert posted["url"] == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/"
        "my-gcp-project/locations/us-central1/publishers/google/models/"
        "gemini-2.5-pro:generateContent"
    )
    assert posted["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert "x-goog-api-key" not in posted["headers"]
    recorded_body = posted["body"]
    golden_body = _load("request_generate_content.json")
    # Structural parity: contents envelope (functionCall/functionResponse),
    # systemInstruction, tools, thinkingConfig budget shape.
    assert recorded_body == golden_body
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded_body) == json.dumps(golden_body)
    # gemini-2.5-pro at high effort takes a token budget, not a thinkingLevel.
    assert recorded_body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 32768,
    }


def test_vertex_express_request_matches_golden_wire_bytes(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _express_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    # Express: global aiplatform host, no project/location path, x-goog-api-key.
    assert posted["url"] == _load("request_url_express.json")["url"]
    assert posted["url"] == (
        "https://aiplatform.googleapis.com/v1/publishers/google/models/"
        "gemini-2.5-pro:generateContent"
    )
    assert posted["headers"]["x-goog-api-key"] == EXPRESS_API_KEY
    assert "Authorization" not in posted["headers"]
    # The Express body is byte-identical to the ADC body: only the endpoint and
    # the auth header differ between the two modes.
    recorded_body = posted["body"]
    golden_body = _load("request_generate_content.json")
    assert recorded_body == golden_body
    assert json.dumps(recorded_body) == json.dumps(golden_body)


def test_vertex_adc_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _adc_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    parsed = _load("parsed_result_adc.json")
    assert result.final_text == parsed["final_text"]
    assert result.final_text == "config.toml sets port 8080."
    # GOOGLE_VERTEX_USAGE_FIELDS remap the promptTokenCount/candidatesTokenCount/
    # totalTokenCount usage metadata onto the normalized token keys.
    assert result.usage == parsed["usage"]
    assert result.usage == {
        "input_tokens": 31,
        "output_tokens": 9,
        "total_tokens": 40,
    }
    # ADC metadata carries vertex_auth_mode="adc" plus the region.
    assert result.metadata == parsed["metadata"]
    assert result.metadata == {
        "provider_response_store_requested": False,
        "finish_reason": "STOP",
        "vertex_auth_mode": "adc",
        "google_cloud_location": "us-central1",
    }
    assert GOOGLE_VERTEX_USAGE_FIELDS == (
        ("promptTokenCount", "input_tokens"),
        ("candidatesTokenCount", "output_tokens"),
        ("totalTokenCount", "total_tokens"),
    )


def test_vertex_express_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _express_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    parsed = _load("parsed_result_express.json")
    assert result.usage == parsed["usage"]
    # Express metadata carries vertex_auth_mode="api-key" and omits the region.
    assert result.metadata == parsed["metadata"]
    assert result.metadata == {
        "provider_response_store_requested": False,
        "finish_reason": "STOP",
        "vertex_auth_mode": "api-key",
    }
    assert "google_cloud_location" not in result.metadata


def test_vertex_error_metadata_matches_golden(tmp_path: Path) -> None:
    error_body = json.dumps(
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "Quota exceeded",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://us-central1-aiplatform.googleapis.com/x",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=GoogleVertexHTTPStatusError.from_http_error(http_error)
    )
    provider = _adc_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleVertexHTTPStatusError"
    # Nested ``error`` envelope: status is lifted verbatim, code sanitized to str.
    assert result.metadata == _load("error_metadata.json")
    assert result.metadata == {
        "http_status": 429,
        "api_error_status": "RESOURCE_EXHAUSTED",
        "api_error_code": "429",
    }


def test_vertex_error_metadata_redacts_secret_looking_status() -> None:
    secret = GoogleVertexHTTPStatusError.from_http_error(
        urllib.error.HTTPError(
            url="https://us-central1-aiplatform.googleapis.com/x",
            code=403,
            msg="Forbidden",
            hdrs=Message(),
            fp=io.BytesIO(
                json.dumps(
                    {"error": {"status": "SECRET_PROMPT_SHOULD_NOT_LEAK"}}
                ).encode("utf-8")
            ),
        )
    )
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in json.dumps(
        secret.metadata, sort_keys=True
    )
