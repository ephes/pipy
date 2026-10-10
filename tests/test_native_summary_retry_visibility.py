"""Private auxiliary retry visibility through real product sessions."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_native_coding_session_retry import _settings

from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderAttemptAllowance, StreamChunkSink
from pipy_harness.native.repl import loop_step
from pipy_harness.native.session_tree import NativeSessionTree


@dataclass
class _Observer:
    events: list[dict[str, Any]] = field(default_factory=list)
    on_event: Callable[[dict[str, Any]], None] | None = None

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)


class _PlainProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.summary_calls = 0
        self.failures = 1
        self.wait_on_second = False
        self.on_summary: Callable[[int], None] | None = None
        self.summary_requests: list[ProviderRequest] = []
        self.summary_sinks: list[tuple[object, object]] = []

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        summary = request.user_prompt.startswith("Provide the")
        if summary:
            self.summary_calls += 1
            self.summary_requests.append(request)
            self.summary_sinks.append((stream_sink, reasoning_sink))
            if self.on_summary is not None:
                self.on_summary(self.summary_calls)
            if self.wait_on_second and self.summary_calls == 2:
                assert cancel_token is not None
                assert cancel_token.event.wait(2)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
        now = datetime.now(UTC)
        failed = summary and self.summary_calls <= self.failures
        return ProviderResult(
            status=HarnessStatus.FAILED if failed else HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="POISON_PRIVATE_PARTIAL"
            if failed
            else "accepted summary"
            if summary
            else "answer",
            error_type="PrivateProviderError" if failed else None,
            error_message="POISON_PRIVATE_FAILURE retry later" if failed else None,
            metadata={"retryable": True, "progress": "text"} if failed else None,
            usage={"input_tokens": 12345} if summary else None,
        )


class _PreparedProvider(_PlainProvider):
    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> object:
        owner = self

        class Handle:
            def complete_attempt(
                self, allowance: ProviderAttemptAllowance
            ) -> ProviderResult:
                del allowance
                return owner.complete(
                    request,
                    stream_sink=stream_sink,
                    reasoning_sink=reasoning_sink,
                    cancel_token=cancel_token,
                )

        return Handle()


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_auxiliary_retry_all_providers_emit_only_bounded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool, phase: str
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    if phase == "auto":
        monkeypatch.setattr(loop_step, "AGENT_HISTORY_MAX_MESSAGES", 5)
    provider = _PreparedProvider() if prepared else _PlainProvider()
    observer = _Observer()
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    commands = (
        "ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"
        if phase == "branch"
        else "a\nb\nc\nd\n" + ("/compact\n" if phase == "manual" else "") + "/exit\n"
    )
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path, max_retries=1),
        native_session=tree,
        automation_observer=observer,
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(commands),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert provider.summary_calls == 2
    statuses = [
        e for e in observer.events if e["type"].startswith("summarization_retry_")
    ]
    assert [e["type"] for e in statuses] == [
        "summarization_retry_scheduled",
        "summarization_retry_attempt_start",
        "summarization_retry_finished",
    ]
    source = "branchSummary" if phase == "branch" else "compaction"
    assert all(e["source"] == source for e in statuses)
    assert statuses[0]["attempt"] == statuses[0]["maxAttempts"] == 1
    assert statuses[0]["delayMs"] == 1
    assert statuses[-1]["outcome"] == "succeeded"
    assert "POISON" not in repr(observer.events)
    assert "12345" not in repr(statuses)
    assert provider.summary_sinks == [(None, None), (None, None)]
    assert len({id(request) for request in provider.summary_requests}) == 1
    assert not any(e["type"].startswith("auto_retry_") for e in observer.events)


def _commands(phase: str) -> str:
    return (
        "ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"
        if phase == "branch"
        else "a\nb\nc\nd\n" + ("/compact\n" if phase == "manual" else "") + "/exit\n"
    )


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, prepared: bool
):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    if phase == "auto":
        monkeypatch.setattr(loop_step, "AGENT_HISTORY_MAX_MESSAGES", 5)
    provider = _PreparedProvider() if prepared else _PlainProvider()
    observer = _Observer()
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path, max_retries=1),
        native_session=tree,
        automation_observer=observer,
        abort_event=threading.Event(),
    )
    return session, provider, observer, tree


def _run(session: CodingSession, tmp_path: Path, phase: str) -> None:
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(_commands(phase)),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )


def _statuses(observer: _Observer):
    return [e for e in observer.events if e["type"].startswith("summarization_retry_")]


def _assert_no_summary(tree: NativeSessionTree) -> None:
    assert not any(
        e.type in {"compaction", "branch_summary"} for e in tree.get_entries()
    )


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
@pytest.mark.parametrize("exception_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("point", ["scheduled", "attempt_start", "finished"])
def test_auxiliary_observer_primary_survives_secondary_failure_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prepared: bool,
    phase: str,
    point: str,
    exception_type: type[BaseException],
) -> None:
    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)
    from pipy_harness.native.repl.provider_selection import ProviderMutationEffects

    owners: list[ProviderMutationEffects] = []
    original_compact = ProviderMutationEffects.compact_context

    def capture(owner: ProviderMutationEffects, *args: Any, **kwargs: Any):
        owners.append(owner)
        return original_compact(owner, *args, **kwargs)

    monkeypatch.setattr(ProviderMutationEffects, "compact_context", capture)
    primary = exception_type("first observer error")
    secondary = RuntimeError("secondary private observer error")

    def fail(event: dict[str, Any]) -> None:
        if event["type"] == "summarization_retry_" + point:
            raise primary
        if event["type"] == "summarization_retry_finished":
            raise secondary

    observer.on_event = fail
    with pytest.raises(exception_type) as caught:
        _run(session, tmp_path, phase)
    assert caught.value is primary
    if phase == "auto":
        assert not owners[-1].ctl.compaction_active
        ends = [e for e in observer.events if e["type"] == "compaction_end"]
        assert len(ends) == 1 and ends[0]["errorMessage"] == "Compaction failed"
    assert provider.summary_calls == (2 if point == "finished" else 1)
    if point != "finished":
        assert any("RuntimeError" in note for note in primary.__notes__)
    assert len([e for e in _statuses(observer) if e["type"].endswith("finished")]) == 1
    _assert_no_summary(tree)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
@pytest.mark.parametrize("active", [False, True])
def test_auxiliary_cancellation_closes_status_without_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prepared: bool,
    phase: str,
    active: bool,
) -> None:
    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)
    assert session.abort_event is not None
    abort = session.abort_event

    def observe(event: dict[str, Any]) -> None:
        if not active and event["type"] == "summarization_retry_scheduled":
            abort.set()

    def invoke(attempt: int) -> None:
        if active and attempt == 2:
            abort.set()

    observer.on_event = observe
    provider.on_summary = invoke
    provider.wait_on_second = active
    _run(session, tmp_path, phase)
    statuses = _statuses(observer)
    assert statuses[-1]["outcome"] == "cancelled"
    assert provider.summary_calls == (2 if active else 1)
    assert sum(e["type"].endswith("attempt_start") for e in statuses) == int(active)
    _assert_no_summary(tree)
    assert "POISON" not in repr(observer.events)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_auxiliary_actual_start_observer_stale_context_blocks_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool, phase: str
) -> None:
    from pipy_harness.native.coding.state import CodingContextChangedError

    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)

    def mutate(event: dict[str, Any]) -> None:
        if event["type"] == "summarization_retry_attempt_start":
            tree.set_leaf(tree.get_leaf_id())

    observer.on_event = mutate
    if phase == "auto":
        with pytest.raises(CodingContextChangedError):
            _run(session, tmp_path, phase)
    else:
        _run(session, tmp_path, phase)
    assert provider.summary_calls == 1
    assert _statuses(observer)[-1]["outcome"] == "stale"
    _assert_no_summary(tree)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_auxiliary_exhaustion_and_frozen_policy_are_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool, phase: str
) -> None:
    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)
    provider.failures = 9

    def change_settings(attempt: int) -> None:
        if attempt == 1:
            assert session.settings_manager is not None
            session.settings_manager.set_value("retry.maxRetries", 0)

    provider.on_summary = change_settings
    _run(session, tmp_path, phase)
    assert provider.summary_calls == 2
    statuses = _statuses(observer)
    assert statuses[-1]["outcome"] == "failed"
    assert statuses[-1]["errorMessage"] == "Summarization failed"
    _assert_no_summary(tree)
    assert "POISON" not in repr(observer.events)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_finished_success_does_not_mean_summary_was_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool, phase: str
) -> None:
    from pipy_harness.native.coding.state import CodingContextChangedError

    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)

    def mutate(event: dict[str, Any]) -> None:
        if event["type"] == "summarization_retry_finished":
            tree.set_leaf(tree.get_leaf_id())

    observer.on_event = mutate
    if phase == "auto":
        with pytest.raises(CodingContextChangedError):
            _run(session, tmp_path, phase)
    else:
        _run(session, tmp_path, phase)
    assert provider.summary_calls == 2
    finished = [e for e in _statuses(observer) if e["type"].endswith("finished")]
    assert len(finished) == 1 and finished[0]["outcome"] == "succeeded"
    _assert_no_summary(tree)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_disabled_auxiliary_retry_does_not_invent_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool, phase: str
) -> None:
    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, prepared)
    assert session.settings_manager is not None
    session.settings_manager.set_value("retry.maxRetries", 0)
    _run(session, tmp_path, phase)
    assert provider.summary_calls == 1
    assert not _statuses(observer)
    _assert_no_summary(tree)
    assert "POISON" not in repr(observer.events)


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("trigger", ["manual", "auto"])
def test_accepted_auxiliary_persistence_error_wins_over_loader_cleanup(
    tmp_path: Path, trigger: str, projected: bool
) -> None:
    from dataclasses import replace

    from test_native_semantic_compaction import _result
    from test_native_semantic_compaction_retry import _prepared_fixture, _transient

    from pipy_harness.native.coding.product_session import (
        CodingProductSessionCoordinator,
    )
    from pipy_harness.native.coding.summary_retry import (
        SummaryRetryFinished,
        SummaryRetryOutcome,
    )

    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("ACCEPTED")])
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    primary = OSError("accepted append failed")

    def fail_persistence(_action: object) -> None:
        raise primary

    product = CodingProductSessionCoordinator(
        state=effects.coding_state,
        port=replace(
            effects.product_session._port,
            apply_compaction_callback=fail_persistence,  # type: ignore[type-var]
        ),
    )
    product.rebuild_active_history()
    statuses: list[object] = []
    cleanup_calls = 0

    def loader(source: object) -> None:
        nonlocal cleanup_calls
        if source is None:
            cleanup_calls += 1
            raise RuntimeError("cleanup failure")

    effects = replace(
        effects,
        product_session=product,
        summary_retry_status=statuses.append,
        summary_retry_loader=loader,
    )
    if projected:
        outcome = effects.compact_context(trigger, project_persistence_failure=True)
        assert outcome.persistence_failed and outcome.result is not None
        assert outcome.result.summary == "ACCEPTED"
    else:
        with pytest.raises(OSError) as caught:
            effects.compact_context(trigger)
        assert caught.value is primary
    assert cleanup_calls == 1
    assert primary.__notes__ == ["summary retry cleanup failed: RuntimeError"]
    assert len(provider.allowances) == 2
    assert effects.coding_state.compaction_count == 1
    finished = [e for e in statuses if isinstance(e, SummaryRetryFinished)]
    assert len(finished) == 1 and finished[0].outcome is SummaryRetryOutcome.SUCCEEDED


@pytest.mark.parametrize("source", ["compaction", "branchSummary"])
def test_summary_countdown_precedes_loader_and_actual_start_restores_it(
    source: str,
) -> None:
    from test_native_retry_visibility import _Harness

    from pipy_harness.native.coding.summary_retry import (
        SummaryRetryAttemptStarted,
        SummaryRetryFinished,
        SummaryRetryOutcome,
        SummaryRetryScheduled,
        SummaryRetrySource,
    )

    harness = _Harness()
    kind = SummaryRetrySource(source)
    renderer = harness.renderer
    if kind is SummaryRetrySource.COMPACTION:
        renderer.start_compaction("manual")
    renderer.set_summary_retry_loader(kind)
    expected = (
        "Compacting context..." if source == "compaction" else "Summarizing branch..."
    )
    try:
        renderer.render_summary_retry(SummaryRetryScheduled(kind, 1, 3, 2000))
        assert "Retrying summarization (1/3) in 2s" in harness.wait_for(
            lambda t: "Retrying" in t
        )
        assert harness.transcript.working_warning
        renderer.render_summary_retry(SummaryRetryAttemptStarted(kind, 1, 3))
        assert expected in harness.wait_for(lambda t: expected in t)
        assert not harness.transcript.working_warning
        renderer.render_summary_retry(
            SummaryRetryFinished(kind, 1, 3, SummaryRetryOutcome.FAILED)
        )
        assert expected in harness.wait_for(lambda t: expected in t)
        assert not harness.transcript.history_blocks
    finally:
        if kind is SummaryRetrySource.COMPACTION:
            renderer.finish_compaction()
        renderer.set_summary_retry_loader(None)
    assert renderer._working_thread is None
    assert harness.transcript.working_text == ""


def test_summary_status_bounds_and_closed_values_reject_unsafe_records() -> None:
    from pipy_harness.native.coding.summary_retry import (
        SummaryRetryFinished,
        SummaryRetryScheduled,
        SummaryRetrySource,
    )

    for invalid in (True, -1, 10):
        with pytest.raises((TypeError, ValueError)):
            SummaryRetryScheduled(SummaryRetrySource.COMPACTION, invalid, 3, 1)
    with pytest.raises(TypeError):
        SummaryRetryScheduled("POISON", 1, 3, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SummaryRetryScheduled(SummaryRetrySource.COMPACTION, 1, 3, 120001)
    with pytest.raises(TypeError):
        SummaryRetryFinished(SummaryRetrySource.COMPACTION, 1, 3, "POISON")  # type: ignore[arg-type]


@pytest.mark.parametrize("source", ["compaction", "branchSummary"])
def test_unadmitted_summary_scope_does_not_arm_loader(source: str) -> None:
    from pipy_harness.native.coding.summary_retry import (
        SummaryRetrySource,
        summary_retry_scope,
    )

    calls: list[object] = []
    with summary_retry_scope(SummaryRetrySource(source), None, calls.append):
        pass
    assert calls == [None]


def test_stale_branch_capture_does_not_publish_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.repl.collaborators import SessionCollaborators

    session, provider, observer, _tree = _fixture(
        tmp_path, monkeypatch, "branch", False
    )
    monkeypatch.setattr(
        SessionCollaborators, "_capture_branch_summary_locked", lambda *_: None
    )
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(_commands("branch")),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert provider.summary_calls == 0
    assert "Summarizing branch" not in error_stream.getvalue()
    assert not _statuses(observer)


@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
@pytest.mark.parametrize("exception_type", [OSError, KeyboardInterrupt])
def test_admitted_loader_failure_preserves_primary_and_closes_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    exception_type: type[BaseException],
) -> None:
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, False)
    primary = exception_type("loader admission failed")
    calls: list[object] = []

    def loader(_renderer: object, source: object) -> None:
        calls.append(source)
        if source is not None:
            raise primary

    monkeypatch.setattr(_ToolLoopRenderer, "set_summary_retry_loader", loader)
    with pytest.raises(exception_type) as caught:
        _run(session, tmp_path, phase)
    assert caught.value is primary
    assert calls[-1] is None and len(calls) == 2
    assert provider.summary_calls == 0
    assert not _statuses(observer)
    if phase == "auto":
        assert len([e for e in observer.events if e["type"] == "compaction_end"]) == 1
    _assert_no_summary(tree)


@pytest.mark.parametrize("phase", ["manual", "auto", "branch"])
def test_admitted_loader_mutation_revalidates_before_first_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from pipy_harness.native.coding.state import CodingContextChangedError
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, False)
    calls: list[object] = []

    def loader(_renderer: object, source: object) -> None:
        calls.append(source)
        if source is not None:
            tree.set_leaf(tree.leaf_id)

    outcomes: list[Any] = []
    if phase == "manual":
        from pipy_harness.native.repl.provider_selection import ProviderMutationEffects

        original = ProviderMutationEffects.compact_context

        def compact(owner: Any, *args: Any, **kwargs: Any):
            outcome = original(owner, *args, **kwargs)
            outcomes.append(outcome)
            return outcome

        monkeypatch.setattr(ProviderMutationEffects, "compact_context", compact)
    elif phase == "branch":
        from pipy_harness.native.repl.collaborators import SessionCollaborators

        original_branch = SessionCollaborators.select_with_branch_summary

        def branch(owner: Any, *args: Any, **kwargs: Any):
            outcome = original_branch(owner, *args, **kwargs)
            outcomes.append(outcome)
            return outcome

        monkeypatch.setattr(SessionCollaborators, "select_with_branch_summary", branch)
    monkeypatch.setattr(_ToolLoopRenderer, "set_summary_retry_loader", loader)
    if phase == "auto":
        with pytest.raises(CodingContextChangedError):
            _run(session, tmp_path, phase)
    else:
        _run(session, tmp_path, phase)
    if phase == "manual":
        assert outcomes[0].notice == "pipy: compact refused: context changed."
        assert outcomes[0].result is None
    elif phase == "branch":
        assert outcomes[0].stale and not outcomes[0].accepted
    assert calls[-1] is None and len(calls) == 2
    assert provider.summary_calls == 0
    assert not _statuses(observer)
    _assert_no_summary(tree)


@pytest.mark.parametrize("early", ["blockedPreparation", "headerStale", "emptyBranch"])
def test_production_early_summary_return_never_arms_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    early: str,
) -> None:
    from dataclasses import replace

    from pipy_harness.native.coding.compaction import CodingCompactionOutcome
    from pipy_harness.native.repl.collaborators import SessionCollaborators
    from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    phase = "branch" if early == "emptyBranch" else "manual"
    session, provider, observer, tree = _fixture(tmp_path, monkeypatch, phase, False)
    calls: list[object] = []
    monkeypatch.setattr(
        _ToolLoopRenderer,
        "set_summary_retry_loader",
        lambda _, source: calls.append(source),
    )
    if early == "emptyBranch":
        original = SessionCollaborators._capture_branch_summary_locked

        def capture(owner: SessionCollaborators, target: Any):
            work = original(owner, target)
            assert work is not None
            return replace(work, messages=())

        monkeypatch.setattr(
            SessionCollaborators, "_capture_branch_summary_locked", capture
        )
    elif early == "headerStale":
        from pipy_harness.native.repl.extension_operations import (
            SessionExtensionOperations,
        )

        outcomes: list[CodingCompactionOutcome] = []
        original_compact = ProviderMutationEffects.compact_context

        def compact(owner: Any, *args: Any, **kwargs: Any):
            outcome = original_compact(owner, *args, **kwargs)
            outcomes.append(outcome)
            return outcome

        def headers(*_args: object) -> None:
            tree.set_leaf(tree.leaf_id)

        monkeypatch.setattr(ProviderMutationEffects, "compact_context", compact)
        monkeypatch.setattr(
            SessionExtensionOperations, "provider_header_callback", headers
        )
    else:
        monkeypatch.setattr(
            ProviderMutationEffects,
            "_prepare_compaction_budget",
            lambda *_: CodingCompactionOutcome("preparation blocked"),
        )
    _run(session, tmp_path, phase)
    if early == "headerStale":
        assert len(outcomes) == 1
        assert outcomes[0].notice == "pipy: compact refused: context changed."
        assert outcomes[0].result is None
    assert calls == [None]
    assert provider.summary_calls == 0
    assert not _statuses(observer)
