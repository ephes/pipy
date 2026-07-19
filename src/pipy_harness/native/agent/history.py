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

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("AgentHistoryCompaction.messages must be a tuple")


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
