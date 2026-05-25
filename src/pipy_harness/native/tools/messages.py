"""Provider-agnostic message envelope shapes for the native tool loop.

These value objects describe what a turn-loop conversation looks like before
any specific provider serializes it. A turn is a tuple of
`UserMessage`, `AssistantMessage`, and `ToolResultMessage` values; later
slices teach each `ProviderPort` adapter how to serialize this tuple into
its native request shape.

The envelope deliberately stays minimal:

- `UserMessage` carries plain user text only.
- `AssistantMessage` carries plain assistant text plus any provider-emitted
  `ProviderToolCall` values from `ProviderResult.tool_calls`.
- `ToolResultMessage` carries one tool execution's provider-visible result;
  the pipy-owned `tool_request_id` is required and validated against the
  pipy-owned prefix, while the opaque `provider_correlation_id` rides
  separately for round-tripping back into the provider.

`LoopMessage` is a tagged union of the three message types. The envelope
defines no provider-specific fields, no archive metadata, and no workspace
effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools.base import SUPPORTED_TOOL_REQUEST_ID_PREFIX


@dataclass(frozen=True, slots=True)
class UserMessage:
    """One user turn in the provider-agnostic message envelope."""

    content: str

    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("UserMessage.content must be a string")
        if len(self.content) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                "UserMessage.content exceeds "
                f"{self.CONTENT_MAX_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """One assistant turn, with optional model-emitted tool intents."""

    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = field(default_factory=tuple)

    CONTENT_MAX_LENGTH: ClassVar[int] = 256 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("AssistantMessage.content must be a string")
        if len(self.content) > self.CONTENT_MAX_LENGTH:
            raise ValueError(
                "AssistantMessage.content exceeds "
                f"{self.CONTENT_MAX_LENGTH} characters"
            )
        if not isinstance(self.tool_calls, tuple):
            raise ValueError("AssistantMessage.tool_calls must be a tuple")
        for index, call in enumerate(self.tool_calls):
            if not isinstance(call, ProviderToolCall):
                raise ValueError(
                    "AssistantMessage.tool_calls["
                    f"{index}] must be a ProviderToolCall"
                )


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """One tool execution's provider-visible result in the envelope.

    The pipy-owned `tool_request_id` must carry the
    `pipy-tool-` prefix; provider-supplied ids ride opaquely as
    `provider_correlation_id`. This shape is the serialization view of a
    tool result and is deliberately distinct from
    `pipy_harness.native.models.NativeToolResult` (archive-safe metadata)
    and from `pipy_harness.native.tools.base.ToolExecutionResult` (the tool
    boundary return value the loop already has in memory). Loop code is
    expected to construct a `ToolResultMessage` from a `ToolExecutionResult`
    when serializing the next provider message.
    """

    tool_request_id: str
    output_text: str
    is_error: bool = False
    provider_correlation_id: str | None = None

    OUTPUT_TEXT_MAX_LENGTH: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tool_request_id, str)
            or not self.tool_request_id
        ):
            raise ValueError(
                "ToolResultMessage requires a non-empty tool_request_id"
            )
        if not self.tool_request_id.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX):
            raise ValueError(
                "ToolResultMessage.tool_request_id must be pipy-owned "
                f"(prefix '{SUPPORTED_TOOL_REQUEST_ID_PREFIX}')"
            )
        if not isinstance(self.output_text, str):
            raise ValueError("ToolResultMessage.output_text must be a string")
        if len(self.output_text) > self.OUTPUT_TEXT_MAX_LENGTH:
            raise ValueError(
                "ToolResultMessage.output_text exceeds "
                f"{self.OUTPUT_TEXT_MAX_LENGTH} characters"
            )
        if not isinstance(self.is_error, bool):
            raise ValueError("ToolResultMessage.is_error must be a bool")
        if self.provider_correlation_id is not None and (
            not isinstance(self.provider_correlation_id, str)
            or not self.provider_correlation_id
        ):
            raise ValueError(
                "ToolResultMessage.provider_correlation_id must be a non-empty "
                "string or None"
            )


LoopMessage = UserMessage | AssistantMessage | ToolResultMessage
"""Tagged union of the supported provider-agnostic message kinds."""
