"""The fake MCP server does not outlive a test run: the guard that fails a
module which leaves one behind, and the server's own end as an orphan."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp_fake_server_guard import end_fake_servers, fake_servers

_TESTS = Path(__file__).parent
_FAKE_SERVER = _TESTS / "fixtures" / "mcp_fake_server.py"
_WAIT_SECONDS = 10.0
# Ignores everything a client does to end it.
_STUBBORN: dict[str, object] = {"onStdinClose": [], "onSigterm": []}


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_gone(pid: int) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while _is_alive(pid):
        assert time.monotonic() < deadline, f"process {pid} still runs"
        time.sleep(0.02)


def _write_scenario(directory: Path, scenario: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


@pytest.fixture
def stubborn_server(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    """A running server that ignores stdin and SIGTERM, and its ``spawn``
    child, which ignores SIGTERM too."""

    scenario = _write_scenario(
        tmp_path / "run",
        {**_STUBBORN, "startup": [{"spawn": {"ignoreSigterm": True}}]},
    )
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", str(_FAKE_SERVER), "--scenario", str(scenario)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    child = 0
    try:
        assert process.stderr is not None
        child = int(process.stderr.readline().split()[1])
        yield process, child
    finally:
        for pid in (process.pid, child):
            if pid and _is_alive(pid):
                os.kill(pid, signal.SIGKILL)
        process.wait(timeout=_WAIT_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            assert stream is not None
            stream.close()


def test_finds_a_server_and_its_child_by_the_directory_of_the_scenario(
    stubborn_server: tuple[subprocess.Popen[bytes], int], tmp_path: Path
) -> None:
    process, child = stubborn_server

    assert set(fake_servers(tmp_path)) == {process.pid, child}
    assert set(fake_servers(tmp_path / "run")) == {process.pid, child}
    assert str(tmp_path / "run" / "scenario.json") in fake_servers(tmp_path)[child]
    # A directory whose name only starts the same is another directory.
    assert fake_servers(tmp_path / "ru") == {}
    assert fake_servers(tmp_path / "other") == {}


def test_ends_the_servers_that_do_not_end_by_themselves(
    stubborn_server: tuple[subprocess.Popen[bytes], int], tmp_path: Path
) -> None:
    process, child = stubborn_server

    assert set(end_fake_servers(tmp_path)) == {process.pid, child}

    assert process.wait(timeout=_WAIT_SECONDS) == -signal.SIGKILL
    _wait_until_gone(child)
    assert end_fake_servers(tmp_path) == {}


def test_a_server_that_is_ending_is_waited_for_and_not_reported(
    tmp_path: Path,
) -> None:
    scenario = _write_scenario(
        tmp_path, {"onStdinClose": [{"sleep": 0.5}, {"exit": 0}]}
    )
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", str(_FAKE_SERVER), "--scenario", str(scenario)],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.close()
    assert process.pid in fake_servers(tmp_path)

    assert end_fake_servers(tmp_path) == {}

    assert process.wait(timeout=_WAIT_SECONDS) == 0


_LEAKY_MODULE = """
import json, os, subprocess, sys
from pathlib import Path


def _start(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"onStdinClose": [], "onSigterm": []}))
    return subprocess.Popen(
        [sys.executable, "-I", os.environ["FAKE_SERVER"], "--scenario", str(scenario)],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def test_leaves_its_server_running(tmp_path):
    process = _start(tmp_path)
    Path(os.environ["PID_FILE"]).write_text(str(process.pid))


def test_ends_its_server(tmp_path):
    process = _start(tmp_path)
    process.kill()
    process.wait()
"""


def test_a_module_that_leaves_a_server_running_fails_and_the_server_is_ended(
    tmp_path: Path,
) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_leaky.py").write_text(_LEAKY_MODULE, encoding="utf-8")
    pid_file = tmp_path / "pid"
    env = {
        **os.environ,
        "PYTHONPATH": str(_TESTS),
        "FAKE_SERVER": str(_FAKE_SERVER),
        "PID_FILE": str(pid_file),
    }

    run = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "mcp_fake_server_guard",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "base"),
            "test_leaky.py",
        ],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    pid = int(pid_file.read_text())
    try:
        assert run.returncode == 1, run.stdout + run.stderr
        assert "2 passed, 1 error" in run.stdout
        assert "left fake MCP server processes running" in run.stdout
        (reported,) = [
            line for line in run.stdout.splitlines() if line.startswith(f"  {pid}: ")
        ]
        # The directory in the command line names the test.
        assert "test_leaves_its_server_running" in reported
        _wait_until_gone(pid)
    finally:
        if _is_alive(pid):
            os.kill(pid, signal.SIGKILL)


_START_AND_DIE = """
import os, subprocess, sys
server = subprocess.Popen(
    [sys.executable, *sys.argv[1:]],
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    start_new_session=True,
)
child = int(server.stderr.readline().split()[1])
print(server.pid, child, flush=True)
os._exit(0)
"""


def test_an_orphaned_server_and_its_child_end_themselves(tmp_path: Path) -> None:
    record = tmp_path / "record.jsonl"
    scenario = _write_scenario(
        tmp_path,
        {
            **_STUBBORN,
            "record": str(record),
            "orphanGraceSeconds": 0.3,
            "startup": [{"spawn": {"ignoreSigterm": True}}],
        },
    )

    host = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _START_AND_DIE,
            "-I",
            str(_FAKE_SERVER),
            "--scenario",
            str(scenario),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert host.returncode == 0, host.stderr
    server, child = (int(pid) for pid in host.stdout.split())
    try:
        _wait_until_gone(server)
        _wait_until_gone(child)
    finally:
        for pid in (server, child):
            if _is_alive(pid):
                os.kill(pid, signal.SIGKILL)
    events = [json.loads(line).get("event") for line in record.read_text().splitlines()]
    # It ignored the end of its stdin and went on its own.
    assert events == ["stdin-closed", "orphaned"]


_DIE_BEFORE_THE_SERVER_STARTS = """
import os, sys, time
parent = os.getpid()
read, write = os.pipe()
if os.fork() == 0:
    os.setsid()
    os.close(write)
    os.read(read, 1)  # Ends when the parent is gone.
    while os.getppid() == parent:
        time.sleep(0.01)
    null = os.open(os.devnull, os.O_RDWR)
    for descriptor in (1, 2):
        os.dup2(null, descriptor)
    # Stdin is the pipe whose other end went with the parent.
    os.dup2(read, 0)
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
os._exit(0)
"""


def test_a_server_whose_parent_ended_before_it_started_ends_itself(
    tmp_path: Path,
) -> None:
    record = tmp_path / "record.jsonl"
    scenario = _write_scenario(
        tmp_path,
        {**_STUBBORN, "record": str(record), "orphanGraceSeconds": 0.3},
    )

    host = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _DIE_BEFORE_THE_SERVER_STARTS,
            "-I",
            str(_FAKE_SERVER),
            "--scenario",
            str(scenario),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert host.returncode == 0, host.stderr
    try:
        deadline = time.monotonic() + _WAIT_SECONDS
        while not (record.exists() and "orphaned" in record.read_text()):
            assert time.monotonic() < deadline, "the server did not end itself"
            time.sleep(0.02)
        deadline = time.monotonic() + _WAIT_SECONDS
        while fake_servers(tmp_path):
            assert time.monotonic() < deadline, "the server still runs"
            time.sleep(0.02)
    finally:
        end_fake_servers(tmp_path)


def test_the_end_of_stdin_starts_the_orphan_time(tmp_path: Path) -> None:
    record = tmp_path / "record.jsonl"
    scenario = _write_scenario(
        tmp_path,
        {**_STUBBORN, "record": str(record), "orphanGraceSeconds": 0.3},
    )
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", str(_FAKE_SERVER), "--scenario", str(scenario)],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        # With a living parent and an open stdin it stays. Not so when this
        # process is pid 1 (a container): the server takes that for a parent
        # that ended, and its time runs from the start.
        if os.getpid() != 1:
            time.sleep(0.8)
            assert process.poll() is None
        assert process.stdin is not None
        process.stdin.close()

        assert process.wait(timeout=_WAIT_SECONDS) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=_WAIT_SECONDS)
    events = [json.loads(line).get("event") for line in record.read_text().splitlines()]
    assert events[-1] == "orphaned"
    if os.getpid() != 1:
        assert events == ["stdin-closed", "orphaned"]
