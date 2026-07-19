"""Provider-neutral messages used by the canonical agent seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pipy_harness.native.agent._validation import (
    require_bool,
    require_non_empty_string,
)
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """One provider-emitted tool intent with its full-content JSON arguments.

    Starts and live updates use the provider correlation id because pipy's own
    tool request id is not allocated until execution. The completed result then
    carries both identities.
    """

    provider_correlation_id: str
    tool_name: str
    arguments_json: ProductContent

    def __post_init__(self) -> None:
        require_non_empty_string(
            self.provider_correlation_id, "AgentToolCall.provider_correlation_id"
        )
        require_non_empty_string(self.tool_name, "AgentToolCall.tool_name")
        if not isinstance(self.arguments_json, ProductContent):
            raise TypeError("AgentToolCall.arguments_json must be ProductContent")


@dataclass(frozen=True, slots=True)
class AgentUserMessage:
    """One user message retained by the reusable agent loop."""

    content: ProductContent
    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("AgentUserMessage.content must be ProductContent")
        if len(self.content.value) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                f"AgentUserMessage.content exceeds {self.CONTENT_MAX_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True)
class AgentAssistantMessage:
    """One assembled assistant message and its tool intents."""

    content: ProductContent
    tool_calls: tuple[AgentToolCall, ...] = ()
    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("AgentAssistantMessage.content must be ProductContent")
        if len(self.content.value) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                "AgentAssistantMessage.content exceeds "
                f"{self.CONTENT_MAX_LENGTH} characters"
            )
        if not isinstance(self.tool_calls, tuple):
            raise TypeError("AgentAssistantMessage.tool_calls must be a tuple")
        if any(not isinstance(call, AgentToolCall) for call in self.tool_calls):
            raise TypeError(
                "AgentAssistantMessage.tool_calls must contain AgentToolCall values"
            )


@dataclass(frozen=True, slots=True)
class AgentToolResultMessage:
    """One provider-visible result carrying both tool identity domains."""

    tool_request_id: str
    tool_name: str
    content: ProductContent
    provider_correlation_id: str
    is_error: bool = False
    added_tool_names: tuple[str, ...] = ()
    CONTENT_MAX_LENGTH: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        require_non_empty_string(
            self.tool_request_id, "AgentToolResultMessage.tool_request_id"
        )
        require_non_empty_string(self.tool_name, "AgentToolResultMessage.tool_name")
        if not isinstance(self.content, ProductContent):
            raise TypeError("AgentToolResultMessage.content must be ProductContent")
        if len(self.content.value) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                "AgentToolResultMessage.content exceeds "
                f"{self.CONTENT_MAX_LENGTH} characters"
            )
        require_bool(self.is_error, "AgentToolResultMessage.is_error")
        require_non_empty_string(
            self.provider_correlation_id,
            "AgentToolResultMessage.provider_correlation_id",
        )
        if not isinstance(self.added_tool_names, tuple):
            raise TypeError("AgentToolResultMessage.added_tool_names must be a tuple")
        for index, name in enumerate(self.added_tool_names):
            require_non_empty_string(
                name, f"AgentToolResultMessage.added_tool_names[{index}]"
            )
        if not self.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "AgentToolResultMessage.tool_request_id must be pipy-owned "
                f"(prefix '{AGENT_TOOL_REQUEST_ID_PREFIX}')"
            )


AgentMessage = AgentUserMessage | AgentAssistantMessage | AgentToolResultMessage
"""Closed message union understood by the canonical agent loop."""

_AGENT_MESSAGE_TYPES = (
    AgentUserMessage,
    AgentAssistantMessage,
    AgentToolResultMessage,
)
"""Runtime counterpart of ``AgentMessage`` for fail-closed validation."""
