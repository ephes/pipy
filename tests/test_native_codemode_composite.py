"""T6b real guest -> canonical loop -> actual executor integration (acceptance D)."""

from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pytest
from codemode_driver import REAL_RUNTIME, group_is_gone, real_runtime_skip_reason
from test_native_agent_loop import (
    _EventSink,
    _provider_call,
    _provider_result,
    _ProviderTurn,
    _QueuedInputs,
    _RequestSource,
    _run_input,
    _StatusPolicy,
    _ToolPolicy,
    _UsagePublisher,
)

from pipy_harness.native.agent import (
    NestedToolCallCompleted,
    NestedToolCallStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop import AgentLoop, AgentLoopOutcome
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.results import AgentRunOutcome
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.codemode import host
from pipy_harness.native.codemode.outcome import ScriptLimits
from pipy_harness.native.codemode.runtime import RUNTIME_PIN, CodemodePaths
from pipy_harness.native.coding.codemode_runner import CodemodeCompositeRunner
from pipy_harness.native.repl.turn_leaves import wait_for_external_tool_interrupt
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    NativeToolCapabilitySnapshot,
    ToolFilterOptions,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> CodemodePaths:
    reason = real_runtime_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    root = tmp_path_factory.mktemp("codemode-composite")
    (root / "runtime").mkdir()
    (root / "runtime" / RUNTIME_PIN.dir_name).symlink_to(REAL_RUNTIME)
    return CodemodePaths(root)


@pytest.fixture
def pids(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    seen: list[int] = []
    real_worker = host._Worker

    class RecordingWorker(real_worker):  # type: ignore[misc, valid-type]
        def __init__(self, argv: list[str], status_write: int) -> None:
            super().__init__(argv, status_write)
            seen.append(self.process.pid)

    monkeypatch.setattr(host, "_Worker", RecordingWorker)
    yield seen
    assert not any(
        thread.name == "codemode-tool-stop" for thread in threading.enumerate()
    )
    for pid in seen:
        assert group_is_gone(pid), f"worker group {pid} survived"


@dataclass
class _Tool:
    name: str
    invoke_hook: Callable[[ToolRequest, ToolContext], None] | None = None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(self.name, "internal fixture", {"type": "object"})

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        if self.invoke_hook is not None:
            self.invoke_hook(request, context)
        return ToolExecutionResult(request.tool_request_id, "fixture result")


class _Policy(_ToolPolicy):
    def __init__(self) -> None:
        super().__init__([])
        self.thread = threading.current_thread()
        self.children: list[AgentToolCall] = []

    def before_execute(self, call: AgentToolCall, /):
        assert threading.current_thread() is self.thread
        if call.tool_name != "codemode":
            self.children.append(call)
        return super().before_execute(call)

    def transform_result(
        self, call: AgentToolCall, result: AgentToolResultMessage, /
    ) -> ProductContent:
        assert threading.current_thread() is self.thread
        return ProductContent(
            result.content.value + (" transformed" if call.tool_name == "read" else "")
        )


def _loop(
    tmp_path: Path,
    code: str,
    *,
    paths: CodemodePaths | None = None,
    waiter: ToolInterruptWaiter | None = None,
    wall: float = 10,
    extra: dict[str, ToolPort] | None = None,
    extensions: dict[str, ToolPort] | None = None,
    names: tuple[str, ...] | None = None,
    policy: _ToolPolicy | None = None,
    enabled: bool = True,
    registered: bool = True,
    runner: CodemodeCompositeRunner | None = None,
    arguments: str | None = None,
    later_call: bool = False,
) -> tuple[AgentLoop, _StatusPolicy, _EventSink]:
    registry: dict[str, ToolPort] = {
        "codemode": _Tool("codemode"),
        "read": ReadTool(),
        "write": WriteTool(),
    }
    if not registered:
        registry.pop("codemode")
    registry.update(extra or {})
    capabilities = NativeToolCapabilities(
        registry,
        extensions or {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.05,
    )
    frozen = capabilities.snapshot_for_projection(capabilities.state)
    order: list[str] = []
    events = _EventSink(order)
    status = _StatusPolicy(order)
    loop = AgentLoop(
        request_source=_RequestSource(order, authorized_names=names),
        provider_turn=_ProviderTurn(
            order,
            [
                ProviderTurnOutcome(
                    result=_provider_result(
                        calls=(
                            _provider_call(
                                "parent",
                                name="codemode",
                                arguments=arguments or json.dumps({"code": code}),
                            ),
                            *(
                                (
                                    _provider_call(
                                        "late",
                                        name="write",
                                        arguments=json.dumps(
                                            {"path": "late", "content": "bad"}
                                        ),
                                    ),
                                )
                                if later_call
                                else ()
                            ),
                        )
                    )
                ),
                ProviderTurnOutcome(result=_provider_result()),
            ],
        ),
        tool_capabilities=frozen,
        tool_policy=policy or _Policy(),
        event_sink=events,
        usage_publisher=_UsagePublisher(order),
        queued_input_port=_QueuedInputs(),
        status_policy=status,
        composite_runner=(
            runner
            or CodemodeCompositeRunner(
                paths=paths, limits=ScriptLimits(wall_seconds=wall)
            )
        )
        if enabled
        else None,
        nested_eligibility=frozen,
        tool_waiter=waiter,
    )
    return loop, status, events


def _parent(outcome: AgentLoopOutcome) -> AgentToolResultMessage:
    return next(
        message
        for message in outcome.final_history
        if isinstance(message, AgentToolResultMessage)
    )


def test_real_multistep_text_errors_effects_and_latest_counters(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    (tmp_path / "a").write_text("alpha")
    (tmp_path / "b").write_text("beta")
    policy = _Policy()
    code = """
text(tools.read(path='a') + '/' + tools.read(path='b'))
try:
    tools.read(path=42)
except ToolError as exc:
    text('malformed: ' + str(exc))
text(tools.write(path='effect', content='retained'))
try:
    tools.read(path='a')
except ToolError as exc:
    text('budget: ' + str(exc))
raise RuntimeError('after effect')
"""
    loop, status, events = _loop(tmp_path, code, paths=paths, policy=policy)
    outcome = loop.run(_run_input(tool_budget=5))
    parent = _parent(outcome)
    assert parent.provider_correlation_id == "parent" and parent.tool_name == "codemode"
    assert parent.is_error
    assert "alpha transformed/beta transformed" in parent.content.value
    assert "malformed:" in parent.content.value and "budget:" in parent.content.value
    assert "after effect" in parent.content.value
    assert (tmp_path / "effect").read_text() == "retained"
    state = outcome.final_tool_state
    assert (
        state.tool_invocation_count,
        state.invocations_this_turn,
        state.nested_malformed_count,
        state.budget_exhausted_count,
    ) == (4, 5, 1, 1)
    assert not state.reserved_parent_slot
    assert [call.provider_correlation_id for call in policy.children] == [
        "parent/1",
        "parent/2",
        "parent/3",
        "parent/4",
    ]
    lifecycle = [
        event
        for event in events.events
        if isinstance(
            event,
            (
                ToolCallStarted,
                ToolCallCompleted,
                NestedToolCallStarted,
                NestedToolCallCompleted,
            ),
        )
    ]
    assert [type(event) for event in lifecycle] == [
        ToolCallStarted,
        *([NestedToolCallStarted, NestedToolCallCompleted] * 5),
        ToolCallCompleted,
    ]
    completed = [
        event for event in lifecycle if isinstance(event, NestedToolCallCompleted)
    ]
    assert [event.status for event in completed] == [
        NestedCallStatus.SETTLED,
        NestedCallStatus.SETTLED,
        NestedCallStatus.MALFORMED,
        NestedCallStatus.SETTLED,
        NestedCallStatus.BUDGET_EXHAUSTED,
    ]
    assert completed[0].result.content.value == "alpha transformed"
    assert all(event.duration_seconds >= 0 for event in completed)
    assert len(pids) == 1


@pytest.mark.parametrize(
    "interruption",
    [ToolExecutionInterruption.OPERATOR_ABORT, ToolExecutionInterruption.LOCAL_COMMAND],
)
@pytest.mark.parametrize("cooperative", [True, False])
def test_interrupt_active_nested_tool_and_fresh_run(
    tmp_path: Path,
    paths: CodemodePaths,
    pids: list[int],
    interruption: ToolExecutionInterruption,
    cooperative: bool,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        entered.set()
        assert context.cancel_event is not None
        if cooperative:
            assert context.cancel_event.wait(5)
            cancelled.set()
        else:
            release.wait(5)
            if context.cancel_event.is_set():
                cancelled.set()

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        while not done.wait(0.01):
            if entered.is_set():
                cancel.set()
                return interruption
        return ToolExecutionInterruption.SETTLED

    runner = CodemodeCompositeRunner(paths=paths)
    try:
        loop, _, events = _loop(
            tmp_path,
            "tools.bash(); tools.write(path='late', content='bad')",
            paths=paths,
            waiter=waiter,
            runner=runner,
            extra={"bash": _Tool("bash", invoke)},
        )
        started = time.monotonic()
        outcome = loop.run(_run_input())
        assert time.monotonic() - started < 3
        assert outcome.result.outcome is AgentRunOutcome.CANCELLED
        children = [
            event
            for event in events.events
            if isinstance(event, NestedToolCallCompleted)
        ]
        assert len(children) == 1 and children[0].status is NestedCallStatus.INTERRUPTED
        assert children[0].result.is_error
        assert not (tmp_path / "late").exists()
        assert not outcome.final_tool_state.reserved_parent_slot
        assert all(group_is_gone(pid) for pid in pids)
    finally:
        release.set()
    assert cancelled.wait(1)
    fresh, _, _ = _loop(tmp_path, "text('fresh')", paths=paths, runner=runner)
    assert "fresh" in _parent(fresh.run(_run_input())).content.value
    assert len(pids) == 2 and pids[0] != pids[1]


def test_external_abort_during_guest_compute(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    abort = threading.Event()
    timer = threading.Timer(0.3, abort.set)
    timer.start()
    try:
        loop, _, _ = _loop(
            tmp_path,
            "while True: pass",
            paths=paths,
            waiter=partial(wait_for_external_tool_interrupt, abort),
        )
        outcome = loop.run(_run_input())
        assert outcome.result.outcome is AgentRunOutcome.CANCELLED
        assert "Script aborted" in _parent(outcome).content.value
        assert not outcome.final_tool_state.reserved_parent_slot
    finally:
        timer.cancel()
        timer.join()


@pytest.mark.parametrize("failure", ["deadline", "backend"])
def test_backend_stop_cancels_tool_without_operator_turn(
    tmp_path: Path,
    paths: CodemodePaths,
    pids: list[int],
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    cancelled = threading.Event()

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        entered.set()
        assert context.cancel_event is not None
        assert context.cancel_event.wait(5)
        cancelled.set()

    if failure == "backend":
        original = host._Channel._loop

        def fail(channel: host._Channel):
            # A real guest has issued a call; inject a backend failure while
            # that actual executor invocation remains active.
            original_service = channel._service

            def service(selector, key):
                original_service(selector, key)
                if entered.wait(0.01):
                    raise RuntimeError("backend failed during tool")

            monkeypatch.setattr(channel, "_service", service)
            return original(channel)

        monkeypatch.setattr(host._Channel, "_loop", fail)
    loop, _, events = _loop(
        tmp_path,
        "tools.bash(); text('late')",
        paths=paths,
        wall=0.5 if failure == "deadline" else 5,
        waiter=partial(wait_for_external_tool_interrupt, threading.Event()),
        extra={"bash": _Tool("bash", invoke)},
    )
    started = time.monotonic()
    outcome = loop.run(_run_input())
    assert time.monotonic() - started < 3
    assert entered.is_set() and cancelled.wait(1)
    children = [
        event for event in events.events if isinstance(event, NestedToolCallCompleted)
    ]
    assert len(children) == 1 and children[0].status is NestedCallStatus.CANCELLED
    assert children[0].result.is_error
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    parent = _parent(outcome)
    assert parent.is_error
    assert (
        "Script timed out" if failure == "deadline" else "backend failed during tool"
    ) in parent.content.value
    assert "late" not in parent.content.value
    assert outcome.final_tool_state.tool_invocation_count == 2
    assert not outcome.final_tool_state.reserved_parent_slot


def test_guest_names_are_frozen_advertised_builtin_identities(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    code = """
for name in ['write', 'bash', 'extension', 'codemode']:
    try:
        getattr(tools, name)
    except AttributeError as exc:
        text(str(exc))
text(tools.read(path='a'))
"""
    (tmp_path / "a").write_text("visible")
    loop, _, _ = _loop(
        tmp_path,
        code,
        paths=paths,
        names=("codemode", "read", "bash", "extension"),
        extra={"bash": _Tool("bash")},
        extensions={"bash": _Tool("bash"), "extension": _Tool("extension")},
    )
    outcome = loop.run(_run_input())
    parent = _parent(outcome)
    assert not parent.is_error
    assert parent.content.value.count("Available tools: tools.read") == 4
    assert "visible transformed" in parent.content.value
    assert outcome.final_tool_state.tool_invocation_count == 2


@pytest.mark.parametrize(
    "enabled,extension,registered",
    [(False, False, True), (True, True, True), (True, True, False)],
)
def test_disabled_or_extension_codemode_retains_direct_behavior(
    tmp_path: Path, pids: list[int], enabled: bool, extension: bool, registered: bool
) -> None:
    loop, _, _ = _loop(
        tmp_path,
        "raise RuntimeError('must not run')",
        enabled=enabled,
        registered=registered,
        extensions={"codemode": _Tool("codemode")} if extension else None,
        arguments="{}",
    )
    outcome = loop.run(_run_input())
    assert _parent(outcome).content.value == "fixture result"
    assert not pids


@pytest.mark.parametrize(
    "arguments", ["{", "{}", '{"code": 42}', '{"code":"pass","extra":true}', "[]"]
)
def test_parent_schema_uses_normal_malformed_semantics(
    tmp_path: Path, pids: list[int], arguments: str
) -> None:
    loop, _, _ = _loop(tmp_path, "", arguments=arguments)
    outcome = loop.run(_run_input())
    assert _parent(outcome).is_error
    assert outcome.final_tool_state.malformed_argument_count == 1
    assert outcome.final_tool_state.tool_invocation_count == 0
    assert not outcome.final_tool_state.reserved_parent_slot
    assert not pids


def test_canonical_hook_failure_propagates_after_guest_cleanup(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    class FailingPolicy(_Policy):
        def transform_result(
            self, call: AgentToolCall, result: AgentToolResultMessage, /
        ) -> ProductContent:
            if call.tool_name == "write":
                raise RuntimeError("canonical transform failure")
            return super().transform_result(call, result)

    loop, _, _ = _loop(
        tmp_path,
        "tools.write(path='effect', content='retained')",
        paths=paths,
        policy=FailingPolicy(),
    )
    with pytest.raises(RuntimeError, match="canonical transform failure"):
        loop.run(_run_input())
    assert (tmp_path / "effect").read_text() == "retained"
    assert pids and all(group_is_gone(pid) for pid in pids)


def test_headless_abort_during_active_tool(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    abort = threading.Event()
    cancelled = threading.Event()

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        abort.set()
        assert context.cancel_event is not None
        assert context.cancel_event.wait(5)
        cancelled.set()

    loop, _, _ = _loop(
        tmp_path,
        "tools.bash()",
        paths=paths,
        waiter=partial(wait_for_external_tool_interrupt, abort),
        extra={"bash": _Tool("bash", invoke)},
    )
    outcome = loop.run(_run_input())
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert cancelled.wait(1)
    assert pids and all(group_is_gone(pid) for pid in pids)


def test_policy_rejection_is_catchable_guest_error(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    from pipy_harness.native.agent.loop_policy import AgentToolPolicyDecision

    class RejectPolicy(_Policy):
        def before_execute(self, call: AgentToolCall, /):
            super().before_execute(call)
            return AgentToolPolicyDecision(
                ProductContent("rejected") if call.tool_name == "write" else None
            )

    loop, _, _ = _loop(
        tmp_path,
        "try:\n    tools.write(path='denied', content='bad')\nexcept ToolError as exc:\n    text(str(exc))\ntext('continued')",
        paths=paths,
        policy=RejectPolicy(),
    )
    outcome = loop.run(_run_input())
    assert not _parent(outcome).is_error
    assert (
        "rejected" in _parent(outcome).content.value
        and "continued" in _parent(outcome).content.value
    )
    assert not (tmp_path / "denied").exists()
    assert outcome.final_tool_state.tool_invocation_count == 1
    assert outcome.final_tool_state.invocations_this_turn == 2


def test_activity_callback_failure_reaps_and_is_parent_error(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        raise RuntimeError("activity callback failure")

    loop, _, _ = _loop(tmp_path, "while True: pass", paths=paths, waiter=waiter)
    outcome = loop.run(_run_input())
    assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
    assert (
        _parent(outcome).is_error
        and "activity callback failure" in _parent(outcome).content.value
    )
    assert pids and all(group_is_gone(pid) for pid in pids)


def test_nested_waiter_failure_preserves_pipeline_failure_and_cleans_up(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    entered = threading.Event()
    cancelled = threading.Event()

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        entered.set()
        assert context.cancel_event is not None
        assert context.cancel_event.wait(5)
        cancelled.set()

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        while not done.wait(0.01):
            if entered.is_set():
                raise RuntimeError("nested waiter failure")
        return ToolExecutionInterruption.SETTLED

    loop, _, _ = _loop(
        tmp_path,
        "tools.bash()",
        paths=paths,
        waiter=waiter,
        extra={"bash": _Tool("bash", invoke)},
    )
    with pytest.raises(RuntimeError, match="nested waiter failure"):
        loop.run(_run_input())
    assert cancelled.wait(1)
    assert pids and all(group_is_gone(pid) for pid in pids)


@pytest.mark.parametrize("keyboard", [False, True])
def test_local_command_or_keyboard_interrupt_during_compute(
    tmp_path: Path, paths: CodemodePaths, pids: list[int], keyboard: bool
) -> None:
    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        time.sleep(0.3)
        cancel.set()
        if keyboard:
            raise KeyboardInterrupt
        return ToolExecutionInterruption.LOCAL_COMMAND

    loop, _, _ = _loop(tmp_path, "while True: pass", paths=paths, waiter=waiter)
    outcome = loop.run(_run_input())
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert "Script aborted" in _parent(outcome).content.value
    assert pids and all(group_is_gone(pid) for pid in pids)


def test_deadline_bounds_noncooperative_executor_join(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        entered.set()
        release.wait(5)
        assert context.cancel_event is not None and context.cancel_event.is_set()
        cancelled.set()

    loop, _, _ = _loop(
        tmp_path,
        "tools.bash()",
        paths=paths,
        wall=0.5,
        waiter=partial(wait_for_external_tool_interrupt, threading.Event()),
        extra={"bash": _Tool("bash", invoke)},
    )
    try:
        started = time.monotonic()
        outcome = loop.run(_run_input())
        assert time.monotonic() - started < 2
        assert entered.is_set() and not cancelled.is_set()
        assert outcome.result.outcome is AgentRunOutcome.SUCCEEDED
        assert "Script timed out" in _parent(outcome).content.value
        assert pids and all(group_is_gone(pid) for pid in pids)
    finally:
        release.set()
    assert cancelled.wait(1)


def test_frozen_builtin_survives_live_registry_reload(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    (tmp_path / "a").write_text("original builtin")
    tools: list[NativeToolCapabilitySnapshot] = []

    def invoke(request: ToolRequest, context: ToolContext) -> None:
        frozen = tools[0]
        frozen.owner.publish(frozen.owner.prepare_extensions({"read": _Tool("read")}))

    loop, _, _ = _loop(
        tmp_path,
        "tools.bash(); text(tools.read(path='a'))",
        paths=paths,
        extra={"bash": _Tool("bash", invoke)},
    )
    from typing import cast

    tools.append(cast(NativeToolCapabilitySnapshot, loop._tools))
    outcome = loop.run(_run_input())
    assert "original builtin transformed" in _parent(outcome).content.value
    assert not _parent(outcome).is_error


def test_no_waiter_keyboard_interrupt_during_real_compute(
    tmp_path: Path,
    paths: CodemodePaths,
    pids: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipy_harness.native.agent.nested_calls import NestedToolCallService
    from pipy_harness.native.coding.codemode_runner import _RunControl

    session_ident = threading.get_ident()
    original_wait = _RunControl._wait
    original_close = NestedToolCallService.close
    closed: list[NestedToolCallService] = []
    timers: list[threading.Timer] = []
    injected = threading.Event()
    computing = threading.Event()
    original_text = host._Channel._on_text

    def on_text(channel: host._Channel, message: dict[str, object]) -> None:
        original_text(channel, message)
        if message.get("value") == "computing":
            computing.set()

    def interrupt() -> None:
        if computing.wait(3):
            injected.set()
            signal.pthread_kill(session_ident, signal.SIGINT)

    def wait(
        control: _RunControl, done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        assert threading.get_ident() == session_ident
        assert control.waiter is None
        if not timers:
            # The guest marker proves actual computation has begun. Send SIGINT
            # to the session thread blocked in its real Event.wait.
            timer = threading.Timer(0, interrupt)
            timers.append(timer)
            timer.start()
        return original_wait(control, done, cancel)

    def close(service: NestedToolCallService) -> None:
        original_close(service)
        closed.append(service)

    monkeypatch.setattr(host._Channel, "_on_text", on_text)
    monkeypatch.setattr(_RunControl, "_wait", wait)
    monkeypatch.setattr(NestedToolCallService, "close", close)
    previous_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        loop, _, events = _loop(
            tmp_path,
            "text('computing')\nwhile True: pass",
            paths=paths,
            wall=5,  # Bounds the test if SIGINT translation regresses.
            later_call=True,
        )
        outcome = loop.run(_run_input())
    finally:
        for timer in timers:
            timer.cancel()
            timer.join()
        signal.signal(signal.SIGINT, previous_handler)
    assert computing.is_set() and injected.is_set()
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    assert outcome.result.cancellation_reason is not None
    assert outcome.result.cancellation_reason.value == "operator_abort"
    results = [
        message
        for message in outcome.final_history
        if isinstance(message, AgentToolResultMessage)
    ]
    assert [message.provider_correlation_id for message in results] == [
        "parent",
        "late",
    ]
    assert "Script aborted" in results[0].content.value
    assert "skipped" in results[1].content.value
    assert not (tmp_path / "late").exists()
    assert len([item for item in events._order if item.startswith("provider:")]) == 1
    assert closed and all(service._closed for service in closed)
    assert not outcome.final_tool_state.reserved_parent_slot
    assert pids and all(group_is_gone(pid) for pid in pids)


def test_ordinary_nested_toolerror_is_not_backend_cancellation(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    loop, _, events = _loop(
        tmp_path,
        "try:\n    tools.read(path='missing')\nexcept ToolError:\n    text('caught ordinary toolerror')",
        paths=paths,
    )
    outcome = loop.run(_run_input())
    assert "caught ordinary toolerror" in _parent(outcome).content.value
    children = [
        event for event in events.events if isinstance(event, NestedToolCallCompleted)
    ]
    assert len(children) == 1 and children[0].status is NestedCallStatus.SETTLED
    assert children[0].result.is_error
    assert outcome.final_tool_state.tool_invocation_count == 2
    assert len(pids) == 1
