"""Codemode worker: the only pipy code that imports ``wasmtime``.

The host never imports this module. It runs it as a standalone script,
``<pipy's interpreter> -I <path to this file> <mode> ...``, in a fresh process
group. It is run by path rather than with ``-m`` so that the worker does not
import the ``pipy_harness`` package, whose eager imports cost far more than the
whole worker startup budget. It therefore depends on the standard library and
``wasmtime`` only; ``-I`` keeps the script directory and user site out of
``sys.path``.

Modes:

``selftest``
    Reads one JSON line ``{"runtime_dir": str, "cache_dir": str}`` from
    stdin, checks that ``wasmtime`` imports, that ``python.wasm`` matches the
    installed manifest, that the compiled ``.cwasm`` cache deserializes under
    the exact engine configuration (recompiling on any mismatch), and that the
    module instantiates. It writes exactly one JSON line to stdout and never
    runs the guest's ``_start``, so no script runs anywhere.

``run <status fd> <request JSON>``
    Runs one script in a fresh guest: ``python -I -S -B -c <prelude>``
    (:mod:`prelude`, read from this directory) under CPython-on-WASI. The
    request is ``{"runtime_dir", "cache_dir", "memory_bytes", "cpu_seconds"}``.
    The guest gets this process's stdin, stdout and stderr, which carry the
    script protocol described in :mod:`prelude`; the worker itself never
    reads or writes them. The guest's environment is set explicitly (empty),
    its argv is fixed, and its only preopen is the runtime's stdlib, read-only
    at ``/lib``. There is no writable preopen. Guest memory is capped by the
    ``Store`` limit; ``cpu_seconds`` is an epoch-deadline backstop (10 ms
    ticks, counted in wall time, so time blocked on the host counts too).

    The worker reports its own status on the private fd, which the guest
    cannot reach (WASI only exposes the preopens and stdio): one
    ``{"type": "ready", "backend", "wasmtime", "runtime_sha256"}`` line just
    before the script starts, then ``{"type": "exit", "reason", "code"?,
    "detail"?}`` with ``reason`` one of ``ok`` (the guest exited with status
    0), ``trap-interrupt`` (the CPU backstop fired), ``trap-stack`` (wasm stack
    exhausted) or ``error`` (setup failed, the guest exited non-zero, or any
    other trap). A setup failure sends ``exit`` without ``ready``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import stat
import sys
import tempfile
import threading
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import wasmtime

BACKEND = "wasi"
PRELUDE_PATH = Path(__file__).with_name("prelude.py")
GUEST_STDLIB = "/lib"
EPOCH_TICK_SECONDS = 0.01
WASM32_MEMORY_MAX = 4 * 1024**3
CPU_SECONDS_MAX = 24 * 3600.0
MANIFEST_NAME = "manifest.json"
WASM_NAME = "python.wasm"

# Every engine setting that changes generated code or its host contract. The
# settings are part of the compiled-cache key, so changing one here forces a
# recompile instead of deserializing code built for another configuration.
ENGINE_SETTINGS: dict[str, Any] = {"epoch_interruption": True}


def build_engine() -> wasmtime.Engine:
    import wasmtime

    config = wasmtime.Config()
    config.epoch_interruption = ENGINE_SETTINGS["epoch_interruption"]
    return wasmtime.Engine(config)


def wasmtime_version() -> str:
    return metadata.version("wasmtime")


def cache_key(wasm_sha256: str, version: str) -> dict[str, Any]:
    """Return everything a compiled module depends on."""

    return {
        "wasmtime": version,
        "system": sys.platform,
        "machine": platform.machine(),
        "engine": ENGINE_SETTINGS,
        "python_wasm_sha256": wasm_sha256,
    }


def cache_stem(key: dict[str, Any]) -> str:
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
    return "python-" + hashlib.sha256(encoded).hexdigest()[:32]


class WorkerError(Exception):
    """A self-test or setup failure with a one-line reason."""


def read_runtime(runtime_dir: Path) -> tuple[str, bytes, str]:
    """Return ``(runtime_sha256, python.wasm bytes, their sha256)``.

    The bytes are hashed and returned together, so the module that gets
    compiled is exactly the one checked against the manifest.
    """

    try:
        manifest = json.loads(_read_regular(runtime_dir / MANIFEST_NAME))
        runtime_sha256 = manifest["runtime_sha256"]
        expected = manifest["files"][WASM_NAME]
        wasm = _read_regular(runtime_dir / WASM_NAME)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkerError(f"cannot read installed runtime: {exc}") from exc
    digest = hashlib.sha256(wasm).hexdigest()
    if not isinstance(runtime_sha256, str) or digest != expected:
        raise WorkerError("python.wasm does not match the installed runtime manifest")
    return runtime_sha256, wasm, digest


def _read_regular(path: Path) -> bytes:
    """Read a regular file without following a symlink or blocking on a FIFO."""

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        return handle.read()


def _try_cached(
    engine: wasmtime.Engine, cwasm: Path, sidecar: Path, key: dict[str, Any]
) -> wasmtime.Module | None:
    import wasmtime

    try:
        record = json.loads(_read_regular(sidecar))
        data = _read_regular(cwasm)
    except (OSError, ValueError):
        return None
    # A .cwasm file is native code. Deserialize only the exact bytes that were
    # hashed, and only when the compiling worker recorded both that hash and
    # the complete key this runtime, wasmtime and engine expect.
    if (
        not isinstance(record, dict)
        or record.get("key") != key
        or record.get("sha256") != hashlib.sha256(data).hexdigest()
    ):
        return None
    try:
        return wasmtime.Module.deserialize(engine, data)
    except wasmtime.WasmtimeError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_module(
    engine: wasmtime.Engine, runtime_dir: Path, cache_dir: Path
) -> tuple[wasmtime.Module, str, str]:
    """Return ``(module, runtime_sha256, cache_state)``.

    ``cache_state`` is ``"hit"`` when the cached module deserialized,
    ``"compiled"`` when there was none and ``"recompiled"`` when a cached file
    existed but was stale, corrupt or built for another engine. A stale cache is
    never used; if the fresh module cannot be cached, this raises.
    """

    import wasmtime

    runtime_sha256, wasm, wasm_sha256 = read_runtime(runtime_dir)
    key = cache_key(wasm_sha256, wasmtime_version())
    stem = cache_stem(key)
    cwasm = cache_dir / f"{stem}.cwasm"
    sidecar = cache_dir / f"{stem}.json"
    module = _try_cached(engine, cwasm, sidecar, key)
    if module is not None:
        return module, runtime_sha256, "hit"
    state = "recompiled" if cwasm.exists() or sidecar.exists() else "compiled"
    try:
        module = wasmtime.Module(engine, wasm)
    except wasmtime.WasmtimeError as exc:
        raise WorkerError(f"cannot compile python.wasm: {_first_line(exc)}") from exc
    data = bytes(module.serialize())
    try:
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_atomic(cwasm, data)
        record = {"sha256": hashlib.sha256(data).hexdigest(), "key": key}
        _write_atomic(sidecar, json.dumps(record, sort_keys=True).encode())
    except OSError as exc:
        raise WorkerError(f"cannot write the compiled module cache: {exc}") from exc
    return module, runtime_sha256, state


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def selftest(request: dict[str, Any]) -> dict[str, Any]:
    try:
        import wasmtime
    except Exception as exc:  # noqa: BLE001 - a broken optional wheel is a reported reason
        raise WorkerError(f"import wasmtime failed: {_first_line(exc)}") from exc
    try:
        runtime_dir = Path(request["runtime_dir"])
        cache_dir = Path(request["cache_dir"])
    except (KeyError, TypeError) as exc:
        raise WorkerError("malformed selftest request") from exc
    version = wasmtime_version()
    engine = build_engine()
    module, runtime_sha256, cache_state = load_module(engine, runtime_dir, cache_dir)
    # Instantiate without calling _start: proves the JIT code maps and runs its
    # setup here (an outer sandbox may forbid that), without running Python.
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    store.set_wasi(wasmtime.WasiConfig())
    store.set_epoch_deadline(1)
    try:
        linker.instantiate(store, module)
    except (wasmtime.WasmtimeError, wasmtime.Trap) as exc:
        raise WorkerError(
            f"cannot instantiate python.wasm: {_first_line(exc)}"
        ) from exc
    return {
        "type": "selftest",
        "ok": True,
        "backend": BACKEND,
        "wasmtime": version,
        "runtime_sha256": runtime_sha256,
        "cache": cache_state,
    }


@dataclass(frozen=True)
class RunConfig:
    runtime_dir: Path
    cache_dir: Path
    memory_bytes: int
    cpu_seconds: float

    @classmethod
    def from_request(cls, request: object) -> RunConfig:
        if not isinstance(request, dict):
            raise WorkerError("malformed run request")
        try:
            config = cls(
                runtime_dir=Path(request["runtime_dir"]),
                cache_dir=Path(request["cache_dir"]),
                memory_bytes=request["memory_bytes"],
                cpu_seconds=request["cpu_seconds"],
            )
        except (KeyError, TypeError) as exc:
            raise WorkerError("malformed run request") from exc
        if (
            not _positive_number(config.memory_bytes)
            or not isinstance(config.memory_bytes, int)
            or config.memory_bytes > WASM32_MEMORY_MAX
            or not _positive_number(config.cpu_seconds)
            or config.cpu_seconds > CPU_SECONDS_MAX
        ):
            raise WorkerError("malformed run request")
        return config


def _positive_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


class StatusChannel:
    """The worker's private status fd; the guest cannot reach it."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def send(self, message: dict[str, Any]) -> None:
        data = memoryview((json.dumps(message) + "\n").encode())
        try:
            while data:
                data = data[os.write(self._fd, data) :]
        except OSError:
            pass  # the host went away; it reaps us by process group

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def guest_wasi_config(config: RunConfig) -> wasmtime.WasiConfig:
    """Return the guest's whole WASI world: fixed argv, empty env, stdio, /lib."""

    import wasmtime

    try:
        prelude = _read_regular(PRELUDE_PATH).decode()
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkerError(f"cannot read the guest prelude: {exc}") from exc
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-I", "-S", "-B", "-c", prelude]
    wasi.env = []
    wasi.inherit_stdin()
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    wasi.preopen_dir(str(config.runtime_dir / "lib"), GUEST_STDLIB, False)
    return wasi


class _EpochTicker:
    def __init__(self, engine: wasmtime.Engine) -> None:
        self._engine = engine
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(EPOCH_TICK_SECONDS):
            self._engine.increment_epoch()

    def __enter__(self) -> _EpochTicker:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()


def _prepare(
    config: RunConfig,
) -> tuple[wasmtime.Engine, wasmtime.Store, wasmtime.Func, str]:
    import wasmtime

    engine = build_engine()
    module, runtime_sha256, _cache_state = load_module(
        engine, config.runtime_dir, config.cache_dir
    )
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=config.memory_bytes)
    store.set_wasi(guest_wasi_config(config))
    store.set_epoch_deadline(_ticks(config.cpu_seconds))
    try:
        instance = linker.instantiate(store, module)
        start = instance.exports(store)["_start"]
    except (wasmtime.WasmtimeError, wasmtime.Trap, KeyError) as exc:
        raise WorkerError(
            f"cannot instantiate python.wasm: {_first_line(exc)}"
        ) from exc
    if not isinstance(start, wasmtime.Func):
        raise WorkerError("python.wasm exports no _start function")
    return engine, store, start, runtime_sha256


def _ticks(seconds: float) -> int:
    return max(1, int(seconds / EPOCH_TICK_SECONDS + 0.999))


def _run_guest(
    engine: wasmtime.Engine,
    store: wasmtime.Store,
    start: wasmtime.Func,
    cpu_seconds: float,
) -> dict[str, Any]:
    import wasmtime

    with _EpochTicker(engine):
        # The deadline counts from here, not from setup.
        store.set_epoch_deadline(_ticks(cpu_seconds))
        try:
            start(store)
        except wasmtime.ExitTrap as exc:
            if exc.code == 0:
                return {"type": "exit", "reason": "ok", "code": 0}
            return {"type": "exit", "reason": "error", "code": exc.code}
        except wasmtime.Trap as exc:
            return {
                "type": "exit",
                "reason": _trap_reason(exc),
                "detail": _last_line(exc),
            }
        except wasmtime.WasmtimeError as exc:
            return {"type": "exit", "reason": "error", "detail": _last_line(exc)}
    return {"type": "exit", "reason": "ok", "code": 0}


def _trap_reason(trap: wasmtime.Trap) -> str:
    import wasmtime

    code = trap.trap_code
    if code == wasmtime.TrapCode.INTERRUPT:
        return "trap-interrupt"
    if code == wasmtime.TrapCode.STACK_OVERFLOW:
        return "trap-stack"
    return "error"


def _last_line(exc: BaseException) -> str:
    lines = [line for line in str(exc).splitlines() if line.strip()]
    return lines[-1].strip()[:300] if lines else type(exc).__name__


def run(config_json: str, status: StatusChannel) -> int:
    """Run one script; report ``ready`` and ``exit`` on ``status``."""

    try:
        try:
            import wasmtime  # noqa: F401 - availability is part of setup
        except Exception as exc:  # noqa: BLE001 - a broken optional wheel is a reported reason
            raise WorkerError(f"import wasmtime failed: {_first_line(exc)}") from exc
        try:
            request = json.loads(config_json)
        except ValueError as exc:
            raise WorkerError("malformed run request") from exc
        config = RunConfig.from_request(request)
        engine, store, start, runtime_sha256 = _prepare(config)
    except WorkerError as exc:
        status.send({"type": "exit", "reason": "error", "detail": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - every setup failure is reported on the status fd
        status.send({"type": "exit", "reason": "error", "detail": _last_line(exc)})
        return 1
    status.send(
        {
            "type": "ready",
            "backend": BACKEND,
            "wasmtime": wasmtime_version(),
            "runtime_sha256": runtime_sha256,
        }
    )
    try:
        outcome = _run_guest(engine, store, start, config.cpu_seconds)
    except Exception as exc:  # noqa: BLE001 - a host-side failure is still an exit report
        outcome = {"type": "exit", "reason": "error", "detail": _last_line(exc)}
    status.send(outcome)
    return 0 if outcome["reason"] == "ok" else 1


def _status_fd(text: str) -> int | None:
    try:
        fd = int(text)
        os.fstat(fd)
    except (ValueError, OSError):
        return None
    return fd if fd > 2 else None


def _main_selftest() -> int:
    try:
        request = json.loads(sys.stdin.readline())
        if not isinstance(request, dict):
            raise WorkerError("malformed selftest request")
        reply = selftest(request)
    except WorkerError as exc:
        reply = {"type": "selftest", "ok": False, "reason": str(exc)}
    except ValueError:
        reply = {
            "type": "selftest",
            "ok": False,
            "reason": "malformed selftest request",
        }
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
    return 0 if reply["ok"] else 1


def main(argv: list[str]) -> int:
    if argv[1:] == ["selftest"]:
        return _main_selftest()
    if len(argv) == 4 and argv[1] == "run":
        fd = _status_fd(argv[2])
        if fd is not None:
            status = StatusChannel(fd)
            try:
                return run(argv[3], status)
            finally:
                status.close()
    sys.stderr.write(
        "usage: worker.py selftest | worker.py run STATUS_FD REQUEST_JSON\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
