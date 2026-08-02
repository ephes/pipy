"""Characterization contracts for the reusable, UI-free tool executor."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentToolCall,
    ProductContent,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolExecutor,
)
from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "maxLength": 128}},
    "required": ["text"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class _FixtureTool:
    invoke_fn: Callable[[ToolRequest, ToolContext], ToolExecutionResult]
    name: str = "echo"
    requests: list[ToolRequest] = field(default_factory=list)
    contexts: list[ToolContext] = field(default_factory=list)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description="Exercise the reusable tool executor.",
            input_schema=_INPUT_SCHEMA,
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        self.requests.append(request)
        self.contexts.append(context)
        return self.invoke_fn(request, context)


def _call(
    arguments_json: str = '{"text":"hello"}',
    *,
    tool_name: str = "echo",
    correlation_id: str = "provider-call-1",
) -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id=correlation_id,
        tool_name=tool_name,
        arguments_json=ProductContent(arguments_json),
    )


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_root=tmp_path)


def _echo_result(
    request: ToolRequest,
    context: ToolContext,
) -> ToolExecutionResult:
    del context
    return ToolExecutionResult(
        tool_request_id=request.tool_request_id,
        output_text=str(request.arguments["text"]),
        provider_correlation_id=request.provider_correlation_id,
    )


def test_execute_validates_arguments_and_preserves_both_identity_domains(
    tmp_path: Path,
) -> None:
    tool = _FixtureTool(_echo_result)
    registry: Mapping[str, _FixtureTool] = MappingProxyType({"echo": tool})

    outcome = ToolExecutor(registry).execute(_call(), _context(tmp_path))

    assert outcome == ToolExecutionOutcome(
        result=outcome.result,
        malformed_arguments=False,
        interruption=ToolExecutionInterruption.SETTLED,
    )
    assert outcome.result.content == ProductContent("hello")
    assert outcome.result.is_error is False
    assert outcome.result.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX)
    assert outcome.result.provider_correlation_id == "provider-call-1"
    assert len(tool.requests) == 1
    assert tool.requests[0].arguments == {"text": "hello"}
    assert tool.requests[0].tool_request_id == outcome.result.tool_request_id
    assert tool.requests[0].provider_correlation_id == "provider-call-1"


@pytest.mark.parametrize(
    ("returned_correlation_id", "expected_correlation_id"),
    [(None, "provider-call-1"), ("tool-selected-id", "tool-selected-id")],
)
def test_execute_normalizes_output_and_provider_correlation(
    tmp_path: Path,
    returned_correlation_id: str | None,
    expected_correlation_id: str,
) -> None:
    chunks: list[str] = []

    def invoke(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        assert context.output_sink is not None
        context.output_sink("live-output")
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="final-output",
            is_error=True,
            provider_correlation_id=returned_correlation_id,
        )

    tool = _FixtureTool(invoke)
    outcome = ToolExecutor({"echo": tool}).execute(
        _call(),
        ToolContext(workspace_root=tmp_path, output_sink=chunks.append),
    )

    assert chunks == ["live-output"]
    assert outcome.result.content == ProductContent("final-output")
    assert outcome.result.is_error is True
    assert outcome.result.provider_correlation_id == expected_correlation_id


def test_error_result_builds_a_balanced_canonical_observation() -> None:
    call = _call(tool_name="missing", correlation_id="provider-missing")

    result = ToolExecutor({}).error_result(call, "expected error")

    assert result.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX)
    assert result.tool_name == "missing"
    assert result.content == ProductContent("expected error")
    assert result.is_error is True
    assert result.provider_correlation_id == "provider-missing"


def test_unknown_tool_is_a_malformed_error_observation(tmp_path: Path) -> None:
    outcome = ToolExecutor({}).execute(_call(tool_name="missing"), _context(tmp_path))

    assert outcome.malformed_arguments is True
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent("unknown tool: missing")
    assert outcome.interruption is ToolExecutionInterruption.SETTLED


def test_invalid_json_is_a_malformed_error_observation(tmp_path: Path) -> None:
    tool = _FixtureTool(_echo_result)

    outcome = ToolExecutor({"echo": tool}).execute(
        _call("{not json"), _context(tmp_path)
    )

    assert outcome.malformed_arguments is True
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent(
        "invalid arguments JSON: Expecting property name enclosed in double quotes"
    )
    assert tool.requests == []


def test_schema_error_is_a_malformed_error_observation(tmp_path: Path) -> None:
    tool = _FixtureTool(_echo_result)

    outcome = ToolExecutor({"echo": tool}).execute(_call("{}"), _context(tmp_path))

    assert outcome.malformed_arguments is True
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent(
        "echo: missing required argument(s): text"
    )
    assert tool.requests == []


def test_non_object_json_is_a_malformed_schema_observation(tmp_path: Path) -> None:
    tool = _FixtureTool(_echo_result)

    outcome = ToolExecutor({"echo": tool}).execute(_call("[]"), _context(tmp_path))

    assert outcome.malformed_arguments is True
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent("echo: expected object")
    assert tool.requests == []


def test_invoke_argument_error_is_a_malformed_error_observation(
    tmp_path: Path,
) -> None:
    def reject(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del request, context
        raise ToolArgumentError("echo", "fixture rejected invocation")

    tool = _FixtureTool(reject)
    outcome = ToolExecutor({"echo": tool}).execute(_call(), _context(tmp_path))

    assert outcome.malformed_arguments is True
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent("echo: fixture rejected invocation")


def test_unexpected_tool_exception_propagates(tmp_path: Path) -> None:
    def crash(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del request, context
        raise RuntimeError("fixture crashed")

    executor = ToolExecutor({"echo": _FixtureTool(crash)})

    with pytest.raises(RuntimeError, match="fixture crashed"):
        executor.execute(_call(), _context(tmp_path))


def test_output_sink_exception_propagates_on_synchronous_path(tmp_path: Path) -> None:
    def invoke(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        assert context.output_sink is not None
        context.output_sink("live output")
        return ToolExecutionResult(request.tool_request_id, "unreachable")

    def failing_sink(chunk: str) -> None:
        assert chunk == "live output"
        raise RuntimeError("sink failed")

    executor = ToolExecutor({"echo": _FixtureTool(invoke)})

    with pytest.raises(RuntimeError, match="sink failed"):
        executor.execute(
            _call(), ToolContext(workspace_root=tmp_path, output_sink=failing_sink)
        )


def test_active_output_sink_exception_propagates_from_worker(tmp_path: Path) -> None:
    def invoke(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        assert context.output_sink is not None
        context.output_sink("worker output")
        return ToolExecutionResult(request.tool_request_id, "unreachable")

    def failing_sink(chunk: str) -> None:
        assert chunk == "worker output"
        raise RuntimeError("worker sink failed")

    def wait_for_completion(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del cancel_event
        assert done_event.wait(timeout=1)
        return ToolExecutionInterruption.SETTLED

    executor = ToolExecutor({"echo": _FixtureTool(invoke)})

    with pytest.raises(RuntimeError, match="worker sink failed"):
        executor.execute(
            _call(),
            ToolContext(workspace_root=tmp_path, output_sink=failing_sink),
            wait_for_interrupt=wait_for_completion,
        )


def test_non_tool_execution_result_propagates_as_type_error(tmp_path: Path) -> None:
    def return_wrong_type(
        request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        del request, context
        return cast(ToolExecutionResult, object())

    executor = ToolExecutor({"echo": _FixtureTool(return_wrong_type)})

    with pytest.raises(
        TypeError, match="tool 'echo' returned non-ToolExecutionResult value"
    ):
        executor.execute(_call(), _context(tmp_path))


def test_no_waiter_executes_synchronously_without_injecting_cancellation(
    tmp_path: Path,
) -> None:
    tool = _FixtureTool(_echo_result)
    context = _context(tmp_path)

    outcome = ToolExecutor({"echo": tool}).execute(_call(), context)

    assert outcome.interruption is ToolExecutionInterruption.SETTLED
    assert len(tool.contexts) == 1
    assert tool.contexts[0] is not context
    assert tool.contexts[0] == context
    assert tool.contexts[0].cancel_event is None


def test_settled_waiter_receives_events_and_injects_cancel_event(
    tmp_path: Path,
) -> None:
    tool = _FixtureTool(_echo_result)
    seen_cancel_events: list[threading.Event] = []

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        assert done_event.wait(timeout=1)
        seen_cancel_events.append(cancel_event)
        return ToolExecutionInterruption.SETTLED

    outcome = ToolExecutor({"echo": tool}).execute(
        _call(), _context(tmp_path), wait_for_interrupt=waiter
    )

    assert outcome.interruption is ToolExecutionInterruption.SETTLED
    assert tool.contexts[0].cancel_event is seen_cancel_events[0]
    assert seen_cancel_events[0].is_set() is False


def test_waiter_keyboard_interrupt_signals_abort_and_returns_cancellation(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    cancellation_seen = threading.Event()

    def block(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            assert context.cancel_event is not None
            started.set()
            assert release.wait(timeout=1)
            if context.cancel_event.is_set():
                cancellation_seen.set()
            return ToolExecutionResult(request.tool_request_id, "late result")
        finally:
            finished.set()

    def interrupt(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        assert started.wait(timeout=1)
        assert done_event.is_set() is False
        assert cancel_event.is_set() is False
        raise KeyboardInterrupt

    executor = ToolExecutor(
        {"echo": _FixtureTool(block)}, cancel_join_timeout_seconds=0.01
    )
    try:
        outcome = executor.execute(
            _call(), _context(tmp_path), wait_for_interrupt=interrupt
        )
        assert outcome.interruption is ToolExecutionInterruption.OPERATOR_ABORT
        assert outcome.malformed_arguments is False
        assert outcome.result.is_error is True
        assert outcome.result.content == ProductContent("tool cancelled by escape")
    finally:
        release.set()
        assert finished.wait(timeout=1)
        assert cancellation_seen.is_set()


def test_invalid_waiter_result_cancels_worker_before_type_error(tmp_path: Path) -> None:
    started = threading.Event()
    finished = threading.Event()

    def finish_on_cancel(
        request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        assert context.cancel_event is not None
        started.set()
        assert context.cancel_event.wait(timeout=1)
        finished.set()
        return ToolExecutionResult(request.tool_request_id, "cancelled")

    def invalid_waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del done_event, cancel_event
        assert started.wait(timeout=1)
        return cast(ToolExecutionInterruption, "invalid")

    executor = ToolExecutor({"echo": _FixtureTool(finish_on_cancel)})

    with pytest.raises(
        TypeError, match="tool interrupt waiter must return ToolExecutionInterruption"
    ):
        executor.execute(_call(), _context(tmp_path), wait_for_interrupt=invalid_waiter)
    assert finished.is_set()


def test_waiter_system_exit_cancels_worker_then_propagates(tmp_path: Path) -> None:
    started = threading.Event()
    finished = threading.Event()

    def finish_on_cancel(
        request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        assert context.cancel_event is not None
        started.set()
        assert context.cancel_event.wait(timeout=1)
        finished.set()
        return ToolExecutionResult(request.tool_request_id, "cancelled")

    def exit_waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del done_event, cancel_event
        assert started.wait(timeout=1)
        raise SystemExit(7)

    executor = ToolExecutor({"echo": _FixtureTool(finish_on_cancel)})

    with pytest.raises(SystemExit) as raised:
        executor.execute(_call(), _context(tmp_path), wait_for_interrupt=exit_waiter)
    assert raised.value.code == 7
    assert finished.is_set()


@pytest.mark.parametrize(
    ("interruption", "expected_text"),
    [
        (
            ToolExecutionInterruption.OPERATOR_ABORT,
            "tool cancelled by escape",
        ),
        (
            ToolExecutionInterruption.LOCAL_COMMAND,
            "tool cancelled by local command",
        ),
    ],
)
def test_interruption_signals_tool_and_returns_balanced_cancellation(
    tmp_path: Path,
    interruption: ToolExecutionInterruption,
    expected_text: str,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    cancellation_seen = threading.Event()

    def block(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            assert context.cancel_event is not None
            started.set()
            assert release.wait(timeout=1)
            if context.cancel_event.is_set():
                cancellation_seen.set()
            return ToolExecutionResult(request.tool_request_id, "late result")
        finally:
            finished.set()

    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        assert started.wait(timeout=1)
        assert done_event.is_set() is False
        assert cancel_event.is_set() is False
        cancel_event.set()
        return interruption

    executor = ToolExecutor(
        {"echo": _FixtureTool(block)}, cancel_join_timeout_seconds=0.01
    )
    try:
        outcome = executor.execute(
            _call(), _context(tmp_path), wait_for_interrupt=waiter
        )
        assert outcome.interruption is interruption
        assert outcome.malformed_arguments is False
        assert outcome.result.is_error is True
        assert outcome.result.content == ProductContent(expected_text)
    finally:
        release.set()
        assert finished.wait(timeout=1)
        assert cancellation_seen.is_set()


@pytest.mark.parametrize(
    "interruption",
    [
        ToolExecutionInterruption.OPERATOR_ABORT,
        ToolExecutionInterruption.LOCAL_COMMAND,
    ],
)
def test_completed_worker_result_wins_while_interruption_is_preserved(
    tmp_path: Path,
    interruption: ToolExecutionInterruption,
) -> None:
    def waiter(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del cancel_event
        assert done_event.wait(timeout=1)
        return interruption

    outcome = ToolExecutor({"echo": _FixtureTool(_echo_result)}).execute(
        _call(), _context(tmp_path), wait_for_interrupt=waiter
    )

    assert outcome.result.content == ProductContent("hello")
    assert outcome.result.is_error is False
    assert outcome.malformed_arguments is False
    assert outcome.interruption is interruption


def test_cancellation_precedes_fast_worker_success_even_before_waiter_returns(
    tmp_path: Path,
) -> None:
    started = threading.Event()

    def finish_on_cancel(
        request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        assert context.cancel_event is not None
        started.set()
        assert context.cancel_event.wait(timeout=1)
        return ToolExecutionResult(request.tool_request_id, "racing success")

    def cancel_then_observe_completion(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        assert started.wait(timeout=1)
        cancel_event.set()
        assert done_event.wait(timeout=1)
        return ToolExecutionInterruption.OPERATOR_ABORT

    outcome = ToolExecutor({"echo": _FixtureTool(finish_on_cancel)}).execute(
        _call(),
        _context(tmp_path),
        wait_for_interrupt=cancel_then_observe_completion,
    )

    assert outcome.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent("tool cancelled by escape")


def test_cancellation_wins_over_racing_worker_exception(tmp_path: Path) -> None:
    started = threading.Event()
    raise_now = threading.Event()
    finished = threading.Event()

    def crash_after_release(
        request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        del request, context
        try:
            started.set()
            assert raise_now.wait(timeout=1)
            raise RuntimeError("racing worker failure")
        finally:
            finished.set()

    def cancel_after_worker_fails(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        assert started.wait(timeout=1)
        cancel_event.set()
        raise_now.set()
        assert done_event.wait(timeout=1)
        return ToolExecutionInterruption.OPERATOR_ABORT

    executor = ToolExecutor({"echo": _FixtureTool(crash_after_release)})

    outcome = executor.execute(
        _call(), _context(tmp_path), wait_for_interrupt=cancel_after_worker_fails
    )

    assert finished.is_set()
    assert outcome.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert outcome.malformed_arguments is False
    assert outcome.result.is_error is True
    assert outcome.result.content == ProductContent("tool cancelled by escape")


def test_abandoned_invocation_cannot_emit_into_the_next_call(tmp_path: Path) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    first_finished = threading.Event()
    active_call = ["A"]
    delivered: list[tuple[str, str]] = []

    def shared_sink(chunk: str) -> None:
        delivered.append((active_call[0], chunk))

    def invoke(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        assert context.output_sink is not None
        text = str(request.arguments["text"])
        if text == "A":
            first_started.set()
            assert release_first.wait(timeout=1)
            context.output_sink("late A")
            first_finished.set()
        else:
            release_first.set()
            assert first_finished.wait(timeout=1)
            context.output_sink("live B")
        return ToolExecutionResult(request.tool_request_id, f"result {text}")

    def interrupt_first(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del done_event, cancel_event
        assert first_started.wait(timeout=1)
        return ToolExecutionInterruption.OPERATOR_ABORT

    executor = ToolExecutor(
        {"echo": _FixtureTool(invoke)}, cancel_join_timeout_seconds=0.001
    )
    first = executor.execute(
        _call('{"text":"A"}'),
        ToolContext(workspace_root=tmp_path, output_sink=shared_sink),
        wait_for_interrupt=interrupt_first,
    )
    active_call[0] = "B"
    second = executor.execute(
        _call('{"text":"B"}', correlation_id="provider-call-2"),
        ToolContext(workspace_root=tmp_path, output_sink=shared_sink),
    )

    assert first.result.content == ProductContent("tool cancelled by escape")
    assert second.result.content == ProductContent("result B")
    assert delivered == [("B", "live B")]


def test_cancellation_does_not_wait_for_an_admitted_backpressured_sink(
    tmp_path: Path,
) -> None:
    sink_admitted = threading.Event()
    release_sink = threading.Event()
    worker_finished = threading.Event()
    execution_finished = threading.Event()
    delivered: list[str] = []
    outcomes: list[ToolExecutionOutcome] = []
    errors: list[BaseException] = []

    def blocking_sink(chunk: str) -> None:
        sink_admitted.set()
        assert release_sink.wait(timeout=1)
        delivered.append(chunk)

    def emit_twice(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            assert context.output_sink is not None
            context.output_sink("admitted")
            context.output_sink("late")
            return ToolExecutionResult(request.tool_request_id, "late result")
        finally:
            worker_finished.set()

    def interrupt_admitted_sink(
        done_event: threading.Event, cancel_event: threading.Event
    ) -> ToolExecutionInterruption:
        del done_event, cancel_event
        assert sink_admitted.wait(timeout=1)
        return ToolExecutionInterruption.OPERATOR_ABORT

    executor = ToolExecutor(
        {"echo": _FixtureTool(emit_twice)}, cancel_join_timeout_seconds=0.01
    )

    def run_execution() -> None:
        try:
            outcomes.append(
                executor.execute(
                    _call(),
                    ToolContext(workspace_root=tmp_path, output_sink=blocking_sink),
                    wait_for_interrupt=interrupt_admitted_sink,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            execution_finished.set()

    execution_thread = threading.Thread(target=run_execution, daemon=True)
    execution_thread.start()
    try:
        assert sink_admitted.wait(timeout=1)
        assert execution_finished.wait(timeout=0.2)
        assert delivered == []
    finally:
        release_sink.set()
        execution_thread.join(timeout=1)
        assert worker_finished.wait(timeout=1)

    assert errors == []
    assert outcomes[0].result.content == ProductContent("tool cancelled by escape")
    assert delivered == ["admitted"]


def test_successive_execute_calls_remain_strictly_sequential(tmp_path: Path) -> None:
    invocation_order: list[str] = []
    active_count = 0

    def record(request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        nonlocal active_count
        del context
        active_count += 1
        assert active_count == 1
        invocation_order.append(str(request.arguments["text"]))
        active_count -= 1
        return ToolExecutionResult(request.tool_request_id, "done")

    executor = ToolExecutor({"echo": _FixtureTool(record)})

    first = executor.execute(_call('{"text":"first"}'), _context(tmp_path))
    second = executor.execute(_call('{"text":"second"}'), _context(tmp_path))

    assert invocation_order == ["first", "second"]
    assert first.interruption is ToolExecutionInterruption.SETTLED
    assert second.interruption is ToolExecutionInterruption.SETTLED
