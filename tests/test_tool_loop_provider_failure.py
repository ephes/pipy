"""Regression tests for the Tool-Loop Parity Track review (round 1).

Critical finding: provider failures were treated as successful tool-loop
turns. The session now surfaces a failed `NativeToolReplResult` with a
deterministic stderr diagnostic when the provider returns
`HarnessStatus.FAILED`.
"""

from __future__ import annotations

import io
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession


def test_provider_failure_surfaces_as_failed_tool_repl_result(tmp_path: Path):
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        status=HarnessStatus.FAILED,
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

    assert result.status == HarnessStatus.FAILED
    assert result.exit_code == 1
    assert result.error_type is not None
    assert "tool-loop ended after provider failure" in error_stream.getvalue()
