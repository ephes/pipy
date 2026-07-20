"""Headless characterization for one canonical provider/tool agent run."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.status import HarnessStatus
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AgentEvent,
    AgentRunCompleted,
    AssistantTextDelta,
    MessageCompleted,
    ProviderFailed,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.agent.loop import (
    AgentLoop,
    AgentLoopOutcome,
    AgentLoopRequestPreparation,
    AgentLoopRunInput,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusDecision,
    AgentToolPolicyDecision,
    AgentToolPolicyState,
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
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
    AgentRunOutcome,
    AgentTurnOutcome,
    AgentUsage,
)
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
    AgentRunEffect,
    AgentUsagePublication,
    AppendAgentMessage,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.tools.base import ToolDefinition


_TOOL = ToolDefinition(
    name="fixture",
    description="Headless fixture tool.",
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
)


class _StringSubclass(str):
    """Construct invalid provider/product values without unchecked typing."""


class _QueuedInputs:
    def __init__(
        self,
        values: Sequence[AgentQueuedInput] = (),
        *,
        order: list[str] | None = None,
    ) -> None:
        self._values = list(values)
        self._order = order
        self.calls = 0

    def take_next(self) -> AgentQueuedInput | None:
        self.calls += 1
        if self._order is not None:
            self._order.append("queue:take_next")
        return self._values.pop(0) if self._values else None


class _DatetimeSubclass(datetime):
    """Exercise exact datetime validation on provider-owned results."""


class _ProviderToolCallSubclass(ProviderToolCall):
    """Exercise exact nested tool-call validation."""


def _provider_result(
    text: str = "done",
    *,
    calls: tuple[ProviderToolCall, ...] = (),
    status: HarnessStatus = HarnessStatus.SUCCEEDED,
    usage: dict[str, object] | None = None,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=status,
        provider_name="fake",
        model_id="fake-model",
        started_at=now,
        ended_at=now,
        final_text=text if status is HarnessStatus.SUCCEEDED else None,
        usage=usage,
        error_type="FixtureProviderError"
        if status is not HarnessStatus.SUCCEEDED
        else None,
        error_message="provider failed"
        if status is not HarnessStatus.SUCCEEDED
        else None,
        tool_calls=calls,
    )


def _provider_call(
    correlation_id: str,
    *,
    name: str = "fixture",
    arguments: str = "{}",
) -> ProviderToolCall:
    return ProviderToolCall(correlation_id, name, arguments)


def _tool_result(
    call: AgentToolCall,
    content: str = "tool result",
    *,
    is_error: bool = False,
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{call.provider_correlation_id}",
        tool_name=call.tool_name,
        content=ProductContent(content),
        is_error=is_error,
        provider_correlation_id=call.provider_correlation_id,
    )


class _EventSink:
    def __init__(
        self, order: list[str], fail_on: type[AgentEvent] | None = None
    ) -> None:
        self.events: list[AgentEvent] = []
        self._order = order
        self._fail_on = fail_on

    def emit(self, event: AgentEvent) -> None:
        self._order.append(f"event:{type(event).__name__}")
        if self._fail_on is not None and isinstance(event, self._fail_on):
            raise RuntimeError("event callback failed")
        self.events.append(event)


class _EffectSink:
    def __init__(self, order: list[str], fail_after: int | None = None) -> None:
        self.effects: list[AgentRunEffect] = []
        self._order = order
        self._fail_after = fail_after

    def emit(self, effect: AgentRunEffect) -> None:
        self._order.append(f"effect:{type(effect).__name__}")
        if self._fail_after is not None and len(self.effects) == self._fail_after:
            raise RuntimeError("effect callback failed")
        self.effects.append(effect)


class _UsagePublisher:
    def __init__(self, order: list[str], *, fail: bool = False) -> None:
        self.publications: list[AgentUsagePublication] = []
        self._order = order
        self._fail = fail

    def publish(self, publication: AgentUsagePublication) -> None:
        self._order.append("usage:publish")
        if self._fail:
            raise RuntimeError("usage callback failed")
        self.publications.append(publication)


class _RequestSource:
    def __init__(
        self,
        order: list[str],
        *,
        authorized_names: tuple[str, ...] | None = None,
    ) -> None:
        self._order = order
        self._authorized_names = authorized_names
        self.histories: list[tuple[AgentMessage, ...]] = []

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
    ) -> AgentLoopRequestPreparation:
        self._order.append(f"request:{turn_index}")
        self.histories.append(history)
        request = ProviderRequest(
            system_prompt="system",
            user_prompt=active_input.accepted_message.content.value,
            provider_name="fake",
            model_id="fake-model",
            cwd=Path("/headless-fixture"),
            messages=active_input.request_messages(history),
            available_tools=available_tools,
        )
        snapshot = snapshot_provider_request(
            request,
            available_tool_names=self._authorized_names,
        )
        return AgentLoopRequestPreparation(history, snapshot)


class _ProviderTurn:
    def __init__(
        self,
        order: list[str],
        outcomes: Sequence[ProviderTurnOutcome],
        *,
        emit_delta: bool = False,
    ) -> None:
        self._order = order
        self._outcomes = list(outcomes)
        self._emit_delta = emit_delta
        self.calls = 0

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome:
        del snapshot
        self._order.append(f"provider:{turn_index}")
        self.calls += 1
        if self._emit_delta:
            event_sink.emit(AssistantTextDelta(turn_index, ProductContent("delta")))
        return self._outcomes.pop(0)


class _Tools:
    def __init__(
        self,
        order: list[str],
        outcomes: Sequence[ToolExecutionOutcome] = (),
    ) -> None:
        self._order = order
        self._outcomes = list(outcomes)
        self.executed: list[AgentToolCall] = []

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        del allowed_names
        self._order.append("tools:definitions")
        return (_TOOL,)

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        del wait_for_interrupt
        self._order.append(f"tool:execute:{call.provider_correlation_id}")
        self.executed.append(call)
        if output_sink is not None:
            output_sink("progress")
        if self._outcomes:
            return self._outcomes.pop(0)
        return ToolExecutionOutcome(_tool_result(call))

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        self._order.append(f"tool:error:{call.provider_correlation_id}")
        return _tool_result(call, output_text, is_error=True)


class _ToolPolicy:
    def __init__(
        self,
        order: list[str],
        *,
        blocked: bool = False,
        fail_before: bool = False,
        fail_transform: bool = False,
    ) -> None:
        self._order = order
        self._blocked = blocked
        self._fail_before = fail_before
        self._fail_transform = fail_transform
        self.before_calls = 0

    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        self._order.append(f"policy:before:{call.provider_correlation_id}")
        self.before_calls += 1
        if self._fail_before:
            raise RuntimeError("tool preflight failed")
        if self._blocked:
            return AgentToolPolicyDecision(ProductContent("fixture block"))
        return AgentToolPolicyDecision()

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent:
        self._order.append(f"policy:transform:{call.provider_correlation_id}")
        if self._fail_transform:
            raise RuntimeError("tool postflight failed")
        return result.content


class _StatusPolicy:
    def __init__(self, order: list[str], fail_on: str | None = None) -> None:
        self._order = order
        self._fail_on = fail_on
        self.tool_states: list[AgentToolPolicyState] = []

    def _record(self, label: str) -> None:
        self._order.append(f"status:{label}")
        if self._fail_on == label:
            raise RuntimeError(f"status {label} failed")

    def run_entered(self) -> None:
        self._record("run_entered")

    def input_accepted(self) -> None:
        self._record("input_accepted")

    def tool_policy_state_changed(self, state: AgentToolPolicyState) -> None:
        self.tool_states.append(state)
        self._record("tool_state")

    def provider_result_observed(self, result: ProviderResult) -> None:
        del result
        self._record("provider_result")

    def provider_cancellation_observed(
        self,
        reason: AgentCancellationReason,
    ) -> None:
        del reason
        self._record("provider_cancelled")

    def provider_succeeded(
        self,
        decision: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
    ) -> None:
        del decision, tool_state
        self._record("provider_succeeded")

    def provider_failed(
        self,
        decision: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
    ) -> None:
        del decision, tool_state
        self._record("provider_failed")

    def no_tool_assistant(self, tool_state: AgentToolPolicyState) -> None:
        del tool_state
        self._record("final_assistant")

    def malformed_fatal(
        self,
        failure: AgentFailure,
        tool_state: AgentToolPolicyState,
    ) -> None:
        del failure, tool_state
        self._record("loop_failed")


@dataclass(frozen=True)
class _FailureOverrides:
    event_sink: _EventSink | None = None
    effect_sink: _EffectSink | None = None
    usage_publisher: _UsagePublisher | None = None
    tool_policy: _ToolPolicy | None = None


def _make_loop(
    order: list[str],
    outcomes: Sequence[ProviderTurnOutcome],
    *,
    request_source: _RequestSource | None = None,
    tools: _Tools | None = None,
    tool_policy: _ToolPolicy | None = None,
    status_policy: _StatusPolicy | None = None,
    event_sink: _EventSink | None = None,
    effect_sink: _EffectSink | None = None,
    usage_publisher: _UsagePublisher | None = None,
    queued_input_port: AgentQueuedInputPort | None = None,
    emit_delta: bool = False,
) -> tuple[AgentLoop, _ProviderTurn, _EventSink, _EffectSink, _UsagePublisher]:
    provider_turn = _ProviderTurn(order, outcomes, emit_delta=emit_delta)
    events = event_sink or _EventSink(order)
    effects = effect_sink or _EffectSink(order)
    usage = usage_publisher or _UsagePublisher(order)
    loop = AgentLoop(
        request_source=request_source or _RequestSource(order),
        provider_turn=provider_turn,
        tool_capabilities=tools or _Tools(order),
        tool_policy=tool_policy or _ToolPolicy(order),
        event_sink=events,
        run_effect_sink=effects,
        usage_publisher=usage,
        queued_input_port=queued_input_port or _QueuedInputs(),
        status_policy=status_policy or _StatusPolicy(order),
    )
    return loop, provider_turn, events, effects, usage


def _run_input(
    *,
    tool_budget: int = 4,
    malformed_count: int = 0,
    malformed_streak: int = 0,
    delivery_kind: AgentQueuedInputKind | None = None,
    queued_content: str = "hello",
) -> AgentLoopRunInput:
    user = AgentUserMessage(ProductContent("hello"))
    return AgentLoopRunInput(
        history=(),
        active_input=AgentActiveInput(user),
        tool_policy_state=AgentToolPolicyState(
            tool_budget=tool_budget,
            malformed_argument_count=malformed_count,
            consecutive_malformed_streak=malformed_streak,
        ),
        pricing=AgentTokenPricing(1.0, 2.0, 3.0),
        accepted_queued_input=(
            AgentQueuedInput(ProductContent(queued_content), delivery_kind)
            if delivery_kind is not None
            else None
        ),
    )


def _event_names(events: Sequence[AgentEvent]) -> list[str]:
    return [type(event).__name__ for event in events]


def test_no_tool_success_is_headless_typed_and_strictly_ordered() -> None:
    order: list[str] = []
    status = _StatusPolicy(order)
    loop, provider, events, effects, usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
                )
            )
        ],
        status_policy=status,
        emit_delta=True,
    )

    outcome = loop.run(
        _run_input(
            delivery_kind=AgentQueuedInputKind.STEERING,
            queued_content="original queued input",
        )
    )

    assert type(outcome) is AgentLoopOutcome
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert outcome.result.usage == AgentUsage(
        input_tokens=5,
        output_tokens=2,
        cost_usd=9 / 1_000_000,
    )
    assert [message.content.value for message in outcome.final_history] == [
        "hello",
        "done",
    ]
    assert outcome.result.messages == outcome.final_history
    assert outcome.terminate_session is False
    assert provider.calls == 1
    assert _event_names(events.events) == [
        "AgentRunStarted",
        "SteeringConsumed",
        "TurnStarted",
        "MessageStarted",
        "MessageCompleted",
        "MessageStarted",
        "AssistantTextDelta",
        "MessageCompleted",
        "TurnCompleted",
        "AgentRunCompleted",
    ]
    consumed = next(
        event for event in events.events if isinstance(event, SteeringConsumed)
    )
    assert consumed.content == ProductContent("original queued input")
    assert [
        effect.message
        for effect in effects.effects
        if isinstance(effect, AppendAgentMessage)
    ] == list(outcome.final_history)
    assert len(usage.publications) == 1
    assert order.index("status:run_entered") < order.index("effect:AppendAgentMessage")
    assert order.index("effect:AppendAgentMessage") < order.index(
        "status:input_accepted"
    )
    assert order.index("status:provider_result") < order.index("usage:publish")
    last_message_completed = max(
        index for index, label in enumerate(order) if label == "event:MessageCompleted"
    )
    assert order.index("usage:publish") < order.index("status:provider_succeeded")
    assert order.index("status:provider_succeeded") < last_message_completed
    assert order.index("effect:AppendAgentMessage", 4) < order.index(
        "status:final_assistant"
    )
    assert order.index("status:final_assistant") < order.index("event:TurnCompleted")
    assert status.tool_states == [outcome.final_tool_state]
    assert order[-2:] == ["status:tool_state", "event:AgentRunCompleted"]


def test_completed_run_takes_exactly_one_next_input_after_terminal_event() -> None:
    order: list[str] = []
    next_input = AgentQueuedInput(
        ProductContent("next run"),
        AgentQueuedInputKind.FOLLOW_UP,
    )
    queued_inputs = _QueuedInputs((next_input,), order=order)
    loop, _provider, _events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        queued_input_port=queued_inputs,
    )

    outcome = loop.run(_run_input())

    assert outcome.next_input is next_input
    assert queued_inputs.calls == 1
    assert order[-2:] == ["event:AgentRunCompleted", "queue:take_next"]


def test_tool_cycle_runs_sequentially_and_carries_results_to_next_request() -> None:
    order: list[str] = []
    call = _provider_call("provider-1")
    tools = _Tools(order)
    request_source = _RequestSource(order)
    status = _StatusPolicy(order)
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(result=_provider_result("calling", calls=(call,))),
            ProviderTurnOutcome(result=_provider_result("finished")),
        ],
        request_source=request_source,
        tools=tools,
        status_policy=status,
    )

    outcome = loop.run(_run_input())

    assert provider.calls == 2
    assert len(request_source.histories) == 2
    assert isinstance(request_source.histories[1][-1], AgentToolResultMessage)
    assert outcome.final_tool_state.tool_invocation_count == 1
    assert outcome.final_tool_state.invocations_this_turn == 1
    assert _event_names(events.events).count("TurnStarted") == 2
    assert _event_names(events.events).count("ToolCallStarted") == 1
    assert _event_names(events.events).count("ToolCallUpdated") == 1
    assert _event_names(events.events).count("ToolCallCompleted") == 1
    assert order.index("policy:before:provider-1") < order.index(
        "tool:execute:provider-1"
    )
    assert order.index("tool:execute:provider-1") < order.index(
        "policy:transform:provider-1"
    )
    assert order.index("policy:transform:provider-1") < order.index(
        "event:ToolCallCompleted"
    )
    first_tool_state = order.index("status:tool_state")
    tool_result_effect = [
        index
        for index, label in enumerate(order)
        if label == "effect:AppendAgentMessage"
    ][2]
    assert first_tool_state + 1 == tool_result_effect
    assert status.tool_states[0].tool_invocation_count == 1


@pytest.mark.parametrize(
    ("authorized", "blocked", "expected_text", "expected_policy_calls"),
    [
        (False, False, "unknown tool: fixture", 0),
        (True, True, "blocked by extension: fixture block", 1),
    ],
)
def test_unauthorized_and_policy_blocked_calls_are_normal_error_results(
    authorized: bool,
    blocked: bool,
    expected_text: str,
    expected_policy_calls: int,
) -> None:
    order: list[str] = []
    request = _RequestSource(
        order,
        authorized_names=("fixture",) if authorized else (),
    )
    policy = _ToolPolicy(order, blocked=blocked)
    tools = _Tools(order)
    status = _StatusPolicy(order)
    loop, _provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result("calling", calls=(_provider_call("p1"),))
            ),
            ProviderTurnOutcome(result=_provider_result()),
        ],
        request_source=request,
        tools=tools,
        tool_policy=policy,
        status_policy=status,
    )

    outcome = loop.run(_run_input(tool_budget=2))

    result = next(
        event.result for event in events.events if isinstance(event, ToolCallCompleted)
    )
    assert result.is_error is True
    assert result.content.value == expected_text
    assert tools.executed == []
    assert policy.before_calls == expected_policy_calls
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert outcome.final_tool_state.invocations_this_turn == 1
    assert outcome.final_tool_state.tool_invocation_count == 0
    policy_result_effect = [
        index
        for index, label in enumerate(order)
        if label == "effect:AppendAgentMessage"
    ][2]
    assert policy_result_effect + 1 == order.index("status:tool_state")
    assert status.tool_states[0].invocations_this_turn == 1


def test_budget_exhaustion_is_an_error_observation_not_a_terminal_outcome() -> None:
    order: list[str] = []
    tools = _Tools(order)
    status = _StatusPolicy(order)
    loop, _provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling",
                    calls=(_provider_call("p1"), _provider_call("p2")),
                )
            ),
            ProviderTurnOutcome(result=_provider_result()),
        ],
        tools=tools,
        status_policy=status,
    )

    outcome = loop.run(_run_input(tool_budget=1))

    completed = [
        event.result for event in events.events if isinstance(event, ToolCallCompleted)
    ]
    assert completed[1].is_error is True
    assert completed[1].content.value == "tool budget exhausted (limit 1)"
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert outcome.terminate_session is False
    assert outcome.final_tool_state.budget_exhausted_count == 1
    assert len(tools.executed) == 1
    budget_state_index = next(
        index
        for index, state in enumerate(status.tool_states)
        if state.budget_exhausted_count == 1
    )
    budget_callback_indices = [
        index for index, label in enumerate(order) if label == "status:tool_state"
    ]
    assert budget_callback_indices[budget_state_index] < order.index("tool:error:p2")


def test_third_malformed_call_is_a_typed_fatal_loop_failure() -> None:
    order: list[str] = []
    call = AgentToolCall("p3", "fixture", ProductContent("{"))
    malformed = ToolExecutionOutcome(
        _tool_result(call, "invalid arguments", is_error=True),
        malformed_arguments=True,
    )
    tools = _Tools(order, [malformed])
    status = _StatusPolicy(order)
    queued_inputs = _QueuedInputs(
        (
            AgentQueuedInput(
                ProductContent("must not run"),
                AgentQueuedInputKind.STEERING,
            ),
        )
    )
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling", calls=(_provider_call("p3", arguments="{"),)
                )
            )
        ],
        tools=tools,
        status_policy=status,
        queued_input_port=queued_inputs,
    )

    outcome = loop.run(_run_input(tool_budget=2, malformed_count=2, malformed_streak=2))

    assert provider.calls == 1
    assert outcome.result.outcome is AgentRunOutcome.FAILED
    assert outcome.result.failure == AgentFailure(
        "NativeToolLoopMalformedFatal",
        ProductContent("3 consecutive malformed tool calls"),
    )
    assert outcome.terminate_session is True
    assert outcome.next_input is None
    assert queued_inputs.calls == 0
    assert outcome.final_tool_state.malformed_argument_count == 3
    assert outcome.final_tool_state.consecutive_malformed_streak == 3
    turn = next(event for event in events.events if isinstance(event, TurnCompleted))
    assert turn.outcome is AgentTurnOutcome.FAILED
    assert order.index("effect:AppendAgentMessage", 5) < order.index(
        "status:loop_failed"
    )
    assert order.index("status:loop_failed") < order.index("event:TurnCompleted")
    malformed_state_index = order.index("status:tool_state")
    malformed_result_effect = [
        index
        for index, label in enumerate(order)
        if label == "effect:AppendAgentMessage"
    ][2]
    assert malformed_state_index + 1 == malformed_result_effect
    assert status.tool_states[0].consecutive_malformed_streak == 3


def test_malformed_streak_spans_iterations_and_a_settled_call_resets_it() -> None:
    order: list[str] = []
    malformed_outcomes = [
        ToolExecutionOutcome(
            _tool_result(
                AgentToolCall(correlation_id, "fixture", ProductContent("{")),
                "invalid arguments",
                is_error=True,
            ),
            malformed_arguments=True,
        )
        for correlation_id in ("p1", "p2")
    ]
    settled_call = AgentToolCall("p3", "fixture", ProductContent("{}"))
    tools = _Tools(
        order,
        [*malformed_outcomes, ToolExecutionOutcome(_tool_result(settled_call))],
    )
    loop, provider, _events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling", calls=(_provider_call("p1", arguments="{"),)
                )
            ),
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling", calls=(_provider_call("p2", arguments="{"),)
                )
            ),
            ProviderTurnOutcome(
                result=_provider_result("calling", calls=(_provider_call("p3"),))
            ),
            ProviderTurnOutcome(result=_provider_result()),
        ],
        tools=tools,
    )

    outcome = loop.run(_run_input(tool_budget=2))

    assert provider.calls == 4
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert outcome.final_tool_state.malformed_argument_count == 2
    assert outcome.final_tool_state.consecutive_malformed_streak == 0
    assert outcome.final_tool_state.tool_invocation_count == 1
    assert outcome.final_tool_state.invocations_this_turn == 1


@pytest.mark.parametrize(
    "next_input",
    [
        None,
        AgentQueuedInput(
            ProductContent("retained follow-up"),
            AgentQueuedInputKind.FOLLOW_UP,
        ),
    ],
)
def test_provider_cancellation_polls_once_after_terminal_event(
    next_input: AgentQueuedInput | None,
) -> None:
    order: list[str] = []
    queued_inputs = _QueuedInputs(
        (next_input,) if next_input is not None else (),
        order=order,
    )
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        ],
        queued_input_port=queued_inputs,
    )

    outcome = loop.run(_run_input())

    assert provider.calls == 1
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert (
        outcome.result.cancellation_reason is AgentCancellationReason.PROVIDER_CANCELLED
    )
    assert outcome.terminate_session is False
    assert outcome.next_input is next_input
    assert queued_inputs.calls == 1
    assert usage.publications == []
    assert any(isinstance(event, RunCancelled) for event in events.events)
    assert isinstance(events.events[-3], MessageCompleted)
    assert isinstance(events.events[-2], TurnCompleted)
    assert events.events[-2].outcome is AgentTurnOutcome.CANCELLED
    assert isinstance(events.events[-1], AgentRunCompleted)
    assert order[-2:] == ["event:AgentRunCompleted", "queue:take_next"]
    assert order.index("event:RunCancelled") < order.index("status:provider_cancelled")
    assert order.index("status:provider_cancelled") < max(
        index for index, label in enumerate(order) if label == "event:MessageCompleted"
    )


@pytest.mark.parametrize(
    ("interruption", "reason"),
    [
        (
            ToolExecutionInterruption.OPERATOR_ABORT,
            AgentCancellationReason.OPERATOR_ABORT,
        ),
        (
            ToolExecutionInterruption.LOCAL_COMMAND,
            AgentCancellationReason.LOCAL_COMMAND,
        ),
    ],
)
def test_tool_interruption_appends_skipped_results_and_cancels_run(
    interruption: ToolExecutionInterruption,
    reason: AgentCancellationReason,
) -> None:
    order: list[str] = []
    first = AgentToolCall("p1", "fixture", ProductContent("{}"))
    tools = _Tools(
        order,
        [ToolExecutionOutcome(_tool_result(first), interruption=interruption)],
    )
    status = _StatusPolicy(order)
    loop, _provider, events, effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling",
                    calls=(_provider_call("p1"), _provider_call("p2")),
                )
            )
        ],
        tools=tools,
        status_policy=status,
    )

    outcome = loop.run(_run_input())

    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert outcome.result.cancellation_reason is reason
    completed = [
        event for event in events.events if isinstance(event, ToolCallCompleted)
    ]
    assert len(completed) == 1
    appended = [effect.message for effect in effects.effects]
    assert isinstance(appended[-1], AgentToolResultMessage)
    assert appended[-1].content.value == "tool skipped because the turn was interrupted"
    assert len(tools.executed) == 1
    assert any(isinstance(event, RunCancelled) for event in events.events)
    assert "status:provider_cancelled" not in order
    assert status.tool_states == [outcome.final_tool_state]
    assert status.tool_states[0].tool_invocation_count == 0
    assert status.tool_states[0].invocations_this_turn == 0
    assert order[-2:] == ["status:tool_state", "event:AgentRunCompleted"]


def test_provider_failure_publishes_usage_then_fails_without_retry() -> None:
    order: list[str] = []
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    status=HarnessStatus.FAILED,
                    usage={"input_tokens": 3, "total_tokens": 3},
                )
            )
        ],
    )

    outcome = loop.run(_run_input())

    assert provider.calls == 1
    assert len(usage.publications) == 1
    assert outcome.result.outcome is AgentRunOutcome.FAILED
    assert outcome.result.will_retry is False
    assert outcome.terminate_session is False
    assert outcome.provider_status is not None
    failed = next(event for event in events.events if isinstance(event, ProviderFailed))
    assert failed.will_retry is False
    assert not any(type(event).__name__.startswith("Retry") for event in events.events)
    assert order.index("usage:publish") < order.index("event:ProviderFailed")
    assert order.index("event:ProviderFailed") < order.index("status:provider_failed")
    assert order.index("status:provider_failed") < max(
        index for index, label in enumerate(order) if label == "event:MessageCompleted"
    )


def test_iteration_guard_falls_through_as_success_without_new_termination_policy() -> (
    None
):
    order: list[str] = []
    tool_budget = 1
    repeated = _provider_result(
        "again", calls=(_provider_call("missing", name="missing"),)
    )
    request = _RequestSource(order, authorized_names=())
    loop, provider, _events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=repeated) for _ in range(tool_budget + 2)],
        request_source=request,
    )

    outcome = loop.run(_run_input(tool_budget=tool_budget))

    assert provider.calls == tool_budget + 2
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert outcome.terminate_session is False


@pytest.mark.parametrize(
    "failure_point",
    [
        "run_entered",
        "input_accepted",
        "provider_result",
        "provider_succeeded",
        "final_assistant",
        "tool_state",
    ],
)
def test_status_callback_failure_propagates_before_later_work(
    failure_point: str,
) -> None:
    order: list[str] = []
    loop, _provider, _events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        status_policy=_StatusPolicy(order, fail_on=failure_point),
    )

    with pytest.raises(RuntimeError, match=f"status {failure_point} failed"):
        loop.run(_run_input())

    assert "event:AgentRunCompleted" not in order


def test_provider_cancellation_callback_failure_cuts_off_terminal_events() -> None:
    order: list[str] = []
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.PROVIDER_CANCELLED
            )
        ],
        status_policy=_StatusPolicy(order, fail_on="provider_cancelled"),
    )

    with pytest.raises(RuntimeError, match="status provider_cancelled failed"):
        loop.run(_run_input())

    assert provider.calls == 1
    assert usage.publications == []
    assert isinstance(events.events[-1], RunCancelled)
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events.events)


def test_provider_failed_callback_failure_cuts_off_terminal_events() -> None:
    order: list[str] = []
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result(status=HarnessStatus.FAILED))],
        status_policy=_StatusPolicy(order, fail_on="provider_failed"),
    )

    with pytest.raises(RuntimeError, match="status provider_failed failed"):
        loop.run(_run_input())

    assert provider.calls == 1
    assert len(usage.publications) == 1
    assert isinstance(events.events[-1], ProviderFailed)
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events.events)


def test_malformed_fatal_callback_failure_cuts_off_terminal_events() -> None:
    order: list[str] = []
    call = AgentToolCall("p3", "fixture", ProductContent("{"))
    tools = _Tools(
        order,
        [
            ToolExecutionOutcome(
                _tool_result(call, "invalid arguments", is_error=True),
                malformed_arguments=True,
            )
        ],
    )
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    "calling", calls=(_provider_call("p3", arguments="{"),)
                )
            )
        ],
        tools=tools,
        status_policy=_StatusPolicy(order, fail_on="loop_failed"),
    )

    with pytest.raises(RuntimeError, match="status loop_failed failed"):
        loop.run(_run_input(tool_budget=2, malformed_count=2, malformed_streak=2))

    assert provider.calls == 1
    assert isinstance(events.events[-1], ToolCallCompleted)
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events.events)


def test_event_effect_usage_and_tool_policy_callback_failures_propagate() -> None:
    factories: tuple[tuple[str, Callable[[list[str]], _FailureOverrides]], ...] = (
        (
            "event callback failed",
            lambda order: _FailureOverrides(
                event_sink=_EventSink(order, fail_on=TurnStarted)
            ),
        ),
        (
            "effect callback failed",
            lambda order: _FailureOverrides(
                effect_sink=_EffectSink(order, fail_after=0)
            ),
        ),
        (
            "usage callback failed",
            lambda order: _FailureOverrides(
                usage_publisher=_UsagePublisher(order, fail=True)
            ),
        ),
        (
            "tool preflight failed",
            lambda order: _FailureOverrides(
                tool_policy=_ToolPolicy(order, fail_before=True)
            ),
        ),
        (
            "tool postflight failed",
            lambda order: _FailureOverrides(
                tool_policy=_ToolPolicy(order, fail_transform=True)
            ),
        ),
    )
    for message, make_overrides in factories:
        order: list[str] = []
        needs_tool = "tool " in message
        result = (
            _provider_result("calling", calls=(_provider_call("p1"),))
            if needs_tool
            else _provider_result()
        )
        overrides = make_overrides(order)
        loop, _provider, _events, _effects, _usage = _make_loop(
            order,
            [ProviderTurnOutcome(result=result)],
            event_sink=overrides.event_sink,
            effect_sink=overrides.effect_sink,
            usage_publisher=overrides.usage_publisher,
            tool_policy=overrides.tool_policy,
        )

        with pytest.raises(RuntimeError, match=message):
            loop.run(_run_input())

        assert "event:AgentRunCompleted" not in order


@pytest.mark.parametrize(
    "invalid_surface",
    ["history", "active_input", "overlay_history", "delivery"],
)
def test_recursive_invalid_run_input_is_rejected_before_side_effects(
    invalid_surface: str,
) -> None:
    order: list[str] = []
    loop, provider, events, effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
    )
    run_input = _run_input()
    invalid_message = AgentUserMessage(
        ProductContent(_StringSubclass("invalid nested content"))
    )
    if invalid_surface == "history":
        object.__setattr__(run_input, "history", (invalid_message,))
    elif invalid_surface == "active_input":
        object.__setattr__(
            run_input,
            "active_input",
            AgentActiveInput(
                run_input.active_input.accepted_message,
                (invalid_message,),
            ),
        )
    elif invalid_surface == "overlay_history":
        overlay = AgentUserMessage(ProductContent("request only"))
        object.__setattr__(run_input, "history", (overlay,))
        object.__setattr__(
            run_input,
            "active_input",
            AgentActiveInput(run_input.active_input.accepted_message, (overlay,)),
        )
    else:
        object.__setattr__(
            run_input,
            "accepted_queued_input",
            AgentQueuedInput(
                invalid_message.content,
                AgentQueuedInputKind.FOLLOW_UP,
            ),
        )

    with pytest.raises((TypeError, ValueError)):
        loop.run(run_input)

    assert order == []
    assert provider.calls == 0
    assert events.events == []
    assert effects.effects == []
    assert usage.publications == []


@pytest.mark.parametrize(
    "invalid_semantics",
    [
        "tool_budget",
        "pricing",
        "accepted_content",
        "history_content",
        "accepted_in_overlay",
    ],
)
def test_semantically_mutated_run_input_is_rejected_before_side_effects(
    invalid_semantics: str,
) -> None:
    order: list[str] = []
    loop, provider, events, effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
    )
    run_input = _run_input()
    if invalid_semantics == "tool_budget":
        object.__setattr__(run_input.tool_policy_state, "tool_budget", 0)
    elif invalid_semantics == "pricing":
        assert run_input.pricing is not None
        object.__setattr__(run_input.pricing, "input_per_million", -1.0)
    elif invalid_semantics == "accepted_content":
        object.__setattr__(
            run_input.active_input.accepted_message.content,
            "value",
            "x" * (AgentUserMessage.CONTENT_MAX_LENGTH + 1),
        )
    elif invalid_semantics == "history_content":
        history_message = AgentAssistantMessage(ProductContent("prior"))
        object.__setattr__(
            history_message.content,
            "value",
            "x" * (AgentAssistantMessage.CONTENT_MAX_LENGTH + 1),
        )
        object.__setattr__(run_input, "history", (history_message,))
    else:
        object.__setattr__(
            run_input.active_input,
            "request_overlay",
            (run_input.active_input.accepted_message,),
        )

    with pytest.raises((TypeError, ValueError)):
        loop.run(run_input)

    assert order == []
    assert provider.calls == 0
    assert events.events == []
    assert effects.effects == []
    assert usage.publications == []


def test_recursive_invalid_prepared_history_stops_before_turn_and_provider() -> None:
    class _InvalidPreparedHistorySource(_RequestSource):
        def prepare(
            self,
            history: tuple[AgentMessage, ...],
            active_input: AgentActiveInput,
            turn_index: int,
            available_tools: tuple[ToolDefinition, ...],
        ) -> AgentLoopRequestPreparation:
            preparation = super().prepare(
                history,
                active_input,
                turn_index,
                available_tools,
            )
            invalid_assistant = AgentAssistantMessage(
                ProductContent(_StringSubclass("invalid prepared content"))
            )
            return AgentLoopRequestPreparation(
                (*preparation.history, invalid_assistant),
                preparation.snapshot,
            )

    order: list[str] = []
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        request_source=_InvalidPreparedHistorySource(order),
    )

    with pytest.raises(TypeError):
        loop.run(_run_input())

    assert provider.calls == 0
    assert not any(isinstance(event, TurnStarted) for event in events.events)
    assert "status:provider_result" not in order


@pytest.mark.parametrize("anchor_shape", ["missing", "duplicate"])
def test_prepared_history_requires_one_accepted_identity_before_provider(
    anchor_shape: str,
) -> None:
    class _InvalidAnchorSource(_RequestSource):
        def prepare(
            self,
            history: tuple[AgentMessage, ...],
            active_input: AgentActiveInput,
            turn_index: int,
            available_tools: tuple[ToolDefinition, ...],
        ) -> AgentLoopRequestPreparation:
            preparation = super().prepare(
                history,
                active_input,
                turn_index,
                available_tools,
            )
            accepted = active_input.accepted_message
            prepared_history = () if anchor_shape == "missing" else (accepted, accepted)
            return AgentLoopRequestPreparation(
                prepared_history,
                preparation.snapshot,
            )

    order: list[str] = []
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        request_source=_InvalidAnchorSource(order),
    )

    with pytest.raises(
        ValueError,
        match="accepted_message must occur exactly once",
    ):
        loop.run(_run_input())

    assert provider.calls == 0
    assert not any(isinstance(event, TurnStarted) for event in events.events)
    assert "status:provider_result" not in order


def test_prepared_history_rejects_request_overlay_before_provider() -> None:
    class _LeakingOverlaySource(_RequestSource):
        def prepare(
            self,
            history: tuple[AgentMessage, ...],
            active_input: AgentActiveInput,
            turn_index: int,
            available_tools: tuple[ToolDefinition, ...],
        ) -> AgentLoopRequestPreparation:
            preparation = super().prepare(
                history,
                active_input,
                turn_index,
                available_tools,
            )
            return AgentLoopRequestPreparation(
                (*preparation.history, *active_input.request_overlay),
                preparation.snapshot,
            )

    order: list[str] = []
    overlay = AgentUserMessage(ProductContent("request only"))
    run_input = _run_input()
    object.__setattr__(
        run_input,
        "active_input",
        AgentActiveInput(run_input.active_input.accepted_message, (overlay,)),
    )
    loop, provider, events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        request_source=_LeakingOverlaySource(order),
    )

    with pytest.raises(ValueError, match="request_overlay messages"):
        loop.run(run_input)

    assert provider.calls == 0
    assert not any(isinstance(event, TurnStarted) for event in events.events)


def test_invalid_provider_usage_semantics_stop_before_status_callback() -> None:
    order: list[str] = []
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result(usage={"input_tokens": -1}))],
    )

    with pytest.raises(ValueError):
        loop.run(_run_input())

    assert provider.calls == 1
    assert "status:provider_result" not in order
    assert usage.publications == []
    assert not any(isinstance(event, TurnCompleted) for event in events.events)


@pytest.mark.parametrize(
    "invalid_semantics",
    [
        "pending",
        "running",
        "oversized_final_text",
        "provider_mismatch",
        "model_mismatch",
    ],
)
def test_semantically_invalid_provider_result_stops_before_callbacks(
    invalid_semantics: str,
) -> None:
    if invalid_semantics == "pending":
        result = _provider_result(status=HarnessStatus.PENDING)
    elif invalid_semantics == "running":
        result = _provider_result(status=HarnessStatus.RUNNING)
    elif invalid_semantics == "oversized_final_text":
        result = _provider_result("x" * (AgentAssistantMessage.CONTENT_MAX_LENGTH + 1))
    elif invalid_semantics == "provider_mismatch":
        result = replace(_provider_result(), provider_name="other-provider")
    else:
        result = replace(_provider_result(), model_id="other-model")
    order: list[str] = []
    loop, provider, events, effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=result)],
    )

    with pytest.raises((TypeError, ValueError)):
        loop.run(_run_input())

    assert provider.calls == 1
    assert "status:provider_result" not in order
    assert "status:provider_succeeded" not in order
    assert "status:provider_failed" not in order
    assert usage.publications == []
    assert len(effects.effects) == 1
    assert not any(isinstance(event, ProviderFailed) for event in events.events)
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events.events)


@pytest.mark.parametrize(
    "invalid_result",
    [
        replace(
            _provider_result(),
            tool_calls=cast(
                tuple[ProviderToolCall, ...],
                [_provider_call("provider-list")],
            ),
        ),
        replace(_provider_result(), provider_name=_StringSubclass("fake")),
        replace(_provider_result(), model_id=_StringSubclass("fake-model")),
        replace(_provider_result(), final_text=_StringSubclass("done")),
        replace(_provider_result(), started_at=_DatetimeSubclass.now(UTC)),
        replace(
            _provider_result(),
            usage={"nested": [_StringSubclass("invalid usage")]},
        ),
        replace(
            _provider_result(),
            metadata={"nested": [_StringSubclass("invalid metadata")]},
        ),
        _provider_result(
            calls=(_ProviderToolCallSubclass("provider-id", "fixture", "{}"),)
        ),
        _provider_result(
            calls=(
                ProviderToolCall(
                    _StringSubclass("provider-id"),
                    "fixture",
                    "{}",
                ),
            )
        ),
        _provider_result(
            calls=(
                ProviderToolCall(
                    "provider-id",
                    _StringSubclass("fixture"),
                    "{}",
                ),
            )
        ),
        _provider_result(
            calls=(
                ProviderToolCall(
                    "provider-id",
                    "fixture",
                    _StringSubclass("{}"),
                ),
            )
        ),
    ],
)
def test_recursive_invalid_provider_result_stops_before_status_and_usage(
    invalid_result: ProviderResult,
) -> None:
    order: list[str] = []
    loop, provider, events, _effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=invalid_result)],
    )

    with pytest.raises(TypeError):
        loop.run(_run_input())

    assert provider.calls == 1
    assert "status:provider_result" not in order
    assert usage.publications == []
    assert not any(isinstance(event, ProviderFailed) for event in events.events)
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events.events)


def test_port_outputs_require_exact_runtime_values_before_publication() -> None:
    class _ProviderResultSubclass(ProviderResult):
        pass

    class _ToolResultSubclass(AgentToolResultMessage):
        pass

    class _InvalidDefinitionTools(_Tools):
        def definitions(
            self,
            allowed_names: Sequence[str] | None = None,
            /,
        ) -> tuple[ToolDefinition, ...]:
            del allowed_names
            return cast(tuple[ToolDefinition, ...], [_TOOL])

    class _InvalidPolicyResultTools(_Tools):
        def error_result(
            self,
            call: AgentToolCall,
            output_text: str,
            /,
        ) -> AgentToolResultMessage:
            del output_text
            return _ToolResultSubclass(
                tool_request_id=f"pipy-tool-{call.provider_correlation_id}",
                tool_name=call.tool_name,
                content=ProductContent("invalid subclass"),
                provider_correlation_id=call.provider_correlation_id,
                is_error=True,
            )

    order: list[str] = []
    loop, _provider, _events, _effects, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result())],
        tools=_InvalidDefinitionTools(order),
    )
    with pytest.raises(TypeError, match="tool definitions must be an exact tuple"):
        loop.run(_run_input())

    exact = _provider_result()
    subclass_result = _ProviderResultSubclass(
        status=exact.status,
        provider_name=exact.provider_name,
        model_id=exact.model_id,
        started_at=exact.started_at,
        ended_at=exact.ended_at,
        final_text=exact.final_text,
    )
    order = []
    loop, _provider, _events, _effects, usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=subclass_result)],
    )
    with pytest.raises(TypeError, match="exact ProviderResult"):
        loop.run(_run_input())
    assert usage.publications == []
    assert "status:provider_result" not in order

    order = []
    loop, _provider, events, _effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    calls=(_provider_call("unknown", name="unknown"),)
                )
            )
        ],
        tools=_InvalidPolicyResultTools(order),
    )
    with pytest.raises(TypeError, match="exact AgentToolResultMessage"):
        loop.run(_run_input())
    assert not any(isinstance(event, ToolCallCompleted) for event in events.events)


@pytest.mark.parametrize(
    "invalid_semantics",
    ["tool_name", "provider_correlation_id", "tool_request_id", "content"],
)
def test_invalid_executed_tool_result_stops_before_completion_and_effect(
    invalid_semantics: str,
) -> None:
    class _InvalidExecutionTools(_Tools):
        def execute(
            self,
            call: AgentToolCall,
            *,
            output_sink: Callable[[str], None] | None = None,
            wait_for_interrupt: ToolInterruptWaiter | None = None,
        ) -> ToolExecutionOutcome:
            del output_sink, wait_for_interrupt
            self._order.append(f"tool:execute:{call.provider_correlation_id}")
            self.executed.append(call)
            result = _tool_result(call)
            if invalid_semantics == "tool_name":
                object.__setattr__(result, "tool_name", "other-tool")
            elif invalid_semantics == "provider_correlation_id":
                object.__setattr__(result, "provider_correlation_id", "other-call")
            elif invalid_semantics == "tool_request_id":
                object.__setattr__(result, "tool_request_id", "provider-owned")
            else:
                object.__setattr__(
                    result.content,
                    "value",
                    "x" * (AgentToolResultMessage.CONTENT_MAX_LENGTH + 1),
                )
            return ToolExecutionOutcome(result)

    order: list[str] = []
    tools = _InvalidExecutionTools(order)
    loop, provider, events, effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(calls=(_provider_call("provider-1"),))
            )
        ],
        tools=tools,
    )

    with pytest.raises((TypeError, ValueError)):
        loop.run(_run_input())

    assert provider.calls == 1
    assert len(tools.executed) == 1
    assert not any(isinstance(event, ToolCallCompleted) for event in events.events)
    assert not any(
        isinstance(effect.message, AgentToolResultMessage)
        for effect in effects.effects
        if isinstance(effect, AppendAgentMessage)
    )
    assert not any(isinstance(event, TurnCompleted) for event in events.events)


def test_invalid_policy_error_result_stops_before_completion_and_effect() -> None:
    class _MismatchedErrorResultTools(_Tools):
        def error_result(
            self,
            call: AgentToolCall,
            output_text: str,
            /,
        ) -> AgentToolResultMessage:
            result = super().error_result(call, output_text)
            object.__setattr__(result, "provider_correlation_id", "other-call")
            return result

    order: list[str] = []
    tools = _MismatchedErrorResultTools(order)
    loop, provider, events, effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(calls=(_provider_call("provider-1"),))
            )
        ],
        request_source=_RequestSource(order, authorized_names=()),
        tools=tools,
    )

    with pytest.raises((TypeError, ValueError)):
        loop.run(_run_input())

    assert provider.calls == 1
    assert not any(isinstance(event, ToolCallCompleted) for event in events.events)
    assert not any(
        isinstance(effect.message, AgentToolResultMessage)
        for effect in effects.effects
        if isinstance(effect, AppendAgentMessage)
    )
    assert not any(isinstance(event, TurnCompleted) for event in events.events)


def test_invalid_skipped_tool_result_stops_before_effect_and_turn_completion() -> None:
    class _InvalidSkippedResultTools(_Tools):
        def error_result(
            self,
            call: AgentToolCall,
            output_text: str,
            /,
        ) -> AgentToolResultMessage:
            result = super().error_result(call, output_text)
            object.__setattr__(result, "tool_name", "other-tool")
            return result

    order: list[str] = []
    first = AgentToolCall("provider-1", "fixture", ProductContent("{}"))
    tools = _InvalidSkippedResultTools(
        order,
        [
            ToolExecutionOutcome(
                _tool_result(first),
                interruption=ToolExecutionInterruption.OPERATOR_ABORT,
            )
        ],
    )
    loop, provider, events, effects, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    calls=(
                        _provider_call("provider-1"),
                        _provider_call("provider-2"),
                    )
                )
            )
        ],
        tools=tools,
    )

    with pytest.raises((TypeError, ValueError)):
        loop.run(_run_input())

    assert provider.calls == 1
    completed = [
        event for event in events.events if isinstance(event, ToolCallCompleted)
    ]
    assert len(completed) == 1
    persisted_results = [
        effect.message
        for effect in effects.effects
        if isinstance(effect, AppendAgentMessage)
        and isinstance(effect.message, AgentToolResultMessage)
    ]
    assert persisted_results == [completed[0].result]
    assert not any(isinstance(event, TurnCompleted) for event in events.events)
