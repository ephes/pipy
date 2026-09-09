"""Internal persistent composition shares the real stream session's owners."""

from __future__ import annotations

import io
from functools import partial
from pathlib import Path
from typing import Any, Never, cast

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.events import AgentEvent, AgentRunCompleted, RunCancelled
from pipy_harness.native.agent_loop_policy import NativeAgentProviderRequestPolicy
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.coding.session_controller import LoopStepSignal
from pipy_harness.native.extensions.activation import _ExtensionCandidate
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.repl import loop_step
from pipy_harness.native.repl.loop_scope import ReplLoopScope
from pipy_harness.native.repl.session_transition import CanonicalSessionLeaseRegistry
from pipy_harness.native.repl.wiring import _PreparedCodingSession
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_renderers import _ToolLoopRenderer


class _NoRead(io.StringIO):
    def readline(self, size: int | None = -1) -> Never:
        raise AssertionError("idle driving must not read frontend input")


class _Sink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@pytest.mark.parametrize("body_fails", [False, True])
def test_terminal_lifetime_claims_initial_tree_and_releases_on_normal_or_fatal_exit(
    tmp_path: Path, body_fails: bool
) -> None:
    tree = NativeSessionTree.create(tmp_path)
    assert tree.path is not None
    session = CodingSession(provider=_RecordingToolProvider(), native_session=tree)

    expected = LookupError if body_fails else None
    try:
        with session._open_lifetime(
            workspace_root=tmp_path,
            input_stream=_NoRead(),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        ):
            with pytest.raises(RuntimeError, match="already active"):
                with CodingSession(
                    provider=_RecordingToolProvider(), native_session=tree
                )._open_lifetime(
                    workspace_root=tmp_path,
                    input_stream=_NoRead(),
                    output_stream=io.StringIO(),
                    error_stream=io.StringIO(),
                ):
                    raise AssertionError("conflicting lifetime was admitted")
            if body_fails:
                raise LookupError("fatal terminal lifetime")
    except LookupError:
        if expected is None:
            raise

    released = CanonicalSessionLeaseRegistry.claim(tree.path)
    released.finish()


def test_terminal_initial_lease_releases_when_startup_fails_after_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path)
    assert tree.path is not None

    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise LookupError("terminal startup failed")

    monkeypatch.setattr(
        loop_step._ReplLoopStep,
        "fire_session_start",
        fail_start,
    )
    with pytest.raises(LookupError, match="terminal startup failed"):
        CodingSession(provider=_RecordingToolProvider(), native_session=tree).run(
            workspace_root=tmp_path,
            input_stream=io.StringIO(""),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    released = CanonicalSessionLeaseRegistry.claim(tree.path)
    released.finish()


def _scope(prepared: _PreparedCodingSession) -> ReplLoopScope:
    delegation = prepared.wiring.delegation
    assert delegation is not None
    callback = cast(partial[LoopStepSignal], delegation.step_once)
    return cast(ReplLoopScope, callback.keywords["scope"])


def test_two_drives_preserve_composition_history_and_release_each_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _RecordingToolProvider()
    sink = _Sink()
    session = CodingSession(provider=provider, agent_event_sink=sink)
    starts: list[object] = []
    shutdowns: list[object] = []
    start = loop_step._ReplLoopStep.fire_session_start
    shutdown = loop_step._ReplLoopStep.fire_session_shutdown

    def record_start(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        starts.append(kwargs["emitter"])
        start(self, **kwargs)

    def record_shutdown(self: loop_step._ReplLoopStep, **kwargs: Any) -> None:
        shutdowns.append(kwargs["emitter"])
        shutdown(self, **kwargs)

    monkeypatch.setattr(loop_step._ReplLoopStep, "fire_session_start", record_start)
    monkeypatch.setattr(
        loop_step._ReplLoopStep, "fire_session_shutdown", record_shutdown
    )
    with session._open_lifetime(
        workspace_root=tmp_path,
        input_stream=_NoRead(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    ) as prepared:
        scope = _scope(prepared)
        owners = (
            scope.coding_state,
            scope.coding_input_queue,
            scope.loop_controller,
            scope.ctl.generation_ref,
            scope.ctl.coding_effects,
            scope.coding_state.state_lock,
            scope.ctl.generation_ref.snapshot().generation,
        )
        assert prepared.drive() is None
        assert len(starts) == 1 and shutdowns == []
        first = "/literal provider input\nwith newline\n\n"
        prepared.enqueue_seed(ProductContent(first))
        assert prepared.drive() is None
        state = session._coding_state
        witness = state.begin_agent_run()
        state.end_agent_run(witness)
        prepared.enqueue_seed(ProductContent("!also provider input"))
        assert prepared.drive() is None
        assert len(provider.requests) == 2
        assert [m.content.value for m in provider.requests[1].messages] == [
            first,
            "answer",
            "!also provider input",
        ]
        assert len([e for e in sink.events if isinstance(e, AgentRunCompleted)]) == 2
        assert state.user_turn_count == 2
        current = _scope(prepared)
        assert all(
            a is b
            for a, b in zip(
                owners,
                (
                    current.coding_state,
                    current.coding_input_queue,
                    current.loop_controller,
                    current.ctl.generation_ref,
                    current.ctl.coding_effects,
                    current.coding_state.state_lock,
                    current.ctl.generation_ref.snapshot().generation,
                ),
                strict=True,
            )
        )
        assert state is scope.coding_state
        assert state.state_lock is scope.ctl.generation_ref.lock
        assert prepared.lifetime is not None
        assert prepared.lifetime.input_queue is scope.coding_input_queue
        assert len(starts) == 1 and shutdowns == []
        result = prepared.close()
        assert result is not None and result.user_turn_count == 2
        assert prepared.close() is result
        with pytest.raises(RuntimeError, match="closed"):
            prepared.drive()
    assert len(shutdowns) == 1
    assert scope.ctl.coding_effects.terminal


def test_repeated_idle_drives_do_not_write_prompt_separators(tmp_path: Path) -> None:
    errors = io.StringIO()
    session = CodingSession(provider=_RecordingToolProvider())
    with session._open_lifetime(
        workspace_root=tmp_path,
        input_stream=_NoRead(),
        output_stream=io.StringIO(),
        error_stream=errors,
    ) as prepared:
        startup_diagnostics = errors.getvalue()
        assert prepared.drive() is None
        assert prepared.drive() is None
        assert errors.getvalue() == startup_diagnostics


def test_idle_context_replacement_is_used_by_next_fresh_run(tmp_path: Path) -> None:
    provider = _RecordingToolProvider()
    session = CodingSession(provider=provider)
    with session._open_lifetime(
        workspace_root=tmp_path,
        input_stream=_NoRead(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    ) as prepared:
        prepared.enqueue_seed(ProductContent("old"))
        assert prepared.drive() is None
        replacement = (AgentUserMessage(ProductContent("replacement context")),)
        session._coding_state.rebuild_history(replacement)
        prepared.enqueue_seed(ProductContent("continue"))
        assert prepared.drive() is None
        assert [m.content.value for m in provider.requests[-1].messages] == [
            "replacement context",
            "continue",
        ]


def test_cancelled_automatic_preparation_settles_before_idle_without_ordinary_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    for index in range(4):
        tree.append_message(AgentUserMessage(ProductContent(f"old {index}")))
        tree.append_message(AgentAssistantMessage(ProductContent("old answer")))
    sink = _Sink()
    requests: list[ProviderRequest] = []

    class CancelSummary(_RecordingToolProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            requests.append(request)
            assert request.system_prompt.startswith("Summarize conversation context")
            raise ProviderCancelledError()

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("ordinary preparation after summary cancellation")

    monkeypatch.setattr(loop_step, "should_compact_agent_history", lambda *a, **k: True)
    monkeypatch.setattr(
        loop_step._RequestPreparationEffects, "_provider_request", forbidden
    )
    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", forbidden)
    monkeypatch.setattr(_ToolLoopRenderer, "refresh_tool_renderers", forbidden)
    session = CodingSession(
        provider=CancelSummary(), native_session=tree, agent_event_sink=sink
    )
    with session._open_lifetime(
        workspace_root=tmp_path,
        input_stream=_NoRead(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    ) as prepared:
        prepared.enqueue_seed(ProductContent("new prompt"))
        assert prepared.drive() is None
        scope = _scope(prepared)
        assert not scope.ctl.agent_settled_pending
        assert not scope.ctl.extension_in_agent_turn
        assert not scope.ctl.coding_effects.terminal
        assert sum(isinstance(e, RunCancelled) for e in sink.events) == 1
        assert sum(isinstance(e, AgentRunCompleted) for e in sink.events) == 1
        assert session._coding_state.compaction_count == 0
        witness = session._coding_state.begin_agent_run()
        session._coding_state.end_agent_run(witness)
    assert len(requests) == 1


@pytest.mark.parametrize("explicit_close", [False, True])
def test_failed_startup_handle_refuses_driving_after_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_close: bool
) -> None:
    monkeypatch.setattr(_ExtensionCandidate, "publish", lambda _candidate: False)
    session = CodingSession(provider=_RecordingToolProvider())
    with session._open_lifetime(
        workspace_root=tmp_path,
        input_stream=_NoRead(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    ) as prepared:
        result = prepared.drive()
        assert result is not None and result.status is HarnessStatus.FAILED
        assert result.error_type == "ExtensionActivationError"
        if explicit_close:
            assert prepared.close() is result
            assert prepared.close() is result
            with pytest.raises(RuntimeError, match="closed"):
                prepared.drive()
    with pytest.raises(RuntimeError, match="closed"):
        prepared.drive()


@pytest.mark.parametrize("body_fails", [False, True])
def test_candidate_cleanup_surrounds_entire_persistent_lifetime_and_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body_fails: bool
) -> None:
    from pipy_harness.native import session_generation

    reports: list[object] = []

    def fail_report(cleanup: object, sink: object) -> None:
        reports.append(cleanup)
        raise KeyboardInterrupt("cleanup report failed")

    monkeypatch.setattr(session_generation, "_report_activation_cleanup", fail_report)
    session = CodingSession(provider=_RecordingToolProvider())
    errors = io.StringIO()
    expected = LookupError if body_fails else KeyboardInterrupt
    with pytest.raises(
        expected, match="body failed" if body_fails else "cleanup report failed"
    ):
        with session._open_lifetime(
            workspace_root=tmp_path,
            input_stream=_NoRead(),
            output_stream=io.StringIO(),
            error_stream=errors,
        ) as prepared:
            assert reports == []
            assert prepared.drive() is None
            assert reports == []
            if body_fails:
                raise LookupError("body failed")
    assert len(reports) == 1
    assert _scope(prepared).ctl.coding_effects.terminal
    if body_fails:
        assert "cleanup report failed: KeyboardInterrupt" in errors.getvalue()


def test_persistent_projection_failure_disposes_unpublished_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native import session_generation
    from pipy_harness.native.repl import extension_attach

    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "startup.py").write_text("def activate(api):\n    pass\n")
    disposed: list[int] = []

    def fail_projection(*_args: object, **_kwargs: object) -> None:
        raise LookupError("projection failed")

    def report_failure(cleanup: Any, sink: object) -> None:
        disposed.append(cleanup.disposed)
        raise KeyboardInterrupt("cleanup report failed")

    monkeypatch.setattr(
        extension_attach, "build_candidate_extension_projection", fail_projection
    )
    monkeypatch.setattr(
        session_generation, "_report_activation_cleanup", report_failure
    )
    errors = io.StringIO()
    with pytest.raises(LookupError, match="projection failed"):
        with CodingSession(provider=_RecordingToolProvider())._open_lifetime(
            workspace_root=tmp_path,
            input_stream=_NoRead(),
            output_stream=io.StringIO(),
            error_stream=errors,
        ):
            raise AssertionError("failed startup yielded a live handle")
    assert disposed == [1]
    assert "cleanup report failed: KeyboardInterrupt" in errors.getvalue()
