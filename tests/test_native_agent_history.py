"""Contracts for dependency-neutral canonical agent-history compaction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from dataclasses import replace
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.history import (
    AgentHistoryCompaction,
    compact_agent_history,
    should_compact_agent_history,
)


def _tool_call(
    correlation: str,
    *,
    name: str = "read",
    arguments_json: str = '{"path": "README.md"}',
) -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id=correlation,
        tool_name=name,
        arguments_json=ProductContent(arguments_json),
    )


def _tool_result(
    correlation: str,
    *,
    request_suffix: str,
    name: str = "read",
    content: str = "tool output",
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id=f"{AGENT_TOOL_REQUEST_ID_PREFIX}{request_suffix}",
        tool_name=name,
        content=ProductContent(content),
        provider_correlation_id=correlation,
    )


def _plain_group(index: int) -> list[AgentMessage]:
    return [
        AgentUserMessage(ProductContent(f"prompt {index}")),
        AgentAssistantMessage(ProductContent(f"answer {index}")),
    ]


def test_compact_agent_history_reports_exact_counts_and_utf8_bytes() -> None:
    first_call = _tool_call(
        "corr-1",
        name="réad",
        arguments_json='{"path":"é"}',
    )
    second_call = _tool_call(
        "corr-2",
        name="list",
        arguments_json="{}",
    )
    messages: list[AgentMessage] = [
        AgentUserMessage(ProductContent("old 🧪")),
        AgentAssistantMessage(
            ProductContent("α"), tool_calls=(first_call, second_call)
        ),
        _tool_result("corr-1", request_suffix="0001", name="réad", content="résultat"),
        _tool_result("corr-2", request_suffix="0002", name="list", content="entries"),
        AgentAssistantMessage(ProductContent("done")),
        AgentUserMessage(ProductContent("middle")),
        AgentAssistantMessage(ProductContent("reply")),
        AgentUserMessage(ProductContent("recent")),
        AgentAssistantMessage(ProductContent("kept")),
    ]
    expected_before = sum(
        (
            len("old 🧪".encode()),
            len("α".encode()),
            len("réad".encode()),
            len('{"path":"é"}'.encode()),
            len("list".encode()),
            len("{}".encode()),
            len("résultat".encode()),
            len("entries".encode()),
            len("done".encode()),
            len("middle".encode()),
            len("reply".encode()),
            len("recent".encode()),
            len("kept".encode()),
        )
    )
    original_messages = tuple(messages)

    result = compact_agent_history(messages, keep_recent_groups=1)

    assert tuple(messages) == original_messages
    assert result == AgentHistoryCompaction(
        messages=tuple(messages[-2:]),
        changed=True,
        dropped_group_count=2,
        dropped_message_count=7,
        dropped_user_count=2,
        dropped_assistant_count=3,
        dropped_tool_call_count=2,
        dropped_tool_result_count=2,
        retained_group_count=1,
        retained_message_count=2,
        bytes_before=expected_before,
        bytes_after=len("recent".encode()) + len("kept".encode()),
    )


def test_compact_agent_history_returns_frozen_detached_noop() -> None:
    messages = _plain_group(1)

    result = compact_agent_history(messages, keep_recent_groups=2)
    messages.extend(_plain_group(2))

    assert result.messages == tuple(_plain_group(1))
    assert not result.changed
    assert result.dropped_group_count == 0
    assert result.dropped_message_count == 0
    assert result.dropped_user_count == 0
    assert result.dropped_assistant_count == 0
    assert result.dropped_tool_call_count == 0
    assert result.dropped_tool_result_count == 0
    assert result.retained_group_count == 1
    assert result.retained_message_count == 2
    assert result.bytes_before == result.bytes_after
    with pytest.raises(FrozenInstanceError):
        setattr(result, "changed", True)
    with pytest.raises(
        TypeError, match="AgentHistoryCompaction.messages must be a tuple"
    ):
        replace(
            result,
            messages=cast(tuple[AgentMessage, ...], list(result.messages)),
        )


@pytest.mark.parametrize("keep_recent_groups", [0, -1])
def test_compact_agent_history_rejects_invalid_keep_recent_groups(
    keep_recent_groups: int,
) -> None:
    with pytest.raises(ValueError, match="keep_recent_groups must be >= 1"):
        compact_agent_history(_plain_group(1), keep_recent_groups=keep_recent_groups)


def test_should_compact_agent_history_uses_strict_thresholds() -> None:
    messages = [
        AgentUserMessage(ProductContent("a")),
        AgentUserMessage(ProductContent("b")),
        AgentUserMessage(ProductContent("c")),
    ]

    assert not should_compact_agent_history(
        messages, max_messages=3, max_bytes=3, keep_recent_groups=2
    )
    assert should_compact_agent_history(
        messages, max_messages=2, max_bytes=10, keep_recent_groups=2
    )
    assert should_compact_agent_history(
        messages, max_messages=10, max_bytes=2, keep_recent_groups=2
    )
    assert not should_compact_agent_history(
        messages[-2:], max_messages=0, max_bytes=0, keep_recent_groups=2
    )


def test_compact_agent_history_treats_projected_custom_users_as_groups() -> None:
    ordinary = AgentUserMessage(ProductContent("ordinary prompt"))
    projected_custom = AgentUserMessage(ProductContent("custom next-turn context"))
    answer = AgentAssistantMessage(ProductContent("answer"))

    result = compact_agent_history(
        [ordinary, projected_custom, answer], keep_recent_groups=1
    )

    assert result.messages == (projected_custom, answer)
    assert result.dropped_group_count == 1
    assert result.dropped_user_count == 1


def test_compact_agent_history_preserves_malformed_arguments_and_tool_pair() -> None:
    malformed_call = _tool_call("corr-bad", arguments_json="{")
    malformed_result = _tool_result(
        "corr-bad",
        request_suffix="bad1",
        content="malformed arguments: invalid JSON",
    )
    messages = [
        *_plain_group(1),
        *_plain_group(2),
        AgentUserMessage(ProductContent("keep this group")),
        AgentAssistantMessage(ProductContent(""), tool_calls=(malformed_call,)),
        malformed_result,
        AgentAssistantMessage(ProductContent("recovered")),
    ]

    result = compact_agent_history(messages, keep_recent_groups=1)

    assert result.messages == tuple(messages[-4:])
    assert result.messages[1] == AgentAssistantMessage(
        ProductContent(""), tool_calls=(malformed_call,)
    )
    assert result.messages[2] is malformed_result


def test_compact_agent_history_drops_leading_non_user_preamble() -> None:
    preamble = AgentAssistantMessage(ProductContent("leading assistant"))
    messages = [preamble, *_plain_group(1), *_plain_group(2), *_plain_group(3)]

    result = compact_agent_history(messages, keep_recent_groups=2)

    assert result.messages == tuple(messages[3:])
    assert isinstance(result.messages[0], AgentUserMessage)
    assert result.dropped_message_count == 3
    assert result.dropped_assistant_count == 2


def test_agent_history_compaction_has_no_product_summary_projection() -> None:
    result = compact_agent_history(
        [*_plain_group(1), *_plain_group(2)], keep_recent_groups=1
    )

    assert not hasattr(result, "summary_block")
    assert not hasattr(result, "safe_metadata")
