"""Product-side collaborator adapters for the reusable headless agent loop.

The three adapters here bind product-owned callables to the canonical
``native.agent.loop`` request-source, provider-turn, and status-policy
protocols. They are pure callable wrappers with no product policy of their own:
the coding-session controller constructs them around freshly bound closures for
each run so the reusable loop can drive request preparation, provider-turn
completion, and the exact synchronous status seams without importing product
composition, UI, persistence, providers, or extensions.
"""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop import AgentLoopRequestPreparation
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.messages import AgentMessage
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
)
from pipy_harness.native.models import ProviderResult
from pipy_harness.native.tools.base import ToolDefinition


class AgentLoopRequestSourceAdapter:
    """Bind product request preparation to the canonical loop port."""

    def __init__(
        self,
        prepare: Callable[
            [
                tuple[AgentMessage, ...],
                AgentActiveInput,
                int,
                tuple[ToolDefinition, ...],
            ],
            AgentLoopRequestPreparation,
        ],
    ) -> None:
        self._prepare = prepare

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
        /,
    ) -> AgentLoopRequestPreparation:
        return self._prepare(history, active_input, turn_index, available_tools)


class AgentLoopProviderTurnAdapter:
    """Bind a freshly materialized product provider turn to the agent loop."""

    def __init__(
        self,
        complete: Callable[
            [AgentProviderRequestSnapshot, AgentEventSink, int],
            ProviderTurnOutcome,
        ],
    ) -> None:
        self._complete = complete

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome:
        return self._complete(snapshot, event_sink, turn_index)


class AgentLoopStatusPolicyAdapter:
    """Apply product status callbacks at exact canonical loop seams."""

    def __init__(
        self,
        *,
        run_entered: Callable[[], None],
        input_accepted: Callable[[], None],
        provider_result_observed: Callable[[ProviderResult], None],
        provider_cancellation_observed: Callable[[AgentCancellationReason], None],
        tool_policy_state_changed: Callable[[AgentToolPolicyState], None],
        provider_succeeded: Callable[
            [AgentProviderStatusDecision, AgentToolPolicyState], None
        ],
        provider_failed: Callable[
            [AgentProviderStatusDecision, AgentToolPolicyState], None
        ],
        no_tool_assistant: Callable[[AgentToolPolicyState], None],
        malformed_fatal: Callable[[AgentFailure, AgentToolPolicyState], None],
    ) -> None:
        self._run_entered = run_entered
        self._input_accepted = input_accepted
        self._provider_result_observed = provider_result_observed
        self._provider_cancellation_observed = provider_cancellation_observed
        self._tool_policy_state_changed = tool_policy_state_changed
        self._provider_succeeded = provider_succeeded
        self._provider_failed = provider_failed
        self._no_tool_assistant = no_tool_assistant
        self._malformed_fatal = malformed_fatal

    def run_entered(self) -> None:
        self._run_entered()

    def input_accepted(self) -> None:
        self._input_accepted()

    def provider_result_observed(self, result: ProviderResult, /) -> None:
        self._provider_result_observed(result)

    def provider_cancellation_observed(
        self,
        reason: AgentCancellationReason,
        /,
    ) -> None:
        self._provider_cancellation_observed(reason)

    def tool_policy_state_changed(
        self,
        state: AgentToolPolicyState,
        /,
    ) -> None:
        self._tool_policy_state_changed(state)

    def provider_succeeded(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        self._provider_succeeded(status, tool_state)

    def provider_failed(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        self._provider_failed(status, tool_state)

    def no_tool_assistant(self, tool_state: AgentToolPolicyState, /) -> None:
        self._no_tool_assistant(tool_state)

    def malformed_fatal(
        self,
        failure: AgentFailure,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        self._malformed_fatal(failure, tool_state)
