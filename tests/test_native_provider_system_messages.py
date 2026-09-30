"""Transcript system messages on the wire, per adapter family (SYS1b).

Pins the request shapes Pi ``1b347794e`` sends for later system messages:
``utils/transcript.ts`` (``resolveTranscript``/``collapseSystemMessages``,
``resolveTranscriptTools``), ``utils/text.ts`` (``renderSystemMessageUpdate``),
``api/openai-responses-shared.ts`` (developer messages, ``additional_tools``,
client ``tool_search``), ``api/openai-codex-responses.ts``,
``api/openai-completions.ts`` (instruction role, kimi tools message),
``api/mistral-conversations.ts`` and ``api/anthropic-messages.ts`` (held
``system`` messages, ``tool_addition``/``tool_removal``, the deferred
placeholder and the tool-changes beta). Google and Bedrock always collapse.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.messages import AgentSystemMessage
from pipy_harness.native.agent.system_messages import (
    render_system_message_update,
    tool_declaration,
)
from pipy_harness.native.deferred_tools import short_hash
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest, ProviderSystemMessage
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponsesProvider,
    _codex_request_body,
)
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.azure_openai_responses import (
    AzureOpenAIResponsesProvider,
)
from pipy_harness.native.providers.bedrock import _build_bedrock_request_body
from pipy_harness.native.providers.mistral import MistralProvider
from pipy_harness.native.providers.openai_completions import (
    OpenAIChatCompletionsProvider,
)
from pipy_harness.native.providers.openai_responses import OpenAIResponsesProvider
from pipy_harness.native.providers.transcript import (
    has_non_additive_tool_changes,
    has_tool_redefinitions,
    resolve_request_transcript,
)
from pipy_harness.native.tools import ToolDefinition

SCHEMA = {"type": "object", "properties": {}}


def _tool(name: str, description: str | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=description or f"{name} tool", input_schema=SCHEMA
    )


A, B, C = _tool("alpha"), _tool("beta"), _tool("gamma")
PROMPT = "P\n\n<cwd>\n/w\n</cwd>"
LEAD = AgentSystemMessage(
    sections=(("preamble", "P"), ("cwd", "<cwd>\n/w\n</cwd>")),
    tools_added=(tool_declaration(A), tool_declaration(B)),
)
ADD_GAMMA = AgentSystemMessage(tools_added=(tool_declaration(C),))
USER = AgentUserMessage(ProductContent("hi"))
CALL = AgentAssistantMessage(
    ProductContent(""),
    tool_calls=(AgentToolCall("call_1", "alpha", ProductContent("{}")),),
)
RESULT = AgentToolResultMessage(
    tool_request_id="pipy-tool-1",
    tool_name="alpha",
    content=ProductContent("done"),
    provider_correlation_id="call_1",
)
HISTORY: tuple[AgentMessage, ...] = (USER, CALL, RESULT)


def _request(
    *later: tuple[int, AgentSystemMessage],
    system_prompt: str = PROMPT,
    messages: tuple[AgentMessage, ...] = HISTORY,
    tools: tuple[ToolDefinition, ...] = (A, B, C),
    lead: AgentSystemMessage | None = LEAD,
    hidden: tuple[str, ...] = (),
) -> ProviderRequest:
    anchored = (() if lead is None else (ProviderSystemMessage(0, lead),)) + tuple(
        ProviderSystemMessage(position, message) for position, message in later
    )
    return ProviderRequest(
        system_prompt=system_prompt,
        user_prompt="hi",
        provider_name="test",
        model_id="test-model",
        cwd=Path("."),
        messages=messages,
        available_tools=tools,
        system_messages=anchored,
        hidden_tool_names=hidden,
    )


class _Client:
    def __init__(self, body: Mapping[str, Any]) -> None:
        self.body = body
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
        self.requests.append({"url": url, "headers": dict(headers), "body": body})
        return JsonResponse(status_code=200, body=dict(self.body))


RESPONSES_OK = {
    "status": "completed",
    "output_text": "ok",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
CHAT_OK = {
    "object": "chat.completion",
    "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
}
ANTHROPIC_OK = {
    "type": "message",
    "role": "assistant",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "ok"}],
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def _responses_body(request: ProviderRequest, **fields: Any) -> dict[str, Any]:
    client = _Client(RESPONSES_OK)
    OpenAIResponsesProvider(
        model_id="gpt-x", api_key="k", http_client=client, **fields
    ).complete(request)
    return dict(client.requests[0]["body"])


def _chat_body(request: ProviderRequest, **fields: Any) -> dict[str, Any]:
    client = _Client(CHAT_OK)
    OpenAIChatCompletionsProvider(
        model_id="gpt-x", api_key="k", http_client=client, **fields
    ).complete(request)
    return dict(client.requests[0]["body"])


def _anthropic(request: ProviderRequest, **fields: Any) -> dict[str, Any]:
    client = _Client(ANTHROPIC_OK)
    AnthropicProvider(
        model_id="claude-x", api_key="k", http_client=client, **fields
    ).complete(request)
    return client.requests[0]


def _responses_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": SCHEMA,
    }


# ---- resolver --------------------------------------------------------------


def test_resolver_keeps_later_messages_in_place_only_when_accepted() -> None:
    request = _request((3, ADD_GAMMA))

    collapsed = resolve_request_transcript(request, supports_mid_convo=False)
    assert collapsed.mid_convo is False
    assert collapsed.leading_text == PROMPT
    assert collapsed.items == HISTORY

    kept = resolve_request_transcript(request, supports_mid_convo=True)
    assert kept.mid_convo is True
    assert kept.leading_text == PROMPT
    assert kept.items == (*HISTORY, ADD_GAMMA)
    assert kept.initial_tools == LEAD.tools_added


def test_resolver_leading_text_is_the_first_message_plus_the_out_of_band_tail() -> None:
    moved = AgentSystemMessage(sections=(("cwd", "<cwd>\n/x\n</cwd>"),))
    summary = "\n\nsummary"
    request = _request((1, moved), system_prompt="P\n\n<cwd>\n/x\n</cwd>" + summary)

    resolved = resolve_request_transcript(request, supports_mid_convo=True)

    assert resolved.leading_text == PROMPT + summary
    assert resolved.items == (USER, moved, CALL, RESULT)


def test_resolver_collapses_a_forced_prompt_or_a_missing_leading_message() -> None:
    forced = _request((3, ADD_GAMMA), system_prompt="forced prompt")
    assert (
        resolve_request_transcript(forced, supports_mid_convo=True).mid_convo is False
    )

    legacy = _request((3, LEAD), lead=None)
    assert (
        resolve_request_transcript(legacy, supports_mid_convo=True).mid_convo is False
    )

    bare = _request(lead=None)
    assert resolve_request_transcript(bare, supports_mid_convo=True).mid_convo is False


def test_hidden_declarations_are_filtered_but_ordinary_removals_stay() -> None:
    remove_beta = AgentSystemMessage(tools_removed=("beta",))
    request = _request((3, remove_beta), (3, ADD_GAMMA), tools=(A,), hidden=("gamma",))

    resolved = resolve_request_transcript(request, supports_mid_convo=True)

    assert resolved.system_messages[1].tools_removed == ("beta",)
    assert resolved.system_messages[2].tools_added == ()
    assert has_non_additive_tool_changes(resolved.system_messages) is True


def test_render_system_message_update_frames_section_changes_by_name() -> None:
    message = AgentSystemMessage(
        content=ProductContent("note"),
        sections=(("cwd", "<cwd>\n/x\n</cwd>"), ("skills", None)),
        tools_added=(tool_declaration(C),),
    )

    assert render_system_message_update(message) == (
        'note\n\nUpdated system prompt section "cwd":\n\n<cwd>\n/x\n</cwd>\n\n'
        'Removed system prompt section "skills".'
    )
    assert render_system_message_update(ADD_GAMMA) == ""


def test_tool_change_predicates_distinguish_redefinition_from_removal() -> None:
    redefined = AgentSystemMessage(
        tools_added=(tool_declaration(_tool("alpha", "v2")),)
    )
    readded = AgentSystemMessage(tools_added=(tool_declaration(A),))
    removal = AgentSystemMessage(tools_removed=("alpha",))

    assert has_tool_redefinitions((LEAD, redefined)) is True
    assert has_tool_redefinitions((LEAD, removal, readded)) is False
    assert has_non_additive_tool_changes((LEAD, removal, readded)) is True
    assert has_non_additive_tool_changes((LEAD, ADD_GAMMA)) is False


# ---- OpenAI Responses / Azure / Codex ----------------------------------------


def test_responses_flags_off_send_the_collapsed_request() -> None:
    body = _responses_body(_request((3, ADD_GAMMA)))

    assert body["instructions"] == PROMPT
    assert [item.get("type", item.get("role")) for item in body["input"]] == [
        "user",
        "function_call",
        "function_call_output",
    ]
    assert body["tools"] == [_responses_tool(tool) for tool in (A, B, C)]


def test_responses_anchor_additional_tools_and_send_developer_updates() -> None:
    moved = AgentSystemMessage(sections=(("cwd", "<cwd>\n/x\n</cwd>"),))
    request = _request(
        (3, ADD_GAMMA), (3, moved), system_prompt="P\n\n<cwd>\n/x\n</cwd>"
    )

    body = _responses_body(
        request,
        supports_mid_convo_system_messages=True,
        supports_additional_tools=True,
        supports_tool_search=True,
    )

    assert body["instructions"] == PROMPT
    assert body["tools"] == [_responses_tool(A), _responses_tool(B)]
    assert body["input"][3:] == [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [_responses_tool(C)],
        },
        {
            "role": "developer",
            "content": 'Updated system prompt section "cwd":\n\n<cwd>\n/x\n</cwd>',
        },
    ]


def test_responses_instruction_role_follows_supports_developer_role() -> None:
    moved = AgentSystemMessage(sections=(("cwd", "<cwd>\n/x\n</cwd>"),))
    request = _request((3, moved), system_prompt="P\n\n<cwd>\n/x\n</cwd>")

    body = _responses_body(
        request, supports_mid_convo_system_messages=True, instruction_role="system"
    )

    assert body["input"][-1]["role"] == "system"


def test_responses_removal_sends_current_tools_and_no_anchored_loads() -> None:
    change = AgentSystemMessage(
        tools_added=(tool_declaration(C),), tools_removed=("beta",)
    )
    body = _responses_body(
        _request((3, change), tools=(A, C)),
        supports_mid_convo_system_messages=True,
        supports_additional_tools=True,
    )

    assert body["tools"] == [_responses_tool(A), _responses_tool(C)]
    assert len(body["input"]) == 3


def test_azure_reads_the_same_responses_flags() -> None:
    client = _Client(RESPONSES_OK)
    AzureOpenAIResponsesProvider(
        model_id="deploy",
        endpoint_url="https://r.openai.azure.com",
        api_key="k",
        http_client=client,
        supports_mid_convo_system_messages=True,
        supports_additional_tools=True,
    ).complete(_request((3, ADD_GAMMA)))
    body = client.requests[0]["body"]

    assert body["tools"] == [_responses_tool(A), _responses_tool(B)]
    assert body["input"][-1]["type"] == "additional_tools"


def test_codex_loads_later_tools_with_a_pi_tool_search_pair() -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.5",
        supports_tool_search=True,
        supports_mid_convo_system_messages=True,
    )

    body = _codex_request_body(provider, _request((3, ADD_GAMMA)))

    call_id = "pi_tool_load_" + short_hash("system:3:gamma")
    assert body["instructions"] == PROMPT
    assert [tool["name"] for tool in body["tools"]] == ["alpha", "beta"]
    assert body["input"][-2:] == [
        {
            "type": "tool_search_call",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "arguments": {"query": "gamma", "limit": 1},
        },
        {
            "type": "tool_search_output",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "tools": [{**_responses_tool(C), "strict": False, "defer_loading": True}],
        },
    ]


def test_codex_tool_search_seed_skips_an_assistant_turn_without_items() -> None:
    empty = AgentAssistantMessage(ProductContent(""))
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.5",
        supports_tool_search=True,
        supports_mid_convo_system_messages=True,
    )
    request = _request((4, ADD_GAMMA), messages=(*HISTORY, empty))

    body = _codex_request_body(provider, request)

    assert body["input"][-2]["call_id"] == "pi_tool_load_" + short_hash(
        "system:3:gamma"
    )


def test_codex_prefers_additional_tools_and_flags_off_collapse() -> None:
    request = _request((3, ADD_GAMMA))
    anchored = _codex_request_body(
        OpenAICodexResponsesProvider(
            model_id="gpt-6.1-sol",
            supports_tool_search=True,
            supports_mid_convo_system_messages=True,
            supports_additional_tools=True,
        ),
        request,
    )
    assert anchored["input"][-1]["type"] == "additional_tools"

    collapsed = _codex_request_body(
        OpenAICodexResponsesProvider(model_id="gpt-6.1-sol", supports_tool_search=True),
        request,
    )
    assert collapsed["instructions"] == PROMPT
    assert [tool["name"] for tool in collapsed["tools"]] == ["alpha", "beta", "gamma"]
    assert all(
        "type" not in item or item["type"].startswith("function")
        for item in collapsed["input"]
    )


# ---- Chat Completions / Mistral ----------------------------------------------


def test_chat_completions_later_messages_and_kimi_tools_message() -> None:
    moved = AgentSystemMessage(
        sections=(("cwd", "<cwd>\n/x\n</cwd>"),), tools_added=(tool_declaration(C),)
    )
    request = _request((3, moved), system_prompt="P\n\n<cwd>\n/x\n</cwd>")

    body = _chat_body(
        request,
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_additions=True,
        instruction_role="developer",
    )

    assert body["messages"][0] == {"role": "system", "content": PROMPT}
    assert body["messages"][-2:] == [
        {
            "role": "system",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "gamma",
                        "description": "gamma tool",
                        "parameters": SCHEMA,
                    },
                }
            ],
        },
        {
            "role": "developer",
            "content": 'Updated system prompt section "cwd":\n\n<cwd>\n/x\n</cwd>',
        },
    ]
    assert [tool["function"]["name"] for tool in body["tools"]] == ["alpha", "beta"]


def test_chat_completions_without_tool_additions_sends_current_tools() -> None:
    body = _chat_body(
        _request((3, ADD_GAMMA)),
        supports_mid_convo_system_messages=True,
        instruction_role="developer",
    )

    assert [message["role"] for message in body["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert [tool["function"]["name"] for tool in body["tools"]] == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_mistral_sends_later_updates_as_system_messages() -> None:
    moved = AgentSystemMessage(sections=(("skills", None),))
    client = _Client(CHAT_OK)
    MistralProvider(
        model_id="mistral-x",
        api_key="k",
        http_client=client,
        supports_mid_convo_system_messages=True,
    ).complete(_request((3, moved), system_prompt=PROMPT))
    messages = client.requests[0]["body"]["messages"]

    assert messages[-1] == {
        "role": "system",
        "content": 'Removed system prompt section "skills".',
    }


# ---- Anthropic -----------------------------------------------------------------


def test_anthropic_native_tool_changes_keep_the_tool_list_growing() -> None:
    change = AgentSystemMessage(
        tools_added=(tool_declaration(C),), tools_removed=("beta",)
    )
    sent = _anthropic(
        _request((3, change), tools=(A, C)),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_changes=True,
    )
    body = sent["body"]

    assert sent["headers"]["anthropic-beta"] == (
        "mid-conversation-tool-changes-2026-07-01"
    )
    assert body["system"] == [
        {"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert [tool["name"] for tool in body["tools"]] == [
        "alpha",
        "beta",
        "__pi_deferred_placeholder__",
        "gamma",
    ]
    assert body["tools"][1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][2] == {
        "name": "__pi_deferred_placeholder__",
        "description": "Reserved placeholder. Never available. Never call this.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "defer_loading": True,
    }
    assert body["tools"][3]["defer_loading"] is True
    assert body["messages"][-1] == {
        "role": "system",
        "content": [
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "beta"},
            },
            {
                "type": "tool_addition",
                "tool": {"type": "tool_reference", "name": "gamma"},
                "cache_control": {"type": "ephemeral"},
            },
        ],
    }


def test_anthropic_holds_a_system_message_until_the_next_assistant_turn() -> None:
    moved = AgentSystemMessage(sections=(("cwd", "<cwd>\n/x\n</cwd>"),))
    answer = AgentAssistantMessage(ProductContent("answer"))
    request = _request(
        (2, moved),
        system_prompt="P\n\n<cwd>\n/x\n</cwd>",
        messages=(*HISTORY, answer),
    )

    body = _anthropic(request, supports_mid_convo_system_messages=True)["body"]

    assert [message["role"] for message in body["messages"]] == [
        "user",
        "assistant",
        "user",
        "system",
        "assistant",
    ]
    assert body["messages"][3] == {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": 'Updated system prompt section "cwd":\n\n<cwd>\n/x\n</cwd>',
            }
        ],
    }


def test_anthropic_without_tool_changes_sends_text_only_and_current_tools() -> None:
    sent = _anthropic(_request((3, ADD_GAMMA)), supports_mid_convo_system_messages=True)

    assert "anthropic-beta" not in sent["headers"]
    assert [tool["name"] for tool in sent["body"]["tools"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert sent["body"]["messages"][-1]["role"] == "user"


def test_anthropic_redefinition_falls_back_to_current_tools() -> None:
    redefined = AgentSystemMessage(
        tools_added=(tool_declaration(_tool("alpha", "v2")),), tools_removed=("alpha",)
    )
    sent = _anthropic(
        _request((3, redefined), tools=(_tool("alpha", "v2"), B)),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_changes=True,
    )

    assert "anthropic-beta" not in sent["headers"]
    assert [tool["name"] for tool in sent["body"]["tools"]] == ["alpha", "beta"]
    assert sent["body"]["messages"][-1]["role"] == "user"


def test_anthropic_configured_beta_header_replaces_the_computed_one() -> None:
    change = AgentSystemMessage(tools_added=(tool_declaration(C),))
    sent = _anthropic(
        _request((3, change)),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_changes=True,
        extra_headers={"Anthropic-Beta": "custom-beta"},
    )

    assert sent["headers"]["Anthropic-Beta"] == "custom-beta"
    assert "anthropic-beta" not in sent["headers"]


def test_anthropic_flags_off_and_bedrock_collapse() -> None:
    request = _request((3, ADD_GAMMA))
    sent = _anthropic(request)
    assert sent["body"]["system"][0]["text"] == PROMPT
    assert [tool["name"] for tool in sent["body"]["tools"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert all(message["role"] != "system" for message in sent["body"]["messages"])

    # Pi's Bedrock adapter always collapses (bedrock-converse-stream.ts:132).
    bedrock = _build_bedrock_request_body(
        request,
        anthropic_version="bedrock-2023-05-31",
        max_tokens=1024,
        model_id="anthropic.claude-x",
        region=None,
        reasoning_effort=None,
    )
    assert bedrock["system"][0]["text"] == PROMPT
    assert [tool["name"] for tool in bedrock["tools"]] == ["alpha", "beta", "gamma"]
    assert all(message["role"] != "system" for message in bedrock["messages"])
