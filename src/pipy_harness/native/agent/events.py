"""Typed, immutable events emitted by the canonical agent boundary."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from pipy_harness.native.agent._validation import (
    require_bool,
    require_non_negative_int,
)
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    _AGENT_MESSAGE_TYPES,
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
    AgentRunResult,
    AgentTurnOutcome,
    AgentUsage,
)


@dataclass(frozen=True, slots=True)
class AgentRunStarted:
    """The reusable agent loop accepted a run request."""


@dataclass(frozen=True, slots=True)
class TurnStarted:
    """A provider/tool turn started."""

    turn_index: int

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "TurnStarted.turn_index")


@dataclass(frozen=True, slots=True)
class MessageStarted:
    """A streamed or non-streamed message entered the public lifecycle.

    ``message`` contains the full non-streamed user/tool message. A streamed
    assistant lifecycle starts with an explicit empty ``AgentAssistantMessage``.
    """

    turn_index: int
    message: AgentMessage

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "MessageStarted.turn_index")
        if not isinstance(self.message, _AGENT_MESSAGE_TYPES):
            raise TypeError("MessageStarted.message must be AgentMessage")


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    """One full-content assistant text delta."""

    turn_index: int
    delta: ProductContent

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "AssistantTextDelta.turn_index")
        if not isinstance(self.delta, ProductContent):
            raise TypeError("AssistantTextDelta.delta must be ProductContent")


@dataclass(frozen=True, slots=True)
class AssistantReasoningDelta:
    """One full-content assistant reasoning delta."""

    turn_index: int
    delta: ProductContent

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "AssistantReasoningDelta.turn_index")
        if not isinstance(self.delta, ProductContent):
            raise TypeError("AssistantReasoningDelta.delta must be ProductContent")


@dataclass(frozen=True, slots=True)
class MessageCompleted:
    """A fully assembled message completed its public lifecycle."""

    turn_index: int
    message: AgentMessage

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "MessageCompleted.turn_index")
        if not isinstance(self.message, _AGENT_MESSAGE_TYPES):
            raise TypeError("MessageCompleted.message must be AgentMessage")


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A model-requested tool call entered execution."""

    turn_index: int
    call: AgentToolCall

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "ToolCallStarted.turn_index")
        if not isinstance(self.call, AgentToolCall):
            raise TypeError("ToolCallStarted.call must be AgentToolCall")


@dataclass(frozen=True, slots=True)
class ToolCallUpdated:
    """A live full-content update from a running tool."""

    turn_index: int
    call: AgentToolCall
    update: ProductContent

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "ToolCallUpdated.turn_index")
        if not isinstance(self.call, AgentToolCall):
            raise TypeError("ToolCallUpdated.call must be AgentToolCall")
        if not isinstance(self.update, ProductContent):
            raise TypeError("ToolCallUpdated.update must be ProductContent")


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    """A tool call finished with its provider-visible result."""

    turn_index: int
    result: AgentToolResultMessage
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "ToolCallCompleted.turn_index")
        if not isinstance(self.result, AgentToolResultMessage):
            raise TypeError("ToolCallCompleted.result must be AgentToolResultMessage")
        if self.duration_seconds is not None:
            if isinstance(self.duration_seconds, bool) or not isinstance(
                self.duration_seconds, (int, float)
            ):
                raise TypeError(
                    "ToolCallCompleted.duration_seconds must be numeric or None"
                )
            if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
                raise ValueError(
                    "ToolCallCompleted.duration_seconds must be finite and nonnegative"
                )


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    """Cumulative run usage changed after a provider turn."""

    cumulative_usage: AgentUsage
    last_turn_total_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.cumulative_usage, AgentUsage):
            raise TypeError("UsageUpdated.cumulative_usage must be AgentUsage")
        require_non_negative_int(
            self.last_turn_total_tokens, "UsageUpdated.last_turn_total_tokens"
        )


@dataclass(frozen=True, slots=True)
class RetryScheduled:
    """The future agent loop scheduled a retry for a transient failure."""

    attempt: int
    max_attempts: int
    delay_ms: int
    failure: AgentFailure

    def __post_init__(self) -> None:
        require_non_negative_int(self.attempt, "RetryScheduled.attempt")
        require_non_negative_int(self.max_attempts, "RetryScheduled.max_attempts")
        require_non_negative_int(self.delay_ms, "RetryScheduled.delay_ms")
        if self.attempt > self.max_attempts:
            raise ValueError("RetryScheduled.attempt must not exceed max_attempts")
        if not isinstance(self.failure, AgentFailure):
            raise TypeError("RetryScheduled.failure must be AgentFailure")


@dataclass(frozen=True, slots=True)
class RetryCompleted:
    """A future agent-loop retry settled."""

    attempt: int
    succeeded: bool
    failure: AgentFailure | None = None

    def __post_init__(self) -> None:
        require_non_negative_int(self.attempt, "RetryCompleted.attempt")
        require_bool(self.succeeded, "RetryCompleted.succeeded")
        if self.failure is not None and not isinstance(self.failure, AgentFailure):
            raise TypeError("RetryCompleted.failure must be AgentFailure or None")
        if self.succeeded and self.failure is not None:
            raise ValueError("successful RetryCompleted cannot carry failure details")
        if not self.succeeded and self.failure is None:
            raise ValueError("unsuccessful RetryCompleted requires failure details")


@dataclass(frozen=True, slots=True)
class SteeringConsumed:
    """Queued steering was consumed by the active run."""

    content: ProductContent

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("SteeringConsumed.content must be ProductContent")


@dataclass(frozen=True, slots=True)
class FollowUpConsumed:
    """A queued follow-up was consumed after the preceding run settled."""

    content: ProductContent

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("FollowUpConsumed.content must be ProductContent")


@dataclass(frozen=True, slots=True)
class ProviderFailed:
    """A provider request failed, whether or not a retry will follow."""

    failure: AgentFailure
    will_retry: bool

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AgentFailure):
            raise TypeError("ProviderFailed.failure must be AgentFailure")
        require_bool(self.will_retry, "ProviderFailed.will_retry")


@dataclass(frozen=True, slots=True)
class RunCancelled:
    """Cancellation was observed by the active run."""

    reason: AgentCancellationReason
    detail: ProductContent | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, AgentCancellationReason):
            raise TypeError("RunCancelled.reason must be AgentCancellationReason")
        if self.detail is not None and not isinstance(self.detail, ProductContent):
            raise TypeError("RunCancelled.detail must be ProductContent or None")


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """A provider/tool turn settled in deterministic order."""

    turn_index: int
    outcome: AgentTurnOutcome
    message: AgentAssistantMessage
    tool_results: tuple[AgentToolResultMessage, ...] = ()

    def __post_init__(self) -> None:
        require_non_negative_int(self.turn_index, "TurnCompleted.turn_index")
        if not isinstance(self.outcome, AgentTurnOutcome):
            raise TypeError("TurnCompleted.outcome must be AgentTurnOutcome")
        if not isinstance(self.message, AgentAssistantMessage):
            raise TypeError("TurnCompleted.message must be AgentAssistantMessage")
        if not isinstance(self.tool_results, tuple):
            raise TypeError("TurnCompleted.tool_results must be a tuple")
        if any(
            not isinstance(result, AgentToolResultMessage)
            for result in self.tool_results
        ):
            raise TypeError(
                "TurnCompleted.tool_results must contain AgentToolResultMessage values"
            )


@dataclass(frozen=True, slots=True)
class AgentRunCompleted:
    """Terminal outcome for one agent run."""

    result: AgentRunResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, AgentRunResult):
            raise TypeError("AgentRunCompleted.result must be AgentRunResult")


AgentEvent = (
    AgentRunStarted
    | TurnStarted
    | MessageStarted
    | AssistantTextDelta
    | AssistantReasoningDelta
    | MessageCompleted
    | ToolCallStarted
    | ToolCallUpdated
    | ToolCallCompleted
    | UsageUpdated
    | RetryScheduled
    | RetryCompleted
    | SteeringConsumed
    | FollowUpConsumed
    | ProviderFailed
    | RunCancelled
    | TurnCompleted
    | AgentRunCompleted
)
"""Closed union of events emitted synchronously by the canonical agent seam."""
