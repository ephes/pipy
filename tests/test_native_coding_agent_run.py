"""Contracts for the coding-session agent-run collaborators.

The three adapters are pure callable wrappers: each forwards its positional-only
arguments to the injected product callable unchanged, returns that callable's
value, and satisfies the matching runtime-checkable ``native.agent.loop``
protocol. ``CodingAgentRunCoordinator`` assembles one canonical ``AgentLoop``
from those adapters and the composed reusable-loop ports, drives one accepted
turn, mirrors the final history into ``CodingSessionState``, and forwards the
loop's controller handoff to the input-queue retention seam.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

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
)
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentUsagePublication,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    AgentLoopStatusPolicyAdapter,
    CodingAgentRunCoordinator,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.models import (
    HarnessStatus,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.provider import StreamChunkSink
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


# ---------------------------------------------------------------------------
# CodingAgentRunCoordinator
# ---------------------------------------------------------------------------


class _FakeProvider:
    """A provider port the coordinator/loop path never invokes directly."""

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        raise AssertionError("the provider port is materialized by the product turn")


class _RequestSource:
    def __init__(self) -> None:
        self.histories: list[tuple[AgentMessage, ...]] = []

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
        /,
    ) -> AgentLoopRequestPreparation:
        self.histories.append(history)
        request = ProviderRequest(
            system_prompt="system",
            user_prompt=active_input.accepted_message.content.value,
            provider_name="fake",
            model_id="fake-model",
            cwd=Path("/coordinator-fixture"),
            messages=active_input.request_messages(history),
            available_tools=available_tools,
        )
        return AgentLoopRequestPreparation(
            history, snapshot_provider_request(request)
        )


class _ProviderTurn:
    def __init__(self, text: str = "assistant reply") -> None:
        self._text = text
        self.calls = 0

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome:
        del snapshot, event_sink, turn_index
        self.calls += 1
        now = datetime.now(UTC)
        return ProviderTurnOutcome(
            result=ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name="fake",
                model_id="fake-model",
                started_at=now,
                ended_at=now,
                final_text=self._text,
                tool_calls=(),
            )
        )


class _Tools:
    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        del allowed_names
        return ()

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:  # pragma: no cover - no tool calls in fixtures
        del call, output_sink, wait_for_interrupt
        raise AssertionError("no tool calls are produced")

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:  # pragma: no cover - no tool calls in fixtures
        return AgentToolResultMessage(
            tool_request_id=f"pipy-tool-{call.provider_correlation_id}",
            tool_name=call.tool_name,
            content=ProductContent(output_text),
            is_error=True,
            provider_correlation_id=call.provider_correlation_id,
        )


class _ToolPolicy:
    def before_execute(
        self,
        call: AgentToolCall,
        /,
    ) -> AgentToolPolicyDecision:  # pragma: no cover - no tool calls in fixtures
        del call
        return AgentToolPolicyDecision()

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent:  # pragma: no cover - no tool calls in fixtures
        del call
        return result.content


class _EventSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


class _UsagePublisher:
    def publish(self, publication: AgentUsagePublication) -> None:
        del publication


class _QueuedInputs:
    def __init__(self, value: AgentQueuedInput | None = None) -> None:
        self._value = value
        self.calls = 0

    def take_next(self) -> AgentQueuedInput | None:
        self.calls += 1
        return self._value


def _coding_state(
    messages: tuple[AgentMessage, ...] = (),
) -> CodingSessionState:
    return CodingSessionState(
        provider=_FakeProvider(),
        provider_name="fake",
        model_id="fake-model",
        usage_accumulator=AgentUsageAccumulator(),
        messages=messages,
    )


def _make_coordinator(
    *,
    coding_state: CodingSessionState,
    provider_turn: AgentLoopProviderTurn | None = None,
    request_source: AgentLoopRequestSource | None = None,
    queued_input_port: _QueuedInputs | None = None,
    retain_next_input: Callable[[AgentQueuedInput | None], None] | None = None,
    event_sink: AgentEventSink | None = None,
) -> CodingAgentRunCoordinator:
    return CodingAgentRunCoordinator(
        request_source=request_source or _RequestSource(),
        provider_turn=provider_turn or _ProviderTurn(),
        status_policy=_status_policy_noop(),
        tool_capabilities=_Tools(),
        tool_policy=_ToolPolicy(),
        event_sink=event_sink or _EventSink(),
        usage_publisher=_UsagePublisher(),
        queued_input_port=queued_input_port or _QueuedInputs(),
        coding_state=coding_state,
        retain_next_input=retain_next_input or (lambda value: None),
    )


def _status_policy_noop() -> AgentLoopStatusPolicyAdapter:
    def noop(*args: object) -> None:
        del args

    return AgentLoopStatusPolicyAdapter(
        run_entered=noop,
        input_accepted=noop,
        provider_result_observed=noop,
        provider_cancellation_observed=noop,
        tool_policy_state_changed=noop,
        provider_succeeded=noop,
        provider_failed=noop,
        no_tool_assistant=noop,
        malformed_fatal=noop,
    )


def _active_input() -> AgentActiveInput:
    return AgentActiveInput(AgentUserMessage(content=ProductContent("hello")))


def _initial_tool_state() -> AgentToolPolicyState:
    return AgentToolPolicyState(tool_budget=5)


def test_run_turn_builds_invokes_loop_and_mirrors_history() -> None:
    prior = AgentUserMessage(content=ProductContent("earlier turn"))
    state = _coding_state(messages=(prior,))
    request_source = _RequestSource()
    provider_turn = _ProviderTurn(text="assistant reply")
    coordinator = _make_coordinator(
        coding_state=state,
        request_source=request_source,
        provider_turn=provider_turn,
    )

    outcome = coordinator.run_turn(
        _active_input(),
        _initial_tool_state(),
        pricing=None,
        accepted_queued_input=None,
    )

    # The loop was assembled and driven exactly once, seeded from live history.
    assert provider_turn.calls == 1
    assert request_source.histories[0][0] is prior
    # The final history is mirrored back into the session state verbatim.
    assert state.messages == outcome.final_history
    assert state.messages[0] is prior
    assistant = state.messages[-1]
    assert isinstance(assistant, AgentAssistantMessage)
    assert assistant.content.value == "assistant reply"


def test_run_turn_retains_next_input_on_the_queue_seam() -> None:
    state = _coding_state()
    handoff = AgentQueuedInput(
        ProductContent("queued follow up"),
        AgentQueuedInputKind.FOLLOW_UP,
    )
    retained: list[AgentQueuedInput | None] = []
    coordinator = _make_coordinator(
        coding_state=state,
        queued_input_port=_QueuedInputs(handoff),
        retain_next_input=retained.append,
    )

    outcome = coordinator.run_turn(
        _active_input(),
        _initial_tool_state(),
        pricing=None,
        accepted_queued_input=None,
    )

    assert outcome.next_input is handoff
    assert retained == [handoff]


def test_run_turn_forwards_none_handoff_to_the_retention_seam() -> None:
    state = _coding_state()
    retained: list[AgentQueuedInput | None] = []
    coordinator = _make_coordinator(
        coding_state=state,
        queued_input_port=_QueuedInputs(None),
        retain_next_input=retained.append,
    )

    outcome = coordinator.run_turn(
        _active_input(),
        _initial_tool_state(),
        pricing=None,
        accepted_queued_input=None,
    )

    assert outcome.next_input is None
    assert retained == [None]


def test_constructor_rejects_non_exact_coding_state() -> None:
    class _StateSubclass(CodingSessionState):
        pass

    subclass_state = _StateSubclass(
        provider=_FakeProvider(),
        provider_name="fake",
        model_id="fake-model",
    )

    with pytest.raises(TypeError, match="exact CodingSessionState"):
        _make_coordinator(coding_state=subclass_state)

    with pytest.raises(TypeError, match="exact CodingSessionState"):
        _make_coordinator(coding_state=cast(CodingSessionState, object()))


def test_constructor_rejects_non_callable_retain_seam() -> None:
    with pytest.raises(TypeError, match="retain_next_input must be callable"):
        _make_coordinator(
            coding_state=_coding_state(),
            retain_next_input=cast(
                Callable[[AgentQueuedInput | None], None], object()
            ),
        )


def test_run_turn_rejects_non_conforming_ports() -> None:
    coordinator = _make_coordinator(
        coding_state=_coding_state(),
        event_sink=cast(AgentEventSink, object()),
    )

    with pytest.raises(TypeError, match="event_sink"):
        coordinator.run_turn(
            _active_input(),
            _initial_tool_state(),
            pricing=None,
            accepted_queued_input=None,
        )
