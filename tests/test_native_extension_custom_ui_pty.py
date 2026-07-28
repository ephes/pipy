"""Real-PTY integration test for `ToolLoopTerminalUi.run_custom_component`.

Drives a trusted extension custom component over a real pseudo-TTY: its lines
render inline (no alternate screen), decoded keystrokes reach `handle_input`,
and the value passed to `done` is returned. This backs `ctx.ui.custom`, used by
the ported `answer` extension's Q&A overlay. No provider turn runs.
"""

from __future__ import annotations

import os
import pty
import select
import struct
import sys
import termios
import threading
import time
import errno
from pathlib import Path
from typing import TextIO, cast

import fcntl
import pytest

from pipy_harness.native.tui import ToolLoopTerminalUi

_DETACHED_PTY_STREAMS: list[object] = []
_ABANDONED_PTY_FDS: list[int] = []
_DRAINER_STOPS: dict[threading.Thread, threading.Event] = {}


def _spawn_live_drainer(fd: int) -> tuple[threading.Thread, list[bytes]]:
    collected: list[bytes] = []
    stop = threading.Event()

    def _drain() -> None:
        while not stop.is_set():
            readable, _, _ = select.select([fd], [], [], 0.05)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    time.sleep(0.01)
                    continue
                return
            if not chunk:
                time.sleep(0.01)
                continue
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    _DRAINER_STOPS[thread] = stop
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


def _detach_text_stream(stream: TextIO) -> None:
    try:
        stream.flush()
    except (OSError, ValueError):
        pass
    detach = getattr(stream, "detach", None)
    if callable(detach):
        try:
            _DETACHED_PTY_STREAMS.append(detach())
        except (OSError, ValueError):
            pass


def _stream_fd(stream: TextIO) -> int | None:
    try:
        return stream.fileno()
    except (OSError, ValueError):
        return None


def _teardown(
    stdin: TextIO,
    terminal: TextIO,
    in_master: int,
    err_master: int,
    err_thread: threading.Thread,
) -> None:
    # Closing TextIOWrapper objects around pty slaves can block in close(2) on
    # macOS. These test streams are opened with closefd=False, so detach the
    # wrappers, stop the nonblocking drainer, and let process teardown reclaim
    # the bounded set of pty descriptors.
    stop = _DRAINER_STOPS.pop(err_thread, None)
    if stop is not None:
        stop.set()
    stdin_fd = _stream_fd(stdin)
    terminal_fd = _stream_fd(terminal)
    _detach_text_stream(stdin)
    _detach_text_stream(terminal)
    if stdin_fd is not None:
        _ABANDONED_PTY_FDS.append(stdin_fd)
    if terminal_fd is not None:
        _ABANDONED_PTY_FDS.append(terminal_fd)
    _ABANDONED_PTY_FDS.extend([in_master, err_master])
    err_thread.join(timeout=2.0)


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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    return ui, stdin, terminal, in_master, err_master, err_thread, err_chunks


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_component_types_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
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
        _teardown(stdin, terminal, in_master, err_master, err_thread)
    assert result == ["hi"]
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured, "custom overlay must not use alt screen"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_component_esc_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
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
        _teardown(stdin, terminal, in_master, err_master, err_thread)
    assert result == [None]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_editor_accepts_newline_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
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
        _teardown(stdin, terminal, in_master, err_master, err_thread)
    assert result == ["seed\nnext"]
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured, "editor overlay must not use alt screen"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_editor_external_editor_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    state_path = tmp_path / "termios-state.txt"
    editor_script = tmp_path / "editor.py"
    editor_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "import termios\n"
        f"Path({str(state_path)!r}).write_text(str(termios.tcgetattr(0)[3]), encoding='utf-8')\n"
        "Path(sys.argv[1]).write_text('edited from external\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor_script}")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
    result: list[object] = []
    # Hold an outer raw owner so the extension editor acquires nested depth 2.
    # The foreign child must still see cooked mode, and the editor must resume
    # physically raw without consuming this owner.
    ui._driver.enter_raw_mode()

    def _run() -> None:
        result.append(ui.run_extension_editor("Draft", "seed"))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "ctrl-g external edit"), "external hint missing"
        os.write(in_master, b"\x07")  # Ctrl+G -> external editor.
        assert _wait_for(err_chunks, "edited from external"), "edited text never rendered"
        os.write(in_master, b"\r")  # Enter -> submit edited text.
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "editor worker did not exit"
        assert ui._driver._raw_mode_depth == 1
        assert ui._driver._terminal_mode_suspend_depth == 0
        assert not (termios.tcgetattr(stdin.fileno())[3] & termios.ICANON)
        ui._driver.restore_terminal_mode()
        assert termios.tcgetattr(stdin.fileno())[3] & termios.ICANON
    finally:
        ui._driver.force_restore_terminal_mode()
        _teardown(stdin, terminal, in_master, err_master, err_thread)

    assert result == ["edited from external"]
    assert state_path.exists(), "external editor did not run"
    assert int(state_path.read_text(encoding="utf-8")) & termios.ICANON
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert captured.count("\x1b[?2004h") == 2
    assert captured.count("\x1b[?2004l") == 2


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_external_editor_suspends_nested_raw_owners_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    state_path = tmp_path / "nested-termios-state.txt"
    editor_script = tmp_path / "nested-editor.py"
    editor_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "import termios\n"
        f"Path({str(state_path)!r}).write_text(str(termios.tcgetattr(0)[3]), encoding='utf-8')\n"
        "Path(sys.argv[1]).write_text('nested edit\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
    try:
        ui._driver.enter_raw_mode()
        ui._driver.enter_raw_mode()

        assert (
            ui._run_extension_external_editor(
                f"{sys.executable} {editor_script}", "seed"
            )
            == "nested edit"
        )
        assert int(state_path.read_text(encoding="utf-8")) & termios.ICANON
        assert ui._driver._raw_mode_depth == 2
        assert ui._driver._terminal_mode_suspend_depth == 0
        assert not (termios.tcgetattr(stdin.fileno())[3] & termios.ICANON)

        ui._driver.restore_terminal_mode()
        assert ui._driver._raw_mode_depth == 1
        assert not (termios.tcgetattr(stdin.fileno())[3] & termios.ICANON)
        ui._driver.restore_terminal_mode()
        assert ui._driver._raw_mode_depth == 0
        assert termios.tcgetattr(stdin.fileno())[3] & termios.ICANON
    finally:
        ui._driver.force_restore_terminal_mode()
        _teardown(stdin, terminal, in_master, err_master, err_thread)

    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert captured.count("\x1b[?2004h") == 2
    assert captured.count("\x1b[?2004l") == 2


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_editor_external_editor_failure_keeps_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    editor_script = tmp_path / "editor.py"
    editor_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('should not load\\n', encoding='utf-8')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor_script}")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
    result: list[object] = []

    def _run() -> None:
        result.append(ui.run_extension_editor("Draft", "seed"))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "ctrl-g external edit"), "external hint missing"
        os.write(in_master, b"\x07")  # Ctrl+G -> failing external editor.
        assert _wait_for(err_chunks, "Launching external editor"), "editor never launched"
        time.sleep(0.2)
        os.write(in_master, b"\r")  # Enter -> submit original text.
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "editor worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master, err_thread)

    assert result == ["seed"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_editor_external_editor_invalid_utf8_keeps_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    editor_script = tmp_path / "editor.py"
    editor_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_bytes(b'\\xff')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor_script}")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
    result: list[object] = []

    def _run() -> None:
        result.append(ui.run_extension_editor("Draft", "seed"))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "ctrl-g external edit"), "external hint missing"
        os.write(in_master, b"\x07")  # Ctrl+G -> invalid UTF-8 save.
        assert _wait_for(err_chunks, "Launching external editor"), "editor never launched"
        time.sleep(0.2)
        os.write(in_master, b"\r")  # Enter -> submit original text.
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "editor worker did not exit"
    finally:
        _teardown(stdin, terminal, in_master, err_master, err_thread)

    assert result == ["seed"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_extension_shortcut_returns_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A registered extension shortcut key decoded by read_line returns
    # the HOTKEY_EXTENSION_SHORTCUT sentinel the session dispatches.
    from pipy_harness.native.tui import HOTKEY_EXTENSION_SHORTCUT_PREFIX

    monkeypatch.setenv("TERM", "xterm-256color")
    ui, stdin, terminal, in_master, err_master, err_thread, err_chunks = _make_ui(tmp_path)
    ui.extension_shortcut_keys = frozenset({"ctrl-x"})
    result: list[str] = []

    def _run() -> None:
        result.append(ui.read_line("> "))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        time.sleep(0.2)
        os.write(in_master, b"\x18")  # Ctrl+X
        worker.join(timeout=8.0)
        assert not worker.is_alive(), "read_line did not return on shortcut"
    finally:
        _teardown(stdin, terminal, in_master, err_master, err_thread)
    assert result == [f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}ctrl-x\n"]
