"""Canonical private execution for branch-summary generation."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from test_native_coding_session_retry import (
    _PreparedProductProvider,
    _result,
    _settings,
    _Sink,
    _transient,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.events import RetryCompleted, RetryScheduled
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.repl.collaborators import (
    SessionCollaborators,
    _BranchSummaryExecution,
)
from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    NativeSessionTree,
    SessionEntry,
)
from pipy_harness.native.session_tree_commands import BranchSummarySelectionResult


def test_branch_summary_retry_is_frozen_private_and_publishes_once(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, max_retries=1)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [
                _result(
                    HarnessStatus.SUCCEEDED,
                    text="root answer",
                    usage={"input_tokens": 2},
                )
            ],
            [
                _result(
                    HarnessStatus.SUCCEEDED,
                    text="main answer",
                    usage={"input_tokens": 3},
                )
            ],
            [
                _transient(),
                _result(
                    HarnessStatus.SUCCEEDED,
                    text="private recovered summary",
                    usage={"input_tokens": 17, "output_tokens": 5},
                ),
            ],
        ]
    )
    sink = _Sink()
    session = CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
        agent_event_sink=sink,
    )
    usage_before_summary = []

    def on_attempt(request_index: int, allowance: object) -> None:
        if request_index == 2 and getattr(allowance, "attempt") == 1:
            usage_before_summary.append(session._coding_state.usage_snapshot())
            settings.set_value("retry.maxRetries", 0)

    provider.on_attempt = on_attempt
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "ROOT\nMAIN\n/tree select 1 summarize:unfinished\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert provider.ordinary_calls == 0
    assert len(provider.prepared_requests) == 3
    summary_request = provider.prepared_requests[2]
    assert summary_request.available_tools == ()
    assert "Focus on: unfinished." in summary_request.system_prompt
    assert [(item.attempt, item.max_attempts) for item in provider.allowances] == [
        (1, 2),
        (1, 2),
        (1, 2),
        (2, 2),
    ]
    assert usage_before_summary == [session._coding_state.usage_snapshot()]
    assert not any(
        isinstance(event, (RetryScheduled, RetryCompleted)) for event in sink.events
    )
    summaries = [
        entry for entry in tree.get_entries() if isinstance(entry, BranchSummaryEntry)
    ]
    assert len(summaries) == 1
    assert summaries[0].summary == "private recovered summary"


def test_exhausted_branch_summary_retry_publishes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_retries=1)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [_transient(), _transient()],
        ]
    )
    errors = io.StringIO()
    CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )

    assert [item.attempt for item in provider.allowances[-2:]] == [1, 2]
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary cancelled; tree and leaf unchanged" in errors.getvalue()


@pytest.mark.parametrize(
    ("max_retries", "failure"),
    [
        (0, _transient()),
        (3, _transient(final_text="provider made progress")),
    ],
)
def test_branch_summary_disabled_or_progress_failure_does_not_retry(
    tmp_path: Path, max_retries: int, failure: ProviderResult
) -> None:
    settings = _settings(tmp_path, max_retries=max_retries)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [failure],
        ]
    )
    CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert provider.allowances[-1].attempt == 1
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )


def test_branch_summary_original_witness_blocks_reissue(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_retries=1)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [_transient(), _result(HarnessStatus.SUCCEEDED, text="unused")],
        ]
    )

    def mutate(request_index: int, allowance: object) -> None:
        if request_index == 2 and getattr(allowance, "attempt") == 1:
            tree.set_leaf(tree.get_leaf_id())

    provider.on_attempt = mutate
    errors = io.StringIO()
    CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )

    assert [item.attempt for item in provider.allowances if item.max_attempts == 2][
        -1:
    ] == [1]
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary refused; context changed" in errors.getvalue()


def test_provider_worker_retained_control_completes_without_effect_lease_deadlock(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, max_retries=1)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [_transient(), _result(HarnessStatus.SUCCEEDED, text="unused")],
        ]
    )
    owner: list[SessionCollaborators] = []
    original_header = SessionCollaborators.active_provider_header_callback
    caller_thread = threading.get_ident()
    control_threads: list[int] = []
    cleanup_abort = threading.Event()
    watchdog_fired = threading.Event()

    def capture(collaborators: SessionCollaborators) -> object:
        owner.append(collaborators)
        return original_header(collaborators)

    def release_deadlock() -> None:
        watchdog_fired.set()
        cleanup_abort.set()

    def invoke_control(request_index: int, allowance: object) -> None:
        if request_index == 2 and getattr(allowance, "attempt") == 1:
            control_threads.append(threading.get_ident())
            cleanup_watchdog = threading.Timer(10, release_deadlock)
            cleanup_watchdog.start()
            try:
                owner[-1].extension_set_session_name("worker-owned")
            finally:
                cleanup_watchdog.cancel()
                cleanup_watchdog.join()

    monkeypatch.setattr(
        SessionCollaborators, "active_provider_header_callback", capture
    )
    provider.on_attempt = invoke_control
    errors = io.StringIO()
    CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
        abort_event=cleanup_abort,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )

    assert not watchdog_fired.is_set()
    assert control_threads and control_threads[0] != caller_thread
    assert tree.name == "worker-owned"
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary refused; context changed" in errors.getvalue()


def test_callback_abort_is_registered_before_branch_provider_and_cancels(
    tmp_path: Path,
) -> None:
    class Signal:
        def __init__(self) -> None:
            self._set = False
            self.callback: Callable[[], None] | None = None
            self.registrations = 0
            self.removals = 0

        def is_set(self) -> bool:
            return self._set

        def register_cancel_callback(
            self, callback: Callable[[], None]
        ) -> Callable[[], None]:
            assert self.callback is None
            self.callback = callback
            self.registrations += 1

            def remove() -> None:
                if self.callback is callback:
                    self.callback = None
                    self.removals += 1

            return remove

        def set(self) -> None:
            self._set = True
            assert self.callback is not None
            self.callback()

    @dataclass
    class Provider:
        signal: Signal
        started: threading.Event
        name: str = "plain"
        model_id: str = "plain-model"
        supports_tool_calls: bool = True
        calls: int = 0

        def complete(
            self,
            request: ProviderRequest,
            *,
            cancel_token: CancelToken | None = None,
            **_kwargs: object,
        ) -> ProviderResult:
            self.calls += 1
            if request.system_prompt.startswith("Summarize the following abandoned"):
                assert self.signal.callback is not None
                assert cancel_token is not None
                self.started.set()
                assert cancel_token.event.wait(2)
                cancel_token.raise_if_cancelled()
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ordinary answer",
            )

    signal = Signal()
    started = threading.Event()
    provider = Provider(signal, started)
    tree = NativeSessionTree.create(tmp_path, persist=False)

    def cancel_started() -> None:
        assert started.wait(2)
        signal.set()

    canceller = threading.Thread(target=cancel_started, daemon=True)
    canceller.start()
    errors = io.StringIO()
    CodingSession(
        provider=provider,
        native_session=tree,
        abort_event=signal,  # type: ignore[arg-type]
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    canceller.join(1)

    assert not canceller.is_alive()
    assert signal.registrations == signal.removals == 3
    assert signal.callback is None
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary cancelled; tree and leaf unchanged" in errors.getvalue()


def test_callback_abort_cancels_branch_retry_backoff(  # noqa: C901
    tmp_path: Path,
) -> None:
    class Signal:
        def __init__(self) -> None:
            self._set = False
            self.callback: Callable[[], None] | None = None
            self.registrations = 0
            self.removals = 0

        def is_set(self) -> bool:
            return self._set

        def register_cancel_callback(
            self, callback: Callable[[], None]
        ) -> Callable[[], None]:
            assert self.callback is None
            self.callback = callback
            self.registrations += 1
            if self.registrations == 4:
                self._set = True
                callback()
                cancelled.set()

            def remove() -> None:
                if self.callback is callback:
                    self.callback = None
                    self.removals += 1

            return remove

        def set(self) -> None:
            self._set = True
            assert self.callback is not None
            self.callback()

    signal = Signal()
    settings = _settings(tmp_path, max_retries=1, base_delay_ms=500)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[
            [_result(HarnessStatus.SUCCEEDED, text="root answer")],
            [_result(HarnessStatus.SUCCEEDED, text="main answer")],
            [_transient(), _result(HarnessStatus.SUCCEEDED, text="unused")],
        ]
    )
    cancelled = threading.Event()

    errors = io.StringIO()
    CodingSession(
        provider=provider,
        settings_manager=settings,
        native_session=tree,
        abort_event=signal,  # type: ignore[arg-type]
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )

    assert cancelled.wait(1)
    assert [item.attempt for item in provider.allowances[-1:]] == [1]
    assert signal.registrations == signal.removals == 4
    assert signal.callback is None
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary cancelled; tree and leaf unchanged" in errors.getvalue()


def test_manual_branch_pending_input_settlement_is_separate_from_prefill() -> None:
    actions: list[str] = []
    pending = SimpleNamespace(
        restore_pending_to_editor=lambda: actions.append("restore"),
        promote_pending_to_drain=lambda: actions.append("promote"),
    )
    executions = [
        _BranchSummaryExecution(
            BranchSummarySelectionResult(False),
            AgentCancellationReason.OPERATOR_ABORT,
        )
    ]
    owner = cast(
        SessionCollaborators,
        SimpleNamespace(
            terminal_ui=SimpleNamespace(
                components=SimpleNamespace(pending_messages=pending)
            ),
            _execute_branch_summary=lambda _target, _directive: executions[0],
        ),
    )
    target = cast(SessionEntry, SimpleNamespace())

    result = SessionCollaborators.select_with_branch_summary(owner, target, "summarize")
    assert result == BranchSummarySelectionResult(False)
    assert actions == ["restore"]

    executions[0] = _BranchSummaryExecution(BranchSummarySelectionResult(False))
    result = SessionCollaborators.select_with_branch_summary(owner, target, "summarize")
    assert result == BranchSummarySelectionResult(False)
    assert actions == ["restore", "promote"]
