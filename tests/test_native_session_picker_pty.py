"""Real-PTY integration tests for the interactive session picker overlay.

These drive ``ToolLoopTerminalUi.run_session_picker`` over a real pseudo-TTY:
the inline overlay renders (no alternate screen), real arrow/Enter/Esc key
decoding works, a TIOCSWINSZ resize repaints coherently while the picker is
open, and the chosen native session file is returned. No provider turn runs.
"""

from __future__ import annotations

import fcntl
import os
import pty
import struct
import termios
import threading
import time
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.native.session_tree_commands import SessionListEntry
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


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _teardown(stdin, terminal, in_master: int, err_master: int) -> None:
    """Close all pty endpoints so the drainer thread sees EOF and exits."""

    for closer in (
        lambda: stdin.close(),
        lambda: terminal.close(),
        lambda: os.close(in_master),
        lambda: os.close(err_master),
    ):
        try:
            closer()
        except OSError:
            pass


def _rows(tmp_path: Path) -> list[SessionListEntry]:
    return [
        SessionListEntry(
            path=Path("/store/a.jsonl"),
            session_id="aaaaaaaa-1111",
            name="alpha",
            message_count=2,
            cwd=str(tmp_path),
            mtime=2.0,
        ),
        SessionListEntry(
            path=Path("/store/b.jsonl"),
            session_id="bbbbbbbb-2222",
            name="beta",
            message_count=3,
            cwd=str(tmp_path),
            mtime=1.0,
        ),
    ]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_picker_navigate_resize_and_select(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    _set_winsize(err_slave, 24, 80)
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    _err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    result: list[object] = []

    def _run() -> None:
        result.append(
            ui.run_session_picker(
                project_sessions=_rows(tmp_path),
                all_sessions=_rows(tmp_path),
                current_path=Path("/store/a.jsonl"),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "Resume session"), "picker title never rendered"
        assert _wait_for(err_chunks, "alpha"), "rows never rendered"
        # Resize while the overlay is open; the poll repaint must keep it coherent.
        _set_winsize(err_slave, 40, 100)
        time.sleep(0.25)
        # Down to the second row (beta), then Enter selects it.
        os.write(in_master, b"\x1b[B")
        time.sleep(0.1)
        os.write(in_master, b"\r")
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "picker worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == [Path("/store/b.jsonl")]
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured, "picker must not enter the alternate screen"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_picker_esc_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    _set_winsize(err_slave, 24, 80)
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    _err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    result: list[object] = []

    def _run() -> None:
        result.append(
            ui.run_session_picker(
                project_sessions=_rows(tmp_path),
                all_sessions=_rows(tmp_path),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "Resume session")
        os.write(in_master, b"\x1b")  # bare Esc cancels
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "picker worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == [None]
