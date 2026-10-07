"""CM1 T1: codemode runtime provisioning and the fail-closed availability check."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from pipy_harness.native.codemode import (
    RUNTIME_PIN,
    CodemodePaths,
    CodemodeRuntimeError,
    RuntimePin,
    availability,
    default_paths,
    install_runtime,
    verify_runtime,
)
from pipy_harness.native.codemode import cli as codemode_cli
from pipy_harness.native.codemode import selftest as selftest_module

CODEMODE_SRC = Path(selftest_module.__file__).parent


def _zip_bytes(members: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("lib/", b"")
        for name, body in members.items():
            bundle.writestr(name, body)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def _fake_runtime(
    tmp_path: Path, *, extra: dict[str, bytes] | None = None, symlink: str | None = None
) -> tuple[Path, RuntimePin]:
    members = {
        "LICENSE": b"license",
        "python.wasm": b"\0asm fake",
        "lib/python3.14/os.py": b"# os",
        **(extra or {}),
    }
    data = _zip_bytes(members, symlink=symlink)
    archive = tmp_path / "py.zip"
    archive.write_bytes(data)
    pin = RuntimePin(
        version="3.14.7",
        url="https://example.invalid/py.zip",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )
    return archive, pin


def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("availability must not start a process here")

    monkeypatch.setattr(subprocess, "Popen", refuse)


# --- install -----------------------------------------------------------------


def test_install_from_file_unpacks_verifies_and_is_idempotent(tmp_path: Path) -> None:
    archive, pin = _fake_runtime(tmp_path)
    paths = CodemodePaths(tmp_path / "codemode")

    first = install_runtime(paths, source=archive, pin=pin)
    second = install_runtime(paths, source=archive, pin=pin)

    assert first.status == "installed"
    assert second.status == "already-installed"
    assert first.runtime_dir == paths.runtime_dir(pin)
    manifest = json.loads((first.runtime_dir / "manifest.json").read_text())
    assert manifest["runtime_sha256"] == pin.sha256
    assert set(manifest["files"]) == {"LICENSE", "python.wasm", "lib/python3.14/os.py"}
    assert verify_runtime(paths, pin) == first.runtime_dir
    assert [
        p.name for p in paths.root.iterdir() if p.name.startswith(".install-")
    ] == []


def test_force_reinstall_replaces_a_drifted_tree(tmp_path: Path) -> None:
    archive, pin = _fake_runtime(tmp_path)
    paths = CodemodePaths(tmp_path / "codemode")
    runtime_dir = install_runtime(paths, source=archive, pin=pin).runtime_dir
    (runtime_dir / "lib" / "python3.14" / "planted.py").write_text("x")

    result = install_runtime(paths, source=archive, pin=pin)

    assert result.status == "installed"
    assert not (runtime_dir / "lib" / "python3.14" / "planted.py").exists()


def test_install_rejects_hash_mismatch_and_leaves_nothing(tmp_path: Path) -> None:
    archive, pin = _fake_runtime(tmp_path)
    wrong = RuntimePin(pin.version, pin.url, "0" * 64, pin.size)
    paths = CodemodePaths(tmp_path / "codemode")

    with pytest.raises(CodemodeRuntimeError, match="does not match the pinned sha256"):
        install_runtime(paths, source=archive, pin=wrong)

    assert not paths.runtime_dir(wrong).exists()
    assert list(paths.root.iterdir()) == []


def test_install_stops_reading_a_download_larger_than_the_pin(tmp_path: Path) -> None:
    archive, pin = _fake_runtime(tmp_path)
    paths = CodemodePaths(tmp_path / "codemode")
    oversized = io.BytesIO(archive.read_bytes() + b"x" * (2 << 20))
    requested: list[str] = []

    def fetch(url: str) -> io.BytesIO:
        requested.append(url)
        return oversized

    with pytest.raises(CodemodeRuntimeError, match="larger than the pinned"):
        install_runtime(paths, pin=pin, fetch=fetch)

    assert requested == [pin.url]
    assert not paths.runtime_dir(pin).exists()


def test_install_downloads_through_the_injected_fetcher(tmp_path: Path) -> None:
    archive, pin = _fake_runtime(tmp_path)
    paths = CodemodePaths(tmp_path / "codemode")

    result = install_runtime(
        paths, pin=pin, fetch=lambda url: io.BytesIO(archive.read_bytes())
    )

    assert result.status == "installed"


@pytest.mark.parametrize(
    ("extra", "symlink"),
    [
        ({"../escape.py": b"x"}, None),
        ({"/abs.py": b"x"}, None),
        ({"other/thing": b"x"}, None),
        ({"lib\\win.py": b"x"}, None),
        ({}, "lib/python3.14/link.py"),
    ],
)
def test_install_refuses_unsafe_archive_members(
    tmp_path: Path, extra: dict[str, bytes], symlink: str | None
) -> None:
    archive, pin = _fake_runtime(tmp_path, extra=extra, symlink=symlink)
    paths = CodemodePaths(tmp_path / "codemode")

    with pytest.raises(CodemodeRuntimeError, match="unsafe member"):
        install_runtime(paths, source=archive, pin=pin)

    assert not paths.runtime_dir(pin).exists()
    assert not (tmp_path / "escape.py").exists()


def test_the_pin_names_the_github_release_asset() -> None:
    assert RUNTIME_PIN.url == (
        "https://github.com/brettcannon/cpython-wasi-build/releases/download/"
        "v3.14.7/python-3.14.7-wasi_sdk-24.zip"
    )
    assert (
        RUNTIME_PIN.sha256
        == "2e064d3fb8172471d39d741348efa722349c40b96301f69968dff714999c584b"
    )
    assert default_paths(Path("/h")).root == Path("/h/.local/state/pipy/codemode")


# --- verify / availability without a real runtime ------------------------------


def _installed(tmp_path: Path) -> tuple[CodemodePaths, RuntimePin, Path]:
    archive, pin = _fake_runtime(tmp_path)
    paths = CodemodePaths(tmp_path / "codemode")
    return paths, pin, install_runtime(paths, source=archive, pin=pin).runtime_dir


def _symlink_manifest(runtime_dir: Path) -> None:
    manifest = runtime_dir / "manifest.json"
    elsewhere = runtime_dir.parent / "elsewhere.json"
    manifest.rename(elsewhere)
    manifest.symlink_to(elsewhere)
    assert manifest.is_symlink() and elsewhere.is_file()


def _fifo_manifest(runtime_dir: Path) -> None:
    manifest = runtime_dir / "manifest.json"
    manifest.unlink()
    os.mkfifo(manifest)
    assert stat.S_ISFIFO(manifest.lstat().st_mode)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda d: (d / "python.wasm").write_bytes(b"evil"),
            "python.wasm was modified",
        ),
        (lambda d: (d / "lib/python3.14/os.py").unlink(), "os.py is missing"),
        (
            lambda d: (d / "lib/python3.14/sitecustomize.py").write_text("x"),
            "sitecustomize.py is unexpected",
        ),
        (
            lambda d: (
                (d / "lib/python3.14/os.py").unlink()
                or (d / "lib/python3.14/os.py").symlink_to("/etc/hosts")
            ),
            "os.py was modified",
        ),
        (
            lambda d: (d / "manifest.json").write_text("{"),
            "manifest is missing or unreadable",
        ),
        (
            _symlink_manifest,
            "manifest is missing or unreadable",
        ),
        (
            _fifo_manifest,
            "manifest is missing or unreadable",
        ),
    ],
)
def test_drifted_runtime_is_unavailable_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: object, expected: str
) -> None:
    paths, pin, runtime_dir = _installed(tmp_path)
    mutate(runtime_dir)  # type: ignore[operator]
    _no_spawn(monkeypatch)

    result = availability(paths, pin=pin, find_spec=lambda name: object())

    assert result.available is False
    assert expected in (result.reason or "")


def test_runtime_installed_for_another_pin_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, pin, runtime_dir = _installed(tmp_path)
    manifest = json.loads((runtime_dir / "manifest.json").read_text())
    manifest["runtime_sha256"] = "f" * 64
    (runtime_dir / "manifest.json").write_text(json.dumps(manifest))
    _no_spawn(monkeypatch)

    result = availability(paths, pin=pin, find_spec=lambda name: object())

    assert result.available is False
    assert "does not match the pinned sha256" in (result.reason or "")


def test_missing_wasmtime_wheel_is_unavailable_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spawn(monkeypatch)

    result = availability(CodemodePaths(tmp_path), find_spec=lambda name: None)

    assert result.available is False
    assert "wasmtime package is not installed" in (result.reason or "")
    assert "'codemode' extra" in (result.reason or "")


def test_missing_runtime_is_unavailable_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spawn(monkeypatch)

    result = availability(CodemodePaths(tmp_path), find_spec=lambda name: object())

    assert result.available is False
    assert "runtime is not installed" in (result.reason or "")
    assert "python -m pipy_harness.native.codemode install" in (result.reason or "")


def _with_fake_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    worker = tmp_path / "fake_worker.py"
    worker.write_text(textwrap.dedent(body))
    monkeypatch.setattr(selftest_module, "WORKER_PATH", worker)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "import sys; sys.stdin.readline(); print('not json'); sys.exit(3)",
            "exited with status 3 and no report",
        ),
        ("import sys; sys.stderr.write('boom\\n'); sys.exit(1)", "no report: boom"),
        (
            "import json, sys; sys.stdin.readline(); print(json.dumps({'type': 'selftest', 'ok': False, 'reason': 'x'}))",
            "self-test failed: x",
        ),
        (
            "import json, sys; print(json.dumps({'type': 'selftest', 'ok': True, 'backend': 'none', 'runtime_sha256': SHA}))",
            "unexpected backend 'none'",
        ),
        (
            "import json, sys; print(json.dumps({'type': 'selftest', 'ok': True, 'backend': 'wasi', 'runtime_sha256': 'x'}))",
            "does not match the pinned sha256",
        ),
        (
            "import json, sys; print(json.dumps({'type': 'selftest', 'ok': True, 'backend': 'wasi', 'runtime_sha256': SHA})); sys.exit(42)",
            "reported success but exited with status 42",
        ),
    ],
)
def test_bad_worker_reports_make_codemode_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, expected: str
) -> None:
    paths, pin, _ = _installed(tmp_path)
    _with_fake_worker(monkeypatch, tmp_path, f"SHA = {pin.sha256!r}\n" + body)

    result = availability(paths, pin=pin, find_spec=lambda name: object())

    assert result.available is False
    assert expected in (result.reason or "")


def test_hung_worker_times_out_and_its_group_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, pin, _ = _installed(tmp_path)
    pid_file = tmp_path / "pid"
    _with_fake_worker(
        monkeypatch,
        tmp_path,
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )

    result = availability(paths, pin=pin, timeout=1.0, find_spec=lambda name: object())

    assert result.available is False
    assert "timed out" in (result.reason or "")
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_worker_gets_an_isolated_interpreter_and_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, pin, _ = _installed(tmp_path)
    monkeypatch.setenv("PIPY_SECRET_TOKEN", "do-not-leak")
    _with_fake_worker(
        monkeypatch,
        tmp_path,
        """
        import json, os, sys
        request = json.loads(sys.stdin.readline())
        print(json.dumps({"type": "selftest", "ok": False, "reason": json.dumps(
            {"env": sorted(os.environ), "isolated": sys.flags.isolated, "sid": os.getsid(0) == os.getpid(),
             "keys": sorted(request)})}))
        """,
    )

    result = availability(paths, pin=pin, find_spec=lambda name: object())

    seen = json.loads((result.reason or "").removeprefix("self-test failed: "))
    assert [
        k for k in seen["env"] if k not in {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    ] == ["PATH"]
    assert seen["isolated"] == 1
    assert seen["sid"] is True
    assert seen["keys"] == ["cache_dir", "runtime_dir"]


def test_cli_install_rejects_a_mismatched_file_and_status_reports_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bogus = tmp_path / "bogus.zip"
    bogus.write_bytes(_zip_bytes({"python.wasm": b"x"}))

    assert codemode_cli.main(["install", "--from-file", str(bogus)]) == 1
    assert "does not match the pinned sha256" in capsys.readouterr().err
    assert codemode_cli.main(["status"]) == 1
    assert "codemode unavailable" in capsys.readouterr().err


# --- structural invariants --------------------------------------------------------


def test_worker_imports_only_stdlib_and_wasmtime() -> None:
    tree = ast.parse((CODEMODE_SRC / "worker.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add((node.module or "").split(".")[0])
    non_stdlib = {name for name in imported if name not in sys.stdlib_module_names}
    assert non_stdlib == {"wasmtime"}


def test_host_side_never_imports_wasmtime(tmp_path: Path) -> None:
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        import pipy_harness.native.codemode as codemode
        import pipy_harness.native.codemode.cli
        codemode.availability(codemode.CodemodePaths(Path({str(tmp_path)!r})))
        assert "wasmtime" not in sys.modules, "host imported wasmtime"
        """
    )
    subprocess.run([sys.executable, "-I", "-c", script], check=True, timeout=60)


# --- real runtime ---------------------------------------------------------------------

_REAL_RUNTIME = default_paths(Path(pwd.getpwuid(os.getuid()).pw_dir)).runtime_dir()


def _real_runtime_skip_reason() -> str | None:
    if importlib.util.find_spec("wasmtime") is None:
        return "wasmtime is not installed (codemode extra)"
    if not _REAL_RUNTIME.is_dir():
        return f"codemode runtime is not installed at {_REAL_RUNTIME}"
    return None


real_runtime = pytest.mark.skipif(
    _real_runtime_skip_reason() is not None, reason=str(_real_runtime_skip_reason())
)


@pytest.fixture
def real_paths(tmp_path: Path) -> CodemodePaths:
    paths = CodemodePaths(tmp_path / "codemode")
    shutil.copytree(_REAL_RUNTIME, paths.runtime_dir(), symlinks=True)
    verify_runtime(paths)
    return paths


@real_runtime
def test_real_runtime_compiles_once_then_hits_the_cache(
    real_paths: CodemodePaths,
) -> None:
    first = availability(real_paths)
    second = availability(real_paths)

    assert first.available is True, first.reason
    assert first.cache == "compiled"
    assert first.runtime_sha256 == RUNTIME_PIN.sha256
    assert first.wasmtime_version is not None and first.wasmtime_version.startswith(
        "49."
    )
    assert second.available is True and second.cache == "hit"
    assert sorted(p.suffix for p in real_paths.cache_dir.iterdir()) == [
        ".cwasm",
        ".json",
    ]


def _cache_files(paths: CodemodePaths) -> tuple[Path, Path]:
    (cwasm,) = paths.cache_dir.glob("*.cwasm")
    return cwasm, cwasm.with_suffix(".json")


def _stale_cwasm_for_another_engine(paths: CodemodePaths) -> None:
    """Replace the cache with a module built without epoch interruption, with a matching sidecar."""

    import wasmtime

    cwasm, sidecar = _cache_files(paths)
    config = wasmtime.Config()
    config.epoch_interruption = False
    other = wasmtime.Module(
        wasmtime.Engine(config), (paths.runtime_dir() / "python.wasm").read_bytes()
    )
    data = bytes(other.serialize())
    cwasm.write_bytes(data)
    record = json.loads(sidecar.read_text())
    record["sha256"] = hashlib.sha256(data).hexdigest()
    sidecar.write_text(json.dumps(record))


@real_runtime
def test_stale_cwasm_for_another_engine_config_is_recompiled(
    real_paths: CodemodePaths,
) -> None:
    assert availability(real_paths).available
    _stale_cwasm_for_another_engine(real_paths)

    result = availability(real_paths)

    assert result.available is True, result.reason
    assert result.cache == "recompiled"
    assert availability(real_paths).cache == "hit"


@real_runtime
def test_cache_pair_recorded_under_another_key_is_never_deserialized(
    real_paths: CodemodePaths,
) -> None:
    assert availability(real_paths).available
    _, sidecar = _cache_files(real_paths)
    record = json.loads(sidecar.read_text())
    record["key"]["python_wasm_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(record))

    result = availability(real_paths)

    assert result.available is True, result.reason
    assert result.cache == "recompiled"


@real_runtime
def test_cwasm_not_matching_its_sidecar_is_never_deserialized(
    real_paths: CodemodePaths,
) -> None:
    assert availability(real_paths).available
    cwasm, _ = _cache_files(real_paths)
    cwasm.write_bytes(b"\x7fELF not really")

    result = availability(real_paths)

    assert result.available is True, result.reason
    assert result.cache == "recompiled"


@real_runtime
def test_stale_cwasm_that_cannot_be_rebuilt_is_unavailable(
    real_paths: CodemodePaths,
) -> None:
    assert availability(real_paths).available
    _stale_cwasm_for_another_engine(real_paths)
    cwasm, sidecar = _cache_files(real_paths)
    cwasm.chmod(0o400)
    sidecar.chmod(0o400)
    real_paths.cache_dir.chmod(0o500)
    try:
        result = availability(real_paths)
    finally:
        real_paths.cache_dir.chmod(0o700)

    assert result.available is False
    assert "cannot write the compiled module cache" in (result.reason or "")
