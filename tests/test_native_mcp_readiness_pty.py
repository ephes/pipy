"""Escape ends a wait for an MCP server in the real terminal UI (S18, SES-10)."""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp_fake_server_guard import end_fake_servers
from pty_sync import wait_for_input_ready_after, wait_for_output

from pipy_harness.native.mcp.readiness import STILL_CONNECTING_NOTICE

_TIMEOUT = 20


def _launch(root: Path, phase: str):
    master, slave = pty.openpty()
    argv = [
        sys.executable,
        str(Path(__file__).with_name("mcp_tui_driver.py")),
        "--workspace",
        str(root),
        "--phase",
        phase,
    ]
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "120"
    env["LINES"] = "40"
    env.pop("NO_COLOR", None)
    process = subprocess.Popen(  # noqa: S603
        argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env=env
    )
    os.close(slave)
    chunks: list[bytes] = []
    stop = threading.Event()

    def drain() -> None:
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


def _finish(process, master, chunks, stop, reader) -> None:
    try:
        assert process.wait(timeout=_TIMEOUT) == 0, b"".join(chunks).decode(
            errors="replace"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
            # A killed session cannot end its servers, and the one of the
            # waiting phases ignores the end of its stdin.
            end_fake_servers(Path(process.args[process.args.index("--workspace") + 1]))
        stop.set()
        reader.join(2)
        os.close(master)


def _wait_for_initialize(root: Path) -> None:
    """Until the server got `initialize`, which it never answers."""

    record = root / "record.jsonl"
    deadline = time.monotonic() + _TIMEOUT
    while True:
        if record.exists() and any(
            json.loads(line).get("event") == "initialize"
            for line in record.read_text().splitlines()
        ):
            return
        assert time.monotonic() < deadline, "the server was not started"
        time.sleep(0.02)


def test_escape_ends_the_first_prompts_wait_and_the_prompt_goes_on(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "prompt")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        _wait_for_initialize(root)
        os.write(fd, b"hello\r")
        # The prompt is held: nothing answers while the server connects.
        assert wait_for_output(chunks, "S18_DONE", timeout=1.0) is None
        os.write(fd, b"\x1b")
        noticed = wait_for_output(
            chunks, STILL_CONNECTING_NOTICE[:40], timeout=_TIMEOUT
        )
        assert noticed is not None
        assert wait_for_input_ready_after(
            chunks, "S18_DONE", after=noticed, timeout=_TIMEOUT
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()


def test_escape_ends_a_tool_calls_wait_and_cancels_the_turn(tmp_path: Path) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "tool")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        _wait_for_initialize(root)
        os.write(fd, b"find the docs\r")
        # The first prompt does not wait for a server without direct tools;
        # the provider's `tool_search` call does.
        started = wait_for_output(chunks, "tool_search", timeout=_TIMEOUT)
        assert started is not None
        assert wait_for_output(chunks, "S18_DONE", after=started, timeout=1.0) is None
        os.write(fd, b"\x1b")
        assert wait_for_input_ready_after(
            chunks, "cancelled", after=started, timeout=_TIMEOUT
        )
        # The turn ended with the call; the session takes the next prompt.
        after = len(b"".join(chunks))
        os.write(fd, b"again\r")
        assert wait_for_input_ready_after(
            chunks, "S18_DONE", after=after, timeout=_TIMEOUT
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
