"""Product-side collaborators for the reusable headless agent loop.

The three adapters here bind product-owned callables to the canonical
``native.agent.loop`` request-source, provider-turn, and status-policy
protocols. They are pure callable wrappers with no product policy of their own:
the coding-session controller constructs them around freshly bound closures for
each run so the reusable loop can drive request preparation, provider-turn
completion, and the exact synchronous status seams without importing product
composition, UI, persistence, providers, or extensions.

``CodingAgentRunCoordinator`` receives those three product adapters plus the
already-composed reusable-loop ports and drives one accepted turn: it assembles
the canonical :class:`~pipy_harness.native.agent.loop.AgentLoop`, builds the run
input from the live coding-session history, invokes the loop, mirrors the final
history back into :class:`CodingSessionState`, and forwards the loop's
controller handoff to the input-queue retention seam. It owns neither queue
storage/ordering/reservation nor persistence writes; those stay with the
product controller.
"""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop import (
    AgentLoop,
    AgentLoopOutcome,
    AgentLoopProviderTurn,
    AgentLoopRequestPreparation,
    AgentLoopRequestSource,
    AgentLoopRunInput,
    AgentLoopStatusPolicy,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusDecision,
    AgentToolPolicy,
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
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputPort,
    AgentRunEffectSink,
    AgentUsagePublisher,
)
from pipy_harness.native.agent.tools import (
    AgentToolCapabilities,
    ToolInterruptWaiter,
)
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.coding.state import CodingSessionState
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


class CodingAgentRunCoordinator:
    """Assemble and drive one reusable ``AgentLoop`` run for the controller.

    The controller builds one coordinator per accepted turn from its freshly
    bound product adapters and the already-composed reusable-loop ports. A
    single :meth:`run_turn` call assembles the canonical loop, constructs the
    run input from the live coding-session history, invokes the loop, mirrors
    the returned final history back into the session state, and forwards the
    loop's controller handoff to the input-queue retention seam. Queue storage,
    ordering, reservation, idle transitions, lifecycle, and persistence writes
    remain the product controller's responsibility.
    """

    def __init__(
        self,
        *,
        request_source: AgentLoopRequestSource,
        provider_turn: AgentLoopProviderTurn,
        status_policy: AgentLoopStatusPolicy,
        tool_capabilities: AgentToolCapabilities,
        tool_policy: AgentToolPolicy,
        event_sink: AgentEventSink,
        run_effect_sink: AgentRunEffectSink,
        usage_publisher: AgentUsagePublisher,
        queued_input_port: AgentQueuedInputPort,
        coding_state: CodingSessionState,
        retain_next_input: Callable[[AgentQueuedInput | None], None],
        tool_waiter: ToolInterruptWaiter | None = None,
    ) -> None:
        if type(coding_state) is not CodingSessionState:
            raise TypeError("coding_state must be an exact CodingSessionState")
        if not callable(retain_next_input):
            raise TypeError("retain_next_input must be callable")
        self._request_source = request_source
        self._provider_turn = provider_turn
        self._status_policy = status_policy
        self._tool_capabilities = tool_capabilities
        self._tool_policy = tool_policy
        self._event_sink = event_sink
        self._run_effect_sink = run_effect_sink
        self._usage_publisher = usage_publisher
        self._queued_input_port = queued_input_port
        self._coding_state = coding_state
        self._retain_next_input = retain_next_input
        self._tool_waiter = tool_waiter

    def run_turn(
        self,
        active_input: AgentActiveInput,
        initial_tool_state: AgentToolPolicyState,
        *,
        pricing: AgentTokenPricing | None,
        accepted_queued_input: AgentQueuedInput | None,
    ) -> AgentLoopOutcome:
        agent_loop = AgentLoop(
            request_source=self._request_source,
            provider_turn=self._provider_turn,
            tool_capabilities=self._tool_capabilities,
            tool_policy=self._tool_policy,
            event_sink=self._event_sink,
            run_effect_sink=self._run_effect_sink,
            usage_publisher=self._usage_publisher,
            queued_input_port=self._queued_input_port,
            status_policy=self._status_policy,
            tool_waiter=self._tool_waiter,
        )
        outcome = agent_loop.run(
            AgentLoopRunInput(
                self._coding_state.messages,
                active_input,
                initial_tool_state,
                pricing=pricing,
                accepted_queued_input=accepted_queued_input,
            )
        )
        self._coding_state.mirror_history(outcome.final_history)
        self._retain_next_input(outcome.next_input)
        return outcome
