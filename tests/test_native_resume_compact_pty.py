"""Real-PTY integration tests for resumed-state visibility and /compact.

These drive the actual inline product-TUI paint path over a real pseudo-TTY at
both Ghostty (100x40) and zellij (80x24) sizes. They prove the resumed-state
banner is committed safely at startup, that `/compact` runs locally and shows a
notice, and that in both cases the renderer never enters the alternate screen
and repaints a coherent inline frame (input separators pinned near the bottom).
"""

from __future__ import annotations

import os
import pty
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import NativeToolReplSession
from pipy_harness.native.models import ProviderResult
from pipy_harness.native.session_resume import ResumeContext
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tui import ToolLoopTerminalUi


def _spawn_live_drainer(fd: int) -> tuple[threading.Thread, list[bytes]]:
    collected: list[bytes] = []

    def _drain() -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread, collected


def _wait_for(collected: list[bytes], needle: str, *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    encoded = needle.encode("utf-8")
    while time.monotonic() < deadline:
        if encoded in b"".join(collected):
            return True
        time.sleep(0.02)
    return False


class _SeqProvider:
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.n = 0

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request, *, stream_sink=None, reasoning_sink=None):  # noqa: ANN001
        self.n += 1
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name="fake",
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"ANSWER {self.n} DONE_{self.n}",
        )


def _resume_context() -> ResumeContext:
    return ResumeContext(
        prior_session_id="2026-04-30T133000Z-studio-pipy-native-parent",
        prior_provider_name="fake",
        prior_model_id="fake-native-bootstrap",
        prior_turn_count=3,
        prior_workspace_hash="HASH",
        prior_started_at="2026-04-30T13:30:00+00:00",
        prior_ended_at="2026-04-30T14:00:00+00:00",
        prior_summary="PRIOR_SUMMARY_SECRET_BODY",
    )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_resume_and_compact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    provider = _SeqProvider()
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        resume_context=_resume_context(),
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(
            session.run(
                workspace_root=tmp_path,
                input_stream=cast(TextIO, stdin),
                output_stream=cast(TextIO, terminal),
                error_stream=cast(TextIO, terminal),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Resumed-state banner is committed at startup (safe labels only).
        assert _wait_for(err_chunks, "Resumed (resume) from session"), (
            f"{label}: resumed-state banner never shown"
        )
        # Three turns build three user-turn groups; wait for each answer so the
        # active-turn Escape watcher does not eat the next input.
        for index in (1, 2, 3):
            os.write(in_master, b"prompt\n")
            assert _wait_for(err_chunks, f"DONE_{index}"), (
                f"{label}: turn {index} answer never rendered"
            )
        os.write(in_master, b"/compact\n")
        assert _wait_for(err_chunks, "compacted conversation context"), (
            f"{label}: /compact notice never shown"
        )
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), f"{label} session did not exit"
    assert result_holder, f"{label} session produced no result"
    result = result_holder[0]
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    assert getattr(result, "compaction_count") == 1

    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Never the alternate screen, so native scrollback works.
    assert "\x1b[?1049h" not in captured
    # The resumed banner must carry only safe labels — never prior summary text.
    assert "PRIOR_SUMMARY_SECRET_BODY" not in captured

    # Coherent inline frame after compaction: the input separators are present
    # and pinned in the lower portion of the window.
    snapshot = parse_ansi_screen(captured, columns=columns, rows=rows)
    separator_rows = [
        index
        for index, line in enumerate(snapshot.viewport)
        if line.strip() and set(line.strip()) == {"─"}
    ]
    assert separator_rows, f"{label}: input frame separators missing after compaction"
    assert max(separator_rows) >= rows - 6
