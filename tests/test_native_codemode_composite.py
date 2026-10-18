"""T6b real guest -> canonical loop -> actual executor integration (acceptance D)."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

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
from pipy_harness.native.codemode.outcome import (
    CPU_SECONDS_MAX,
    CallResult,
    ScriptImage,
    ScriptLimits,
    ScriptOutcome,
)
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
    ToolExposure,
    ToolPort,
    ToolRequest,
)
from pipy_harness.native.tools.codemode import CodemodeTool, codemode_definition
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> CodemodePaths:
    reason = real_runtime_skip_reason()
    if reason is not None:
        if os.environ.get("PIPY_CODEMODE_REQUIRE_RUNTIME") == "1":
            pytest.fail(reason)
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
    exposure: ToolExposure = "direct"

    @property
    def definition(self) -> ToolDefinition:
        # The loop validates a call's arguments before its hooks, so the
        # fixture standing in for codemode takes codemode's arguments.
        schema = (
            codemode_definition().input_schema
            if self.name == "codemode"
            else {"type": "object"}
        )
        return ToolDefinition(
            self.name, "internal fixture", schema, exposure=self.exposure
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        if self.invoke_hook is not None:
            self.invoke_hook(request, context)
        return ToolExecutionResult(request.tool_request_id, "fixture result")


class _Policy(_ToolPolicy):
    def __init__(self) -> None:
        super().__init__([])
        self.thread = threading.current_thread()
        self.children: list[AgentToolCall] = []

    def before_execute(
        self, call: AgentToolCall, arguments: Any, parent_call_id: str | None, /
    ):
        assert threading.current_thread() is self.thread
        if call.tool_name != "codemode":
            self.children.append(call)
        return super().before_execute(call, arguments, parent_call_id)

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        arguments: Any,
        parent_call_id: str | None,
        /,
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
    public_definition: bool = False,
) -> tuple[AgentLoop, _StatusPolicy, _EventSink]:
    registry: dict[str, ToolPort] = {
        "codemode": CodemodeTool() if public_definition else _Tool("codemode"),
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
        nested_eligibility=capabilities,
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
text('past the budget: ' + tools.read(path='a'))
raise RuntimeError('after effect')
"""
    loop, status, events = _loop(tmp_path, code, paths=paths, policy=policy)
    # The parent holds the only place in the budget; the script's calls take
    # none, as Pi has no limit on the calls a tool makes.
    outcome = loop.run(_run_input(tool_budget=1))
    parent = _parent(outcome)
    assert parent.provider_correlation_id == "parent" and parent.tool_name == "codemode"
    assert parent.is_error
    assert "alpha transformed/beta transformed" in parent.content.value
    assert "malformed:" in parent.content.value
    assert "past the budget: alpha transformed" in parent.content.value
    assert "after effect" in parent.content.value
    assert parent.details is not None
    record = parent.details["nestedCalls"]
    assert [c["status"] for c in record["calls"]] == [
        "ok",
        "ok",
        "error",
        "ok",
        "ok",
    ]
    assert record["complete"] is True
    assert record["calls"][3]["arguments"]["path"] == "effect"
    assert (tmp_path / "effect").read_text() == "retained"
    state = outcome.final_tool_state
    assert (
        state.tool_invocation_count,
        state.invocations_this_turn,
        state.nested_malformed_count,
        state.budget_exhausted_count,
    ) == (5, 1, 1, 0)
    assert not state.reserved_parent_slot
    # parent/3's arguments fail validation, so it reaches no hook (Pi).
    assert [call.provider_correlation_id for call in policy.children] == [
        "parent/1",
        "parent/2",
        "parent/4",
        "parent/5",
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
        NestedCallStatus.SETTLED,
    ]
    assert completed[0].result.content.value == "alpha transformed"
    assert all(event.duration_seconds >= 0 for event in completed)
    # Pi's durationMs: only the calls whose tool ran have an execution time;
    # the malformed call never started.
    assert [event.execution_seconds is not None for event in completed] == [
        True,
        True,
        False,
        True,
        True,
    ]
    parent_end = lifecycle[-1]
    assert isinstance(parent_end, ToolCallCompleted)
    assert parent_end.execution_seconds is not None  # the script ran
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
        parent = _parent(outcome)
        assert parent.details is not None
        assert parent.details["nestedCalls"]["calls"][0]["status"] == "cancelled"
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
    assert parent.details is not None
    row = parent.details["nestedCalls"]["calls"][0]
    assert row["status"] == "cancelled"
    assert row["durationMs"] == children[0].duration_seconds * 1000
    assert (
        "Script timed out" if failure == "deadline" else "backend failed during tool"
    ) in parent.content.value
    assert "late" not in parent.content.value
    assert outcome.final_tool_state.tool_invocation_count == 2
    assert not outcome.final_tool_state.reserved_parent_slot


def test_guest_names_are_the_callable_snapshot(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    # `write` is active but a request hook withheld it; `orchestrate` is
    # model-only and `withdrawn` hidden. `listed` is callable although no
    # request declares it, and an extension's `bash` replaces the builtin.
    code = """
for name in ['write', 'codemode', 'orchestrate', 'withdrawn']:
    try:
        getattr(tools, name)
    except AttributeError as exc:
        text(str(exc))
text(tools.read(path='a'))
text(tools.listed() + ' ' + tools.bash())
"""
    (tmp_path / "a").write_text("visible")
    loop, _, _ = _loop(
        tmp_path,
        code,
        paths=paths,
        names=("codemode", "read", "bash", "orchestrate"),
        extra={"bash": _Tool("bash")},
        extensions={
            "bash": _Tool("bash"),
            "listed": _Tool("listed", exposure="codemode"),
            "orchestrate": _Tool("orchestrate", exposure="model-only"),
            "withdrawn": _Tool("withdrawn", exposure="hidden"),
        },
    )
    outcome = loop.run(_run_input())
    parent = _parent(outcome)
    assert not parent.is_error
    assert (
        # Registration order: the builtins, then the extension tools.
        parent.content.value.count(" does not exist. Available: read, bash, listed.")
        == 4
    )
    assert "visible transformed" in parent.content.value
    assert "fixture result fixture result" in parent.content.value
    assert outcome.final_tool_state.tool_invocation_count == 4


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
    loop, _, events = _loop(tmp_path, "", arguments=arguments)
    outcome = loop.run(_run_input())
    assert _parent(outcome).is_error
    # The script never started: no execution time (Pi omits durationMs).
    (end,) = [e for e in events.events if isinstance(e, ToolCallCompleted)]
    assert end.execution_seconds is None
    assert outcome.final_tool_state.malformed_argument_count == 1
    assert outcome.final_tool_state.tool_invocation_count == 0
    assert not outcome.final_tool_state.reserved_parent_slot
    assert not pids


def test_canonical_hook_failure_propagates_after_guest_cleanup(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    class FailingPolicy(_Policy):
        def transform_result(
            self,
            call: AgentToolCall,
            result: AgentToolResultMessage,
            arguments: Any,
            parent_call_id: str | None,
            /,
        ) -> ProductContent:
            if call.tool_name == "write":
                raise RuntimeError("canonical transform failure")
            return super().transform_result(call, result, arguments, parent_call_id)

    loop, _, events = _loop(
        tmp_path,
        "tools.write(path='effect', content='retained')",
        paths=paths,
        policy=FailingPolicy(),
    )
    with pytest.raises(RuntimeError, match="canonical transform failure"):
        loop.run(_run_input())
    assert (tmp_path / "effect").read_text() == "retained"
    parent = next(e.result for e in events.events if isinstance(e, ToolCallCompleted))
    assert parent.details is not None
    assert parent.details["nestedCalls"]["calls"][0]["status"] == "unfinished"
    assert parent.details["nestedCalls"]["complete"] is False
    assert parent.details["nestedCalls"]["calls"][0]["arguments"]["path"] == "effect"
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
        def before_execute(
            self, call: AgentToolCall, arguments: Any, parent_call_id: str | None, /
        ):
            super().before_execute(call, arguments, parent_call_id)
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
    # The blocked nested call takes no place in the budget; the parent does.
    assert outcome.final_tool_state.invocations_this_turn == 1


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


@pytest.mark.parametrize("stop", ["timeout", "operator"])
def test_completed_write_record_and_effect_survive_compute_stop(
    tmp_path: Path, paths: CodemodePaths, pids: list[int], stop: str
) -> None:
    abort = threading.Event()
    timer = threading.Timer(0.7, abort.set)
    if stop == "operator":
        timer.start()
    try:
        loop, _, _ = _loop(
            tmp_path,
            "tools.write(path='effect', content='retained');\nwhile True: pass",
            paths=paths,
            wall=0.7 if stop == "timeout" else 5,
            waiter=partial(wait_for_external_tool_interrupt, abort),
        )
        outcome = loop.run(_run_input())
        parent = _parent(outcome)
        assert parent.is_error and parent.details is not None
        assert (tmp_path / "effect").read_text() == "retained"
        record = parent.details["nestedCalls"]
        assert record["complete"] is True
        assert len(record["calls"]) == 1
        assert record["calls"][0]["status"] == "ok"
        assert record["calls"][0]["arguments"]["path"] == "effect"
        assert record["calls"][0]["durationMs"] >= 0
        assert (
            "Script timed out" if stop == "timeout" else "Script aborted"
        ) in parent.content.value
    finally:
        if stop == "operator":
            timer.cancel()
            timer.join()
    assert all(group_is_gone(pid) for pid in pids)


@pytest.mark.parametrize("spill_failure", [False, True])
def test_public_definition_write_then_raise_and_spill(
    tmp_path: Path,
    paths: CodemodePaths,
    pids: list[int],
    monkeypatch: pytest.MonkeyPatch,
    spill_failure: bool,
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    if spill_failure:

        def fail_open(**kwargs):
            raise OSError("spill unavailable")

        monkeypatch.setattr("tempfile.NamedTemporaryFile", fail_open)
    code = "tools.write(path='effect', content='retained'); print('é🙂' * 25000); raise RuntimeError('after effect')"
    loop, _, _ = _loop(tmp_path, code, paths=paths, public_definition=True)
    parent = _parent(loop.run(_run_input(tool_budget=5)))
    assert (tmp_path / "effect").read_text() == "retained"
    assert parent.is_error and parent.provider_correlation_id == "parent"
    assert "after effect" in parent.content.value
    assert "not undone" in parent.content.value and "write (ok)" in parent.content.value
    assert parent.details is not None
    assert parent.details["nestedCalls"]["calls"][0]["status"] == "ok"
    if spill_failure:
        assert "fullOutputPath" not in parent.details
        assert "could not be saved" in parent.content.value
        return
    spill = Path(parent.details["fullOutputPath"])
    body = spill.read_text()
    assert body.startswith(
        "<console_output>\n" + "é🙂" * 25000 + "\n</console_output>\nScript error:\n"
    )
    assert "RuntimeError: after effect" in body
    assert "not undone" in body and "write (ok)" in body
    assert len(parent.content.value) < 41000


@pytest.mark.parametrize("kind", ["timeout", "aborted"])
@pytest.mark.parametrize("spill_failure", [False, True])
def test_stopped_composite_spill_preserves_records_and_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, spill_failure: bool
) -> None:
    from pipy_harness.native.codemode.outcome import (
        CallStatus,
        ErrorKind,
        ScriptError,
        ScriptOutcome,
        ToolCallRecord,
    )

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    abort_requested = False

    def stopped(code, *, start_tool, activity_waiter, **kwargs):
        nonlocal abort_requested
        settled: list[CallResult] = []
        until = threading.Event()

        def settle(result: CallResult) -> None:
            settled.append(result)
            until.set()

        start_tool("write", '{"path":"effect","content":"retained"}', settle)
        while not settled:
            activity_waiter(until, threading.Event())
        assert settled[0].ok
        if kind == "aborted":
            abort_requested = True
            activity_waiter(threading.Event(), threading.Event())
        return ScriptOutcome(
            ScriptError(ErrorKind(kind), "stopped"),
            output=("partial" * 10000,),
            calls=(ToolCallRecord("write", CallStatus.OK),),
        )

    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script", stopped
    )
    if spill_failure:

        def fail_open(**kwargs):
            raise OSError("spill unavailable")

        monkeypatch.setattr("tempfile.NamedTemporaryFile", fail_open)

    def waiter(done, cancel):
        return (
            ToolExecutionInterruption.OPERATOR_ABORT
            if abort_requested
            else ToolExecutionInterruption.SETTLED
        )

    loop, _, _ = _loop(tmp_path, "unused", public_definition=True, waiter=waiter)
    outcome = loop.run(_run_input())
    parent = _parent(outcome)
    assert parent.is_error and parent.details is not None
    assert parent.details["nestedCalls"]["calls"][0]["status"] == "ok"
    assert "not undone" in parent.content.value and "write (ok)" in parent.content.value
    assert (tmp_path / "effect").read_text() == "retained"
    assert (outcome.result.outcome is AgentRunOutcome.CANCELLED) == (kind == "aborted")
    assert ("fullOutputPath" in parent.details) is not spill_failure


# --- calls that run at the same time (G3) --------------------------------------


@dataclass
class _Answering:
    """A fixture tool whose result text ``answer`` gives."""

    name: str
    answer: Callable[[ToolRequest, ToolContext], str]
    properties: dict[str, object] | None = None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            self.name,
            "internal fixture",
            {"type": "object", "properties": self.properties or {}},
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            request.tool_request_id, self.answer(request, context)
        )


def test_submitted_calls_run_at_the_same_time_and_the_record_keeps_their_order(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    # Pi: "runs nested calls in parallel and returns only the script result"
    # (agent-session-codemode.test.ts). The three tools return only once all
    # three run, so the script finishes only if they run at the same time.
    together = threading.Barrier(3, timeout=5)

    def echo(request: ToolRequest, context: ToolContext) -> str:
        together.wait()
        return f"echo: {request.arguments['text']}"

    def stats(request: ToolRequest, context: ToolContext) -> str:
        together.wait()
        return "files 2"

    code = """
a = tools.echo.submit(text="one")
b = tools.echo.submit({"text": "two"})
stats = tools.stats.submit()
print(stats.result())
text(",".join(tool["name"] for tool in ALL_TOOLS if tool["name"] in ("echo", "stats")))
text({"a": a.result(), "b": b.result(), "again": a.result()})
"""
    loop, _, events = _loop(
        tmp_path,
        code,
        paths=paths,
        extra={
            "echo": _Answering("echo", echo, {"text": {"type": "string"}}),
            "stats": _Answering("stats", stats),
        },
    )
    outcome = loop.run(_run_input())
    parent = _parent(outcome)
    assert not parent.is_error, parent.content.value
    assert parent.content.value.endswith(
        "==> text 1/2 <==\necho,stats\n==> text 2/2 <==\n"
        '{"a": "echo: one", "b": "echo: two", "again": "echo: one"}\n'
        "<console_output>\nfiles 2\n</console_output>"
    )
    assert parent.details is not None
    record = parent.details["nestedCalls"]
    assert [(c["id"], c["name"], c["status"]) for c in record["calls"]] == [
        ("parent/1", "echo", "ok"),
        ("parent/2", "echo", "ok"),
        ("parent/3", "stats", "ok"),
    ]
    assert [c["arguments"] for c in record["calls"]] == [
        {"text": "one"},
        {"text": "two"},
        {},
    ]
    assert record["complete"] is True
    started = [e for e in events.events if isinstance(e, NestedToolCallStarted)]
    assert [e.call.provider_correlation_id for e in started] == [
        "parent/1",
        "parent/2",
        "parent/3",
    ]
    # Nested calls never become results of their own.
    results = [
        m for m in outcome.final_history if isinstance(m, AgentToolResultMessage)
    ]
    assert len(results) == 1
    state = outcome.final_tool_state
    assert (state.tool_invocation_count, state.invocations_this_turn) == (4, 1)
    assert not state.reserved_parent_slot


def test_a_script_makes_more_calls_than_the_tool_budget_has_places(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    # Goal check r3 D1: Pi has no limit on the calls a tool makes, so a
    # script's calls, submitted or not, take no place in the tool budget.
    release = threading.Event()

    def slow(request: ToolRequest, context: ToolContext) -> str:
        release.wait(5)
        return f"done {request.arguments['n']}"

    def fast(request: ToolRequest, context: ToolContext) -> str:
        release.set()
        return f"done {request.arguments['n']}"

    code = """
calls = [tools.slow.submit(n=1), tools.fast.submit(n=2), tools.slow.submit(n=3)]
for call in calls:
    text(call.result())
for n in range(4, 9):
    text(tools.fast(n=n))
"""
    loop, _, _ = _loop(
        tmp_path,
        code,
        paths=paths,
        extra={
            "slow": _Answering("slow", slow, {"n": {"type": "integer"}}),
            "fast": _Answering("fast", fast, {"n": {"type": "integer"}}),
        },
    )
    outcome = loop.run(_run_input(tool_budget=2))
    parent = _parent(outcome)
    assert not parent.is_error, parent.content.value
    assert "budget" not in parent.content.value
    for n in range(1, 9):
        assert f"==> text {n}/8 <==\ndone {n}" in parent.content.value
    assert parent.details is not None
    calls = parent.details["nestedCalls"]["calls"]
    assert [c["status"] for c in calls] == ["ok"] * 8
    state = outcome.final_tool_state
    assert (
        state.invocations_this_turn,
        state.tool_invocation_count,
        state.budget_exhausted_count,
    ) == (1, 9, 0)


def test_a_call_still_running_when_the_script_ends_is_cancelled(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    cancelled = threading.Event()

    def slow(request: ToolRequest, context: ToolContext) -> str:
        assert context.cancel_event is not None
        if context.cancel_event.wait(5):
            cancelled.set()
        return "finished"

    loop, _, _ = _loop(
        tmp_path,
        "tools.slow.submit()\ntext('ended')",
        paths=paths,
        extra={"slow": _Answering("slow", slow)},
    )
    started = time.monotonic()
    outcome = loop.run(_run_input())
    assert time.monotonic() - started < 4
    parent = _parent(outcome)
    assert not parent.is_error
    assert "ended" in parent.content.value
    assert parent.details is not None
    (row,) = parent.details["nestedCalls"]["calls"]
    assert (row["name"], row["status"]) == ("slow", "cancelled")
    assert row["error"] == "tool stopped by script backend"
    assert cancelled.wait(1)
    assert not outcome.final_tool_state.reserved_parent_slot


def test_an_interruption_cancels_every_running_call(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    entered: list[str] = []
    cancelled: list[str] = []

    def wait(request: ToolRequest, context: ToolContext) -> str:
        entered.append(request.arguments["n"])
        assert context.cancel_event is not None
        if context.cancel_event.wait(5):
            cancelled.append(request.arguments["n"])
        return "finished"

    def waiter(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        while not done.wait(0.01):
            if len(entered) == 2:
                cancel.set()
                return ToolExecutionInterruption.OPERATOR_ABORT
        return ToolExecutionInterruption.SETTLED

    loop, _, _ = _loop(
        tmp_path,
        "a = tools.wait.submit(n='a')\nb = tools.wait.submit(n='b')\n"
        "a.result(); b.result()\ntools.write(path='late', content='bad')",
        paths=paths,
        waiter=waiter,
        extra={"wait": _Answering("wait", wait, {"n": {"type": "string"}})},
    )
    started = time.monotonic()
    outcome = loop.run(_run_input())
    assert time.monotonic() - started < 4
    assert outcome.result.outcome is AgentRunOutcome.CANCELLED
    parent = _parent(outcome)
    assert parent.details is not None
    assert [c["status"] for c in parent.details["nestedCalls"]["calls"]] == [
        "cancelled",
        "cancelled",
    ]
    assert sorted(cancelled) == ["a", "b"]
    assert not (tmp_path / "late").exists()
    assert not outcome.final_tool_state.reserved_parent_slot


def test_a_call_a_script_call_made_ends_when_that_call_returns(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    # A tool the script called starts a call on a thread of its own and
    # returns without waiting: that call is cancelled before the script goes
    # on, as when the tool ran under ``serve``.
    entered = threading.Event()
    cancelled = threading.Event()

    def leaf(request: ToolRequest, context: ToolContext) -> str:
        entered.set()
        assert context.cancel_event is not None
        if context.cancel_event.wait(5):
            cancelled.set()
        return "leaf"

    def child(request: ToolRequest, context: ToolContext) -> str:
        nested = context.nested_tools
        assert nested is not None
        threading.Thread(target=nested.execute, args=("leaf", {}), daemon=True).start()
        assert entered.wait(5)
        return "child"

    loop, _, _ = _loop(
        tmp_path,
        "text(tools.child())",
        paths=paths,
        extra={"child": _Answering("child", child), "leaf": _Answering("leaf", leaf)},
    )
    outcome = loop.run(_run_input())
    parent = _parent(outcome)
    assert not parent.is_error, parent.content.value
    assert parent.details is not None
    assert [(c["id"], c["status"]) for c in parent.details["nestedCalls"]["calls"]] == [
        ("parent/1", "ok"),
        ("parent/1/1", "cancelled"),
    ]
    assert cancelled.wait(1)


# Goal-check finding M17, Pi ``executeCodemode``: per-script options from the
# first line, and no deadline by default.


def test_the_options_line_sets_the_deadline_and_the_output_budget(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    code = (
        '// @options: {"timeout_ms": 1500, "max_output_tokens": 10}\n'
        "print('x' * 100)\n"
        "while True:\n"
        "    pass\n"
    )
    runner = CodemodeCompositeRunner(paths=paths)
    loop, _, _ = _loop(tmp_path, code, runner=runner)

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert parent.is_error
    assert parent.details is not None
    assert parent.details["truncation"]["maxTokens"] == 10
    body = Path(parent.details["fullOutputPath"]).read_text()
    assert body.startswith(
        "<console_output>\n" + "x" * 100 + "\n</console_output>\nScript error:\n"
    )
    assert "Script timed out: Execution timed out after 1.5 s" in body


def test_the_options_line_keeps_the_line_numbers(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    code = '// @options: {"max_output_tokens": 2000}\n\nraise ValueError("third")\n'
    loop, _, _ = _loop(tmp_path, code, paths=paths)

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert parent.is_error
    assert "line 3" in parent.content.value
    assert "ValueError: third" in parent.content.value


def test_invalid_options_are_an_error_before_any_script_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("the script must not run")

    monkeypatch.setattr("pipy_harness.native.coding.codemode_runner.run_script", no_run)
    loop, _, _ = _loop(tmp_path, '// @options: {"retries": 2}\ntext(1)')

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert parent.is_error
    assert parent.content.value == (
        "@options only supports `max_output_tokens` and `timeout_ms`; got `retries`"
    )


@pytest.mark.parametrize(
    ("first_line", "wall_seconds", "max_tokens"),
    [
        ("", None, 10_000),
        ('// @options: {"timeout_ms": 2500}\n', 2.5, 10_000),
        # A budget past the 64 KiB a result's text may hold acts as the most
        # that fits.
        ('// @options: {"max_output_tokens": 1000000}\n', None, 16_000),
    ],
)
def test_a_script_has_no_deadline_unless_it_sets_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_line: str,
    wall_seconds: float | None,
    max_tokens: int,
) -> None:
    seen: list[ScriptLimits] = []

    def fake_run(code: str, **kwargs: object) -> ScriptOutcome:
        limits = kwargs["limits"]
        assert isinstance(limits, ScriptLimits)
        seen.append(limits)
        return ScriptOutcome(None, output=("y" * 200_000,))

    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script", fake_run
    )
    runner = CodemodeCompositeRunner()
    loop, _, _ = _loop(tmp_path, first_line + "text(1)", runner=runner)

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert [limits.wall_seconds for limits in seen] == [wall_seconds]
    if wall_seconds is None:
        assert seen[0].cpu_seconds == CPU_SECONDS_MAX
    assert not parent.is_error
    assert parent.details is not None
    assert parent.details["truncation"]["maxTokens"] == max_tokens
    assert len(parent.content.value) < 64 * 1024


def test_the_budget_leaves_room_for_the_images_path_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: the largest budget plus 40 image path lines must still
    # fit the 64 KiB a result's text may hold; each image is saved once.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    output: tuple[str | ScriptImage, ...] = (
        "z" * 64_000,
        *([ScriptImage(png, "image/png")] * 40),
    )

    def fake_run(code: str, **kwargs: object) -> ScriptOutcome:
        return ScriptOutcome(None, output=output)

    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script", fake_run
    )
    loop, _, _ = _loop(
        tmp_path,
        '// @options: {"max_output_tokens": 16000}\ntext(1)',
        runner=CodemodeCompositeRunner(),
    )

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert not parent.is_error, parent.content.value[:300]
    assert len(parent.content.value) <= 64 * 1024
    assert parent.content.value.count("[Image saved to ") == 40
    assert len(list(tmp_path.glob("pipy-codemode-*.png"))) == 1
    assert parent.details is not None
    assert parent.details["truncation"]["maxTokens"] < 16_000


def test_output_over_the_line_limit_is_truncated_and_spilled(
    tmp_path: Path,
    paths: CodemodePaths,
    pids: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Goal-check finding M18: 1,100,000 characters used to fail the
    # sandbox; like Pi the script completes, and its output is cut to the
    # budget with the whole of it in the spill file.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    loop, _, _ = _loop(tmp_path, "text('x' * 1_100_000)", paths=paths)

    parent = _parent(loop.run(_run_input(tool_budget=5)))

    assert not parent.is_error, parent.content.value[:300]
    assert parent.content.value.startswith("Script completed\n")
    assert "Warning: truncated output (original token count: 275000)" in (
        parent.content.value
    )
    assert parent.details is not None
    assert Path(parent.details["fullOutputPath"]).read_text() == "x" * 1_100_000


def test_a_call_whose_arguments_the_record_leaves_out_keeps_a_preview(
    tmp_path: Path, paths: CodemodePaths, pids: list[int]
) -> None:
    # Goal check r8, D1: Pi's codemode keeps the first 200 characters of
    # each call's arguments in details.calls, so a restored or exported row
    # still names the file of a write whose arguments are over 8 KiB, which
    # the nestedCalls record replaces by their byte count.
    from pipy_harness.native.tool_rows import (
        nested_line_text,
        nested_lines_from_details,
    )

    code = "tools.write(path='recognizable.txt', content='x' * 9000)\n" + (
        "text(tools.read(path='recognizable.txt')[:5])"
    )
    loop, _, _ = _loop(tmp_path, code, paths=paths)
    parent = _parent(loop.run(_run_input()))
    assert not parent.is_error and parent.details is not None
    record = parent.details["nestedCalls"]
    write, read = record["calls"]
    assert "arguments" not in write and write["argumentsBytes"] > 9000
    assert read["arguments"] == {"path": "recognizable.txt"}
    # Only the call the record left out has a preview.
    previews = parent.details["argumentPreviews"]
    assert set(previews) == {write["id"]}
    preview = previews[write["id"]]
    assert len(preview) == 200 and preview.endswith("...")
    assert preview.startswith('{"path"') and "recognizable.txt" in preview
    write_line, read_line = nested_lines_from_details(parent.details)
    assert write_line.arguments == preview
    assert "recognizable.txt" in nested_line_text(write_line, expanded=False)
    assert read_line.arguments == '{"path":"recognizable.txt"}'
    assert all(group_is_gone(pid) for pid in pids)


@pytest.mark.parametrize(
    "mode",
    ["success", "abort", "keyboard", "waiter-error", "unwind"],
)
def test_executable_input_scope_balances_after_service_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from contextlib import contextmanager

    from pipy_harness.native.agent.nested_calls import NestedToolCallService

    events: list[str] = []
    owner = threading.get_ident()
    active = False

    @contextmanager
    def scope():
        nonlocal active
        assert threading.get_ident() == owner and not active
        active = True
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")
            active = False

    close = NestedToolCallService.close

    def record_close(self):
        events.append("close")
        close(self)

    def waiter(done, cancel):
        assert active and threading.get_ident() == owner
        events.append("wait")
        if mode == "keyboard":
            raise KeyboardInterrupt
        if mode == "waiter-error":
            raise RuntimeError("waiter failed")
        cancel.set()
        return ToolExecutionInterruption.OPERATOR_ABORT

    def run(code, **kwargs):
        assert active and threading.get_ident() == owner
        events.append("guest")
        if mode == "unwind":
            raise SystemExit("guest unwind")
        if mode in {"abort", "keyboard", "waiter-error"}:
            kwargs["activity_waiter"](threading.Event(), threading.Event())
        return ScriptOutcome(None, output=("finished",))

    monkeypatch.setattr(NestedToolCallService, "close", record_close)
    monkeypatch.setattr("pipy_harness.native.coding.codemode_runner.run_script", run)
    code = "text(1)"
    loop, _, _ = _loop(
        tmp_path,
        code,
        runner=CodemodeCompositeRunner(execution_input_scope=scope),
        waiter=waiter,
    )
    if mode == "unwind":
        with pytest.raises(SystemExit, match="guest unwind"):
            loop.run(_run_input())
    else:
        outcome = loop.run(_run_input())
        parent = _parent(outcome)
        assert parent.is_error == (mode == "waiter-error")
    assert events.count("enter") == events.count("exit") == 1
    assert not active
    assert events[0] == "enter"
    assert events.index("guest") < events.index("close") < events.index("exit")


def test_failed_input_scope_entry_launches_no_guest_or_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[str] = []

    class FailedScope:
        def __enter__(self):
            entered.append("entry attempted")
            raise RuntimeError("input acquisition failed")

        def __exit__(self, *args):
            entered.append("release")

    def no_guest(*args, **kwargs):
        pytest.fail("failed input ownership must not launch a guest")

    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script", no_guest
    )
    loop, _, _ = _loop(
        tmp_path,
        "text(1)",
        runner=CodemodeCompositeRunner(execution_input_scope=FailedScope),
    )
    parent = _parent(loop.run(_run_input()))
    assert parent.is_error and "input acquisition failed" in parent.content.value
    assert entered == ["entry attempted"]


def test_default_headless_runner_needs_no_input_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script",
        lambda *args, **kwargs: ScriptOutcome(None, output=("headless",)),
    )
    loop, _, _ = _loop(tmp_path, "text(1)", runner=CodemodeCompositeRunner())
    assert "headless" in _parent(loop.run(_run_input())).content.value


def test_input_scope_covers_source_refusal_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    events = []

    @contextmanager
    def scope():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def no_guest(*args, **kwargs):
        pytest.fail("refused source must not launch a guest")

    monkeypatch.setattr(
        "pipy_harness.native.coding.codemode_runner.run_script", no_guest
    )
    loop, _, _ = _loop(
        tmp_path,
        '// @options: {"retries": 2}\ntext(1)',
        runner=CodemodeCompositeRunner(execution_input_scope=scope),
    )
    parent = _parent(loop.run(_run_input()))
    assert parent.is_error and "retries" in parent.content.value
    assert events == ["enter", "exit"]


def test_executable_outer_owner_keeps_paste_and_settled_wait_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import termios
    import tty

    from pipy_harness.native.terminal_driver import TerminalDriver
    from pipy_harness.native.tui import TURN_ABORTED, TURN_SETTLED, TerminalUi

    read_fd, write_fd = os.pipe()
    raw_entries = []
    restored = []
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda fd: raw_entries.append(fd))
    monkeypatch.setattr(termios, "tcsetattr", lambda *args: restored.append(args))
    with os.fdopen(read_fd) as input_stream:
        ui = TerminalUi(
            input_stream=input_stream, terminal_stream=io.StringIO(), cwd=tmp_path
        )
        done, cancel = threading.Event(), threading.Event()
        decode = TerminalDriver.read_key_if_available
        decoded: list[str | None] = []

        def read_key(self, fd, timeout):
            assert not done.is_set(), "settled work must not consume another key"
            if decoded:
                assert ui.components.input_editor.text == "/"
                assert ui.components.input_editor.pending_paste == ""
                return "esc"
            key = decode(self, fd, timeout)
            decoded.append(key)
            return key

        monkeypatch.setattr(TerminalDriver, "read_key_if_available", read_key)

        def run(*args, **kwargs):
            # Tool-command mode consumes/discards paste rather than submitting it.
            ui.components.input_editor.text = "/"
            os.write(write_fd, b"\x1b[200~literal\r\nsecond line\x1b[201~")
            assert (
                ui.wait_for_active_turn_interrupt(
                    done, cancel, poll_seconds=0, accept_commands=True
                )
                == TURN_ABORTED
            )
            assert cancel.is_set() and decoded == ["paste"]
            done.set()
            cancel.clear()
            assert ui.wait_for_active_turn_interrupt(done, cancel) == TURN_SETTLED
            assert not cancel.is_set()
            assert restored == []
            return ScriptOutcome(None, output=("finished",))

        monkeypatch.setattr(
            "pipy_harness.native.coding.codemode_runner.run_script", run
        )
        loop, _, _ = _loop(
            tmp_path,
            "text(1)",
            runner=CodemodeCompositeRunner(
                execution_input_scope=ui.components.driver.raw_mode
            ),
        )
        try:
            parent = _parent(loop.run(_run_input()))
            assert not parent.is_error, parent.content.value
        finally:
            os.close(write_fd)
    assert raw_entries == [read_fd] and len(restored) == 1
