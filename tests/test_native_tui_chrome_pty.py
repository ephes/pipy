"""Real-PTY integration test for extension chrome regions (slice B).

This boots the actual product TUI (`ToolLoopTerminalUi.read_line` over a real
pseudo-TTY via `NativeToolReplSession.run`, like the slice-17/cancellation PTY
tests) rather than poking `_frame_lines()` internals. It proves the contract
that matters once the regions are woven into the live paint path: with a custom
header, an above-editor widget, and a custom footer set, the painted frame
shows those chrome rows AND the typed input is still visible/usable.
"""

from __future__ import annotations

import os
import pty
import threading
import time
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_chrome_header_widget_and_footer_render_with_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = FakeNativeProvider(supports_tool_calls=True)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
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
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"

        # Install the three chrome regions on the live UI; each setter repaints.
        def header_factory(theme: object) -> object:
            return type("C", (), {"render": lambda self, w: ["CHROME_HEADER_LINE"]})()

        def footer_factory(theme: object, fd: object) -> object:
            return type("C", (), {"render": lambda self, w: ["CHROME_FOOTER_LINE"]})()

        ui.set_extension_header(header_factory)
        ui.set_extension_widget(
            "w", ["CHROME_ABOVE_WIDGET"], placement="above_editor"
        )
        ui.set_extension_footer(footer_factory)

        assert _wait_for(err_chunks, "CHROME_HEADER_LINE"), "header never painted"
        assert _wait_for(err_chunks, "CHROME_ABOVE_WIDGET"), "widget never painted"
        assert _wait_for(err_chunks, "CHROME_FOOTER_LINE"), "footer never painted"

        # Type input (without submitting) and confirm it stays visible alongside
        # the chrome rows: the chrome must not starve or hide the editor.
        os.write(in_master, b"hello chrome")
        assert _wait_for(err_chunks, "hello chrome"), "typed input never painted"

        snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=100,
            rows=40,
        )
        os.write(in_master, b"\x03")  # ctrl-c clears the prompt and exits
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

    screen = "\n".join(snapshot.viewport)
    # All three chrome regions and the typed input are present in the final frame.
    assert "CHROME_HEADER_LINE" in screen, "header row missing from frame"
    assert "CHROME_ABOVE_WIDGET" in screen, "above-editor widget missing from frame"
    assert "CHROME_FOOTER_LINE" in screen, "custom footer missing from frame"
    assert "hello chrome" in screen, "typed input not visible alongside chrome"
