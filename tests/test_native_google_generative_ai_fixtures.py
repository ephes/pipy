"""Golden wire-byte fixtures for the Google Gemini generateContent adapter.

These characterization fixtures pin the exact request the
``native.providers.google_generative_ai`` adapter serializes onto
``native.http`` (the ``generateContent`` endpoint with URL-embedded ``?key=``
auth, the ``contents`` envelope with ``functionCall``/``functionResponse`` and
``inlineData`` image parts, the ``systemInstruction`` block, the flat
``tools.functionDeclarations`` shape, and the per-model
``generationConfig.thinkingConfig`` budget shape) alongside the parsed
success usage/output and the sanitized nested ``error`` metadata. They exist so
the Phase 5.2 provider-family move cannot silently alter the bytes on the wire:
the request body is captured straight off a recording ``RecordingJsonHTTPClient``
and compared to checked-in goldens both structurally and as the ``json.dumps``
payload.
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
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.providers.google_generative_ai import (
    GOOGLE_USAGE_FIELDS,
    GoogleGenerativeAIProvider,
    GoogleHTTPStatusError,
    JsonResponse,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "google_generative_ai"

MODEL_ID = "gemini-2.5-pro"
API_KEY = "AIzaSyEXAMPLE-GOOGLE-KEY"


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
        provider_name="google",
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
                        provider_correlation_id="google-tool-0",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "config.toml"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-read-1",
                tool_name="read",
                content=ProductContent("port = 8080"),
                provider_correlation_id="google-tool-0",
                added_tool_names=(),
            ),
        ),
        available_tools=(_read_tool(),),
        attachments=(
            ProviderImageAttachment(
                media_type="image/png",
                data_base64="aGVsbG8=",
                byte_count=5,
                sha256="0" * 64,
                source_label="@image:diagram.png",
            ),
        ),
    )


def _make_provider(client: RecordingJsonHTTPClient) -> GoogleGenerativeAIProvider:
    return GoogleGenerativeAIProvider(
        model_id=MODEL_ID,
        api_key=API_KEY,
        http_client=client,
        reasoning_effort="high",
    )


def test_google_generate_content_request_matches_golden_wire_bytes(
    tmp_path: Path,
) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    # generateContent endpoint with URL-embedded ?key= auth (no auth header).
    assert posted["url"] == _load("request_url.json")["url"]
    assert posted["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-pro:generateContent?key=AIzaSyEXAMPLE-GOOGLE-KEY"
    )
    recorded_body = posted["body"]
    golden_body = _load("request_generate_content.json")
    # Structural parity: contents envelope (functionCall/functionResponse +
    # inlineData image), systemInstruction, tools, thinkingConfig budget shape.
    assert recorded_body == golden_body
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded_body) == json.dumps(golden_body)
    # gemini-2.5-pro at high effort takes a token budget, not a thinkingLevel.
    assert recorded_body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 32768,
    }
    # The current-turn image rides an inlineData part on the last user content.
    assert recorded_body["contents"][-1]["parts"][-1] == {
        "inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}
    }


def test_google_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    parsed = _load("parsed_result.json")
    assert result.final_text == parsed["final_text"]
    assert result.final_text == "config.toml sets port 8080."
    # GOOGLE_USAGE_FIELDS remap the promptTokenCount/candidatesTokenCount/
    # totalTokenCount usage metadata onto the normalized token keys.
    assert result.usage == parsed["usage"]
    assert result.usage == {
        "input_tokens": 31,
        "output_tokens": 9,
        "total_tokens": 40,
    }
    assert result.metadata == parsed["metadata"]
    assert result.metadata == {
        "provider_response_store_requested": False,
        "finish_reason": "STOP",
    }
    assert GOOGLE_USAGE_FIELDS == (
        ("promptTokenCount", "input_tokens"),
        ("candidatesTokenCount", "output_tokens"),
        ("totalTokenCount", "total_tokens"),
    )


def test_google_error_metadata_matches_golden(tmp_path: Path) -> None:
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
        url="https://generativelanguage.googleapis.com/x",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=GoogleHTTPStatusError.from_http_error(http_error)
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "GoogleHTTPStatusError"
    # Nested ``error`` envelope: status is lifted verbatim, code sanitized to str.
    assert result.metadata == _load("error_metadata.json")
    assert result.metadata == {
        "http_status": 429,
        "api_error_status": "RESOURCE_EXHAUSTED",
        "api_error_code": "429",
    }


def test_google_error_metadata_redacts_secret_looking_status() -> None:
    secret = GoogleHTTPStatusError.from_http_error(
        urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com/x",
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
