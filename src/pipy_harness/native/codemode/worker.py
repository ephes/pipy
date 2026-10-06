"""Codemode worker: the only pipy code that imports ``wasmtime``.

The host never imports this module. It runs it as a standalone script,
``<pipy's interpreter> -I <path to this file> <mode>``, in a fresh process
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

The run mode (T2) reuses :func:`build_engine` and :func:`load_module`.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import wasmtime

BACKEND = "wasi"
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


def main(argv: list[str]) -> int:
    if argv[1:] != ["selftest"]:
        sys.stderr.write("usage: worker.py selftest\n")
        return 2
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
