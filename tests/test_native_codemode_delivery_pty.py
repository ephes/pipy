"""Real input-waiter Escape, child rows, persistence and startup replay."""

from __future__ import annotations

import errno
import json
import os
import pty
import pwd
import select
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic

import pytest
from codemode_driver import real_runtime_skip_reason
from pty_sync import output_bytes, wait_for_input_ready_after, wait_for_output


def launch(root, phase, replay=None, handoff=None):
    master, slave = pty.openpty()
    argv = [
        sys.executable,
        str(Path(__file__).with_name("codemode_tui_evidence.py")),
        "--workspace",
        str(root),
        "--phase",
        phase,
    ]
    if replay:
        argv += ["--replay", str(replay)]
    if handoff is not None:
        argv += ["--handoff-fd", str(handoff.fileno())]
    env = dict(os.environ)
    # The suite isolates HOME globally. Preserve the installed runtime home;
    # the driver isolates product settings/theme/session state itself.
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "120"
    env["LINES"] = "40"
    env.pop("NO_COLOR", None)
    process = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        env=env,
        pass_fds=() if handoff is None else (handoff.fileno(),),
    )  # noqa: S603
    os.close(slave)
    chunks = []
    stop = threading.Event()

    def drain():
        while not stop.is_set():
            if not select.select([master], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            if not chunk:
                return
            chunks.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    return process, master, chunks, stop, reader


def send_first_escape(fd, handoff, deadline):
    if handoff is not None:
        assert select.select([handoff], [], [], max(0, deadline - monotonic()))[0], (
            "read/compute barrier missing"
        )
        assert handoff.recv(1) == b"H"
    os.write(fd, b"\x1b")
    if handoff is not None:
        handoff.sendall(b"R")


def stop_recorded_workers(root):
    recorded = root / "worker-pids.json"
    if recorded.exists():
        for pid in json.loads(recorded.read_text()):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def reap_failed_process(process):
    if process.poll() is None:
        # Allow the session thread to cancel/reap its guest before escalation.
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def close_capture(master, stop, reader):
    try:
        stop.set()
        reader.join(2)
        assert not reader.is_alive(), "PTY reader did not stop"
    finally:
        os.close(master)


def finish(process, master, chunks, stop, reader, root):
    """Assert normal exit, or preserve the active failure while cleaning up."""
    try:
        if sys.exception() is None:
            assert process.wait(timeout=15) == 0, b"".join(chunks).decode(
                errors="replace"
            )
    finally:
        original = sys.exception()
        try:
            if original is not None:
                reap_failed_process(process)
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            assert original is not None
            original.add_note(f"PTY process cleanup: {cleanup_error!r}")
        finally:
            try:
                # Worker sessions are distinct. Parent kill does not establish
                # that those groups stopped; retire recorded survivors too.
                if original is not None:
                    stop_recorded_workers(root)
            except (OSError, json.JSONDecodeError) as cleanup_error:
                assert original is not None
                original.add_note(f"PTY worker cleanup: {cleanup_error!r}")
            finally:
                try:
                    close_capture(master, stop, reader)
                except (OSError, AssertionError, RuntimeError) as cleanup_error:
                    if original is None:
                        raise
                    original.add_note(f"PTY capture cleanup: {cleanup_error!r}")


@pytest.mark.parametrize("phase", ["success", "bash", "compute"])
def test_full_tui_driver_and_replay(tmp_path, phase):
    reason = real_runtime_skip_reason()
    if reason:
        if os.environ.get("PIPY_CODEMODE_REQUIRE_RUNTIME") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    root = tmp_path / "evidence"
    handoff, child_handoff = socket.socketpair() if phase == "compute" else (None, None)
    try:
        process, fd, chunks, stop, reader = launch(root, phase, handoff=child_handoff)
    finally:
        if child_handoff is not None:
            child_handoff.close()
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=15)
        os.write(fd, b"summarize fixtures\r")
        if phase == "success":
            assert wait_for_output(chunks, "… bash", timeout=15)
            assert wait_for_output(chunks, "✓ read", timeout=15)
        else:
            deadline = monotonic() + 15
            marker = "… bash" if phase == "bash" else "✓ read"
            assert wait_for_output(
                chunks, marker, timeout=max(0, deadline - monotonic())
            )
            if phase == "bash":
                from test_native_codemode_delivery import wait_until

                wait_until(lambda: (root / "bash-active").exists())
            send_first_escape(fd, handoff, deadline)
            assert wait_for_input_ready_after(chunks, "Script aborted", timeout=15)
            if phase == "bash":
                assert wait_for_output(chunks, "⊘ bash")
            os.write(fd, b"fresh summary\r")
        summary_end = wait_for_output(chunks, '"count": 3', timeout=15)
        assert summary_end is not None
        # The parent result paints before the final provider continuation.
        # Wait for its *later* answer and editor readiness, not a raw-mode
        # transition owned by an active-turn watcher between loop steps.
        assert wait_for_input_ready_after(
            chunks, "T9_DONE", after=summary_end, timeout=15
        )
        os.write(fd, b"/exit\r")
    finally:
        if handoff is not None:
            handoff.close()
        finish(process, fd, chunks, stop, reader, root)
    verified = json.loads((root / "verified.json").read_text())
    assert verified["workersReaped"] and verified["phase"] == phase
    tree = Path((root / "session-path.txt").read_text())
    entries = [json.loads(line) for line in tree.read_text().splitlines()]
    result_entry = next(
        e
        for e in reversed(entries)
        if e.get("type") == "message" and e["message"]["role"] == "tool"
    )
    retained_results = [
        e
        for e in entries
        if e.get("type") == "message" and e["message"]["role"] == "tool"
    ]
    replay = launch(tmp_path / "replay", "success", tree)
    process, fd, chunks, stop, reader = replay
    try:
        assert wait_for_input_ready_after(chunks, "✓ read", timeout=15)
        assert wait_for_output(chunks, "gamma.txt")
        if phase == "bash":
            assert wait_for_output(chunks, "⊘ bash")
        # Select the real retained parent result via the product command. A
        # fresh replay frame must reconstruct its nested evidence again.
        after = len(output_bytes(chunks))
        os.write(fd, f"/tree select {result_entry['id']}\r".encode())
        assert wait_for_input_ready_after(
            chunks, "continuing from entry", after=after, timeout=15
        )
        assert wait_for_output(chunks, "✓ read", after=after)
        os.write(fd, b"/exit\r")
    finally:
        finish(process, fd, chunks, stop, reader, tmp_path / "replay")
    assert not (tmp_path / "replay" / "script-worker-started").exists()
    reopened = [json.loads(line) for line in tree.read_text().splitlines()]
    assert [
        e
        for e in reopened
        if e.get("type") == "message" and e["message"]["role"] == "tool"
    ] == retained_results


def test_failed_assertion_reaps_child_without_success_wait(tmp_path, monkeypatch):
    """A failing product assertion must survive failure cleanup unchanged."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )  # noqa: S603
    os.close(slave)
    stop = threading.Event()
    reader = threading.Thread(target=lambda: stop.wait(10))
    reader.start()
    waits = []
    original_wait = process.wait

    def wait(timeout=None):
        waits.append(timeout)
        return original_wait(timeout=timeout)

    monkeypatch.setattr(process, "wait", wait)
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )  # noqa: S603
    (tmp_path / "worker-pids.json").write_text(json.dumps([worker.pid]))
    failure = AssertionError("original product assertion")
    with pytest.raises(AssertionError) as caught:
        try:
            raise failure
        finally:
            finish(process, master, [], stop, reader, tmp_path)
    assert worker.wait(timeout=5) == -signal.SIGKILL
    assert caught.value is failure
    assert process.poll() is not None and not reader.is_alive()
    assert 15 not in waits
    with pytest.raises(OSError) as closed:
        os.fstat(master)
    assert closed.value.errno == errno.EBADF
