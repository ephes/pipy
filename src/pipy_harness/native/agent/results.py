"""Terminal results and usage values for canonical agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from pipy_harness.native.agent._validation import (
    require_bool,
    require_non_empty_string,
)
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import _AGENT_MESSAGE_TYPES, AgentMessage


class AgentRunOutcome(StrEnum):
    """Closed terminal outcomes for one agent run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentCancellationReason(StrEnum):
    """Closed reasons why an active run observed cancellation."""

    OPERATOR_ABORT = "operator_abort"
    STEERING = "steering"
    LOCAL_COMMAND = "local_command"
    PROVIDER_CANCELLED = "provider_cancelled"


class AgentTurnOutcome(StrEnum):
    """Closed outcomes for one provider/tool turn within an agent run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """Provider-neutral token and cost totals for a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        counters = (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) for value in counters
        ):
            raise TypeError("AgentUsage token counters must be integers")
        if any(value < 0 for value in counters):
            raise ValueError("AgentUsage token counters must not be negative")
        if isinstance(self.cost_usd, bool) or not isinstance(
            self.cost_usd, (int, float)
        ):
            raise TypeError("AgentUsage.cost_usd must be numeric")
        if not isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("AgentUsage.cost_usd must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class AgentFailure:
    """A typed provider or loop failure; its message is product content."""

    error_type: str
    message: ProductContent
    retryable: bool = False

    def __post_init__(self) -> None:
        require_non_empty_string(self.error_type, "AgentFailure.error_type")
        if not isinstance(self.message, ProductContent):
            raise TypeError("AgentFailure.message must be ProductContent")
        require_bool(self.retryable, "AgentFailure.retryable")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Immutable terminal state returned by the reusable agent boundary."""

    outcome: AgentRunOutcome
    messages: tuple[AgentMessage, ...]
    usage: AgentUsage = AgentUsage()
    failure: AgentFailure | None = None
    will_retry: bool = False
    cancellation_reason: AgentCancellationReason | None = None
    cancellation_detail: ProductContent | None = None

    def __post_init__(self) -> None:
        _validate_agent_run_result_fields(self)
        _validate_agent_run_result_invariants(self)


def _validate_agent_run_result_fields(result: AgentRunResult) -> None:
    """Validate the primitive field types and message shape of a run result."""

    if not isinstance(result.outcome, AgentRunOutcome):
        raise TypeError("AgentRunResult.outcome must be AgentRunOutcome")
    if not isinstance(result.messages, tuple):
        raise TypeError("AgentRunResult.messages must be a tuple")
    if any(
        not isinstance(message, _AGENT_MESSAGE_TYPES) for message in result.messages
    ):
        raise TypeError("AgentRunResult.messages contains an unsupported message")
    if not isinstance(result.usage, AgentUsage):
        raise TypeError("AgentRunResult.usage must be AgentUsage")
    if result.failure is not None and not isinstance(result.failure, AgentFailure):
        raise TypeError("AgentRunResult.failure must be AgentFailure or None")
    require_bool(result.will_retry, "AgentRunResult.will_retry")
    if result.cancellation_reason is not None and not isinstance(
        result.cancellation_reason, AgentCancellationReason
    ):
        raise TypeError(
            "AgentRunResult.cancellation_reason must be "
            "AgentCancellationReason or None"
        )
    if result.cancellation_detail is not None and not isinstance(
        result.cancellation_detail, ProductContent
    ):
        raise TypeError(
            "AgentRunResult.cancellation_detail must be ProductContent or None"
        )


def _validate_agent_run_result_invariants(result: AgentRunResult) -> None:
    """Validate outcome-specific failure, retry, and cancellation invariants."""

    if result.outcome is AgentRunOutcome.FAILED and result.failure is None:
        raise ValueError("failed AgentRunResult requires failure details")
    if result.outcome is not AgentRunOutcome.FAILED and result.failure is not None:
        raise ValueError("only failed AgentRunResult may carry failure details")
    if result.will_retry and result.outcome is not AgentRunOutcome.FAILED:
        raise ValueError("only failed AgentRunResult may set will_retry")
    if (
        result.outcome is AgentRunOutcome.CANCELLED
        and result.cancellation_reason is None
    ):
        raise ValueError("cancelled AgentRunResult requires a cancellation reason")
    if result.outcome is not AgentRunOutcome.CANCELLED and (
        result.cancellation_reason is not None
        or result.cancellation_detail is not None
    ):
        raise ValueError("only cancelled AgentRunResult may carry cancellation details")
