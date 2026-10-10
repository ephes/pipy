"""Reasoning storage and replay (DF1-F2b, DF1-F6b, SYS1c remainder).

Pi (``1b347794e``) keeps an assistant message's ordered content -- thinking
blocks with their provider signatures, text with its ``textSignature``, tool
calls with a Gemini ``thoughtSignature`` -- stores it in the session, replays
it to the model that produced it (``transform-messages.ts`` and each
adapter's ``convertMessages``) and gives any other model the thinking as text.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import envelope_to_chat_message
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.assistant_blocks import (
    PartialAssistantContent,
    assistant_message,
)
from pipy_harness.native.agent.content import (
    REDACTED_THINKING_TEXT,
    TextContent,
    ThinkingContent,
    display_segments,
)
from pipy_harness.native.agent.events import (
    AssistantReasoningDelta,
    AssistantTextDelta,
    RetryScheduled,
)
from pipy_harness.native.agent.messages import AgentMessageUsage
from pipy_harness.native.agent.results import AgentFailure
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.coding.request_budget import estimate_request
from pipy_harness.native.deferred_tools import short_hash
from pipy_harness.native.http import JsonResponse, ProviderHTTPError
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponseParseError,
    _parse_response_events,
    _responses_input_messages,
)
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.anthropic_messages_wire import (
    envelope_to_message,
)
from pipy_harness.native.providers.anthropic_messages_wire import (
    parse_response as parse_anthropic,
)
from pipy_harness.native.providers.azure_openai_responses import (
    AzureOpenAIResponsesProvider,
)
from pipy_harness.native.providers.google_generate_content_wire import (
    envelope_to_content,
)
from pipy_harness.native.providers.google_generate_content_wire import (
    parse_response as parse_gemini,
)
from pipy_harness.native.providers.openai_responses_wire import (
    AZURE_TOOL_CALL_PROVIDERS,
    OPENAI_TOOL_CALL_PROVIDERS,
    ResponsesReplay,
)
from pipy_harness.native.providers.openai_responses_wire import (
    parse_response as parse_responses,
)
from pipy_harness.native.providers.replay_content import (
    ReplayTarget,
    transform_assistant_blocks,
)
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree

NOW = datetime(2026, 9, 30, tzinfo=UTC)
SIGNED = ThinkingContent("I should read the file.", "sig-abc")
CALL = AgentToolCall("toolu_1", "read", ProductContent('{"path":"a"}'))


def _message(
    *blocks: TextContent | ThinkingContent | AgentToolCall,
    provider: str = "anthropic",
    api: str = "anthropic-messages",
    model: str = "claude-x",
) -> AgentAssistantMessage:
    return AgentAssistantMessage.from_blocks(
        blocks, provider=provider, api=api, model=model
    )


# -- message model -------------------------------------------------------------


def test_blocks_must_match_content_and_calls_and_default_layout_is_dropped() -> None:
    message = _message(SIGNED, TextContent("answer"), CALL)
    assert message.content.value == "answer"
    assert message.tool_calls == (CALL,)
    assert message.ordered_content() == (SIGNED, TextContent("answer"), CALL)

    plain = AgentAssistantMessage.from_blocks([TextContent("answer"), CALL])
    assert plain.blocks == ()
    assert plain == AgentAssistantMessage(ProductContent("answer"), (CALL,))
    assert plain.ordered_content() == (TextContent("answer"), CALL)
    # Thinking is history: a message with it differs from one without.
    assert message != AgentAssistantMessage(ProductContent("answer"), (CALL,))

    with pytest.raises(ValueError, match="text must equal"):
        AgentAssistantMessage(ProductContent("other"), blocks=(TextContent("x"),))
    with pytest.raises(ValueError, match="tool calls must equal"):
        AgentAssistantMessage(ProductContent(""), blocks=(CALL,))
    with pytest.raises(TypeError, match="blocks must hold"):
        AgentAssistantMessage(ProductContent(""), blocks=("x",))  # type: ignore[arg-type]


def test_a_stopped_message_keeps_partial_tool_calls() -> None:
    aborted = AgentAssistantMessage.from_blocks(
        [ThinkingContent("partial"), CALL], stop_reason=AgentStopReason.ABORTED
    )
    assert aborted.tool_calls == (CALL,)


# -- transformMessages -----------------------------------------------------------


def test_transform_keeps_own_blocks_and_converts_foreign_ones() -> None:
    redacted = ThinkingContent(REDACTED_THINKING_TEXT, "opaque", redacted=True)
    unsigned = ThinkingContent("unsigned thought")
    blank_signed = ThinkingContent("", "encrypted")
    blank = ThinkingContent("  ")
    signed_text = TextContent("answer", '{"v":1,"id":"msg_1"}')
    call = AgentToolCall("c", "read", ProductContent("{}"), thought_signature="QUJD")
    message = _message(
        redacted, SIGNED, unsigned, blank_signed, blank, signed_text, call
    )

    same = ReplayTarget("anthropic", "anthropic-messages", "claude-x")
    assert transform_assistant_blocks(message, same) == (
        redacted,
        SIGNED,
        unsigned,
        blank_signed,
        signed_text,
        call,
    )
    for other in (
        ReplayTarget("anthropic", "anthropic-messages", "claude-y"),
        ReplayTarget("anthropic", "other-api", "claude-x"),
        ReplayTarget("openai", "anthropic-messages", "claude-x"),
        None,
    ):
        assert transform_assistant_blocks(message, other) == (
            TextContent(SIGNED.thinking),
            TextContent("unsigned thought"),
            TextContent("answer"),
            AgentToolCall("c", "read", ProductContent("{}")),
        )


def test_a_message_without_a_recorded_api_is_another_models() -> None:
    legacy = AgentAssistantMessage.from_blocks(
        [SIGNED, TextContent("a")], provider="anthropic", model="claude-x"
    )
    target = ReplayTarget("anthropic", "anthropic-messages", "claude-x")
    assert transform_assistant_blocks(legacy, target)[0] == TextContent(SIGNED.thinking)


# -- Anthropic -----------------------------------------------------------------


ANTHROPIC = ReplayTarget("anthropic", "anthropic-messages", "claude-x")


def test_anthropic_parses_thinking_redacted_text_and_tool_use_in_order() -> None:
    parsed = parse_anthropic(
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "Reading."},
                {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {}},
                {"type": "thinking", "thinking": "late"},
            ],
        },
        parse_error_class=ProviderHTTPError,
        response_label="Anthropic",
        tool_call_provider_prefix="anthropic",
    )
    assert parsed.content_blocks == (
        ThinkingContent("plan", "sig-1"),
        ThinkingContent(REDACTED_THINKING_TEXT, "opaque", redacted=True),
        TextContent("Reading."),
        parsed.tool_calls[0],
        ThinkingContent("late", ""),
    )


def test_anthropic_replays_own_thinking_with_signatures() -> None:
    message = _message(
        ThinkingContent(REDACTED_THINKING_TEXT, "opaque", redacted=True),
        SIGNED,
        ThinkingContent("unsigned"),
        ThinkingContent("   ", " "),
        TextContent("Reading."),
        CALL,
    )
    wire = envelope_to_message(
        message, parse_error_class=ProviderHTTPError, target=ANTHROPIC
    )
    assert wire == {
        "role": "assistant",
        "content": [
            {"type": "redacted_thinking", "data": "opaque"},
            {
                "type": "thinking",
                "thinking": "I should read the file.",
                "signature": "sig-abc",
            },
            {"type": "text", "text": "unsigned"},
            {"type": "text", "text": "Reading."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "read",
                "input": {"path": "a"},
            },
        ],
    }


def test_anthropic_gets_another_models_thinking_as_text() -> None:
    codex = _message(
        ThinkingContent("codex plan", '{"type":"reasoning","id":"rs_1"}'),
        TextContent("done", '{"v":1,"id":"msg_1"}'),
        provider="openai-codex",
        api="openai-codex-responses",
        model="gpt-x",
    )
    wire = envelope_to_message(
        codex, parse_error_class=ProviderHTTPError, target=ANTHROPIC
    )
    assert wire["content"] == [
        {"type": "text", "text": "codex plan"},
        {"type": "text", "text": "done"},
    ]


class _AnthropicClient:
    def __init__(
        self, body: Mapping[str, Any] | None = None, status: int = 200
    ) -> None:
        self.body = body or {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "s1"},
                {"type": "text", "text": "hello"},
            ],
            "usage": {},
        }
        self.status = status
        self.requests: list[Mapping[str, Any]] = []
        self.cancel = False

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        del url, headers, timeout_seconds, cancel_token
        self.requests.append(body)
        if self.cancel:
            raise ProviderCancelledError()
        return JsonResponse(status_code=self.status, body=dict(self.body))


def _request(
    messages: tuple[Any, ...] = (),
    *,
    provider: str = "anthropic",
    model: str = "claude-x",
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="go",
        provider_name=provider,
        model_id=model,
        cwd=Path("/tmp"),
        messages=messages or (AgentUserMessage(ProductContent("go")),),
    )


def test_anthropic_result_records_blocks_api_and_effort_on_every_outcome() -> None:
    client = _AnthropicClient()
    provider = AnthropicProvider(
        model_id="claude-x",
        api_key="k",
        http_client=client,
        supports_mid_convo_effort=True,
        reasoning_effort="medium",
    )
    result = provider.complete(_request())
    assert result.api == "anthropic-messages"
    assert result.provider_thinking_level == "medium"
    assert result.content_blocks == (ThinkingContent("hmm", "s1"), TextContent("hello"))

    failing = AnthropicProvider(
        model_id="claude-x",
        api_key="k",
        http_client=_AnthropicClient(status=500),
        supports_mid_convo_effort=True,
        reasoning_effort="medium",
    )
    failed = failing.complete(_request())
    assert failed.status is HarnessStatus.FAILED
    assert failed.provider_thinking_level == "medium"

    cancelled_client = _AnthropicClient()
    cancelled_client.cancel = True
    cancelling = AnthropicProvider(
        model_id="claude-x",
        api_key="k",
        http_client=cancelled_client,
        supports_mid_convo_effort=True,
        reasoning_effort="medium",
    )
    with pytest.raises(ProviderCancelledError) as caught:
        cancelling.complete(_request())
    assert caught.value.partial is not None
    assert caught.value.partial.provider_thinking_level == "medium"


def test_anthropic_request_replays_the_stored_answer_to_the_same_model() -> None:
    client = _AnthropicClient()
    provider = AnthropicProvider(model_id="claude-x", api_key="k", http_client=client)
    first = provider.complete(_request())
    answer = assistant_message(first, AgentMessageUsage(), _request())
    history = (
        AgentUserMessage(ProductContent("go")),
        answer,
        AgentUserMessage(ProductContent("again")),
    )
    provider.complete(_request(history))
    assert client.requests[-1]["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "s1"},
            {"type": "text", "text": "hello"},
        ],
    }
    other = AnthropicProvider(model_id="claude-y", api_key="k", http_client=client)
    other.complete(_request(history, model="claude-y"))
    assert client.requests[-1]["messages"][1]["content"] == [
        {"type": "text", "text": "hmm"},
        {"type": "text", "text": "hello"},
    ]


# -- OpenAI Responses / Azure / Codex -----------------------------------------------

REASONING_ITEM = {
    "id": "rs_1",
    "type": "reasoning",
    "summary": [
        {"type": "summary_text", "text": "First part"},
        {"type": "summary_text", "text": "Second part"},
    ],
    "encrypted_content": "ENC",
}
MESSAGE_ITEM = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "phase": "commentary",
    "content": [{"type": "output_text", "text": "Reading.", "annotations": []}],
}
CALL_ITEM = {
    "id": "fc_1",
    "type": "function_call",
    "call_id": "call_1",
    "name": "read",
    "arguments": '{"path":"a"}',
}


def test_responses_body_parses_reasoning_message_and_compound_call() -> None:
    parsed = parse_responses(
        {"status": "completed", "output": [REASONING_ITEM, MESSAGE_ITEM, CALL_ITEM]},
        parse_error_class=ProviderHTTPError,
        response_label="OpenAI",
        nested_usage_fields=(),
        tool_call_provider_prefix="openai",
    )
    thinking, text, call = parsed.content_blocks
    assert thinking == ThinkingContent(
        "First part\n\nSecond part",
        json.dumps(REASONING_ITEM, separators=(",", ":")),
    )
    assert text == TextContent("Reading.", '{"v":1,"id":"msg_1","phase":"commentary"}')
    assert isinstance(call, ProviderToolCall)
    assert call.provider_correlation_id == "call_1|fc_1"
    assert parsed.final_text == "Reading."
    assert parsed.tool_calls == (call,)


def _codex_message(model: str = "gpt-a") -> AgentAssistantMessage:
    return _message(
        ThinkingContent(
            "First part", json.dumps(REASONING_ITEM, separators=(",", ":"))
        ),
        TextContent("Reading.", '{"v":1,"id":"msg_1","phase":"commentary"}'),
        AgentToolCall("call_1|fc_1", "read", ProductContent('{"path":"a"}')),
        provider="openai-codex",
        api="openai-codex-responses",
        model=model,
    )


def test_codex_replays_reasoning_items_message_ids_and_item_ids_to_itself() -> None:
    request = _request(
        (AgentUserMessage(ProductContent("go")), _codex_message()),
        provider="openai-codex",
        model="gpt-a",
    )
    items = _responses_input_messages(request)
    assert items[1:] == [
        REASONING_ITEM,
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Reading.", "annotations": []}],
            "status": "completed",
            "id": "msg_1",
            "phase": "commentary",
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "read",
            "arguments": '{"path":"a"}',
            "id": "fc_1",
        },
    ]


def test_codex_model_switch_sends_reasoning_as_text_and_drops_item_ids() -> None:
    request = _request(
        (AgentUserMessage(ProductContent("go")), _codex_message("gpt-a")),
        provider="openai-codex",
        model="gpt-b",
    )
    items = _responses_input_messages(request)
    assert items[1:] == [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "First part", "annotations": []}
            ],
            "status": "completed",
            "id": "msg_pi_1",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Reading.", "annotations": []}],
            "status": "completed",
            "id": "msg_pi_1_1",
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "read",
            "arguments": '{"path":"a"}',
        },
    ]


def test_responses_foreign_compound_ids_hash_on_accepting_targets_only() -> None:
    message = _codex_message()
    openai = ResponsesReplay(
        ReplayTarget("openai", "openai-responses", "gpt-a"),
        OPENAI_TOOL_CALL_PROVIDERS,
    )
    call = openai.items(message, 0)[-1]
    assert call["id"] == f"fc_{short_hash('fc_1')}"
    azure = ResponsesReplay(
        ReplayTarget("azure-openai", "azure-openai-responses", "gpt-a"),
        AZURE_TOOL_CALL_PROVIDERS,
    )
    assert "id" not in azure.items(message, 0)[-1]
    # A long message id is shortened like Pi (OpenAI allows 64 characters).
    long_id = "msg_" + "x" * 80
    long_text = _message(
        TextContent("t", json.dumps({"v": 1, "id": long_id})),
        provider="openai-codex",
        api="openai-codex-responses",
        model="gpt-a",
    )
    codex = ResponsesReplay(
        ReplayTarget("openai-codex", "openai-codex-responses", "gpt-a"),
        OPENAI_TOOL_CALL_PROVIDERS,
    )
    assert codex.items(long_text, 0)[0]["id"] == f"msg_{short_hash(long_id)}"


def test_responses_gets_anthropic_thinking_as_a_message() -> None:
    anthropic = _message(SIGNED, TextContent("answer"))
    replay = ResponsesReplay(
        ReplayTarget("openai", "openai-responses", "gpt-a"),
        OPENAI_TOOL_CALL_PROVIDERS,
    )
    items = replay.items(anthropic, 3)
    assert [item["content"][0]["text"] for item in items] == [  # type: ignore[index]
        SIGNED.thinking,
        "answer",
    ]
    assert [item["id"] for item in items] == ["msg_pi_3", "msg_pi_3_1"]


def _events(*events: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield from events


def test_codex_stream_assembles_items_and_backfills_encrypted_content() -> None:
    reasoning_done = dict(REASONING_ITEM)
    del reasoning_done["encrypted_content"]
    reasoning_chunks: list[str] = []
    text_chunks: list[str] = []
    parsed = _parse_response_events(
        _events(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": "rs_1", "type": "reasoning", "summary": []},
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "item_id": "rs_1",
                "delta": "First",
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": reasoning_done,
            },
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {"id": "msg_1", "type": "message", "content": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "item_id": "msg_1",
                "delta": "Reading.",
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": MESSAGE_ITEM,
            },
            {
                "type": "response.output_item.added",
                "output_index": 2,
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "read",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 2,
                "item_id": "fc_1",
                "delta": '{"path":"a"}',
            },
            {"type": "response.output_item.done", "output_index": 2, "item": CALL_ITEM},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [REASONING_ITEM, MESSAGE_ITEM, CALL_ITEM],
                },
            },
        ),
        stream_sink=text_chunks.append,
        reasoning_sink=reasoning_chunks.append,
    )
    thinking, text, call = parsed.content_blocks
    assert isinstance(thinking, ThinkingContent)
    assert json.loads(thinking.signature or "")["encrypted_content"] == "ENC"
    assert thinking.thinking == "First part\n\nSecond part"
    assert text.signature == '{"v":1,"id":"msg_1","phase":"commentary"}'  # type: ignore[union-attr]
    assert isinstance(call, ProviderToolCall)
    assert call.provider_correlation_id == "call_1|fc_1"
    assert (reasoning_chunks, text_chunks) == (["First"], ["Reading."])


def test_codex_stream_keeps_interleaved_calls_apart_by_output_index() -> None:
    parsed = _parse_response_events(
        _events(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "fc_a",
                    "type": "function_call",
                    "call_id": "c_a",
                    "name": "read",
                },
            },
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {
                    "id": "fc_b",
                    "type": "function_call",
                    "call_id": "c_b",
                    "name": "ls",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": '{"b":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '{"a":1}',
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": "2}",
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ),
    )
    assert [
        (c.provider_correlation_id, c.arguments_json) for c in parsed.tool_calls
    ] == [
        ("c_a|fc_a", '{"a":1}'),
        ("c_b|fc_b", '{"b":2}'),
    ]


def test_codex_delta_for_an_unknown_item_never_joins_another_call() -> None:
    parsed = _parse_response_events(
        _events(
            {
                "type": "response.output_item.added",
                "item": {
                    "id": "fc_a",
                    "type": "function_call",
                    "call_id": "c_a",
                    "name": "read",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_b",
                "delta": '{"b":1}',
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_a",
                "delta": '{"a":1}',
            },
            {
                "type": "response.output_item.added",
                "item": {
                    "id": "fc_b",
                    "type": "function_call",
                    "call_id": "c_b",
                    "name": "ls",
                },
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ),
    )
    assert [
        (c.provider_correlation_id, c.arguments_json) for c in parsed.tool_calls
    ] == [
        ("c_a|fc_a", '{"a":1}'),
        ("c_b|fc_b", '{"b":1}'),
    ]


def test_codex_reasoning_text_deltas_stream_like_summaries() -> None:
    reasoning: list[str] = []
    _parse_response_events(
        _events(
            {"type": "response.reasoning_text.delta", "delta": "raw"},
            {"type": "response.output_text.delta", "delta": "ok"},
            {"type": "response.completed", "response": {"status": "completed"}},
        ),
        reasoning_sink=reasoning.append,
    )
    assert reasoning == ["raw"]


def test_codex_abort_and_failure_keep_the_partial_content() -> None:
    token = CancelToken()

    def stream() -> Iterator[Mapping[str, Any]]:
        yield {"type": "response.reasoning_summary_text.delta", "delta": "thinking"}
        yield {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "read",
            },
        }
        yield {
            "type": "response.function_call_arguments.delta",
            "output_index": 1,
            "delta": '{"pa',
        }
        token.cancel()
        yield {"type": "response.output_text.delta", "delta": "never"}

    with pytest.raises(ProviderCancelledError) as caught:
        _parse_response_events(stream(), cancel_token=token)
    partial = caught.value.partial
    assert partial is not None
    thinking, call = partial.content_blocks
    assert thinking == ThinkingContent("thinking")
    assert isinstance(call, ProviderToolCall)
    assert (call.provider_correlation_id, call.arguments_json) == (
        "call_1|fc_1",
        '{"pa',
    )

    with pytest.raises(OpenAICodexResponseParseError) as failed:
        _parse_response_events(
            _events(
                {"type": "response.output_text.delta", "delta": "half"},
                {"type": "error", "message": "boom"},
            )
        )
    assert failed.value.content_blocks == (TextContent("half"),)


def test_azure_requests_encrypted_reasoning_and_replays_it() -> None:
    class Client:
        def __init__(self) -> None:
            self.bodies: list[Mapping[str, Any]] = []

        def post_json(self, url: str, **kwargs: Any) -> JsonResponse:
            del url
            self.bodies.append(kwargs["body"])
            return JsonResponse(
                status_code=200,
                body={"status": "completed", "output": [REASONING_ITEM, MESSAGE_ITEM]},
            )

    client = Client()
    provider = AzureOpenAIResponsesProvider(
        model_id="gpt-a",
        endpoint_url="https://r.openai.azure.com",
        api_key="k",
        deployment="gpt-a",
        http_client=client,  # type: ignore[arg-type]
        reasoning_effort="medium",
        reasoning_summary="auto",
        include_encrypted_reasoning=True,
    )
    request = _request(provider="azure-openai", model="gpt-a")
    result = provider.complete(request)
    assert client.bodies[0]["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert client.bodies[0]["include"] == ["reasoning.encrypted_content"]
    answer = assistant_message(result, AgentMessageUsage(), request)
    provider.complete(
        _request(
            (
                AgentUserMessage(ProductContent("go")),
                answer,
                AgentUserMessage(ProductContent("more")),
            ),
            provider="azure-openai",
            model="gpt-a",
        )
    )
    replayed = client.bodies[1]["input"]
    assert REASONING_ITEM in replayed
    assert {"id": "msg_1", "phase": "commentary"}.items() <= next(
        item for item in replayed if item.get("type") == "message"
    ).items()


# -- Google --------------------------------------------------------------------

GOOGLE = ReplayTarget("google", "google-generative-ai", "gemini-3-pro")


def test_gemini_parse_merges_parts_and_keeps_signatures() -> None:
    parsed = parse_gemini(
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "thought": True,
                                "text": "Plan ",
                                "thoughtSignature": "QUJD",
                            },
                            {"thought": True, "text": "more"},
                            {"text": "Answer"},
                            {"text": "", "thoughtSignature": "REVG"},
                            {
                                "functionCall": {
                                    "id": "f1",
                                    "name": "read",
                                    "args": {},
                                },
                                "thoughtSignature": "R0hJ",
                            },
                        ]
                    },
                }
            ]
        },
        parse_error_class=ProviderHTTPError,
        response_label="Google",
        tool_call_provider_prefix="google",
    )
    thinking, text, call = parsed.content_blocks
    assert thinking == ThinkingContent("Plan more", "QUJD")
    assert text == TextContent("Answer", "REVG")
    assert isinstance(call, ProviderToolCall)
    assert call.thought_signature == "R0hJ"
    assert parsed.final_text == "Answer"


def test_gemini_replays_valid_signatures_to_the_same_model_only() -> None:
    call = AgentToolCall("f1", "read", ProductContent("{}"), thought_signature="R0hJ")
    message = _message(
        ThinkingContent("Plan", "QUJD"),
        TextContent("Answer"),
        TextContent("", "REVG"),
        call,
        provider="google",
        api="google-generative-ai",
        model="gemini-3-pro",
    )
    same = envelope_to_content(
        message, parse_error_class=ProviderHTTPError, target=GOOGLE
    )
    assert same["parts"] == [
        {"thought": True, "text": "Plan", "thoughtSignature": "QUJD"},
        {"text": "Answer"},
        {"text": "", "thoughtSignature": "REVG"},
        {
            "functionCall": {"id": "f1", "name": "read", "args": {}},
            "thoughtSignature": "R0hJ",
        },
    ]
    invalid = _message(
        ThinkingContent("Plan", "not base64!"),
        provider="google",
        api="google-generative-ai",
        model="gemini-3-pro",
    )
    assert envelope_to_content(
        invalid, parse_error_class=ProviderHTTPError, target=GOOGLE
    )["parts"] == [{"thought": True, "text": "Plan"}]
    other = ReplayTarget("google", "google-generative-ai", "gemini-2.5-pro")
    assert envelope_to_content(
        message, parse_error_class=ProviderHTTPError, target=other
    )["parts"] == [
        {"text": "Plan"},
        {"text": "Answer"},
        {"functionCall": {"id": "f1", "name": "read", "args": {}}},
    ]


# -- Chat Completions ----------------------------------------------------------


def test_chat_completions_gets_thinking_as_joined_text() -> None:
    wire = envelope_to_chat_message(_message(SIGNED, TextContent("answer"), CALL))
    assert wire["content"] == "I should read the file.answer"


# -- the loop ------------------------------------------------------------------


class _Sink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def _result(**fields: Any) -> ProviderResult:
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="anthropic",
        model_id="claude-x",
        started_at=NOW,
        ended_at=NOW,
        **fields,
    )


def test_assistant_message_uses_adapter_blocks_or_streamed_reasoning() -> None:
    call = ProviderToolCall("c1", "read", "{}", thought_signature="QUJD")
    result = _result(
        final_text="a",
        tool_calls=(call,),
        content_blocks=(SIGNED, TextContent("a"), call),
        api="anthropic-messages",
        provider_thinking_level="high",
    )
    message = assistant_message(result, AgentMessageUsage(), _request())
    assert message.blocks[0] == SIGNED
    assert message.tool_calls[0].thought_signature == "QUJD"
    assert (message.api, message.provider_thinking_level) == (
        "anthropic-messages",
        "high",
    )

    streamed = assistant_message(
        _result(final_text="answer"),
        AgentMessageUsage(),
        _request(),
        (ThinkingContent("streamed"), TextContent("answer")),
    )
    assert streamed.ordered_content() == (
        ThinkingContent("streamed"),
        TextContent("answer"),
    )


def test_partial_recorder_orders_segments_and_restarts_on_retry() -> None:
    sink = _Sink()
    partial = PartialAssistantContent(sink, 0)  # type: ignore[arg-type]
    partial.emit(AssistantReasoningDelta(0, ProductContent("th")))
    partial.emit(AssistantReasoningDelta(0, ProductContent("ink")))
    partial.emit(AssistantTextDelta(0, ProductContent("text")))
    partial.emit(AssistantReasoningDelta(1, ProductContent("other turn")))
    assert partial.blocks() == (ThinkingContent("think"), TextContent("text"))
    partial.emit(
        RetryScheduled(1, 3, 10, AgentFailure("ProviderError", ProductContent("x")))
    )
    assert partial.blocks() == (ThinkingContent("think"), TextContent("text"))
    partial.retry_attempt_started()
    assert partial.blocks() == ()
    assert len(sink.events) == 5


def test_loop_abort_keeps_the_adapter_partial_and_effort() -> None:
    from test_native_agent_loop import _make_loop, _run_input

    from pipy_harness.native.agent import AgentCancellationReason
    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
    from pipy_harness.native.models import ProviderPartial

    partial_call = ProviderToolCall("call_1|fc_1", "read", '{"pa')
    loop, _provider, _events, _usage = _make_loop(
        [],
        [
            ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
                partial=ProviderPartial(
                    content_blocks=(ThinkingContent("partial"), partial_call),
                    provider_thinking_level="high",
                ),
            )
        ],
        emit_delta=True,
    )
    aborted = loop.run(_run_input()).final_history[-1]
    assert isinstance(aborted, AgentAssistantMessage)
    assert aborted.stop_reason is AgentStopReason.ABORTED
    assert aborted.provider_thinking_level == "high"
    assert aborted.ordered_content() == (
        ThinkingContent("partial"),
        AgentToolCall("call_1|fc_1", "read", ProductContent("{}")),
    )


def test_loop_failure_keeps_streamed_thinking_or_the_adapter_partial() -> None:
    from test_native_agent_loop import _make_loop, _provider_result, _run_input

    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome

    class ReasoningTurn:
        def __init__(self, result: ProviderResult) -> None:
            self.result = result

        def complete(
            self, snapshot: object, event_sink: Any, turn_index: int, /
        ) -> ProviderTurnOutcome:
            del snapshot
            event_sink.emit(AssistantReasoningDelta(turn_index, ProductContent("why")))
            event_sink.emit(AssistantTextDelta(turn_index, ProductContent("half")))
            return ProviderTurnOutcome(result=self.result)

    failed_result = _provider_result(status=HarnessStatus.FAILED)
    loop, _provider, _events, _usage = _make_loop([], [])
    loop._provider_turn = ReasoningTurn(failed_result)  # type: ignore[assignment]
    failed = loop.run(_run_input()).final_history[-1]
    assert isinstance(failed, AgentAssistantMessage)
    assert failed.stop_reason is AgentStopReason.ERROR
    assert failed.ordered_content() == (ThinkingContent("why"), TextContent("half"))

    from dataclasses import replace

    with_partial = replace(
        failed_result,
        content_blocks=(TextContent("adapter"),),
        provider_thinking_level="low",
    )
    loop, _provider, _events, _usage = _make_loop([], [])
    loop._provider_turn = ReasoningTurn(with_partial)  # type: ignore[assignment]
    failed = loop.run(_run_input()).final_history[-1]
    assert isinstance(failed, AgentAssistantMessage)
    assert failed.content.value == "adapter"
    assert failed.provider_thinking_level == "low"


def test_loop_rejects_blocks_that_disagree_with_the_result() -> None:
    from dataclasses import replace

    from test_native_agent_loop import _make_loop, _provider_result, _run_input

    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome

    bad = replace(_provider_result("done"), content_blocks=(TextContent("other"),))
    loop, _provider, _events, _usage = _make_loop([], [ProviderTurnOutcome(result=bad)])
    with pytest.raises(ValueError, match="content_blocks must match"):
        loop.run(_run_input())


# -- storage, JSON, budget ---------------------------------------------------------


def test_session_round_trip_keeps_blocks_signatures_and_api(tmp_path: Path) -> None:
    call = AgentToolCall("f1", "read", ProductContent("{}"), thought_signature="R0hJ")
    message = AgentAssistantMessage.from_blocks(
        [
            ThinkingContent(REDACTED_THINKING_TEXT, "opaque", redacted=True),
            TextContent("before", '{"v":1,"id":"msg_1"}'),
            call,
            TextContent(" after"),
        ],
        provider="google",
        api="google-generative-ai",
        model="gemini-3-pro",
    )
    aborted = AgentAssistantMessage.from_blocks(
        [ThinkingContent("partial"), AgentToolCall("c2", "ls", ProductContent('{"pa'))],
        stop_reason=AgentStopReason.ABORTED,
        provider="openai-codex",
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(ProductContent("go")))
    tree.append_message(message)
    tree.append_message(aborted)
    assert tree.path is not None
    raw = [json.loads(line) for line in tree.path.read_text().splitlines()]
    stored = raw[-2]["message"]
    assert stored["api"] == "google-generative-ai"
    assert stored["blocks"][0] == {
        "type": "thinking",
        "thinking": REDACTED_THINKING_TEXT,
        "thinkingSignature": "opaque",
        "redacted": True,
    }
    assert stored["blocks"][2] == {"type": "toolCall", "index": 0}
    assert stored["tool_calls"][0]["thoughtSignature"] == "R0hJ"

    reopened = NativeSessionTree.open(tree.path)
    restored = [
        entry.message
        for entry in reopened.build_context_entries()
        if isinstance(entry, MessageEntry)
    ]
    assert restored[1] == message
    assert restored[1].api == "google-generative-ai"
    assert restored[2] == aborted


def test_json_messages_carry_pi_ordered_content() -> None:
    call = AgentToolCall("f1", "read", ProductContent("{}"), thought_signature="R0hJ")
    message = _message(
        ThinkingContent("r", "sig", redacted=True), TextContent("t", "ts"), call
    )
    body = serialize_message(message)
    assert body["api"] == "anthropic-messages"
    assert body["content"] == [
        {
            "type": "thinking",
            "thinking": "r",
            "thinkingSignature": "sig",
            "redacted": True,
        },
        {"type": "text", "text": "t", "textSignature": "ts"},
        {
            "type": "toolCall",
            "id": "f1",
            "name": "read",
            "arguments": {},
            "thoughtSignature": "R0hJ",
        },
    ]


def test_json_partials_carry_thinking_deltas() -> None:
    emitted: list[dict[str, Any]] = []

    class Sink:
        def emit(self, event: dict[str, Any]) -> None:
            emitted.append(event)

    adapter = AutomationAgentEventAdapter(Sink())  # type: ignore[arg-type]
    adapter.emit(AssistantReasoningDelta(0, ProductContent("hm")))
    adapter.emit(AssistantTextDelta(0, ProductContent("ok")))
    first, second = emitted
    assert first["assistantMessageEvent"]["type"] == "thinking_delta"
    assert second["assistantMessageEvent"]["contentIndex"] == 1
    assert second["message"]["content"] == [
        {"type": "thinking", "thinking": "hm"},
        {"type": "text", "text": "ok"},
    ]


def test_request_estimate_counts_thinking_text() -> None:
    plain = _request(
        (AgentUserMessage(ProductContent("go")), _message(TextContent("a")))
    )
    thinking = _request(
        (
            AgentUserMessage(ProductContent("go")),
            _message(ThinkingContent("x" * 400, "s" * 4000), TextContent("a")),
        )
    )
    base = estimate_request(plain, image_count=0, output_reserve=0).message_tokens
    more = estimate_request(thinking, image_count=0, output_reserve=0).message_tokens
    # The thinking text counts (pipy's bytes/3 unit); the signature does not.
    assert more - base == (400 + 2) // 3


def test_a_non_streamed_answer_draws_thinking_and_text_in_order() -> None:
    from pipy_harness.native.agent.events import MessageCompleted, MessageStarted
    from pipy_harness.native.ui.state import (
        CompleteAssistantMessage,
        RenderBufferedAssistantText,
        RenderBufferedAssistantThinking,
        UiState,
        reduce,
    )

    message = _message(
        ThinkingContent("plan"), TextContent("answer"), ThinkingContent("more")
    )
    state, _ = reduce(
        UiState(), MessageStarted(0, AgentAssistantMessage(ProductContent("")))
    )
    _, decisions = reduce(state, MessageCompleted(0, message))
    assert decisions == (
        RenderBufferedAssistantThinking("plan"),
        RenderBufferedAssistantText("answer", has_tool_calls=False),
        RenderBufferedAssistantThinking("more"),
        CompleteAssistantMessage(has_tool_calls=False),
    )
    # Streamed reasoning is already on screen: only the unstreamed text follows.
    state, _ = reduce(
        UiState(), MessageStarted(0, AgentAssistantMessage(ProductContent("")))
    )
    state, _ = reduce(state, AssistantReasoningDelta(0, ProductContent("plan")))
    _, decisions = reduce(state, MessageCompleted(0, message))
    assert decisions == (
        RenderBufferedAssistantText("answer", has_tool_calls=False),
        CompleteAssistantMessage(has_tool_calls=False),
    )


def test_display_segments_follow_pi_assistant_component() -> None:
    assert display_segments(
        [
            ThinkingContent(" a "),
            ThinkingContent(""),
            ThinkingContent("b"),
            TextContent("text"),
            TextContent("  "),
            CALL,
            ThinkingContent("c"),
        ]
    ) == [(True, "a\n\nb"), (False, "text"), (True, "c")]


def test_tool_results_still_pair_after_a_thinking_message() -> None:
    result = AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="read",
        content=ProductContent("out"),
        provider_correlation_id="call_1|fc_1",
    )
    replay = ResponsesReplay(
        ReplayTarget("openai-codex", "openai-codex-responses", "gpt-a"),
        OPENAI_TOOL_CALL_PROVIDERS,
    )
    assert replay.items(result, 2) == [
        {"type": "function_call_output", "call_id": "call_1", "output": "out"}
    ]
