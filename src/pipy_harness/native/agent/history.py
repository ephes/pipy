"""Mechanical compaction of canonical agent message history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)


@dataclass(frozen=True, slots=True)
class AgentHistoryCompaction:
    """Mechanical result of compacting canonical agent messages."""

    messages: tuple[AgentMessage, ...]
    removed_messages: tuple[AgentMessage, ...]
    changed: bool
    dropped_group_count: int
    dropped_message_count: int
    dropped_user_count: int
    dropped_assistant_count: int
    dropped_tool_call_count: int
    dropped_tool_result_count: int
    retained_group_count: int
    retained_message_count: int
    bytes_before: int
    bytes_after: int
    retained_user_anchor: AgentUserMessage | None = None
    retained_suffix_boundary: AgentMessage | None = None

    def __post_init__(self) -> None:
        _validate_compaction_tuples(self)
        _validate_compaction_counts(self)
        _validate_compaction_anchor(self)


def _validate_compaction_tuples(compaction: AgentHistoryCompaction) -> None:
    if not isinstance(compaction.messages, tuple):
        raise TypeError("AgentHistoryCompaction.messages must be a tuple")
    if not isinstance(compaction.removed_messages, tuple):
        raise TypeError("AgentHistoryCompaction.removed_messages must be a tuple")
    if compaction.changed != bool(compaction.removed_messages):
        raise ValueError("changed must agree with nonempty removed_messages")
    if compaction.dropped_message_count != len(compaction.removed_messages):
        raise ValueError("dropped_message_count must match removed_messages")
    if compaction.retained_message_count != len(compaction.messages):
        raise ValueError("retained_message_count must match messages")


def _validate_compaction_counts(compaction: AgentHistoryCompaction) -> None:
    removed_assistants = tuple(
        message
        for message in compaction.removed_messages
        if isinstance(message, AgentAssistantMessage)
    )
    expected_counts = (
        sum(
            isinstance(message, AgentUserMessage)
            for message in compaction.removed_messages
        ),
        len(removed_assistants),
        sum(len(message.tool_calls) for message in removed_assistants),
        sum(
            isinstance(message, AgentToolResultMessage)
            for message in compaction.removed_messages
        ),
        len(_user_group_boundaries(compaction.messages)),
    )
    actual_counts = (
        compaction.dropped_user_count,
        compaction.dropped_assistant_count,
        compaction.dropped_tool_call_count,
        compaction.dropped_tool_result_count,
        compaction.retained_group_count,
    )
    if actual_counts != expected_counts:
        raise ValueError("message-kind and group counts must match exact tuples")
    if compaction.dropped_group_count != compaction.dropped_user_count:
        raise ValueError("dropped_group_count must match removed users")
    if compaction.bytes_after != _messages_bytes(compaction.messages) or (
        compaction.bytes_before
        != compaction.bytes_after + _messages_bytes(compaction.removed_messages)
    ):
        raise ValueError("byte counts must match exact tuples")


def _validate_compaction_anchor(compaction: AgentHistoryCompaction) -> None:
    if (compaction.retained_user_anchor is None) != (
        compaction.retained_suffix_boundary is None
    ):
        raise ValueError("retained anchor and suffix boundary must be paired")
    if compaction.retained_user_anchor is None:
        return
    anchor_positions = [
        index
        for index, message in enumerate(compaction.messages)
        if message is compaction.retained_user_anchor
    ]
    boundary_positions = [
        index
        for index, message in enumerate(compaction.messages)
        if message is compaction.retained_suffix_boundary
    ]
    if (
        len(anchor_positions) != 1
        or len(boundary_positions) != 1
        or anchor_positions[0] >= boundary_positions[0]
    ):
        raise ValueError("retained anchor must precede the exact suffix boundary")


def compact_agent_history(
    messages: Sequence[AgentMessage],
    *,
    keep_recent_groups: int,
) -> AgentHistoryCompaction:
    """Retain recent user-turn groups without splitting tool exchanges."""

    if keep_recent_groups < 1:
        raise ValueError("keep_recent_groups must be >= 1")

    message_list = list(messages)
    bytes_before = _messages_bytes(message_list)
    boundaries = _user_group_boundaries(message_list)
    if len(boundaries) <= keep_recent_groups:
        return AgentHistoryCompaction(
            messages=tuple(message_list),
            removed_messages=(),
            changed=False,
            dropped_group_count=0,
            dropped_message_count=0,
            dropped_user_count=0,
            dropped_assistant_count=0,
            dropped_tool_call_count=0,
            dropped_tool_result_count=0,
            retained_group_count=len(boundaries),
            retained_message_count=len(message_list),
            bytes_before=bytes_before,
            bytes_after=bytes_before,
        )

    cut_index = boundaries[len(boundaries) - keep_recent_groups]
    dropped = message_list[:cut_index]
    retained = message_list[cut_index:]
    dropped_assistants = tuple(
        message for message in dropped if isinstance(message, AgentAssistantMessage)
    )
    return AgentHistoryCompaction(
        messages=tuple(retained),
        removed_messages=tuple(dropped),
        changed=True,
        dropped_group_count=len(boundaries) - keep_recent_groups,
        dropped_message_count=len(dropped),
        dropped_user_count=sum(
            1 for message in dropped if isinstance(message, AgentUserMessage)
        ),
        dropped_assistant_count=len(dropped_assistants),
        dropped_tool_call_count=sum(
            len(message.tool_calls) for message in dropped_assistants
        ),
        dropped_tool_result_count=sum(
            1 for message in dropped if isinstance(message, AgentToolResultMessage)
        ),
        retained_group_count=keep_recent_groups,
        retained_message_count=len(retained),
        bytes_before=bytes_before,
        bytes_after=_messages_bytes(retained),
    )


def compact_agent_history_tool_cycles(
    messages: Sequence[AgentMessage],
    *,
    accepted_user: AgentUserMessage,
) -> AgentHistoryCompaction:
    """Remove older settled tool cycles after the exact latest accepted user."""

    if type(accepted_user) is not AgentUserMessage:
        raise TypeError("accepted_user must be an exact AgentUserMessage")
    message_list = list(messages)
    bytes_before = _messages_bytes(message_list)
    anchors = [
        index for index, message in enumerate(message_list) if message is accepted_user
    ]
    if len(anchors) != 1 or any(
        isinstance(message, AgentUserMessage)
        for message in message_list[anchors[0] + 1 :]
    ):
        return _compaction_result(message_list, (), bytes_before=bytes_before)

    anchor_index = anchors[0]
    cycles = _settled_tool_cycles(message_list, anchor_index + 1)
    if cycles is None or len(cycles) < 2:
        return _compaction_result(message_list, (), bytes_before=bytes_before)

    newest_start, _ = cycles[-1]
    removed = message_list[anchor_index + 1 : newest_start]
    retained = message_list[: anchor_index + 1] + message_list[newest_start:]
    suffix_boundary = message_list[newest_start]
    retained_identities = [id(message) for message in retained]
    if len(set(retained_identities)) != len(retained_identities) or set(
        retained_identities
    ).intersection(id(message) for message in removed):
        return _compaction_result(message_list, (), bytes_before=bytes_before)
    return _compaction_result(
        retained,
        removed,
        bytes_before=bytes_before,
        retained_user_anchor=accepted_user,
        retained_suffix_boundary=suffix_boundary,
    )


def _settled_tool_cycles(
    messages: Sequence[AgentMessage], start: int
) -> list[tuple[int, int]] | None:
    cycles: list[tuple[int, int]] = []
    index = start
    while index < len(messages):
        assistant = messages[index]
        if not isinstance(assistant, AgentAssistantMessage) or not assistant.tool_calls:
            return None
        identities = [
            (call.provider_correlation_id, call.tool_name)
            for call in assistant.tool_calls
        ]
        if len({correlation_id for correlation_id, _ in identities}) != len(identities):
            return None
        cycle_start = index
        index += 1
        for correlation_id, tool_name in identities:
            if index >= len(messages):
                return None
            result = messages[index]
            if not isinstance(result, AgentToolResultMessage) or (
                result.provider_correlation_id,
                result.tool_name,
            ) != (correlation_id, tool_name):
                return None
            index += 1
        cycles.append((cycle_start, index))
    return cycles


def _compaction_result(
    retained: Sequence[AgentMessage],
    removed: Sequence[AgentMessage],
    *,
    bytes_before: int,
    retained_user_anchor: AgentUserMessage | None = None,
    retained_suffix_boundary: AgentMessage | None = None,
) -> AgentHistoryCompaction:
    removed_assistants = tuple(
        message for message in removed if isinstance(message, AgentAssistantMessage)
    )
    retained_boundaries = _user_group_boundaries(retained)
    return AgentHistoryCompaction(
        messages=tuple(retained),
        removed_messages=tuple(removed),
        retained_user_anchor=retained_user_anchor,
        retained_suffix_boundary=retained_suffix_boundary,
        changed=bool(removed),
        dropped_group_count=0,
        dropped_message_count=len(removed),
        dropped_user_count=sum(
            isinstance(message, AgentUserMessage) for message in removed
        ),
        dropped_assistant_count=len(removed_assistants),
        dropped_tool_call_count=sum(
            len(message.tool_calls) for message in removed_assistants
        ),
        dropped_tool_result_count=sum(
            isinstance(message, AgentToolResultMessage) for message in removed
        ),
        retained_group_count=len(retained_boundaries),
        retained_message_count=len(retained),
        bytes_before=bytes_before,
        bytes_after=_messages_bytes(retained),
    )


def should_compact_agent_history(
    messages: Sequence[AgentMessage],
    *,
    max_messages: int,
    max_bytes: int,
    keep_recent_groups: int,
) -> bool:
    """Return whether thresholds permit dropping an older user-turn group."""

    message_list = list(messages)
    boundaries = _user_group_boundaries(message_list)
    if len(boundaries) <= keep_recent_groups:
        return False
    return len(message_list) > max_messages or _messages_bytes(message_list) > max_bytes


def _message_bytes(message: AgentMessage) -> int:
    if isinstance(message, AgentUserMessage):
        return len(message.content.value.encode("utf-8"))
    if isinstance(message, AgentAssistantMessage):
        return len(message.content.value.encode("utf-8")) + sum(
            len(call.tool_name.encode("utf-8"))
            + len(call.arguments_json.value.encode("utf-8"))
            for call in message.tool_calls
        )
    if isinstance(message, AgentToolResultMessage):
        return len(message.content.value.encode("utf-8"))
    return 0


def _messages_bytes(messages: Sequence[AgentMessage]) -> int:
    return sum(_message_bytes(message) for message in messages)


def _user_group_boundaries(messages: Sequence[AgentMessage]) -> list[int]:
    return [
        index
        for index, message in enumerate(messages)
        if isinstance(message, AgentUserMessage)
    ]
