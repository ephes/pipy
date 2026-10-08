"""CM1 T3: the codemode host runner against a scripted fake worker.

The fake worker (``codemode_fake_worker.py``) plays worker and guest from a
scenario string, so every protocol rule, cap and cleanup path of
:func:`run_script` is pinned without wasmtime. The same red-team vectors
against the real CPython-on-WASI guest live in
``test_native_codemode_host_runtime.py``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.codemode import host, worker
from pipy_harness.native.codemode import outcome as outcome_module
from pipy_harness.native.codemode.outcome import (
    CallResult,
    CallStatus,
    ErrorKind,
    ScriptLimits,
    ScriptOutcome,
)
from pipy_harness.native.codemode.runtime import CodemodePaths, RuntimePin

FAKE_WORKER = Path(__file__).with_name("codemode_fake_worker.py")
PIN = RuntimePin(version="0", url="file:///fake", sha256="ab" * 32, size=1)
LIMITS = ScriptLimits(wall_seconds=5.0)

Callback = Callable[[str, str], CallResult]


def _no_tools(name: str, arguments_json: str) -> CallResult:
    raise AssertionError(f"unexpected tool call {name} {arguments_json}")


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Point the runner at the fake worker; collect every worker pid."""

    pids: list[int] = []
    real_worker = host._Worker

    class RecordingWorker(real_worker):  # type: ignore[misc, valid-type]
        def __init__(self, argv: list[str], status_write: int) -> None:
            super().__init__(argv, status_write)
            pids.append(self.process.pid)

    monkeypatch.setattr(host, "_Worker", RecordingWorker)
    yield pids
    for pid in pids:
        assert _group_is_gone(pid), f"worker group {pid} survived"


def _group_is_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _run(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    *,
    call_tool: Callback = _no_tools,
    tool_names: tuple[str, ...] = ("read", "write"),
    limits: ScriptLimits = LIMITS,
    cancel: threading.Event | None = None,
    tool_stop: threading.Event | None = None,
    code: str = "pass",
    activity_waiter: host.ActivityWaiter | None = None,
) -> ScriptOutcome:
    def fake_argv(python: str, status_fd: int, request: str) -> list[str]:
        json.loads(request)  # the real request is still well-formed JSON
        return [python, "-I", str(FAKE_WORKER), str(status_fd), PIN.sha256, scenario]

    monkeypatch.setattr(host, "_worker_argv", fake_argv)
    return host.run_script(
        code,
        call_tool=call_tool,
        tool_names=tool_names,
        limits=limits,
        cancel=cancel,
        tool_stop=tool_stop,
        paths=CodemodePaths(Path("/nonexistent-codemode-root")),
        pin=PIN,
        activity_waiter=activity_waiter,
    )


def _sandbox_error(outcome: ScriptOutcome, fragment: str) -> None:
    assert outcome.error is not None, outcome
    assert outcome.error.kind is ErrorKind.SANDBOX, outcome.error
    assert fragment in outcome.error.message, outcome.error.message


# --- the well-behaved path --------------------------------------------------------


def test_a_script_runs_calls_tools_and_completes(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    seen: list[tuple[str, str, str]] = []

    def call_tool(name: str, arguments_json: str) -> CallResult:
        seen.append((threading.current_thread().name, name, arguments_json))
        if name == "write":
            return CallResult(ok=False, text="denied")
        return CallResult(ok=True, text="file body")

    scenario = (
        "run = start()\n"
        "send({'type': 'text', 'value': run['code'] + '|' + ','.join(run['tools'])})\n"
        "first = call(1, 'read', {'path': 'a.txt'})\n"
        "second = call(2, 'write', {'path': 'b'})\n"
        "send({'type': 'text', 'value': json.dumps([first, second])})\n"
        "done()\n"
        "exit_status()\n"
    )
    outcome = _run(
        monkeypatch,
        scenario,
        call_tool=call_tool,
        code="print(1)",
        tool_names=("write", "read"),
    )
    assert outcome.ok, outcome
    assert outcome.output[0] == "print(1)|read,write"
    assert json.loads(outcome.output[1]) == [
        {"type": "result", "id": 1, "ok": True, "value": "file body"},
        {"type": "result", "id": 2, "ok": False, "error": "denied"},
    ]
    # Callbacks run on the calling thread, never on the I/O thread.
    assert seen == [
        ("MainThread", "read", '{"path": "a.txt"}'),
        ("MainThread", "write", '{"path": "b"}'),
    ]
    assert [(c.name, c.status) for c in outcome.calls] == [
        ("read", CallStatus.OK),
        ("write", CallStatus.ERROR),
    ]
    assert len(spawned) == 1


def test_a_script_failure_carries_its_traceback(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    error = {
        "type": "ValueError",
        "message": "bad",
        "traceback": "Traceback ...\nValueError: bad\n",
    }
    scenario = (
        f"start()\nsend({{'type': 'done', 'ok': False, 'error': {error!r}}})\nhang()\n"
    )
    outcome = _run(monkeypatch, scenario)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert outcome.error.message == "Traceback ...\nValueError: bad"


def test_an_unknown_tool_name_never_reaches_the_callback(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    scenario = (
        "start()\n"
        "reply = call(1, 'reed', {})\n"
        "send({'type': 'text', 'value': json.dumps(reply)})\n"
        "done()\n"
    )
    outcome = _run(monkeypatch, scenario)
    assert outcome.ok, outcome
    reply = json.loads(outcome.output[0])
    assert reply["ok"] is False
    assert "Did you mean tools.read?" in reply["error"]
    assert outcome.calls == ()


# --- protocol violations (red team: malformed JSON crashed the spike host) --------


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        (b"not json\n", "not JSON"),
        (b"\xff\xfe\n", "not JSON"),
        (b"[" * 100_000 + b"\n", "not JSON"),
        (
            b'{"type": "call", "id": 1, "name": "read", "args": {"x": NaN}}\n',
            "not JSON",
        ),
        (
            b'{"type": "call", "id": 1, "name": "read", "args": {"x": 1e999}}\n',
            "not JSON",
        ),
        (b'{"type": "done", "ok": true, "extra": -1e999}\n', "not JSON"),
        (b'{"type": "text", "value": "x", "n": 1e400}\n', "not JSON"),
        (b"[1, 2]\n", "not a JSON object"),
        (b'{"type": "shell"}\n', "unknown type"),
        (b'{"type": ["text"]}\n', "unknown type"),
        (b'{"type": "text", "value": 3}\n', "no string value"),
        (b'{"type": "call", "id": 2, "name": "read", "args": {}}\n', "unexpected id"),
        (
            b'{"type": "call", "id": true, "name": "read", "args": {}}\n',
            "unexpected id",
        ),
        (b'{"type": "call", "id": 1, "name": 5, "args": {}}\n', "malformed"),
        (b'{"type": "call", "id": 1, "name": "read", "args": []}\n', "malformed"),
        (b'{"type": "done"}\n', "malformed done"),
        (b'{"type": "done", "ok": false, "error": {"type": "X"}}\n', "malformed done"),
    ],
)
def test_protocol_violations_kill_the_worker_without_raising(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], line: bytes, fragment: str
) -> None:
    scenario = (
        f"start()\nsend({{'type': 'text', 'value': 'before'}})\nraw({line!r})\nhang()\n"
    )
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario)
    _sandbox_error(outcome, fragment)
    assert outcome.error is not None
    assert outcome.error.message.startswith("protocol violation: ")
    assert outcome.output == ("before",)
    assert time.monotonic() - started < 3


def test_a_second_outstanding_call_is_a_violation(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    stop = threading.Event()
    calls: list[str] = []

    def call_tool(name: str, arguments_json: str) -> CallResult:
        calls.append(name)
        stop.wait(5)
        return CallResult(ok=True, text="late")

    # The guest never reads its stdin and keeps calling (the call flood).
    scenario = (
        "start()\n"
        "for n in range(1, 10_000):\n"
        "    send({'type': 'call', 'id': n, 'name': 'read', 'args': {}})\n"
        "hang()\n"
    )
    outcome = _run(monkeypatch, scenario, call_tool=call_tool, tool_stop=stop)
    _sandbox_error(outcome, "while a tool call was pending")
    # Both calls usually arrive in one chunk, so the run may end before the
    # first callback starts; it never runs more than once.
    assert calls in ([], ["read"])
    assert stop.is_set()


@pytest.mark.parametrize(
    ("name", "reply_bytes"), [("read", 4 * 1024 * 1024), ("unknown", 0)]
)
def test_unread_replies_keep_the_call_outstanding(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], name: str, reply_bytes: int
) -> None:
    # The guest never reads its stdin and keeps calling. A reply stays
    # outstanding until it is written in full, so at most one unread reply is
    # ever buffered and the next call is a violation. Small (unknown-tool)
    # replies fit in the pipe until it fills.
    def call_tool(tool: str, arguments_json: str) -> CallResult:
        return CallResult(ok=True, text="r" * reply_bytes)

    scenario = (
        "start()\n"
        "for n in range(1, 100_000):\n"
        f"    send({{'type': 'call', 'id': n, 'name': {name!r}, 'args': {{}}}})\n"
        "    time.sleep(0.0005 if n == 1 else 0)\n"
        "hang()\n"
    )
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    _sandbox_error(outcome, "while a tool call was pending")
    assert len(outcome.calls) <= 1


def test_a_large_reply_the_guest_never_reads_does_not_block_the_host(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    # The guest asks for 8 MiB, never reads it, and floods output: the host's
    # writes must not block, so it still sees the flood (a violation, since
    # nothing may arrive while a call is pending).
    def call_tool(name: str, arguments_json: str) -> CallResult:
        return CallResult(ok=True, text="x" * (8 * 1024 * 1024))

    scenario = (
        "start()\n"
        "send({'type': 'call', 'id': 1, 'name': 'read', 'args': {}})\n"
        "while True:\n"
        "    send({'type': 'text', 'value': 'y' * 1000})\n"
    )
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    _sandbox_error(outcome, "while a tool call was pending")
    assert time.monotonic() - started < 4


@pytest.mark.parametrize(
    "after",
    [b'{"type":"done","ok":true}\n', b"garbage\n", b'{"type":"text","value":"t"}\n'],
)
def test_a_line_after_a_call_in_the_same_chunk_ends_the_run_before_the_tool(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], after: bytes
) -> None:
    # Red-team V4: a call and a done in one write raced the I/O thread, so the
    # tool sometimes ran after the guest had declared success.
    ran: list[str] = []

    def call_tool(name: str, arguments_json: str) -> CallResult:
        ran.append(name)
        return CallResult(ok=True, text="did it")

    line = b'{"type":"call","id":1,"name":"write","args":{"path":"x"}}\n' + after
    scenario = f"start()\nraw({line!r})\nhang()\n"
    for _ in range(10):
        outcome = _run(monkeypatch, scenario, call_tool=call_tool)
        _sandbox_error(outcome, "protocol violation: ")
        assert outcome.calls == ()
    assert ran == []


def test_a_done_in_a_later_chunk_while_a_call_runs_is_a_violation(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def call_tool(name: str, arguments_json: str) -> CallResult:
        entered.set()
        release.wait(5)
        return CallResult(ok=True, text="did it")

    scenario = (
        "start()\n"
        "send({'type': 'call', 'id': 1, 'name': 'write', 'args': {}})\n"
        "time.sleep(0.3)\n"
        "done()\n"
        "hang()\n"
    )
    stop = threading.Event()

    def release_when_stopped() -> None:
        stop.wait(5)
        release.set()

    threading.Thread(target=release_when_stopped, daemon=True).start()
    outcome = _run(monkeypatch, scenario, call_tool=call_tool, tool_stop=stop)
    assert entered.is_set()
    _sandbox_error(outcome, "while a tool call was pending")
    assert [c.status for c in outcome.calls] == [CallStatus.OK]


def test_lone_surrogates_from_the_guest_become_replacement_characters(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    # Red-team: lone surrogates parsed into host strings that cannot be encoded
    # as UTF-8, breaking every downstream sink. Escaped and raw (surrogatepass
    # bytes) forms both reach json.loads.
    seen: list[str] = []

    def call_tool(name: str, arguments_json: str) -> CallResult:
        seen.append(arguments_json)
        return CallResult(ok=True, text="ok")

    raw_surrogate = "\udfff".encode("utf-8", "surrogatepass")
    text_line = b'{"type":"text","value":"A\\ud800B"}\n'
    call_line = (
        b'{"type":"call","id":1,"name":"write","args":{"k\\udc00":"'
        + raw_surrogate
        + b'","ok":"\\ud83d\\ude00"}}\n'
    )
    done_line = (
        b'{"type":"done","ok":false,"error":{"type":"ValueError",'
        b'"message":"\\ud83d","traceback":"ValueError: \\ud83d"}}\n'
    )
    scenario = (
        f"start()\nraw({text_line!r})\nraw({call_line!r})\nrecv()\n"
        f"raw({done_line!r})\nhang()\n"
    )
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    assert outcome.output == ("A\ufffdB",)
    assert json.loads(seen[0]) == {"k\ufffd": "\ufffd", "ok": "\U0001f600"}
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert outcome.error.message == "ValueError: \ufffd"
    for text in (*outcome.output, *seen, outcome.error.message):
        text.encode("utf-8")


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-16"])
def test_guest_lines_must_be_utf8(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], encoding: str
) -> None:
    # json.loads(bytes) would detect UTF-16/32 itself, and their escapes hide
    # from a byte-level surrogate check; the wire is UTF-8 only.
    def call_tool(name: str, arguments_json: str) -> CallResult:
        raise AssertionError("an off-wire encoding reached the callback")

    body = '{"type":"call","id":1,"name":"write","args":{"k":"\\ud800"}}'
    line = body.encode(encoding) + b"\n"
    scenario = f"start()\nraw({line!r})\nhang()\n"
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    _sandbox_error(outcome, "not JSON")
    assert outcome.calls == ()


def test_a_reply_blocked_on_a_full_pipe_still_meets_the_deadline(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    def call_tool(name: str, arguments_json: str) -> CallResult:
        return CallResult(ok=True, text="x" * (8 * 1024 * 1024))

    scenario = (
        "start()\nsend({'type': 'call', 'id': 1, 'name': 'read', 'args': {}})\nhang()\n"
    )
    limits = ScriptLimits(wall_seconds=1.0)
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario, call_tool=call_tool, limits=limits)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.TIMEOUT
    assert outcome.error.message == "Execution timed out after 1 s"
    assert time.monotonic() - started < 3


def test_a_forged_done_is_terminal(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    scenario = (
        "start()\n"
        "send({'type': 'text', 'value': 'a'})\n"
        'raw(b\'{"type":"done","ok":true}\\n{"type":"text","value":"same chunk"}\\n\')\n'
        "send({'type': 'text', 'value': 'after'})\n"
        "send({'type': 'call', 'id': 1, 'name': 'read', 'args': {}})\n"
        "hang()\n"
    )
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario)
    assert outcome.ok, outcome
    assert outcome.output == ("a",)
    assert time.monotonic() - started < 3


def test_an_oversized_line_is_caught_while_buffering(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    # No newline ever arrives: the cap must fire on the buffered bytes.
    scenario = (
        'start()\nraw(b\'{"type":"text","value":"\' + b\'x\' * 300_000)\nhang()\n'
    )
    limits = ScriptLimits(wall_seconds=5.0, line_bytes=64 * 1024)
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario, limits=limits)
    _sandbox_error(outcome, "a guest line exceeded 65536 bytes")
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    ("limits", "fragment"),
    [
        (ScriptLimits(wall_seconds=5.0, output_bytes=10_000), "10000 bytes"),
        (ScriptLimits(wall_seconds=5.0, output_messages=50), "50 messages"),
    ],
)
def test_a_print_flood_hits_the_output_cap(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],
    limits: ScriptLimits,
    fragment: str,
) -> None:
    scenario = "start()\nwhile True:\n    send({'type': 'text', 'value': 'z' * 100})\n"
    outcome = _run(monkeypatch, scenario, limits=limits)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.SCRIPT
    assert fragment in outcome.error.message
    assert "write large data through a tool" in outcome.error.message
    assert sum(len(item) for item in outcome.output) <= limits.output_bytes
    assert len(outcome.output) <= limits.output_messages


def test_a_guest_blocked_in_a_read_ends_at_the_wall_limit(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    scenario = "start()\nrecv()\n"
    limits = ScriptLimits(wall_seconds=0.8)
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario, limits=limits)
    elapsed = time.monotonic() - started
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.TIMEOUT
    assert 0.7 < elapsed < 2.5
    assert 0.7 < outcome.wall_seconds < 2.5


# --- worker status ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "kind", "fragment"),
    [
        (
            "recv()\nsend({'type': 'done', 'ok': True})\nhang()\n",
            ErrorKind.SANDBOX,
            "before the worker was ready",
        ),
        (
            "ready(sha256='cd' * 32)\nhang()\n",
            ErrorKind.SANDBOX,
            "unexpected backend or runtime",
        ),
        (
            "ready(backend='native')\nhang()\n",
            ErrorKind.SANDBOX,
            "unexpected backend or runtime",
        ),
        (
            "raw(b'garbage\\n', STATUS_FD)\nhang()\n",
            ErrorKind.SANDBOX,
            "malformed status line",
        ),
        (
            "raw(b'x' * 70_000, STATUS_FD)\nhang()\n",
            ErrorKind.SANDBOX,
            "status output is too large",
        ),
        (
            "exit_status('error', detail='no runtime')\n",
            ErrorKind.SANDBOX,
            "could not start the script: no runtime",
        ),
        ("start()\n", ErrorKind.SANDBOX, "exited without reporting its status"),
        ("start()\nexit_status('trap-interrupt')\n", ErrorKind.TIMEOUT, "CPU backstop"),
        (
            "start()\nexit_status('trap-stack')\n",
            ErrorKind.SCRIPT,
            "wasm stack overflow",
        ),
        (
            "start()\nexit_status('error', code=3)\n",
            ErrorKind.SANDBOX,
            "rejected a host message",
        ),
        (
            "start()\nexit_status('error', code=7)\n",
            ErrorKind.SCRIPT,
            "exited with status 7",
        ),
        (
            "start()\nexit_status('ok', code=0)\n",
            ErrorKind.SCRIPT,
            "exited with status 0",
        ),
        (
            "start()\nexit_status('error', detail='wasm trap: unreachable')\n",
            ErrorKind.SANDBOX,
            "stopped unexpectedly: wasm trap: unreachable",
        ),
    ],
)
def test_worker_status_decides_runs_without_a_done_line(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],
    scenario: str,
    kind: ErrorKind,
    fragment: str,
) -> None:
    outcome = _run(monkeypatch, scenario)
    assert outcome.error is not None, outcome
    assert outcome.error.kind is kind, outcome.error
    assert fragment in outcome.error.message, outcome.error.message


def test_worker_stderr_is_kept_capped_as_diagnostics(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    scenario = (
        "start()\nraw(b'e' * 100_000 + b'TAIL', 2)\nexit_status('error', code=1)\n"
    )
    limits = ScriptLimits(wall_seconds=5.0, stderr_bytes=1000)
    outcome = _run(monkeypatch, scenario, limits=limits)
    assert outcome.diagnostics.startswith("e" * 996 + "TAIL")
    assert (
        'worker exit: {"type": "exit", "reason": "error", "code": 1}'
        in outcome.diagnostics
    )


def test_a_worker_that_cannot_start_is_a_sandbox_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_stop = threading.Event()
    outcome = host.run_script(
        "pass",
        call_tool=_no_tools,
        tool_names=(),
        tool_stop=tool_stop,
        paths=CodemodePaths(Path("/nonexistent-codemode-root")),
        python="/nonexistent/python",
    )
    _sandbox_error(outcome, "Failed to start the worker")
    assert tool_stop.is_set()


# --- limits checked before spawning -----------------------------------------------


def test_oversized_code_and_a_preset_cancel_never_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_spawn(*args: Any) -> list[str]:
        raise AssertionError("spawned a worker")

    monkeypatch.setattr(host, "_worker_argv", no_spawn)
    big_stop = threading.Event()
    big = host.run_script(
        "x" * 101,
        call_tool=_no_tools,
        tool_names=(),
        limits=ScriptLimits(code_bytes=100),
        tool_stop=big_stop,
    )
    assert big.error is not None
    assert big.error.kind is ErrorKind.SCRIPT
    assert "101 bytes, more than the 100-byte limit" in big.error.message
    assert big_stop.is_set()
    cancel = threading.Event()
    cancel.set()
    aborted_stop = threading.Event()
    aborted = host.run_script(
        "pass",
        call_tool=_no_tools,
        tool_names=(),
        cancel=cancel,
        tool_stop=aborted_stop,
    )
    assert aborted.error is not None
    assert aborted.error.kind is ErrorKind.ABORTED
    assert aborted_stop.is_set()


@pytest.mark.parametrize("value", [0, -1.0, float("nan"), float("inf"), True])
def test_limits_must_be_positive_and_finite(value: float) -> None:
    with pytest.raises(
        ValueError, match="wall_seconds must be a positive finite number"
    ):
        ScriptLimits(wall_seconds=value)


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"wall_seconds": 90_000.0}, "at most 86400 s"),
        ({"wall_seconds": 86_400.0 - 4}, "at most 86400 s"),
        ({"memory_bytes": 4 * 1024**3 + 1}, "memory_bytes must be at most"),
        ({"memory_bytes": 1024.0 * 1024}, "memory_bytes must be an int"),
    ],
)
def test_limits_the_worker_would_refuse_are_refused_up_front(
    limits: dict[str, float], message: str
) -> None:
    # Red-team: these used to construct and then fail as a confusing sandbox
    # error ("malformed run request") from the worker.
    with pytest.raises(ValueError, match=message):
        ScriptLimits(**limits)  # type: ignore[arg-type]


def test_limit_bounds_match_the_worker() -> None:
    assert outcome_module.MEMORY_BYTES_MAX == worker.WASM32_MEMORY_MAX
    assert outcome_module.CPU_SECONDS_MAX == worker.CPU_SECONDS_MAX
    largest = ScriptLimits(
        wall_seconds=worker.CPU_SECONDS_MAX - 5, memory_bytes=worker.WASM32_MEMORY_MAX
    )
    request = json.loads(host._run_request(CodemodePaths(Path("/x")), PIN, largest))
    worker.RunConfig.from_request(request)  # does not raise


# --- cancellation -----------------------------------------------------------------


def test_cancel_during_a_tool_callback_aborts_and_marks_the_call(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    cancel = threading.Event()
    tool_stop = threading.Event()

    def call_tool(name: str, arguments_json: str) -> CallResult:
        threading.Timer(0.2, cancel.set).start()
        assert tool_stop.wait(5), "the runner never signalled tool_stop"
        return CallResult(ok=False, text="interrupted", cancelled=True)

    scenario = (
        "start()\n"
        "call(1, 'read', {})\n"
        "send({'type': 'text', 'value': 'not reached'})\n"
        "done()\n"
    )
    started = time.monotonic()
    outcome = _run(
        monkeypatch, scenario, call_tool=call_tool, cancel=cancel, tool_stop=tool_stop
    )
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.ABORTED
    assert outcome.error.message == "Execution aborted"
    assert [(c.name, c.status) for c in outcome.calls] == [
        ("read", CallStatus.CANCELLED)
    ]
    assert outcome.output == ()
    assert time.monotonic() - started < 3


def test_the_deadline_during_a_tool_callback_signals_tool_stop(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    tool_stop = threading.Event()

    def call_tool(name: str, arguments_json: str) -> CallResult:
        assert tool_stop.wait(5)
        return CallResult(ok=False, text="stopped", cancelled=True)

    scenario = "start()\ncall(1, 'read', {})\ndone()\n"
    outcome = _run(
        monkeypatch,
        scenario,
        call_tool=call_tool,
        tool_stop=tool_stop,
        limits=ScriptLimits(wall_seconds=0.5),
    )
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.TIMEOUT
    assert [c.status for c in outcome.calls] == [CallStatus.CANCELLED]


def test_cancel_during_guest_compute_aborts_promptly(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    scenario = (
        "start()\nsend({'type': 'text', 'value': 'working'})\nwhile True:\n    pass\n"
    )
    started = time.monotonic()
    outcome = _run(monkeypatch, scenario, cancel=cancel)
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.ABORTED
    assert outcome.output == ("working",)
    assert time.monotonic() - started < 2


def test_a_raising_callback_is_a_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    def call_tool(name: str, arguments_json: str) -> CallResult:
        raise RuntimeError("boom")

    scenario = "start()\ncall(1, 'read', {})\ndone()\n"
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    _sandbox_error(outcome, "the read tool callback failed: RuntimeError: boom")
    assert [c.status for c in outcome.calls] == [CallStatus.ERROR]


@pytest.mark.parametrize(("returned", "type_name"), [(None, "NoneType"), ("ok", "str")])
def test_a_callback_returning_a_non_call_result_is_a_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], returned: Any, type_name: str
) -> None:
    # Red-team: reading .cancelled outside the guarded call made run_script raise.
    def call_tool(name: str, arguments_json: str) -> CallResult:
        return returned  # type: ignore[no-any-return]

    scenario = "start()\ncall(1, 'read', {})\ndone()\n"
    outcome = _run(monkeypatch, scenario, call_tool=call_tool)
    _sandbox_error(
        outcome,
        f"the read tool callback failed: TypeError: returned {type_name}, "
        "not CallResult",
    )
    assert [c.status for c in outcome.calls] == [CallStatus.ERROR]


# --- tool_stop ownership ----------------------------------------------------------


def test_a_tool_stop_already_set_is_refused_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_spawn(*args: Any) -> list[str]:
        raise AssertionError("spawned a worker")

    monkeypatch.setattr(host, "_worker_argv", no_spawn)
    used = threading.Event()
    used.set()  # e.g. left over from an earlier run
    outcome = host.run_script(
        "pass", call_tool=_no_tools, tool_names=(), tool_stop=used
    )
    _sandbox_error(outcome, "tool_stop must be a fresh event")


def test_a_nested_run_cannot_share_the_outer_tool_stop(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    # Red-team: a nested run with the same event set it on exit, so every later
    # callback of the outer run saw "stop" at entry.
    stop = threading.Event()
    stop_at_entry: list[bool] = []
    inner: list[ScriptOutcome] = []

    def call_tool(name: str, arguments_json: str) -> CallResult:
        stop_at_entry.append(stop.is_set())
        if name == "read":
            inner.append(
                host.run_script(
                    "pass", call_tool=_no_tools, tool_names=(), tool_stop=stop
                )
            )
        return CallResult(ok=True, text="r")

    scenario = "start()\ncall(1, 'read', {})\ncall(2, 'write', {})\ndone()\nhang()\n"
    outcome = _run(monkeypatch, scenario, call_tool=call_tool, tool_stop=stop)
    assert outcome.ok, outcome
    assert stop_at_entry == [False, False]
    assert inner[0].error is not None
    assert "tool_stop must be a fresh event" in inner[0].error.message
    assert stop.is_set()  # set once the outer run ended
    assert host._tool_stops_in_use == set()


# --- start-up failures ------------------------------------------------------------


def _failing_pipe(fail_on: int) -> Callable[[], tuple[int, int]]:
    real_pipe = os.pipe
    count = 0

    def pipe() -> tuple[int, int]:
        # Count only the runner's own pipes, not subprocess's.
        nonlocal count
        if sys._getframe(1).f_globals.get("__name__") == host.__name__:
            count += 1
            if count == fail_on:
                raise OSError(24, "Too many open files")
        return real_pipe()

    return pipe


@pytest.mark.parametrize("fail_on", [1, 2])
def test_no_fd_for_a_pipe_is_a_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], fail_on: int
) -> None:
    # Red-team: EMFILE on the status pipe (1) raised out of run_script; the
    # wake pipe (2) is created after the worker started.
    before = _open_fds()
    monkeypatch.setattr(os, "pipe", _failing_pipe(fail_on))
    outcome = _run(monkeypatch, "start()\ndone()\n")
    monkeypatch.undo()
    _sandbox_error(outcome, "Failed to start the worker: [Errno 24]")
    assert len(spawned) == fail_on - 1
    assert _open_fds() <= before


def test_no_thread_for_the_io_loop_is_a_sandbox_error(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    def no_thread(self: Any) -> None:
        raise RuntimeError("can't start new thread")

    before = _open_fds()
    monkeypatch.setattr(host._Channel, "start", no_thread)
    outcome = _run(monkeypatch, "start()\ndone()\n")
    _sandbox_error(outcome, "Failed to start the worker: can't start new thread")
    assert len(spawned) == 1
    assert _open_fds() <= before


# --- cleanup ----------------------------------------------------------------------


def _open_fds() -> set[int]:
    return {
        int(name)
        for name in os.listdir(
            "/dev/fd" if os.path.isdir("/dev/fd") else "/proc/self/fd"
        )
    }


def test_runs_leak_no_processes_fds_or_threads(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    scenarios = [
        "start()\ndone()\n",
        "start()\nraw(b'nope\\n')\nhang()\n",
        "start()\nwhile True:\n    send({'type': 'text', 'value': 'z' * 100})\n",
        "start()\nrecv()\n",
    ]
    _run(monkeypatch, scenarios[0])  # warm up lazily created fds
    fds = _open_fds()
    threads = threading.active_count()
    for scenario in scenarios:
        _run(
            monkeypatch,
            scenario,
            limits=ScriptLimits(wall_seconds=0.5, output_messages=20),
        )
    assert threading.active_count() == threads
    assert _open_fds() == fds
    assert not any(t.name == "codemode-io" for t in threading.enumerate())


@pytest.mark.parametrize("failure", ["exception", "keyboard", "cancel"])
def test_activity_waiter_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],
    failure: str,
) -> None:
    session_thread = threading.current_thread()
    seen = []

    def waiter(activity: threading.Event, cancel: threading.Event) -> None:
        assert threading.current_thread() is session_thread
        seen.append(True)
        if failure == "exception":
            raise RuntimeError("activity failure")
        if failure == "keyboard":
            raise KeyboardInterrupt
        cancel.set()

    outcome = _run(monkeypatch, "start(); time.sleep(10)", activity_waiter=waiter)
    assert seen and spawned
    assert outcome.error is not None
    assert outcome.error.kind is (
        ErrorKind.SANDBOX if failure == "exception" else ErrorKind.ABORTED
    )


def test_activity_queue_and_event_have_no_lost_wake() -> None:
    # Exercise the production publication/acknowledgement methods with racing
    # producers, without pipes. The final publication must remain observable.
    import queue

    channel = object.__new__(host._Channel)
    channel.inbox = queue.SimpleQueue()
    channel.activity = threading.Event()
    channel._activity_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def producer() -> None:
        for index in range(1000):
            barrier.wait()
            channel.post_activity(host._Call(index, "read", "{}"))
            barrier.wait()

    thread = threading.Thread(target=producer)
    thread.start()
    try:
        for _ in range(1000):
            barrier.wait()
            event = channel.take_activity()
            barrier.wait()
            if event is None:
                assert channel.activity.is_set()
                assert channel.take_activity() is not None
            assert channel.take_activity() is None
            assert not channel.activity.is_set()
    finally:
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_io_base_exception_signals_activity_and_tool_stop(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],
) -> None:
    def fail(channel: host._Channel) -> None:
        raise SystemExit("I/O stopped")

    monkeypatch.setattr(host._Channel, "_loop", fail)
    stop = threading.Event()

    def waiter(activity: threading.Event, cancel: threading.Event) -> None:
        activity.wait()

    outcome = _run(
        monkeypatch,
        "start(); time.sleep(10)",
        tool_stop=stop,
        activity_waiter=waiter,
    )
    _sandbox_error(outcome, "I/O stopped")
    assert stop.is_set() and spawned


def _inject_completion_fault(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> tuple[list[host._Channel], threading.Event]:
    channels: list[host._Channel] = []
    original_start = host._Channel.start
    stop = threading.Event()
    original_stop_set = stop.set

    def fail() -> None:
        raise RuntimeError("completion fault")

    def start(channel: host._Channel) -> None:
        channels.append(channel)
        if failure == "finished":
            monkeypatch.setattr(channel.finished, "set", fail)
        original_start(channel)

    def stop_set() -> None:
        if threading.current_thread().name == "codemode-io":
            fail()
        original_stop_set()

    monkeypatch.setattr(host._Channel, "start", start)
    if failure == "kill":
        monkeypatch.setattr(host._Worker, "kill", lambda worker: fail())
    elif failure == "diagnostics":
        monkeypatch.setattr(host._Channel, "_diagnostics", lambda channel: fail())
    elif failure == "stop":
        monkeypatch.setattr(stop, "set", stop_set)

    return channels, stop


@pytest.mark.parametrize("with_waiter", [False, True])
@pytest.mark.parametrize("failure", ["kill", "diagnostics", "finished", "stop"])
def test_completion_failure_returns_and_reaps(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],
    with_waiter: bool,
    failure: str,
) -> None:
    channels, stop = _inject_completion_fault(monkeypatch, failure)
    outcomes: list[ScriptOutcome] = []
    failures: list[BaseException] = []

    def waiter(activity: threading.Event, cancel: threading.Event) -> None:
        activity.wait()  # Deliberately unbounded: completion must wake it.
        assert stop.is_set()  # Includes a fault in the stop setter itself.

    def drive() -> None:
        try:
            outcomes.append(
                _run(
                    monkeypatch,
                    "start(); done(); hang()",
                    tool_stop=stop,
                    activity_waiter=waiter if with_waiter else None,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - report driver failures on the test thread
            failures.append(exc)

    driver = threading.Thread(target=drive, daemon=True)
    started = time.monotonic()
    driver.start()
    driver.join(timeout=2)
    bounded = not driver.is_alive()
    if not bounded:
        # Rescue a regressed host without hanging pytest or leaving a group.
        for channel in channels:
            channel.post_activity(host._Finished(None))
        driver.join(timeout=host.REAP_TIMEOUT_SECONDS + 1)
    assert bounded, "host did not return after completion fault"
    assert not failures
    assert time.monotonic() - started < 2
    assert len(outcomes) == 1
    _sandbox_error(outcomes[0], "completion fault")
    assert stop.is_set() and spawned
    assert all(_group_is_gone(pid) for pid in spawned)
    for pid in spawned:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize("fault", [OSError, PermissionError])
def test_signal_fault_still_kills_owned_group_and_reaps(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int], fault: type[OSError]
) -> None:
    real_killpg = os.killpg
    attempted: list[int] = []

    def killpg(pid: int, sig: int) -> None:
        if sig == 0:
            real_killpg(pid, sig)
        else:
            attempted.append(pid)
            raise fault("signal fault")

    monkeypatch.setattr(os, "killpg", killpg)
    outcome = _run(monkeypatch, "start(); done(); hang()")
    assert outcome.error is None
    assert attempted and set(attempted) == set(spawned)
    assert all(_group_is_gone(pid) for pid in spawned)
    for pid in spawned:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize("queued", [False, True])
def test_dead_io_thread_never_waits_for_missing_completion(queued: bool) -> None:
    import queue

    channel = object.__new__(host._Channel)
    channel.inbox = queue.SimpleQueue()
    channel.activity = threading.Event()
    channel._activity_lock = threading.Lock()
    channel._thread = threading.Thread(target=lambda: None)
    channel._thread.start()
    channel._thread.join(timeout=1)
    assert not channel.alive()
    completed = host._Finished(None, ("completed",))
    if queued:
        channel.post_activity(completed)

    def waiter(activity: threading.Event, cancel: threading.Event) -> None:
        pytest.fail("dead I/O thread must not invoke an unbounded waiter")

    outcome = host._serve(channel, _no_tools, threading.Event(), [], waiter)
    if queued:
        assert outcome is completed
    else:
        assert outcome.error is not None
        assert outcome.error.kind is ErrorKind.SANDBOX
        assert "without completion" in outcome.error.message


def test_standalone_polling_keyboard_interrupt_aborts_and_reaps(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    original_start = host._Channel.start
    interrupted = threading.Event()

    def start(channel: host._Channel) -> None:
        original_wait = channel.activity.wait

        def wait(timeout: float | None = None) -> bool:
            if not interrupted.is_set():
                interrupted.set()
                raise KeyboardInterrupt
            return original_wait(timeout)

        monkeypatch.setattr(channel.activity, "wait", wait)
        original_start(channel)

    monkeypatch.setattr(host._Channel, "start", start)
    outcome = _run(monkeypatch, "start(); hang()")
    assert interrupted.is_set()
    assert outcome.error is not None
    assert outcome.error.kind is ErrorKind.ABORTED
    assert spawned and all(_group_is_gone(pid) for pid in spawned)
