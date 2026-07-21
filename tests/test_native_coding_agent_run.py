"""Contracts for the relocated agent-loop collaborator adapters.

Each adapter is a pure callable wrapper: it forwards its positional-only
arguments to the injected product callable unchanged, returns that callable's
value, and satisfies the matching runtime-checkable ``native.agent.loop``
protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop import (
    AgentLoopProviderTurn,
    AgentLoopRequestPreparation,
    AgentLoopRequestSource,
    AgentLoopStatusPolicy,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusAction,
    AgentProviderStatusDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.messages import AgentMessage, AgentUserMessage
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
)
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    AgentLoopStatusPolicyAdapter,
)
from pipy_harness.native.models import HarnessStatus, ProviderResult
from pipy_harness.native.tools.base import ToolDefinition


class _RecordingSink:
    def emit(self, event: object) -> None:  # pragma: no cover - never emitted here
        raise AssertionError("adapters must not emit events themselves")


def _accepted_message() -> AgentUserMessage:
    return AgentUserMessage(content=ProductContent("accepted prompt"))


def _tool_state() -> AgentToolPolicyState:
    return AgentToolPolicyState(tool_budget=5)


def _provider_result() -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="fake",
        model_id="fake-model",
        started_at=now,
        ended_at=now,
        final_text="response",
        tool_calls=(),
    )


def test_request_source_adapter_forwards_and_satisfies_protocol() -> None:
    seen: list[
        tuple[
            tuple[AgentMessage, ...],
            AgentActiveInput,
            int,
            tuple[ToolDefinition, ...],
        ]
    ] = []
    accepted = _accepted_message()
    active_input = AgentActiveInput(accepted)
    # The adapter never inspects the preparation or snapshot; use a sentinel so
    # the forwarding contract stays decoupled from unrelated constructor shape.
    preparation = cast(AgentLoopRequestPreparation, object())

    def prepare(
        history: tuple[AgentMessage, ...],
        loop_active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
    ) -> AgentLoopRequestPreparation:
        seen.append((history, loop_active_input, turn_index, available_tools))
        return preparation

    adapter = AgentLoopRequestSourceAdapter(prepare)

    assert isinstance(adapter, AgentLoopRequestSource)
    history: tuple[AgentMessage, ...] = (accepted,)
    tools: tuple[ToolDefinition, ...] = ()
    result = adapter.prepare(history, active_input, 3, tools)

    assert result is preparation
    assert seen == [(history, active_input, 3, tools)]


def test_provider_turn_adapter_forwards_and_satisfies_protocol() -> None:
    seen: list[tuple[AgentProviderRequestSnapshot, AgentEventSink, int]] = []
    outcome = ProviderTurnOutcome(result=_provider_result())
    snapshot = cast(AgentProviderRequestSnapshot, object())

    def complete(
        provider_snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
    ) -> ProviderTurnOutcome:
        seen.append((provider_snapshot, event_sink, turn_index))
        return outcome

    adapter = AgentLoopProviderTurnAdapter(complete)

    assert isinstance(adapter, AgentLoopProviderTurn)
    sink = _RecordingSink()
    result = adapter.complete(snapshot, sink, 7)

    assert result is outcome
    assert seen == [(snapshot, sink, 7)]


def test_status_policy_adapter_forwards_each_seam_and_satisfies_protocol() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def record(name: str) -> Callable[..., None]:
        def callback(*args: object) -> None:
            calls.append((name, args))

        return callback

    adapter = AgentLoopStatusPolicyAdapter(
        run_entered=record("run_entered"),
        input_accepted=record("input_accepted"),
        provider_result_observed=record("provider_result_observed"),
        provider_cancellation_observed=record("provider_cancellation_observed"),
        tool_policy_state_changed=record("tool_policy_state_changed"),
        provider_succeeded=record("provider_succeeded"),
        provider_failed=record("provider_failed"),
        no_tool_assistant=record("no_tool_assistant"),
        malformed_fatal=record("malformed_fatal"),
    )

    assert isinstance(adapter, AgentLoopStatusPolicy)

    tool_state = _tool_state()
    result = _provider_result()
    decision = AgentProviderStatusDecision(AgentProviderStatusAction.SUCCEEDED)
    failure = AgentFailure(
        error_type="malformed",
        message=ProductContent("bad arguments"),
    )
    reason = AgentCancellationReason.OPERATOR_ABORT

    adapter.run_entered()
    adapter.input_accepted()
    adapter.provider_result_observed(result)
    adapter.provider_cancellation_observed(reason)
    adapter.tool_policy_state_changed(tool_state)
    adapter.provider_succeeded(decision, tool_state)
    adapter.provider_failed(decision, tool_state)
    adapter.no_tool_assistant(tool_state)
    adapter.malformed_fatal(failure, tool_state)

    assert calls == [
        ("run_entered", ()),
        ("input_accepted", ()),
        ("provider_result_observed", (result,)),
        ("provider_cancellation_observed", (reason,)),
        ("tool_policy_state_changed", (tool_state,)),
        ("provider_succeeded", (decision, tool_state)),
        ("provider_failed", (decision, tool_state)),
        ("no_tool_assistant", (tool_state,)),
        ("malformed_fatal", (failure, tool_state)),
    ]
