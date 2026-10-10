"""DF1-F6b live stopped turns match their durable history presentation."""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_native_agent_loop import _make_loop, _provider_result, _run_input
from test_native_session_history_render import _Terminal
from test_native_ui_coding_session_renderer import _Harness

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentStopReason,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import TextContent
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import (
    ProviderPartial,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.ui.rendering import RenderingAgentEventAdapter


@pytest.mark.parametrize("reason", tuple(AgentCancellationReason))
def test_cancelled_provider_turn_live_rows_match_reopened_history(
    tmp_path: Path, reason: AgentCancellationReason
) -> None:
    loop, _, events, _ = _make_loop(
        [], [ProviderTurnOutcome(cancellation_reason=reason)], emit_delta=True
    )
    outcome = loop.run(_run_input())
    h = _Harness()
    adapter = RenderingAgentEventAdapter(h.renderer)
    for event in events.events:
        adapter.emit(event)
    live = h.transcript.history_blocks
    assert live == [("assistant", ("delta",)), ("error", ("Operation aborted",))]
    assert h.transcript.assistant_text == ""
    assert h.transcript.working_text == ""

    tree = NativeSessionTree.create(tmp_path / "saved")
    tree.append_message(AgentUserMessage(ProductContent("question")))
    tree.append_message(outcome.final_history[-1])
    assert tree.path is not None
    restored = _Terminal(tmp_path / "restored")
    restored.tree = NativeSessionTree.open(tree.path, strict=True)
    restored.history.render_active_branch()
    assert restored.rows()[1:] == live


class _StoppedProvider:
    supports_tool_calls = True
    name = "stopped-display-fixture"
    model_id = "fixture-model"

    def __init__(
        self, failure: bool, *, streamed: bool, partial_call: bool = False
    ) -> None:
        self.failure = failure
        self.streamed = streamed
        self.partial_call = partial_call
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        self.requests.append(request)
        first = len(self.requests) == 1
        if first and self.streamed:
            sink = kwargs["stream_sink"]
            assert callable(sink)
            sink("partial answer")
        blocks = (
            TextContent("partial answer"),
            ProviderToolCall("partial-call", "read", '{"pa'),
        )
        if first and not self.failure:
            error = ProviderCancelledError("fixture cancellation")
            if self.partial_call:
                error.partial = ProviderPartial(content_blocks=blocks)
            raise error
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.FAILED if first else HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="partial answer" if first else "next answer",
            content_blocks=blocks if first and self.partial_call else (),
            error_type="FixtureError" if first else None,
            error_message="provider exploded" if first else None,
        )


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("partial_call", [False, True])
def test_coding_session_draws_one_marker_and_continues_with_intact_history(
    tmp_path: Path, failure: bool, streamed: bool, partial_call: bool
) -> None:
    provider = _StoppedProvider(failure, streamed=streamed, partial_call=partial_call)
    tree = NativeSessionTree.create(tmp_path / "saved")
    output, errors = io.StringIO(), io.StringIO()
    CodingSession(provider=provider, native_session=tree).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\nsecond\n/exit\n"),
        output_stream=output,
        error_stream=errors,
    )
    marker = (
        ("provider exploded" if partial_call else "Error: provider exploded")
        if failure
        else "Operation aborted"
    )
    assert errors.getvalue().count(marker) == 1
    assert "pipy: provider failure during turn:" not in errors.getvalue()
    assert "next answer" in output.getvalue()
    if streamed or failure or partial_call:
        assert output.getvalue().count("partial answer") == 1
    assert len(provider.requests) == 2
    assert [m.content.value for m in provider.requests[1].messages] == [
        "first",
        "second",
    ]
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path, strict=True)
    stopped = [
        e.message
        for e in reopened.get_entries()
        if isinstance(e, MessageEntry)
        and isinstance(e.message, AgentAssistantMessage)
        and e.message.stop_reason is not None
    ]
    assert len(stopped) == 1
    assert bool(stopped[0].tool_calls) is partial_call
    assert stopped[0].stop_reason is (
        AgentStopReason.ERROR if failure else AgentStopReason.ABORTED
    )
    restored = _Terminal(tmp_path / "restored")
    restored.tree = reopened
    restored.history.render_active_branch()
    if not partial_call:
        assert restored.rows().count(("error", (marker,))) == 1
    # Exact live/reopen partial-call equality is covered by the display regression.


def test_terminal_failure_after_stream_draws_marker_once(tmp_path: Path) -> None:
    loop, _, events, _ = _make_loop(
        [],
        [ProviderTurnOutcome(result=_provider_result(status=HarnessStatus.FAILED))],
        emit_delta=True,
    )
    loop.run(_run_input())
    h = _Harness()
    adapter = RenderingAgentEventAdapter(h.renderer)
    for event in events.events:
        adapter.emit(event)
    assert h.transcript.history_blocks == [
        ("assistant", ("delta",)),
        ("error", ("Error: provider failed",)),
    ]


@pytest.mark.parametrize("outcomes", [["fail", "fail", "ok"], ["fail"] * 3])
def test_retry_attempt_errors_and_terminal_marker_are_not_duplicated(
    tmp_path: Path, outcomes: list[str]
) -> None:
    from test_native_retry_visibility import _retry_settings, _StreamingScriptProvider

    provider = _StreamingScriptProvider(outcomes)
    errors = io.StringIO()
    CodingSession(provider=provider, settings_manager=_retry_settings(tmp_path, 1)).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    error_rows = [
        line for line in errors.getvalue().splitlines() if line.startswith("Error:")
    ]
    assert len(error_rows) == (2 if outcomes[-1] == "ok" else 3)
    assert "pipy: provider failure" not in errors.getvalue()
    assert "Operation aborted" not in errors.getvalue()


def test_retry_wait_abort_keeps_existing_retry_notice_and_one_abort_marker(
    tmp_path: Path,
) -> None:
    import threading

    from test_native_retry_visibility import _retry_settings, _StreamingScriptProvider

    from pipy_harness.native.agent import AgentEvent, RetryScheduled

    abort = threading.Event()

    class AbortOnRetry:
        def emit(self, event: AgentEvent) -> None:
            if isinstance(event, RetryScheduled):
                abort.set()

    provider = _StreamingScriptProvider(["fail", "ok"])
    errors = io.StringIO()
    CodingSession(
        provider=provider,
        settings_manager=_retry_settings(tmp_path, 5000),
        abort_event=abort,
        agent_event_sink=AbortOnRetry(),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    assert provider.calls == 1
    assert errors.getvalue().count("Operation aborted") == 1
    assert errors.getvalue().count("Retry failed after") == 1


@pytest.mark.skipif(os.name != "posix", reason="pty requires posix")
def test_pty_failure_preserves_partial_answer_and_next_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pty_sync import output_bytes, wait_for_input_ready_after
    from test_native_coding_session_terminal_pty import _run_editor_pty

    provider = _StoppedProvider(True, streamed=True)

    def drive(master: int, chunks: list[bytes]) -> None:
        start = len(output_bytes(chunks))
        os.write(master, b"first\n")
        assert (
            wait_for_input_ready_after(chunks, "Error: provider exploded", after=start)
            is not None
        )
        start = len(output_bytes(chunks))
        os.write(master, b"second\n")
        assert (
            wait_for_input_ready_after(chunks, "next answer", after=start) is not None
        )

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "partial answer" in captured
    assert "pipy: provider failure during turn" not in captured
    assert len(provider.requests) == 2
