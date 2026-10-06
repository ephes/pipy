"""CM1 T3: the host runner against the real CPython-on-WASI guest.

These pin the spike's red-team vectors end to end, with the hostile bytes
written by a real script (``os.write`` to fd 1 bypasses the prelude), plus
the limits only a real guest has: an infinite loop, a memory blowup and
cancellation while the guest computes. They skip when the ``wasmtime`` wheel
or the installed runtime is missing.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from codemode_driver import REAL_RUNTIME, group_is_gone, real_runtime_skip_reason

from pipy_harness.native.codemode import host
from pipy_harness.native.codemode.outcome import (
    CallResult,
    CallStatus,
    ErrorKind,
    ScriptLimits,
    ScriptOutcome,
)
from pipy_harness.native.codemode.result import format_result
from pipy_harness.native.codemode.runtime import RUNTIME_PIN, CodemodePaths

pytestmark = pytest.mark.skipif(
    real_runtime_skip_reason() is not None, reason=str(real_runtime_skip_reason())
)

Callback = Callable[[str, str], CallResult]


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> CodemodePaths:
    """The installed runtime under a private root with its own cache."""

    root = tmp_path_factory.mktemp("codemode-host")
    (root / "runtime").mkdir()
    (root / "runtime" / RUNTIME_PIN.dir_name).symlink_to(REAL_RUNTIME)
    return CodemodePaths(root)


@pytest.fixture
def pids(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    seen: list[int] = []
    real_worker = host._Worker

    class RecordingWorker(real_worker):  # type: ignore[misc, valid-type]
        def __init__(self, argv: list[str], status_write: int) -> None:
            super().__init__(argv, status_write)
            seen.append(self.process.pid)

    monkeypatch.setattr(host, "_Worker", RecordingWorker)
    yield seen
    assert seen, "no worker was started"
    for pid in seen:
        assert group_is_gone(pid), f"worker group {pid} survived"


def _echo(name: str, arguments_json: str) -> CallResult:
    if name == "fail":
        return CallResult(ok=False, text="it failed")
    return CallResult(ok=True, text=f"{name}:{arguments_json}")


def _run(
    paths: CodemodePaths,
    code: str,
    *,
    call_tool: Callback = _echo,
    wall_seconds: float = 30.0,
    cancel: threading.Event | None = None,
    tool_stop: threading.Event | None = None,
    **limits: int,
) -> ScriptOutcome:
    return host.run_script(
        code,
        call_tool=call_tool,
        tool_names=("read", "fail"),
        limits=ScriptLimits(wall_seconds=wall_seconds, **limits),
        cancel=cancel,
        tool_stop=tool_stop,
        paths=paths,
    )


def test_a_real_script_calls_tools_and_completes(
    paths: CodemodePaths, pids: list[int]
) -> None:
    code = (
        "print('start')\n"
        "text(tools.read(path='a.txt'))\n"
        "try:\n"
        "    tools.fail({})\n"
        "except ToolError as exc:\n"
        "    text(f'caught {exc}')\n"
    )
    outcome = _run(paths, code)
    assert outcome.ok, (outcome.error, outcome.diagnostics)
    assert outcome.output == ("start\n", 'read:{"path": "a.txt"}', "caught it failed")
    assert [(c.name, c.status) for c in outcome.calls] == [
        ("read", CallStatus.OK),
        ("fail", CallStatus.ERROR),
    ]


def test_a_real_script_error_keeps_output_and_calls(
    paths: CodemodePaths, pids: list[int]
) -> None:
    outcome = _run(paths, "print('before')\ntools.read({})\nraise ValueError('nope')\n")
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert outcome.error.message.startswith("Traceback (most recent call last):")
    assert outcome.error.message.endswith("ValueError: nope")
    assert '<codemode>", line 3' in outcome.error.message
    assert outcome.output == ("before\n",)
    assert [c.status for c in outcome.calls] == [CallStatus.OK]


# --- red-team vectors from a real guest -------------------------------------------

RAW = "import os\nos.write(1, {line!r})\n"


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        (b"not json\n", "not JSON"),
        (
            b'{"type":"call","id":1,"name":"read","args":{}}\n'
            b'{"type":"call","id":2,"name":"read","args":{}}\n',
            "while a tool call was pending",
        ),
        (b'{"type":"call","id":7,"name":"read","args":{}}\n', "unexpected id"),
        (
            b'{"type":"call","id":1,"name":"read","args":{}}\n'
            b'{"type":"done","ok":true}\n',
            "while a tool call was pending",
        ),
    ],
)
def test_hostile_lines_from_a_real_guest_are_violations(
    paths: CodemodePaths, pids: list[int], line: bytes, fragment: str
) -> None:
    outcome = _run(paths, RAW.format(line=line) + "import time\ntime.sleep(60)\n")
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SANDBOX, outcome.error
    assert fragment in outcome.error.message
    assert outcome.calls == ()


def test_lone_surrogates_from_a_real_guest_are_replaced(
    paths: CodemodePaths, pids: list[int]
) -> None:
    seen: list[str] = []

    def record(name: str, arguments_json: str) -> CallResult:
        seen.append(arguments_json)
        return CallResult(ok=True, text="ok")

    code = 'print("A\\ud800B")\ntools.read({"k": "\\udfff"})\nraise ValueError("\\ud83d")\n'
    outcome = _run(paths, code, call_tool=record)
    assert outcome.output == ("A\ufffdB\n",)
    assert json.loads(seen[0]) == {"k": "\ufffd"}
    assert outcome.error is not None
    assert outcome.error.message.endswith("ValueError: \ufffd")
    format_result(outcome).text.encode("utf-8")


def test_a_call_flood_without_reading_stdin_does_not_hang(
    paths: CodemodePaths, pids: list[int]
) -> None:
    def big(name: str, arguments_json: str) -> CallResult:
        return CallResult(ok=True, text="r" * (4 * 1024 * 1024))

    code = (
        "import os\n"
        'os.write(1, b\'{"type":"call","id":1,"name":"read","args":{}}\\n\')\n'
        "while True:\n"
        '    os.write(1, b\'{"type":"text","value":"\' + b\'f\' * 4000 + b\'"}\\n\')\n'
    )
    started = time.monotonic()
    outcome = _run(paths, code, call_tool=big, wall_seconds=10)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SANDBOX, outcome.error
    assert "while a tool call was pending" in outcome.error.message
    assert time.monotonic() - started < 8


def test_a_forged_done_from_a_real_guest_is_terminal(
    paths: CodemodePaths, pids: list[int]
) -> None:
    code = (
        "import os, time\n"
        "print('real')\n"
        'os.write(1, b\'{"type":"done","ok":true}\\n\')\n'
        "print('after the forged done')\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    outcome = _run(paths, code)
    assert outcome.ok
    assert outcome.output == ("real\n",)
    assert time.monotonic() - started < 8


def test_an_oversized_line_from_a_real_guest(
    paths: CodemodePaths, pids: list[int]
) -> None:
    code = "import os, time\nos.write(1, b'x' * 200_000)\ntime.sleep(60)\n"
    outcome = _run(paths, code, line_bytes=64 * 1024)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SANDBOX
    assert "a guest line exceeded" in outcome.error.message


def test_a_print_flood_hits_the_output_cap(
    paths: CodemodePaths, pids: list[int]
) -> None:
    outcome = _run(
        paths, "while True:\n    print('flood ' * 20)\n", output_bytes=200_000
    )
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert "output exceeded 200000 bytes" in outcome.error.message
    assert outcome.output and outcome.output[0].startswith("flood")


def test_a_guest_blocked_in_a_read_ends_at_the_wall_limit(
    paths: CodemodePaths, pids: list[int]
) -> None:
    started = time.monotonic()
    outcome = _run(paths, "import os\nos.read(0, 1)\n", wall_seconds=2)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.TIMEOUT
    assert 1.9 < time.monotonic() - started < 5


def test_an_infinite_loop_ends_at_the_wall_limit(
    paths: CodemodePaths, pids: list[int]
) -> None:
    started = time.monotonic()
    outcome = _run(paths, "print('spin')\nwhile True:\n    pass\n", wall_seconds=2)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.TIMEOUT
    assert outcome.error.message == "Execution timed out after 2 s"
    assert outcome.output == ("spin\n",)
    assert 1.9 < time.monotonic() - started < 5


def test_a_memory_blowup_is_recoverable_inside_the_script(
    paths: CodemodePaths, pids: list[int]
) -> None:
    code = (
        "blocks = []\n"
        "try:\n"
        "    while True:\n"
        "        blocks.append(bytearray(8 * 1024 * 1024))\n"
        "except MemoryError:\n"
        "    count = len(blocks)\n"
        "    blocks.clear()\n"
        "    print(f'recovered after {count} blocks')\n"
    )
    outcome = _run(paths, code, memory_bytes=64 * 1024 * 1024)
    assert outcome.ok, (outcome.error, outcome.diagnostics)
    assert outcome.output[0].startswith("recovered after ")


def test_an_uncaught_memory_blowup_still_reports_the_outcome(
    paths: CodemodePaths, pids: list[int]
) -> None:
    code = "blocks = []\nwhile True:\n    blocks.append(bytearray(8 * 1024 * 1024))\n"
    outcome = _run(paths, code, memory_bytes=64 * 1024 * 1024)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert "MemoryError" in outcome.error.message


def test_cancel_during_guest_compute(paths: CodemodePaths, pids: list[int]) -> None:
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    started = time.monotonic()
    outcome = _run(paths, "while True:\n    pass\n", cancel=cancel)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.ABORTED
    assert time.monotonic() - started < 3


def test_cancel_during_a_tool_callback(paths: CodemodePaths, pids: list[int]) -> None:
    cancel = threading.Event()
    tool_stop = threading.Event()

    def slow(name: str, arguments_json: str) -> CallResult:
        cancel.set()  # the operator cancels while this tool runs
        assert tool_stop.wait(5)
        return CallResult(ok=False, text="interrupted", cancelled=True)

    code = "print('before')\ntools.read({})\nprint('not reached')\n"
    outcome = _run(paths, code, call_tool=slow, cancel=cancel, tool_stop=tool_stop)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.ABORTED
    assert outcome.output == ("before\n",)
    assert [c.status for c in outcome.calls] == [CallStatus.CANCELLED]


def test_a_missing_runtime_fails_closed(tmp_path: Path, pids: list[int]) -> None:
    outcome = _run(CodemodePaths(tmp_path), "print('never')\n")
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SANDBOX
    assert "could not start the script" in outcome.error.message
    assert outcome.output == ()


def _open_fds() -> set[int]:
    return {
        int(name)
        for name in os.listdir(
            "/dev/fd" if os.path.isdir("/dev/fd") else "/proc/self/fd"
        )
    }


def test_real_runs_leak_no_fds_or_threads(
    paths: CodemodePaths, pids: list[int]
) -> None:
    _run(paths, "pass\n")
    fds = _open_fds()
    threads = threading.active_count()
    _run(paths, "print(1)\n")
    _run(paths, "while True:\n    pass\n", wall_seconds=1)
    _run(paths, RAW.format(line=b"garbage\n") + "import time\ntime.sleep(60)\n")
    assert threading.active_count() == threads
    assert _open_fds() == fds


def test_effects_before_a_failure_are_reported_as_not_undone(
    paths: CodemodePaths, pids: list[int]
) -> None:
    written: list[str] = []

    def write(name: str, arguments_json: str) -> CallResult:
        written.append(arguments_json)
        return CallResult(ok=True, text="wrote 5 bytes")

    code = "tools.read(path='notes.txt', content='hello')\nprint('saved')\nraise RuntimeError('after the write')\n"
    outcome = _run(paths, code, call_tool=write)
    result = format_result(outcome)
    assert written == ['{"path": "notes.txt", "content": "hello"}']
    assert result.is_error
    assert result.text.startswith("Script failed\nWall time ")
    assert (
        "Output:\nsaved\nScript error:\nTraceback (most recent call last):"
        in result.text
    )
    assert result.text.endswith(
        "RuntimeError: after the write\n\n"
        "Tool calls made before the failure (they are not undone): read (ok)"
    )
