"""Golden wire-byte fixtures for the Amazon Bedrock InvokeModel adapter.

These characterization fixtures pin the exact request the
``native.providers.bedrock`` adapter serializes onto ``native.http`` (the
region-templated InvokeModel URL, the ``anthropic_version`` envelope, the
canonical message serialization, the flat ``tools`` shape, and the
``budget_tokens`` thinking shape) alongside the SigV4-signed header set for a
fixed clock — the *signing-order* fixture, which pins the canonical
signed-header sequence and the deterministic ``Authorization``/``X-Amz-*``
headers — plus the parsed success usage/output and the sanitized top-level
``message``/``__type`` error metadata. They exist so the Phase 5.2
provider-family move cannot silently alter the bytes on the wire or the SigV4
signing order: the request body is captured straight off a recording
``RecordingJsonHTTPClient`` and the signed headers straight off ``_sigv4_sign``,
both compared to checked-in goldens.
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime
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
from pipy_harness.native.providers.bedrock import (
    AmazonBedrockProvider,
    BedrockHTTPStatusError,
    JsonResponse,
    _sigv4_sign,
)
from pipy_harness.native.tools.base import ToolDefinition

_FIXTURES = Path(__file__).parent / "fixtures" / "bedrock"

FIXED_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"


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
        provider_name="amazon-bedrock",
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


def _make_provider(client: RecordingJsonHTTPClient) -> AmazonBedrockProvider:
    return AmazonBedrockProvider(
        model_id=MODEL_ID,
        region="us-east-1",
        access_key="AKIDEXAMPLE",
        secret_key=SECRET_KEY,
        session_token=None,
        http_client=client,
        reasoning_effort="high",
        _clock=lambda: FIXED_NOW,
    )


def test_bedrock_invoke_model_request_matches_golden_wire_bytes(
    tmp_path: Path,
) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    # Region-templated InvokeModel URL with the percent-encoded model id.
    assert posted["url"] == _load("signed_request.json")["url"]
    assert posted["url"] == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
        "anthropic.claude-3-5-sonnet-20240620-v1%3A0/invoke"
    )
    recorded_body = posted["body"]
    golden_body = _load("request_invoke_model.json")
    # Structural parity: anthropic_version envelope, system, messages, tools,
    # budget-thinking shape (claude-3-5-sonnet is non-adaptive).
    assert recorded_body == golden_body
    # Exact wire payload: native.http serializes the body with json.dumps.
    assert json.dumps(recorded_body) == json.dumps(golden_body)
    assert recorded_body["anthropic_version"] == "bedrock-2023-05-31"
    assert recorded_body["thinking"] == {
        "type": "enabled",
        "budget_tokens": 16384,
        "display": "summarized",
    }


def test_bedrock_sigv4_signed_headers_match_signing_order_golden() -> None:
    """Pin the SigV4 signed-header sequence for a fixed clock.

    The signed body is the exact InvokeModel request bytes from the request
    fixture, so the canonical signed-header list, the deterministic
    ``Authorization`` signature, and the ``X-Amz-*`` headers stay byte-identical
    across the provider-family move.
    """

    golden = _load("signed_request.json")
    signed = _sigv4_sign(
        method="POST",
        url=golden["url"],
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        body=json.dumps(_load("request_invoke_model.json")).encode("utf-8"),
        region="us-east-1",
        service="bedrock",
        access_key="AKIDEXAMPLE",
        secret_key=SECRET_KEY,
        session_token=None,
        now=FIXED_NOW,
    )
    assert signed == golden["headers"]
    # SignedHeaders list is sorted and stable; it is the signing-order contract.
    signed_headers = (
        signed["Authorization"].split("SignedHeaders=", 1)[1].split(",", 1)[0]
    )
    assert signed_headers == golden["signed_headers"]
    assert signed_headers == "accept;content-type;host;x-amz-content-sha256;x-amz-date"


def test_bedrock_success_response_parses_from_golden(tmp_path: Path) -> None:
    client = RecordingJsonHTTPClient(
        JsonResponse(status_code=200, body=_load("response_success.json"))
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    parsed = _load("parsed_result.json")
    assert result.final_text == parsed["final_text"]
    # extract_anthropic_usage synthesizes total from input+output+cache reads+writes.
    assert result.usage == parsed["usage"]
    assert result.usage == {
        "input_tokens": 42,
        "output_tokens": 8,
        "total_tokens": 67,
        "cached_tokens": 12,
        "cache_write_tokens": 5,
    }
    assert result.metadata == parsed["metadata"]
    assert result.metadata == {"stop_reason": "end_turn", "aws_region": "us-east-1"}


def test_bedrock_error_metadata_matches_golden(tmp_path: Path) -> None:
    error_body = json.dumps(
        {"message": "Rate exceeded", "__type": "ThrottlingException"}
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    client = RecordingJsonHTTPClient(
        error=BedrockHTTPStatusError.from_http_error(http_error)
    )
    provider = _make_provider(client)

    result = provider.complete(_tool_history_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "BedrockHTTPStatusError"
    # Top-level ``message`` wins over ``__type`` (setdefault precedence).
    assert result.metadata == _load("error_metadata.json")
    assert result.metadata == {"http_status": 429, "api_error_type": "Rate exceeded"}


def test_bedrock_error_metadata_falls_back_to_type_and_redacts_secret() -> None:
    # When ``message`` is absent, ``__type`` supplies the api_error_type.
    type_only = BedrockHTTPStatusError.from_http_error(
        urllib.error.HTTPError(
            url="https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
            code=400,
            msg="Bad Request",
            hdrs=Message(),
            fp=io.BytesIO(
                json.dumps({"__type": "ValidationException"}).encode("utf-8")
            ),
        )
    )
    assert type_only.metadata == {
        "http_status": 400,
        "api_error_type": "ValidationException",
    }

    # A secret-looking top-level ``message`` is sanitized, never leaked.
    secret = BedrockHTTPStatusError.from_http_error(
        urllib.error.HTTPError(
            url="https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
            code=403,
            msg="Forbidden",
            hdrs=Message(),
            fp=io.BytesIO(
                json.dumps({"message": "SECRET_PROMPT_SHOULD_NOT_LEAK"}).encode("utf-8")
            ),
        )
    )
    assert "SECRET_PROMPT_SHOULD_NOT_LEAK" not in json.dumps(
        secret.metadata, sort_keys=True
    )
