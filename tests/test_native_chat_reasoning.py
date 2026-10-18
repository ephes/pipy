"""Generic buffered Chat reasoning capture, exact-target replay and compat."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from provider_chat_completions_contract import FakeJsonHTTPClient

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import envelope_to_chat_message
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    ProductContent,
)
from pipy_harness.native.agent.assistant_blocks import agent_blocks
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
)
from pipy_harness.native.providers.chat_completions_wire import (
    ChatTranscriptOptions,
    chat_transcript,
)
from pipy_harness.native.providers.cloudflare import CloudflareWorkersAIProvider
from pipy_harness.native.providers.openai_completions import (
    OpenAIChatCompletionsProvider,
)
from pipy_harness.native.providers.openrouter import OpenRouterChatCompletionsProvider
from pipy_harness.native.providers.replay_content import ReplayTarget

API = "openai-completions"
DETAILS = [
    {
        "type": "reasoning.text",
        "text": "metadata only",
        "signature": None,
        "id": "a",
        "format": "x",
        "index": 0,
        "extra": {"keep": [1, None]},
    },
    {"type": "reasoning.text", "text": "separate completed entry", "id": None},
    {"type": "reasoning.summary", "summary": "not display"},
    {"type": "reasoning.encrypted", "data": ""},
]
LEGACY = {"type": "reasoning.encrypted", "id": "legacy", "data": "opaque", "other": [1]}


def response(**message: Any) -> JsonResponse:
    return JsonResponse(
        200,
        {
            "object": "chat.completion",
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
        },
    )


def request(tmp_path: Path, provider: str = "custom") -> ProviderRequest:
    return ProviderRequest(
        system_prompt="",
        user_prompt="hello",
        provider_name=provider,
        model_id="chat-model",
        cwd=tmp_path,
    )


def adapter(name: str, client: FakeJsonHTTPClient):
    if name == "openrouter":
        return OpenRouterChatCompletionsProvider(
            "chat-model", api_key="test", http_client=client
        )
    if name == "cloudflare":
        return CloudflareWorkersAIProvider(
            "chat-model", account_id="account", api_token="test", http_client=client
        )
    return OpenAIChatCompletionsProvider(
        "chat-model", api_key="test", provider_name=name, http_client=client
    )


@pytest.mark.parametrize("name", ["custom", "openrouter", "cloudflare"])
def test_buffered_adapter_captures_order_and_real_continuation(tmp_path, name):
    client = FakeJsonHTTPClient(
        response(
            reasoning_content="plan",
            reasoning="duplicate",
            reasoning_details=DETAILS,
            content="answer",
            tool_calls=[
                {
                    "id": "call-source",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"file"}'},
                }
            ],
        )
    )
    provider = adapter(name, client)
    sinks = []
    result = provider.complete(
        request(tmp_path, name), stream_sink=sinks.append, reasoning_sink=sinks.append
    )
    assert result.status is HarnessStatus.SUCCEEDED and result.api == API
    assert not sinks  # Still buffered: this slice introduces no streaming callbacks.
    thinking, text, tool = result.content_blocks
    assert thinking == ThinkingContent("plan", json.dumps(DETAILS, ensure_ascii=False))
    assert text == TextContent("answer") and tool == result.tool_calls[0]
    assert tool.thought_signature is None
    assert result.usage["total_tokens"] == 7
    assistant = AgentAssistantMessage.from_blocks(
        agent_blocks(result.content_blocks),
        provider=name,
        api=result.api,
        model=result.model_id,
    )
    provider.complete(replace(request(tmp_path, name), messages=(assistant,)))
    outgoing = client.requests[-1]["body"]["messages"][0]
    assert outgoing["content"] == "answer" and outgoing["reasoning_details"] == DETAILS
    assert "reasoning_content" not in outgoing and "reasoning" not in outgoing
    assert outgoing["tool_calls"][0]["id"] == "call-source"


@pytest.mark.parametrize(
    ("fields", "text", "signature"),
    [
        (
            {
                "reasoning_content": "first",
                "reasoning": "second",
                "reasoning_text": "third",
            },
            "first",
            "reasoning_content",
        ),
        (
            {"reasoning_content": "", "reasoning": "second", "reasoning_text": "third"},
            "second",
            "reasoning",
        ),
        (
            {"reasoning_content": 4, "reasoning": False, "reasoning_text": "third"},
            "third",
            "reasoning_text",
        ),
        ({"reasoning_content": " ", "reasoning": "second"}, " ", "reasoning_content"),
    ],
)
def test_scalar_capture_first_nonempty_string(tmp_path, fields, text, signature):
    client = FakeJsonHTTPClient(response(**fields))
    result = adapter("custom", client).complete(request(tmp_path))
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.content_blocks == (ThinkingContent(text, signature),)
    assert result.final_text is None


def test_opencode_go_alias_is_provider_identity_not_model(tmp_path):
    client = FakeJsonHTTPClient(response(reasoning="plan", content="answer"))
    result = adapter("opencode-go", client).complete(request(tmp_path, "opencode-go"))
    assert result.content_blocks[0] == ThinkingContent("plan", "reasoning_content")
    assert adapter("custom", client).complete(request(tmp_path)).content_blocks[
        0
    ] == ThinkingContent("plan", "reasoning")


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        "bad",
        {},
        {"type": []},
        {"type": "other", "text": "x"},
        {"type": "reasoning.text", "text": 4},
        {"type": "reasoning.text", "text": "x", "signature": 4},
        {"type": "reasoning.summary", "summary": False},
        {"type": "reasoning.encrypted", "data": None},
        {"type": "reasoning.text", "text": "x", "id": 3},
        {"type": "reasoning.text", "text": "x", "format": None},
        {"type": "reasoning.text", "text": "x", "index": True},
        {"type": "reasoning.text", "text": "x", "index": None},
    ],
)
def test_inbound_filters_individual_invalid_details_but_signature_replay_rejects_whole_array(
    tmp_path, invalid
):
    client = FakeJsonHTTPClient(response(reasoning_details=[invalid, *DETAILS]))
    result = adapter("custom", client).complete(request(tmp_path))
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.content_blocks == (
        ThinkingContent("", json.dumps(DETAILS, ensure_ascii=False)),
    )
    historical = assistant(ThinkingContent("plan", json.dumps([invalid, *DETAILS])))
    wire = project(historical)
    assert "reasoning_details" not in wire and "reasoning_content" not in wire
    client.response = response(reasoning_details=[invalid])
    assert (
        adapter("custom", client).complete(request(tmp_path)).status
        is HarnessStatus.FAILED
    )


def assistant(*blocks, provider="custom", api=API, model="chat-model"):
    return AgentAssistantMessage.from_blocks(
        blocks, provider=provider, api=api, model=model
    )


def project(message, *, thinking_as_text=False, required=False, target=None):
    return envelope_to_chat_message(
        message,
        target=target or ReplayTarget("custom", API, "chat-model"),
        requires_thinking_as_text=thinking_as_text,
        requires_reasoning_content=required,
    )


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "[]",
        "{}",
        "invalid",
        '"reasoning"',
        json.dumps([{"type": "reasoning.text", "text": "x", "index": False}]),
    ],
)
def test_invalid_signature_never_becomes_protocol_key(signature):
    wire = project(assistant(ThinkingContent("plan", signature), TextContent("answer")))
    assert wire == {"role": "assistant", "content": "answer"}


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
def test_valid_array_wins_legacy_and_scalar_with_independent_required_field(
    required, as_text
):
    message = assistant(
        ThinkingContent("one", "reasoning"),
        ThinkingContent("two", json.dumps(DETAILS)),
        TextContent("answer"),
        AgentToolCall(
            "call", "read", ProductContent("{}"), thought_signature=json.dumps(LEGACY)
        ),
    )
    wire = project(message, thinking_as_text=as_text, required=required)
    assert wire["reasoning_details"] == DETAILS and "reasoning" not in wire
    assert (wire.get("reasoning_content") == "") == required
    assert wire["content"] == (
        [{"type": "text", "text": "one\n\ntwo"}, {"type": "text", "text": "answer"}]
        if as_text
        else "answer"
    )


@pytest.mark.parametrize("required", [False, True])
def test_scalar_plus_legacy_fallback_suppresses_scalar(required):
    message = assistant(
        ThinkingContent("plan", "reasoning_content"),
        TextContent("answer"),
        AgentToolCall(
            "call", "read", ProductContent("{}"), thought_signature=json.dumps(LEGACY)
        ),
    )
    wire = project(message, required=required)
    assert wire["reasoning_details"] == [LEGACY]
    assert wire.get("reasoning_content") == ("" if required else None)


@pytest.mark.parametrize(
    "change",
    [{"id": ""}, {"data": ""}, {"id": None}, {"type": "reasoning.text", "text": "x"}],
)
def test_legacy_requires_nonempty_encrypted_id_and_data(change):
    legacy = {**LEGACY, **change}
    message = assistant(
        ThinkingContent("plan", "reasoning_content"),
        TextContent("answer"),
        AgentToolCall(
            "call", "read", ProductContent("{}"), thought_signature=json.dumps(legacy)
        ),
    )
    wire = project(message)
    assert "reasoning_details" not in wire and wire["reasoning_content"] == "plan"


@pytest.mark.parametrize(
    "target",
    [
        ReplayTarget("other", API, "chat-model"),
        ReplayTarget("custom", "openai-responses", "chat-model"),
        ReplayTarget("custom", API, "other-model"),
    ],
)
def test_each_target_mismatch_strips_all_reasoning_without_mutating_message(target):
    message = assistant(
        ThinkingContent("plan", json.dumps(DETAILS)),
        TextContent("answer", "text-meta"),
        AgentToolCall(
            "call", "read", ProductContent("{}"), thought_signature=json.dumps(LEGACY)
        ),
    )
    original = message.ordered_content()
    wire = project(message, target=target, thinking_as_text=True)
    assert wire["content"] == "answer" and "reasoning_details" not in wire
    assert message.ordered_content() == original


def test_scalar_joins_nonblank_blocks_and_first_valid_array_search():
    message = assistant(
        ThinkingContent(" ", "reasoning_text"),
        ThinkingContent("one", "reasoning"),
        ThinkingContent("two", "reasoning_content"),
        TextContent("answer"),
    )
    assert project(message)["reasoning"] == "one\ntwo"
    message = replace(
        message,
        blocks=(
            *message.blocks,
            ThinkingContent("", "[]"),
            ThinkingContent("", json.dumps(DETAILS)),
        ),
    )
    assert project(message)["reasoning_details"] == DETAILS


def test_transcript_skips_metadata_only_but_thinking_as_text_makes_content(tmp_path):
    message = assistant(ThinkingContent("plan", json.dumps(DETAILS)))
    req = replace(request(tmp_path), messages=(message,))
    assert (
        chat_transcript(
            req, ChatTranscriptOptions(api=API, requires_reasoning_content=True)
        ).messages
        == []
    )
    assert chat_transcript(
        req, ChatTranscriptOptions(api=API, requires_thinking_as_text=True)
    ).messages[0]["content"] == [{"type": "text", "text": "plan"}]


@pytest.mark.parametrize(
    ("provider", "url", "reasoning", "compat", "expected"),
    [
        ("deepseek", "https://example.test/v1", True, {}, True),
        ("custom", "https://API.DeepSeek.Com/v1", True, {}, True),
        ("openrouter", "https://openrouter.ai/api/v1", True, {}, False),
        (
            "deepseek",
            "https://example.test/v1",
            True,
            {"requiresReasoningContentOnAssistantMessages": False},
            False,
        ),
        (
            "custom",
            "https://example.test/v1",
            True,
            {"requiresReasoningContentOnAssistantMessages": True},
            True,
        ),
        (
            "deepseek",
            "https://example.test/v1",
            False,
            {"requiresReasoningContentOnAssistantMessages": True},
            False,
        ),
    ],
)
def test_catalog_flags_reach_actual_requests(
    tmp_path, provider, url, reasoning, compat, expected
):
    spec = NativeModelSpec(
        provider_name=provider,
        model_id="deepseek-routed-name",
        display_name="Chat",
        api=API,
        base_url=url,
        reasoning=reasoning,
        compat={**compat, "requiresThinkingAsText": True},
    )
    built = constructed(tmp_path, spec)
    client = built.http_client
    message = assistant(
        ThinkingContent("plan", json.dumps(DETAILS)),
        TextContent("answer"),
        provider=provider,
        model=spec.model_id,
    )
    built.complete(
        replace(
            request(tmp_path, provider), model_id=spec.model_id, messages=(message,)
        )
    )
    wire = client.requests[0]["body"]["messages"][0]
    assert wire["content"][0] == {"type": "text", "text": "plan"}
    assert wire["reasoning_details"] == DETAILS
    assert (wire.get("reasoning_content") == "") == expected


def constructed(tmp_path, spec):
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key="test",
        models_json_auth=None,
        thinking_level=None,
    )
    return build_provider(
        resolved, spec=spec, http_client=FakeJsonHTTPClient(response(content="OK"))
    )


@pytest.mark.parametrize("api", [API, "cloudflare-workers-ai"])
def test_custom_and_cloudflare_construction_pass_both_explicit_false_flags(
    tmp_path, api
):
    spec = NativeModelSpec(
        provider_name="custom",
        model_id="chat-model",
        display_name="Chat",
        api=api,
        base_url="https://example.test/v1",
        reasoning=True,
        compat={
            "requiresThinkingAsText": False,
            "requiresReasoningContentOnAssistantMessages": False,
        },
    )
    built = constructed(tmp_path, spec)
    client = built.http_client
    message = assistant(
        ThinkingContent("plan", "reasoning_text"), TextContent("answer")
    )
    built.complete(replace(request(tmp_path), messages=(message,)))
    wire = client.requests[0]["body"]["messages"][0]
    assert wire["content"] == "answer" and wire["reasoning_text"] == "plan"
    assert "reasoning_content" not in wire


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_text"])
def test_scalar_adapter_continuation_preserves_selected_field(tmp_path, field):
    client = FakeJsonHTTPClient(response(**{field: "plan", "content": "answer"}))
    provider = adapter("custom", client)
    result = provider.complete(request(tmp_path))
    message = AgentAssistantMessage.from_blocks(
        agent_blocks(result.content_blocks),
        provider=result.provider_name,
        api=result.api,
        model=result.model_id,
    )
    provider.complete(replace(request(tmp_path), messages=(message,)))
    assert client.requests[-1]["body"]["messages"] == [
        {"role": "assistant", "content": "answer", field: "plan"}
    ]


def test_imported_opencode_go_scalar_alias_replays_as_reasoning_content():
    message = assistant(
        ThinkingContent("plan", "reasoning"),
        TextContent("answer"),
        provider="opencode-go",
    )
    wire = project(message, target=ReplayTarget("opencode-go", API, "chat-model"))
    assert wire == {
        "role": "assistant",
        "content": "answer",
        "reasoning_content": "plan",
    }


@pytest.mark.parametrize(
    "common",
    [
        {},
        {"id": None},
        {"id": "id"},
        {"format": "format"},
        {"index": -1},
        {"index": 1.5},
        {"signature": None},
        {"signature": "signed"},
    ],
)
def test_valid_detail_common_fields_preserve_completed_json(tmp_path, common):
    detail = {
        "type": "reasoning.text",
        "text": "not display",
        **common,
        "unknown": [True, {"a": None}],
    }
    client = FakeJsonHTTPClient(response(reasoning_details=[detail], content="answer"))
    result = adapter("custom", client).complete(request(tmp_path))
    assert result.content_blocks[0] == ThinkingContent(
        "", json.dumps([detail], ensure_ascii=False)
    )
    historical = assistant(*agent_blocks(result.content_blocks))
    assert project(historical)["reasoning_details"] == [detail]


def test_legacy_tool_details_keep_tool_order_and_never_become_new_capture(tmp_path):
    second = {**LEGACY, "id": "second", "data": "other"}
    message = assistant(
        TextContent("answer"),
        AgentToolCall(
            "a", "read", ProductContent("{}"), thought_signature=json.dumps(LEGACY)
        ),
        AgentToolCall(
            "b", "read", ProductContent("{}"), thought_signature=json.dumps(second)
        ),
    )
    assert project(message)["reasoning_details"] == [LEGACY, second]
    client = FakeJsonHTTPClient(
        response(
            reasoning_details=[LEGACY, second],
            content="answer",
            tool_calls=[{"id": "a", "function": {"name": "read", "arguments": "{}"}}],
        )
    )
    captured = adapter("custom", client).complete(request(tmp_path))
    assert captured.tool_calls[0].thought_signature is None
    assert json.loads(captured.content_blocks[0].signature) == [LEGACY, second]


def test_cloudflare_catalog_explicit_true_flags_reach_wire(tmp_path):
    spec = NativeModelSpec(
        provider_name="custom",
        model_id="chat-model",
        display_name="Chat",
        api="cloudflare-workers-ai",
        base_url="https://example.test/v1",
        reasoning=True,
        compat={
            "requiresThinkingAsText": True,
            "requiresReasoningContentOnAssistantMessages": True,
        },
    )
    built = constructed(tmp_path, spec)
    message = assistant(ThinkingContent("plan", "reasoning"), TextContent("answer"))
    built.complete(replace(request(tmp_path), messages=(message,)))
    assert built.http_client.requests[0]["body"]["messages"] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "plan"},
                {"type": "text", "text": "answer"},
            ],
            "reasoning_content": "",
        }
    ]
