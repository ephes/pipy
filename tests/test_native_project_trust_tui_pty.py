"""Real-PTY coverage for the pre-runtime project-trust selector."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest


_CHILD = r"""
import sys
from pathlib import Path
from pipy_harness.native.project_trust import get_project_trust_options
from pipy_harness.native.tui import run_startup_project_trust_selector

cwd = Path(sys.argv[1])
result_path = Path(sys.argv[2])
option = run_startup_project_trust_selector(
    cwd=cwd,
    options=get_project_trust_options(cwd, include_session_only=True),
)
result_path.write_text("CANCEL" if option is None else option.label, encoding="utf-8")
"""

_REPL_CHILD = r"""
import sys
from pipy_harness.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def _set_size(fd: int, columns: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def _wait_for(fd: int, needle: bytes, *, timeout: float = 8.0) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.1)
        if not readable:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        output.extend(chunk)
        if needle in output:
            return bytes(output)
    raise AssertionError(f"selector did not render {needle!r}: {bytes(output)!r}")


def _run_selector(
    tmp_path: Path, keys: bytes, *, resize_before_input: bool = False
) -> tuple[str, bytes]:
    cwd = tmp_path / "parent" / "project"
    cwd.mkdir(parents=True, exist_ok=True)
    result_path = tmp_path / "result.txt"
    master, slave = pty.openpty()
    _set_size(slave, 80, 24)
    env = os.environ.copy()
    env.update({"TERM": "xterm-256color", "NO_COLOR": "1"})
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(cwd), str(result_path)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = _wait_for(master, b"Trust project folder?")
    if resize_before_input:
        _set_size(master, 100, 40)
        time.sleep(0.25)
    os.write(master, keys)
    deadline = time.monotonic() + 8.0
    while proc.poll() is None and time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], 0.1)
        if not readable:
            continue
        try:
            output += os.read(master, 65536)
        except OSError:
            break
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=2.0)
        raise AssertionError(f"selector did not exit after input: {output!r}")
    while True:
        readable, _, _ = select.select([master], [], [], 0.05)
        if not readable:
            break
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.close(master)
    assert proc.returncode == 0
    return result_path.read_text(encoding="utf-8"), output


@pytest.mark.skipif(os.name != "posix", reason="real PTY requires POSIX")
@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (b"\r", "Trust"),
        (b"\x1b[B\r", "Trust parent folder"),
        (b"\x1b[B\x1b[B\r", "Trust (this session only)"),
        (b"\x1b[B\x1b[B\x1b[B\r", "Do not trust"),
    ],
)
def test_startup_trust_selector_choices_at_80x24(
    tmp_path: Path, keys: bytes, expected: str
) -> None:
    result, output = _run_selector(tmp_path, keys)
    assert result.startswith(expected)
    canonical = str((tmp_path / "parent" / "project").resolve()).encode()
    assert canonical[:48] in output  # the canonical path is width-clipped at 80 cols
    assert b"project settings/resources and packages" in output


@pytest.mark.skipif(os.name != "posix", reason="real PTY requires POSIX")
def test_startup_trust_selector_resize_then_cancel_recovers(
    tmp_path: Path,
) -> None:
    result, output = _run_selector(tmp_path, b"\x1b", resize_before_input=True)
    assert result == "CANCEL"
    assert b"\x1b[?2004l" in output  # bracketed paste/raw-mode cleanup


@pytest.mark.skipif(os.name != "posix", reason="real PTY requires POSIX")
def test_untrusted_product_warning_is_live_only_not_session_content(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "project"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(
        '{"defaultProjectTrust":"never","quietStartup":true}\n',
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    master, slave = pty.openpty()
    _set_size(slave, 80, 24)
    env = os.environ.copy()
    env.update(
        {
            "TERM": "xterm-256color",
            "NO_COLOR": "1",
            "PIPY_CONFIG_HOME": str(config),
            "PIPY_SKIP_VERSION_CHECK": "1",
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _REPL_CHILD,
            "repl",
            "--cwd",
            str(cwd),
            "--session-dir",
            str(sessions),
            "--session-id",
            "trust-warning-test",
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = _wait_for(master, b"This project is not trusted")
    output += _wait_for(master, b"\x1b[?2004h")
    os.write(master, b"/exit\r")
    deadline = time.monotonic() + 8.0
    while proc.poll() is None and time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], 0.1)
        if readable:
            try:
                output += os.read(master, 65536)
            except OSError:
                break
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=2.0)
        raise AssertionError("product TUI did not exit after /exit")
    os.close(master)
    assert proc.returncode == 0
    assert b"Use /trust to save a trust decision" in output
    session_files = list(sessions.glob("**/*.jsonl"))
    assert len(session_files) == 1
    session_text = session_files[0].read_text(encoding="utf-8")
    assert "This project is not trusted" not in session_text
    assert "Trust project folder" not in session_text
