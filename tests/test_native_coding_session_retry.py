"""Product-level contracts for ordinary Codex-capable request retry."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.events import (
    AgentEvent,
    AgentRunCompleted,
    AgentRunSettled,
    ProviderFailed,
    RetryCompleted,
    RetryScheduled,
    RunCancelled,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.coding.state import CodingContextChangedError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    ProviderAttemptAllowance,
    StreamChunkSink,
)
from pipy_harness.native.settings import SettingsManager


def _result(
    status: HarnessStatus,
    *,
    text: str | None = None,
    metadata: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=status,
        provider_name="openai-codex",
        model_id="gpt-test",
        started_at=now,
        ended_at=now,
        final_text=text,
        usage=usage,
        error_type="TransientError" if status is HarnessStatus.FAILED else None,
        error_message="try later" if status is HarnessStatus.FAILED else None,
        metadata=metadata,
    )


def _transient(**changes: Any) -> ProviderResult:
    return replace(
        _result(
            HarnessStatus.FAILED,
            metadata={"retryable": True, "progress": "none"},
        ),
        **changes,
    )


def _settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    max_retries: int = 1,
    provider_max_retries: int | None = None,
    base_delay_ms: int = 1,
    max_delay_ms: int = 1,
) -> SettingsManager:
    provider = {"maxRetryDelayMs": max_delay_ms}
    if provider_max_retries is not None:
        provider["maxRetries"] = provider_max_retries
    body = {
        "retry": {
            "enabled": enabled,
            "maxRetries": max_retries,
            "baseDelayMs": base_delay_ms,
            "provider": provider,
        }
    }
    path = tmp_path / "settings.json"
    path.write_text(__import__("json").dumps(body), encoding="utf-8")
    return SettingsManager(global_path=path)


@dataclass(slots=True)
class _Sink:
    events: list[AgentEvent] = field(default_factory=list)
    on_event: Callable[[AgentEvent], None] | None = None
    thread_ids: list[int] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        self.thread_ids.append(threading.get_ident())
        if self.on_event is not None:
            self.on_event(event)


@dataclass(slots=True)
class _PreparedProductProvider:
    scripts: list[list[ProviderResult | Callable[[CancelToken | None], ProviderResult]]]
    name: str = "openai-codex"
    model_id: str = "gpt-test"
    supports_tool_calls: bool = True
    prepared_requests: list[ProviderRequest] = field(default_factory=list)
    allowances: list[ProviderAttemptAllowance] = field(default_factory=list)
    ordinary_calls: int = 0
    on_attempt: Callable[[int, ProviderAttemptAllowance], None] | None = None

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        del request
        self.ordinary_calls += 1
        raise AssertionError("managed product execution must use prepared completion")

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> object:
        del stream_sink, reasoning_sink
        request_index = len(self.prepared_requests)
        self.prepared_requests.append(request)
        script = self.scripts[request_index]
        owner = self

        class _Handle:
            def complete_attempt(
                self, allowance: ProviderAttemptAllowance
            ) -> ProviderResult:
                owner.allowances.append(allowance)
                if owner.on_attempt is not None:
                    owner.on_attempt(request_index, allowance)
                outcome = script[allowance.attempt - 1]
                if callable(outcome):
                    return outcome(cancel_token)
                return outcome

        return _Handle()


def _run(
    tmp_path: Path,
    provider: object,
    settings: SettingsManager,
    prompts: str,
    *,
    sink: _Sink | None = None,
    abort_event: threading.Event | None = None,
):
    session = CodingSession(
        provider=provider,  # type: ignore[arg-type]
        settings_manager=settings,
        agent_event_sink=sink,
        abort_event=abort_event,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return session, result


def test_policy_is_captured_per_request_and_next_prompt_refreshes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, max_retries=1)
    provider = _PreparedProductProvider(
        scripts=[
            [_transient(), _result(HarnessStatus.SUCCEEDED, text="first recovered")],
            [_transient()],
            [_result(HarnessStatus.SUCCEEDED, text="third works")],
        ]
    )

    def change_policy(request_index: int, allowance: ProviderAttemptAllowance) -> None:
        if request_index == 0 and allowance.attempt == 1:
            settings.set_value("retry.maxRetries", 0)

    provider.on_attempt = change_policy
    sink = _Sink()
    session, result = _run(
        tmp_path, provider, settings, "one\ntwo\nthree\n/exit\n", sink=sink
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert provider.ordinary_calls == 0
    assert [(a.attempt, a.max_attempts) for a in provider.allowances] == [
        (1, 2),
        (2, 2),
        (1, 1),
        (1, 1),
    ]
    assert [request.user_prompt for request in provider.prepared_requests] == [
        "one",
        "two",
        "three",
    ]
    assert session._coding_state.provider_failure is None
    assert sum(isinstance(event, ProviderFailed) for event in sink.events) == 2
    assert sum(isinstance(event, AgentRunCompleted) for event in sink.events) == 4
    assert sum(isinstance(event, AgentRunSettled) for event in sink.events) == 3


@pytest.mark.parametrize(
    ("enabled", "global_count", "provider_count", "expected_attempts"),
    [
        (False, 9, 9, 1),
        (True, 2, None, 3),
        (True, 8, 0, 1),
        (True, 99, None, 10),
    ],
)
def test_enabled_inheritance_override_and_clamping_reach_product_attempts(
    tmp_path: Path,
    enabled: bool,
    global_count: int,
    provider_count: int | None,
    expected_attempts: int,
) -> None:
    settings = _settings(
        tmp_path,
        enabled=enabled,
        max_retries=global_count,
        provider_max_retries=provider_count,
    )
    provider = _PreparedProductProvider(scripts=[[_transient()] * expected_attempts])
    _run(tmp_path, provider, settings, "one\n/exit\n")

    assert len(provider.allowances) == expected_attempts
    assert all(a.max_attempts == expected_attempts for a in provider.allowances)


def test_product_retry_caps_server_delay_with_captured_setting(tmp_path: Path) -> None:
    provider = _PreparedProductProvider(
        scripts=[
            [
                _transient(
                    metadata={
                        "retryable": True,
                        "progress": "none",
                        "retry_after_seconds": 99.0,
                    }
                ),
                _result(HarnessStatus.SUCCEEDED, text="ok"),
            ]
        ]
    )
    sink = _Sink()
    _run(
        tmp_path,
        provider,
        _settings(tmp_path, max_retries=1, base_delay_ms=1, max_delay_ms=7),
        "one\n/exit\n",
        sink=sink,
    )

    scheduled = [event for event in sink.events if isinstance(event, RetryScheduled)]
    assert len(scheduled) == 1
    assert scheduled[0].delay_ms == 7


@pytest.mark.parametrize(
    "blocked_result",
    [
        _transient(final_text="partial"),
        _transient(usage={"input_tokens": 1}),
        _transient(metadata={"retryable": True, "progress": "accepted"}),
    ],
)
def test_partial_progress_payload_or_usage_does_not_block_product_retry(
    tmp_path: Path, blocked_result: ProviderResult
) -> None:
    # Pi retries a failed assistant message whatever it streamed first.
    provider = _PreparedProductProvider(
        scripts=[[blocked_result, _result(HarnessStatus.SUCCEEDED, text="ok")]]
    )
    _run(tmp_path, provider, _settings(tmp_path, max_retries=3), "one\n/exit\n")
    assert [(a.attempt, a.max_attempts) for a in provider.allowances] == [
        (1, 4),
        (2, 4),
    ]


@pytest.mark.parametrize(("enabled", "calls"), [(True, 3), (False, 1)])
def test_ordinary_provider_is_retried_when_retry_is_enabled(
    tmp_path: Path, enabled: bool, calls: int
) -> None:
    class UnsupportedProvider:
        name = "openai"
        model_id = "gpt-test"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            self.calls += 1
            return replace(
                _transient(),
                provider_name=request.provider_name,
                model_id=request.model_id,
            )

    provider = UnsupportedProvider()
    sink = _Sink()
    _run(
        tmp_path,
        provider,
        _settings(tmp_path, enabled=enabled, max_retries=2),
        "one\n/exit\n",
        sink=sink,
    )
    assert provider.calls == calls
    scheduled = [event for event in sink.events if isinstance(event, RetryScheduled)]
    assert [(event.attempt, event.max_attempts) for event in scheduled] == [
        (attempt, 2) for attempt in range(1, calls)
    ]


def test_cancellation_during_backoff_balances_retry_and_prevents_reissue(
    tmp_path: Path,
) -> None:
    abort = threading.Event()
    provider = _PreparedProductProvider(scripts=[[_transient(), _transient()]])
    sink = _Sink(
        on_event=lambda event: (
            abort.set() if isinstance(event, RetryScheduled) else None
        )
    )
    _, result = _run(
        tmp_path,
        provider,
        _settings(tmp_path, max_retries=1, base_delay_ms=5000, max_delay_ms=5000),
        "one\n/exit\n",
        sink=sink,
        abort_event=abort,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.allowances) == 1
    retries = [e for e in sink.events if isinstance(e, RetryScheduled | RetryCompleted)]
    assert [type(e) for e in retries] == [RetryScheduled, RetryCompleted]
    assert isinstance(retries[1], RetryCompleted) and not retries[1].succeeded
    assert sum(isinstance(event, RunCancelled) for event in sink.events) == 0
    settlements = [e for e in sink.events if isinstance(e, AgentRunSettled)]
    assert len(settlements) == 1
    assert settlements[0].result.cancellation_reason is not None


def test_cancellation_during_reissued_provider_phase_has_no_late_writes(
    tmp_path: Path,
) -> None:
    abort = threading.Event()
    entered = threading.Event()

    def wait_for_cancel(token: CancelToken | None) -> ProviderResult:
        assert token is not None
        entered.set()
        assert token.event.wait(timeout=2)
        raise ProviderCancelledError()

    provider = _PreparedProductProvider(scripts=[[_transient(), wait_for_cancel]])
    sink = _Sink()

    def cancel_reissue(request_index: int, allowance: ProviderAttemptAllowance) -> None:
        del request_index
        if allowance.attempt == 2:
            abort.set()

    provider.on_attempt = cancel_reissue
    _run(
        tmp_path,
        provider,
        _settings(tmp_path, max_retries=1),
        "one\n/exit\n",
        sink=sink,
        abort_event=abort,
    )
    assert entered.is_set()
    assert [(a.attempt, a.max_attempts) for a in provider.allowances] == [
        (1, 2),
        (2, 2),
    ]
    retries = [e for e in sink.events if isinstance(e, RetryCompleted)]
    assert len(retries) == 1 and not retries[0].succeeded
    assert sum(isinstance(event, RunCancelled) for event in sink.events) == 1


def test_stale_context_before_failed_attempt_acceptance_rejects_publication(
    tmp_path: Path,
) -> None:
    provider = _PreparedProductProvider(scripts=[[_transient(), _transient()]])
    sink = _Sink()
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path, max_retries=1),
        agent_event_sink=sink,
    )

    def replace_binding(
        request_index: int, allowance: ProviderAttemptAllowance
    ) -> None:
        if request_index == 0 and allowance.attempt == 1:
            session._coding_state.refresh_provider(provider)

    provider.on_attempt = replace_binding
    with pytest.raises(CodingContextChangedError):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("one\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    assert len(provider.allowances) == 1
    retries = [e for e in sink.events if isinstance(e, RetryScheduled | RetryCompleted)]
    # Stale state is refused before failed-attempt acceptance or scheduling.
    assert retries == []
    assert not any(
        isinstance(message, AgentAssistantMessage)
        for message in session._coding_state.messages
    )


@pytest.mark.parametrize("cancel_backoff", [False, True])
def test_retry_sdk_result_is_only_logical_settlement(
    tmp_path: Path, cancel_backoff: bool
) -> None:
    from pipy_harness.native.agent.results import AgentRunOutcome
    from pipy_harness.native.agent_adapters import SdkAgentEventAdapter

    sdk = SdkAgentEventAdapter()
    abort = threading.Event()
    provider = _PreparedProductProvider(
        scripts=[
            [_transient(), _transient(), _result(HarnessStatus.SUCCEEDED, text="ok")]
        ]
    )
    sink = _Sink()

    def observe(event: AgentEvent) -> None:
        sdk.emit(event)
        if isinstance(event, AgentRunCompleted):
            assert sdk.result is None
        if cancel_backoff and isinstance(event, RetryScheduled):
            abort.set()

    sink.on_event = observe
    _run(
        tmp_path,
        provider,
        _settings(tmp_path, max_retries=2),
        "one\n/exit\n",
        sink=sink,
        abort_event=abort,
    )
    settled = [e for e in sink.events if isinstance(e, AgentRunSettled)]
    assert len(settled) == 1 and sdk.result is settled[0].result
    assert sdk.result.outcome is (
        AgentRunOutcome.CANCELLED if cancel_backoff else AgentRunOutcome.SUCCEEDED
    )
    assistants = [
        m for m in sdk.result.messages if isinstance(m, AgentAssistantMessage)
    ]
    assert len(assistants) == (1 if cancel_backoff else 3)


@pytest.mark.parametrize("interruptible", [False, True])
@pytest.mark.parametrize(
    "failed_append, end_observer_raises", [(1, False), (2, False), (2, True)]
)
def test_failed_attempt_append_failure_is_primary_and_prevents_reissue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruptible: bool,
    failed_append: int,
    end_observer_raises: bool,
) -> None:
    from pipy_harness.native.session_tree import NativeSessionTree

    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    original = NativeSessionTree.append_message
    provider = _PreparedProductProvider(
        scripts=[
            [_transient(), _transient(), _result(HarnessStatus.SUCCEEDED, text="ok")]
        ]
    )

    class EndSink(_Sink):
        def emit(self, event: AgentEvent) -> None:
            super().emit(event)
            if end_observer_raises and isinstance(event, RetryCompleted):
                raise RuntimeError("secondary retry-end observer failed")

    sink = EndSink()
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path, max_retries=2),
        native_session=tree,
        agent_event_sink=sink,
        abort_event=threading.Event() if interruptible else None,
    )
    primary = OSError("failed-attempt append failed")

    append_count = 0

    def append(owner: NativeSessionTree, message: Any) -> Any:
        nonlocal append_count
        if isinstance(message, AgentAssistantMessage):
            assert session._coding_state.messages[-1] is message
            append_count += 1
            if append_count == failed_append:
                raise primary
        return original(owner, message)

    monkeypatch.setattr(NativeSessionTree, "append_message", append)
    with pytest.raises(OSError) as caught:
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("one\n/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    assert caught.value is primary
    if end_observer_raises:
        assert any("RuntimeError" in note for note in primary.__notes__)
    assert len(provider.allowances) == failed_append
    retries = [
        e for e in sink.events if isinstance(e, (RetryScheduled, RetryCompleted))
    ]
    assert [type(e) for e in retries] == (
        [] if failed_append == 1 else [RetryScheduled, RetryCompleted]
    )
    if failed_append == 2:
        assert isinstance(retries[-1], RetryCompleted) and not retries[-1].succeeded
        assert retries[-1].attempt == 1
    assert not any(isinstance(e, AgentRunSettled) for e in sink.events)


@pytest.mark.parametrize("interruptible", [False, True])
def test_retry_start_observer_context_change_blocks_provider_reissue(
    tmp_path: Path,
    interruptible: bool,
) -> None:
    from pipy_harness.native.agent.events import AgentRunStarted

    provider = _PreparedProductProvider(
        scripts=[[_transient(), _result(HarnessStatus.SUCCEEDED, text="stale")]]
    )
    sink = _Sink()
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path),
        agent_event_sink=sink,
        abort_event=threading.Event() if interruptible else None,
    )
    starts = 0

    def observe(event: AgentEvent) -> None:
        nonlocal starts
        if isinstance(event, AgentRunStarted):
            starts += 1
            if starts == 2:
                session._coding_state.refresh_provider(provider)

    sink.on_event = observe
    with pytest.raises(CodingContextChangedError):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("one\n/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    assert len(provider.allowances) == 1
    assert not any(isinstance(e, AgentRunSettled) for e in sink.events)
    assert not any(
        isinstance(m, AgentAssistantMessage) and m.content.value == "stale"
        for m in session._coding_state.messages
    )

    completions = [e for e in sink.events if isinstance(e, RetryCompleted)]
    assert len(completions) == 1 and completions[0].failure is not None
    assert completions[0].failure.error_type == "retry_admission_rejected"
    assert completions[0].failure.message.value == "Retry admission was rejected."


def test_retry_preserves_ordered_failed_blocks_usage_and_unexecuted_parent(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.agent.content import TextContent, ThinkingContent
    from pipy_harness.native.agent.messages import AgentStopReason
    from pipy_harness.native.models import ProviderToolCall

    failed = _transient(
        content_blocks=(
            ThinkingContent("unfinished thinking"),
            TextContent("unfinished answer"),
            ProviderToolCall("parent", "codemode", '{"code":"await read('),
        ),
        usage={"input_tokens": 7, "output_tokens": 3},
    )
    provider = _PreparedProductProvider(
        scripts=[[failed, _result(HarnessStatus.SUCCEEDED, text="ok")]]
    )
    session, _ = _run(tmp_path, provider, _settings(tmp_path), "one\n/exit\n")
    messages = [
        m
        for m in session._coding_state.messages
        if isinstance(m, AgentAssistantMessage)
    ]
    assert messages[0].stop_reason is AgentStopReason.ERROR
    assert messages[0].ordered_content()[:2] == (
        ThinkingContent("unfinished thinking"),
        TextContent("unfinished answer"),
    )
    assert len(messages[0].tool_calls) == 1
    assert messages[0].tool_calls[0].tool_name == "codemode"
    assert messages[0].usage is not None
    assert messages[0].usage.input == 7 and messages[0].usage.output == 3
    assert session._coding_state.result_snapshot().tool_invocation_count == 0


@pytest.mark.parametrize("interruptible", [False, True])
def test_context_change_during_actual_backoff_keeps_error_and_rejects_reissue(
    tmp_path: Path,
    interruptible: bool,
) -> None:
    from pipy_harness.native.agent.messages import AgentStopReason
    from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree

    tree = NativeSessionTree.create(tmp_path, persist=False)
    provider = _PreparedProductProvider(
        scripts=[[_transient(), _result(HarnessStatus.SUCCEEDED, text="stale")]]
    )
    sink = _Sink()
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path),
        native_session=tree,
        agent_event_sink=sink,
        abort_event=threading.Event() if interruptible else None,
    )

    def change(event: AgentEvent) -> None:
        if isinstance(event, RetryScheduled):
            session._coding_state.refresh_provider(provider)

    sink.on_event = change
    with pytest.raises(CodingContextChangedError):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("one\n/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    assert len(provider.allowances) == 1
    retry_events = [
        e for e in sink.events if isinstance(e, (RetryScheduled, RetryCompleted))
    ]
    assert [type(e) for e in retry_events] == [RetryScheduled, RetryCompleted]
    assert isinstance(retry_events[1], RetryCompleted)
    assert retry_events[1].failure is not None
    assert retry_events[1].failure.error_type == "retry_admission_rejected"
    assistants = [
        entry.message
        for entry in tree.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentAssistantMessage)
    ]
    assert len(assistants) == 1 and assistants[0].stop_reason is AgentStopReason.ERROR
