"""Pure policy values and transitions for the reusable agent loop."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pipy_harness.status import HarnessStatus
from pipy_harness.native.agent._validation import (
    require_bool,
    require_non_negative_int,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    freeze_provider_request,
    validate_agent_tool_call,
    validate_agent_tool_result_message,
    validate_product_content,
)
from pipy_harness.native.agent.results import AgentFailure
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult


MAX_AGENT_TOOL_BUDGET = 200


@dataclass(frozen=True, slots=True)
class AgentProviderRequestPolicyInput:
    """Product-neutral input with an explicitly deep-frozen request baseline.

    ``ProviderRequest`` is only shallowly frozen. Construction here rebuilds its
    tool definitions with detached, recursively immutable schema containers so
    mutable product dictionaries and lists never enter canonical policy state.
    """

    baseline: ProviderRequest
    active_input: AgentActiveInput

    def __post_init__(self) -> None:
        if type(self.baseline) is not ProviderRequest:
            raise TypeError("baseline must be ProviderRequest")
        if type(self.active_input) is not AgentActiveInput:
            raise TypeError("active_input must be AgentActiveInput")
        _validate_active_input(self.active_input)
        object.__setattr__(self, "baseline", freeze_provider_request(self.baseline))


def _validate_active_input(active_input: AgentActiveInput) -> None:
    if type(active_input.accepted_message) is not AgentUserMessage:
        raise TypeError("active_input accepted_message must be exact AgentUserMessage")
    validate_product_content(
        active_input.accepted_message.content, "AgentUserMessage.content"
    )
    if type(active_input.request_overlay) is not tuple:
        raise TypeError("active_input request_overlay must be an exact tuple")
    for message in active_input.request_overlay:
        if type(message) is not AgentUserMessage:
            raise TypeError(
                "request_overlay must contain exact AgentUserMessage values"
            )
        validate_product_content(message.content, "AgentUserMessage.content")


def _validate_tool_execution_outcome(outcome: ToolExecutionOutcome) -> None:
    validate_agent_tool_result_message(outcome.result)
    if type(outcome.malformed_arguments) is not bool:
        raise TypeError("ToolExecutionOutcome.malformed_arguments must be exact bool")
    if type(outcome.interruption) is not ToolExecutionInterruption:
        raise TypeError("ToolExecutionOutcome.interruption must be exact enum")


def _validate_agent_failure(failure: object) -> None:
    if type(failure) is not AgentFailure:
        raise TypeError("failure must be an exact AgentFailure or None")
    if type(failure.error_type) is not str or not failure.error_type:
        raise TypeError("AgentFailure.error_type must be a non-empty exact string")
    validate_product_content(failure.message, "AgentFailure.message")
    if type(failure.retryable) is not bool:
        raise TypeError("AgentFailure.retryable must be an exact bool")


@runtime_checkable
class AgentProviderRequestPolicy(Protocol):
    """Prepare one immutable request and authorization snapshot."""

    def prepare(
        self,
        policy_input: AgentProviderRequestPolicyInput,
        /,
    ) -> AgentProviderRequestSnapshot: ...


@dataclass(frozen=True, slots=True)
class AgentToolPolicyDecision:
    """Caller policy result for a tool that passed core authorization."""

    blocked_reason: ProductContent | None = None

    def __post_init__(self) -> None:
        validate_agent_tool_policy_decision(self)


def validate_agent_tool_policy_decision(decision: object) -> None:
    """Recursively validate one exact tool-policy callback decision."""

    if type(decision) is not AgentToolPolicyDecision:
        raise TypeError("decision must be an exact AgentToolPolicyDecision")
    if decision.blocked_reason is not None:
        validate_product_content(
            decision.blocked_reason,
            "AgentToolPolicyDecision.blocked_reason",
        )


@runtime_checkable
class AgentToolPolicy(Protocol):
    """Product-owned tool policy constrained to blocking and content transforms."""

    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision: ...

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent: ...


@dataclass(frozen=True, slots=True)
class AgentToolPolicyState:
    """Immutable counters governing one agent run's tool decisions."""

    tool_budget: int
    malformed_limit: int = 3
    invocations_this_turn: int = 0
    tool_invocation_count: int = 0
    malformed_argument_count: int = 0
    consecutive_malformed_streak: int = 0
    budget_exhausted_count: int = 0

    def __post_init__(self) -> None:
        require_non_negative_int(self.tool_budget, "tool_budget")
        if not 1 <= self.tool_budget <= MAX_AGENT_TOOL_BUDGET:
            raise ValueError(
                f"tool_budget must be between 1 and {MAX_AGENT_TOOL_BUDGET}"
            )
        require_non_negative_int(self.malformed_limit, "malformed_limit")
        if self.malformed_limit != 3:
            raise ValueError("malformed_limit must be 3")
        for field_name in (
            "invocations_this_turn",
            "tool_invocation_count",
            "malformed_argument_count",
            "consecutive_malformed_streak",
            "budget_exhausted_count",
        ):
            require_non_negative_int(getattr(self, field_name), field_name)
        if self.invocations_this_turn > self.tool_budget:
            raise ValueError("invocations_this_turn must not exceed tool_budget")
        if self.consecutive_malformed_streak > self.malformed_argument_count:
            raise ValueError(
                "consecutive_malformed_streak must not exceed malformed_argument_count"
            )


class AgentToolPolicyAction(StrEnum):
    """Closed actions produced by tool admission and settlement transitions."""

    EXECUTE = "execute"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNAUTHORIZED = "unauthorized"
    BLOCKED = "blocked"
    SETTLED = "settled"
    MALFORMED = "malformed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class AgentToolPolicyTransition:
    """One typed tool-policy action and its next immutable counter state."""

    action: AgentToolPolicyAction
    state: AgentToolPolicyState
    failure: AgentFailure | None = None
    interruption: ToolExecutionInterruption | None = None

    def __post_init__(self) -> None:
        if type(self.action) is not AgentToolPolicyAction:
            raise TypeError("action must be AgentToolPolicyAction")
        if type(self.state) is not AgentToolPolicyState:
            raise TypeError("state must be AgentToolPolicyState")
        if self.failure is not None:
            _validate_agent_failure(self.failure)
        if (
            self.interruption is not None
            and type(self.interruption) is not ToolExecutionInterruption
        ):
            raise TypeError("interruption must be ToolExecutionInterruption or None")
        if self.action is AgentToolPolicyAction.INTERRUPTED:
            if self.interruption in {None, ToolExecutionInterruption.SETTLED}:
                raise ValueError("interrupted transition requires an interruption")
        elif self.interruption is not None:
            raise ValueError("only interrupted transitions carry an interruption")
        if self.action is AgentToolPolicyAction.MALFORMED:
            if self.state.consecutive_malformed_streak == 0:
                raise ValueError("malformed transition requires a malformed streak")
            expected_failure = (
                _malformed_failure(self.state.malformed_limit)
                if self.state.consecutive_malformed_streak >= self.state.malformed_limit
                else None
            )
            if self.failure != expected_failure:
                raise ValueError(
                    "malformed transition failure must match the malformed limit"
                )
        elif self.failure is not None:
            raise ValueError("only malformed transitions carry a failure")


def decide_tool_admission(
    state: AgentToolPolicyState,
    snapshot: AgentProviderRequestSnapshot,
    call: AgentToolCall,
) -> AgentToolPolicyTransition:
    """Apply the core budget and request-authorization gates in order."""

    if type(state) is not AgentToolPolicyState:
        raise TypeError("state must be AgentToolPolicyState")
    if type(snapshot) is not AgentProviderRequestSnapshot:
        raise TypeError("snapshot must be AgentProviderRequestSnapshot")
    if type(call) is not AgentToolCall:
        raise TypeError("call must be AgentToolCall")
    validate_agent_tool_call(call)
    if state.invocations_this_turn >= state.tool_budget:
        return AgentToolPolicyTransition(
            AgentToolPolicyAction.BUDGET_EXHAUSTED,
            replace(
                state,
                budget_exhausted_count=state.budget_exhausted_count + 1,
            ),
        )
    if not snapshot.authorizes(call.tool_name):
        return _consume_without_execution(state, AgentToolPolicyAction.UNAUTHORIZED)
    return AgentToolPolicyTransition(AgentToolPolicyAction.EXECUTE, state)


def apply_tool_policy_decision(
    state: AgentToolPolicyState,
    policy_decision: AgentToolPolicyDecision,
) -> AgentToolPolicyTransition:
    """Apply caller tool policy after core admission returned ``EXECUTE``."""

    if type(state) is not AgentToolPolicyState:
        raise TypeError("state must be AgentToolPolicyState")
    if type(policy_decision) is not AgentToolPolicyDecision:
        raise TypeError("policy_decision must be AgentToolPolicyDecision")
    validate_agent_tool_policy_decision(policy_decision)
    if policy_decision.blocked_reason is not None:
        return _consume_without_execution(state, AgentToolPolicyAction.BLOCKED)
    return AgentToolPolicyTransition(AgentToolPolicyAction.EXECUTE, state)


def settle_tool_execution(
    state: AgentToolPolicyState,
    outcome: ToolExecutionOutcome,
) -> AgentToolPolicyTransition:
    """Advance counters after execution, with interruption taking precedence."""

    if type(state) is not AgentToolPolicyState:
        raise TypeError("state must be AgentToolPolicyState")
    if type(outcome) is not ToolExecutionOutcome:
        raise TypeError("outcome must be ToolExecutionOutcome")
    _validate_tool_execution_outcome(outcome)
    if outcome.interruption is not ToolExecutionInterruption.SETTLED:
        return AgentToolPolicyTransition(
            AgentToolPolicyAction.INTERRUPTED,
            state,
            interruption=outcome.interruption,
        )
    if outcome.malformed_arguments:
        malformed_count = state.malformed_argument_count + 1
        malformed_streak = state.consecutive_malformed_streak + 1
        next_state = replace(
            state,
            malformed_argument_count=malformed_count,
            consecutive_malformed_streak=malformed_streak,
        )
        failure = None
        if malformed_streak >= state.malformed_limit:
            failure = _malformed_failure(state.malformed_limit)
        return AgentToolPolicyTransition(
            AgentToolPolicyAction.MALFORMED,
            next_state,
            failure=failure,
        )
    if state.invocations_this_turn >= state.tool_budget:
        raise ValueError("settled execution cannot exceed tool_budget")
    return AgentToolPolicyTransition(
        AgentToolPolicyAction.SETTLED,
        replace(
            state,
            invocations_this_turn=state.invocations_this_turn + 1,
            tool_invocation_count=state.tool_invocation_count + 1,
            consecutive_malformed_streak=0,
        ),
    )


def _consume_without_execution(
    state: AgentToolPolicyState,
    action: AgentToolPolicyAction,
) -> AgentToolPolicyTransition:
    return AgentToolPolicyTransition(
        action,
        replace(state, invocations_this_turn=state.invocations_this_turn + 1),
    )


def _malformed_failure(limit: int) -> AgentFailure:
    return AgentFailure(
        "NativeToolLoopMalformedFatal",
        ProductContent(f"{limit} consecutive malformed tool calls"),
    )


class AgentProviderStatusAction(StrEnum):
    """Closed normalized provider completion status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentProviderStatusDecision:
    """Typed provider status with diagnostic-only response metadata."""

    action: AgentProviderStatusAction
    failure: AgentFailure | None = None
    response_status: str | None = None
    will_retry: bool = False

    def __post_init__(self) -> None:
        if type(self.action) is not AgentProviderStatusAction:
            raise TypeError("action must be AgentProviderStatusAction")
        if self.failure is not None:
            _validate_agent_failure(self.failure)
        if self.response_status is not None:
            if type(self.response_status) is not str:
                raise TypeError("response_status must be a string or None")
            if not self.response_status:
                raise ValueError("response_status must not be empty")
        require_bool(self.will_retry, "will_retry")
        if self.will_retry:
            raise ValueError("provider status normalization does not schedule retries")
        if self.action is AgentProviderStatusAction.SUCCEEDED:
            if self.failure is not None:
                raise ValueError("successful provider status cannot carry a failure")
        elif self.failure is None:
            raise ValueError("failed provider status requires a failure")


def normalize_provider_status(
    result: ProviderResult,
    *,
    provider_name: str,
) -> AgentProviderStatusDecision:
    """Normalize one provider status without adding retry or terminal-loop policy."""

    if type(result) is not ProviderResult:
        raise TypeError("result must be ProviderResult")
    if type(provider_name) is not str or not provider_name:
        raise TypeError("provider_name must be a non-empty string")
    if type(result.status) is not HarnessStatus:
        raise TypeError("ProviderResult.status must be HarnessStatus")
    if result.status is HarnessStatus.SUCCEEDED:
        return AgentProviderStatusDecision(AgentProviderStatusAction.SUCCEEDED)
    response_status = _response_status(result.metadata)
    error_type = result.error_type or "ProviderFailed"
    error_message = result.error_message or (
        f"provider {provider_name!r} returned status "
        f"{result.status.value!r} without a final response"
    )
    return AgentProviderStatusDecision(
        AgentProviderStatusAction.FAILED,
        AgentFailure(error_type, ProductContent(error_message)),
        response_status=response_status,
    )


def _response_status(metadata: dict[str, object] | None) -> str | None:
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise TypeError("ProviderResult.metadata must be a mapping or None")
    response_status = metadata.get("response_status")
    if isinstance(response_status, str) and response_status:
        return response_status
    return None
