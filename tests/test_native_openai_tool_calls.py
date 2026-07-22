"""OpenAI Responses tool-call serialization and parsing tests.

These tests pin `OpenAIResponsesProvider` as the second real provider with
`supports_tool_calls=True`. The provider serializes the loop's message
envelope and `available_tools` into the OpenAI Responses API
`input`/`tools` shape, and parses returned `function_call` items into
`ProviderToolCall` instances that the loop can dispatch into the
production tool registry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.providers.openai_responses import (
    JsonResponse,
    OpenAIResponsesProvider,
)
from pipy_harness.native.tools import make_tool_request_id
from pipy_harness.native.tools.base import ToolDefinition
from pipy_harness.native.tools.read import ReadTool


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
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_internal",
                    "call_id": "call_abc",
                    "name": "read",
                    "arguments": json.dumps({"path": "README.md"}),
                }
            ],
        },
    )


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


def _completed_response() -> JsonResponse:
    return JsonResponse(
        status_code=200,
        body={"status": "completed", "output_text": "done"},
    )


def test_openai_supports_tool_calls_is_true():
    provider = OpenAIResponsesProvider(model_id="gpt-test", api_key="sk-test")

    assert provider.supports_tool_calls is True


def test_openai_serializes_tools_in_responses_request(tmp_path: Path):
    client = FakeJsonHTTPClient(_tool_call_response())
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )
    read_tool = ReadTool()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="please read",
        provider_name="openai",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(AgentUserMessage(content=ProductContent("please read")),),
        available_tools=(read_tool.definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    body = client.requests[0]["body"]
    assert body["model"] == "gpt-test"
    assert body["instructions"] == "SYS"
    assert body["store"] is False
    assert isinstance(body["tools"], list)
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "read"
    assert "description" in body["tools"][0]
    assert isinstance(body["tools"][0]["parameters"], dict)
    assert body["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "please read"}],
        }
    ]


def test_openai_parses_function_call_into_provider_tool_call(tmp_path: Path):
    client = FakeJsonHTTPClient(_tool_call_response())
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
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


def test_openai_serializes_tool_result_envelope(tmp_path: Path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output_text": "done",
            },
        )
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )
    request_id = make_tool_request_id()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="follow up",
        provider_name="openai",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(
            AgentUserMessage(content=ProductContent("read it")),
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_abc",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "README.md"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id=request_id,
                tool_name="read",
                content=ProductContent("<file contents>"),
                provider_correlation_id="call_abc",
                added_tool_names=("read",),
            ),
        ),
        available_tools=(ReadTool().definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    items = client.requests[0]["body"]["input"]
    assert items[0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "read it"}],
    }
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_abc",
        "name": "read",
        "arguments": '{"path": "README.md"}',
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": "<file contents>",
    }
    assert [tool["name"] for tool in client.requests[0]["body"]["tools"]] == ["read"]


def test_openai_places_deferred_tools_at_result_marker(tmp_path: Path) -> None:
    client = FakeJsonHTTPClient(_completed_response())
    provider = OpenAIResponsesProvider(
        model_id="gpt-5.5", api_key="sk-test", http_client=client,
        supports_tool_search=True,
    )
    provider.complete(ProviderRequest(
        system_prompt="SYS", user_prompt="load", provider_name="openai",
        model_id="gpt-5.5", cwd=tmp_path,
        messages=(
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(AgentToolCall("call_loader", "loader", ProductContent("{}")),),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-load", tool_name="loader",
                content=ProductContent("loaded"),
                provider_correlation_id="call_loader",
                added_tool_names=("late_tool", "later_tool", "late_tool", "missing_tool"),
            ),
        ),
        available_tools=(_tool("loader"), _tool("late_tool"), _tool("later_tool")),
    ))

    body = client.requests[0]["body"]
    assert [tool["name"] for tool in body["tools"]] == ["loader"]
    assert body["input"][-3:] == [
        {"type": "function_call_output", "call_id": "call_loader", "output": "loaded"},
        {
            "type": "tool_search_call", "call_id": "pi_tool_load_dulyo1k6qd28",
            "execution": "client", "status": "completed",
            "arguments": {"query": "late_tool later_tool", "limit": 2},
        },
        {
            "type": "tool_search_output", "call_id": "pi_tool_load_dulyo1k6qd28",
            "execution": "client", "status": "completed",
            "tools": [
                {
                    "type": "function", "name": name,
                    "description": f"{name} description",
                    "parameters": {"type": "object", "properties": {}},
                    "strict": False, "defer_loading": True,
                }
                for name in ("late_tool", "later_tool")
            ],
        },
    ]


def test_openai_places_distinct_searches_at_multiple_result_markers(
    tmp_path: Path,
) -> None:
    client = FakeJsonHTTPClient(_completed_response())
    provider = OpenAIResponsesProvider(
        model_id="gpt-5.5", api_key="sk-test", http_client=client,
        supports_tool_search=True,
    )
    provider.complete(ProviderRequest(
        system_prompt="SYS", user_prompt="load twice", provider_name="openai",
        model_id="gpt-5.5", cwd=tmp_path,
        messages=(
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(AgentToolCall("call_first", "loader", ProductContent("{}")),),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-load-first", tool_name="loader",
                content=ProductContent("first loaded"),
                provider_correlation_id="call_first",
                added_tool_names=("late_tool", "shared_tool"),
            ),
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(AgentToolCall("call_second", "loader", ProductContent("{}")),),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-load-second", tool_name="loader",
                content=ProductContent("second loaded"),
                provider_correlation_id="call_second",
                added_tool_names=("shared_tool", "later_tool"),
            ),
        ),
        available_tools=(
            _tool("loader"), _tool("late_tool"), _tool("shared_tool"),
            _tool("later_tool"),
        ),
    ))

    body = client.requests[0]["body"]
    assert [tool["name"] for tool in body["tools"]] == ["loader"]
    searches = [
        item for item in body["input"]
        if item.get("type") in {"tool_search_call", "tool_search_output"}
    ]
    assert [item["call_id"] for item in searches] == [
        "pi_tool_load_14aec2a1ydkqv2", "pi_tool_load_14aec2a1ydkqv2",
        "pi_tool_load_hvb69z1li7qa9", "pi_tool_load_hvb69z1li7qa9",
    ]
    assert searches[0]["arguments"] == {
        "query": "late_tool shared_tool", "limit": 2,
    }
    assert [tool["name"] for tool in searches[1]["tools"]] == [
        "late_tool", "shared_tool",
    ]
    assert searches[2]["arguments"] == {"query": "later_tool", "limit": 1}
    assert [tool["name"] for tool in searches[3]["tools"]] == ["later_tool"]


def test_openai_tool_search_unsupported_and_all_deferred_matrix(tmp_path: Path) -> None:
    marker = AgentToolResultMessage(
        tool_request_id="pipy-tool-load", tool_name="loader",
        content=ProductContent("loaded"),
        provider_correlation_id="call_loader", added_tool_names=("late_tool",),
    )
    for supported, expected_top_level, expected_search in (
        (False, ["late_tool"], False), (True, [], True),
    ):
        client = FakeJsonHTTPClient(_completed_response())
        OpenAIResponsesProvider(
            model_id="gpt-test", api_key="sk-test", http_client=client,
            supports_tool_search=supported,
        ).complete(ProviderRequest(
            system_prompt="SYS", user_prompt="load", provider_name="openai",
            model_id="gpt-test", cwd=tmp_path, messages=(marker,),
            available_tools=(_tool("late_tool"),),
        ))
        body = client.requests[0]["body"]
        assert [tool["name"] for tool in body.get("tools", [])] == expected_top_level
        assert any(item.get("type") == "tool_search_output" for item in body["input"]) is expected_search


def test_openai_includes_assistant_text_alongside_tool_calls(tmp_path: Path):
    """An assistant turn with both `content` and `tool_calls` serializes
    both: an `output_text` message item plus one `function_call` item."""

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "done"},
        )
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="x",
        provider_name="openai",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(
            AgentUserMessage(content=ProductContent("hello")),
            AgentAssistantMessage(
                content=ProductContent("let me read it"),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_abc",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "x.py"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id=make_tool_request_id(),
                tool_name="read",
                content=ProductContent("..."),
                provider_correlation_id="call_abc",
            ),
        ),
        available_tools=(ReadTool().definition,),
    )

    provider.complete(request)

    items = client.requests[0]["body"]["input"]
    assert items[1] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "let me read it"}],
    }
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_abc"


def test_openai_handles_dict_arguments_object(tmp_path: Path):
    """If a provider variant ever returns `arguments` as a JSON object
    rather than an encoded string, the parser must round-trip it back to
    a string so the loop can re-parse uniformly."""

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_x",
                        "name": "read",
                        "arguments": {"path": "x.py"},
                    }
                ],
            },
        )
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert result.tool_calls[0].arguments_json == '{"path": "x.py"}'


def test_openai_falls_back_to_id_when_call_id_missing(tmp_path: Path):
    """Some Responses payloads omit `call_id` and only carry `id`. The
    parser must still produce a non-empty `provider_correlation_id`."""

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_only_id",
                        "name": "read",
                        "arguments": "{}",
                    }
                ],
            },
        )
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert result.tool_calls[0].provider_correlation_id == "fc_only_id"


def test_openai_legacy_callers_still_get_plain_completion(tmp_path: Path):
    """Callers that do not supply `messages` keep the legacy single-turn
    body builder. This guards non-tool-loop callers such as
    `pipy run --agent pipy-native --goal ...`.
    """

    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={"status": "completed", "output_text": "hi"},
        )
    )
    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
        http_client=client,
    )

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="openai",
        model_id="gpt-test",
        cwd=tmp_path,
    )

    provider.complete(request)

    body = client.requests[0]["body"]
    assert "tools" not in body
    assert body["input"] == "hello"
    assert body["instructions"] == "SYS"
