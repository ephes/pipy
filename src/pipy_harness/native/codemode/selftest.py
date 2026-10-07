"""Fail-closed availability check for codemode (spike result §3).

Codemode is available only when every check passes:

1. the ``wasmtime`` distribution is importable by pipy's interpreter;
2. the pinned runtime is installed and its tree matches the manifest;
3. a fresh worker process (pipy's interpreter, ``-I``, its own process group,
   environment reduced to ``PATH``) imports ``wasmtime``, deserializes or
   recompiles the ``.cwasm`` cache under the exact engine configuration,
   instantiates the module, and reports backend ``wasi`` with the pinned
   runtime hash.

Any failure yields ``Availability(available=False, reason=...)``. The check
never raises, never imports ``wasmtime`` into the host process, and never runs a
script: there is no fallback interpreter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipy_harness.native.codemode.runtime import (
    RUNTIME_PIN,
    CodemodePaths,
    CodemodeRuntimeError,
    RuntimePin,
    default_paths,
    verify_runtime,
)

WORKER_PATH = Path(__file__).with_name("worker.py")
EXTRA_HINT = "install pipy with the 'codemode' extra"
_SELFTEST_TIMEOUT_SECONDS = 60.0
_REAP_TIMEOUT_SECONDS = 5.0
_REPLY_LIMIT = 64 * 1024
_REASON_LIMIT = 300


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    reason: str | None = None
    wasmtime_version: str | None = None
    runtime_sha256: str | None = None
    cache: str | None = None


def availability(
    paths: CodemodePaths | None = None,
    *,
    pin: RuntimePin = RUNTIME_PIN,
    python: str = sys.executable,
    timeout: float = _SELFTEST_TIMEOUT_SECONDS,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> Availability:
    """Return whether codemode can run here, with the reason when it cannot."""

    paths = paths or default_paths()
    try:
        if find_spec("wasmtime") is None:
            return _unavailable(f"the wasmtime package is not installed ({EXTRA_HINT})")
        runtime_dir = verify_runtime(paths, pin)
        returncode, reply = _run_selftest(python, runtime_dir, paths.cache_dir, timeout)
    except CodemodeRuntimeError as exc:
        return _unavailable(str(exc))
    except Exception as exc:  # noqa: BLE001 - availability is a fail-closed probe and never raises
        return _unavailable(f"self-test failed: {type(exc).__name__}: {exc}")
    return _judge(reply, returncode, pin)


def _unavailable(reason: str) -> Availability:
    return Availability(available=False, reason=reason[:_REASON_LIMIT])


def _run_selftest(
    python: str, runtime_dir: Path, cache_dir: Path, timeout: float
) -> tuple[int | None, dict[str, Any]]:
    request = (
        json.dumps({"runtime_dir": str(runtime_dir), "cache_dir": str(cache_dir)})
        + "\n"
    )
    process = subprocess.Popen(  # noqa: S603 - fixed argv: pipy's interpreter and its own worker file
        [python, "-I", str(WORKER_PATH), "selftest"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", os.defpath)},
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(request.encode(), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CodemodeRuntimeError(f"self-test timed out after {timeout:g} s") from exc
    finally:
        _kill_group(process)
    return process.returncode, _parse_reply(process.returncode, stdout, stderr)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill and reap the worker's own process group, never anything else.

    The group is signalled only while its leader is still unreaped: once the
    leader has been waited for, its pid (and so the group id) may be reused by
    an unrelated process. The self-test worker starts no processes, so a reaped
    leader leaves no group behind.
    """

    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _parse_reply(
    returncode: int | None, stdout: bytes, stderr: bytes
) -> dict[str, Any]:
    lines = stdout[-_REPLY_LIMIT:].decode("utf-8", "replace").strip().splitlines()
    try:
        reply = json.loads(lines[-1]) if lines else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or reply.get("type") != "selftest":
        detail = stderr[-_REASON_LIMIT:].decode("utf-8", "replace").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        raise CodemodeRuntimeError(
            f"self-test worker exited with status {returncode} and no report{tail}"
        )
    return reply


def _judge(
    reply: dict[str, Any], returncode: int | None, pin: RuntimePin
) -> Availability:
    if reply.get("ok") is True and returncode != 0:
        return _unavailable(
            f"self-test worker reported success but exited with status {returncode}"
        )
    if reply.get("ok") is not True:
        reason = reply.get("reason")
        return _unavailable(
            f"self-test failed: {reason if isinstance(reason, str) else 'no reason given'}"
        )
    if reply.get("backend") != "wasi":
        return _unavailable(
            f"self-test reported an unexpected backend {reply.get('backend')!r}"
        )
    if reply.get("runtime_sha256") != pin.sha256:
        return _unavailable(
            "self-test reported a runtime that does not match the pinned sha256"
        )
    version = reply.get("wasmtime")
    cache = reply.get("cache")
    return Availability(
        available=True,
        wasmtime_version=version if isinstance(version, str) else None,
        runtime_sha256=pin.sha256,
        cache=cache if isinstance(cache, str) else None,
    )
