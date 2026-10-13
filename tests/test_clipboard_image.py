"""Tests for the OS clipboard-image read helper (Pi Ctrl+V parity)."""

from __future__ import annotations

import sys
import time

import pytest

from pipy_harness.native.clipboard import _default_run_capture, read_clipboard_image

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_reads_png_via_pngpaste_on_macos() -> None:
    captured: list[list[str]] = []

    def run_capture(argv: list[str]) -> bytes | None:
        captured.append(argv)
        return _PNG if argv[0].endswith("pngpaste") else None

    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=run_capture,
    )
    assert result.found
    assert result.data == _PNG
    assert result.media_type == "image/png"


def test_reads_via_wl_paste_on_linux() -> None:
    def run_capture(argv: list[str]) -> bytes | None:
        return _JPEG if argv[0].endswith("wl-paste") else None

    result = read_clipboard_image(
        platform="linux",
        which=lambda name: f"/usr/bin/{name}" if name == "wl-paste" else None,
        run_capture=run_capture,
    )
    assert result.found
    assert result.media_type == "image/jpeg"


def test_no_tool_available_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="linux",
        which=lambda name: None,
        run_capture=lambda argv: None,
    )
    assert not result.found
    assert "no clipboard image tool" in result.detail.lower()


def test_non_image_clipboard_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: b"this is just text, not an image",
    )
    assert not result.found


def test_empty_clipboard_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: b"",
    )
    assert not result.found


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_reads_stdout_bytes() -> None:
    # The bounded reader returns the helper's stdout verbatim (happy path).
    data = _default_run_capture(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'PNGBYTES')"]
    )
    assert data == b"PNGBYTES"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_times_out_on_hanging_helper() -> None:
    # A clipboard helper that never writes and never exits must not hang the
    # editor when Ctrl+V is pressed: the bounded reader gives up after the
    # deadline, kills the helper, and reports "no image" (None) promptly.
    start = time.monotonic()
    data = _default_run_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.3,
    )
    elapsed = time.monotonic() - start
    assert data is None
    assert elapsed < 10.0, "reader blocked on the hanging helper instead of timing out"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_does_not_inherit_stdin() -> None:
    # The helper must not be able to consume the session's terminal stdin: its
    # stdin is /dev/null, so a stdin-reading helper sees immediate EOF and the
    # reader returns its (empty) output rather than blocking on terminal input.
    data = _default_run_capture(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        timeout=2.0,
    )
    assert data == b""


def test_oversized_clipboard_image_is_rejected() -> None:
    # A clipboard image larger than the attachment cap must not be returned
    # (so it is never written to disk), bounding memory/disk via Ctrl+V.
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024)
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: big,
    )
    assert not result.found
    assert "too large" in result.detail.lower()


@pytest.mark.parametrize("returncode", [None, 1, -9])
def test_unfinished_or_failed_capture_is_not_an_image(returncode) -> None:
    from pipy_harness.native.clipboard import (
        _CaptureReadState,
        _classify_capture_result,
    )

    state = _CaptureReadState([_PNG], total=len(_PNG))
    assert _classify_capture_result(returncode, state, len(_PNG) + 1) is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
def test_cleanup_waits_again_after_exit_timeout(monkeypatch) -> None:
    import io
    import os
    import subprocess
    from typing import cast

    from pipy_harness.native import clipboard

    calls: list[str] = []

    class Process:
        pid = 123
        returncode: int | None = None

        def wait(self, *, timeout):
            calls.append("wait")
            if calls == ["wait"]:
                raise subprocess.TimeoutExpired("clipboard-helper", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(os, "killpg", lambda *_: calls.append("kill"))
    proc = Process()
    state = clipboard._CaptureReadState([_PNG], total=len(_PNG))
    clipboard._cleanup_capture_process(
        cast(subprocess.Popen[bytes], proc),
        io.BytesIO(),
        state,
        len(_PNG) + 1,
        deadline=time.monotonic() + 1.0,
    )
    assert calls[:3] == ["wait", "kill", "wait"]
    assert proc.returncode == -9
    assert clipboard._classify_capture_result(0, state, len(_PNG) + 1) is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_eof_before_hung_exit_is_reaped_and_not_an_image(monkeypatch) -> None:
    import os
    import subprocess

    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    try:
        result = read_clipboard_image(
            platform="darwin",
            which=lambda name: sys.executable if name == "pngpaste" else None,
            run_capture=lambda _: _default_run_capture(
                [
                    sys.executable,
                    "-c",
                    f"import os,time; os.write(1, {_PNG!r}); os.close(1); time.sleep(60)",
                ],
                timeout=2.0,
            ),
        )
        assert not result.found
        assert processes[0].returncode is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
def test_group_kill_permission_error_still_closes_and_reaps(monkeypatch) -> None:
    import io
    import os
    import subprocess
    from typing import cast

    from pipy_harness.native import clipboard

    calls: list[str] = []

    class Process:
        pid = 123

        def wait(self, *, timeout):
            calls.append("wait")
            return 0

    def denied(*_):
        calls.append("kill")
        raise PermissionError("zombie-only group")

    monkeypatch.setattr(os, "killpg", denied)
    stdout = io.BytesIO()
    state = clipboard._CaptureReadState([_PNG], total=len(_PNG))
    clipboard._cleanup_capture_process(
        cast(subprocess.Popen[bytes], Process()),
        stdout,
        state,
        len(_PNG),
        deadline=time.monotonic() + 1.0,
    )
    assert stdout.closed
    assert calls[:2] == ["kill", "wait"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
def test_capture_kills_descendant_holding_stdout(tmp_path, monkeypatch) -> None:
    import os
    import select
    import signal
    import subprocess

    pid_path = tmp_path / "child.pid"
    read_fd, write_fd = os.pipe()
    popen = subprocess.Popen
    write_open = True

    def launch(*args, **kwargs):
        nonlocal write_open
        kwargs["pass_fds"] = (write_fd,)
        proc = popen(*args, **kwargs)
        os.close(write_fd)
        write_open = False
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    script = (
        "import os,subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        f"pass_fds=({write_fd},)); "
        f"open({str(pid_path)!r},'w').write(str(child.pid)); "
        f"os.write(1, {_PNG!r})"
    )
    child_pid = None
    descendant_stopped = False
    try:
        assert _default_run_capture([sys.executable, "-c", script], timeout=2) is None
        child_pid = int(pid_path.read_text())
        # EOF on a second inherited pipe proves the descendant stopped, even
        # if init has not yet reaped its zombie. No process-table access needed.
        ready, _, _ = select.select([read_fd], [], [], 5)
        assert ready, "clipboard descendant survived cleanup"
        assert os.read(read_fd, 1) == b""
        descendant_stopped = True
    finally:
        os.close(read_fd)
        if write_open:
            os.close(write_fd)
        if child_pid is None and pid_path.exists():
            child_pid = int(pid_path.read_text())
        if child_pid is not None and not descendant_stopped:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass
