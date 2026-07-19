"""OpenAI Codex Responses tool-call serialization and SSE assembly tests.

These tests pin `OpenAICodexResponsesProvider` as the third real
provider with `supports_tool_calls=True`. The provider serializes the
loop's message envelope and `available_tools` into the Codex Responses
streaming `input`/`tools` shape, and reassembles streamed function
calls across `response.output_item.added`,
`response.function_call_arguments.delta`,
`response.function_call_arguments.done`, and
`response.output_item.done` events into `ProviderToolCall` instances
that the loop can dispatch.
"""

from __future__ import annotations

import base64
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
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
    OpenAICodexResponsesProvider,
    SseResponse,
)
from pipy_harness.native.tools import make_tool_request_id
from pipy_harness.native.tools.base import ToolDefinition
from pipy_harness.native.tools.read import ReadTool


class FakeSseHTTPClient:
    def __init__(self, response: SseResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float | None,
        cancel_token: object = None,
    ) -> SseResponse:
        self.requests.append({"url": url, "body": dict(body)})
        return self.response


class InMemoryCredentialStore:
    def __init__(self, credentials: OpenAICodexCredentials | None) -> None:
        self.credentials = credentials
        self.saved: list[OpenAICodexCredentials] = []

    def load(self) -> OpenAICodexCredentials | None:
        return self.credentials

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self.credentials = credentials
        self.saved.append(credentials)

    def delete(self) -> bool:
        had_credentials = self.credentials is not None
        self.credentials = None
        return had_credentials


def _base64url(value: Mapping[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def fake_jwt(account_id: str = "acct_test") -> str:
    header = _base64url({"alg": "none"})
    payload = _base64url(
        {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    )
    return f"{header}.{payload}.signature"


def credentials() -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=fake_jwt("acct_test"),
        refresh_token="refresh-test",
        expires_at=4_102_444_800,
        account_id="acct_test",
    )


def auth_manager_with(creds: OpenAICodexCredentials) -> OpenAICodexAuthManager:
    return OpenAICodexAuthManager(store=InMemoryCredentialStore(creds))


def sse_payload(events: list[Mapping[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


def _completed_sse() -> SseResponse:
    return SseResponse(
        status_code=200,
        body=sse_payload([
            {"type": "response.output_text.delta", "delta": "done"},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]),
    )


def test_openai_codex_supports_tool_calls_is_true():
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test", auth_manager=auth_manager_with(credentials())
    )

    assert provider.supports_tool_calls is True


def test_openai_codex_serializes_tools_in_responses_request(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {"type": "response.output_text.delta", "delta": "ok"},
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )
    read_tool = ReadTool()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="please read",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(AgentUserMessage(content=ProductContent("please read")),),
        available_tools=(read_tool.definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    body = client.requests[0]["body"]
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


def test_openai_codex_assembles_function_call_from_sse_deltas(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_abc",
                            "call_id": "call_abc",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_abc",
                        "delta": '{"path":',
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_abc",
                        "delta": ' "README.md"}',
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc_abc",
                        "arguments": '{"path": "README.md"}',
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, ProviderToolCall)
    assert call.provider_correlation_id == "call_abc|fc_abc"
    assert call.tool_name == "read"
    # The `.done` arguments win over accumulated deltas.
    assert call.arguments_json == '{"path": "README.md"}'


def test_openai_codex_finalizes_function_call_on_output_item_done(tmp_path: Path):
    """If the stream variant skips `function_call_arguments.done` and the
    final arguments only land on `response.output_item.done`, the
    assembler must still produce the complete `ProviderToolCall`."""

    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_b",
                            "call_id": "call_b",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_b",
                        "delta": '{"path": "x.py"}',
                    },
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "id": "fc_b",
                            "call_id": "call_b",
                            "name": "read",
                            "arguments": '{"path": "x.py"}',
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].provider_correlation_id == "call_b|fc_b"
    assert result.tool_calls[0].arguments_json == '{"path": "x.py"}'


def test_openai_codex_uses_item_id_when_stream_omits_call_id(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_only",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc_only",
                        "arguments": '{"path": "x.py"}',
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].provider_correlation_id == "fc_only"


def test_openai_codex_assembles_multiple_function_calls_in_order(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc_1",
                        "arguments": '{"path": "a.py"}',
                    },
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_2",
                            "call_id": "call_2",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc_2",
                        "arguments": '{"path": "b.py"}',
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert [call.provider_correlation_id for call in result.tool_calls] == [
        "call_1|fc_1",
        "call_2|fc_2",
    ]


def test_openai_codex_merges_added_metadata_into_delta_placeholder(
    tmp_path: Path,
):
    """First-review fix-up: when a `response.function_call_arguments.delta`
    arrives before `response.output_item.added` for the same `item_id`, the
    assembler creates a placeholder with no `call_id` / `name`. The
    later `added` event must merge that metadata into the placeholder so
    finalization does not drop the call as nameless."""

    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_late",
                        "delta": '{"path":',
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_late",
                        "delta": ' "README.md"}',
                    },
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_late",
                            "call_id": "call_late",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.provider_correlation_id == "call_late|fc_late"
    assert call.tool_name == "read"
    assert call.arguments_json == '{"path": "README.md"}'


def test_openai_codex_merges_added_metadata_into_done_placeholder(
    tmp_path: Path,
):
    """First-review fix-up companion: same merge behavior must hold when
    only a `response.function_call_arguments.done` event arrives before
    `response.output_item.added` (no incremental deltas)."""

    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc_done_first",
                        "arguments": '{"path": "x.py"}',
                    },
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "function_call",
                            "id": "fc_done_first",
                            "call_id": "call_done_first",
                            "name": "read",
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(
        ProviderRequest(
            system_prompt="SYS",
            user_prompt="go",
            provider_name="openai-codex",
            model_id="gpt-test",
            cwd=tmp_path,
            messages=(AgentUserMessage(content=ProductContent("go")),),
            available_tools=(ReadTool().definition,),
        )
    )

    assert len(result.tool_calls) == 1
    assert (
        result.tool_calls[0].provider_correlation_id
        == "call_done_first|fc_done_first"
    )
    assert result.tool_calls[0].arguments_json == '{"path": "x.py"}'


def test_openai_codex_serializes_tool_result_envelope(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {"type": "response.output_text.delta", "delta": "done"},
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )
    request_id = make_tool_request_id()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="follow up",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(
            AgentUserMessage(content=ProductContent("read it")),
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_abc|fc_abc",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "README.md"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id=request_id,
                tool_name="read",
                content=ProductContent("<file contents>"),
                provider_correlation_id="call_abc|fc_abc",
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
        "id": "fc_abc",
        "call_id": "call_abc",
        "name": "read",
        "arguments": '{"path": "README.md"}',
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": "<file contents>",
    }


def test_openai_codex_replays_legacy_call_id_without_item_id(tmp_path: Path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {"type": "response.output_text.delta", "delta": "done"},
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )
    request_id = make_tool_request_id()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="follow up",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=tmp_path,
        messages=(
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        provider_correlation_id="call_legacy",
                        tool_name="read",
                        arguments_json=ProductContent('{"path": "README.md"}'),
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id=request_id,
                tool_name="read",
                content=ProductContent("<file contents>"),
                provider_correlation_id="call_legacy",
            ),
        ),
        available_tools=(ReadTool().definition,),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    items = client.requests[0]["body"]["input"]
    assert items[0] == {
        "type": "function_call",
        "call_id": "call_legacy",
        "name": "read",
        "arguments": '{"path": "README.md"}',
    }
    assert items[1] == {
        "type": "function_call_output",
        "call_id": "call_legacy",
        "output": "<file contents>",
    }


def test_openai_codex_places_deferred_tools_with_full_correlation_hash(tmp_path: Path) -> None:
    client = FakeSseHTTPClient(_completed_sse())
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.6-sol", auth_manager=auth_manager_with(credentials()),
        http_client=client, supports_tool_search=True,
    )
    provider.complete(ProviderRequest(
        system_prompt="SYS", user_prompt="load", provider_name="openai-codex",
        model_id="gpt-5.6-sol", cwd=tmp_path,
        messages=(
            AgentAssistantMessage(
                content=ProductContent(""),
                tool_calls=(
                    AgentToolCall(
                        "call_loader|fc_loader", "loader", ProductContent("{}")
                    ),
                ),
            ),
            AgentToolResultMessage(
                tool_request_id="pipy-tool-load", tool_name="loader",
                content=ProductContent("loaded"),
                provider_correlation_id="call_loader|fc_loader",
                added_tool_names=("late_tool", "later_tool"),
            ),
        ),
        available_tools=(_tool("loader"), _tool("late_tool"), _tool("later_tool")),
    ))

    body = client.requests[0]["body"]
    assert [tool["name"] for tool in body["tools"]] == ["loader"]
    assert body["input"][-3:] == [
        {"type": "function_call_output", "call_id": "call_loader", "output": "loaded"},
        {
            "type": "tool_search_call", "call_id": "pi_tool_load_igackd1nf7qef",
            "execution": "client", "status": "completed",
            "arguments": {"query": "late_tool later_tool", "limit": 2},
        },
        {
            "type": "tool_search_output", "call_id": "pi_tool_load_igackd1nf7qef",
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


def test_openai_codex_tool_search_defaults_off(tmp_path: Path) -> None:
    client = FakeSseHTTPClient(_completed_sse())
    OpenAICodexResponsesProvider(
        model_id="gpt-test", auth_manager=auth_manager_with(credentials()),
        http_client=client,
    ).complete(ProviderRequest(
        system_prompt="SYS", user_prompt="load", provider_name="openai-codex",
        model_id="gpt-test", cwd=tmp_path,
        messages=(AgentToolResultMessage(
            tool_request_id="pipy-tool-load", tool_name="loader",
            content=ProductContent("loaded"),
            provider_correlation_id="call_loader|fc_loader",
            added_tool_names=("late_tool",),
        ),),
        available_tools=(_tool("late_tool"),),
    ))

    body = client.requests[0]["body"]
    assert [tool["name"] for tool in body["tools"]] == ["late_tool"]
    assert not any(item.get("type") == "tool_search_output" for item in body["input"])


def test_openai_codex_legacy_callers_still_get_no_tools_field(tmp_path: Path):
    """Callers that do not supply `messages` or `available_tools` keep the
    existing body builder, with no `tools` field set, and the resulting
    `ProviderResult.tool_calls` stays empty. This guards against
    regressions in non-tool-loop callers such as
    `pipy run --agent pipy-native --goal ...`.
    """

    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {"type": "response.output_text.delta", "delta": "hi"},
                    {
                        "type": "response.completed",
                        "response": {"status": "completed"},
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=tmp_path,
    )

    result = provider.complete(request)

    body = client.requests[0]["body"]
    assert "tools" not in body
    assert body["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
    ]
    assert result.final_text == "hi"
    assert result.tool_calls == ()
