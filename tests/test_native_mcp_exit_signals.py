"""A pipy that is sent SIGTERM or SIGHUP ends its stdio MCP servers.

The real command runs in a process of its own with a server that is still
connecting and ignores the end of its stdin, the kind that stays behind when
pipy ends without its close sequence (STD-05, STD-06).
"""

from __future__ import annotations

import json
import os
import pty
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

import pytest
from mcp_fake_server_guard import end_fake_servers, fake_servers
from pty_sync import wait_for_output

_FAKE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fake_server.py"
_TIMEOUT = 20.0
_PIPY = [sys.executable, "-m", "pipy_harness.cli"]


class _Workspace:
    """An isolated config home with one server. Without a scenario it never
    finishes ``initialize``, does not notice the end of its stdin and
    records SIGTERM."""

    def __init__(self, root: Path, scenario: dict[str, object] | None = None) -> None:
        self.root = root
        self.record = root / "record.jsonl"
        if scenario is None:
            scenario = {
                "methods": {"initialize": [{"record": "initialize"}, {"sleep": 600}]},
                "onStdinClose": [],
                "onSigterm": [{"exit": 0}],
            }
        scenario_path = root / "scenario.json"
        scenario_path.write_text(
            json.dumps({"record": str(self.record), **scenario}), encoding="utf-8"
        )
        config = root / "config"
        config.mkdir()
        server = {
            "command": sys.executable,
            "args": ["-I", str(_FAKE_SERVER), "--scenario", str(scenario_path)],
            "exposure": "deferred",
        }
        (config / "mcp.json").write_text(
            json.dumps({"mcpServers": {"slow": server}}), encoding="utf-8"
        )
        self.cwd = root / "work"
        self.cwd.mkdir()
        (root / "home").mkdir()
        self.env = dict(os.environ)
        for name, leaf in (
            ("HOME", "home"),
            ("PIPY_CONFIG_HOME", "config"),
            ("PIPY_NATIVE_THEME_PATH", "theme.json"),
            ("PIPY_NATIVE_DEFAULTS_PATH", "defaults.json"),
            ("PIPY_AUTH_DIR", "auth"),
            ("PIPY_PROMPT_HISTORY_PATH", "prompt-history.json"),
            ("PIPY_NATIVE_SESSIONS_ROOT", "sessions"),
            ("XDG_STATE_HOME", "state"),
        ):
            self.env[name] = str(root / leaf)
        self.env["PIPY_OFFLINE"] = "1"
        self.env["TERM"] = "xterm-256color"
        self.env.pop("NO_COLOR", None)

    def events(self) -> list[str]:
        if not self.record.exists():
            return []
        lines = self.record.read_text(encoding="utf-8").splitlines()
        return [event for line in lines if (event := json.loads(line).get("event"))]

    def wait_for_the_server(self, pipy: subprocess.Popen[bytes]) -> int:
        """Until the server got ``initialize``; returns its pid."""

        deadline = time.monotonic() + _TIMEOUT
        while "initialize" not in self.events():
            assert pipy.poll() is None, "pipy ended before its server started"
            assert time.monotonic() < deadline, "the server was not started"
            time.sleep(0.02)
        (pid,) = fake_servers(self.root)
        return pid


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[_Workspace]:
    workspace = _Workspace(tmp_path)
    try:
        yield workspace
    finally:
        # Nothing of a failed case stays for the next one.
        end_fake_servers(tmp_path)


def _wait_until_gone(pid: int) -> None:
    deadline = time.monotonic() + _TIMEOUT
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        assert time.monotonic() < deadline, f"server {pid} still runs"
        time.sleep(0.02)


def _read_until(
    stream: IO[bytes], predicate: Callable[[dict[str, object]], bool]
) -> dict[str, object]:
    for line in stream:
        record = json.loads(line)
        if predicate(record):
            return record
    raise AssertionError("pipy ended its output first")


def _end(pipy: subprocess.Popen[bytes]) -> None:
    if pipy.poll() is None:
        pipy.kill()
        pipy.wait(timeout=5)


@pytest.mark.parametrize(
    ("signum", "status"), [(signal.SIGTERM, 143), (signal.SIGHUP, 129)]
)
def test_an_rpc_session_that_is_signalled_closes_its_server(
    workspace: _Workspace, signum: signal.Signals, status: int
) -> None:
    pipy = subprocess.Popen(  # noqa: S603
        [*_PIPY, "--mode", "rpc"],
        cwd=workspace.cwd,
        env=workspace.env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        server = workspace.wait_for_the_server(pipy)

        pipy.send_signal(signum)

        assert pipy.wait(timeout=_TIMEOUT) == status
        # The server does not read its stdin while it sleeps in
        # `initialize`, so only SIGTERM ends it.
        _wait_until_gone(server)
        assert workspace.events() == ["initialize", "sigterm"]
    finally:
        _end(pipy)
        assert pipy.stdin is not None
        pipy.stdin.close()


@pytest.fixture(params=["pipe", "bounded-socket"])
def rpc_stdout(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[int | socket.socket, IO[bytes] | None, int | None]]:
    """Run the same RPC signal scenario with ordinary and bounded stdout."""

    if request.param == "pipe":
        yield subprocess.PIPE, None, None
        return
    receiver, sender = socket.socketpair()
    with receiver, sender:
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        capacity = sender.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        with receiver.makefile("rb") as output:
            yield sender, output, capacity


def test_an_rpc_session_signalled_during_a_run_aborts_it_drops_the_queue_and_closes_its_server(
    workspace: _Workspace,
    rpc_stdout: tuple[int | socket.socket, IO[bytes] | None, int | None],
) -> None:
    destination, output, send_buffer = rpc_stdout
    pipy = subprocess.Popen(  # noqa: S603
        [*_PIPY, "--mode", "rpc"],
        cwd=workspace.cwd,
        env=workspace.env,
        stdin=subprocess.PIPE,
        stdout=(
            destination.fileno()
            if isinstance(destination, socket.socket)
            else destination
        ),
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    reader: threading.Thread | None = None
    try:
        if isinstance(destination, socket.socket):
            destination.close()
        output = output if output is not None else pipy.stdout
        assert pipy.stdin is not None and output is not None
        server = workspace.wait_for_the_server(pipy)
        # The fake provider holds a turn that starts with BLOCK for 30
        # seconds, or until the turn is cancelled.
        prompt = {"id": "p", "type": "prompt", "message": "BLOCK and wait"}
        pipy.stdin.write(json.dumps(prompt).encode() + b"\n")
        pipy.stdin.flush()
        _read_until(output, lambda record: record.get("type") == "agent_start")
        follow_up = {"id": "f", "type": "follow_up", "message": "SECOND"}
        pipy.stdin.write(json.dumps(follow_up).encode() + b"\n")
        pipy.stdin.flush()
        _read_until(output, lambda record: record.get("id") == "f")

        chunks: list[bytes] = []
        reader_errors: list[Exception] = []

        def drain() -> None:
            try:
                chunks.append(output.read())
            except Exception as exc:  # noqa: BLE001 - surface reader failures below
                reader_errors.append(exc)

        # JSONL flushes honor stdout backpressure. Drain before signalling/waiting
        # so a full transport cannot block the worker that shutdown must join.
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        pipy.send_signal(signal.SIGTERM)

        # Well before the provider would have answered.
        assert pipy.wait(timeout=_TIMEOUT) == 143
        _wait_until_gone(server)
        assert workspace.events() == ["initialize", "sigterm"]
        # The queued follow-up did not run.
        reader.join(2)
        assert not reader.is_alive(), "RPC stdout reader did not reach EOF"
        assert not reader_errors, reader_errors
        remainder = b"".join(chunks)
        if send_buffer is not None:
            assert len(remainder) > send_buffer, (
                "fixture did not exceed its send buffer"
            )
        rest = [json.loads(line) for line in remainder.splitlines()]
        assert not [record for record in rest if record.get("type") == "agent_start"]
        assert "SEEN:SECOND" not in json.dumps(rest)
    finally:
        _end(pipy)
        if reader is not None:
            reader.join(2)
            assert not reader.is_alive(), "RPC stdout reader survived child cleanup"
        for stream in (pipy.stdin, output):
            if stream is not None:
                stream.close()


def test_a_print_run_that_is_signalled_closes_its_server(
    workspace: _Workspace,
) -> None:
    # Keep print alive until the signal; MCP connects in the background.
    pipy = subprocess.Popen(  # noqa: S603
        [*_PIPY, "-p", "BLOCK and wait"],
        cwd=workspace.cwd,
        env=workspace.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        server = workspace.wait_for_the_server(pipy)

        pipy.send_signal(signal.SIGTERM)

        assert pipy.wait(timeout=_TIMEOUT) == 143
        _wait_until_gone(server)
        assert workspace.events() == ["initialize", "sigterm"]
    finally:
        _end(pipy)


def test_pipy_mcp_list_that_is_signalled_closes_its_server(
    workspace: _Workspace,
) -> None:
    pipy = subprocess.Popen(  # noqa: S603
        [*_PIPY, "mcp", "list"],
        cwd=workspace.cwd,
        env=workspace.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        server = workspace.wait_for_the_server(pipy)

        pipy.send_signal(signal.SIGTERM)

        assert pipy.wait(timeout=_TIMEOUT) == 143
        _wait_until_gone(server)
        assert workspace.events() == ["initialize", "sigterm"]
    finally:
        _end(pipy)


def test_a_terminal_session_that_is_signalled_restores_the_terminal_and_closes_its_server(
    workspace: _Workspace,
) -> None:
    master, slave = pty.openpty()
    env = {**workspace.env, "COLUMNS": "120", "LINES": "40"}
    pipy = subprocess.Popen(  # noqa: S603
        _PIPY,
        cwd=workspace.cwd,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    chunks: list[bytes] = []

    def drain() -> None:
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        # Bracketed paste on: the terminal UI owns the terminal.
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        server = workspace.wait_for_the_server(pipy)
        before = len(b"".join(chunks))

        pipy.send_signal(signal.SIGTERM)

        assert pipy.wait(timeout=_TIMEOUT) == 143
        _wait_until_gone(server)
        assert workspace.events() == ["initialize", "sigterm"]
        # Bracketed paste off again: the terminal was given back.
        assert wait_for_output(chunks, b"\x1b[?2004l", after=before, timeout=_TIMEOUT)
    finally:
        _end(pipy)
        os.close(master)
        reader.join(2)


def test_a_signal_during_the_close_of_a_finished_run_still_ends_the_server(
    tmp_path: Path,
) -> None:
    # The server connects and reads its stdin, and ignores both its end and
    # SIGTERM, so the close of the finished run takes until SIGKILL.
    workspace = _Workspace(
        tmp_path,
        {
            "startup": [{"record": "started"}],
            "onStdinClose": [],
            "onSigterm": [],
        },
    )
    pipy = subprocess.Popen(  # noqa: S603
        [*_PIPY, "-p", "hello"],
        cwd=workspace.cwd,
        env=workspace.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + _TIMEOUT
        while "stdin-closed" not in workspace.events():
            assert time.monotonic() < deadline, "the run did not close its server"
            time.sleep(0.01)
        (server,) = fake_servers(tmp_path)

        pipy.send_signal(signal.SIGTERM)

        assert pipy.wait(timeout=_TIMEOUT) == 143
        # The interrupted close is finished on the way out: pipy does not
        # end before the server did.
        with pytest.raises(ProcessLookupError):
            os.kill(server, 0)
        assert "sigterm" in workspace.events()
    finally:
        _end(pipy)
        end_fake_servers(tmp_path)
