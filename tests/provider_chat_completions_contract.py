"""Shared scenarios for equivalent Chat Completions provider adapters."""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, cast

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderPort, ProviderRequest
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.http import JsonHTTPClient, JsonResponse


class FakeJsonHTTPClient:
    """Capture one provider's JSON requests and return a configured result."""

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
        cancel_token: CancelToken | None = None,
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


ProviderFactory = Callable[[JsonHTTPClient, str], ProviderPort]
RequestFactory = Callable[[Path], ProviderRequest]
StatusErrorFactory = Callable[[urllib.error.HTTPError], Exception]
WireAssertion = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ChatCompletionsContract:
    """Provider-specific ports and expectations used by the shared scenarios."""

    provider_id: str
    model_id: str
    http_error_url: str
    make_provider: ProviderFactory
    make_request: RequestFactory
    make_status_error: StatusErrorFactory
    http_error_type: str
    http_error_message: str
    configuration_error_type: str
    parse_error_type: str
    parse_error_message: str
    assert_success_wire: WireAssertion
    assert_tool_result_wire: WireAssertion


@dataclass(frozen=True, slots=True)
class ChatCompletionsScenario:
    """One independently collected provider-contract scenario."""

    id: str
    run: Callable[[ChatCompletionsContract, Path], None]


def _success_returns_final_text(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "id": "gen-provider-id-should-not-store",
                "object": "chat.completion",
                "model": contract.model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"hello from {contract.provider_id}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )
    )
    provider = contract.make_provider(client, contract.model_id)

    result = provider.complete(contract.make_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == contract.provider_id
    assert result.model_id == contract.model_id
    assert result.final_text == f"hello from {contract.provider_id}"
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }
    contract.assert_success_wire(client.requests[0])


def _success_returns_tool_calls(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    provider = contract.make_provider(client, contract.model_id)

    result = provider.complete(contract.make_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert not result.final_text
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "call_abc123"
    assert call.tool_name == "read_file"
    assert call.arguments_json == '{"path":"README.md"}'


def _tool_result_round_trip(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = contract.make_provider(client, contract.model_id)
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="ignored when messages are set",
        provider_name=contract.provider_id,
        model_id=contract.model_id,
        cwd=tmp_path,
        messages=(
            AgentUserMessage(content=ProductContent("please read README")),
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_abc123",
                        tool_name="read_file",
                        arguments_json=ProductContent('{"path":"README.md"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-0001",
                tool_name="read_file",
                content=ProductContent("file contents"),
                provider_correlation_id="call_abc123",
            ),
        ),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "done"
    contract.assert_tool_result_wire(client.requests[0])


def _http_429_returns_failed_result(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    error_body = json.dumps(
        {
            "error": {
                "code": 429,
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url=contract.http_error_url,
        code=429,
        msg="Too Many Requests",
        hdrs=cast("Message[str, str]", {}),
        fp=io.BytesIO(error_body),
    )
    client = FakeJsonHTTPClient(error=contract.make_status_error(http_error))
    provider = contract.make_provider(client, contract.model_id)

    result = provider.complete(contract.make_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == contract.http_error_type
    assert result.error_message == contract.http_error_message
    assert result.metadata == {
        "api_error_code": "429",
        "http_status": 429,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def _missing_model_returns_failed_result(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient()
    provider = contract.make_provider(client, "")
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hi",
        provider_name=contract.provider_id,
        model_id="",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == contract.configuration_error_type
    assert "--native-model is required" in (result.error_message or "")
    assert client.requests == []


def _malformed_json_response_returns_failed_result(
    contract: ChatCompletionsContract,
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [],
            },
        )
    )
    provider = contract.make_provider(client, contract.model_id)

    result = provider.complete(contract.make_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == contract.parse_error_type
    assert result.error_message == contract.parse_error_message
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
    }


CHAT_COMPLETIONS_SCENARIOS = (
    ChatCompletionsScenario("success-returns-final-text", _success_returns_final_text),
    ChatCompletionsScenario("success-returns-tool-calls", _success_returns_tool_calls),
    ChatCompletionsScenario("tool-result-round-trip", _tool_result_round_trip),
    ChatCompletionsScenario(
        "http-429-returns-failed-result", _http_429_returns_failed_result
    ),
    ChatCompletionsScenario(
        "missing-model-returns-failed-result", _missing_model_returns_failed_result
    ),
    ChatCompletionsScenario(
        "malformed-json-response-returns-failed-result",
        _malformed_json_response_returns_failed_result,
    ),
)


def scenario_ids(provider_id: str) -> tuple[str, ...]:
    """Return readable ids that identify both provider and scenario."""

    return tuple(
        f"{provider_id}-{scenario.id}" for scenario in CHAT_COMPLETIONS_SCENARIOS
    )
