"""Provider-neutral messages used by the canonical agent seam."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
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


class AgentStopReason(StrEnum):
    """Why an assistant turn ended early (Pi ``StopReason``).

    ``None`` on a message means the turn completed (Pi ``stop``/``toolUse``).
    Pi records ``aborted`` and ``error`` messages with the content streamed so
    far and skips them when replaying history to a provider
    (``transform-messages.ts``); see :func:`provider_replay_messages`.
    """

    ABORTED = "aborted"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentAssistantMessage:
    """One assembled assistant message and its tool intents.

    A message with a ``stop_reason`` is an aborted or failed turn: it keeps
    the streamed partial text, never carries tool calls, and is transcript
    state that providers never see again.
    """

    content: ProductContent
    tool_calls: tuple[AgentToolCall, ...] = ()
    stop_reason: AgentStopReason | None = None
    error_message: str | None = None
    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if self.stop_reason is not None and type(self.stop_reason) is not (
            AgentStopReason
        ):
            raise TypeError(
                "AgentAssistantMessage.stop_reason must be AgentStopReason or None"
            )
        if self.error_message is not None:
            if type(self.error_message) is not str:
                raise TypeError(
                    "AgentAssistantMessage.error_message must be a string or None"
                )
            if self.stop_reason is None:
                raise ValueError(
                    "AgentAssistantMessage.error_message requires a stop_reason"
                )
        if self.stop_reason is not None and self.tool_calls:
            raise ValueError("a stopped AgentAssistantMessage carries no tool calls")
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


@dataclass(frozen=True, slots=True)
class AgentToolDeclaration:
    """One tool as a system message declares it to the model.

    Pi ``toToolDeclaration`` (``packages/ai/src/utils/transcript.ts``): the
    name, description and JSON-schema parameters, without executable fields.
    ``parameters_json`` holds the schema as JSON text so two declarations are
    equal exactly when Pi's ``declarationsEqual`` JSON comparison says so.
    """

    name: str
    description: str
    parameters_json: str

    def __post_init__(self) -> None:
        require_non_empty_string(self.name, "AgentToolDeclaration.name")
        if type(self.description) is not str:
            raise TypeError("AgentToolDeclaration.description must be a string")
        require_non_empty_string(
            self.parameters_json, "AgentToolDeclaration.parameters_json"
        )


@dataclass(frozen=True, slots=True)
class AgentSystemMessage:
    """Prompt and tool state recorded in the transcript (Pi ``SystemMessage``).

    Pi ``9e05370b2``: the leading system message declares the prompt sections
    and every tool; later ones patch sections by name (``None`` removes one)
    and list tool additions and removals. Replaying them in order yields the
    current prompt and tools (``native/agent/system_messages.py``).

    A system message is transcript state only (:data:`AgentTranscriptMessage`).
    It is emitted, returned in the run result and persisted, but it never
    enters provider history (:data:`AgentMessage`): pipy sends the replayed
    prompt out of band, like Pi's ``collapseSystemMessages`` for models
    without mid-conversation system messages.
    """

    content: ProductContent = ProductContent("")
    sections: tuple[tuple[str, str | None], ...] = ()
    tools_added: tuple[AgentToolDeclaration, ...] = ()
    tools_removed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("AgentSystemMessage.content must be ProductContent")
        if not isinstance(self.sections, tuple):
            raise TypeError("AgentSystemMessage.sections must be a tuple")
        names: set[str] = set()
        for index, section in enumerate(self.sections):
            if not isinstance(section, tuple) or len(section) != 2:
                raise TypeError(
                    f"AgentSystemMessage.sections[{index}] must be a (name, text) pair"
                )
            name, text = section
            require_non_empty_string(name, f"AgentSystemMessage.sections[{index}]")
            if name in names:
                raise ValueError(f"AgentSystemMessage section {name!r} is repeated")
            names.add(name)
            if text is not None and type(text) is not str:
                raise TypeError(
                    f"AgentSystemMessage section {name!r} must be a string or None"
                )
        if not isinstance(self.tools_added, tuple) or any(
            not isinstance(tool, AgentToolDeclaration) for tool in self.tools_added
        ):
            raise TypeError(
                "AgentSystemMessage.tools_added must be a tuple of AgentToolDeclaration"
            )
        if not isinstance(self.tools_removed, tuple):
            raise TypeError("AgentSystemMessage.tools_removed must be a tuple")
        for index, name in enumerate(self.tools_removed):
            require_non_empty_string(name, f"AgentSystemMessage.tools_removed[{index}]")


AgentMessage = AgentUserMessage | AgentAssistantMessage | AgentToolResultMessage
"""Closed union of provider-history messages understood by the agent loop."""

AgentTranscriptMessage = AgentSystemMessage | AgentMessage
"""A message as the transcript records it: provider history plus system state.

Lifecycle events, run results and the session tree carry this union (Pi's
``Message`` includes ``SystemMessage``); provider history stays
:data:`AgentMessage`.
"""


def provider_replay_messages(
    messages: Sequence[AgentMessage],
) -> tuple[AgentMessage, ...]:
    """History as a provider may see it: without aborted or failed turns.

    Pi ``transformMessages`` (``packages/ai/src/api/transform-messages.ts``)
    skips every assistant message whose ``stopReason`` is ``error`` or
    ``aborted``: it is an incomplete turn, and the model retries from the
    last valid state. Stopped messages carry no tool calls, so skipping one
    never orphans a tool result.
    """

    kept = tuple(
        message
        for message in messages
        if not (
            isinstance(message, AgentAssistantMessage)
            and message.stop_reason is not None
        )
    )
    if len(kept) == len(messages) and isinstance(messages, tuple):
        return messages  # nothing to drop: keep the caller's exact tuple
    return kept


_AGENT_MESSAGE_TYPES = (
    AgentUserMessage,
    AgentAssistantMessage,
    AgentToolResultMessage,
)

_AGENT_TRANSCRIPT_MESSAGE_TYPES = (AgentSystemMessage, *_AGENT_MESSAGE_TYPES)
"""Runtime counterpart of ``AgentMessage`` for fail-closed validation."""
