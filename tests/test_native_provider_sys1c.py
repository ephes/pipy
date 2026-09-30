"""SYS1c: the leading prompt's shape, sessions without a leading system
message, Anthropic mid-conversation effort, and restoring tools on ``/tree``.

Pi ``1b347794e``: ``api/openai-responses-shared.ts`` (the leading prompt is the
first input item), ``api/openai-codex-responses.ts`` (``instructions`` with
the ``"You are a helpful assistant."`` default), ``api/openai-completions.ts``
and ``api/mistral-conversations.ts`` (instruction role, empty prompt sends
nothing), ``utils/transcript.ts`` (``getInitialSystemMessage``),
``api/anthropic-messages.ts`` (``supportsMidConvoEffort``: ``output_config``
system messages, ``block_binding``, betas) and ``core/agent-session.ts``
(``_restoreToolsFromTranscript`` in ``navigateTree``).
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from test_native_provider_system_messages import (
    ADD_GAMMA,
    ANTHROPIC_OK,
    CHAT_OK,
    HISTORY,
    LEAD,
    PROMPT,
    RESPONSES_OK,
    A,
    B,
    C,
    _anthropic,
    _chat_body,
    _Client,
    _request,
    _responses_body,
    _responses_tool,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.messages import AgentSystemMessage
from pipy_harness.native.agent.system_messages import tool_declaration
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS
from pipy_harness.native.coding.request_budget import _system_tokens
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderSystemMessage
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponsesProvider,
    _codex_request_body,
)
from pipy_harness.native.provider_construction import resolve_transcript_compat
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.azure_openai_responses import (
    AzureOpenAIResponsesProvider,
)
from pipy_harness.native.providers.mistral import MistralProvider
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools import ToolDefinition
from pipy_harness.native.tools.read import ReadTool

EFFORT_BETAS = (
    "mid-conversation-output-config-2026-07-01,thinking-binding-controls-2026-08-01"
)


def _effort(level: str) -> dict[str, Any]:
    return {"role": "system", "content": [], "output_config": {"effort": level}}


# ---- leading prompt ---------------------------------------------------------


def test_responses_empty_prompt_sends_no_leading_item_and_legacy_is_a_list() -> None:
    body = _responses_body(_request(lead=None, system_prompt=""))
    assert "instructions" not in body
    assert body["input"][0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "hi"}],
    }

    legacy = _responses_body(
        _request(lead=None, messages=()), instruction_role="system"
    )
    assert legacy["input"] == [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]


def test_azure_sends_the_leading_prompt_as_the_first_input_item() -> None:
    client = _Client(RESPONSES_OK)
    AzureOpenAIResponsesProvider(
        model_id="deploy",
        endpoint_url="https://r.openai.azure.com",
        api_key="k",
        http_client=client,
        instruction_role="system",
    ).complete(_request(lead=None))
    body = client.requests[0]["body"]

    assert "instructions" not in body
    assert body["input"][0] == {"role": "system", "content": PROMPT}


def test_codex_keeps_instructions_with_pi_default_for_an_empty_prompt() -> None:
    provider = OpenAICodexResponsesProvider(model_id="gpt-6.1-sol")

    body = _codex_request_body(provider, _request(lead=None))
    assert body["instructions"] == PROMPT
    assert body["input"][0]["role"] == "user"

    empty = _codex_request_body(provider, _request(lead=None, system_prompt=""))
    assert empty["instructions"] == "You are a helpful assistant."


def test_chat_completions_and_mistral_omit_an_empty_leading_prompt() -> None:
    body = _chat_body(_request(lead=None, system_prompt=""))
    assert [message["role"] for message in body["messages"]] == [
        "user",
        "assistant",
        "tool",
    ]
    system = _chat_body(_request(lead=None))
    assert system["messages"][0] == {"role": "system", "content": PROMPT}

    client = _Client(CHAT_OK)
    MistralProvider(model_id="m", api_key="k", http_client=client).complete(
        _request(lead=None, system_prompt="")
    )
    assert client.requests[0]["body"]["messages"][0]["role"] == "user"


# ---- a session without a leading system message (made before SYS1a) ---------


def test_responses_without_a_leading_message_sends_the_state_later() -> None:
    body = _responses_body(
        _request((3, LEAD), lead=None),
        supports_mid_convo_system_messages=True,
        supports_additional_tools=True,
    )

    assert "tools" not in body
    assert body["input"][0]["role"] == "user"
    assert body["input"][3:] == [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [_responses_tool(A), _responses_tool(B)],
        },
        {
            "role": "developer",
            "content": (
                'Updated system prompt section "preamble":\n\nP\n\n'
                'Updated system prompt section "cwd":\n\n<cwd>\n/w\n</cwd>'
            ),
        },
    ]


def test_codex_without_a_leading_message_uses_the_default_instructions() -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-6.1-sol",
        supports_mid_convo_system_messages=True,
        supports_additional_tools=True,
    )

    body = _codex_request_body(provider, _request((3, LEAD), lead=None))

    assert body["instructions"] == "You are a helpful assistant."
    assert body["input"][-1]["role"] == "developer"
    assert body["input"][-2]["type"] == "additional_tools"


def test_anthropic_without_a_leading_message_sends_no_system_and_current_tools() -> (
    None
):
    sent = _anthropic(
        _request((3, LEAD), lead=None, tools=(A, B)),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_changes=True,
    )
    body = sent["body"]

    assert "system" not in body
    assert "anthropic-beta" not in sent["headers"]
    assert [tool["name"] for tool in body["tools"]] == ["alpha", "beta"]
    assert body["messages"][-1]["role"] == "system"
    assert [block["type"] for block in body["messages"][-1]["content"]] == ["text"]


def test_without_the_flag_a_session_without_a_leading_message_collapses() -> None:
    body = _responses_body(_request((3, LEAD), lead=None))

    assert body["input"][0] == {"role": "developer", "content": PROMPT}
    assert len(body["input"]) == 4


def test_budget_counts_every_message_as_later_without_a_leading_one() -> None:
    leading = _request((3, ADD_GAMMA))
    assert _system_tokens(leading)[1] == 2  # one later message, one kept tool

    legacy = _request((3, LEAD), lead=None)
    tokens, framing = _system_tokens(legacy)
    assert framing == 3  # one later message and its two declarations
    assert tokens > (len(PROMPT.encode()) + 2) // 3


# ---- Anthropic mid-conversation effort -----------------------------------------


def _answer(level: str | None, provider: str = "anthropic") -> AgentAssistantMessage:
    return AgentAssistantMessage(
        ProductContent("answer"),
        provider=provider,
        model="claude-x",
        provider_thinking_level=level,
    )


def test_effort_model_sends_adaptive_thinking_and_trailing_effort() -> None:
    sent = _anthropic(
        _request(lead=None),
        supports_mid_convo_effort=True,
        reasoning_effort="xhigh",
    )
    body = sent["body"]

    assert sent["headers"]["anthropic-beta"] == EFFORT_BETAS
    assert body["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert body["output_config"] == {"effort": "high"}
    # The cache marker lands on the trailing user turn before insertion.
    assert body["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1] == _effort("xhigh")


def test_effort_defaults_to_high_and_maps_minimal() -> None:
    unset = _anthropic(_request(lead=None), supports_mid_convo_effort=True)
    assert unset["body"]["messages"][-1] == _effort("high")
    minimal = _anthropic(
        _request(lead=None),
        supports_mid_convo_effort=True,
        reasoning_effort="minimal",
    )
    assert minimal["body"]["messages"][-1] == _effort("low")


def test_effort_replays_recorded_levels_for_the_same_provider_only() -> None:
    messages = (
        AgentUserMessage(ProductContent("one")),
        _answer("medium"),
        AgentUserMessage(ProductContent("two")),
        _answer("medium", provider="openrouter"),
        AgentUserMessage(ProductContent("three")),
        _answer("bogus"),
        AgentUserMessage(ProductContent("four")),
        _answer(None),
        AgentUserMessage(ProductContent("five")),
    )
    sent = _anthropic(
        _request(lead=None, messages=messages),
        supports_mid_convo_effort=True,
        reasoning_effort="low",
    )

    assert [
        message.get("output_config", {}).get("effort", message["role"])
        for message in sent["body"]["messages"]
    ] == [
        "user",
        "medium",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "low",
    ]


def test_effort_keeps_sys1b_cache_markers_and_held_updates() -> None:
    moved = AgentSystemMessage(sections=(("cwd", "<cwd>\n/x\n</cwd>"),))
    sent = _anthropic(
        _request((3, moved), system_prompt="P\n\n<cwd>\n/x\n</cwd>"),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_effort=True,
    )
    messages = sent["body"]["messages"]
    # The held update ends the conversation and takes the marker; the
    # effort message follows it without one.
    assert messages[-2]["role"] == "system"
    assert messages[-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert messages[-1] == _effort("high")

    trailing_assistant = _anthropic(
        _request(lead=None, messages=(*HISTORY, _answer("high"))),
        supports_mid_convo_effort=True,
    )["body"]["messages"]
    assert trailing_assistant[-3:] == [
        _effort("high"),
        {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        _effort("high"),
    ]


def test_effort_betas_join_tool_changes_and_yield_to_a_configured_header() -> None:
    change = AgentSystemMessage(tools_added=(tool_declaration(C),))
    both = _anthropic(
        _request((3, change)),
        supports_mid_convo_system_messages=True,
        supports_mid_convo_tool_changes=True,
        supports_mid_convo_effort=True,
    )
    assert both["headers"]["anthropic-beta"] == (
        EFFORT_BETAS + ",mid-conversation-tool-changes-2026-07-01"
    )

    configured = _anthropic(
        _request(lead=None),
        supports_mid_convo_effort=True,
        extra_headers={"anthropic-beta": "custom"},
    )
    assert configured["headers"]["anthropic-beta"] == "custom"


def test_without_the_flag_the_body_is_unchanged() -> None:
    sent = _anthropic(
        _request(lead=None, messages=(*HISTORY, _answer("high"))),
        reasoning_effort="high",
        force_adaptive_thinking=True,
    )
    body = sent["body"]

    assert "anthropic-beta" not in sent["headers"]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert all(message["role"] != "system" for message in body["messages"])


def test_effort_result_is_recorded_persisted_and_serialized(tmp_path: Path) -> None:
    client = _Client(ANTHROPIC_OK)
    result = AnthropicProvider(
        model_id="claude-x",
        api_key="k",
        http_client=client,
        supports_mid_convo_effort=True,
        reasoning_effort="max",
    ).complete(_request(lead=None))
    assert result.provider_thinking_level == "max"
    plain = AnthropicProvider(
        model_id="claude-x", api_key="k", http_client=_Client(ANTHROPIC_OK)
    ).complete(_request(lead=None))
    assert plain.provider_thinking_level is None

    message = _answer("max")
    assert serialize_message(message)["providerThinkingLevel"] == "max"
    assert list(serialize_message(message))[:5] == [
        "role",
        "content",
        "provider",
        "model",
        "providerThinkingLevel",
    ]
    tree = NativeSessionTree.create(tmp_path, persist=True)
    tree.append_message(AgentUserMessage(ProductContent("q")))
    tree.append_message(message)
    assert tree.path is not None
    stored = [
        json.loads(line)
        for line in tree.path.read_text().splitlines()
        if '"assistant"' in line
    ]
    assert stored[-1]["message"]["providerThinkingLevel"] == "max"
    reopened = NativeSessionTree.open(tree.path)
    restored = reopened.build_coding_context().messages[-1]
    assert isinstance(restored, AgentAssistantMessage)
    assert restored.provider_thinking_level == "max"


def test_catalog_rows_and_construction_read_the_effort_flag() -> None:
    effort_rows = {
        spec.model_id
        for spec in BUILTIN_MODEL_ROWS
        if (spec.compat or {}).get("supportsMidConvoEffort") is True
    }
    assert effort_rows == {
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-fable-5-1",
    }
    for spec in BUILTIN_MODEL_ROWS:
        if spec.api == "anthropic-messages":
            assert resolve_transcript_compat(spec).supports_mid_convo_effort is (
                spec.model_id in effort_rows and spec.provider_name == "anthropic"
            )


# ---- _restoreToolsFromTranscript --------------------------------------------


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=name, input_schema={"type": "object"})


class _Port:
    def __init__(self, name: str) -> None:
        self.definition = _tool(name)

    def invoke(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("not invoked")


def test_restore_active_tools_drops_unknown_and_filter_hidden_names(
    tmp_path: Path,
) -> None:
    capabilities = NativeToolCapabilities(
        {"read": _Port("read"), "bash": _Port("bash")},  # type: ignore[dict-item]
        {"ext": _Port("ext")},  # type: ignore[dict-item]
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
    )
    capabilities.restore_active_tools(["ext", "gone", "read"])
    assert capabilities.state.active_tool_names == frozenset({"ext", "read"})
    capabilities.restore_active_tools([])
    assert capabilities.state.active_tool_names == frozenset()

    filtered = NativeToolCapabilities(
        {"read": _Port("read"), "bash": _Port("bash")},  # type: ignore[dict-item]
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions(exclude=("bash",)),
        cancel_join_timeout_seconds=1.0,
    )
    filtered.restore_active_tools(["read", "bash"])
    assert filtered.state.active_tool_names == frozenset({"read"})


class _Recording:
    name = "fake"
    supports_tool_calls = True
    model_id = "fake-model"
    supports_mid_convo_system_messages = True

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
        )


def _branch_tree(cwd: Path, *, declared: bool) -> tuple[NativeSessionTree, str]:
    tree = NativeSessionTree.create(cwd, persist=False)
    if declared:
        tree.append_message(
            AgentSystemMessage(tools_added=(tool_declaration(ReadTool().definition),))
        )
    tree.append_message(AgentUserMessage(ProductContent("ROOT")))
    leaf = tree.append_message(AgentAssistantMessage(ProductContent("ANSWER")))
    return tree, leaf.id


def _run(tree: NativeSessionTree, cwd: Path, inputs: str) -> _Recording:
    provider = _Recording()
    CodingSession(provider=provider, native_session=tree).run(
        workspace_root=cwd,
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return provider


def _names(request: ProviderRequest) -> list[str]:
    return [tool.name for tool in request.available_tools]


def test_tree_navigation_restores_the_branch_tool_set(tmp_path: Path) -> None:
    tree, leaf = _branch_tree(tmp_path, declared=True)

    provider = _run(tree, tmp_path, f"/tree select {leaf}\nnext\n/exit\n")

    assert _names(provider.requests[0]) == ["read"]
    turn = provider.requests[0].system_messages[-1].message
    assert turn.tools_added == ()
    assert turn.tools_removed == ()


def test_resume_does_not_restore_and_undeclared_branches_keep_tools(
    tmp_path: Path,
) -> None:
    # Pi's CLI always passes the configured tools, so only /tree restores.
    (tmp_path / "resume").mkdir()
    tree, _leaf = _branch_tree(tmp_path / "resume", declared=True)
    resumed = _run(tree, tmp_path / "resume", "next\n/exit\n")
    assert len(_names(resumed.requests[0])) > 1

    (tmp_path / "bare").mkdir()
    bare, leaf = _branch_tree(tmp_path / "bare", declared=False)
    navigated = _run(bare, tmp_path / "bare", f"/tree select {leaf}\nnext\n/exit\n")
    assert _names(navigated.requests[0]) == _names(resumed.requests[0])


def test_system_anchor_without_leading_position_is_valid_request_state() -> None:
    request = _request((3, LEAD), lead=None)
    assert request.system_messages == (ProviderSystemMessage(3, LEAD),)


def test_branch_summary_navigation_restores_the_branch_tool_set(
    tmp_path: Path,
) -> None:
    tree, target = _branch_tree(tmp_path, declared=True)
    tree.append_message(AgentUserMessage(ProductContent("MORE")))
    tree.append_message(AgentAssistantMessage(ProductContent("MORE ANSWER")))

    provider = _run(tree, tmp_path, f"/tree select {target} summarize\nnext\n/exit\n")

    assert provider.requests[0].available_tools == ()  # the private summary
    assert _names(provider.requests[1]) == ["read"]
