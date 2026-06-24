"""Regression tests for the Tool-Loop Parity Track review.

Provider failures are surfaced on stderr but no longer tear the whole
REPL down — a transient HTTP 503/429 from one provider turn should not
end the user's session. The diagnostic stays visible so the user knows
the turn aborted, and the REPL stays available for the next prompt.
"""

from __future__ import annotations

import io
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession


def test_provider_failure_keeps_repl_alive_with_visible_diagnostic(
    tmp_path: Path,
):
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        status=HarnessStatus.FAILED,
        metadata={"response_status": "rate_limited"},
    )
    session = NativeToolReplSession(provider=provider)
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
    assert "provider failure during turn" in error_stream.getvalue()
    assert "response_status=rate_limited" in error_stream.getvalue()
