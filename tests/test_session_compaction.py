"""Tests for live in-session compaction of native provider context."""

from __future__ import annotations

import json

from pipy_harness.native.conversation import (
    NativeNoToolReplConversationContext,
)
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_compaction import (
    DEFAULT_KEEP_RECENT_GROUPS,
    NoToolCompactionResult,
    ToolLoopCompactionResult,
    compact_no_tool_context,
    compact_tool_loop_messages,
    should_compact_no_tool_context,
    should_compact_tool_loop_messages,
)
from pipy_harness.native.tools.base import SUPPORTED_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


def _tool_call(correlation: str, *, name: str = "read") -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation,
        tool_name=name,
        arguments_json='{"path": "README.md"}',
    )


def _tool_result(correlation: str, *, request_suffix: str = "0001") -> ToolResultMessage:
    return ToolResultMessage(
        tool_request_id=f"{SUPPORTED_TOOL_REQUEST_ID_PREFIX}{request_suffix}",
        output_text="RAW_TOOL_OUTPUT_BODY_THAT_IS_SENSITIVE",
        provider_correlation_id=correlation,
    )


def _group(index: int) -> list:
    """One complete user-turn group: user -> assistant(tool) -> tool -> assistant."""

    correlation = f"corr-{index}"
    return [
        UserMessage(content=f"user prompt {index}"),
        AssistantMessage(content="", tool_calls=(_tool_call(correlation),)),
        _tool_result(correlation, request_suffix=f"{index:04d}"),
        AssistantMessage(content=f"final answer {index}"),
    ]


def _conversation(group_count: int) -> list:
    messages: list = []
    for index in range(group_count):
        messages.extend(_group(index))
    return messages


def test_compact_tool_loop_drops_old_groups_keeps_recent() -> None:
    messages = _conversation(5)

    result = compact_tool_loop_messages(messages, keep_recent_groups=2)

    assert isinstance(result, ToolLoopCompactionResult)
    assert result.changed
    # Only the last two user-turn groups remain.
    user_messages = [m for m in result.messages if isinstance(m, UserMessage)]
    assert [m.content for m in user_messages] == ["user prompt 3", "user prompt 4"]
    assert result.dropped_group_count == 3
    assert result.retained_group_count == 2


def test_compact_tool_loop_retained_history_starts_with_user_message() -> None:
    messages = _conversation(4)

    result = compact_tool_loop_messages(messages, keep_recent_groups=1)

    assert result.messages
    assert isinstance(result.messages[0], UserMessage)


def test_compact_tool_loop_never_orphans_tool_results() -> None:
    messages = _conversation(4)

    result = compact_tool_loop_messages(messages, keep_recent_groups=2)

    # Every ToolResultMessage in the retained history must be preceded by an
    # AssistantMessage whose tool_calls include its provider_correlation_id.
    seen_correlations: set[str] = set()
    for message in result.messages:
        if isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                seen_correlations.add(call.provider_correlation_id)
        if isinstance(message, ToolResultMessage):
            assert message.provider_correlation_id in seen_correlations


def test_compact_tool_loop_noop_when_few_groups() -> None:
    messages = _conversation(2)

    result = compact_tool_loop_messages(messages, keep_recent_groups=2)

    assert not result.changed
    assert tuple(result.messages) == tuple(messages)


def test_compact_tool_loop_summary_is_metadata_only() -> None:
    messages = _conversation(5)

    result = compact_tool_loop_messages(messages, keep_recent_groups=2)

    # The summary injected into provider context must never carry raw tool
    # output, user text, or model text from the dropped groups.
    block = result.summary_block
    assert "RAW_TOOL_OUTPUT_BODY_THAT_IS_SENSITIVE" not in block
    assert "user prompt 0" not in block
    assert "final answer 0" not in block
    assert "README.md" not in block
    # It does carry safe counts.
    assert "3" in block  # dropped group count appears somewhere


def test_compact_tool_loop_safe_metadata_has_no_bodies() -> None:
    messages = _conversation(5)

    result = compact_tool_loop_messages(messages, keep_recent_groups=2)

    serialized = json.dumps(result.safe_metadata(), sort_keys=True)
    for forbidden in (
        "RAW_TOOL_OUTPUT_BODY_THAT_IS_SENSITIVE",
        "user prompt",
        "final answer",
        "README.md",
    ):
        assert forbidden not in serialized
    assert result.safe_metadata()["compaction_dropped_group_count"] == 3


def test_should_compact_tool_loop_by_message_count() -> None:
    messages = _conversation(6)  # 24 messages

    assert should_compact_tool_loop_messages(
        messages, max_messages=10, max_bytes=10**9, keep_recent_groups=2
    )
    assert not should_compact_tool_loop_messages(
        messages, max_messages=10**6, max_bytes=10**9, keep_recent_groups=2
    )


def test_should_compact_tool_loop_requires_enough_groups() -> None:
    messages = _conversation(2)

    # Even over the message threshold, refuse when there is nothing to drop
    # beyond the recent groups we must keep.
    assert not should_compact_tool_loop_messages(
        messages, max_messages=1, max_bytes=1, keep_recent_groups=2
    )


def test_compact_no_tool_context_drops_old_exchanges() -> None:
    context = NativeNoToolReplConversationContext.empty(max_exchanges=8)
    for index in range(5):
        context = context.append_successful_exchange(
            user_prompt=f"prompt {index}",
            provider_final_text=f"answer {index}",
        )

    result = compact_no_tool_context(context, keep_recent=2)

    assert isinstance(result, NoToolCompactionResult)
    assert result.changed
    retained = result.context.exchanges
    assert [e.user_prompt for e in retained] == ["prompt 3", "prompt 4"]
    assert result.dropped_exchange_count == 3


def test_compact_no_tool_context_summary_metadata_only() -> None:
    context = NativeNoToolReplConversationContext.empty(max_exchanges=8)
    for index in range(4):
        context = context.append_successful_exchange(
            user_prompt=f"SENSITIVE_PROMPT_{index}",
            provider_final_text=f"SENSITIVE_ANSWER_{index}",
        )

    result = compact_no_tool_context(context, keep_recent=1)

    assert "SENSITIVE_PROMPT_0" not in result.summary_block
    assert "SENSITIVE_ANSWER_0" not in result.summary_block
    serialized = json.dumps(result.safe_metadata(), sort_keys=True)
    assert "SENSITIVE_PROMPT" not in serialized
    assert "SENSITIVE_ANSWER" not in serialized


def test_compact_no_tool_context_noop_when_few_exchanges() -> None:
    context = NativeNoToolReplConversationContext.empty(max_exchanges=8)
    context = context.append_successful_exchange(
        user_prompt="only", provider_final_text="answer"
    )

    result = compact_no_tool_context(context, keep_recent=2)

    assert not result.changed
    assert result.context.exchanges == context.exchanges


def test_should_compact_no_tool_context_threshold() -> None:
    context = NativeNoToolReplConversationContext.empty(max_exchanges=8)
    for index in range(4):
        context = context.append_successful_exchange(
            user_prompt=f"prompt {index}",
            provider_final_text=f"answer {index}",
        )

    assert should_compact_no_tool_context(
        context, max_exchanges=2, max_bytes=10**9, keep_recent=1
    )
    assert not should_compact_no_tool_context(
        context, max_exchanges=10, max_bytes=10**9, keep_recent=1
    )


def test_default_keep_recent_groups_is_reasonable() -> None:
    assert DEFAULT_KEEP_RECENT_GROUPS >= 1
