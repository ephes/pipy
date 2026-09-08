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
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.repl import loop_step
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
    _OperationAbortBridge,
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
    assert refused == ["foreign"] * 4 + ["reentry"] * 4


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
    seen = 0
    session: ProductSession

    def observer(event: AgentEvent) -> None:
        nonlocal worker, seen
        if isinstance(event, AgentRunCompleted) and seen == 0:
            seen = 1
            worker = threading.Thread(target=session.cancel)
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


def test_abort_bridge_replays_outside_lock_and_detaches_exact_latch() -> None:
    bridge = _OperationAbortBridge()
    first = bridge.begin()
    bridge.cancel()
    seen: list[bool] = []

    def callback() -> None:
        assert bridge._lock.acquire(blocking=False)
        bridge._lock.release()
        seen.append(bridge.is_set())

    unregister = bridge.register_cancel_callback(callback)
    assert seen == [True]
    bridge.retire(first)
    second = bridge.begin()
    bridge.retire(first)
    unregister()
    first.set()
    assert not bridge.is_set()
    bridge.cancel()
    assert second.is_set()
    bridge.retire(second)
    bridge.cancel()
    assert not bridge.is_set()


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
    tmp_path: Path,
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
    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, resources=resources
    ) as session:
        first = session.submit("seed")
        assert first.user_turn_count == 2
        assert [
            m.content.value for m in first.messages if isinstance(m, AgentUserMessage)
        ] == ["seed", "extension continuation"]
        assert proof.read_text() == "start\n"
        assert session.submit("next").user_turn_count == 3
        assert proof.read_text() == "start\n"
    assert proof.read_text() == "start\nstop\n"


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
