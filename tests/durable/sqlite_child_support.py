"""Bounded real child processes for the durable SQLite tests (DE03a onward).

Each child runs ``sys.executable -I -c <prelude + code>`` in its own session and
process group with a minimal environment, as in ``test_memory_storage.py``'s
bounded child. Children report JSON lines on stdout (``say``) and read commands
from stdin (``command``). The parent reads events against deadlines, never
sleeps for ordering, and on exit kills the whole group, reaps the child, closes
every pipe and checks that the group is gone (a forked grandchild included).

Stores normally use the current semantic schema version. Historical migration
tests explicitly select v1 in their child prelude. Tests below the Storage layer
keep scratch rows in the immutable v1 ``conversations`` table as raw bytes
(``put`` and ``stored``, also in the child prelude); nothing reads them as records.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any

SCRATCH_INSERT = "INSERT INTO conversations (record) VALUES (?)"
SCRATCH_SELECT = "SELECT record FROM conversations ORDER BY id"

PRELUDE = f"""
import json, os, sys
sys.path[:0] = {list(map(str, (Path(__file__).resolve().parents[2] / "src", Path(__file__).resolve().parents[1], Path(__file__).resolve().parent)))!r}
from pipy_harness.native.durable import ownership
from pipy_harness.native.durable import sqlite_connection
from pipy_harness.native.durable import sqlite_schema
from pipy_harness.native.durable.sqlite_connection import open_owned_database

def put(connection, value):
    connection.execute({SCRATCH_INSERT!r}, (value.encode(),))

def stored(connection):
    return [v.decode() for (v,) in connection.execute({SCRATCH_SELECT!r})]

def say(event, **fields):
    sys.stdout.write(json.dumps({{"event": event, "pid": os.getpid(), **fields}}) + "\\n")
    sys.stdout.flush()

def command():
    return sys.stdin.readline().strip()

def modes(root):
    found = {{}}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(top, name)
            found[os.path.relpath(full, root)] = oct(os.lstat(full).st_mode & 0o7777)
    return found
"""


def put(connection: sqlite3.Connection, value: str) -> None:
    """A scratch row: raw bytes in the v1 ``conversations`` table."""

    connection.execute(SCRATCH_INSERT, (value.encode(),))


def stored(connection: sqlite3.Connection) -> list[str]:
    return [value.decode() for (value,) in connection.execute(SCRATCH_SELECT)]


def group_exists(group: int) -> bool:
    """Whether any member, a zombie included, is still in the process group."""

    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS refuses to signal a group whose only members are zombies
        # awaiting their reaper; the group still exists.
        return True
    return True


def process_state(pid: int) -> str:
    """The one-letter scheduler state of ``pid`` ("Z" for a zombie), "" if gone.

    Portable to every supported Python: ``os.waitid`` with ``WNOWAIT`` exists on
    macOS only from Python 3.13. Linux reads ``/proc`` (no ``ps`` in slim
    containers); macOS asks ``ps``.
    """

    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
                return stream.read().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return ""
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.stdout.strip()[:1]


def wait_until_zombie(pid: int, timeout: float = 30.0) -> None:
    """Wait, bounded, until an unreaped child has exited and is a zombie."""

    deadline = time.monotonic() + timeout
    while process_state(pid) != "Z":
        if time.monotonic() >= deadline:
            raise AssertionError(f"Process {pid} never became a zombie")
        time.sleep(0.01)


def kill_group(group: int) -> None:
    """SIGKILL every live member; nothing to do for a gone or all-zombie group."""

    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Gone, or (macOS) only zombies left, which no signal can reach.
        pass


class Child:
    """One bounded child; use as a context manager so it is always reaped."""

    def __init__(self, code: str, *args: str, home: Path, cwd: Path | None = None):
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", PRELUDE + code, *args],
            cwd=cwd,
            env={"PATH": os.defpath, "HOME": str(home), "LC_ALL": "C.UTF-8"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.group = self.process.pid
        assert os.getpgid(self.process.pid) == self.group
        self.events: list[dict[str, Any]] = []
        self._out = b""
        self._err = b""
        self._eof = False
        self._err_eof = False

    @property
    def pid(self) -> int:
        return self.process.pid

    def evidence(self) -> dict[str, Any]:
        return {
            "pid": self.process.pid,
            "group": self.group,
            "returncode": self.process.poll(),
            "events": self.events,
            "pending_stdout": self._out.decode(errors="replace"),
            "stderr": self._err.decode(errors="replace"),
        }

    def _pump(self, deadline: float) -> None:
        """Read whatever is available on stdout/stderr until ``deadline``."""

        assert self.process.stdout is not None and self.process.stderr is not None
        open_streams = {}
        if not self._eof:
            open_streams[self.process.stdout.fileno()] = "out"
        if not self._err_eof:
            open_streams[self.process.stderr.fileno()] = "err"
        with selectors.DefaultSelector() as selector:
            for fd, label in open_streams.items():
                selector.register(fd, selectors.EVENT_READ, label)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not open_streams:
                return
            for key, _ in selector.select(timeout=remaining):
                fd = key.fd
                chunk = os.read(fd, 65536)
                if key.data == "out":
                    if chunk:
                        self._out += chunk
                    else:
                        self._eof = True
                elif chunk:
                    self._err += chunk
                else:
                    self._err_eof = True

    def next_event(self, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._out:
            if self._eof or time.monotonic() >= deadline:
                raise AssertionError(
                    f"No child event before the deadline: {self.evidence()}"
                )
            self._pump(deadline)
        line, self._out = self._out.split(b"\n", 1)
        event: dict[str, Any] = json.loads(line)
        self.events.append(event)
        return event

    def expect(self, name: str, timeout: float = 60.0) -> dict[str, Any]:
        event = self.next_event(timeout)
        assert event["event"] == name, (name, self.evidence())
        return event

    def send(self, text: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(text.encode() + b"\n")
        self.process.stdin.flush()

    def close_stdin(self) -> None:
        assert self.process.stdin is not None
        if not self.process.stdin.closed:
            self.process.stdin.close()

    def wait_stdout_eof(self, timeout: float = 60.0) -> None:
        """Every writer of stdout (child and grandchildren) has exited."""

        deadline = time.monotonic() + timeout
        while not self._eof:
            if time.monotonic() >= deadline:
                raise AssertionError(f"Child output stayed open: {self.evidence()}")
            self._pump(deadline)

    def wait(self, timeout: float = 60.0) -> int:
        return self.process.wait(timeout=timeout)

    def kill_child_only(self) -> None:
        """SIGKILL the child itself, leaving any grandchild in the group alive."""

        self.process.kill()
        self.process.wait(timeout=30)

    def kill_group(self) -> None:
        kill_group(self.group)
        self.process.wait(timeout=30)

    def reap(self, timeout: float = 30.0) -> None:
        try:
            if self.process.poll() is None or group_exists(self.group):
                kill_group(self.group)
            self.process.wait(timeout=timeout)
            deadline = time.monotonic() + timeout
            # A reparented grandchild is reaped by init; bound that wait.
            while group_exists(self.group) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not group_exists(self.group), self.evidence()
        finally:
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                if stream is not None and not stream.closed:
                    stream.close()

    def __enter__(self) -> Child:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.reap()
