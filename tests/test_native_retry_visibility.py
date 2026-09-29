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
from pathlib import Path

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AssistantTextDelta,
    MessageStarted,
    RetryCompleted,
    RetryScheduled,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage
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
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.frame_renderer import FrameLine, style_line
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.rendering import RenderingAgentEventAdapter
from pipy_harness.native.ui.screen import ScreenRenderInputs
from pipy_harness.native.ui.state import FinishRetry, ScheduleRetry, UiState, reduce


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
        cache_hit_percent=None,
        uncached_input_tokens=usage.uncached_input_tokens,
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
