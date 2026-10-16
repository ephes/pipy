"""Nested calls follow the execution mode (Pi ``NestedToolCallRunner``).

A call is exclusive when the session is sequential or its tool's
``execution_mode`` is ``"sequential"``, unless it is made inside the call
that holds the exclusive queue. Exclusive calls run one at a time, in the
order they were asked for; other calls run beside them.
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pytest
from test_native_coding_session import _CollectingAgentEventSink
from test_native_extension_tool_metadata import _result, _StubProvider, _tool_results
from test_native_extension_tool_orchestration import (
    _STATUS,
    _message,
    _run_parent,
    _running_settle,
    _wait,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ToolCallCompleted,
)
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.agent.nested_calls import (
    NestedToolCallOutcome,
    NestedToolCallService,
    PendingNestedCall,
)
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools import ToolDefinition
from pipy_harness.native.tools.registry import production_tool_registry


def _service(
    settle, modes, *, sequential=None, queue_events=None
) -> NestedToolCallService:
    parent = AgentToolCall("p", "parent", ProductContent("{}"))
    tools = tuple(
        ToolDefinition(
            name=name,
            description=name,
            input_schema={"type": "object"},
            execution_mode=mode,
        )
        for name, mode in modes.items()
    )
    return NestedToolCallService(
        parent,
        frozenset(modes),
        settle,
        lambda call, text: _message(call, text, error=True),
        callable_tools=tools,
        sequential=sequential,
        queue_events=queue_events,
    )


class _QueueEvents:
    """Records the start and end events of queued calls."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.events: list[tuple[str, str, NestedToolCallOutcome | None]] = []
        self._fail_on = fail_on

    def queued(self, call) -> None:
        if self._fail_on == "queued":
            raise RuntimeError("start event failed")
        self.events.append(("queued", call.provider_correlation_id, None))

    def withdrawn(self, outcome) -> None:
        if self._fail_on == "withdrawn":
            raise RuntimeError("end event failed")
        call_id = outcome.nested_call.provider_correlation_id
        self.events.append(("withdrawn", call_id, outcome))


class _Active:
    """Counts the tools running at once, per tool name."""

    def __init__(self, **together: int) -> None:
        # Calls of these names wait until that many run at once.
        self._together = {
            name: threading.Barrier(parties, timeout=5)
            for name, parties in together.items()
        }
        self._lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.most: dict[str, int] = {}
        self.order: list[str] = []

    def run(self, call, nested_tools) -> str:
        name = call.tool_name
        with self._lock:
            self.order.append(call.provider_correlation_id)
            self.active[name] = self.active.get(name, 0) + 1
            total = sum(self.active.values())
            self.most[name] = max(self.most.get(name, 0), self.active[name])
            self.most["*"] = max(self.most.get("*", 0), total)
        barrier = self._together.get(name)
        if barrier is not None:
            barrier.wait()
        time.sleep(0.05)
        with self._lock:
            self.active[name] -= 1
        return "ok"


def _fan_out(names):
    """A handler that calls each of ``names`` from a thread of its own."""

    def handler(context):
        results: list[str] = []
        threads = [
            threading.Thread(
                target=lambda name=name: results.append(
                    context.nested_tools.execute(name, {}).content
                )
            )
            for name in names
        ]
        for thread in threads:
            thread.start()
            time.sleep(0.01)  # ask in this order
        for thread in threads:
            thread.join(10)
        return ",".join(results)

    return handler


def test_calls_of_a_sequential_tool_run_one_at_a_time() -> None:
    # Pi nested-tool-calls.test.ts "serializes concurrent calls to sequential
    # tools": maxActive {sequential: 1, parallel: 3}.
    active = _Active(parallel=3)
    service = _service(
        _running_settle(active.run), {"sequential": "sequential", "parallel": None}
    )

    first = _run_parent(_fan_out(["sequential"] * 3), service, _wait)
    second = _run_parent(_fan_out(["parallel"] * 3), service, _wait)

    assert first.result.content.value == "ok,ok,ok"
    assert second.result.content.value == "ok,ok,ok"
    assert active.most == {"sequential": 1, "parallel": 3, "*": 3}


def test_a_parallel_call_runs_beside_the_sequential_ones() -> None:
    # The sequential call holds the queue until a parallel call ran.
    parallel_ran = threading.Event()

    def execute(call, nested_tools):
        if call.tool_name == "sequential":
            return "sequential" if parallel_ran.wait(5) else "alone"
        parallel_ran.set()
        return "parallel"

    service = _service(
        _running_settle(execute), {"sequential": "sequential", "parallel": None}
    )

    outcome = _run_parent(_fan_out(["sequential", "parallel"]), service, _wait)

    assert sorted(outcome.result.content.value.split(",")) == [
        "parallel",
        "sequential",
    ]


def test_a_sequential_session_runs_every_nested_call_one_at_a_time() -> None:
    # Pi `isSequential()`: the agent executes tool calls sequentially; a
    # tool's own "parallel" does not override it.
    counters = [_Active()]
    mode = ["sequential"]
    service = _service(
        _running_settle(lambda call, tools: counters[0].run(call, tools)),
        {"parallel": "parallel", "plain": None},
        sequential=lambda: mode[0] == "sequential",
    )

    _run_parent(_fan_out(["parallel", "plain", "parallel"]), service, _wait)
    assert counters[0].most["*"] == 1

    # Read at each call, as Pi reads `agent.toolExecution`.
    mode[0] = "parallel"
    counters[0] = _Active(plain=3)
    _run_parent(_fan_out(["plain"] * 3), service, _wait)
    assert counters[0].most["plain"] == 3


def test_queued_calls_run_in_the_order_they_were_asked_for() -> None:
    active = _Active()
    service = _service(_running_settle(active.run), {"sequential": "sequential"})

    _run_parent(_fan_out(["sequential"] * 4), service, _wait)

    assert active.order == ["p/1", "p/2", "p/3", "p/4"]
    assert [r.nested_call.provider_correlation_id for r in service.records()] == [
        "p/1",
        "p/2",
        "p/3",
        "p/4",
    ]


def test_calls_inside_the_exclusive_call_do_not_wait_for_it() -> None:
    # Pi `holdsQueue`: a call made inside the call that holds the queue, at
    # any depth, does not wait on it; otherwise it would wait for itself.
    def execute(call, nested_tools):
        if call.tool_name == "outer":
            return "outer > " + nested_tools.execute("middle", {}).content
        if call.tool_name == "middle":
            return "middle > " + nested_tools.execute("outer_leaf", {}).content
        return "leaf"

    service = _service(
        _running_settle(execute),
        {"outer": "sequential", "middle": None, "outer_leaf": "sequential"},
    )

    outcome = _run_parent(
        lambda context: context.nested_tools.execute("outer", {}).content,
        service,
        _wait,
    )

    assert outcome.result.content.value == "outer > middle > leaf"
    assert [r.nested_call.provider_correlation_id for r in service.records()] == [
        "p/1",
        "p/1/1",
        "p/1/1/1",
    ]


def _blocking_settle(release: threading.Event, ran: list[str]):
    """A pipeline whose tool waits for ``release`` or for its cancellation."""

    def settle(call):
        def run(*, wait_for_interrupt, nested_tools, output_sink=None):
            ran.append(call.provider_correlation_id)
            done = threading.Event()
            cancel = threading.Event()

            def tool() -> None:
                while not (release.is_set() or cancel.is_set()):
                    time.sleep(0.01)
                done.set()

            threading.Thread(target=tool).start()
            reason = wait_for_interrupt(done, cancel)
            cancel.set()
            done.wait(5)
            if reason is ToolExecutionInterruption.SETTLED:
                return ToolExecutionOutcome(_message(call, "ran"))
            return ToolExecutionOutcome(
                _message(call, f"stopped: {reason.value}", error=True),
                cancelled=True,
            )

        def finish(execution):
            status = NestedCallStatus.CANCELLED if execution.cancelled else _STATUS
            return NestedToolCallOutcome(call, execution.result, status)

        return PendingNestedCall(call, run, finish)

    return settle


def test_a_queued_call_is_cancelled_without_running_when_its_parent_ends() -> None:
    release = threading.Event()
    ran: list[str] = []
    answers: list[str] = []
    asked = threading.Event()
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})

    def handler(context):
        def ask() -> None:
            answers.append(context.nested_tools.execute("sequential", {}).content)

        threading.Thread(target=ask).start()
        while not ran:
            time.sleep(0.01)
        threading.Thread(target=ask).start()
        asked.set()
        time.sleep(0.1)  # the second call waits for its turn
        return "returned"

    outcome = _run_parent(handler, service, _wait)
    release.set()

    assert outcome.result.content.value == "returned"
    assert ran == ["p/1"]
    records = service.records()
    assert [(r.nested_call.provider_correlation_id, r.status) for r in records] == [
        ("p/1", NestedCallStatus.CANCELLED),
        ("p/2", NestedCallStatus.CANCELLED),
    ]
    assert records[1].result.content.value == "tool cancelled by its caller"
    for _ in range(100):
        if len(answers) == 2:
            break
        time.sleep(0.02)
    assert sorted(answers) == ["stopped: caller_stop", "tool cancelled by its caller"]


def test_a_signal_set_while_a_call_waits_cancels_it_when_its_turn_comes() -> None:
    # Pi keeps a call whose signal aborted in the queue; when its turn comes
    # it does not run.
    release = threading.Event()
    ran: list[str] = []
    answers: list[str] = []
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})
    signal = threading.Event()

    def handler(context):
        first = threading.Thread(
            target=lambda: answers.append(
                context.nested_tools.execute("sequential", {}).content
            )
        )
        first.start()
        while not ran:
            time.sleep(0.01)
        second = threading.Thread(
            target=lambda: answers.append(
                context.nested_tools.execute("sequential", {}, signal=signal).content
            )
        )
        second.start()
        time.sleep(0.1)
        signal.set()
        release.set()
        first.join(5)
        second.join(5)
        return "returned"

    _run_parent(handler, service, _wait)

    assert ran == ["p/1"]
    assert answers == ["ran", "tool cancelled by its caller"]


def test_a_session_thread_call_waits_for_its_turn_and_serves_meanwhile() -> None:
    # A call on the session thread (``call``) cannot block it: it waits in
    # the queue while the session thread serves the call that holds it.
    release = threading.Event()
    ran: list[str] = []
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})
    stop = threading.Event()
    batch = service.batch(_wait, stop)
    settled: list[str] = []
    batch.start(
        "sequential", "{}", lambda result, outcome: settled.append(result.content)
    )
    threading.Timer(0.2, release.set).start()

    outcome = service.call("sequential", "{}")

    assert outcome.result.content.value == "ran"
    assert ran == ["p/1", "p/2"]
    assert settled == ["ran"]
    batch.end(ToolExecutionInterruption.SCRIPT_STOP)


def test_a_batch_runs_its_sequential_calls_one_at_a_time() -> None:
    # A codemode script's submitted calls (G3) follow the execution mode.
    active = _Active(parallel=2)
    service = _service(
        _running_settle(active.run), {"sequential": "sequential", "parallel": None}
    )
    stop = threading.Event()
    batch = service.batch(_wait, stop)
    settled: list[str] = []
    done = threading.Event()

    def note(result, outcome) -> None:
        settled.append(result.tool_call_id)
        if len(settled) == 5:
            done.set()

    for name in ("sequential", "parallel", "sequential", "parallel", "sequential"):
        batch.start(name, "{}", note)
    # ``pump`` also returns on any job (the codemode runner's wake).
    while not done.is_set():
        wake = threading.Event()
        assert batch.pump(wake, threading.Event()) is ToolExecutionInterruption.SETTLED
    batch.end(ToolExecutionInterruption.SCRIPT_STOP)

    assert active.most["sequential"] == 1
    assert active.most["parallel"] == 2
    assert [o for o in active.order if o in {"p/1", "p/3", "p/5"}] == [
        "p/1",
        "p/3",
        "p/5",
    ]
    assert [r.nested_call.provider_correlation_id for r in service.records()] == [
        "p/1",
        "p/2",
        "p/3",
        "p/4",
        "p/5",
    ]


def test_a_batch_end_cancels_the_calls_waiting_for_their_turn() -> None:
    release = threading.Event()
    ran: list[str] = []
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})
    stop = threading.Event()
    batch = service.batch(_wait, stop)
    settled: list[tuple[str, str]] = []
    for _ in range(3):
        batch.start(
            "sequential",
            "{}",
            lambda result, outcome: settled.append(
                (result.tool_call_id, result.content)
            ),
        )

    stop.set()
    batch.end(ToolExecutionInterruption.SCRIPT_STOP)
    release.set()

    assert ran == ["p/1"]
    assert sorted(settled) == [
        ("p/1", "stopped: script_stop"),
        ("p/2", "tool stopped by script backend"),
        ("p/3", "tool stopped by script backend"),
    ]
    assert [r.status for r in service.records()] == [NestedCallStatus.CANCELLED] * 3


def test_an_interruption_ends_the_running_call_and_the_queued_ones() -> None:
    release = threading.Event()
    ran: list[str] = []
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})
    stop = threading.Event()
    batch = service.batch(_wait, stop)
    for _ in range(2):
        batch.start("sequential", "{}", lambda result, outcome: None)

    batch.end(ToolExecutionInterruption.OPERATOR_ABORT)
    release.set()

    assert ran == ["p/1"]
    (first, second) = service.records()
    assert second.status is NestedCallStatus.INTERRUPTED
    assert second.interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert second.result.content.value == "tool cancelled by escape"
    assert service.interruption is ToolExecutionInterruption.OPERATOR_ABORT


def test_execution_mode_is_validated() -> None:
    with pytest.raises(ValueError, match="execution_mode"):
        ToolDefinition(
            name="t",
            description="t",
            input_schema={"type": "object"},
            execution_mode="serial",  # type: ignore[arg-type]
        )


_EXTENSION = """import json
import threading
import time
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolResult

LOG = Path(__file__).with_name('log.jsonl')
_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}
_LOCK = threading.Lock()
_STATE = {'active': 0, 'most': 0}


def _counter(ctx, params):
    with _LOCK:
        _STATE['active'] += 1
        _STATE['most'] = max(_STATE['most'], _STATE['active'])
    time.sleep(0.05)
    with _LOCK:
        _STATE['active'] -= 1
    return ToolResult(content='counted')


def _fan(ctx, params):
    results = []
    threads = [threading.Thread(
        target=lambda: results.append(ctx.execute_tool('counter', {}).result.content))
        for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    with LOG.open('a') as handle:
        handle.write(json.dumps(['most', _STATE['most']]) + '\\n')
    return ToolResult(content=','.join(results))


def activate(api):
    api.register_tool(ExtensionTool(
        name='counter', description='Shared state.', input_schema=_NONE,
        exposure='codemode', execution_mode='sequential', handler=_counter))
    api.register_tool(ExtensionTool(
        name='fan', description='Runs counter three times at once.',
        input_schema=_NONE, handler=_fan))
"""


def test_an_extension_tool_declares_sequential_execution(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi docs/extensions.md: "Use sequential execution when tools share
    # mutable in-memory state."
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "fan.py").write_text(_EXTENSION, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "fan", "{}"),)),
            _result(final_text="done"),
        ]
    )
    sink = _CollectingAgentEventSink()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=20,
        agent_event_sink=sink,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "counted,counted,counted"
    rows = [
        json.loads(line) for line in (directory / "log.jsonl").read_text().splitlines()
    ]
    assert rows == [["most", 1]]
    started = [e for e in sink.events if isinstance(e, NestedToolCallStarted)]
    assert [e.call.provider_correlation_id for e in started] == [
        "p1/1",
        "p1/2",
        "p1/3",
    ]
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    assert [c["status"] for c in completed.details["nestedCalls"]["calls"]] == [
        "ok"
    ] * 3


def test_queued_calls_end_as_the_interruption_that_closed_the_service() -> None:
    # A nested call interrupted by escape closes the service: the queued
    # calls end with that interruption, not as cancelled by their caller.
    release = threading.Event()
    ran: list[str] = []
    blocking = _blocking_settle(release, ran)

    def settle(call):
        if call.tool_name == "abort":
            return NestedToolCallOutcome(
                call,
                _message(call, "tool cancelled by escape", error=True),
                NestedCallStatus.INTERRUPTED,
                ToolExecutionInterruption.OPERATOR_ABORT,
            )
        return blocking(call)

    service = _service(settle, {"sequential": "sequential", "abort": None})
    batch = service.batch(_wait, threading.Event())
    for name in ("sequential", "sequential", "abort"):
        batch.start(name, "{}", lambda result, outcome: None)
    release.set()

    records = {r.nested_call.provider_correlation_id: r for r in service.records()}
    assert ran == ["p/1"]
    assert records["p/2"].status is NestedCallStatus.INTERRUPTED
    assert records["p/2"].interruption is ToolExecutionInterruption.OPERATOR_ABORT
    assert records["p/2"].result.content.value == "tool cancelled by escape"


def test_every_queued_call_is_answered_when_one_answer_fails() -> None:
    release = threading.Event()
    ran: list[str] = []
    service = _service(_blocking_settle(release, ran), {"sequential": "sequential"})
    batch = service.batch(_wait, threading.Event())
    answered: list[str] = []

    def failing(result, outcome) -> None:
        answered.append(result.tool_call_id)
        raise RuntimeError("callback broke")

    batch.start("sequential", "{}", lambda result, outcome: None)
    batch.start("sequential", "{}", failing)
    batch.start(
        "sequential", "{}", lambda result, outcome: answered.append(result.tool_call_id)
    )

    with pytest.raises(RuntimeError, match="callback broke"):
        batch.end(ToolExecutionInterruption.SCRIPT_STOP)
    release.set()

    assert answered == ["p/2", "p/3"]


def test_a_failing_fallback_answer_does_not_stop_the_others() -> None:
    # The cancellation result cannot be made, and the fallback answer's
    # callback raises too: the later call is still answered, and the first
    # failure is the one raised.
    release = threading.Event()
    ran: list[str] = []
    broken = [False]
    parent = AgentToolCall("p", "parent", ProductContent("{}"))

    def error_result(call, text):
        if broken[0]:
            raise ValueError("no error result")
        return _message(call, text, error=True)

    service = NestedToolCallService(
        parent,
        frozenset({"sequential"}),
        _blocking_settle(release, ran),
        error_result,
        callable_tools=(
            ToolDefinition(
                name="sequential",
                description="s",
                input_schema={"type": "object"},
                execution_mode="sequential",
            ),
        ),
    )
    batch = service.batch(_wait, threading.Event())
    answered: list[str] = []

    def failing(result, outcome) -> None:
        raise RuntimeError("callback broke")

    batch.start("sequential", "{}", lambda result, outcome: None)
    batch.start("sequential", "{}", failing)
    batch.start(
        "sequential", "{}", lambda result, outcome: answered.append(result.content)
    )
    broken[0] = True

    with pytest.raises(ValueError, match="no error result"):
        service._withdraw(lambda turn: True, ToolExecutionInterruption.SCRIPT_STOP)
    release.set()

    assert len(answered) == 1


def test_a_queued_call_emits_its_start_when_queued_and_its_end_when_withdrawn() -> None:
    # Pi nested-tool-calls.ts emits `tool_execution_start` before it awaits
    # the exclusive queue. A queued call whose turn never comes (pipy
    # cancels it when its caller returns) ends with its end event.
    release = threading.Event()
    ran: list[str] = []
    events = _QueueEvents()
    service = _service(
        _blocking_settle(release, ran),
        {"sequential": "sequential"},
        queue_events=events,
    )
    stop = threading.Event()
    batch = service.batch(_wait, stop)
    for _ in range(2):
        batch.start("sequential", "{}", lambda result, outcome: None)

    # The first runs; the second waits and has started already.
    assert ran == ["p/1"]
    assert [(kind, call_id) for kind, call_id, _ in events.events] == [
        ("queued", "p/2")
    ]
    time.sleep(0.05)
    stop.set()
    batch.end(ToolExecutionInterruption.SCRIPT_STOP)
    release.set()

    assert [(kind, call_id) for kind, call_id, _ in events.events] == [
        ("queued", "p/2"),
        ("withdrawn", "p/2"),
    ]
    withdrawn = events.events[1][2]
    assert withdrawn is not None
    assert withdrawn.status is NestedCallStatus.CANCELLED
    # Its duration runs from its start, when it was queued.
    assert withdrawn.duration_seconds >= 0.05
    (record,) = [
        r for r in service.records() if r.nested_call.provider_correlation_id == "p/2"
    ]
    assert record.duration_seconds == withdrawn.duration_seconds


def test_a_queued_call_whose_turn_comes_has_no_second_queue_event() -> None:
    events = _QueueEvents()
    active = _Active()
    service = _service(
        _running_settle(active.run), {"sequential": "sequential"}, queue_events=events
    )

    outcome = _run_parent(_fan_out(["sequential"] * 2), service, _wait)

    assert outcome.result.content.value == "ok,ok"
    assert [(kind, call_id) for kind, call_id, _ in events.events] == [
        ("queued", "p/2")
    ]


@pytest.mark.parametrize("fail_on", ["queued", "withdrawn"])
def test_a_failing_queue_event_fails_the_pipeline_and_answers_the_caller(
    fail_on: str,
) -> None:
    release = threading.Event()
    ran: list[str] = []
    service = _service(
        _blocking_settle(release, ran),
        {"sequential": "sequential"},
        queue_events=_QueueEvents(fail_on=fail_on),
    )
    batch = service.batch(_wait, threading.Event())
    answers: list[str] = []
    batch.start("sequential", "{}", lambda result, outcome: None)

    with pytest.raises(RuntimeError, match="event failed"):
        batch.start(
            "sequential",
            "{}",
            lambda result, outcome: answers.append(result.content),
        )
        batch.end(ToolExecutionInterruption.SCRIPT_STOP)
    release.set()

    assert answers == ["Nested tool calls are not available in this context"]
    with pytest.raises(RuntimeError, match="event failed"):
        service.raise_pipeline_failure()
    assert service.durable_record()["complete"] is False


_QUEUE_EXTENSION = """import threading
import time
from pipy_harness.extensions import ExtensionTool, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}
_RUNNING = threading.Event()
_RELEASE = threading.Event()


def _gate(ctx, params):
    _RUNNING.set()
    while not _RELEASE.wait(0.01):
        if ctx.signal is not None and ctx.signal.is_set():
            return ToolResult(content='stopped')
    return ToolResult(content='passed')


def _two(ctx, params, *, wait):
    _RUNNING.clear()
    _RELEASE.clear()
    results = []
    first = threading.Thread(
        target=lambda: results.append(ctx.execute_tool('gate', {}).result.content))
    second = threading.Thread(
        target=lambda: results.append(ctx.execute_tool('gate', {}).result.content))
    first.start()
    _RUNNING.wait(5)
    second.start()
    time.sleep(0.3)  # the second call is queued behind the first
    if not wait:
        return ToolResult(content='left')
    _RELEASE.set()
    first.join(10)
    second.join(10)
    return ToolResult(content=','.join(results))


def activate(api):
    api.register_tool(ExtensionTool(
        name='gate', description='Waits.', input_schema=_NONE,
        exposure='codemode', execution_mode='sequential', handler=_gate))
    api.register_tool(ExtensionTool(
        name='both', description='Runs gate twice.', input_schema=_NONE,
        handler=lambda ctx, params: _two(ctx, params, wait=True)))
    api.register_tool(ExtensionTool(
        name='leave', description='Leaves gate queued.', input_schema=_NONE,
        handler=lambda ctx, params: _two(ctx, params, wait=False)))
"""


def _queue_session(tmp_path: Path, monkeypatch, tool: str):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True)
    (directory / "queue.py").write_text(_QUEUE_EXTENSION, encoding="utf-8")
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", tool, "{}"),)),
            _result(final_text="done"),
        ]
    )
    sink = _CollectingAgentEventSink()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=20,
        agent_event_sink=sink,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED
    lifecycle = [
        ("start" if isinstance(e, NestedToolCallStarted) else "end", e)
        for e in sink.events
        if isinstance(e, (NestedToolCallStarted, NestedToolCallCompleted))
    ]
    return provider, sink, lifecycle


def _order(lifecycle) -> list[tuple[str, str]]:
    return [(kind, e.call.provider_correlation_id) for kind, e in lifecycle]


def test_a_queued_call_starts_before_the_call_ahead_of_it_ends(
    tmp_path: Path, monkeypatch
) -> None:
    provider, _, lifecycle = _queue_session(tmp_path, monkeypatch, "both")

    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "passed,passed"
    # One start and one end for each call; the second, queued, starts at once.
    assert _order(lifecycle) == [
        ("start", "p1/1"),
        ("start", "p1/2"),
        ("end", "p1/1"),
        ("end", "p1/2"),
    ]
    ends = {e.call.provider_correlation_id: e for kind, e in lifecycle if kind == "end"}
    # Its record's duration includes the wait for its turn, as Pi's does.
    assert ends["p1/2"].duration_seconds >= ends["p1/2"].execution_seconds
    assert ends["p1/2"].duration_seconds >= 0.3


def test_a_withdrawn_queued_call_has_a_start_and_an_end(
    tmp_path: Path, monkeypatch
) -> None:
    provider, sink, lifecycle = _queue_session(tmp_path, monkeypatch, "leave")

    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "left"
    order = _order(lifecycle)
    assert sorted(order) == [
        ("end", "p1/1"),
        ("end", "p1/2"),
        ("start", "p1/1"),
        ("start", "p1/2"),
    ]
    assert order.index(("start", "p1/2")) < order.index(("end", "p1/2"))
    assert order.index(("start", "p1/2")) < order.index(("end", "p1/1"))
    (withdrawn,) = [
        e
        for kind, e in lifecycle
        if kind == "end" and e.call.provider_correlation_id == "p1/2"
    ]
    assert withdrawn.status is NestedCallStatus.CANCELLED
    assert withdrawn.execution_seconds is None
    assert withdrawn.result.content.value == "tool cancelled by its caller"
    # JSON and RPC modes: a start and an end, without `durationMs` for a
    # call that never ran.
    projected: list[dict[str, object]] = []

    class _Sink:
        def emit(self, event) -> None:
            projected.append(event)

    adapter = AutomationAgentEventAdapter(_Sink())  # type: ignore[arg-type]
    for event in sink.events:
        adapter.emit(event)
    queued = [e for e in projected if e.get("toolCallId") == "p1/2"]
    assert [e["type"] for e in queued] == ["tool_execution_start", "tool_execution_end"]
    assert queued[1]["isError"] is True and "durationMs" not in queued[1]
    completed = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert completed.details is not None
    calls = completed.details["nestedCalls"]["calls"]
    assert [(c["id"], c["status"]) for c in calls] == [
        ("p1/1", "cancelled"),
        ("p1/2", "cancelled"),
    ]
