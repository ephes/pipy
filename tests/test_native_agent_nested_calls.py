"""Fake composite runners pin T6a ownership without a guest or public tool."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import replace

import pytest
from test_native_agent_loop import (
    _EventSink,
    _provider_call,
    _provider_result,
    _ProviderTurn,
    _QueuedInputs,
    _RequestSource,
    _run_input,
    _StatusPolicy,
    _tool_result,
    _ToolPolicy,
    _Tools,
    _UsagePublisher,
)

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
    TurnCompleted,
)
from pipy_harness.native.agent.loop import AgentLoop
from pipy_harness.native.agent.loop_policy import AgentToolPolicyDecision
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_calls import (
    NestedCallStatus,
    NestedToolCallService,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.tools.base import ToolDefinition


class _NestedTools(_Tools):
    def definitions(
        self, allowed_names: Sequence[str] | None = None, /
    ) -> tuple[ToolDefinition, ...]:
        super().definitions(allowed_names)
        return tuple(
            ToolDefinition(name, "fixture", {"type": "object"})
            for name in ("codemode", "read", "write", "extension")
        )

    def eligible_names(self) -> frozenset[str]:
        return frozenset({"read", "write", "bash"})

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        if call.arguments_json.value == "malformed":
            self.executed.append(call)
            return ToolExecutionOutcome(
                _tool_result(call, "bad arguments", is_error=True),
                malformed_arguments=True,
            )
        return super().execute(
            call, output_sink=output_sink, wait_for_interrupt=wait_for_interrupt
        )


class _Policy(_ToolPolicy):
    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        super().before_execute(call)
        return AgentToolPolicyDecision(
            ProductContent("denied") if call.tool_name == "write" else None
        )

    def transform_result(
        self, call: AgentToolCall, result: AgentToolResultMessage, /
    ) -> ProductContent:
        super().transform_result(call, result)
        return ProductContent(result.content.value + " transformed")


class _Runner:
    def __init__(
        self,
        body: Callable[[AgentToolCall, NestedToolCallService], ToolExecutionOutcome],
    ) -> None:
        self.body = body
        self.services: list[NestedToolCallService] = []
        self.waiters: list[ToolInterruptWaiter | None] = []

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        self.services.append(service)
        self.waiters.append(tool_waiter)
        return self.body(call, service)


def _setup(
    runner: _Runner | None,
    *,
    names: tuple[str, ...] | None = None,
    tools: _NestedTools | None = None,
    policy: _ToolPolicy | None = None,
    parents: int = 1,
    tool_waiter: ToolInterruptWaiter | None = None,
    status: _StatusPolicy | None = None,
) -> tuple[AgentLoop, _NestedTools, _EventSink, list[str]]:
    order: list[str] = []
    tools = tools or _NestedTools(order)
    events = _EventSink(order)
    provider = _ProviderTurn(
        order,
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    calls=tuple(
                        _provider_call(f"parent{i}", name="codemode")
                        for i in range(parents)
                    )
                )
            ),
            ProviderTurnOutcome(result=_provider_result()),
        ],
    )
    loop = AgentLoop(
        request_source=_RequestSource(order, authorized_names=names),
        provider_turn=provider,
        tool_capabilities=tools,
        tool_policy=policy or _Policy(order),
        event_sink=events,
        usage_publisher=_UsagePublisher(order),
        queued_input_port=_QueuedInputs(),
        status_policy=status or _StatusPolicy(order),
        composite_runner=runner,
        nested_eligibility=tools,
        tool_waiter=tool_waiter,
    )
    return loop, tools, events, order


def test_nested_pipeline_statuses_budget_and_parent_only_records() -> None:
    seen = []

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        for name, args in [
            ("read", "{}"),
            ("write", "{}"),
            ("bash", "{}"),
            *[("read", "malformed")] * 3,
            ("read", "{}"),
            ("read", "{}"),
        ]:
            seen.append(service.call(name, args))
        assert service.call("codemode", "{}").status is NestedCallStatus.REFUSED
        assert service.call("extension", "{}").status is NestedCallStatus.REFUSED
        return ToolExecutionOutcome(_tool_result(call))

    runner = _Runner(run)
    loop, tools, events, order = _setup(runner)
    outcome = loop.run(_run_input(tool_budget=7, malformed_count=2, malformed_streak=2))
    assert [item.status for item in seen] == [
        NestedCallStatus.SETTLED,
        NestedCallStatus.BLOCKED,
        NestedCallStatus.UNAUTHORIZED,
        *[NestedCallStatus.MALFORMED] * 3,
        NestedCallStatus.BUDGET_EXHAUSTED,
        NestedCallStatus.BUDGET_EXHAUSTED,
    ]
    assert seen[0].result.content.value.endswith(" transformed")
    assert [call.provider_correlation_id for call in tools.executed] == [
        "parent0/1",
        "parent0/4",
        "parent0/5",
        "parent0/6",
    ]
    state = outcome.final_tool_state
    assert (
        state.tool_invocation_count,
        state.nested_malformed_count,
        state.budget_exhausted_count,
        state.invocations_this_turn,
        state.consecutive_malformed_streak,
        state.reserved_parent_slot,
    ) == (2, 3, 2, 7, 0, False)
    assert [
        m.provider_correlation_id
        for m in outcome.final_history
        if isinstance(m, AgentToolResultMessage)
    ] == ["parent0"]
    tool_events = [
        e
        for e in events.events
        if isinstance(e, (ToolCallStarted, ToolCallCompleted, ToolCallUpdated))
    ]
    assert len(tool_events) == 2
    assert [
        r.provider_correlation_id
        for e in events.events
        if isinstance(e, TurnCompleted)
        for r in e.tool_results
    ] == ["parent0"]
    assert order.count("tools:definitions") == 2
    assert "policy:before:parent0/1" in order and "policy:transform:parent0/1" in order
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


@pytest.mark.parametrize(
    "kind", ["success", "error", "exception", "malformed", "interrupt"]
)
def test_parent_uses_latest_child_state_and_releases(kind: str) -> None:
    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "{}")
        service.call("read", "malformed")
        if kind == "exception":
            raise RuntimeError("runner broke")
        return ToolExecutionOutcome(
            _tool_result(call, is_error=kind == "error"),
            malformed_arguments=kind == "malformed",
            interruption=ToolExecutionInterruption.OPERATOR_ABORT
            if kind == "interrupt"
            else ToolExecutionInterruption.SETTLED,
        )

    runner = _Runner(run)
    loop, _, _, _ = _setup(runner)
    outcome = loop.run(_run_input(tool_budget=5))
    state = outcome.final_tool_state
    assert state.nested_malformed_count == 1
    assert state.tool_invocation_count == (
        1 if kind in {"malformed", "interrupt"} else 2
    )
    assert state.invocations_this_turn == (
        2 if kind in {"malformed", "interrupt"} else 3
    )
    assert not state.reserved_parent_slot
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


def test_thread_refusal_record_bounds_and_fresh_parent() -> None:
    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        refused = []
        worker = threading.Thread(
            target=lambda: refused.append(service.call("read", "{}"))
        )
        worker.start()
        worker.join()
        assert refused[0].status is NestedCallStatus.REFUSED
        assert service.records() == ()
        for _ in range(300):
            service.call("read", "{}")
        assert len(service.records()) == 256
        snapshot = service.records()
        service.call("read", "{}")
        assert service.records() == snapshot
        return ToolExecutionOutcome(_tool_result(call))

    runner = _Runner(run)
    loop, _, _, _ = _setup(runner, parents=2)
    loop.run(_run_input(tool_budget=4))
    # Exhaustion means the second provider parent is not dispatched.
    assert len(runner.services) == 1
    runner = _Runner(lambda call, service: ToolExecutionOutcome(_tool_result(call)))
    loop, _, _, _ = _setup(runner, parents=2)
    loop.run(_run_input(tool_budget=4))
    assert len(runner.services) == 2 and runner.services[0] is not runner.services[1]


def test_disabled_port_executes_ordinary_codemode_extension() -> None:
    loop, tools, _, _ = _setup(None)
    outcome = loop.run(_run_input())
    assert [call.tool_name for call in tools.executed] == ["codemode"]
    assert outcome.final_tool_state.tool_invocation_count == 1


def test_canonical_nested_callback_failure_propagates_and_closes() -> None:
    runner = _Runner(
        lambda call, service: (
            service.call("read", "{}"),
            ToolExecutionOutcome(_tool_result(call)),
        )[1]
    )
    loop, _, _, _ = _setup(runner, policy=_ToolPolicy([], fail_transform=True))
    with pytest.raises(RuntimeError, match="postflight"):
        loop.run(_run_input())
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


@pytest.mark.parametrize("invalid", ["identity", "type"])
def test_invalid_runner_result_becomes_parent_error_and_closes(invalid: str) -> None:
    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "{}")
        if invalid == "identity":
            return ToolExecutionOutcome(
                _tool_result(AgentToolCall("other", "read", ProductContent("{}")))
            )
        return None  # type: ignore[return-value]

    runner = _Runner(run)
    loop, _, _, _ = _setup(runner)
    outcome = loop.run(_run_input())
    parent = next(
        m for m in outcome.final_history if isinstance(m, AgentToolResultMessage)
    )
    assert parent.is_error and "composite runner failed" in parent.content.value
    assert outcome.final_tool_state.tool_invocation_count == 2
    assert not outcome.final_tool_state.reserved_parent_slot
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


def test_nested_waiter_validation_and_callback_threads() -> None:
    session_thread = threading.current_thread()
    waiter_calls = []

    def waiter(
        done: threading.Event, cancel: threading.Event, /
    ) -> ToolExecutionInterruption:
        assert threading.current_thread() is session_thread
        waiter_calls.append(done)
        done.wait(1)
        return ToolExecutionInterruption.SETTLED

    # Real executor path, fake builtin: argument schema validation and waiter forwarding.
    from pathlib import Path

    from pipy_harness.native.tool_capabilities import (
        NativeToolCapabilities,
        ToolFilterOptions,
    )
    from pipy_harness.native.tools.base import (
        ToolContext,
        ToolExecutionResult,
        ToolRequest,
    )

    class Read:
        definition = ToolDefinition(
            "read",
            "fixture",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

        def invoke(
            self, request: ToolRequest, context: ToolContext
        ) -> ToolExecutionResult:
            return ToolExecutionResult(
                request.tool_request_id,
                "read text",
                provider_correlation_id=request.provider_correlation_id,
            )

    capabilities = NativeToolCapabilities(
        {"read": Read()},
        {},
        workspace_root=Path("/tmp"),
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    pinned = capabilities.snapshot_for_projection(capabilities.state)
    seen = []

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        seen.extend([service.call("read", '{"path":"x"}'), service.call("read", "{}")])
        return ToolExecutionOutcome(_tool_result(call))

    class PinnedTools(_NestedTools):
        def execute(
            self,
            call: AgentToolCall,
            *,
            output_sink: Callable[[str], None] | None = None,
            wait_for_interrupt: ToolInterruptWaiter | None = None,
        ) -> ToolExecutionOutcome:
            return pinned.execute(
                call, output_sink=output_sink, wait_for_interrupt=wait_for_interrupt
            )

    class ThreadPolicy(_Policy):
        def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
            assert threading.current_thread() is session_thread
            return super().before_execute(call)

        def transform_result(
            self, call: AgentToolCall, result: AgentToolResultMessage, /
        ) -> ProductContent:
            assert threading.current_thread() is session_thread
            return super().transform_result(call, result)

    runner = _Runner(run)
    loop, _, _, _ = _setup(
        runner, tools=PinnedTools([]), policy=ThreadPolicy([]), tool_waiter=waiter
    )
    outcome = loop.run(_run_input())
    assert [item.status for item in seen] == [
        NestedCallStatus.SETTLED,
        NestedCallStatus.MALFORMED,
    ]
    assert len(waiter_calls) == 2 and runner.waiters == [waiter]
    assert outcome.final_tool_state.nested_malformed_count == 1


def test_transient_records_bound_argument_bytes_and_drop_mutable_details() -> None:
    class DetailedTools(_NestedTools):
        def execute(
            self,
            call: AgentToolCall,
            *,
            output_sink: Callable[[str], None] | None = None,
            wait_for_interrupt: ToolInterruptWaiter | None = None,
        ) -> ToolExecutionOutcome:
            result = super().execute(
                call, output_sink=output_sink, wait_for_interrupt=wait_for_interrupt
            )
            return ToolExecutionOutcome(replace(result.result, details={"mutable": []}))

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "x" * 8193)
        assert service.records() == ()
        for _ in range(5):
            service.call("read", "x" * 8192)
        assert len(service.records()) == 4
        assert all(record.result.details is None for record in service.records())
        return ToolExecutionOutcome(_tool_result(call))

    runner = _Runner(run)
    loop, _, _, _ = _setup(runner, tools=DetailedTools([]))
    loop.run(_run_input(tool_budget=10))


def test_parent_block_and_unauthorized_never_dispatch() -> None:
    runner = _Runner(lambda call, service: ToolExecutionOutcome(_tool_result(call)))
    loop, tools, _, _ = _setup(runner, names=("read",))
    loop.run(_run_input())
    assert not runner.services and not tools.executed
    loop, tools, _, _ = _setup(runner, policy=_ToolPolicy([], blocked=True))
    loop.run(_run_input())
    assert not runner.services and not tools.executed


def test_child_interruption_closes_service_and_preserves_prior_counters() -> None:
    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        first = service.call("read", "{}")
        interrupted = service.call("read", "{}")
        assert first.status is NestedCallStatus.SETTLED
        assert interrupted.status is NestedCallStatus.INTERRUPTED
        assert service.call("read", "{}").status is NestedCallStatus.REFUSED
        return ToolExecutionOutcome(
            _tool_result(call),
            interruption=interrupted.interruption or ToolExecutionInterruption.SETTLED,
        )

    runner = _Runner(run)
    first_call = AgentToolCall("parent0/1", "read", ProductContent("{}"))
    second_call = AgentToolCall("parent0/2", "read", ProductContent("{}"))
    tools = _NestedTools(
        [],
        [
            ToolExecutionOutcome(_tool_result(first_call)),
            ToolExecutionOutcome(
                _tool_result(second_call, is_error=True),
                interruption=ToolExecutionInterruption.LOCAL_COMMAND,
            ),
        ],
    )
    loop, _, events, _ = _setup(runner, tools=tools)
    outcome = loop.run(_run_input())
    state = outcome.final_tool_state
    assert (
        state.tool_invocation_count,
        state.invocations_this_turn,
        state.reserved_parent_slot,
    ) == (1, 1, False)
    assert len([e for e in events.events if isinstance(e, ToolCallCompleted)]) == 1


def test_reentrant_call_is_refused_without_second_execution() -> None:
    service_holder: list[NestedToolCallService] = []

    class Policy(_Policy):
        def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
            if "/" in call.provider_correlation_id:
                assert (
                    service_holder[0].call("read", "{}").status
                    is NestedCallStatus.REFUSED
                )
            return super().before_execute(call)

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service_holder.append(service)
        assert service.call("read", "{}").status is NestedCallStatus.SETTLED
        assert len(service.records()) == 1
        return ToolExecutionOutcome(_tool_result(call))

    loop, tools, _, _ = _setup(_Runner(run), policy=Policy([]))
    loop.run(_run_input())
    assert len(tools.executed) == 1


def test_invalid_nested_result_propagates_and_closes() -> None:
    runner = _Runner(
        lambda call, service: (
            service.call("read", "{}"),
            ToolExecutionOutcome(_tool_result(call)),
        )[1]
    )
    tools = _NestedTools(
        [],
        [
            ToolExecutionOutcome(
                _tool_result(AgentToolCall("wrong", "read", ProductContent("{}")))
            )
        ],
    )
    loop, _, _, _ = _setup(runner, tools=tools)
    with pytest.raises(ValueError, match="correlation"):
        loop.run(_run_input())
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


def test_hidden_builtin_is_unauthorized_and_extension_replacement_refused() -> None:
    class EligibilityTools(_NestedTools):
        def eligible_names(self) -> frozenset[str]:
            return frozenset({"write"})

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        assert service.call("read", "{}").status is NestedCallStatus.REFUSED
        assert service.call("write", "{}").status is NestedCallStatus.UNAUTHORIZED
        return ToolExecutionOutcome(_tool_result(call))

    loop, tools, _, _ = _setup(
        _Runner(run), tools=EligibilityTools([]), names=("codemode", "read")
    )
    outcome = loop.run(_run_input())
    assert not tools.executed
    assert outcome.final_tool_state.invocations_this_turn == 2


@pytest.mark.parametrize(
    "interruption",
    [ToolExecutionInterruption.OPERATOR_ABORT, ToolExecutionInterruption.LOCAL_COMMAND],
)
def test_parent_preserves_child_interruption_ignored_by_runner(
    interruption: ToolExecutionInterruption,
) -> None:
    from pipy_harness.native.agent.results import AgentRunOutcome

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "{}")
        service.call("read", "{}")
        assert service.interruption is interruption
        return ToolExecutionOutcome(_tool_result(call))

    calls = [
        AgentToolCall(f"parent0/{i}", "read", ProductContent("{}")) for i in (1, 2)
    ]
    tools = _NestedTools(
        [],
        [
            ToolExecutionOutcome(_tool_result(calls[0])),
            ToolExecutionOutcome(_tool_result(calls[1]), interruption=interruption),
        ],
    )
    runner = _Runner(run)
    status = _StatusPolicy([])
    loop, _, events, order = _setup(runner, tools=tools, parents=2, status=status)
    outcome = loop.run(_run_input())
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert outcome.result.cancellation_reason is not None
    assert outcome.result.cancellation_reason.value == interruption.value
    assert len([entry for entry in order if entry.startswith("provider:")]) == 1
    assert len(runner.services) == 1
    results = [
        m for m in outcome.final_history if isinstance(m, AgentToolResultMessage)
    ]
    assert [r.provider_correlation_id for r in results] == ["parent0", "parent1"]
    assert "skipped" in results[1].content.value
    assert len([e for e in events.events if isinstance(e, ToolCallCompleted)]) == 1
    state = outcome.final_tool_state
    assert (
        state.tool_invocation_count,
        state.invocations_this_turn,
        state.reserved_parent_slot,
    ) == (1, 1, False)
    assert status.tool_states[-1] == state
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


@pytest.mark.parametrize("limit", ["count", "per_call", "total"])
def test_sticky_interruption_survives_evidence_limits(limit: str) -> None:
    from pipy_harness.native.agent.nested_calls import NestedToolCallOutcome

    interrupt = False

    def settle(call: AgentToolCall) -> NestedToolCallOutcome:
        return NestedToolCallOutcome(
            call,
            _tool_result(call),
            NestedCallStatus.INTERRUPTED if interrupt else NestedCallStatus.SETTLED,
            ToolExecutionInterruption.OPERATOR_ABORT if interrupt else None,
        )

    service = NestedToolCallService(
        AgentToolCall("parent", "codemode", ProductContent("{}")),
        frozenset({"read"}),
        settle,
        lambda call, text: _tool_result(call, text),
    )
    if limit == "count":
        for _ in range(256):
            service.call("read", "{}")
    elif limit == "total":
        for _ in range(4):
            service.call("read", "x" * 8192)
    snapshot = service.records()
    interrupt = True
    service.call("read", "x" * 8193 if limit == "per_call" else "{}")
    assert service.records() == snapshot
    assert service.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    failures = []

    def wrong_thread() -> None:
        try:
            _ = service.interruption
        except RuntimeError as exc:
            failures.append(exc)

    worker = threading.Thread(target=wrong_thread)
    worker.start()
    worker.join()
    assert len(failures) == 1
    with pytest.raises(AttributeError):
        service.interruption = None  # type: ignore[misc]


def test_child_counters_published_before_later_canonical_hook_failure() -> None:
    from test_native_coding_state import _state

    product_state = _state()
    session_thread = threading.current_thread()

    class Status(_StatusPolicy):
        def tool_policy_state_changed(self, state, /) -> None:
            assert threading.current_thread() is session_thread
            product_state.sync_tool_policy(state)
            super().tool_policy_state_changed(state)

    class Policy(_Policy):
        def transform_result(
            self, call: AgentToolCall, result: AgentToolResultMessage, /
        ) -> ProductContent:
            if call.provider_correlation_id == "parent0":
                raise RuntimeError("later postflight")
            return super().transform_result(call, result)

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "{}")
        service.call("read", "{}")
        return ToolExecutionOutcome(_tool_result(call))

    status = Status([])
    runner = _Runner(run)
    loop, _, _, _ = _setup(runner, parents=2, policy=Policy([]), status=status)
    with pytest.raises(RuntimeError, match="later postflight"):
        loop.run(_run_input(tool_budget=2))
    published = status.tool_states[-1]
    assert (
        published.tool_invocation_count,
        published.budget_exhausted_count,
        published.reserved_parent_slot,
    ) == (2, 1, False)
    assert any(
        s.tool_invocation_count == 1
        and s.budget_exhausted_count == 1
        and s.reserved_parent_slot
        for s in status.tool_states
    )

    snapshot = product_state.result_snapshot()
    assert (snapshot.tool_invocation_count, snapshot.budget_exhausted_count) == (2, 1)


def test_child_status_failure_propagates_instead_of_runner_error() -> None:
    class Status(_StatusPolicy):
        def tool_policy_state_changed(self, state, /) -> None:
            super().tool_policy_state_changed(state)
            if state.reserved_parent_slot:
                raise RuntimeError("child publication failed")

    runner = _Runner(
        lambda call, service: (
            service.call("read", "{}"),
            ToolExecutionOutcome(_tool_result(call)),
        )[1]
    )
    status = Status([])
    loop, _, _, _ = _setup(runner, status=status)
    with pytest.raises(RuntimeError, match="child publication failed"):
        loop.run(_run_input())
    assert not status.tool_states[-1].reserved_parent_slot
    assert runner.services[0].call("read", "{}").status is NestedCallStatus.REFUSED


@pytest.mark.parametrize("fault", ["acquisition", "validation"])
def test_reservation_fault_closes_service_without_publishing_invalid_state(
    monkeypatch, fault: str
) -> None:
    import pipy_harness.native.agent.loop as module

    services = []
    from pipy_harness.native.agent.loop_policy import reserve_composite_parent

    original_reserve = reserve_composite_parent

    def create(*args, **kwargs):
        service = NestedToolCallService(*args, **kwargs)
        services.append(service)
        return service

    def reserve(transition):
        if fault == "acquisition":
            raise ValueError("reservation fault")
        value = original_reserve(transition)
        object.__setattr__(value, "invocations_this_turn", value.tool_budget)
        return value

    monkeypatch.setattr(module, "NestedToolCallService", create)
    monkeypatch.setattr(module, "reserve_composite_parent", reserve)
    runner = _Runner(lambda call, service: ToolExecutionOutcome(_tool_result(call)))
    status = _StatusPolicy([])
    loop, _, _, _ = _setup(runner, status=status)
    with pytest.raises(ValueError):
        loop.run(_run_input())
    assert not runner.services
    assert services[0].call("read", "{}").status is NestedCallStatus.REFUSED
    assert all(
        not state.reserved_parent_slot and state.tool_invocation_count == 0
        for state in status.tool_states
    )
