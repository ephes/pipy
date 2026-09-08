"""Contracts for dependency-neutral canonical agent-history compaction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
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
    compact_agent_history_tool_cycles,
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
    is_error: bool = False,
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id=f"{AGENT_TOOL_REQUEST_ID_PREFIX}{request_suffix}",
        tool_name=name,
        content=ProductContent(content),
        provider_correlation_id=correlation,
        is_error=is_error,
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
        removed_messages=tuple(messages[:-2]),
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
    assert result.removed_messages == ()
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
    with pytest.raises(
        ValueError,
        match="changed must agree with nonempty removed_messages",
    ):
        replace(result, changed=True)


def test_intra_group_cut_partitions_exact_objects_and_keeps_newest_batch() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    old_assistant = AgentAssistantMessage(
        ProductContent(""), (_tool_call("old-a"), _tool_call("old-b", name="list"))
    )
    old_a = _tool_result("old-a", request_suffix="old-a")
    old_b = _tool_result("old-b", request_suffix="old-b", name="list")
    new_assistant = AgentAssistantMessage(
        ProductContent(""), (_tool_call("new", arguments_json="{"),)
    )
    new_result = _tool_result(
        "new", request_suffix="new", content="invalid arguments", is_error=True
    )
    messages: list[AgentMessage] = [
        user,
        old_assistant,
        old_a,
        old_b,
        new_assistant,
        new_result,
    ]

    result = compact_agent_history_tool_cycles(messages, accepted_user=user)

    assert result.messages == (user, new_assistant, new_result)
    assert result.removed_messages == (old_assistant, old_a, old_b)
    assert all(
        item is expected
        for item, expected in zip(
            result.messages, (user, new_assistant, new_result), strict=True
        )
    )
    assert result.retained_user_anchor is user
    assert result.retained_suffix_boundary is new_assistant
    assert result.changed and result.dropped_group_count == 0
    assert result.dropped_message_count == 3
    assert result.dropped_user_count == 0
    assert result.dropped_assistant_count == 1
    assert result.dropped_tool_call_count == 2
    assert result.dropped_tool_result_count == 2
    assert result.retained_group_count == 1
    assert result.retained_message_count == 3
    assert all(
        item is expected
        for item, expected in zip(
            result.removed_messages, (old_assistant, old_a, old_b), strict=True
        )
    )


@pytest.mark.parametrize(
    "tail",
    [
        [AgentAssistantMessage(ProductContent("plain"))],
        [AgentAssistantMessage(ProductContent(""), (_tool_call("a"),))],
        [AgentToolResultMessage("pipy-tool-orphan", "read", ProductContent("x"), "a")],
        [
            AgentAssistantMessage(ProductContent(""), (_tool_call("a"),)),
            _tool_result("a", request_suffix="a"),
            AgentUserMessage(ProductContent("later")),
        ],
        [
            AgentAssistantMessage(
                ProductContent(""), (_tool_call("a"), _tool_call("a", name="list"))
            ),
            _tool_result("a", request_suffix="a"),
            _tool_result("a", request_suffix="a2", name="list"),
        ],
        [
            AgentAssistantMessage(ProductContent(""), (_tool_call("a"),)),
            _tool_result("a", request_suffix="a", name="list"),
        ],
        [
            AgentAssistantMessage(ProductContent(""), (_tool_call("a"),)),
            _tool_result("wrong", request_suffix="a"),
        ],
        [
            AgentAssistantMessage(
                ProductContent(""), (_tool_call("a"), _tool_call("b", name="list"))
            ),
            _tool_result("b", request_suffix="b", name="list"),
            _tool_result("a", request_suffix="a"),
        ],
        [
            AgentAssistantMessage(ProductContent(""), (_tool_call("a"),)),
            _tool_result("a", request_suffix="a"),
            _tool_result("a", request_suffix="extra"),
        ],
    ],
    ids=(
        "plain",
        "missing",
        "orphan",
        "intervening-user",
        "duplicate-id",
        "wrong-name",
        "wrong-correlation",
        "reversed-results",
        "extra-result",
    ),
)
def test_intra_group_cut_refuses_unsafe_tail(tail: list[AgentMessage]) -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    old_cycle: list[AgentMessage] = [
        AgentAssistantMessage(ProductContent(""), (_tool_call("old"),)),
        _tool_result("old", request_suffix="old"),
    ]
    newest_cycle: list[AgentMessage] = [
        AgentAssistantMessage(ProductContent(""), (_tool_call("new"),)),
        _tool_result("new", request_suffix="new"),
    ]
    messages = [user, *old_cycle, *tail, *newest_cycle]
    result = compact_agent_history_tool_cycles(messages, accepted_user=user)
    assert not result.changed
    assert result.messages == tuple(messages)
    assert result.removed_messages == ()


def test_intra_group_cut_requires_exact_unique_latest_user_and_two_cycles() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    cycle: list[AgentMessage] = [
        AgentAssistantMessage(ProductContent(""), (_tool_call("a"),)),
        _tool_result("a", request_suffix="a"),
    ]
    assert not compact_agent_history_tool_cycles(
        [user, *cycle], accepted_user=user
    ).changed
    assert not compact_agent_history_tool_cycles(
        [user, user, *cycle, *cycle], accepted_user=user
    ).changed
    equal_user = AgentUserMessage(ProductContent("accepted"))
    assert not compact_agent_history_tool_cycles(
        [user, *cycle, *cycle], accepted_user=equal_user
    ).changed
    with pytest.raises(TypeError, match="exact AgentUserMessage"):
        compact_agent_history_tool_cycles(
            [user], accepted_user=cast(AgentUserMessage, cycle[0])
        )


def test_intra_group_cut_refuses_repeated_newest_suffix_identity() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    old_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("old"),))
    old_result = _tool_result("old", request_suffix="old")
    newest_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("new"),))
    newest_result = _tool_result("new", request_suffix="new")
    messages: list[AgentMessage] = [
        newest_assistant,
        user,
        old_assistant,
        old_result,
        newest_assistant,
        newest_result,
    ]

    result = compact_agent_history_tool_cycles(messages, accepted_user=user)

    assert not result.changed
    assert result.messages == tuple(messages)
    assert result.removed_messages == ()


def test_intra_group_cut_refuses_identity_shared_by_prefix_and_removed_cycle() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    shared_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("old"),))
    old_result = _tool_result("old", request_suffix="old")
    newest_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("new"),))
    newest_result = _tool_result("new", request_suffix="new")
    messages: list[AgentMessage] = [
        shared_assistant,
        user,
        shared_assistant,
        old_result,
        newest_assistant,
        newest_result,
    ]

    result = compact_agent_history_tool_cycles(messages, accepted_user=user)

    assert not result.changed
    assert result.messages == tuple(messages)
    assert result.removed_messages == ()


def test_intra_group_cut_refuses_identity_shared_by_old_and_newest_cycles() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    shared_assistant = AgentAssistantMessage(
        ProductContent(""), (_tool_call("shared"),)
    )
    old_result = _tool_result("shared", request_suffix="old")
    newest_result = _tool_result("shared", request_suffix="new")
    messages: list[AgentMessage] = [
        user,
        shared_assistant,
        old_result,
        shared_assistant,
        newest_result,
    ]

    result = compact_agent_history_tool_cycles(messages, accepted_user=user)

    assert not result.changed
    assert result.messages == tuple(messages)
    assert result.removed_messages == ()


def test_compaction_value_rejects_inconsistent_exact_cut_invariants() -> None:
    user = AgentUserMessage(ProductContent("accepted"))
    old_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("old"),))
    old_result = _tool_result("old", request_suffix="old")
    newest_assistant = AgentAssistantMessage(ProductContent(""), (_tool_call("new"),))
    newest_result = _tool_result("new", request_suffix="new")
    cut = compact_agent_history_tool_cycles(
        [user, old_assistant, old_result, newest_assistant, newest_result],
        accepted_user=user,
    )
    assert cut.changed

    invalid_replacements = (
        {"dropped_message_count": cut.dropped_message_count + 1},
        {"retained_message_count": cut.retained_message_count + 1},
        {"dropped_assistant_count": cut.dropped_assistant_count + 1},
        {"dropped_group_count": cut.dropped_group_count + 1},
        {"bytes_before": cut.bytes_before + 1},
        {"retained_suffix_boundary": None},
        {"retained_suffix_boundary": user},
    )
    for changes in invalid_replacements:
        with pytest.raises(ValueError):
            replace(cut, **changes)
    with pytest.raises(TypeError, match="removed_messages must be a tuple"):
        replace(
            cut,
            removed_messages=cast(tuple[AgentMessage, ...], list(cut.removed_messages)),
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


@pytest.mark.parametrize(
    ("messages", "max_messages", "max_bytes", "keep_recent_groups"),
    [
        ([*_plain_group(1), *_plain_group(2), *_plain_group(3)], 5, 10_000, 2),
        ([*_plain_group(1), *_plain_group(2)], 100, 1, 1),
        (
            [
                AgentAssistantMessage(ProductContent("preamble")),
                *_plain_group(1),
                *_plain_group(2),
            ],
            4,
            10_000,
            1,
        ),
        (
            [
                AgentUserMessage(ProductContent("old")),
                AgentAssistantMessage(
                    ProductContent(""), tool_calls=(_tool_call("corr-old"),)
                ),
                _tool_result("corr-old", request_suffix="old1"),
                *_plain_group(2),
            ],
            4,
            10_000,
            1,
        ),
    ],
    ids=("message-threshold", "byte-threshold", "preamble", "tool-exchange"),
)
def test_threshold_triggered_changed_compaction_always_drops_a_group(
    messages: list[AgentMessage],
    max_messages: int,
    max_bytes: int,
    keep_recent_groups: int,
) -> None:
    assert should_compact_agent_history(
        messages,
        max_messages=max_messages,
        max_bytes=max_bytes,
        keep_recent_groups=keep_recent_groups,
    )

    result = compact_agent_history(
        messages,
        keep_recent_groups=keep_recent_groups,
    )

    assert result.changed
    assert result.dropped_group_count >= 1
    assert result.dropped_message_count >= result.dropped_group_count


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
