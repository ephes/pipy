"""Real-PTY integration test for `ToolLoopTerminalUi.run_custom_component`.

Drives a trusted extension custom component over a real pseudo-TTY: its lines
render inline (no alternate screen), decoded keystrokes reach `handle_input`,
and the value passed to `done` is returned. This backs `ctx.ui.custom`, used by
the ported `answer` extension's Q&A overlay. No provider turn runs.
"""

from __future__ import annotations

import os
import pty
import struct
import termios
import threading
import time
from pathlib import Path
from typing import TextIO, cast

import fcntl
import pytest

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


class _ProbeComponent:
    """Renders a marker + a typed buffer; Enter submits, Esc cancels."""

    def __init__(self, done) -> None:
        self._done = done
        self.buffer = ""

    def render(self, width: int) -> list[str]:
        return [
            f"PROBE-OVERLAY w={width}",
            f"text:[{self.buffer}]",
            "enter=submit esc=cancel",
        ]

    def handle_input(self, key: str) -> None:
        if key == "enter":
            self._done(self.buffer)
        elif key == "esc":
            self._done(None)
        elif key == "backspace":
            self.buffer = self.buffer[:-1]
        elif len(key) == 1 and key.isprintable():
            self.buffer += key


def _make_ui(tmp_path: Path):
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
    return ui, stdin, terminal, in_master, err_master, err_chunks


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_component_types_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_chunks = _make_ui(tmp_path)
    result: list[object] = []

    def _run() -> None:
        result.append(ui.run_custom_component(lambda done: _ProbeComponent(done)))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "PROBE-OVERLAY"), "overlay never rendered"
        os.write(in_master, b"hi")
        time.sleep(0.1)
        assert _wait_for(err_chunks, "text:[hi]"), "typed text never rendered"
        os.write(in_master, b"\r")  # Enter -> submit
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "custom-component worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == ["hi"]
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured, "custom overlay must not use alt screen"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_component_esc_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_chunks = _make_ui(tmp_path)
    result: list[object] = []

    def _run() -> None:
        result.append(ui.run_custom_component(lambda done: _ProbeComponent(done)))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "PROBE-OVERLAY"), "overlay never rendered"
        os.write(in_master, b"\x1b")  # Esc -> cancel
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "custom-component worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == [None]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_editor_accepts_newline_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_chunks = _make_ui(tmp_path)
    result: list[object] = []

    def _run() -> None:
        result.append(ui.run_extension_editor("Draft", "seed"))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "Draft"), "editor overlay never rendered"
        os.write(in_master, b"\x1b\r")  # Alt+Enter -> newline fallback.
        time.sleep(0.1)
        os.write(in_master, b"next")
        assert _wait_for(err_chunks, "next"), "typed second line never rendered"
        os.write(in_master, b"\r")  # Enter -> submit
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "editor worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == ["seed\nnext"]
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured, "editor overlay must not use alt screen"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_shortcut_returns_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A registered extension shortcut key (ctrl-g) decoded by read_line returns
    # the HOTKEY_EXTENSION_SHORTCUT sentinel the session dispatches.
    from pipy_harness.native.tui import HOTKEY_EXTENSION_SHORTCUT_PREFIX

    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_chunks = _make_ui(tmp_path)
    ui.extension_shortcut_keys = frozenset({"ctrl-g"})
    result: list[str] = []

    def _run() -> None:
        result.append(ui.read_line("> "))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        time.sleep(0.2)
        os.write(in_master, b"\x07")  # Ctrl+G
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "read_line did not return on shortcut"
    finally:
        _teardown(stdin, terminal, in_master, err_master)
    assert result == [f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}ctrl-g\n"]
