"""Provider-neutral messages used by the canonical agent seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, ClassVar

from pipy_harness.native.agent._validation import (
    require_bool,
    require_non_empty_string,
    require_non_negative_int,
)
from pipy_harness.native.agent.content import (
    ProductContent,
    TextContent,
    ThinkingContent,
)
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
    # Pi ``ToolCall.thoughtSignature`` (Gemini): replayed to the same model.
    thought_signature: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(
            self.provider_correlation_id, "AgentToolCall.provider_correlation_id"
        )
        require_non_empty_string(self.tool_name, "AgentToolCall.tool_name")
        if not isinstance(self.arguments_json, ProductContent):
            raise TypeError("AgentToolCall.arguments_json must be ProductContent")
        if self.thought_signature is not None and type(self.thought_signature) is not (
            str
        ):
            raise TypeError("AgentToolCall.thought_signature must be a string or None")


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
class AgentUsageCost:
    """Pi ``Usage.cost``: dollars per token class and their total."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("input", "output", "cache_read", "cache_write", "total"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"AgentUsageCost.{field_name} must be numeric")
            if not isfinite(value) or value < 0:
                raise ValueError(
                    f"AgentUsageCost.{field_name} must be finite and nonnegative"
                )
            object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True, slots=True)
class AgentMessageUsage:
    """Pi ``Usage`` stored on one assistant message.

    ``input`` is the uncached prompt (neither read from nor written to the
    cache). ``reasoning`` (part of ``output``) and ``cache_write_1h`` (part of
    ``cache_write``) are ``None`` unless the provider reported them.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: AgentUsageCost = AgentUsageCost()
    reasoning: int | None = None
    cache_write_1h: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input",
            "output",
            "cache_read",
            "cache_write",
            "total_tokens",
        ):
            require_non_negative_int(
                getattr(self, field_name), f"AgentMessageUsage.{field_name}"
            )
        for field_name in ("reasoning", "cache_write_1h"):
            value = getattr(self, field_name)
            if value is not None:
                require_non_negative_int(value, f"AgentMessageUsage.{field_name}")
        if type(self.cost) is not AgentUsageCost:
            raise TypeError("AgentMessageUsage.cost must be an AgentUsageCost")


AgentContentBlock = TextContent | ThinkingContent | AgentToolCall
"""One entry of Pi's ordered ``AssistantMessage.content``."""


@dataclass(frozen=True, slots=True)
class AgentAssistantMessage:
    """One assembled assistant message and its tool intents.

    A message with a ``stop_reason`` is an aborted or failed turn: it keeps
    the content streamed so far (partial tool calls included, which are never
    executed) and is transcript state that providers never see again.

    ``blocks`` is Pi's ordered ``content`` (text, thinking and tool-call
    blocks with their provider signatures) whenever it holds more than
    ``content`` followed by ``tool_calls``: its text blocks joined are
    ``content`` and its tool calls are ``tool_calls``. A message that is just
    that default layout stores no blocks, so it compares equal to one built
    from text and tool calls; :meth:`ordered_content` returns the blocks
    either way.

    ``usage``, ``provider``, ``api`` and ``model`` record which model answered
    and what the response cost (Pi ``AssistantMessage.usage/provider/api/model``);
    ``provider_thinking_level`` is the effort an Anthropic mid-conversation
    effort model answered at (Pi ``providerThinkingLevel``). They describe
    the turn rather than the conversation, so they take no part in equality:
    two messages with the same content and tool calls are the same history.
    """

    content: ProductContent
    tool_calls: tuple[AgentToolCall, ...] = ()
    stop_reason: AgentStopReason | None = None
    error_message: str | None = None
    usage: AgentMessageUsage | None = field(default=None, compare=False)
    provider: str | None = field(default=None, compare=False)
    model: str | None = field(default=None, compare=False)
    provider_thinking_level: str | None = field(default=None, compare=False)
    blocks: tuple[AgentContentBlock, ...] = ()
    # Pi ``AssistantMessage.api``: the adapter API that answered.
    api: str | None = field(default=None, compare=False)
    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    @classmethod
    def from_blocks(
        cls, blocks: Sequence[AgentContentBlock], **fields: Any
    ) -> AgentAssistantMessage:
        """Build a message from Pi's ordered content."""

        ordered = tuple(blocks)
        text = "".join(b.text for b in ordered if isinstance(b, TextContent))
        calls = tuple(b for b in ordered if isinstance(b, AgentToolCall))
        return cls(ProductContent(text), calls, blocks=ordered, **fields)

    def thinking_blocks(self) -> tuple[ThinkingContent, ...]:
        """The message's thinking blocks, in order."""

        return tuple(b for b in self.blocks if isinstance(b, ThinkingContent))

    def ordered_content(self) -> tuple[AgentContentBlock, ...]:
        """Pi's ordered ``content`` of this message."""

        if self.blocks:
            return self.blocks
        return _default_layout(self.content.value, self.tool_calls)

    def __post_init__(self) -> None:
        _validate_turn_metadata(self)
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
        _validate_blocks(self)


def _default_layout(
    text: str, tool_calls: tuple[AgentToolCall, ...]
) -> tuple[AgentContentBlock, ...]:
    head: tuple[AgentContentBlock, ...] = (TextContent(text),) if text else ()
    return (*head, *tool_calls)


def _validate_blocks(message: AgentAssistantMessage) -> None:
    """Check ``blocks`` against ``content``/``tool_calls``; drop a default layout."""

    blocks = message.blocks
    if not isinstance(blocks, tuple):
        raise TypeError("AgentAssistantMessage.blocks must be a tuple")
    if not blocks:
        return
    if any(
        type(block) not in (TextContent, ThinkingContent, AgentToolCall)
        for block in blocks
    ):
        raise TypeError(
            "AgentAssistantMessage.blocks must hold TextContent, ThinkingContent "
            "or AgentToolCall values"
        )
    text = "".join(b.text for b in blocks if isinstance(b, TextContent))
    if text != message.content.value:
        raise ValueError("AgentAssistantMessage.blocks text must equal its content")
    calls = tuple(b for b in blocks if isinstance(b, AgentToolCall))
    if calls != message.tool_calls:
        raise ValueError(
            "AgentAssistantMessage.blocks tool calls must equal its tool_calls"
        )
    if blocks == _default_layout(message.content.value, message.tool_calls):
        object.__setattr__(message, "blocks", ())


def _validate_turn_metadata(message: AgentAssistantMessage) -> None:
    if message.usage is not None and type(message.usage) is not AgentMessageUsage:
        raise TypeError("AgentAssistantMessage.usage must be AgentMessageUsage or None")
    for field_name in ("provider", "api", "model", "provider_thinking_level"):
        value = getattr(message, field_name)
        if value is not None:
            require_non_empty_string(value, f"AgentAssistantMessage.{field_name}")


@dataclass(frozen=True, slots=True)
class AgentToolResultMessage:
    """One provider-visible result carrying both tool identity domains."""

    tool_request_id: str
    tool_name: str
    content: ProductContent
    provider_correlation_id: str
    is_error: bool = False
    # Pi ``ToolResultMessage.details``: stored and rendered, never sent to a
    # provider, so it takes no part in history equality.
    details: Mapping[str, Any] | None = field(default=None, compare=False)
    CONTENT_MAX_LENGTH: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        if self.details is not None and not isinstance(self.details, Mapping):
            raise TypeError("AgentToolResultMessage.details must be a mapping or None")
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
    last valid state. Stopped messages may carry unexecuted partial tool calls but have no tool
    results, so skipping one never orphans a result. Private summaries quote
    their partial text separately rather than replaying the stopped message.
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
