"""A stand-in for the codemode worker, for host-runner tests (CM1 T3).

Run as ``python -I codemode_fake_worker.py STATUS_FD SHA256 SCENARIO``. It
plays both the worker and the guest: ``SCENARIO`` is Python source executed
with helpers that write status lines, guest protocol lines or raw bytes, so a
test can script any well-behaved or hostile sequence without wasmtime.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import time

STATUS_FD = int(sys.argv[1])
SHA256 = sys.argv[2]
SCENARIO = sys.argv[3]
_stdin = open(0, "rb", buffering=0, closefd=False)  # noqa: SIM115 - lives for the process


def raw(data: bytes, fd: int = 1) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def send(message: object) -> None:
    raw((json.dumps(message) + "\n").encode())


def status(message: object) -> None:
    raw((json.dumps(message) + "\n").encode(), STATUS_FD)


def ready(sha256: str = SHA256, backend: str = "wasi") -> None:
    status(
        {
            "type": "ready",
            "backend": backend,
            "wasmtime": "fake",
            "runtime_sha256": sha256,
        }
    )


def exit_status(reason: str = "ok", **fields: object) -> None:
    status({"type": "exit", "reason": reason, **fields})


def recv() -> dict:
    line = b""
    while not line.endswith(b"\n"):
        chunk = _stdin.read(1)
        if not chunk:
            raise SystemExit(9)
        line += chunk
    return json.loads(line)


def start() -> dict:
    """Report ready and read the run line, as the real worker and guest do."""

    ready()
    return recv()


def call(call_id: int, name: str, args: dict) -> dict:
    send({"type": "call", "id": call_id, "name": name, "args": args})
    return recv()


def done(ok: bool = True) -> None:
    send({"type": "done", "ok": ok})


def hang() -> None:
    while True:
        time.sleep(60)


exec(SCENARIO)  # noqa: S102 - test fixture: the scenario is test source
