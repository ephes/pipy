"""Pseudo-TTY integration test for the bounded tool-loop REPL.

These tests prove the live behavior the previous suite could not pin:
on a real PTY-backed stdout, the renderer emits ANSI styles for tool
calls and prints streaming text chunks incrementally. We run the loop
against ``FakeNativeProvider`` so the test is deterministic and does
not require credentials or a network.
"""

from __future__ import annotations

import io
import os
import pty
import select
import threading
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    NativeToolReplSession,
    ProviderToolCall,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


class _NoOpTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="noop",
            description="No-op tool used to drive the loop.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="noop result",
            provider_correlation_id=request.provider_correlation_id,
        )


def _spawn_pty_drainer(fd: int) -> tuple[threading.Thread, list[bytes]]:
    """Drain a PTY master FD in a background thread.

    PTY buffers are small (often 4 KB); without a concurrent drainer the
    writer blocks once the buffer fills. The thread stops when the master
    side reports EOF (after the slave is closed) or hits a 6-second
    inactivity timeout, which is generous compared with the few-millisecond
    duration of these in-process sessions.
    """

    collected: list[bytes] = []

    def _drain() -> None:
        while True:
            rlist, _, _ = select.select([fd], [], [], 6.0)
            if not rlist:
                return
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread, collected


@pytest.mark.skipif(
    os.name != "posix", reason="pty integration requires posix"
)
def test_pty_tool_loop_streams_and_renders_tool_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    out_master, out_slave = pty.openpty()
    err_master, err_slave = pty.openpty()

    output_stream = os.fdopen(out_slave, "w", buffering=1, encoding="utf-8")
    error_stream = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")

    out_thread, output_chunks = _spawn_pty_drainer(out_master)
    err_thread, error_chunks = _spawn_pty_drainer(err_master)

    text_chunks = ("Pi parity is ", "49 out of 50.")
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=(
            (
                ProviderToolCall(
                    provider_correlation_id="cc",
                    tool_name="noop",
                    arguments_json="{}",
                ),
            ),
            (),
        ),
        programmable_text_chunks=text_chunks,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={"noop": _NoOpTool()},
        tool_budget=5,
        input_runtime="plain",
    )
    input_stream = io.StringIO("where are we?\n")
    try:
        result = session.run(
            workspace_root=tmp_path,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=error_stream,
        )
    finally:
        output_stream.flush()
        error_stream.flush()
        output_stream.close()
        error_stream.close()

    assert result.status == HarnessStatus.SUCCEEDED

    out_thread.join(timeout=8.0)
    err_thread.join(timeout=8.0)
    os.close(out_master)
    os.close(err_master)

    output_text = b"".join(output_chunks).decode("utf-8", errors="replace")
    error_text = b"".join(error_chunks).decode("utf-8", errors="replace")

    # Streaming surface — Pi-shape (no `assistant > ` label, leading
    # newline separates the streamed answer from tool blocks).
    assert "Pi parity is " in output_text
    assert "49 out of 50." in output_text
    assert "assistant" not in output_text

    # The buffered final_text fallback never prints a second copy on
    # the same turn (so users do not see the answer twice). The fake
    # provider streams chunks on each of the two scripted turns, so
    # the streamed phrase appears exactly twice — never four times.
    streamed_count = output_text.count("49 out of 50.")
    assert streamed_count == 2, (
        f"buffered fallback duplicated streamed text "
        f"(streamed={streamed_count}, expected=2)"
    )

    # Tool block rendering surface
    assert "noop(" in error_text
    assert "noop result" in error_text
    assert "Took" in error_text

    # ANSI escapes present on the real PTY error stream (tool blocks +
    # spinner are the styled surfaces). Pi-shape streaming output stays
    # plain text so terminal copy/paste does not pick up escape codes.
    assert "\x1b[" in error_text
