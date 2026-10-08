"""Real input-waiter Escape, child rows, persistence and startup replay."""

from __future__ import annotations

import errno
import json
import os
import pty
import pwd
import select
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from codemode_driver import real_runtime_skip_reason
from pty_sync import output_bytes, wait_for_input_ready_after, wait_for_output


def launch(root, phase, replay=None):
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
    env = dict(os.environ)
    # The suite isolates HOME globally. Preserve the installed runtime home;
    # the driver isolates product settings/theme/session state itself.
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "120"
    env["LINES"] = "40"
    env.pop("NO_COLOR", None)
    process = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env=env
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


def finish(process, master, chunks, stop, reader):
    try:
        assert process.wait(timeout=15) == 0, b"".join(chunks).decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        stop.set()
        reader.join(2)
        os.close(master)


@pytest.mark.parametrize("phase", ["success", "bash", "compute"])
def test_full_tui_driver_and_replay(tmp_path, phase):
    reason = real_runtime_skip_reason()
    if reason:
        if os.environ.get("PIPY_CODEMODE_REQUIRE_RUNTIME") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    root = tmp_path / "evidence"
    process, fd, chunks, stop, reader = launch(root, phase)
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=15)
        os.write(fd, b"summarize fixtures\r")
        if phase == "success":
            assert wait_for_output(chunks, "… bash", timeout=15)
            assert wait_for_output(chunks, "✓ read", timeout=15)
        else:
            marker = "… bash" if phase == "bash" else "✓ read"
            assert wait_for_output(chunks, marker, timeout=15)
            if phase == "bash":
                from test_native_codemode_delivery import wait_until

                wait_until(lambda: (root / "bash-active").exists())
            os.write(fd, b"\x1b")
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
        finish(process, fd, chunks, stop, reader)
    verified = json.loads((root / "verified.json").read_text())
    assert verified["workersReaped"] and verified["phase"] == phase
    tree = Path((root / "session-path.txt").read_text())
    entries = [json.loads(line) for line in tree.read_text().splitlines()]
    result_entry = next(
        e
        for e in reversed(entries)
        if e.get("type") == "message" and e["message"]["role"] == "tool"
    )
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
        finish(process, fd, chunks, stop, reader)
