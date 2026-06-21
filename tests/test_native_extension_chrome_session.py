"""Session-level tests for slice-B extension chrome wiring.

`test_chrome_calls_do_not_leak_to_archive` proves a captured-stream run (no
TTY, so no live driver) never persists chrome content into the on-disk
session archive. `test_pty_session_renders_then_reload_clears_chrome` boots a
real PTY-backed session and exercises the LIVE driver end to end: an extension
*command* sets a widget + title through `ctx.ui` (the only lifecycle path that
carries the live `ui_driver`), the region renders, then `/reload` clears it.
"""

from __future__ import annotations

import io
import os
import pty
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


# Sets a widget + title on session_start. In a captured-stream run there is no
# live ui_driver, so this never reaches the screen/archive (the no-leak case).
_EXT = '''
def activate(api):
    @api.on("session_start")
    def _s(event, ctx):
        ctx.ui.set_widget("demo", ["DEMO_WIDGET"])
        ctx.ui.set_title("demo-title")
'''

# A custom command is the lifecycle path that carries the live ui_driver, so
# its chrome reaches the real TUI. Used by the PTY render+reload-clear test.
_EXT_CMD = '''
def activate(api):
    def _demo(ctx, args):
        ctx.ui.set_widget("demo", ["DEMO_WIDGET"], placement="above_editor")
        ctx.ui.set_title("demo-title")

    api.register_command("demo", "set demo chrome", _demo)
'''


class _Provider:
    name = "stub"
    model_id = "m"

    @property
    def supports_tool_calls(self):
        return True

    def complete(self, request: ProviderRequest, **_k) -> ProviderResult:
        now = datetime(2026, 6, 21, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
            tool_calls=(),
        )


def test_chrome_calls_do_not_leak_to_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    ws = tmp_path / "work"
    (ws / ".pipy" / "extensions").mkdir(parents=True)
    (ws / ".pipy" / "extensions" / "chrome-demo.py").write_text(_EXT, encoding="utf-8")

    session = NativeToolReplSession(
        provider=_Provider(), tool_registry=production_tool_registry(), tool_budget=3
    )
    result = session.run(
        workspace_root=ws,
        input_stream=io.StringIO("hi\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED
    blob = ""
    sroot = tmp_path / "sessions"
    if sroot.exists():
        for p in sroot.rglob("*"):
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="replace")
    assert "DEMO_WIDGET" not in blob
    assert "demo-title" not in blob


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


def _wait_until_absent(
    collected: list[bytes],
    needle: str,
    *,
    columns: int,
    rows: int,
    timeout: float = 8.0,
) -> bool:
    """Wait until ``needle`` is absent from the *current* rendered frame.

    The raw byte stream is append-only, so an old paint keeps the needle in
    history forever; we must look at the latest screen state instead.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = parse_ansi_screen(
            b"".join(collected).decode("utf-8", errors="replace"),
            columns=columns,
            rows=rows,
        )
        if needle not in "\n".join(snap.viewport):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_session_renders_then_reload_clears_chrome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A PTY-backed session builds a real terminal_ui, so the live driver +
    # region rendering + reload-clear are exercised. An extension *command*
    # carries the live ui_driver (the session_start path does not), so the
    # command is what drives the chrome.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "chrome-demo.py"
    ext_file.write_text(_EXT_CMD, encoding="utf-8")

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
    session = NativeToolReplSession(provider=_Provider(), tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup never painted"

        # Run the extension command: it sets the widget via the live driver.
        os.write(in_master, b"/demo\n")
        assert _wait_for(err_chunks, "DEMO_WIDGET"), "widget never painted"

        # Remove the extension, then /reload: chrome must be cleared. The
        # session_start hook does not re-fire on reload and the file is gone,
        # so nothing re-sets the widget -> it must vanish from the frame.
        ext_file.unlink()
        os.write(in_master, b"/reload\n")
        assert _wait_until_absent(
            err_chunks, "DEMO_WIDGET", columns=100, rows=40
        ), "widget still on screen after /reload cleared chrome"

        os.write(in_master, b"\x03")  # ctrl-c exits the prompt
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x03")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)
