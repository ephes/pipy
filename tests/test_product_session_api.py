"""Public persistent embedding, native effects and cancellation ownership."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider

from pipy_harness import sdk
from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentRunCompleted,
    AgentRunStarted,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.events import AssistantTextDelta, RunCancelled
from pipy_harness.native.cancellation import CancelToken, _AcceptedAbortSignal
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    _ExternalAbortSignalView,
    _ExternalClaim,
    _ExternalReservationToken,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.repl import loop_step
from pipy_harness.native.repl.session_transition import (
    CanonicalSessionLeaseRegistry,
    CanonicalSessionLeaseSlot,
    ProductSessionTransitionError,
    SessionTransitionCoordinator,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.product_api import (
    ProductSession,
    _HeadlessStream,
)
from pipy_harness.runner import HarnessRunner


class Sink:
    def __init__(self, callback: Callable[[AgentEvent], None] | None = None) -> None:
        self.events: list[AgentEvent] = []
        self.callback = callback

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.callback is not None:
            self.callback(event)


def _call(
    name: str, arguments: dict[str, object], correlation: str
) -> ProviderToolCall:
    return ProviderToolCall(correlation, name, json.dumps(arguments))


def _join(worker: threading.Thread) -> None:
    worker.join(5)
    assert not worker.is_alive()


def test_two_turn_real_tools_history_and_lifecycle_without_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("product embedding must not run an archive adapter")

    monkeypatch.setattr(HarnessRunner, "run", forbidden)
    monkeypatch.setattr(CodingSessionAdapter, "run", forbidden)
    provider = _RecordingToolProvider(
        call_script=(
            (
                _call(
                    "write",
                    {"path": "sample.txt", "content": "bounded content\n"},
                    "write-1",
                ),
            ),
            (),
            (
                _call("read", {"path": "sample.txt"}, "read-1"),
                _call(
                    "bash",
                    {"command": "test -f sample.txt && cat sample.txt", "timeout": 2},
                    "check-1",
                ),
            ),
            (),
        )
    )
    sink = Sink()
    starts: list[object] = []
    stops: list[object] = []
    start = loop_step._ReplLoopStep.fire_session_start
    stop = loop_step._ReplLoopStep.fire_session_shutdown

    def started(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        starts.append(self)
        start(self, **kwargs)

    def stopped(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        stops.append(self)
        stop(self, **kwargs)

    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_start", started)
    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_shutdown", stopped)
    diagnostics: list[str] = []
    with sdk.create_product_session(
        workspace=tmp_path,
        provider=provider,
        observer=sink,
        diagnostic_sink=diagnostics.append,
    ) as session:
        empty = session.snapshot()
        assert session._prepared.lifetime is not None
        assert session._abort._capture_queue() is session._prepared.lifetime.input_queue
        first = session.submit("write sample")
        assert (tmp_path / "sample.txt").read_text() == "bounded content\n"
        assert empty.messages == () and first.user_turn_count == 1
        assert starts and not stops
        second = session.submit("read and check")
        assert second.messages[: len(first.messages)] == first.messages
        assert provider.requests[2].messages == (
            *first.messages,
            AgentUserMessage(ProductContent("read and check")),
        )
        assert second.user_turn_count == 2 and second.tool_invocation_count == 3
        results = [m for m in second.messages if isinstance(m, AgentToolResultMessage)]
        assert len(results) == 3 and all(not m.is_error for m in results)
        assert "bounded content" in results[1].content.value
        assert "bounded content" in results[2].content.value
        with pytest.raises(FrozenInstanceError):
            second.user_turn_count = 0  # type: ignore[misc]
        assert isinstance(second.messages, tuple)
        assert len(starts) == 1 and not stops
        session.close()
        session.close()
        assert session.snapshot() == second
        with pytest.raises(RuntimeError, match="closed"):
            session.submit("later")
    assert len(stops) == 1
    assert len([e for e in sink.events if isinstance(e, AgentRunStarted)]) == 2
    assert len([e for e in sink.events if isinstance(e, AgentRunCompleted)]) == 2
    assert diagnostics and not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.parametrize(
    "content", ["/exit\n!echo literal\n\n", "!touch unwanted", "  exact whitespace  "]
)
def test_submit_is_literal_multiline_provider_content(
    tmp_path: Path, content: str
) -> None:
    provider = _RecordingToolProvider()
    with sdk.create_product_session(workspace=tmp_path, provider=provider) as session:
        state = session.submit(content)
        assert state.messages[0] == AgentUserMessage(ProductContent(content))
        assert provider.requests[0].messages[0].content.value == content
    assert not (tmp_path / "unwanted").exists()


@pytest.mark.parametrize("content", ["", " \n\t", None, 12])
def test_invalid_input_does_not_close_or_poison_session(
    tmp_path: Path, content: object
) -> None:
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider()
    ) as session:
        with pytest.raises((TypeError, ValueError)):
            session.submit(cast(str, content))
        assert session.submit("valid").user_turn_count == 1


@pytest.mark.parametrize("load", [True, False])
def test_instruction_optout_is_independent_of_fail_closed_project_trust(
    tmp_path: Path, load: bool
) -> None:
    (tmp_path / "AGENTS.md").write_text("WORKSPACE INSTRUCTION")
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "SYSTEM.md").write_text("UNTRUSTED SYSTEM")
    provider = _RecordingToolProvider()
    session = (
        sdk.create_product_session(workspace=tmp_path, provider=provider)
        if load
        else sdk.create_product_session(
            workspace=tmp_path, provider=provider, load_context_files=False
        )
    )
    with session:
        session.submit("hello")
        assert session._session.settings_manager is not None
        assert not session._session.settings_manager.project_trusted
    prompt = provider.requests[0].system_prompt
    assert ("WORKSPACE INSTRUCTION" in prompt) is load
    assert "UNTRUSTED SYSTEM" not in prompt


def test_injected_settings_and_resolved_workspace_are_reused(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "SYSTEM.md").write_text("TRUSTED SYSTEM")
    settings = SettingsManager.for_workspace(tmp_path, project_trusted=True)
    provider = _RecordingToolProvider()
    with sdk.create_product_session(
        workspace=tmp_path / ".", provider=provider, settings=settings
    ) as session:
        session.submit("hello")
        assert session._session.settings_manager is settings
        assert provider.requests[0].cwd == tmp_path.resolve()
        assert "TRUSTED SYSTEM" in provider.requests[0].system_prompt


def test_factory_validates_before_shared_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared: list[Path] = []
    prepare = CodingSessionAdapter.prepare_session_context

    def record(self: CodingSessionAdapter, cwd: Path) -> Any:
        prepared.append(cwd)
        return prepare(self, cwd)

    monkeypatch.setattr(CodingSessionAdapter, "prepare_session_context", record)
    with pytest.raises(ValueError, match="directory"):
        sdk.create_product_session(
            workspace=tmp_path / "missing", provider=_RecordingToolProvider()
        )
    assert not prepared
    with pytest.raises(ValueError, match="tool-capable"):
        sdk.create_product_session(workspace=tmp_path, provider=FakeNativeProvider())
    link = tmp_path / "link"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link.symlink_to(workspace, target_is_directory=True)
    with sdk.create_product_session(workspace=link, provider=_RecordingToolProvider()):
        assert prepared[-1] == workspace.resolve()


def test_foreign_thread_and_reentrant_operations_refuse(tmp_path: Path) -> None:
    refused: list[str] = []
    session: ProductSession

    def observer(event: AgentEvent) -> None:
        if not isinstance(event, AgentRunStarted):
            return
        for operation in (
            lambda: session.submit("nested"),
            session.fork,
            session.clone,
            session.new_session,
            lambda: session.switch_session(tmp_path / "nested.jsonl"),
            session.snapshot,
            session.close,
            session.__enter__,
        ):
            with pytest.raises(RuntimeError, match="reentrant"):
                operation()
            refused.append("reentry")

    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), observer=Sink(observer)
    ) as session:

        def foreign() -> None:
            for operation in (
                lambda: session.submit("foreign"),
                session.fork,
                session.clone,
                session.new_session,
                lambda: session.switch_session(tmp_path / "foreign.jsonl"),
                session.snapshot,
                session.close,
                session.__enter__,
            ):
                with pytest.raises(RuntimeError, match="construction thread"):
                    operation()
                refused.append("foreign")
            session.cancel()

        worker = threading.Thread(target=foreign)
        worker.start()
        _join(worker)
        assert session.submit("outer").user_turn_count == 1
    assert refused == ["foreign"] * 8 + ["reentry"] * 8


def test_construction_thread_object_identity_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider()
    ) as session:
        other = threading.Thread()
        with monkeypatch.context() as patch:
            patch.setattr(threading, "current_thread", lambda: other)
            with pytest.raises(RuntimeError, match="construction thread"):
                session.snapshot()
        assert session.snapshot().user_turn_count == 0


class BlockingProvider(_RecordingToolProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.block = True
        self.entered: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        self.entered.append(request)
        if self.block:
            self.block = False
            token = cast(CancelToken, kwargs["cancel_token"])
            self.started.set()
            try:
                assert token.event.wait(5)
                token.raise_if_cancelled()
            finally:
                self.finished.set()
        return super().complete(request, **kwargs)


def test_cross_thread_provider_cancel_then_success_and_idle_cancel(
    tmp_path: Path,
) -> None:
    provider = BlockingProvider()
    sink = Sink()
    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, observer=sink
    ) as session:

        def cancel() -> None:
            assert provider.started.wait(5)
            session.cancel()

        worker = threading.Thread(target=cancel)
        worker.start()
        try:
            session.submit("cancel me")
        finally:
            _join(worker)
        assert provider.finished.is_set()
        assert any(isinstance(e, RunCancelled) for e in sink.events)
        session.cancel()
        state = session.submit("next succeeds")
        assert state.messages[-1].content.value == "answer"
        assert state.user_turn_count == 2


def test_old_cancel_captured_before_retirement_cannot_signal_next_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = threading.Event()
    release = threading.Event()
    signalled = threading.Event()
    original = _AcceptedAbortSignal.set

    def pause(self: _AcceptedAbortSignal) -> None:
        captured.set()
        assert release.wait(5)
        original(self)
        signalled.set()

    monkeypatch.setattr(_AcceptedAbortSignal, "set", pause)
    worker: threading.Thread | None = None
    worker_failures: list[BaseException] = []
    seen = 0
    session: ProductSession

    def cancel() -> None:
        try:
            session.cancel()
        except BaseException as error:  # noqa: BLE001 - asserted after bounded join
            worker_failures.append(error)

    def observer(event: AgentEvent) -> None:
        nonlocal worker, seen
        if isinstance(event, AgentRunCompleted) and seen == 0:
            seen = 1
            worker = threading.Thread(target=cancel)
            worker.start()
            assert captured.wait(5)
        elif isinstance(event, AgentRunStarted) and seen == 1:
            seen = 2
            release.set()
            assert signalled.wait(5)

    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), observer=Sink(observer)
    ) as session:
        try:
            session.submit("first")
            state = session.submit("second")
            assert state.messages[-1].content.value == "answer"
            assert not session._abort.is_set()
        finally:
            release.set()
            if worker is not None:
                _join(worker)
    assert worker_failures == []


def test_native_abort_view_replays_outside_lock_and_detaches_exact_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _ExternalAbortSignalView()
    assert not view.is_set()
    view.cancel()
    queue = CodingInputQueue()
    view.bind(queue)
    with pytest.raises(RuntimeError, match="already bound"):
        view.bind(CodingInputQueue())
    first = queue._begin_external_operation(ProductContent("first"))
    assert first is not None
    view.cancel()
    seen: list[bool] = []
    registration_checked: list[bool] = []
    thread_failures: list[BaseException] = []
    register = _AcceptedAbortSignal.register_cancel_callback

    def assert_queue_unlocked() -> None:
        try:
            queue._external_admission_snapshot()
        except BaseException as error:  # noqa: BLE001 - asserted after bounded join
            thread_failures.append(error)

    def checked_register(
        signal: _AcceptedAbortSignal, callback: Callable[[], None]
    ) -> Callable[[], None]:
        assert view._binding_lock.acquire(blocking=False)
        view._binding_lock.release()
        reader = threading.Thread(target=assert_queue_unlocked)
        reader.start()
        _join(reader)
        registration_checked.append(True)
        return register(signal, callback)

    monkeypatch.setattr(
        _AcceptedAbortSignal, "register_cancel_callback", checked_register
    )

    def callback() -> None:
        assert view._binding_lock.acquire(blocking=False)
        view._binding_lock.release()
        reader = threading.Thread(target=assert_queue_unlocked)
        reader.start()
        _join(reader)
        seen.append(view.is_set())

    unregister = view.register_cancel_callback(callback)
    assert registration_checked == [True]
    assert thread_failures == []
    assert seen == [True]
    assert queue._settle_external(first.token) is not None
    second = queue._begin_external_operation(ProductContent("second"))
    assert second is not None
    unregister()
    first.abort_signal.set()
    assert not view.is_set()
    second_unregister = view.register_cancel_callback(callback)
    assert registration_checked == [True, True]
    assert seen == [True]
    view.cancel()
    assert second.abort_signal.is_set()
    assert seen == [True, True]
    second_unregister()
    assert queue._settle_external(second.token) is not None
    view.cancel()
    assert not view.is_set()


def test_semantic_summary_cancel_settles_before_next_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    for index in range(4):
        tree.append_message(AgentUserMessage(ProductContent(f"old {index}")))
        tree.append_message(AgentAssistantMessage(ProductContent("answer")))
    provider = BlockingProvider()
    sink = Sink()
    monkeypatch.setattr(loop_step, "should_compact_agent_history", lambda *a, **k: True)
    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, tree=tree, observer=sink
    ) as session:

        def cancel() -> None:
            assert provider.started.wait(5)
            session.cancel()

        worker = threading.Thread(target=cancel)
        worker.start()
        try:
            first = session.submit("cancel summary")
        finally:
            _join(worker)
        assert provider.requests == [] and first.compaction_count == 0
        assert provider.entered[0].system_prompt.startswith(
            "Summarize conversation context"
        )
        assert any(isinstance(e, RunCancelled) for e in sink.events)
        monkeypatch.setattr(
            loop_step, "should_compact_agent_history", lambda *a, **k: False
        )
        second = session.submit("continue")
        assert second.messages[-1].content.value == "answer"


class CooperativeTool:
    definition = ToolDefinition(
        "wait", "wait for cancellation", {"type": "object", "properties": {}}
    )

    def __init__(self) -> None:
        self.started = threading.Event()
        self.finished = threading.Event()

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            assert context.cancel_event is not None
            self.started.set()
            assert context.cancel_event.wait(5)
            return ToolExecutionResult(request.tool_request_id, "late success")
        finally:
            self.finished.set()


def test_model_tool_cancel_uses_canonical_worker_and_next_submit_succeeds(
    tmp_path: Path,
) -> None:
    tool = CooperativeTool()
    provider = _RecordingToolProvider(
        call_script=((_call("wait", {}, "wait-1"), _call("wait", {}, "skipped")),)
    )
    sink = Sink()
    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, tools={"wait": tool}, observer=sink
    ) as session:

        def cancel() -> None:
            assert tool.started.wait(5)
            session.cancel()

        worker = threading.Thread(target=cancel)
        worker.start()
        try:
            first = session.submit("use tool")
        finally:
            _join(worker)
        assert tool.finished.is_set()
        results = [m for m in first.messages if isinstance(m, AgentToolResultMessage)]
        assert len(results) == 2 and all(m.is_error for m in results)
        assert (
            "cancelled" in results[0].content.value
            and "skipped" in results[1].content.value
        )
        assert session.submit("recover").messages[-1].content.value == "answer"


def test_headless_diagnostics_forward_fragments_without_buffering() -> None:
    fragments: list[str] = []
    stream = _HeadlessStream(fragments.append)
    assert stream.write("piece") == 5
    assert stream.write("\n") == 1
    stream.flush()
    assert stream.write("") == 0
    assert fragments == ["piece", "\n"]
    assert not stream.isatty()
    with pytest.raises(RuntimeError, match="frontend input"):
        stream.readline()


def test_observer_failure_retires_lifetime_and_snapshot_remains_available(
    tmp_path: Path,
) -> None:
    def fail(event: AgentEvent) -> None:
        if isinstance(event, AgentRunStarted):
            raise LookupError("observer failure")

    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), observer=Sink(fail)
    )
    with pytest.raises(LookupError, match="observer failure"):
        session.submit("failure")
    assert session._prepared.lifetime is not None and session._prepared.lifetime.closed
    assert session._scope is None
    assert not session._abort.is_set()
    session.snapshot()
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("later")


def test_settled_extension_continuation_runs_before_submit_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "continuation.py"
    proof = tmp_path / "lifecycle.txt"
    extension.write_text(f"""
from pathlib import Path

def activate(api):
    pending = True
    @api.on("session_start")
    def start(event, ctx):
        Path({str(proof)!r}).write_text("start\\n")
    @api.on("agent_settled")
    def settled(event, ctx):
        nonlocal pending
        if pending:
            pending = False
            api.send_user_message("extension continuation")
    @api.on("session_shutdown")
    def stop(event, ctx):
        with Path({str(proof)!r}).open("a") as output:
            output.write("stop\\n")
""")
    provider = _RecordingToolProvider()
    resources = RuntimeResourceOptions(extension_paths=(extension,))
    claims: list[_ExternalClaim] = []
    settlements: list[_ExternalReservationToken] = []
    claimed_queues: list[CodingInputQueue] = []
    observed_boundaries: list[str] = []
    begin = CodingInputQueue._begin_external_operation
    settle = CodingInputQueue._settle_external

    def record_begin(
        queue: CodingInputQueue, content: ProductContent
    ) -> _ExternalClaim | None:
        claim = begin(queue, content)
        if claim is not None:
            claims.append(claim)
            claimed_queues.append(queue)
        return claim

    def record_settle(
        queue: CodingInputQueue, token: _ExternalReservationToken
    ) -> object:
        settlements.append(token)
        return settle(queue, token)

    monkeypatch.setattr(CodingInputQueue, "_begin_external_operation", record_begin)
    monkeypatch.setattr(CodingInputQueue, "_settle_external", record_settle)
    session: ProductSession
    run_starts = 0

    def observe(event: AgentEvent) -> None:
        nonlocal run_starts
        if isinstance(event, AgentRunStarted):
            run_starts += 1
            if run_starts != 2:
                return
            boundary = "continuation_start"
        elif isinstance(event, AgentRunCompleted) and not observed_boundaries:
            boundary = "first_agent_end"
        else:
            return
        assert len(claims) == 1 and len(claimed_queues) == 1
        assert settlements == []
        reservation = claimed_queues[0]._external_admission_snapshot().reservation
        assert reservation is not None
        assert reservation.token is claims[0].token and reservation.claimed
        assert session._abort._capture_latch() is claims[0].abort_signal
        observed_boundaries.append(boundary)

    with sdk.create_product_session(
        workspace=tmp_path,
        provider=provider,
        resources=resources,
        observer=Sink(observe),
    ) as session:
        first = session.submit("seed")
        assert first.user_turn_count == 2
        assert [
            m.content.value for m in first.messages if isinstance(m, AgentUserMessage)
        ] == ["seed", "extension continuation"]
        assert observed_boundaries == ["first_agent_end", "continuation_start"]
        assert len(claims) == 1
        assert settlements == [claims[0].token]
        assert not claims[0].abort_signal.is_set()
        assert proof.read_text() == "start\n"
        assert session.submit("next").user_turn_count == 3
        assert proof.read_text() == "start\n"
    assert proof.read_text() == "start\nstop\n"


def test_cancel_remains_bound_through_settled_hook_continuation(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "cancel-continuation.py"
    extension.write_text("""
def activate(api):
    pending = True
    @api.on("agent_settled")
    def settled(event, ctx):
        nonlocal pending
        if pending:
            pending = False
            api.send_user_message("extension continuation")
""")

    class ContinuationBlockingProvider(_RecordingToolProvider):
        def __init__(self) -> None:
            super().__init__()
            self.ordinary_calls = 0
            self.continuation_started = threading.Event()

        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            if not request.system_prompt.startswith("Summarize conversation context"):
                self.ordinary_calls += 1
                if self.ordinary_calls == 2:
                    token = cast(CancelToken, kwargs["cancel_token"])
                    self.continuation_started.set()
                    assert token.event.wait(5)
                    token.raise_if_cancelled()
            return super().complete(request, **kwargs)

    provider = ContinuationBlockingProvider()
    sink = Sink()
    resources = RuntimeResourceOptions(extension_paths=(extension,))
    with sdk.create_product_session(
        workspace=tmp_path,
        provider=provider,
        resources=resources,
        observer=sink,
    ) as session:
        worker_failures: list[BaseException] = []

        def cancel() -> None:
            try:
                assert provider.continuation_started.wait(5)
                session.cancel()
            except BaseException as error:  # noqa: BLE001 - checked after join
                worker_failures.append(error)

        worker = threading.Thread(target=cancel)
        worker.start()
        try:
            state = session.submit("seed")
        finally:
            _join(worker)

        assert worker_failures == []
        assert state.user_turn_count == 2
        assert any(isinstance(event, RunCancelled) for event in sink.events)
        assert not session._abort.is_set()
        assert session.submit("next").user_turn_count == 3


def test_observer_can_cancel_from_provider_delta_thread(tmp_path: Path) -> None:
    provider = FakeNativeProvider(
        supports_tool_calls=True, programmable_text_chunks=("first", "suppressed")
    )
    events: list[AgentEvent] = []
    refused: list[str] = []
    session: ProductSession

    def observer(event: AgentEvent) -> None:
        events.append(event)
        if isinstance(event, AssistantTextDelta):
            with pytest.raises(RuntimeError, match="construction thread"):
                session.snapshot()
            refused.append("snapshot")
            session.cancel()

    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, observer=Sink(observer)
    ) as session:
        session.submit("cancel in callback")
        assert any(isinstance(e, RunCancelled) for e in events)
        assert [e.delta.value for e in events if isinstance(e, AssistantTextDelta)] == [
            "first"
        ]
        assert refused == ["snapshot"]


def test_diagnostic_exception_retires_and_preserves_entry_guard(tmp_path: Path) -> None:
    armed = False
    refused: list[str] = []
    session: ProductSession

    def diagnostic(fragment: str) -> None:
        if not armed:
            return
        with pytest.raises(RuntimeError, match="reentrant"):
            session.snapshot()
        refused.append(fragment)
        raise LookupError("diagnostic failure")

    provider = _RecordingToolProvider(
        call_script=(
            (_call("write", {"path": "made.txt", "content": "kept"}, "write"),),
        )
    )
    session = sdk.create_product_session(
        workspace=tmp_path, provider=provider, diagnostic_sink=diagnostic
    )
    armed = True
    with pytest.raises(LookupError, match="diagnostic failure"):
        session.submit("write")
    assert refused and session._scope is None
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("later")


def test_startup_failure_cleans_candidate_without_synthetic_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.extensions.activation import _ExtensionCandidate

    disposed: list[object] = []
    shutdowns: list[object] = []
    dispose = _ExtensionCandidate.dispose

    def record_dispose(self: _ExtensionCandidate) -> Any:
        disposed.append(self)
        return dispose(self)

    def fail(*args: object, **kwargs: object) -> None:
        raise LookupError("start failure")

    def shutdown(*args: object, **kwargs: object) -> None:
        shutdowns.append(None)

    monkeypatch.setattr(_ExtensionCandidate, "dispose", record_dispose)
    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_start", fail)
    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_shutdown", shutdown)
    with pytest.raises(LookupError, match="start failure"):
        sdk.create_product_session(
            workspace=tmp_path, provider=_RecordingToolProvider()
        )
    assert len(disposed) == 1 and not shutdowns


def test_provider_failure_is_snapshot_observable_and_allows_retry(
    tmp_path: Path,
) -> None:
    class Fail(_RecordingToolProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            result = super().complete(request, **kwargs)
            if len(self.requests) == 1:
                return replace(
                    result,
                    status=HarnessStatus.FAILED,
                    final_text="provider unavailable",
                )
            return result

    session = sdk.create_product_session(workspace=tmp_path, provider=Fail())
    state = session.submit("fail")
    assert state.provider_failure is not None
    assert session.snapshot() == state
    assert session.submit("retry").provider_failure is None
    session.close()


def test_unexpected_provider_exception_disposes_once(tmp_path: Path) -> None:
    class Fail(_RecordingToolProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            raise LookupError("unexpected provider failure")

    session = sdk.create_product_session(workspace=tmp_path, provider=Fail())
    with pytest.raises(LookupError, match="unexpected provider failure"):
        session.submit("fail")
    assert session._scope is None
    assert session._prepared.lifetime is not None and session._prepared.lifetime.closed
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("later")


def test_no_abort_headless_stream_keeps_direct_tool_execution(tmp_path: Path) -> None:
    import io

    from pipy_harness.native.coding.session import CodingSession

    owner = threading.current_thread()
    invoked: list[bool] = []

    class DirectTool:
        definition = ToolDefinition(
            "direct", "fixture", {"type": "object", "properties": {}}
        )

        def invoke(
            self, request: ToolRequest, context: ToolContext
        ) -> ToolExecutionResult:
            assert context.cancel_event is None
            assert threading.current_thread() is owner
            invoked.append(True)
            return ToolExecutionResult(request.tool_request_id, "direct")

    provider = _RecordingToolProvider(call_script=((_call("direct", {}, "direct-1"),),))
    result = CodingSession(
        provider=provider, tool_registry={"direct": DirectTool()}
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED and invoked == [True]


def test_failed_startup_handle_is_disposed_before_factory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.extensions.activation import _ExtensionCandidate
    from pipy_harness.native.repl.wiring import _PreparedCodingSession

    failed: list[_PreparedCodingSession] = []
    close = _PreparedCodingSession.close

    def record(self: _PreparedCodingSession) -> Any:
        failed.append(self)
        return close(self)

    monkeypatch.setattr(_ExtensionCandidate, "publish", lambda candidate: False)
    monkeypatch.setattr(_PreparedCodingSession, "close", record)
    with pytest.raises(RuntimeError):
        sdk.create_product_session(
            workspace=tmp_path, provider=_RecordingToolProvider()
        )
    assert len(failed) == 1 and failed[0].lifetime is None
    with pytest.raises(RuntimeError, match="closed"):
        failed[0].drive()


@pytest.mark.parametrize("body_fails", [True, False])
def test_candidate_cleanup_failure_preserves_primary_error_at_public_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body_fails: bool
) -> None:
    from pipy_harness.native import session_generation

    reported: list[bool] = []

    def fail_report(*args: object) -> None:
        reported.append(True)
        raise KeyboardInterrupt("cleanup report failed")

    monkeypatch.setattr(session_generation, "_report_activation_cleanup", fail_report)
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider()
    )
    with pytest.raises(
        LookupError if body_fails else KeyboardInterrupt,
        match="body failure" if body_fails else "cleanup report failed",
    ):
        with session:
            session.submit("run")
            if body_fails:
                raise LookupError("body failure")
    session.close()
    assert reported == [True]
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("later")


def test_explicit_private_tree_persists_full_product_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive"
    monkeypatch.setenv("PIPY_SESSION_DIR", str(archive))
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    ) as session:
        session.submit("private product prompt")
    assert tree.path is not None
    stored = tree.path.read_text()
    assert "private product prompt" in stored and "answer" in stored
    assert not archive.exists()


def test_open_product_session_rebuilds_exact_tree_context_and_appends_once(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    ) as first:
        first.submit("first durable prompt")
    assert tree.path is not None
    before = tree.path.read_text(encoding="utf-8")
    lifecycle = tmp_path / "reopened-lifecycle.txt"
    extension = tmp_path / "reopened-lifecycle.py"
    extension.write_text(f"""
from pathlib import Path

def activate(api):
    @api.on("session_start")
    def start(event, ctx):
        with Path({str(lifecycle)!r}).open("a") as output:
            output.write("start\\n")

    @api.on("session_shutdown")
    def shutdown(event, ctx):
        with Path({str(lifecycle)!r}).open("a") as output:
            output.write("shutdown\\n")
""")

    provider = _RecordingToolProvider()
    sink = Sink()
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=tree.path,
        provider=provider,
        resources=RuntimeResourceOptions(extension_paths=(extension,)),
        observer=sink,
    ) as reopened:
        reopened.submit("second durable prompt")
    assert [message.content.value for message in provider.requests[0].messages] == [
        "first durable prompt",
        "answer",
        "second durable prompt",
    ]
    after = tree.path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.count("second durable prompt") == 1
    assert lifecycle.read_text() == "start\nshutdown\n"
    assert sum(isinstance(event, AgentRunStarted) for event in sink.events) == 1


def test_open_product_session_rebuilds_durable_compaction_context(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    tree.append_message(AgentUserMessage(ProductContent("discarded user")))
    tree.append_message(AgentAssistantMessage(ProductContent("discarded reply")))
    retained_user = tree.append_message(
        AgentUserMessage(ProductContent("retained user"))
    )
    tree.append_message(AgentAssistantMessage(ProductContent("retained reply")))
    tree.append_compaction(
        summary="durable compaction summary",
        first_kept_entry_id=retained_user.id,
        tokens_before=10,
    )
    assert tree.path is not None

    provider = _RecordingToolProvider()
    with sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=provider
    ) as reopened:
        reopened.submit("new user")

    request = provider.requests[0]
    assert [message.content.value for message in request.messages] == [
        "retained user",
        "retained reply",
        "new user",
    ]
    assert request.system_prompt.endswith("\n\ndurable compaction summary")


def test_open_product_session_refuses_invalid_or_foreign_tree_before_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared: list[Path] = []
    prepare = CodingSessionAdapter.prepare_session_context

    def record(self: CodingSessionAdapter, cwd: Path) -> Any:
        prepared.append(cwd)
        return prepare(self, cwd)

    monkeypatch.setattr(CodingSessionAdapter, "prepare_session_context", record)
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(ValueError, match="does not exist"):
        sdk.open_product_session(
            workspace=tmp_path, session_path=missing, provider=_RecordingToolProvider()
        )
    assert prepared == []

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    tree = NativeSessionTree.create(foreign, session_dir=tmp_path / "private-tree")
    assert tree.path is not None
    with pytest.raises(ValueError, match="workspace does not match"):
        sdk.open_product_session(
            workspace=tmp_path,
            session_path=tree.path,
            provider=_RecordingToolProvider(),
        )
    assert prepared == []


def test_public_persistent_path_lease_excludes_aliases_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    assert tree.path is not None
    prepared: list[Path] = []
    prepare = CodingSessionAdapter.prepare_session_context

    def record(self: CodingSessionAdapter, cwd: Path) -> Any:
        prepared.append(cwd)
        return prepare(self, cwd)

    monkeypatch.setattr(CodingSessionAdapter, "prepare_session_context", record)
    first = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    alias = tmp_path / "session-alias.jsonl"
    alias.symlink_to(tree.path)
    with pytest.raises(RuntimeError, match="already active"):
        sdk.open_product_session(
            workspace=tmp_path, session_path=alias, provider=_RecordingToolProvider()
        )
    assert len(prepared) == 1
    first.close()
    with sdk.open_product_session(
        workspace=tmp_path, session_path=alias, provider=_RecordingToolProvider()
    ):
        assert len(prepared) == 2


def test_public_persistent_aliases_bind_the_claimed_canonical_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    assert tree.path is not None
    target = tree.path.resolve()
    alias = tmp_path / "session-alias.jsonl"
    alias.symlink_to(target)
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(target.read_bytes())
    open_tree = NativeSessionTree.open

    def retarget_after_claim(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        assert path == target
        alias.unlink()
        alias.symlink_to(replacement)
        return open_tree(path, persist=persist, strict=strict)

    monkeypatch.setattr(NativeSessionTree, "open", retarget_after_claim)
    with sdk.open_product_session(
        workspace=tmp_path, session_path=alias, provider=_RecordingToolProvider()
    ) as reopened:
        reopened.submit("open canonical target")
    assert b"open canonical target" in target.read_bytes()
    assert b"open canonical target" not in replacement.read_bytes()

    create_alias = tmp_path / "create-session-alias.jsonl"
    create_alias.symlink_to(target)
    injected = open_tree(create_alias)
    prepare = CodingSessionAdapter.prepare_session_context

    def retarget_before_composition(self: CodingSessionAdapter, cwd: Path) -> Any:
        assert injected.path == target
        create_alias.unlink()
        create_alias.symlink_to(replacement)
        return prepare(self, cwd)

    monkeypatch.setattr(
        CodingSessionAdapter, "prepare_session_context", retarget_before_composition
    )
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=injected
    ) as created:
        created.submit("create canonical target")
    assert injected.path == target
    assert b"create canonical target" in target.read_bytes()
    assert b"create canonical target" not in replacement.read_bytes()


def test_persistent_path_lease_claims_before_startup_without_holding_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    entered_preparation = threading.Event()
    release_preparation = threading.Event()
    second_finished = threading.Event()
    prepared: list[Path] = []
    outcomes: list[str] = []
    prepare = CodingSessionAdapter.prepare_session_context

    def block_first(self: CodingSessionAdapter, cwd: Path) -> Any:
        prepared.append(cwd)
        if len(prepared) == 1:
            entered_preparation.set()
            assert release_preparation.wait(5)
        return prepare(self, cwd)

    monkeypatch.setattr(CodingSessionAdapter, "prepare_session_context", block_first)

    def first_lifetime() -> None:
        with sdk.create_product_session(
            workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
        ):
            outcomes.append("first")

    def conflicting_lifetime() -> None:
        assert tree.path is not None
        try:
            sdk.open_product_session(
                workspace=tmp_path,
                session_path=tree.path,
                provider=_RecordingToolProvider(),
            )
        except RuntimeError as error:
            assert "already active" in str(error)
            outcomes.append("conflict")
        finally:
            second_finished.set()

    first = threading.Thread(target=first_lifetime)
    first.start()
    assert entered_preparation.wait(5)
    second = threading.Thread(target=conflicting_lifetime)
    second.start()
    assert second_finished.wait(5)
    assert prepared == [tmp_path.resolve()]
    release_preparation.set()
    _join(first)
    _join(second)
    assert outcomes == ["conflict", "first"]


def test_persistent_path_lease_releases_after_startup_and_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    assert tree.path is not None
    from pipy_harness.native.extensions.activation import _ExtensionCandidate

    monkeypatch.setattr(_ExtensionCandidate, "publish", lambda candidate: False)
    with pytest.raises(RuntimeError):
        sdk.create_product_session(
            workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
        )
    monkeypatch.undo()

    class Fail(_RecordingToolProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            raise LookupError("terminal provider failure")

    session = sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=Fail()
    )
    with pytest.raises(LookupError, match="terminal provider failure"):
        session.submit("fail")
    with sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=_RecordingToolProvider()
    ) as reopened:
        reopened.submit("recovered")


def test_retired_product_cancel_cannot_reach_reopened_lifetime(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    assert tree.path is not None
    first = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    stale_cancel = first.cancel
    first.close()
    provider = _RecordingToolProvider()
    with sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=provider
    ) as reopened:
        stale_cancel()
        assert reopened.submit("fresh").messages[-1].content.value == "answer"
    assert len(provider.requests) == 1


def test_terminal_driver_failure_raises_after_once_only_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stops: list[object] = []
    shutdown = loop_step._ReplLoopStep.fire_session_shutdown

    def record_shutdown(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        stops.append(self)
        shutdown(self, **kwargs)

    monkeypatch.setattr(
        loop_step._ReplLoopStep, "fire_session_shutdown", record_shutdown
    )
    provider = _RecordingToolProvider(
        call_script=(tuple(_call("write", {}, f"bad-{i}") for i in range(3)),)
    )
    session = sdk.create_product_session(workspace=tmp_path, provider=provider)
    with pytest.raises(
        RuntimeError,
        match="NativeToolLoopMalformedFatal: 3 consecutive malformed tool calls",
    ):
        session.submit("produce malformed tool calls")
    assert session._prepared.lifetime is not None
    assert session._prepared.lifetime.closed
    assert session._scope is None
    assert session.snapshot().malformed_argument_count == 3
    assert not session._abort.is_set()
    session.close()
    assert len(stops) == 1
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("later")


def test_public_fork_and_clone_replace_one_persistent_tree_lifetime(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    source = tree.append_message(AgentUserMessage(ProductContent("source")))
    assert tree.path is not None
    original_path = tree.path
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    ) as session:
        forked = session.fork(source.id)
        assert forked.operation == "fork"
        assert forked.status == "completed"
        assert forked.previous is not None
        assert forked.previous.session_path == original_path
        assert forked.active.session_path is not None
        assert forked.active.session_path.parent == original_path.parent
        assert forked.active.session_path != original_path
        cloned = session.clone()
        assert cloned.operation == "clone"
        assert cloned.status == "completed"
        assert cloned.previous == forked.active
        assert cloned.active.session_id != forked.active.session_id
    assert NativeSessionTree.open(original_path).leaf_id == source.id


def test_public_fork_clone_refusals_validation_and_frozen_exports(
    tmp_path: Path,
) -> None:
    assert sdk.ProductSessionTarget is not None
    target = sdk.ProductSessionTarget("id", None, None)
    with pytest.raises(FrozenInstanceError):
        target.session_id = "other"  # type: ignore[misc]
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider()
    ) as ephemeral:
        assert ephemeral.new_session().refusal == "ephemeral_source"
        with pytest.raises(TypeError, match="session_path"):
            ephemeral.switch_session(cast(Any, "not-a-path"))
        refused = ephemeral.clone()
        assert refused.status == "refused"
        assert refused.refusal == "ephemeral_source"
        assert refused.active.session_path is None

    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    ) as session:
        assert session.clone().refusal == "missing_leaf"
        assert session.fork("unknown").refusal == "unknown_entry"
        with pytest.raises(TypeError, match="entry_id"):
            session.fork(cast(Any, 1))
        with pytest.raises(ValueError, match="nonempty"):
            session.fork("")


def test_public_fork_handoff_releases_source_and_retains_child_lease(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    tree.append_message(AgentUserMessage(ProductContent("source")))
    assert tree.path is not None
    source_path = tree.path
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    result = session.clone()
    assert result.active.session_path is not None
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=source_path,
        provider=_RecordingToolProvider(),
    ):
        pass
    with pytest.raises(RuntimeError, match="already active"):
        sdk.open_product_session(
            workspace=tmp_path,
            session_path=result.active.session_path,
            provider=_RecordingToolProvider(),
        )
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.fork()
    with pytest.raises(RuntimeError, match="closed"):
        session.clone()
    with pytest.raises(RuntimeError, match="closed"):
        session.new_session()
    with pytest.raises(RuntimeError, match="closed"):
        session.switch_session(result.active.session_path)
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=result.active.session_path,
        provider=_RecordingToolProvider(),
    ):
        pass


def test_transition_lease_slot_finishes_once_aborts_candidate_and_requires_source_slot(
    tmp_path: Path,
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    source.append_message(AgentUserMessage(ProductContent("source")))
    assert source.path is not None
    source_path = source.path.resolve()
    candidate_path = source_path.parent / "candidate.jsonl"
    slot = CanonicalSessionLeaseSlot(CanonicalSessionLeaseRegistry.claim(source_path))
    handoff = slot.prepare(candidate_path)
    handoff.abort()
    handoff.abort()
    # Candidate abort is idempotent and does not release the old current lease.
    candidate = CanonicalSessionLeaseRegistry.claim(candidate_path)
    candidate.finish()
    with pytest.raises(RuntimeError, match="already active"):
        CanonicalSessionLeaseRegistry.claim(source_path)
    slot.finish()
    slot.finish()
    released_source = CanonicalSessionLeaseRegistry.claim(source_path)
    released_source.finish()

    mismatched = CanonicalSessionLeaseSlot(
        CanonicalSessionLeaseRegistry.claim(candidate_path)
    )
    gated: list[str] = []

    def record_gate(_entry: str | None) -> bool:
        gated.append("gate")
        return True

    coordinator = SessionTransitionCoordinator(
        workspace=tmp_path,
        get_tree=lambda: source,
        set_tree=lambda _tree: (_ for _ in ()).throw(
            AssertionError("must not publish")
        ),
        session_before_fork=record_gate,
        session_before_switch=lambda _target: True,
        rebuild=lambda: (_ for _ in ()).throw(AssertionError("must not rebuild")),
        clear_extension_inputs=lambda: (_ for _ in ()).throw(
            AssertionError("must not clear")
        ),
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        coordinator.fork_external(None, operation="clone", leases=mismatched)
    assert raised.value.failure.stage == "publish"
    assert not raised.value.failure.published
    assert gated == []
    mismatched.finish()


def test_public_fork_uses_in_memory_non_message_entry_without_source_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    message = tree.append_message(AgentUserMessage(ProductContent("source")))
    label = tree.append_label_change(message.id, "kept")

    def forbid_open(*_args: object, **_kwargs: object) -> NativeSessionTree:
        raise AssertionError("public fork must not reopen its source")

    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(forbid_open))
    with sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    ) as session:
        result = session.fork(label.id)
        assert result.status == "completed"


@pytest.mark.parametrize("method", ["fork", "clone"])
@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ("        return SessionDecision(allow=True)\n", "completed"),
        ("        return SessionDecision(allow=False, reason='stay')\n", "refused"),
        ("        raise LookupError('gate crash')\n", "refused"),
    ],
)
def test_public_fork_gate_preserves_lifetime_and_uses_canonical_vocabulary(
    tmp_path: Path,
    method: str,
    body: str,
    expected_status: str,
) -> None:
    """Public fork/clone shares the terminal gate but never restarts its lifetime."""

    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    selected = tree.append_message(AgentUserMessage(ProductContent("source")))
    assert tree.path is not None
    source_path = tree.path
    proof = tmp_path / "fork-gate.txt"
    extension = tmp_path / "fork-gate.py"
    extension.write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import SessionDecision\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def start(event, ctx):\n"
        "        with PROOF.open('a') as output: output.write('start\\n')\n"
        "    @api.on('session_before_fork')\n"
        "    def gate(event, ctx):\n"
        "        with PROOF.open('a') as output:\n"
        "            output.write(f'gate:{event.operation}:{event.target}\\n')\n"
        f"{body}"
        "    @api.on('session_shutdown')\n"
        "    def stop(event, ctx):\n"
        "        with PROOF.open('a') as output: output.write('shutdown\\n')\n",
        encoding="utf-8",
    )
    resources = RuntimeResourceOptions(extension_paths=(extension,))
    session = sdk.create_product_session(
        workspace=tmp_path,
        provider=_RecordingToolProvider(),
        tree=tree,
        resources=resources,
    )
    result = session.clone() if method == "clone" else session.fork(selected.id)
    assert result.status == expected_status
    assert proof.read_text().splitlines() == [
        "start",
        f"gate:fork:{selected.id}",
    ]
    if expected_status == "completed":
        assert result.active.session_path is not None
        assert result.active.session_path != source_path
    else:
        assert result.refusal == "extension_refusal"
        assert list(source_path.parent.glob("*.jsonl")) == [source_path]
        with pytest.raises(RuntimeError, match="already active"):
            sdk.open_product_session(
                workspace=tmp_path,
                session_path=source_path,
                provider=_RecordingToolProvider(),
            )
        assert (
            session.submit("old facade remains usable").messages[-1].content.value
            == "answer"
        )
    session.close()
    assert proof.read_text().splitlines()[-1] == "shutdown"
    assert proof.read_text().count("start\n") == 1
    assert proof.read_text().count("shutdown\n") == 1


def test_public_fork_publish_failure_aborts_candidate_and_keeps_source_usable(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    tree.append_message(AgentUserMessage(ProductContent("source")))
    assert tree.path is not None
    source_path = tree.path
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    transition = session._prepared.wiring.transition
    assert transition is not None

    def fail_publish(_candidate: NativeSessionTree) -> None:
        raise LookupError("injected publication failure")

    object.__setattr__(transition, "set_tree", fail_publish)
    with pytest.raises(ProductSessionTransitionError) as raised:
        session.clone()
    failure = raised.value.failure
    assert failure.stage == "publish" and not failure.published
    assert failure.retained.session_path == source_path
    assert session._scope is not None
    assert session.submit("source still active").messages[-1].content.value == "answer"
    candidates = [
        path for path in source_path.parent.glob("*.jsonl") if path != source_path
    ]
    assert len(candidates) == 1
    # Candidate creation is durable, but its unadopted claim was released.
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=candidates[0],
        provider=_RecordingToolProvider(),
    ):
        pass
    session.close()


def test_public_fork_rebuild_failure_fails_closed_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts: list[object] = []
    stops: list[object] = []
    start = loop_step._ReplLoopStep.fire_session_start
    shutdown = loop_step._ReplLoopStep.fire_session_shutdown

    def record_start(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        starts.append(self)
        start(self, **kwargs)

    def record_shutdown(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        stops.append(self)
        shutdown(self, **kwargs)

    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_start", record_start)
    monkeypatch.setattr(
        loop_step._ReplLoopStep, "fire_session_shutdown", record_shutdown
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "private-tree")
    tree.append_message(AgentUserMessage(ProductContent("source")))
    assert tree.path is not None
    source_path = tree.path
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    transition = session._prepared.wiring.transition
    assert transition is not None

    def fail_rebuild() -> None:
        raise LookupError("injected rebuild failure")

    object.__setattr__(transition, "rebuild", fail_rebuild)
    with pytest.raises(ProductSessionTransitionError) as raised:
        session.clone()
    failure = raised.value.failure
    assert failure.stage == "rebuild" and failure.published
    child_path = failure.retained.session_path
    assert child_path is not None and child_path != source_path
    assert session._scope is None
    assert session._prepared.lifetime is not None and session._prepared.lifetime.closed
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("must fail closed")
    session.close()
    assert len(starts) == 1
    assert len(stops) == 1
    # Publication is not rolled back: both durable files survive, while both
    # lifetime claims have been released by the fail-closed retirement.
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=source_path,
        provider=_RecordingToolProvider(),
    ):
        pass
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=child_path,
        provider=_RecordingToolProvider(),
    ):
        pass


def test_public_new_and_switch_replace_history_and_handoff_leases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    source.append_message(AgentUserMessage(ProductContent("source")))
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target.append_message(AgentUserMessage(ProductContent("target")))
    assert source.path is not None and target.path is not None
    source_path = source.path
    target_path = target.path
    proof = tmp_path / "switch-hook.txt"
    extension = tmp_path / "switch-hook.py"
    extension.write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import SessionDecision\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('session_before_switch')\n"
        "    def gate(event, ctx):\n"
        "        with PROOF.open('a') as output:\n"
        "            output.write(f'{event.operation}:{event.target}\\n')\n"
        "        return SessionDecision(allow=True)\n",
        encoding="utf-8",
    )

    session = sdk.create_product_session(
        workspace=tmp_path,
        provider=_RecordingToolProvider(),
        tree=source,
        resources=RuntimeResourceOptions(extension_paths=(extension,)),
    )
    fresh = session.new_session()
    assert fresh.operation == "new" and fresh.status == "completed"
    assert fresh.previous is not None and fresh.previous.session_path == source_path
    assert fresh.active.session_path is not None
    assert fresh.active.session_path.parent == source_path.parent
    assert NativeSessionTree.open(fresh.active.session_path, strict=True).entries == []
    assert session.snapshot().messages == ()
    with sdk.open_product_session(
        workspace=tmp_path, session_path=source_path, provider=_RecordingToolProvider()
    ):
        pass

    original_open = NativeSessionTree.open

    def open_while_claimed(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        assert persist and strict
        with pytest.raises(RuntimeError, match="already active"):
            CanonicalSessionLeaseRegistry.claim(path)
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_while_claimed))
    switched = session.switch_session(target_path)
    assert switched.operation == "switch" and switched.status == "completed"
    assert switched.previous == fresh.active
    assert switched.active.session_path == target_path.resolve()
    assert proof.read_text().splitlines() == [
        "switch:new",
        f"switch:{target_path.resolve()}",
    ]
    assert [message.content.value for message in session.snapshot().messages] == [
        "target"
    ]
    with pytest.raises(RuntimeError, match="already active"):
        sdk.open_product_session(
            workspace=tmp_path,
            session_path=target_path,
            provider=_RecordingToolProvider(),
        )
    session.close()


def test_public_new_switch_gate_refuses_before_creation_or_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    source_path = source.path
    extension = tmp_path / "switch-gate.py"
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_switch')\n"
        "    def gate(event, ctx):\n"
        "        return SessionDecision(allow=False)\n",
        encoding="utf-8",
    )
    session = sdk.create_product_session(
        workspace=tmp_path,
        provider=_RecordingToolProvider(),
        tree=source,
        resources=RuntimeResourceOptions(extension_paths=(extension,)),
    )
    original_open = NativeSessionTree.open

    def forbid_open(*_args: object, **_kwargs: object) -> NativeSessionTree:
        raise AssertionError("vetoed switch must not read target")

    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(forbid_open))
    before = set(source_path.parent.glob("*.jsonl"))
    assert session.new_session().refusal == "extension_refusal"
    assert set(source_path.parent.glob("*.jsonl")) == before
    assert session.switch_session(target.path).refusal == "extension_refusal"
    assert session._scope is not None
    monkeypatch.setattr(NativeSessionTree, "open", original_open)
    session.close()


def test_public_switch_same_path_is_noop_before_gate_or_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert tree.path is not None
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=tree
    )
    transition = session._prepared.wiring.transition
    assert transition is not None
    object.__setattr__(
        transition,
        "session_before_switch",
        lambda _target: (_ for _ in ()).throw(AssertionError("must not gate")),
    )
    monkeypatch.setattr(
        NativeSessionTree,
        "open",
        staticmethod(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not load")
            )
        ),
    )
    result = session.switch_session(tree.path)
    assert result.status == "completed" and result.active == result.previous
    session.close()


def test_public_switch_strict_load_failure_releases_candidate_and_keeps_source(
    tmp_path: Path,
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("not json\n", encoding="utf-8")
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        session.switch_session(malformed)
    assert raised.value.failure.stage == "load"
    assert not raised.value.failure.published
    assert raised.value.failure.retained.session_path == source.path
    released = CanonicalSessionLeaseRegistry.claim(malformed)
    released.finish()
    assert (
        session.submit("source remains usable").messages[-1].content.value == "answer"
    )
    session.close()


def test_public_switch_workspace_mismatch_and_lease_conflict_are_nonpublishing(
    tmp_path: Path,
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    mismatch = NativeSessionTree.create(other_workspace, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert (
        source.path is not None
        and mismatch.path is not None
        and target.path is not None
    )
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        session.switch_session(mismatch.path)
    assert raised.value.failure.stage == "load" and not raised.value.failure.published
    holder = sdk.open_product_session(
        workspace=tmp_path, session_path=target.path, provider=_RecordingToolProvider()
    )
    refused = session.switch_session(target.path)
    assert refused.status == "refused" and refused.refusal == "lease_conflict"
    assert session._scope is not None
    holder.close()
    session.close()


@pytest.mark.parametrize("operation", ["new", "switch"])
def test_public_new_switch_prepublication_setter_failure_keeps_source_usable(
    tmp_path: Path, operation: str
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    transition = session._prepared.wiring.transition
    assert transition is not None
    object.__setattr__(
        transition,
        "set_tree",
        lambda _tree: (_ for _ in ()).throw(LookupError("injected publication")),
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        (
            session.new_session()
            if operation == "new"
            else session.switch_session(target.path)
        )
    assert raised.value.failure.stage == "publish"
    assert not raised.value.failure.published
    assert raised.value.failure.retained.session_path == source.path
    assert (
        session.submit("source remains usable").messages[-1].content.value == "answer"
    )
    session.close()


@pytest.mark.parametrize("operation", ["new", "switch"])
def test_public_new_switch_rebuild_failure_closes_published_facade(
    tmp_path: Path, operation: str
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    transition = session._prepared.wiring.transition
    assert transition is not None
    object.__setattr__(
        transition,
        "rebuild",
        lambda: (_ for _ in ()).throw(LookupError("injected rebuild")),
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        (
            session.new_session()
            if operation == "new"
            else session.switch_session(target.path)
        )
    assert raised.value.failure.stage == "rebuild"
    assert raised.value.failure.published
    assert session._scope is None
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("must fail closed")
    session.close()


def test_public_switch_accepts_equivalent_absolute_header_workspace_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "child").mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    source = NativeSessionTree.create(workspace, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(workspace, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    records = target.path.read_text(encoding="utf-8").splitlines()
    header = json.loads(records[0])
    header["cwd"] = str(alias / "child" / "..")
    target.path.write_text(
        "\n".join((json.dumps(header), *records[1:])) + "\n", encoding="utf-8"
    )

    with sdk.create_product_session(
        workspace=workspace, provider=_RecordingToolProvider(), tree=source
    ) as session:
        result = session.switch_session(target.path)
        assert result.status == "completed"
        assert result.active.session_path == target.path.resolve()


def test_admitted_new_and_switch_share_public_transition_semantics(
    tmp_path: Path,
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    current = [source]
    gates: list[str] = []
    rebuilds: list[None] = []
    clears: list[None] = []
    slot = CanonicalSessionLeaseSlot(CanonicalSessionLeaseRegistry.claim(source.path))

    def allow_switch(target_name: str) -> bool:
        gates.append(target_name)
        return True

    coordinator = SessionTransitionCoordinator(
        workspace=tmp_path.resolve(),
        get_tree=lambda: current[0],
        set_tree=lambda tree: current.__setitem__(0, tree),
        session_before_fork=lambda _entry: True,
        session_before_switch=allow_switch,
        rebuild=lambda: rebuilds.append(None),
        clear_extension_inputs=lambda: clears.append(None),
    )
    try:
        fresh = coordinator.new_admitted(leases=slot)
        assert fresh.status == "completed"
        assert current[0].path == fresh.active.session_path
        switched = coordinator.switch_admitted(target.path, leases=slot)
        assert switched.status == "completed"
        assert current[0].path == target.path.resolve()
        assert gates == ["new", str(target.path.resolve())]
        assert rebuilds == [None, None]
        assert clears == [None, None]
    finally:
        slot.finish()


@pytest.mark.parametrize("operation", ["new", "switch"])
def test_public_new_switch_clear_failure_closes_published_facade_and_releases_leases(
    tmp_path: Path, operation: str
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    transition = session._prepared.wiring.transition
    assert transition is not None
    object.__setattr__(
        transition,
        "clear_extension_inputs",
        lambda: (_ for _ in ()).throw(LookupError("injected clear")),
    )
    with pytest.raises(ProductSessionTransitionError) as raised:
        (
            session.new_session()
            if operation == "new"
            else session.switch_session(target.path)
        )
    failure = raised.value.failure
    assert failure.stage == "rebuild" and failure.published
    assert session._scope is None
    assert failure.retained.session_path is not None
    session.close()
    with sdk.open_product_session(
        workspace=tmp_path, session_path=source.path, provider=_RecordingToolProvider()
    ):
        pass
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=failure.retained.session_path,
        provider=_RecordingToolProvider(),
    ):
        pass


@pytest.mark.parametrize(("operation", "fails"), [("new", False), ("switch", True)])
def test_public_new_switch_preserve_once_only_facade_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, fails: bool
) -> None:
    starts: list[object] = []
    stops: list[object] = []
    start = loop_step._ReplLoopStep.fire_session_start
    shutdown = loop_step._ReplLoopStep.fire_session_shutdown

    def record_start(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        starts.append(self)
        start(self, **kwargs)

    def record_shutdown(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        stops.append(self)
        shutdown(self, **kwargs)

    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_start", record_start)
    monkeypatch.setattr(
        loop_step._ReplLoopStep, "fire_session_shutdown", record_shutdown
    )
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    target = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "trees")
    assert source.path is not None and target.path is not None
    session = sdk.create_product_session(
        workspace=tmp_path, provider=_RecordingToolProvider(), tree=source
    )
    if fails:
        transition = session._prepared.wiring.transition
        assert transition is not None
        object.__setattr__(
            transition,
            "clear_extension_inputs",
            lambda: (_ for _ in ()).throw(LookupError("injected clear")),
        )
        with pytest.raises(ProductSessionTransitionError):
            session.switch_session(target.path)
    else:
        assert session.new_session().status == "completed"
    session.close()
    assert len(starts) == 1
    assert len(stops) == 1
