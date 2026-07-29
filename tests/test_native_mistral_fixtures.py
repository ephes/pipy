"""Golden wire-byte fixtures for the Mistral Chat Completions adapter.

These characterization fixtures pin the exact request the
``native.providers.mistral`` adapter serializes onto ``native.http`` (the Chat
Completions ``messages`` serialization, the ``tools`` shape, and the mapped
``reasoning_effort`` passthrough), the parsed success usage/output, and the
sanitized HTTP-status error metadata. They exist so the Phase 5.2 provider-family
move cannot silently alter the bytes on the wire: the request body is captured
straight off a recording ``RecordingJsonHTTPClient`` and compared to a
checked-in golden, both structurally and as the exact serialized wire payload.
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
from pipy_harness.native.providers.mistral import (
    JsonResponse,
    MistralHTTPStatusError,
    MistralProvider,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "mistral"


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
        provider_name="mistral",
        model_id="mistral-large-latest",
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


def test_mistral_request_matches_golden_wire_bytes(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-test",
        http_client=client,
        reasoning_effort="high",
    )

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    recorded_body = client.requests[0]["body"]
    golden_body = _load("request_tools_reasoning.json")
    # Structural parity: chat messages serialization, tools, and reasoning_effort.
    assert recorded_body == golden_body
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded_body) == json.dumps(golden_body)


def test_mistral_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key="sk-test",
        http_client=client,
        reasoning_effort="high",
    )

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.final_text == "config.toml sets port 8080."
    assert result.usage == {
        "input_tokens": 42,
        "output_tokens": 8,
        "total_tokens": 50,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }


def test_mistral_error_metadata_matches_golden(tmp_path: Path) -> None:
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
        url="https://api.mistral.ai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=MistralHTTPStatusError.from_http_error(http_error)
    )
    provider = MistralProvider(
        model_id="mistral-large-latest", api_key="sk-test", http_client=client
    )

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralHTTPStatusError"
    assert result.metadata == _load("error_metadata.json")
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in json.dumps(
        result.metadata, sort_keys=True
    )
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in (result.error_message or "")
