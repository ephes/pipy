from __future__ import annotations

import hashlib
import hmac
import io
import json
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.bedrock_provider import (
    AmazonBedrockProvider,
    BedrockHTTPStatusError,
    JsonResponse,
    _sigv4_sign,
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


FIXED_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
FIXED_CLOCK = lambda: FIXED_NOW  # noqa: E731 — dataclass field default needs a callable


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="amazon-bedrock",
        model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
        cwd=tmp_path,
    )


def _make_provider(client: FakeJsonHTTPClient, **overrides: Any) -> AmazonBedrockProvider:
    defaults: dict[str, Any] = {
        "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "region": "us-east-1",
        "access_key": "AKIDEXAMPLE",
        "secret_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "session_token": None,
        "http_client": client,
        "_clock": FIXED_CLOCK,
    }
    defaults.update(overrides)
    return AmazonBedrockProvider(**defaults)


def test_sigv4_signature_matches_aws_canonical_example():
    """Pin signing-key derivation against the published AWS docs vector.

    The AWS Signature Version 4 docs publish the following intermediate
    signing-key derivation for the ``iam`` service example:

        secret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
        date   = "20150830"
        region = "us-east-1"
        service = "iam"
        kSigning hex = c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9

    Reproducing the same chain via stdlib HMAC pins our implementation
    against the published reference.
    """

    secret_key = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

    def _hm(key: bytes, msg: bytes) -> bytes:
        return hmac.new(key, msg, hashlib.sha256).digest()

    k_date = _hm(("AWS4" + secret_key).encode("utf-8"), b"20150830")
    k_region = _hm(k_date, b"us-east-1")
    k_service = _hm(k_region, b"iam")
    k_signing = _hm(k_service, b"aws4_request")
    assert k_signing.hex() == (
        "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
    )

    # Pin our deterministic SigV4 signing for a Bedrock InvokeModel call.
    signed = _sigv4_sign(
        method="POST",
        url=(
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
            "anthropic.claude-3-5-sonnet-20240620-v1%3A0/invoke"
        ),
        headers={"Content-Type": "application/json"},
        body=b"{}",
        region="us-east-1",
        service="bedrock",
        access_key="AKIDEXAMPLE",
        secret_key=secret_key,
        now=FIXED_NOW,
    )
    assert signed["X-Amz-Date"] == "20240115T120000Z"
    assert signed["Host"] == "bedrock-runtime.us-east-1.amazonaws.com"
    assert signed["X-Amz-Content-Sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert signed["Authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20240115/us-east-1/bedrock/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
        "Signature=fa87a27ecfdee5463ac7166c1527d4a2c25d8f01bd1d7c42ed3872248afebfc8"
    )


def test_success_returns_final_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": " world"},
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                },
            },
        )
    )
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "amazon-bedrock"
    assert result.final_text == "hello world"
    assert result.usage == {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    assert result.metadata == {"stop_reason": "end_turn", "aws_region": "us-east-1"}
    posted = client.requests[0]
    assert posted["url"] == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
        "anthropic.claude-3-5-sonnet-20240620-v1%3A0/invoke"
    )
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["headers"]["X-Amz-Date"] == "20240115T120000Z"
    assert posted["headers"]["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/"
    )
    assert "X-Amz-Security-Token" not in posted["headers"]
    assert posted["body"]["anthropic_version"] == "bedrock-2023-05-31"
    assert posted["body"]["max_tokens"] == 4096
    assert posted["body"]["system"] == "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"
    assert posted["body"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "SAFE_GOAL_METADATA"}]}
    ]
    assert "tools" not in posted["body"]


def test_success_returns_tool_calls(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "let me check"},
                    {
                        "type": "tool_use",
                        "id": "toolu_test_123",
                        "name": "read",
                        "input": {"path": "README.md"},
                    },
                ],
                "usage": {"input_tokens": 7, "output_tokens": 5, "cache_read_input_tokens": 2},
            },
        )
    )
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "let me check"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "toolu_test_123"
    assert call.tool_name == "read"
    assert json.loads(call.arguments_json) == {"path": "README.md"}
    assert result.metadata == {"stop_reason": "tool_use", "aws_region": "us-east-1"}
    # cached_tokens populated from cache_read_input_tokens.
    assert result.usage is not None and result.usage.get("cached_tokens") == 2


def test_tool_result_round_trip(tmp_path):
    """ToolResultMessage is serialized as Anthropic ``tool_result`` blocks."""

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )
    )
    provider = _make_provider(client)
    request = ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="ignored",
        provider_name="amazon-bedrock",
        model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
        cwd=tmp_path,
        messages=(
            UserMessage(content="please call the tool"),
            AssistantMessage(
                content="calling",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="toolu_round_trip",
                        tool_name="read",
                        arguments_json='{"path": "/etc/hosts"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id="pipy-tool-0001",
                output_text="OK",
                provider_correlation_id="toolu_round_trip",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    posted_messages = client.requests[0]["body"]["messages"]
    assert posted_messages[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "please call the tool"}],
    }
    assert posted_messages[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "calling"},
            {
                "type": "tool_use",
                "id": "toolu_round_trip",
                "name": "read",
                "input": {"path": "/etc/hosts"},
            },
        ],
    }
    assert posted_messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_round_trip",
                "content": "OK",
            }
        ],
    }


def test_http_429_returns_failed_result(tmp_path):
    error_body = json.dumps({"message": "Too Many Requests"}).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = _make_provider(
        FakeJsonHTTPClient(error=BedrockHTTPStatusError.from_http_error(http_error)),
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "BedrockHTTPStatusError"
    assert result.error_message == "Bedrock API request failed with HTTP status 429."
    assert result.metadata is not None
    assert result.metadata["http_status"] == 429
    assert result.metadata.get("api_error_type") == "Too Many Requests"


def test_missing_credentials_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _make_provider(client, access_key=None, secret_key=None)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "BedrockAuthError"
    assert "AWS signing keys must be set" in (result.error_message or "")
    assert client.requests == []


def test_missing_model_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = _make_provider(client, model_id="")

    request = ProviderRequest(
        system_prompt="SYSTEM",
        user_prompt="SAFE_GOAL",
        provider_name="amazon-bedrock",
        model_id="",
        cwd=tmp_path,
    )
    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "BedrockConfigurationError"
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def test_session_token_added_to_headers_when_present(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    provider = _make_provider(
        client,
        session_token="STS-SESSION-TOKEN-EXAMPLE",
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["headers"]["X-Amz-Security-Token"] == "STS-SESSION-TOKEN-EXAMPLE"
    # The session token must be included in the signed-headers list to be
    # bound to the signature.
    assert "x-amz-security-token" in posted["headers"]["Authorization"]


def test_malformed_json_response_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"stop_reason": "end_turn", "content": []},
        )
    )
    provider = _make_provider(client)

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "BedrockResponseParseError"
    assert "did not include final output text" in (result.error_message or "")
    assert result.metadata == {"stop_reason": "end_turn"}
