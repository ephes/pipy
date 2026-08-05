"""Regression tests for the Tool-Loop Parity Track review.

Provider failures are surfaced on stderr but no longer tear the whole
REPL down — a transient HTTP 503/429 from one provider turn should not
end the user's session. The diagnostic stays visible so the user knows
the turn aborted, and the REPL stays available for the next prompt.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.automation.run_modes import run_print_mode
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.provider import StreamChunkSink


def test_provider_failure_keeps_repl_alive_with_visible_diagnostic(
    tmp_path: Path,
):
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        status=HarnessStatus.FAILED,
        metadata={"response_status": "rate_limited"},
    )
    session = CodingSession(provider=provider)
    input_stream = io.StringIO("hello\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    # The REPL hits EOF on the next read after the soft-fail diagnostic
    # and exits cleanly. Status is succeeded because the session itself
    # closed normally; the per-turn failure is recorded on stderr.
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.error_type is None
    assert result.provider_failure_type == "ProviderFailed"
    assert "provider failure during turn" in error_stream.getvalue()
    assert "response_status=rate_limited" in error_stream.getvalue()


class _FailOnceProvider:
    name = "openai-codex"
    model_id = "gpt-test"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink, cancel_token
        self.calls += 1
        now = datetime.now(UTC)
        if self.calls == 1:
            return ProviderResult(
                status=HarnessStatus.FAILED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                error_type="OpenAICodexStreamInterruptedError",
                error_message=(
                    "OpenAI Codex stream was interrupted before completion."
                ),
                metadata={
                    "phase": "stream",
                    "retryable": True,
                    "transport": "sse",
                },
            )
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"recovered:{request.user_prompt}",
            usage={},
        )


def test_exhausted_transport_failure_leaves_repl_usable_for_next_prompt(
    tmp_path: Path,
) -> None:
    provider = _FailOnceProvider()
    session = CodingSession(provider=provider)
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\nsecond\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert provider.calls == 2
    assert result.provider_failure_type is None
    assert result.provider_failure_message is None
    assert "provider failure during turn" in error_stream.getvalue()
    assert "OpenAI Codex stream was interrupted before completion." in (
        error_stream.getvalue()
    )
    assert "recovered:second" in output_stream.getvalue()


def test_print_mode_returns_stable_transport_failure_diagnostic(
    tmp_path: Path,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run_print_mode(
        adapter=CodingSessionAdapter(provider=_FailOnceProvider()),
        prompt="first",
        cwd=tmp_path,
        stdout=stdout,
        error_stream=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "OpenAI Codex stream was interrupted before completion." in (
        stderr.getvalue()
    )
    assert "read operation timed out" not in stderr.getvalue().lower()
