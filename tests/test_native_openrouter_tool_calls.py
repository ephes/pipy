"""Slice 12 follow-up tests: OpenRouter tool-call serialization and parsing.

These tests pin OpenRouter as the first real provider with
`supports_tool_calls=True`. The provider serializes the loop message
envelope and `available_tools` into the OpenAI Chat Completions tool
format, and parses returned `tool_calls` into `ProviderToolCall`
instances that the loop can dispatch into the production tool registry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.openrouter_provider import (
    JsonResponse,
    OpenRouterChatCompletionsProvider,
)
from pipy_harness.native.tools import (
    AssistantMessage,
    ReadTool,
    ToolResultMessage,
    UserMessage,
    make_tool_request_id,
)


class FakeJsonHTTPClient:
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
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
        self.requests.append({"url": url, "body": dict(body)})
        return self.response


def _tool_call_response() -> JsonResponse:
    return JsonResponse(
        status_code=200,
        body={
            "object": "chat.completion",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": json.dumps({"path": "README.md"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    )


def test_openrouter_supports_tool_calls_is_true():
    provider = OpenRouterChatCompletionsProvider(model_id="openai/gpt-test")

    assert provider.supports_tool_calls is True


def test_openrouter_serializes_tools_in_chat_request(tmp_path: Path):
    client = FakeJsonHTTPClient(_tool_call_response())
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )
    read_tool = ReadTool()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="please read",
        provider_name="openrouter",
        model_id="openai/gpt-test",
        cwd=tmp_path,
        messages=(UserMessage(content="please read"),),
        available_tools=(read_tool.definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    body = client.requests[0]["body"]
    assert body["model"] == "openai/gpt-test"
    assert isinstance(body["tools"], list)
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "read"
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "please read"}


def test_openrouter_parses_tool_calls_into_provider_tool_call(tmp_path: Path):
    client = FakeJsonHTTPClient(_tool_call_response())
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openrouter",
            model_id="openai/gpt-test",
            cwd=tmp_path,
            messages=(UserMessage(content="go"),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, ProviderToolCall)
    assert call.provider_correlation_id == "call_abc"
    assert call.tool_name == "read"
    assert call.arguments_json == '{"path": "README.md"}'


def test_openrouter_serializes_tool_result_envelope(tmp_path: Path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"content": "done", "tool_calls": []},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )
    request_id = make_tool_request_id()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="follow up",
        provider_name="openrouter",
        model_id="openai/gpt-test",
        cwd=tmp_path,
        messages=(
            UserMessage(content="read it"),
            AssistantMessage(
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="call_abc",
                        tool_name="read",
                        arguments_json='{"path": "README.md"}',
                    ),
                ),
            ),
            ToolResultMessage(
                tool_request_id=request_id,
                output_text="<file contents>",
                provider_correlation_id="call_abc",
            ),
        ),
        available_tools=(ReadTool().definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    body_messages = client.requests[0]["body"]["messages"]
    assistant_message = body_messages[2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"][0]["id"] == "call_abc"
    assert assistant_message["tool_calls"][0]["function"]["name"] == "read"
    tool_message = body_messages[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_abc"
    assert tool_message["content"] == "<file contents>"


def test_openrouter_handles_dict_arguments_object():
    """Some providers return `arguments` as a JSON object instead of a string.
    The parser must JSON-encode it back so the loop can re-parse uniformly.
    """

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {
                                        "name": "read",
                                        "arguments": {"path": "x.py"},
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
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openrouter",
            model_id="openai/gpt-test",
            cwd=Path("/tmp").resolve(),
        )
    )

    assert result.tool_calls[0].arguments_json == '{"path": "x.py"}'


def test_openrouter_legacy_callers_still_get_plain_completion(tmp_path: Path):
    """Callers that do not supply `messages` keep the legacy single-turn body
    builder. This guards against regressions in the existing
    `/ask-file`/`/propose-file` paths."""

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
        http_client=client,
    )

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="openrouter",
        model_id="openai/gpt-test",
        cwd=tmp_path,
    )

    provider.complete(request)

    body = client.requests[0]["body"]
    assert "tools" not in body
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hello"},
    ]
