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
from pty_sync import wait_for_fd_input_ready_after, wait_for_fd_output

_CHILD = r"""
import sys
from pathlib import Path
from pipy_harness.native.project_trust import get_project_trust_options
from pipy_harness.native.startup_selectors import run_startup_project_trust_selector

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


def _wait_for(
    fd: int,
    needle: bytes,
    *,
    initial: bytes = b"",
    after: int = 0,
    timeout: float = 8.0,
) -> tuple[bytes, int]:
    observed = wait_for_fd_output(
        fd,
        needle,
        initial=initial,
        after=after,
        timeout=timeout,
    )
    if observed.match_end is None:
        raise AssertionError(f"selector did not render {needle!r}: {observed.output!r}")
    return observed.output, observed.match_end


def _wait_for_input_ready_after(
    fd: int, needle: bytes, *, timeout: float = 8.0
) -> bytes:
    observed = wait_for_fd_input_ready_after(fd, needle, timeout=timeout)
    if observed.ready_end is None:
        raise AssertionError(
            "selector did not become input-ready after "
            f"{needle!r} (observed_end={observed.observed_end!r}): {observed.output!r}"
        )
    return observed.output


def _read_pty_chunk(fd: int) -> bytes:
    try:
        return os.read(fd, 65536)
    except OSError:
        return b""


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
    output = _wait_for_input_ready_after(master, b"Trust project folder?")
    if resize_before_input:
        resize_start = len(output)
        _set_size(master, 100, 40)
        output, _ = _wait_for(
            master,
            b"\x1b[2J",
            initial=output,
            after=resize_start,
        )
    os.write(master, keys)
    deadline = time.monotonic() + 8.0
    while proc.poll() is None and time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], 0.1)
        if not readable:
            continue
        chunk = _read_pty_chunk(master)
        if not chunk:
            break
        output += chunk
    # PTY EOF/EIO can precede waitpid observing exit. Keep the original deadline
    # rather than killing a child immediately after successful terminal cleanup.
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
        os.close(master)
        raise AssertionError(f"selector did not exit after input: {output!r}")
    while True:
        readable, _, _ = select.select([master], [], [], 0.05)
        if not readable:
            break
        chunk = _read_pty_chunk(master)
        if not chunk:
            break
        output += chunk
    os.close(master)
    assert proc.returncode == 0
    return result_path.read_text(encoding="utf-8"), output


@pytest.mark.parametrize("pty_end", ["eio", "eof", "timeout"])
def test_selector_waits_for_process_exit_after_pty_closes(
    tmp_path, monkeypatch, pty_end
):
    class ExitingProcess:
        returncode = None
        waited = False
        killed = False

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            assert timeout > 0
            if pty_end == "timeout" and not self.killed:
                raise subprocess.TimeoutExpired("selector", timeout)
            self.waited = True
            self.returncode = -9 if self.killed else 0
            return self.returncode

        def kill(self):
            if pty_end != "timeout":
                pytest.fail("PTY closure must not kill a successfully exiting child")
            self.killed = True

    proc = ExitingProcess()

    def spawn(args, **kwargs):
        Path(args[-1]).write_text("CANCEL", encoding="utf-8")
        return proc

    def read(fd, size):
        if pty_end == "eio":
            raise OSError("PTY slave closed")
        return b""

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(pty, "openpty", os.pipe)
    monkeypatch.setattr(sys.modules[__name__], "_set_size", lambda *_: None)
    monkeypatch.setattr(
        sys.modules[__name__], "_wait_for_input_ready_after", lambda *_: b""
    )
    monkeypatch.setattr(select, "select", lambda *args: ([args[0][0]], [], []))
    monkeypatch.setattr(os, "write", lambda fd, data: len(data))
    monkeypatch.setattr(os, "read", read)
    if pty_end == "timeout":
        with pytest.raises(AssertionError, match="selector did not exit after input"):
            _run_selector(tmp_path, b"\x1b")
        assert proc.killed
    else:
        result, _ = _run_selector(tmp_path, b"\x1b")
        assert result == "CANCEL"
    assert proc.waited


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


def _run_untrusted_product(cwd: Path, config: Path, sessions: Path) -> bytes:
    master, slave = pty.openpty()
    try:
        try:
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
        finally:
            os.close(slave)
        output = _wait_for_input_ready_after(master, b"This project is not trusted")
        os.write(master, b"/exit\r")
        deadline = time.monotonic() + 8.0
        while proc.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if not readable:
                continue
            chunk = _read_pty_chunk(master)
            if not chunk:
                break
            output += chunk
        # PTY EOF/EIO can precede waitpid. Use the remaining original deadline,
        # like the selector path, and kill only when the process wait times out.
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            raise AssertionError("product TUI did not exit after /exit")
        assert proc.returncode == 0
        return output
    finally:
        os.close(master)


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
    output = _run_untrusted_product(cwd, config, sessions)
    assert b"Use /trust to save a trust decision" in output
    session_files = list(sessions.glob("**/*.jsonl"))
    assert len(session_files) == 1
    session_text = session_files[0].read_text(encoding="utf-8")
    assert "This project is not trusted" not in session_text
    assert "Trust project folder" not in session_text


@pytest.fixture
def product_pipe(monkeypatch):
    master, slave = os.pipe()
    closed = []
    original_close = os.close

    def close(fd):
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(pty, "openpty", lambda: (master, slave))
    monkeypatch.setattr(os, "close", close)
    try:
        yield master, slave, closed
    finally:
        for fd in (master, slave):
            if fd not in closed:
                original_close(fd)


@pytest.mark.parametrize("pty_end", ["eio", "eof", "timeout"])
def test_product_waits_for_process_exit_after_pty_closes(
    tmp_path, monkeypatch, product_pipe, pty_end
):
    class ExitingProcess:
        returncode = None
        killed = False

        def __init__(self):
            self.wait_timeouts = []

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            self.wait_timeouts.append(timeout)
            if pty_end == "timeout" and not self.killed:
                raise subprocess.TimeoutExpired("product", timeout)
            self.returncode = -9 if self.killed else 0
            return self.returncode

        def kill(self):
            self.killed = True

    proc = ExitingProcess()
    master, slave, closed = product_pipe
    clock = iter([100.0, 100.5, 101.0, 108.5])

    def read(fd, size):
        if pty_end == "eio":
            raise OSError("PTY slave closed")
        return b""

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(sys.modules[__name__], "_set_size", lambda *_: None)
    monkeypatch.setattr(
        sys.modules[__name__], "_wait_for_input_ready_after", lambda *_: b"warning"
    )
    monkeypatch.setattr(select, "select", lambda *args: ([args[0][0]], [], []))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock, 108.5))
    monkeypatch.setattr(os, "write", lambda fd, data: len(data))
    monkeypatch.setattr(os, "read", read)
    if pty_end == "timeout":
        with pytest.raises(
            AssertionError, match="product TUI did not exit after /exit"
        ):
            _run_untrusted_product(tmp_path, tmp_path, tmp_path)
        assert proc.killed
        assert proc.wait_timeouts == [7.0, 2.0]
    else:
        assert _run_untrusted_product(tmp_path, tmp_path, tmp_path) == b"warning"
        assert not proc.killed
        assert proc.wait_timeouts == [7.0]
    assert closed == [slave, master]


@pytest.mark.parametrize("failure", ["spawn", "input-ready", "wait"])
def test_product_closes_pty_on_unexpected_failure(
    tmp_path, monkeypatch, product_pipe, failure
):
    master, slave, closed = product_pipe

    class ExitingProcess:
        returncode = None

        def poll(self):
            return None

        def wait(self, *, timeout):
            raise RuntimeError("wait failed")

        def kill(self):
            pytest.fail("unexpected failure must not be treated as process timeout")

    def spawn(*args, **kwargs):
        if failure == "spawn":
            raise RuntimeError("spawn failed")
        return ExitingProcess()

    def input_ready(*args):
        if failure == "input-ready":
            raise RuntimeError("input-ready failed")
        return b"warning"

    monkeypatch.setattr(sys.modules[__name__], "_set_size", lambda *_: None)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(
        sys.modules[__name__], "_wait_for_input_ready_after", input_ready
    )
    monkeypatch.setattr(os, "write", lambda fd, data: len(data))
    monkeypatch.setattr(os, "read", lambda *args: b"")
    monkeypatch.setattr(select, "select", lambda *args: ([args[0][0]], [], []))
    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        _run_untrusted_product(tmp_path, tmp_path, tmp_path)
    assert closed == [slave, master]
