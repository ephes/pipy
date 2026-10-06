"""CM1 T2: negative isolation probes for the codemode guest (acceptance H).

These port the WASI red-team vectors from the spike (spike result §2) into
regression tests. Each probe script runs in a real CPython-on-WASI guest via
the minimal test driver and reports, per attempt, either ``OK <value>`` or
``DENIED <exception type>``. The worker gets a private copy of the runtime
with host-planted escape symlinks, a canary secret in its environment and an
extra inherited fd, so a leak of any of them would be visible.

They skip when the ``wasmtime`` wheel or the installed runtime is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from codemode_driver import (
    REAL_RUNTIME,
    WorkerRun,
    group_is_gone,
    real_runtime_skip_reason,
    run_worker,
)

pytestmark = pytest.mark.skipif(
    real_runtime_skip_reason() is not None, reason=str(real_runtime_skip_reason())
)

CANARY = "codemode-canary-7f3a9c"
PROBE_HELPER = (
    "import json, os, sys\n"
    "_results = {}\n"
    "def _t(label, fn):\n"
    "    try:\n"
    "        _results[label] = 'OK ' + repr(fn())[:200]\n"
    "    except BaseException as exc:\n"
    "        _results[label] = 'DENIED ' + type(exc).__name__\n"
)
PROBE_REPORT = "text(json.dumps(_results))\n"


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """A runtime copy with planted escape links, a host secret and a cache."""

    root = tmp_path_factory.mktemp("codemode-isolation")
    secret_dir = root / "secret"
    secret_dir.mkdir()
    secret = secret_dir / "credentials.txt"
    secret.write_text(CANARY)
    runtime = root / "runtime"
    shutil.copytree(REAL_RUNTIME, runtime, symlinks=True)
    stdlib = runtime / "lib" / "python3.14"
    (stdlib / "escape_abs").symlink_to(secret)
    (stdlib / "escape_dir").symlink_to(secret_dir, target_is_directory=True)
    (stdlib / "escape_rel").symlink_to("../../../secret/credentials.txt")
    yield {
        "root": root,
        "runtime": runtime,
        "stdlib": stdlib,
        "secret": secret,
        "cache": root / "cache",
    }


def _probe(sandbox: dict[str, Path], body: str, **kwargs: Any) -> dict[str, str]:
    run = _run(sandbox, PROBE_HELPER + body + PROBE_REPORT, **kwargs)
    assert run.done == {"type": "done", "ok": True}, (run.done, run.stderr)
    assert len(run.texts) == 1, run.texts
    results: dict[str, str] = json.loads(run.texts[0])
    return results


def _run(sandbox: dict[str, Path], code: str, **kwargs: Any) -> WorkerRun:
    return run_worker(
        code,
        runtime_dir=sandbox["runtime"],
        cache_dir=sandbox["cache"],
        **kwargs,
    )


def _denied(results: dict[str, str]) -> dict[str, str]:
    """Return the attempts that were NOT denied (should be empty)."""

    return {label: r for label, r in results.items() if not r.startswith("DENIED")}


def test_host_filesystem_proc_and_dev_do_not_exist(sandbox: dict[str, Path]) -> None:
    host_paths = [
        str(sandbox["secret"]),
        str(sandbox["runtime"]),
        str(Path.home() / ".ssh"),
        str(Path.home() / ".local" / "state" / "pipy"),
        str(Path(__file__).resolve()),
        "/etc/passwd",
        "/proc",
        "/proc/self/environ",
        "/proc/self/maps",
        "/dev",
        "/dev/null",
        "/dev/tty",
        "/dev/fd/0",
        "/tmp",
        "/lib/../../etc/passwd",
        "/lib/../secret/credentials.txt",
    ]
    body = (
        f"for p in {host_paths!r}:\n"
        "    _t('exists ' + p, lambda p=p: os.path.exists(p) or 1 / 0)\n"
        "    _t('open ' + p, lambda p=p: open(p, 'rb').read(1))\n"
        "_t('listdir /', lambda: os.listdir('/'))\n"
        "_t('listdir .', lambda: os.listdir('.'))\n"
        "_t('home', lambda: os.path.exists(os.path.expanduser('~/.ssh')) or 1 / 0)\n"
        "_t('control', lambda: os.path.exists('/lib/python3.14/this.py'))\n"
    )
    results = _probe(sandbox, body)

    assert results.pop("control") == "OK True"
    assert _denied(results) == {}
    assert len(results) == 2 * len(host_paths) + 3


def test_stdlib_preopen_is_read_only(sandbox: dict[str, Path]) -> None:
    stdlib = sandbox["stdlib"]
    before = sorted(p.name for p in stdlib.iterdir())
    this_py = stdlib / "this.py"
    digest = hashlib.sha256(this_py.read_bytes()).hexdigest()
    mode = this_py.stat().st_mode
    body = (
        "L = '/lib/python3.14/'\n"
        "_t('create', lambda: open(L + 'pwned.py', 'w'))\n"
        "_t('append', lambda: open(L + 'this.py', 'a'))\n"
        "_t('truncate', lambda: open(L + 'this.py', 'r+b').truncate(0))\n"
        "_t('mkdir', lambda: os.mkdir(L + 'pwned'))\n"
        "_t('unlink', lambda: os.unlink(L + 'this.py'))\n"
        "_t('rename', lambda: os.rename(L + 'this.py', L + 'that.py'))\n"
        "_t('symlink', lambda: os.symlink('/etc', L + 'pwned_link'))\n"
        "_t('hardlink', lambda: os.link(L + 'this.py', L + 'this2.py'))\n"
        "_t('utime', lambda: os.utime(L + 'this.py', (0, 0)))\n"
        "_t('rmdir', lambda: os.rmdir('/lib/python3.14/json'))\n"
        # WASI has no chmod; CPython's stub returns without doing anything.
        "_t('chmod', lambda: os.chmod(L + 'this.py', 0o777))\n"
    )
    results = _probe(sandbox, body)

    assert results.pop("chmod") == "OK None"
    assert _denied(results) == {}
    assert sorted(p.name for p in stdlib.iterdir()) == before
    assert hashlib.sha256(this_py.read_bytes()).hexdigest() == digest
    assert this_py.stat().st_mode == mode


def test_planted_symlinks_cannot_leave_the_preopen(sandbox: dict[str, Path]) -> None:
    body = (
        "L = '/lib/python3.14/'\n"
        "for name in ('escape_abs', 'escape_rel', 'escape_dir/credentials.txt'):\n"
        "    _t('read ' + name, lambda n=name: open(L + n).read())\n"
        "_t('listdir escape_dir', lambda: os.listdir(L + 'escape_dir'))\n"
        "_t('control', lambda: open(L + 'this.py').read(4))\n"
    )
    stdlib = sandbox["stdlib"]
    assert (stdlib / "escape_abs").read_text() == CANARY  # the host can follow them
    assert (stdlib / "escape_rel").read_text() == CANARY
    results = _probe(sandbox, body)

    assert results.pop("control") == "OK 's = '"
    assert _denied(results) == {}
    assert len(results) == 4


def test_guest_sees_only_stdio_and_the_stdlib_preopen(
    sandbox: dict[str, Path],
) -> None:
    inherited = os.open(sandbox["secret"], os.O_RDONLY)
    try:
        body = (
            "fds = []\n"
            "for fd in range(1024):\n"
            "    try:\n"
            "        os.fstat(fd)\n"
            "        fds.append(fd)\n"
            "    except OSError:\n"
            "        pass\n"
            "_results['fds'] = fds\n"
            f"_t('read inherited fd', lambda: os.read({inherited}, 100))\n"
            "_t('openat .. from preopen', lambda: os.open('..', os.O_RDONLY, dir_fd=3))\n"
            "_t('listdir preopen ..', lambda: os.listdir('/lib/..'))\n"
        )
        results = _probe(sandbox, body, extra_fds=(inherited,))
    finally:
        os.close(inherited)

    assert results.pop("fds") == [0, 1, 2, 3]  # type: ignore[comparison-overlap]
    assert _denied(results) == {}


def test_native_code_loading_is_unavailable(sandbox: dict[str, Path]) -> None:
    body = (
        "for m in ('_ctypes', 'ctypes', 'cffi', '_posixsubprocess',\n"
        "          '_multiprocessing', 'multiprocessing', 'mmap', 'fcntl',\n"
        "          'resource', 'ssl', '_ssl'):\n"
        "    _t('import ' + m, lambda m=m: __import__(m))\n"
        "_t('CDLL', lambda: __import__('ctypes').CDLL(None))\n"
    )
    results = _probe(sandbox, body)

    assert _denied(results) == {}


def test_no_processes_or_threads(sandbox: dict[str, Path]) -> None:
    body = (
        "import subprocess, _thread, threading\n"
        "_t('subprocess.run', lambda: subprocess.run(['/bin/echo', 'x']))\n"
        "_t('Popen', lambda: subprocess.Popen(['/bin/sh']))\n"
        "for name in ('system', 'fork', 'forkpty', 'posix_spawn', 'execv',\n"
        "             'popen', 'spawnv'):\n"
        "    _t('os.' + name, lambda n=name: getattr(os, n)('/bin/sh'))\n"
        "_t('_thread', lambda: _thread.start_new_thread(lambda: None, ()))\n"
        "_t('threading', lambda: threading.Thread(target=lambda: None).start())\n"
    )
    results = _probe(sandbox, body)

    assert _denied(results) == {}
    assert len(results) == 11


def test_no_network_or_dns(sandbox: dict[str, Path]) -> None:
    accepted: list[object] = []
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.2)
    port = server.getsockname()[1]
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                connection, address = server.accept()
            except OSError:
                continue
            accepted.append(address)
            connection.close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        body = (
            "import socket, urllib.request\n"
            "for fam in ('AF_INET', 'AF_INET6', 'AF_UNIX'):\n"
            "    _t('socket ' + fam, lambda f=fam: socket.socket(getattr(socket, f)))\n"
            f"_t('connect', lambda: socket.create_connection(('127.0.0.1', {port}), 1))\n"
            "_t('getaddrinfo', lambda: socket.getaddrinfo('example.com', 443))\n"
            "_t('gethostbyname', lambda: socket.gethostbyname('localhost'))\n"
            "_t('https', lambda: urllib.request.urlopen('https://example.com'))\n"
            f"_t('http', lambda: urllib.request.urlopen('http://127.0.0.1:{port}/'))\n"
        )
        results = _probe(sandbox, body)
    finally:
        stop.set()
        thread.join(timeout=5)
        server.close()

    assert _denied(results) == {}
    assert len(results) == 8
    assert accepted == []


def test_environment_and_credentials_are_invisible(sandbox: dict[str, Path]) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(Path.home()),
        "PIPY_SECRET_TOKEN": CANARY,
    }
    body = (
        "_results['environ'] = dict(os.environ)\n"
        "_results['argv'] = sys.argv\n"
        "_results['paths'] = [sys.executable, sys.prefix, *sys.path]\n"
        "_t('getenv', lambda: os.getenv('PIPY_SECRET_TOKEN') or 1 / 0)\n"
        "_t('getuid', lambda: os.getuid())\n"
        "_t('getlogin', lambda: os.getlogin())\n"
        "_t('pwd', lambda: __import__('pwd'))\n"
    )
    run = _run(sandbox, PROBE_HELPER + body + PROBE_REPORT, env=env)

    assert run.done == {"type": "done", "ok": True}, run.stderr
    results = json.loads(run.texts[0])
    assert results.pop("environ") == {}
    assert results.pop("argv") == ["-c"]
    paths = results.pop("paths")
    assert all(not p or p == "/" or p.startswith("/lib") for p in paths), paths
    assert _denied(results) == {}
    assert CANARY not in json.dumps(run.messages) + run.stderr
    assert str(sandbox["root"]) not in json.dumps(run.messages)


def test_signals_reach_only_the_guest(sandbox: dict[str, Path]) -> None:
    body = (
        "import signal\n"
        "_t('kill parent', lambda: os.kill(os.getppid(), signal.SIGKILL))\n"
        "_t('kill pid 1', lambda: os.kill(1, signal.SIGTERM))\n"
        "_t('killpg', lambda: os.killpg(0, signal.SIGKILL))\n"
        "_t('setsid', lambda: os.setsid())\n"
        "_t('alarm', lambda: signal.alarm(1))\n"
        "_t('setitimer', lambda: signal.setitimer(signal.ITIMER_REAL, 0.1))\n"
    )
    results = _probe(sandbox, body)

    assert _denied(results) == {}

    # raise_signal is wasi-libc's abort: it traps this guest and nothing else.
    run = _run(sandbox, "import signal\nsignal.raise_signal(signal.SIGTERM)\n")
    assert run.done is None
    assert run.exit is not None and run.exit["reason"] == "error"
    assert group_is_gone(run.pid)


def test_a_blocked_guest_is_killed_with_its_whole_group(
    sandbox: dict[str, Path],
) -> None:
    # The guest blocks reading the protocol channel, where the epoch cannot
    # interrupt it; only the host's wall deadline ends it.
    run = _run(
        sandbox,
        "import os\ntext('blocking')\nos.read(0, 1)\n",
        wall_seconds=1.0,
        cpu_seconds=30.0,
    )

    assert run.timed_out and run.texts == ["blocking"]
    assert run.status[0]["type"] == "ready" and run.exit is None
    assert group_is_gone(run.pid)


def test_the_runtime_copy_is_untouched(sandbox: dict[str, Path]) -> None:
    manifest = json.loads((sandbox["runtime"] / "manifest.json").read_text())
    planted = {"escape_abs", "escape_dir", "escape_rel"}
    for relative, expected in manifest["files"].items():
        path = sandbox["runtime"] / relative
        assert stat.S_ISREG(path.lstat().st_mode), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
    on_disk = {
        p.relative_to(sandbox["runtime"]).as_posix()
        for p in sandbox["runtime"].rglob("*")
        if not p.is_dir() or p.is_symlink()
    }
    extra = on_disk - set(manifest["files"]) - {"manifest.json"}
    assert {Path(p).name for p in extra} == planted
