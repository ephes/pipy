"""Minimal test driver for the codemode worker (CM1 T2).

This is NOT the production host runner (that is T3). It starts one worker the
way the host will (pipy's interpreter, ``-I``, its own session, a private
status pipe), sends the run request, answers ``call`` messages from a dict of
fake tools, enforces a wall deadline, and always kills and reaps the worker's
process group. Tests use it to observe the guest's messages and the worker's
status lines.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipy_harness.native.codemode import default_paths
from pipy_harness.native.codemode.selftest import WORKER_PATH

REAL_RUNTIME = default_paths(Path(pwd.getpwuid(os.getuid()).pw_dir)).runtime_dir()

ToolFn = Callable[[dict[str, Any]], str]


def real_runtime_skip_reason() -> str | None:
    if importlib.util.find_spec("wasmtime") is None:
        return "wasmtime is not installed (codemode extra)"
    if not REAL_RUNTIME.is_dir():
        return f"codemode runtime is not installed at {REAL_RUNTIME}"
    return None


class ToolFailure(Exception):
    """Raised by a fake tool to send an ``ok: false`` result."""


@dataclass
class WorkerRun:
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: list[dict[str, Any]] = field(default_factory=list)
    returncode: int | None = None
    stderr: str = ""
    timed_out: bool = False
    pid: int = 0

    @property
    def texts(self) -> list[str]:
        return [m["value"] for m in self.messages if m.get("type") == "text"]

    @property
    def output(self) -> str:
        return "".join(self.texts)

    @property
    def done(self) -> dict[str, Any] | None:
        dones = [m for m in self.messages if m.get("type") == "done"]
        return dones[-1] if dones else None

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [m for m in self.messages if m.get("type") == "call"]

    @property
    def exit(self) -> dict[str, Any] | None:
        exits = [s for s in self.status if s.get("type") == "exit"]
        return exits[-1] if exits else None


def run_worker(
    code: str,
    *,
    runtime_dir: Path,
    cache_dir: Path,
    tools: Mapping[str, ToolFn] | None = None,
    tool_names: list[str] | None = None,
    memory_bytes: int = 256 * 1024 * 1024,
    cpu_seconds: float = 30.0,
    wall_seconds: float = 30.0,
    env: Mapping[str, str] | None = None,
    extra_fds: tuple[int, ...] = (),
    first_line: bytes | None = None,
    reply: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    host_pid: int | None = None,
) -> WorkerRun:
    tools = dict(tools or {})
    request = json.dumps(
        {
            "runtime_dir": str(runtime_dir),
            "cache_dir": str(cache_dir),
            "memory_bytes": memory_bytes,
            "cpu_seconds": cpu_seconds,
            "host_pid": os.getpid() if host_pid is None else host_pid,
        }
    )
    status_read, status_write = os.pipe()
    process = subprocess.Popen(  # noqa: S603 - test driver: fixed argv
        [sys.executable, "-I", str(WORKER_PATH), "run", str(status_write), request],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(status_write, *extra_fds),
        env=dict(env) if env is not None else {"PATH": os.environ.get("PATH", "")},
        start_new_session=True,
    )
    os.close(status_write)
    result = WorkerRun(pid=process.pid)
    try:
        assert process.stdin is not None
        if first_line is None:
            names = sorted(tools) if tool_names is None else tool_names
            first_line = (
                json.dumps({"type": "run", "code": code, "tools": names}) + "\n"
            ).encode()
        process.stdin.write(first_line)
        process.stdin.flush()
        _pump(process, result, tools, reply, time.monotonic() + wall_seconds)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        process.wait(timeout=10)
        result.returncode = process.returncode
        result.status = _read_status(status_read)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return result


def _pump(
    process: subprocess.Popen[bytes],
    result: WorkerRun,
    tools: dict[str, ToolFn],
    reply: Callable[[dict[str, Any]], dict[str, Any]] | None,
    deadline: float,
) -> None:
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    buffer = b""
    stderr = b""
    open_streams = 2
    while open_streams:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.timed_out = True
            break
        for key, _ in selector.select(timeout=remaining):
            chunk = os.read(key.fileobj.fileno(), 65536)  # type: ignore[union-attr]
            if not chunk:
                selector.unregister(key.fileobj)
                open_streams -= 1
                continue
            if key.data == "err":
                stderr += chunk
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                result.messages.append(message)
                if message.get("type") == "call":
                    answer = (reply or (lambda m: _answer(m, tools)))(message)
                    assert process.stdin is not None
                    process.stdin.write((json.dumps(answer) + "\n").encode())
                    process.stdin.flush()
    selector.close()
    result.stderr = stderr.decode("utf-8", "replace")


def _answer(message: dict[str, Any], tools: dict[str, ToolFn]) -> dict[str, Any]:
    tool = tools[message["name"]]
    try:
        return {
            "type": "result",
            "id": message["id"],
            "ok": True,
            "value": tool(message["args"]),
        }
    except ToolFailure as exc:
        return {"type": "result", "id": message["id"], "ok": False, "error": str(exc)}


def _read_status(fd: int) -> list[dict[str, Any]]:
    chunks = []
    try:
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    lines = b"".join(chunks).decode().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def group_is_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False
