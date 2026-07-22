"""Golden wire-byte fixtures for the Anthropic Messages adapter.

These characterization fixtures pin the exact request the
``native.providers.anthropic_messages`` adapter serializes onto
``native.http`` (system prompt, canonical-message serialization with
``tool_use``/``tool_result`` blocks, the flat ``tools`` shape, and the two
thinking shapes — ``budget_tokens`` for non-adaptive models versus
``type: adaptive`` + ``output_config.effort`` for the adaptive Claude
families), the parsed success usage/output, and the sanitized HTTP-status
error metadata. They exist so the Phase 5.2 provider-family move cannot
silently alter the bytes on the wire: the request body is captured straight off
a recording ``RecordingJsonHTTPClient`` and compared to a checked-in golden,
both structurally and as the exact serialized wire payload.
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
from pipy_harness.native.providers.anthropic_messages import (
    AnthropicHTTPStatusError,
    AnthropicProvider,
    JsonResponse,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "anthropic_messages"


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


def _tool_history_request(tmp_path: Path, model_id: str) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="You are the pipy native assistant.",
        user_prompt="Read config.toml and summarize it.",
        provider_name="anthropic",
        model_id=model_id,
        cwd=tmp_path,
        messages=(
            AgentUserMessage(
                content=ProductContent("Read config.toml and summarize it.")
            ),
            AgentAssistantMessage(
                content=ProductContent("I'll read the file first."),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="toolu_read_1",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "config.toml"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-read-1",
                tool_name="read",
                content=ProductContent("port = 8080"),
                provider_correlation_id="toolu_read_1",
                added_tool_names=(),
            ),
        ),
        available_tools=(_read_tool(),),
    )


def test_anthropic_budget_thinking_request_matches_golden_wire_bytes(
    tmp_path: Path,
) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = AnthropicProvider(
        model_id="claude-sonnet-4-5",
        api_key="sk-ant-test",
        http_client=client,
        reasoning_effort="high",
    )

    result = provider.complete(_tool_history_request(tmp_path, "claude-sonnet-4-5"))

    assert result.status == HarnessStatus.SUCCEEDED
    recorded_body = client.requests[0]["body"]
    golden_body = _load("request_tools_budget_thinking.json")
    # Structural parity: system, message envelopes, tools, budget thinking.
    assert recorded_body == golden_body
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded_body) == json.dumps(golden_body)


def test_anthropic_adaptive_thinking_request_matches_golden_wire_bytes(
    tmp_path: Path,
) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = AnthropicProvider(
        model_id="claude-opus-4-8",
        api_key="sk-ant-test",
        http_client=client,
        reasoning_effort="high",
    )

    result = provider.complete(_tool_history_request(tmp_path, "claude-opus-4-8"))

    assert result.status == HarnessStatus.SUCCEEDED
    recorded_body = client.requests[0]["body"]
    golden_body = _load("request_tools_adaptive_thinking.json")
    # Adaptive Claude models take type:adaptive + output_config.effort.
    assert recorded_body == golden_body
    assert json.dumps(recorded_body) == json.dumps(golden_body)


def test_anthropic_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = AnthropicProvider(
        model_id="claude-sonnet-4-5",
        api_key="sk-ant-test",
        http_client=client,
    )

    result = provider.complete(_tool_history_request(tmp_path, "claude-sonnet-4-5"))

    assert result.final_text == "config.toml sets port 8080."
    # extract_anthropic_usage synthesizes total from input+output+cache reads+writes.
    assert result.usage == {
        "input_tokens": 42,
        "output_tokens": 8,
        "total_tokens": 67,
        "cached_tokens": 12,
        "cache_write_tokens": 5,
    }
    assert result.metadata == {"stop_reason": "end_turn"}


def test_anthropic_error_metadata_matches_golden(tmp_path: Path) -> None:
    error_body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "SECRET_PROMPT_SHOULD_NOT_LEAK",
            },
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=AnthropicHTTPStatusError.from_http_error(http_error)
    )
    provider = AnthropicProvider(
        model_id="claude-sonnet-4-5", api_key="sk-ant-test", http_client=client
    )

    result = provider.complete(_tool_history_request(tmp_path, "claude-sonnet-4-5"))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "AnthropicHTTPStatusError"
    assert result.metadata == _load("error_metadata.json")
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in json.dumps(
        result.metadata, sort_keys=True
    )
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in (result.error_message or "")
