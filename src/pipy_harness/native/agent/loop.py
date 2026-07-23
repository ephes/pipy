"""UI-free synchronous ownership of one accepted agent run."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

from pipy_harness.status import HarnessStatus
from pipy_harness.native.agent._validation import require_bool
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AgentRunCompleted,
    AgentRunStarted,
    FollowUpConsumed,
    MessageCompleted,
    MessageStarted,
    ProviderFailed,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusAction,
    AgentProviderStatusDecision,
    AgentToolPolicy,
    AgentToolPolicyAction,
    AgentToolPolicyState,
    apply_tool_policy_decision,
    decide_tool_admission,
    normalize_provider_status,
    settle_tool_execution,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    validate_agent_tool_call,
    validate_agent_tool_result_message,
    validate_product_content,
    validate_provider_request_snapshot,
)
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
    AgentRunOutcome,
    AgentRunResult,
    AgentTurnOutcome,
)
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
    AgentUsagePublication,
    AgentUsagePublisher,
)
from pipy_harness.native.agent.tools import (
    AgentToolCapabilities,
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentUsageAccumulator,
)
from pipy_harness.native.models import ProviderResult, ProviderToolCall
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class AgentLoopRunInput:
    """Immutable inputs for one already accepted user prompt."""

    history: tuple[AgentMessage, ...]
    active_input: AgentActiveInput
    tool_policy_state: AgentToolPolicyState
    pricing: AgentTokenPricing | None = None
    accepted_queued_input: AgentQueuedInput | None = None

    def __post_init__(self) -> None:
        _validate_loop_run_input(self)


def _validate_loop_run_input(run_input: AgentLoopRunInput) -> None:
    _validate_history(run_input.history)
    _validate_active_input(run_input.active_input)
    if any(
        message is run_input.active_input.accepted_message
        for message in run_input.history
    ):
        raise ValueError("accepted_message must not already occur in initial history")
    _validate_overlay_absent(run_input.history, run_input.active_input)
    if type(run_input.tool_policy_state) is not AgentToolPolicyState:
        raise TypeError("tool_policy_state must be an exact AgentToolPolicyState")
    _revalidate_tool_policy_state(run_input.tool_policy_state)
    if run_input.tool_policy_state.invocations_this_turn != 0:
        raise ValueError("a new agent run must start with zero turn invocations")
    if (
        run_input.pricing is not None
        and type(run_input.pricing) is not AgentTokenPricing
    ):
        raise TypeError("pricing must be an exact AgentTokenPricing or None")
    if run_input.pricing is not None:
        _revalidate_pricing(run_input.pricing)
    _validate_queued_input(
        run_input.accepted_queued_input,
        "accepted_queued_input",
    )


@dataclass(frozen=True, slots=True)
class AgentLoopRequestPreparation:
    """History and exact request snapshot prepared for one provider turn."""

    history: tuple[AgentMessage, ...]
    snapshot: AgentProviderRequestSnapshot

    def __post_init__(self) -> None:
        _validate_request_preparation(self)


def _validate_request_preparation(
    preparation: AgentLoopRequestPreparation,
) -> None:
    if type(preparation) is not AgentLoopRequestPreparation:
        raise TypeError("request source must return AgentLoopRequestPreparation")
    _validate_history(preparation.history)
    validate_provider_request_snapshot(preparation.snapshot)


@runtime_checkable
class AgentLoopRequestSource(Protocol):
    """Apply product request and compaction policy for one provider turn."""

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
        /,
    ) -> AgentLoopRequestPreparation: ...


@runtime_checkable
class AgentLoopProviderTurn(Protocol):
    """Materialize and execute one freshly bound provider request."""

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome: ...


@runtime_checkable
class AgentLoopStatusPolicy(Protocol):
    """Product callbacks invoked at exact synchronous agent-loop seams."""

    def run_entered(self) -> None: ...

    def input_accepted(self) -> None: ...

    def tool_policy_state_changed(
        self,
        state: AgentToolPolicyState,
        /,
    ) -> None: ...

    def provider_result_observed(self, result: ProviderResult, /) -> None: ...

    def provider_cancellation_observed(
        self,
        reason: AgentCancellationReason,
        /,
    ) -> None: ...

    def provider_succeeded(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None: ...

    def provider_failed(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None: ...

    def no_tool_assistant(self, tool_state: AgentToolPolicyState, /) -> None: ...

    def malformed_fatal(
        self,
        failure: AgentFailure,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentLoopOutcome:
    """Final canonical result plus state returned to the product controller."""

    result: AgentRunResult
    final_history: tuple[AgentMessage, ...]
    final_tool_state: AgentToolPolicyState
    next_input: AgentQueuedInput | None = None
    terminate_session: bool = False
    provider_status: AgentProviderStatusDecision | None = None

    def __post_init__(self) -> None:
        if type(self.result) is not AgentRunResult:
            raise TypeError("result must be an exact AgentRunResult")
        _validate_history(self.final_history)
        if type(self.final_tool_state) is not AgentToolPolicyState:
            raise TypeError("final_tool_state must be AgentToolPolicyState")
        _validate_queued_input(self.next_input, "next_input")
        require_bool(self.terminate_session, "terminate_session")
        if (
            self.provider_status is not None
            and type(self.provider_status) is not AgentProviderStatusDecision
        ):
            raise TypeError(
                "provider_status must be AgentProviderStatusDecision or None"
            )
        if self.terminate_session and self.result.outcome is not AgentRunOutcome.FAILED:
            raise ValueError("only a failed run may terminate the product session")


class _IterationDisposition(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"


@dataclass(slots=True)
class _RunState:
    history: tuple[AgentMessage, ...]
    tool_state: AgentToolPolicyState
    usage: AgentUsageAccumulator
    failure: AgentFailure | None = None
    cancellation: AgentCancellationReason | None = None
    provider_status: AgentProviderStatusDecision | None = None
    terminate_session: bool = False


@dataclass(frozen=True, slots=True)
class _ToolCycleOutcome:
    disposition: _IterationDisposition
    failure: AgentFailure | None = None
    cancellation: AgentCancellationReason | None = None


class AgentLoop:
    """Run one accepted prompt through the synchronous provider/tool cycle."""

    def __init__(
        self,
        *,
        request_source: AgentLoopRequestSource,
        provider_turn: AgentLoopProviderTurn,
        tool_capabilities: AgentToolCapabilities,
        tool_policy: AgentToolPolicy,
        event_sink: AgentEventSink,
        usage_publisher: AgentUsagePublisher,
        queued_input_port: AgentQueuedInputPort,
        status_policy: AgentLoopStatusPolicy,
        tool_waiter: ToolInterruptWaiter | None = None,
    ) -> None:
        _require_protocol(request_source, AgentLoopRequestSource, "request_source")
        _require_protocol(provider_turn, AgentLoopProviderTurn, "provider_turn")
        _require_protocol(tool_capabilities, AgentToolCapabilities, "tool_capabilities")
        _require_protocol(tool_policy, AgentToolPolicy, "tool_policy")
        _require_protocol(event_sink, AgentEventSink, "event_sink")
        _require_protocol(usage_publisher, AgentUsagePublisher, "usage_publisher")
        _require_protocol(
            queued_input_port,
            AgentQueuedInputPort,
            "queued_input_port",
        )
        _require_protocol(status_policy, AgentLoopStatusPolicy, "status_policy")
        if tool_waiter is not None and not callable(tool_waiter):
            raise TypeError("tool_waiter must be callable or None")
        self._request_source = request_source
        self._provider_turn = provider_turn
        self._tools = tool_capabilities
        self._tool_policy = tool_policy
        self._events = event_sink
        self._usage_publisher = usage_publisher
        self._queued_inputs = queued_input_port
        self._status = status_policy
        self._tool_waiter = tool_waiter

    def run(self, run_input: AgentLoopRunInput) -> AgentLoopOutcome:
        if type(run_input) is not AgentLoopRunInput:
            raise TypeError("run_input must be an exact AgentLoopRunInput")
        _validate_loop_run_input(run_input)
        state = _RunState(
            run_input.history,
            run_input.tool_policy_state,
            AgentUsageAccumulator(run_input.pricing),
        )
        self._events.emit(AgentRunStarted())
        self._emit_consumed_input(run_input)
        self._status.run_entered()
        self._append_message(state, run_input.active_input.accepted_message)
        self._status.input_accepted()
        for turn_index in range(run_input.tool_policy_state.tool_budget + 2):
            disposition = self._run_iteration(state, run_input.active_input, turn_index)
            if disposition is _IterationDisposition.STOP:
                break
        result = self._build_result(state, run_input.active_input)
        self._status.tool_policy_state_changed(state.tool_state)
        self._events.emit(AgentRunCompleted(result))
        next_input = None
        if not state.terminate_session:
            next_input = self._queued_inputs.take_next()
            _validate_queued_input(next_input, "next_input")
        return AgentLoopOutcome(
            result,
            state.history,
            state.tool_state,
            next_input=next_input,
            terminate_session=state.terminate_session,
            provider_status=state.provider_status,
        )

    def _emit_consumed_input(self, run_input: AgentLoopRunInput) -> None:
        queued_input = run_input.accepted_queued_input
        if queued_input is None:
            return
        if queued_input.kind is AgentQueuedInputKind.STEERING:
            self._events.emit(SteeringConsumed(queued_input.content))
        elif queued_input.kind is AgentQueuedInputKind.FOLLOW_UP:
            self._events.emit(FollowUpConsumed(queued_input.content))

    def _run_iteration(
        self,
        state: _RunState,
        active_input: AgentActiveInput,
        turn_index: int,
    ) -> _IterationDisposition:
        preparation = self._request_source.prepare(
            state.history,
            active_input,
            turn_index,
            _validate_tool_definitions(self._tools.definitions()),
        )
        _validate_request_preparation(preparation)
        _validate_accepted_message_anchor(
            preparation.history,
            active_input.accepted_message,
        )
        _validate_overlay_absent(preparation.history, active_input)
        state.history = preparation.history
        self._start_turn(turn_index, active_input.accepted_message)
        completion = self._provider_turn.complete(
            preparation.snapshot,
            self._events,
            turn_index,
        )
        if type(completion) is not ProviderTurnOutcome:
            raise TypeError("provider turn must return ProviderTurnOutcome")
        if completion.result is None:
            return self._settle_provider_cancellation(state, completion, turn_index)
        return self._settle_provider_result(
            state,
            active_input,
            preparation.snapshot,
            completion.result,
            turn_index,
        )

    def _start_turn(
        self,
        turn_index: int,
        accepted_message: AgentUserMessage,
    ) -> None:
        self._events.emit(TurnStarted(turn_index))
        if turn_index == 0:
            self._events.emit(MessageStarted(turn_index, accepted_message))
            self._events.emit(MessageCompleted(turn_index, accepted_message))
        self._events.emit(
            MessageStarted(turn_index, AgentAssistantMessage(ProductContent("")))
        )

    def _settle_provider_cancellation(
        self,
        state: _RunState,
        completion: ProviderTurnOutcome,
        turn_index: int,
    ) -> _IterationDisposition:
        reason = completion.cancellation_reason
        assert reason is not None
        state.cancellation = reason
        empty_assistant = AgentAssistantMessage(ProductContent(""))
        self._events.emit(RunCancelled(reason))
        self._status.provider_cancellation_observed(reason)
        self._events.emit(MessageCompleted(turn_index, empty_assistant))
        self._events.emit(
            TurnCompleted(turn_index, AgentTurnOutcome.CANCELLED, empty_assistant)
        )
        return _IterationDisposition.STOP

    def _settle_provider_result(
        self,
        state: _RunState,
        active_input: AgentActiveInput,
        snapshot: AgentProviderRequestSnapshot,
        result: ProviderResult,
        turn_index: int,
    ) -> _IterationDisposition:
        _validate_provider_result(result, snapshot)
        self._status.provider_result_observed(result)
        self._publish_usage(state, result)
        provider_status = normalize_provider_status(
            result,
            provider_name=snapshot.request.provider_name,
        )
        state.provider_status = provider_status
        if provider_status.action is AgentProviderStatusAction.FAILED:
            return self._settle_provider_failure(state, provider_status, turn_index)
        self._status.provider_succeeded(provider_status, state.tool_state)
        state.failure = None
        assistant = _assistant_message(result)
        self._events.emit(MessageCompleted(turn_index, assistant))
        self._append_message(state, assistant)
        if not assistant.tool_calls:
            self._status.no_tool_assistant(state.tool_state)
            self._events.emit(
                TurnCompleted(turn_index, AgentTurnOutcome.SUCCEEDED, assistant)
            )
            return _IterationDisposition.STOP
        return self._run_tool_cycle(
            state, active_input, snapshot, assistant, turn_index
        )

    def _publish_usage(self, state: _RunState, result: ProviderResult) -> None:
        sample = AgentProviderUsageSample.from_mapping(result.usage)
        state.usage.absorb(sample)
        self._usage_publisher.publish(
            AgentUsagePublication(
                sample,
                state.usage.agent_usage(),
                state.usage.last_total_tokens,
            )
        )

    def _settle_provider_failure(
        self,
        state: _RunState,
        status: AgentProviderStatusDecision,
        turn_index: int,
    ) -> _IterationDisposition:
        failure = status.failure
        assert failure is not None
        state.failure = failure
        empty_assistant = AgentAssistantMessage(ProductContent(""))
        self._events.emit(ProviderFailed(failure, will_retry=status.will_retry))
        self._status.provider_failed(status, state.tool_state)
        self._events.emit(MessageCompleted(turn_index, empty_assistant))
        self._events.emit(
            TurnCompleted(turn_index, AgentTurnOutcome.FAILED, empty_assistant)
        )
        return _IterationDisposition.STOP

    def _run_tool_cycle(
        self,
        state: _RunState,
        active_input: AgentActiveInput,
        snapshot: AgentProviderRequestSnapshot,
        assistant: AgentAssistantMessage,
        turn_index: int,
    ) -> _IterationDisposition:
        del active_input
        results: list[AgentToolResultMessage] = []
        cycle_outcome = _ToolCycleOutcome(_IterationDisposition.CONTINUE)
        for call_index, call in enumerate(assistant.tool_calls):
            cycle_outcome = self._handle_tool_call(
                state,
                snapshot,
                assistant.tool_calls,
                call_index,
                call,
                results,
                turn_index,
            )
            if cycle_outcome.disposition is _IterationDisposition.STOP:
                break
        turn_outcome = AgentTurnOutcome.SUCCEEDED
        if cycle_outcome.cancellation is not None:
            state.cancellation = cycle_outcome.cancellation
            self._events.emit(RunCancelled(cycle_outcome.cancellation))
            turn_outcome = AgentTurnOutcome.CANCELLED
        elif cycle_outcome.failure is not None:
            state.failure = cycle_outcome.failure
            state.terminate_session = True
            turn_outcome = AgentTurnOutcome.FAILED
        self._events.emit(
            TurnCompleted(turn_index, turn_outcome, assistant, tuple(results))
        )
        return cycle_outcome.disposition

    def _handle_tool_call(
        self,
        state: _RunState,
        snapshot: AgentProviderRequestSnapshot,
        calls: tuple[AgentToolCall, ...],
        call_index: int,
        call: AgentToolCall,
        results: list[AgentToolResultMessage],
        turn_index: int,
    ) -> _ToolCycleOutcome:
        admission = decide_tool_admission(state.tool_state, snapshot, call)
        if admission.action is AgentToolPolicyAction.BUDGET_EXHAUSTED:
            state.tool_state = admission.state
            self._status.tool_policy_state_changed(state.tool_state)
            result = self._tools.error_result(
                call,
                f"tool budget exhausted (limit {state.tool_state.tool_budget})",
            )
            self._record_policy_result(state, call, result, results, turn_index)
            return _ToolCycleOutcome(_IterationDisposition.CONTINUE)
        if admission.action is AgentToolPolicyAction.UNAUTHORIZED:
            result = self._tools.error_result(call, f"unknown tool: {call.tool_name}")
            self._record_policy_result(state, call, result, results, turn_index)
            state.tool_state = admission.state
            self._status.tool_policy_state_changed(state.tool_state)
            return _ToolCycleOutcome(_IterationDisposition.CONTINUE)
        return self._execute_admitted_tool(
            state, calls, call_index, call, results, turn_index, admission.state
        )

    def _execute_admitted_tool(
        self,
        state: _RunState,
        calls: tuple[AgentToolCall, ...],
        call_index: int,
        call: AgentToolCall,
        results: list[AgentToolResultMessage],
        turn_index: int,
        admitted_state: AgentToolPolicyState,
    ) -> _ToolCycleOutcome:
        self._events.emit(ToolCallStarted(turn_index, call))
        decision = self._tool_policy.before_execute(call)
        transition = apply_tool_policy_decision(admitted_state, decision)
        if transition.action is AgentToolPolicyAction.BLOCKED:
            assert decision.blocked_reason is not None
            result = self._tools.error_result(
                call,
                f"blocked by extension: {decision.blocked_reason.value}",
            )
            self._complete_and_append(state, call, result, results, turn_index)
            state.tool_state = transition.state
            self._status.tool_policy_state_changed(state.tool_state)
            return _ToolCycleOutcome(_IterationDisposition.CONTINUE)
        return self._execute_tool(state, calls, call_index, call, results, turn_index)

    def _execute_tool(
        self,
        state: _RunState,
        calls: tuple[AgentToolCall, ...],
        call_index: int,
        call: AgentToolCall,
        results: list[AgentToolResultMessage],
        turn_index: int,
    ) -> _ToolCycleOutcome:
        started_at = datetime.now(UTC)
        execution = self._tools.execute(
            call,
            output_sink=self._tool_output_sink(turn_index, call),
            wait_for_interrupt=self._tool_waiter,
        )
        settlement = settle_tool_execution(state.tool_state, execution)
        _validate_tool_result_for_call(execution.result, call)
        result = _transform_tool_result(self._tool_policy, call, execution.result)
        _validate_tool_result_for_call(result, call)
        duration = (datetime.now(UTC) - started_at).total_seconds()
        self._events.emit(
            ToolCallCompleted(turn_index, result, duration_seconds=duration)
        )
        results.append(result)
        if settlement.action is AgentToolPolicyAction.INTERRUPTED:
            self._append_message(state, result)
            self._append_skipped_tools(state, calls[call_index + 1 :], results)
            return _ToolCycleOutcome(
                _IterationDisposition.STOP,
                cancellation=_interruption_reason(settlement.interruption),
            )
        state.tool_state = settlement.state
        self._status.tool_policy_state_changed(state.tool_state)
        self._append_message(state, result)
        if settlement.action is AgentToolPolicyAction.MALFORMED:
            if settlement.failure is not None:
                self._status.malformed_fatal(settlement.failure, state.tool_state)
                return _ToolCycleOutcome(
                    _IterationDisposition.STOP,
                    failure=settlement.failure,
                )
            return _ToolCycleOutcome(_IterationDisposition.CONTINUE)
        assert settlement.action is AgentToolPolicyAction.SETTLED
        return _ToolCycleOutcome(_IterationDisposition.CONTINUE)

    def _tool_output_sink(
        self,
        turn_index: int,
        call: AgentToolCall,
    ) -> Callable[[str], None]:
        def _emit(chunk: str) -> None:
            self._events.emit(ToolCallUpdated(turn_index, call, ProductContent(chunk)))

        return _emit

    def _record_policy_result(
        self,
        state: _RunState,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        results: list[AgentToolResultMessage],
        turn_index: int,
    ) -> None:
        _validate_tool_result_for_call(result, call)
        self._events.emit(ToolCallStarted(turn_index, call))
        self._complete_and_append(state, call, result, results, turn_index)

    def _complete_and_append(
        self,
        state: _RunState,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        results: list[AgentToolResultMessage],
        turn_index: int,
    ) -> None:
        _validate_tool_result_for_call(result, call)
        self._events.emit(ToolCallCompleted(turn_index, result))
        results.append(result)
        self._append_message(state, result)

    def _append_skipped_tools(
        self,
        state: _RunState,
        calls: Sequence[AgentToolCall],
        results: list[AgentToolResultMessage],
    ) -> None:
        for call in calls:
            skipped = self._tools.error_result(
                call,
                "tool skipped because the turn was interrupted",
            )
            _validate_tool_result_for_call(skipped, call)
            results.append(skipped)
            self._append_message(state, skipped)

    def _append_message(self, state: _RunState, message: AgentMessage) -> None:
        state.history = (*state.history, message)

    @staticmethod
    def _build_result(
        state: _RunState,
        active_input: AgentActiveInput,
    ) -> AgentRunResult:
        messages = active_input.result_messages(state.history)
        usage = state.usage.agent_usage()
        if state.failure is not None:
            return AgentRunResult(
                AgentRunOutcome.FAILED,
                messages,
                usage,
                failure=state.failure,
            )
        if state.cancellation is not None:
            return AgentRunResult(
                AgentRunOutcome.CANCELLED,
                messages,
                usage,
                cancellation_reason=state.cancellation,
            )
        return AgentRunResult(AgentRunOutcome.SUCCEEDED, messages, usage)


def _assistant_message(result: ProviderResult) -> AgentAssistantMessage:
    calls = tuple(
        AgentToolCall(
            call.provider_correlation_id,
            call.tool_name,
            ProductContent(call.arguments_json),
        )
        for call in result.tool_calls
    )
    return AgentAssistantMessage(ProductContent(result.final_text or ""), calls)


def _transform_tool_result(
    policy: AgentToolPolicy,
    call: AgentToolCall,
    result: AgentToolResultMessage,
) -> AgentToolResultMessage:
    content = policy.transform_result(call, result)
    validate_product_content(content, "AgentToolPolicy.transform_result result")
    if content == result.content:
        return result
    return replace(result, content=content)


def _interruption_reason(
    interruption: ToolExecutionInterruption | None,
) -> AgentCancellationReason:
    if interruption is ToolExecutionInterruption.LOCAL_COMMAND:
        return AgentCancellationReason.LOCAL_COMMAND
    if interruption is ToolExecutionInterruption.OPERATOR_ABORT:
        return AgentCancellationReason.OPERATOR_ABORT
    raise ValueError("interrupted tool call requires a closed cancellation reason")


def _validate_history(history: object) -> None:
    if type(history) is not tuple:
        raise TypeError("history must be an exact tuple")
    for message in history:
        _validate_message(message)


def _validate_message(message: object) -> None:
    if type(message) is AgentUserMessage:
        validate_product_content(message.content, "AgentUserMessage.content")
        AgentUserMessage.__post_init__(message)
        return
    if type(message) is AgentAssistantMessage:
        validate_product_content(message.content, "AgentAssistantMessage.content")
        if type(message.tool_calls) is not tuple:
            raise TypeError("AgentAssistantMessage.tool_calls must be an exact tuple")
        for call in message.tool_calls:
            validate_agent_tool_call(call)
            AgentToolCall.__post_init__(call)
        AgentAssistantMessage.__post_init__(message)
        return
    if type(message) is AgentToolResultMessage:
        validate_agent_tool_result_message(message)
        AgentToolResultMessage.__post_init__(message)
        return
    raise TypeError("history contains an unsupported message")


def _validate_active_input(active_input: object) -> None:
    if type(active_input) is not AgentActiveInput:
        raise TypeError("active_input must be an exact AgentActiveInput")
    if type(active_input.accepted_message) is not AgentUserMessage:
        raise TypeError("accepted_message must be an exact AgentUserMessage")
    validate_product_content(
        active_input.accepted_message.content,
        "AgentActiveInput.accepted_message.content",
    )
    AgentUserMessage.__post_init__(active_input.accepted_message)
    if type(active_input.request_overlay) is not tuple:
        raise TypeError("request_overlay must be an exact tuple")
    for message in active_input.request_overlay:
        if type(message) is not AgentUserMessage:
            raise TypeError(
                "request_overlay must contain exact AgentUserMessage values"
            )
        validate_product_content(message.content, "request_overlay message content")
        AgentUserMessage.__post_init__(message)
        if message is active_input.accepted_message:
            raise ValueError("accepted_message must not occur in request_overlay")


def _validate_queued_input(queued_input: object, field_name: str) -> None:
    if queued_input is None:
        return
    if type(queued_input) is not AgentQueuedInput:
        raise TypeError(f"{field_name} must be an exact AgentQueuedInput or None")
    validate_product_content(queued_input.content, f"{field_name}.content")
    if type(queued_input.kind) is not AgentQueuedInputKind:
        raise TypeError(f"{field_name}.kind must be an exact AgentQueuedInputKind")


def _validate_accepted_message_anchor(
    history: tuple[AgentMessage, ...],
    accepted_message: AgentUserMessage,
) -> None:
    matches = sum(message is accepted_message for message in history)
    if matches != 1:
        raise ValueError("accepted_message must occur exactly once in prepared history")


def _validate_provider_result(
    result: object,
    snapshot: AgentProviderRequestSnapshot,
) -> None:
    validated = _validate_provider_result_identity(result, snapshot)
    _validate_provider_result_scalars(validated)
    _validate_provider_result_optional_mappings(validated)
    _validate_provider_result_tool_calls(validated)


def _validate_provider_result_identity(
    result: object,
    snapshot: AgentProviderRequestSnapshot,
) -> ProviderResult:
    if type(result) is not ProviderResult:
        raise TypeError("provider turn result must be an exact ProviderResult")
    if type(result.status) is not HarnessStatus:
        raise TypeError("ProviderResult.status must be an exact HarnessStatus")
    if result.status not in {
        HarnessStatus.SUCCEEDED,
        HarnessStatus.FAILED,
        HarnessStatus.ABORTED,
    }:
        raise ValueError("ProviderResult.status must be terminal")
    for field_name in ("provider_name", "model_id"):
        if type(getattr(result, field_name)) is not str:
            raise TypeError(f"ProviderResult.{field_name} must be an exact string")
    if result.provider_name != snapshot.request.provider_name:
        raise ValueError("ProviderResult.provider_name must match the request")
    if result.model_id != snapshot.request.model_id:
        raise ValueError("ProviderResult.model_id must match the request")
    return result


def _validate_provider_result_scalars(result: ProviderResult) -> None:
    for field_name in ("started_at", "ended_at"):
        if type(getattr(result, field_name)) is not datetime:
            raise TypeError(f"ProviderResult.{field_name} must be an exact datetime")
    for field_name in ("final_text", "error_type", "error_message"):
        value = getattr(result, field_name)
        if value is not None and type(value) is not str:
            raise TypeError(
                f"ProviderResult.{field_name} must be an exact string or None"
            )
    AgentAssistantMessage(ProductContent(result.final_text or ""))


def _validate_provider_result_optional_mappings(result: ProviderResult) -> None:
    if result.usage is not None:
        _validate_json_mapping(result.usage, "ProviderResult.usage")
        _validate_usage_sample(AgentProviderUsageSample.from_mapping(result.usage))
    if result.metadata is not None:
        _validate_json_mapping(result.metadata, "ProviderResult.metadata")


def _validate_provider_result_tool_calls(result: ProviderResult) -> None:
    if type(result.tool_calls) is not tuple:
        raise TypeError("ProviderResult.tool_calls must be an exact tuple")
    for call in result.tool_calls:
        _validate_provider_tool_call(call)


def _validate_provider_tool_call(call: object) -> None:
    if type(call) is not ProviderToolCall:
        raise TypeError(
            "ProviderResult.tool_calls must contain exact ProviderToolCall values"
        )
    for field_name in (
        "provider_correlation_id",
        "tool_name",
        "arguments_json",
    ):
        value = getattr(call, field_name)
        if type(value) is not str:
            raise TypeError(f"ProviderToolCall.{field_name} must be an exact string")
    if not call.provider_correlation_id:
        raise ValueError("ProviderToolCall.provider_correlation_id must not be empty")
    if not call.tool_name:
        raise ValueError("ProviderToolCall.tool_name must not be empty")
    if (
        len(call.provider_correlation_id)
        > ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
    ):
        raise ValueError("ProviderToolCall.provider_correlation_id is too long")
    if len(call.tool_name) > ProviderToolCall.TOOL_NAME_MAX_LENGTH:
        raise ValueError("ProviderToolCall.tool_name is too long")
    if len(call.arguments_json) > ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH:
        raise ValueError("ProviderToolCall.arguments_json is too long")


def _validate_tool_result_for_call(
    result: object,
    call: AgentToolCall,
) -> None:
    validate_agent_tool_result_message(result)
    assert type(result) is AgentToolResultMessage
    AgentToolResultMessage.__post_init__(result)
    if result.tool_name != call.tool_name:
        raise ValueError("tool result name must match the originating call")
    if result.provider_correlation_id != call.provider_correlation_id:
        raise ValueError("tool result correlation id must match the originating call")


def _revalidate_tool_policy_state(state: AgentToolPolicyState) -> None:
    AgentToolPolicyState(
        tool_budget=state.tool_budget,
        malformed_limit=state.malformed_limit,
        invocations_this_turn=state.invocations_this_turn,
        tool_invocation_count=state.tool_invocation_count,
        malformed_argument_count=state.malformed_argument_count,
        consecutive_malformed_streak=state.consecutive_malformed_streak,
        budget_exhausted_count=state.budget_exhausted_count,
    )


def _revalidate_pricing(pricing: AgentTokenPricing) -> None:
    AgentTokenPricing(
        input_per_million=pricing.input_per_million,
        output_per_million=pricing.output_per_million,
        reasoning_per_million=pricing.reasoning_per_million,
        cache_read_per_million=pricing.cache_read_per_million,
        cache_write_per_million=pricing.cache_write_per_million,
    )


def _validate_overlay_absent(
    history: tuple[AgentMessage, ...],
    active_input: AgentActiveInput,
) -> None:
    if any(
        message is overlay
        for message in history
        for overlay in active_input.request_overlay
    ):
        raise ValueError("request_overlay messages must not enter canonical history")


def _validate_usage_sample(sample: AgentProviderUsageSample) -> None:
    if any(
        getattr(sample, field_name) < 0 for field_name in sample.__dataclass_fields__
    ):
        raise ValueError("ProviderResult.usage counters must not be negative")


def _validate_json_mapping(value: object, field_name: str) -> None:
    if type(value) is not dict:
        raise TypeError(f"{field_name} must be an exact dict")
    _validate_json_value(value, field_name, set())


def _validate_json_value(value: object, field_name: str, seen: set[int]) -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain finite numbers")
        return
    if type(value) not in {dict, list}:
        raise TypeError(f"{field_name} must contain exact JSON values")
    identity = id(value)
    if identity in seen:
        raise ValueError(f"{field_name} must not contain cycles")
    seen.add(identity)
    try:
        if isinstance(value, dict):
            for key, child in value.items():
                if type(key) is not str:
                    raise TypeError(f"{field_name} keys must be exact strings")
                _validate_json_value(child, field_name, seen)
        else:
            assert isinstance(value, list)
            for child in value:
                _validate_json_value(child, field_name, seen)
    finally:
        seen.remove(identity)


def _validate_tool_definitions(definitions: object) -> tuple[ToolDefinition, ...]:
    if type(definitions) is not tuple:
        raise TypeError("tool definitions must be an exact tuple")
    if any(type(definition) is not ToolDefinition for definition in definitions):
        raise TypeError("tool definitions must contain exact ToolDefinition values")
    return definitions


def _require_protocol(value: object, protocol: type[object], field_name: str) -> None:
    if not isinstance(value, protocol):
        raise TypeError(f"{field_name} must implement its agent-loop protocol")
