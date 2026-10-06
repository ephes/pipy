"""DF1-F4: Pi's retry classification, retry rendering, and footer context.

Pi reference (pi-mono @4df157433): ``ai/src/utils/retry.ts``
(``isRetryableAssistantError``), ``ai/src/utils/overflow.ts``
(``isContextOverflow``), ``coding-agent`` interactive ``auto_retry_start`` /
``auto_retry_end`` rendering with ``RetryStatusIndicator``, and
``getContextUsage`` taking usage from the last successful assistant message.
"""

from __future__ import annotations

import io
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AssistantTextDelta,
    MessageStarted,
    RetryCompleted,
    RetryScheduled,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
)
from pipy_harness.native.agent.provider_retry import (
    ProviderManagedRetryPolicy,
    is_context_overflow_error_text,
    is_retryable_error_text,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.agent.results import AgentFailure
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.agent.usage_json import usage_to_json
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.frame_renderer import FrameLine, style_line
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.rendering import RenderingAgentEventAdapter
from pipy_harness.native.ui.screen import ScreenRenderInputs
from pipy_harness.native.ui.state import FinishRetry, ScheduleRetry, UiState, reduce

# Every Pi assistant message carries ``usage`` (USAGE1); these carry none.
ZERO_USAGE = usage_to_json(AgentMessageUsage())


def _failure(message: str) -> AgentFailure:
    return AgentFailure("ProviderError", ProductContent(message), retryable=True)


# -- Pi classifier -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Anthropic API request failed with HTTP status 529. overloaded_error",
        "Codex error: server_is_overloaded",
        "Rate limit reached for requests",
        "OpenAI API request failed with HTTP status 503.",
        "network error",
        "socket hang up",
        "Request timed out",
        "Processing failed. You can retry your request.",
        "websocket closed",
    ],
)
def test_pi_retryable_patterns(text: str) -> None:
    assert is_retryable_error_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "OpenAI API request failed with HTTP status 400.",
        "429 insufficient_quota",
        "Rate limit: billing hard limit",
        "Codex error: bad_request",
    ],
)
def test_pi_non_retryable_patterns(text: str) -> None:
    assert not is_retryable_error_text(text)


@pytest.mark.parametrize(
    ("text", "provider", "overflow"),
    [
        ("prompt is too long: 213462 tokens > 200000 maximum", "anthropic", True),
        ("Codex error: 50000 tokens exceeds the context window", "openai", True),
        ("context_length_exceeded", "openai", True),
        ("400 status code (no body)", "cerebras", True),
        ("400 status code (no body)", "openai", False),
        # Pi's non-overflow exclusion keeps throttling retryable.
        ("ThrottlingException: rate limit, too many tokens", "bedrock", False),
        ("OpenAI API request failed with HTTP status 503.", "openai", False),
    ],
)
def test_pi_context_overflow_patterns(text: str, provider: str, overflow: bool) -> None:
    assert is_context_overflow_error_text(text, provider) is overflow


# -- network failures from the shared JSON transport ---------------------------


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(
            socket.gaierror(8, "nodename nor servname provided, or not known")
        ),
        urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer")),
        ConnectionResetError(54, "Connection reset by peer"),
        TimeoutError("The read operation timed out"),
    ],
)
def test_anthropic_network_failure_is_retried_through_the_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """Pi retries network errors; pipy carries the transport classification."""

    calls: list[int] = []
    reply = {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def read(self, *_args: object) -> bytes:
            return json.dumps(reply).encode()

        def close(self) -> None:
            return None

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        del request, timeout
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = AnthropicProvider(model_id="claude-test", api_key="sk-test")
    events: list[object] = []

    class _Sink:
        def emit(self, event: object) -> None:
            events.append(event)

    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        ProviderRequest("system", "user", "anthropic", "claude-test", tmp_path),
        _Sink(),  # type: ignore[arg-type]
        turn_index=0,
        retry_policy=ProviderManagedRetryPolicy(4, 0.01, 1.0, 2.0, 0.0),
        before_reissue=lambda: None,
    )

    assert len(calls) == 2
    assert outcome.result is not None and outcome.result.final_text == "ok"
    assert [type(event).__name__ for event in events] == [
        "RetryScheduled",
        "RetryCompleted",
    ]


def test_certificate_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        del request, timeout
        calls.append(1)
        raise urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = AnthropicProvider(model_id="claude-test", api_key="sk-test")
    outcome = ProviderTurnExecutor(retry_sleep=lambda _delay: None).complete(
        provider,
        ProviderRequest("system", "user", "anthropic", "claude-test", tmp_path),
        _NullSink(),  # type: ignore[arg-type]
        turn_index=0,
        retry_policy=ProviderManagedRetryPolicy(4, 0.01, 1.0, 2.0, 0.0),
        before_reissue=lambda: None,
    )
    assert len(calls) == 1
    assert outcome.result is not None
    assert outcome.result.metadata == {"retryable": False}


class _NullSink:
    def emit(self, event: object) -> None:
        del event


# -- reducer -----------------------------------------------------------------


def test_reducer_maps_retry_events_and_restarts_the_stream() -> None:
    state = UiState()
    state, _ = reduce(
        state, MessageStarted(0, AgentAssistantMessage(ProductContent("")))
    )
    state, _ = reduce(state, AssistantTextDelta(0, ProductContent("Half")))
    assert state.assistant_streamed

    state, decisions = reduce(state, RetryScheduled(1, 3, 2000, _failure("boom 503")))
    assert decisions == (ScheduleRetry(1, 3, 2000, "boom 503"),)
    assert state.assistant_active and not state.assistant_streamed

    _, decisions = reduce(state, RetryCompleted(3, False, _failure("still 503")))
    assert decisions == (FinishRetry(False, 3, "still 503"),)
    _, decisions = reduce(state, RetryCompleted(1, True))
    assert decisions == (FinishRetry(True, 1, None),)


# -- TUI renderer ------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Harness:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.chrome = ExtensionChromeState()
        render_inputs = ScreenRenderInputs(lambda: 80, io.StringIO(), lambda: False)
        self.transcript = TranscriptComponent(
            PaintLock(threading.RLock()),
            lambda: None,
            reset_scrollback=lambda: None,
            render_inputs=render_inputs,
        )
        self.renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=self.chrome,
            render_inputs=render_inputs,
            interrupt_key_text=lambda: "escape",
            clock=self.clock,
        )

    def wait_for(self, predicate, deadline_seconds: float = 2.0) -> str:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            text = self.transcript.working_text
            if predicate(text):
                return text
            time.sleep(0.01)
        return self.transcript.working_text


def test_tui_retry_shows_failed_attempt_then_pi_countdown_loader() -> None:
    h = _Harness()
    adapter = RenderingAgentEventAdapter(h.renderer)
    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    h.renderer.stream_sink("Half an ans")

    adapter.emit(RetryScheduled(1, 3, 2000, _failure("Codex error: overloaded")))

    # The partial stream and Pi's ``Error: <message>`` line settle in order.
    assert h.transcript.history_blocks[-2:] == [
        ("assistant", ("Half an ans",)),
        ("error", ("Error: Codex error: overloaded",)),
    ]
    row = h.wait_for(lambda text: "Retrying" in text)
    assert row.endswith("Retrying (1/3) in 2s... (escape to cancel)")
    assert h.transcript.working_warning

    h.clock.now += 1.2
    row = h.wait_for(lambda text: "in 1s" in text)
    assert row.endswith("Retrying (1/3) in 1s... (escape to cancel)")

    # The backoff is over and the retried request runs: back to Working...
    h.clock.now += 1.0
    row = h.wait_for(lambda text: "Working..." in text)
    assert row.endswith("Working...")
    assert not h.transcript.working_warning

    adapter.emit(RetryCompleted(1, True))
    h.renderer.stream_sink("Full answer")
    h.renderer.complete_assistant_message(has_tool_calls=False)
    assert h.transcript.history_blocks[-1] == ("assistant", ("Full answer",))
    assert [kind for kind, _ in h.transcript.history_blocks].count("error") == 1


def test_tui_final_retry_failure_uses_pi_line() -> None:
    h = _Harness()
    adapter = RenderingAgentEventAdapter(h.renderer)
    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(RetryScheduled(3, 3, 8000, _failure("HTTP status 503.")))
    adapter.emit(RetryCompleted(3, False, _failure("HTTP status 503.")))

    assert h.transcript.history_blocks[-1] == (
        "error",
        ("Retry failed after 3 attempts: HTTP status 503.",),
    )
    assert h.transcript.working_text == ""


def test_tui_retry_row_shows_even_when_working_is_hidden() -> None:
    h = _Harness()
    h.chrome.working_visible = False
    h.renderer.schedule_retry(
        attempt=2, max_attempts=3, delay_ms=4000, error_message="boom"
    )
    row = h.wait_for(lambda text: "Retrying" in text)
    assert "Retrying (2/3) in 4s..." in row
    h.clock.now += 5
    assert h.wait_for(lambda text: text == "") == ""


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        ("manual", "Compacting context... (escape to cancel)"),
        ("threshold", "Auto-compacting... (escape to cancel)"),
        ("overflow", "Auto-compacting... (escape to cancel)"),
    ],
)
def test_tui_compaction_shows_pi_status_loader(reason: str, label: str) -> None:
    h = _Harness()
    # Pi shows the compaction loader even without a running turn.
    h.chrome.working_visible = False
    h.renderer.start_compaction(reason)
    row = h.wait_for(lambda text: label in text)
    assert row.endswith(label)
    assert not h.transcript.working_warning

    h.renderer.finish_compaction()
    assert h.transcript.working_text == ""


def test_working_warning_row_uses_the_warning_colour() -> None:
    style = ChromeStyle(enabled=True, truecolor=False)
    styled = style_line(
        FrameLine(" ⠋ Retrying (1/3) in 2s...", "working_warning"), style, 80
    )
    assert style.warning("⠋") in styled
    plain = style_line(FrameLine(" ⠋ Working...", "working"), style, 80)
    assert style.warning("⠋") not in plain


# -- legacy renderer -----------------------------------------------------------


def test_legacy_renderer_writes_pi_retry_lines() -> None:
    stderr = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stderr)
    renderer.schedule_retry(
        attempt=1, max_attempts=3, delay_ms=2000, error_message="HTTP status 529."
    )
    renderer.finish_retry(succeeded=True, attempt=1, final_error=None)
    renderer.finish_retry(succeeded=False, attempt=3, final_error="Retry cancelled")
    assert stderr.getvalue().splitlines() == [
        "Error: HTTP status 529.",
        "Retrying (1/3) in 2s...",
        "Retry failed after 3 attempts: Retry cancelled",
    ]


# -- JSON / RPC projection -----------------------------------------------------


def test_automation_partial_restarts_after_a_scheduled_retry() -> None:
    emitted: list[dict[str, object]] = []

    class _Sink:
        def emit(self, event: dict[str, object]) -> None:
            emitted.append(event)

    adapter = AutomationAgentEventAdapter(_Sink())  # type: ignore[arg-type]
    adapter.emit(MessageStarted(0, AgentAssistantMessage(ProductContent(""))))
    adapter.emit(AssistantTextDelta(0, ProductContent("Half")))
    adapter.emit(RetryScheduled(1, 3, 2000, _failure("Codex error: overloaded")))
    adapter.emit(AssistantTextDelta(0, ProductContent("Full")))
    adapter.emit(RetryCompleted(1, True))

    assert [event["type"] for event in emitted] == [
        "message_start",
        "message_update",
        "auto_retry_start",
        "message_update",
        "auto_retry_end",
    ]
    assert emitted[2] == {
        "type": "auto_retry_start",
        "attempt": 1,
        "maxAttempts": 3,
        "delayMs": 2000,
        "errorMessage": "Codex error: overloaded",
    }
    assert emitted[3]["message"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Full"}],
        "usage": ZERO_USAGE,
        "stopReason": "stop",
    }
    assert emitted[4] == {"type": "auto_retry_end", "success": True, "attempt": 1}


# -- footer context ------------------------------------------------------------


def test_failed_turn_keeps_the_last_successful_context_usage() -> None:
    usage = AgentUsageAccumulator()
    usage.absorb(AgentProviderUsageSample(input_tokens=1500, output_tokens=100))
    assert usage.last_total_tokens == 1600

    # A failed provider result carries no usage (Pi skips error messages).
    usage.absorb(AgentProviderUsageSample.from_mapping(None))
    assert usage.last_total_tokens == 1600

    usage.absorb(AgentProviderUsageSample(input_tokens=2000, output_tokens=50))
    assert usage.last_total_tokens == 2050


def test_footer_context_does_not_jump_after_a_failed_turn(tmp_path: Path) -> None:
    from pipy_harness.native.chrome import _ChromeFooterEffects, _context_budget_for
    from pipy_harness.native.coding.state import CodingSessionUsageSnapshot

    usage = AgentUsageAccumulator()
    usage.absorb(AgentProviderUsageSample(input_tokens=1500, output_tokens=132))
    usage.absorb(AgentProviderUsageSample.from_mapping(None))
    snapshot = CodingSessionUsageSnapshot(
        usage=usage.agent_usage(),
        last_total_tokens=usage.last_total_tokens,
    )
    effects = object.__new__(_ChromeFooterEffects)
    budget = _context_budget_for("openai-codex", "gpt-5.5", declared_window=272_000)
    pct = effects._context_used_pct(
        budget=budget,
        usage_snapshot=snapshot,
        tool_invocation_count=8,
        user_turn_count=5,
    )
    assert pct == pytest.approx(100.0 * 1632 / budget.token_budget)


# -- stored messages with F6's stopped assistants ------------------------------


class _StreamingScriptProvider:
    """An ordinary (non-prepared) provider streaming a partial per attempt."""

    name = "anthropic"
    model_id = "claude-test"
    supports_tool_calls = True

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        self.calls += 1
        outcome = self.outcomes[self.calls - 1]
        stream_sink = kwargs.get("stream_sink")
        now = datetime.now(UTC)
        if outcome == "ok":
            if callable(stream_sink):
                stream_sink("final answer")
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=request.provider_name,
                model_id=request.model_id,
                started_at=now,
                ended_at=now,
                final_text="final answer",
            )
        if callable(stream_sink):
            stream_sink(f"partial {self.calls}")
        return ProviderResult(
            status=HarnessStatus.FAILED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            error_type="AnthropicHTTPStatusError",
            error_message="Anthropic API request failed with HTTP status 529.",
            metadata={"http_status": 529, "api_error_type": "overloaded_error"},
        )


def _retry_settings(tmp_path: Path, base_delay_ms: int) -> SettingsManager:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "retry": {
                    "enabled": True,
                    "maxRetries": 2,
                    "baseDelayMs": base_delay_ms,
                    "provider": {"maxRetryDelayMs": base_delay_ms * 4},
                }
            }
        ),
        encoding="utf-8",
    )
    return SettingsManager(global_path=path)


def _stored_assistants(
    tmp_path: Path,
    provider: _StreamingScriptProvider,
    *,
    base_delay_ms: int,
    abort_on_retry: bool = False,
) -> list[AgentAssistantMessage]:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    abort = threading.Event()

    class _AbortOnRetry:
        def emit(self, event: object) -> None:
            if abort_on_retry and isinstance(event, RetryScheduled):
                abort.set()

    CodingSession(
        provider=provider,  # type: ignore[arg-type]
        settings_manager=_retry_settings(tmp_path, base_delay_ms),
        native_session=tree,
        agent_event_sink=_AbortOnRetry(),  # type: ignore[arg-type]
        abort_event=abort if abort_on_retry else None,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return [
        entry.message
        for entry in tree.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentAssistantMessage)
    ]


def test_retried_then_successful_turn_stores_only_the_success(tmp_path: Path) -> None:
    provider = _StreamingScriptProvider(["fail", "fail", "ok"])
    stored = _stored_assistants(tmp_path, provider, base_delay_ms=1)

    assert provider.calls == 3
    assert [(m.content.value, m.stop_reason) for m in stored] == [
        ("final answer", None)
    ]


def test_finally_failed_retry_stores_one_error_message(tmp_path: Path) -> None:
    provider = _StreamingScriptProvider(["fail", "fail", "fail"])
    stored = _stored_assistants(tmp_path, provider, base_delay_ms=1)

    assert provider.calls == 3
    assert len(stored) == 1
    # Only the last attempt's partial survives; earlier ones were retried.
    assert stored[0].content.value == "partial 3"
    assert stored[0].stop_reason is AgentStopReason.ERROR
    assert stored[0].error_message == (
        "Anthropic API request failed with HTTP status 529."
    )


def test_abort_during_the_retry_wait_stores_one_aborted_message(
    tmp_path: Path,
) -> None:
    provider = _StreamingScriptProvider(["fail", "ok"])
    stored = _stored_assistants(
        tmp_path, provider, base_delay_ms=5000, abort_on_retry=True
    )

    assert provider.calls == 1
    assert len(stored) == 1
    # The scheduled retry already discarded the failed attempt's partial.
    assert stored[0].content.value == ""
    assert stored[0].stop_reason is AgentStopReason.ABORTED
