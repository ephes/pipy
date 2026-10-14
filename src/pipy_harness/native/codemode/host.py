"""Codemode host runner: run one script in a fresh sandboxed worker (CM1 T3).

:func:`run_script` starts the worker (:mod:`.worker`) in its own process
group, feeds it the script, serves its ``tools.<name>(args)`` calls and its
discovery calls through injected callbacks and always kills and reaps it. It never raises for anything
the guest does: every failure becomes a :class:`ScriptOutcome` with one of the
four error kinds (spike result §4.6).

Threads (spike result §4.1 host rules, §4.2 step 4):

- One I/O thread owns every pipe. All pipe I/O is non-blocking, and the
  deadline and the cancel event are checked on every loop iteration, so a
  guest that floods output or never reads its stdin cannot stall the host.
  It validates every guest line (untrusted) and enforces the line, output and
  stderr caps. Replies are encoded and bounded on the calling thread. On any protocol violation, cap, deadline or cancel it kills the
  worker's process group at once and reports the outcome.
- The calling (session) thread starts each tool call and runs the discovery
  callback. The I/O thread hands it each ``call`` and ``discover`` through a
  queue, in the guest's order; the calling thread starts the call (or runs
  the callback) and posts the reply back once it settled. Several requests
  may be outstanding, at most :data:`.prelude.MAX_PENDING_REQUESTS`, and
  replies go back in the order the calls settle.

The worker's process group is the only thing ever signalled, and only while
its leader is unreaped (a reaped pid may be reused). Nothing in the guest can
fork, so the group contains only the worker.
"""

from __future__ import annotations

import collections
import json
import math
import os
import queue
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.native.codemode.declarations import to_codemode_identifier
from pipy_harness.native.codemode.outcome import (
    CallResult,
    CallStatus,
    DiscoveryResult,
    ErrorKind,
    ScriptError,
    ScriptImage,
    ScriptLimits,
    ScriptOutcome,
    ScriptTool,
    StoreWrites,
    ToolCallRecord,
)
from pipy_harness.native.codemode.prelude import (
    DISCOVERY_NAMES,
    EXIT_PROTOCOL,
    MAX_PENDING_REQUESTS,
    MAX_STORE_TOTAL_CHARS,
    MAX_STORE_VALUE_CHARS,
    image_type,
    is_base64,
    store_json,
    tool_table,
    unknown_tool_message,
)
from pipy_harness.native.codemode.runtime import (
    RUNTIME_PIN,
    CodemodePaths,
    RuntimePin,
    default_paths,
)
from pipy_harness.native.codemode.selftest import WORKER_PATH

ActivityWaiter = Callable[[threading.Event, threading.Event], None]

ToolCallback = Callable[[str, str], CallResult]
"""``(tool name, arguments as a JSON object string) -> CallResult``."""

ToolStarter = Callable[[str, str, Callable[[CallResult], None]], None]
"""``(tool name, arguments as a JSON object string, settle)``: start one call
and return; ``settle(result)`` is then called once, on the calling thread,
at once or later (from the activity waiter, or after the run ended)."""

DiscoverCallback = Callable[[str, dict[str, Any]], DiscoveryResult]
"""``(helper name, its arguments by parameter name) -> DiscoveryResult``.

The helper name is one of ``search_tools``, ``describe_tool`` and
``describe_namespace``. The arguments come from the guest unchecked: any JSON
values, and any or none of the helper's parameters."""

# The backend the worker must report in its ``ready`` line (worker.BACKEND;
# the host never imports the worker module).
BACKEND = "wasi"
POLL_SECONDS = 0.02
REAP_TIMEOUT_SECONDS = 5.0
JOIN_TIMEOUT_SECONDS = 5.0
STATUS_LIMIT = 64 * 1024
READ_CHUNK = 64 * 1024

# A lone UTF-16 surrogate (JSON ``"\\ud800"``) parses into a Python ``str`` that
# cannot be encoded as UTF-8; every guest string is scrubbed to U+FFFD.
_SURROGATE = re.compile("[\ud800-\udfff]")

# How the prelude's image line starts, and the room for the rest of its frame.
_IMAGE_LINE = b'{"type":"image",'
_IMAGE_FRAME = 1024
# How the prelude's done line starts. With store writes it may be longer than
# other lines: each character of a key or of a value's JSON text takes at
# most 12 bytes on the wire (an escaped astral character), and each entry at
# most 8 more. The values set are at most ``MAX_STORE_TOTAL_CHARS``; the keys
# deleted are keys of the snapshot.
_DONE_LINE = b'{"type":"done",'
_STORE_BYTES_PER_CHAR = 20

ABORTED_MESSAGE = "Execution aborted"
OUTPUT_GUIDANCE = (
    "the script's output exceeded {limit}; print a summary and write large "
    "data through a tool instead"
)


def run_script(
    code: str,
    *,
    call_tool: ToolCallback | None = None,
    start_tool: ToolStarter | None = None,
    tools: Iterable[ScriptTool],
    discover: DiscoverCallback | None = None,
    limits: ScriptLimits | None = None,
    cancel: threading.Event | None = None,
    tool_stop: threading.Event | None = None,
    paths: CodemodePaths | None = None,
    pin: RuntimePin = RUNTIME_PIN,
    python: str = sys.executable,
    activity_waiter: ActivityWaiter | None = None,
    store: Mapping[str, Any] | None = None,
) -> ScriptOutcome:
    """Run ``code`` in a fresh worker and return its outcome; never raises.

    Exactly one of ``call_tool`` and ``start_tool`` runs the script's tool
    calls, on this (the calling) thread and only for the names of ``tools``;
    any other name gets an error result without reaching it. ``call_tool``
    runs a call to its end, so calls run one at a time; ``start_tool``
    starts it and settles it later, so the calls a script submits run at
    the same time. Calls are started in the order the script made them, and
    the outcome's ``calls`` keep that order; a call that did not settle
    before the run ended is recorded as cancelled. After a ``done`` the
    calls the script made before it are still started; after any other end
    they are not. ``tools`` is ordered: the script sees it as
    ``ALL_TOOLS``, and the first tool wins an identifier two names share.

    ``discover`` answers the script's ``search_tools``, ``describe_tool`` and
    ``describe_namespace`` calls, on this thread too. It executes no tool and
    its calls are not part of the outcome's ``calls``. Without it the helpers
    raise in the script. It must honour ``cancel`` and ``tool_stop`` itself:
    the runner cannot interrupt a callback. ``tool_stop`` (if given) is set as
    soon as the run ends (deadline, violation, cancel), and after a ``done``
    once the calls made before it were started, so a call still in flight
    can stop its tool. It must be a fresh event
    per run: one already set, or in use by another run (a nested run from a
    callback, say), is refused with a sandbox error and left untouched.
    Setting ``cancel`` ends the run as ``aborted``.

    ``activity_waiter``, when supplied, runs on the calling thread while the
    guest computes. It waits for the activity event (queued call or finish)
    or signals cancel, without reading worker pipes. Publication and empty
    acknowledgement share one lock. Every I/O exit publishes completion and
    wakes activity, including failures in completion reporting. Omitting it
    retains standalone polling. KeyboardInterrupt during either wait sets
    cancel and returns an aborted outcome; parent-turn state belongs to callers.

    ``store`` is what the script's ``load()`` reads, by key (JSON values; a
    value that is not JSON is left out). A script that succeeds reports what
    it changed with ``store()`` as the outcome's ``store_writes``; the host
    checks them against the store's limits, and keeping them is the caller's
    job.

    Availability (:func:`.selftest.availability`) is the caller's job; the
    runner still fails closed: the worker re-checks ``python.wasm`` against the
    manifest and must report the expected backend and pinned runtime hash.
    """

    if (call_tool is None) == (start_tool is None):
        raise TypeError("run_script takes exactly one of call_tool and start_tool")
    if start_tool is None:
        assert call_tool is not None
        start_tool = _starter(call_tool)
    if tool_stop is not None and not _claim_tool_stop(tool_stop):
        return ScriptOutcome(
            ScriptError(
                ErrorKind.SANDBOX,
                "tool_stop must be a fresh event that no other run uses",
            )
        )
    try:
        return _run_script(
            code,
            start_tool,
            tools,
            discover,
            limits or ScriptLimits(),
            cancel,
            tool_stop,
            paths,
            pin,
            python,
            activity_waiter,
            _store_snapshot(store or {}),
        )
    finally:
        if tool_stop is not None:
            tool_stop.set()
            _release_tool_stop(tool_stop)


_tool_stops_lock = threading.Lock()
_tool_stops_in_use: set[threading.Event] = set()


def _claim_tool_stop(tool_stop: threading.Event) -> bool:
    with _tool_stops_lock:
        if tool_stop.is_set() or tool_stop in _tool_stops_in_use:
            return False
        _tool_stops_in_use.add(tool_stop)
        return True


def _release_tool_stop(tool_stop: threading.Event) -> None:
    with _tool_stops_lock:
        _tool_stops_in_use.discard(tool_stop)


def _starter(call_tool: ToolCallback) -> ToolStarter:
    """``call_tool`` as a starter that settles each call before it returns."""

    def start(
        name: str, arguments_json: str, settle: Callable[[CallResult], None]
    ) -> None:
        settle(call_tool(name, arguments_json))

    return start


def _run_script(
    code: str,
    start_tool: ToolStarter,
    tools: Iterable[ScriptTool],
    discover: DiscoverCallback | None,
    limits: ScriptLimits,
    cancel: threading.Event | None,
    tool_stop: threading.Event | None,
    paths: CodemodePaths | None,
    pin: RuntimePin,
    python: str,
    activity_waiter: ActivityWaiter | None,
    snapshot: dict[str, str],
) -> ScriptOutcome:
    cancel = cancel or threading.Event()
    started = time.monotonic()
    early = _precheck(code, limits, cancel)
    if early is not None:
        return ScriptOutcome(early, wall_seconds=time.monotonic() - started)
    records = _tool_records(tools)
    run_line = _encode(
        {"type": "run", "code": code, "tools": records, "store": snapshot}
    )
    calls: list[ToolCallRecord] = []
    finished = _launch(
        _Launch(
            run_line=run_line,
            request=_run_request(paths or default_paths(), pin, limits),
            python=python,
            limits=limits,
            pin=pin,
            names=frozenset(record["name"] for record in records),
            identifiers=tuple(entry["name"] for entry in tool_table(records)[1]),
            deadline=started + limits.wall_seconds,
            cancel=cancel,
            tool_stop=tool_stop,
            snapshot=snapshot,
        ),
        _Callbacks(start_tool, discover),
        calls,
        activity_waiter,
    )
    return ScriptOutcome(
        finished.error,
        output=finished.output,
        calls=tuple(calls),
        wall_seconds=time.monotonic() - started,
        diagnostics=finished.diagnostics,
        store_writes=finished.store_writes if finished.error is None else StoreWrites(),
    )


def _store_snapshot(store: Mapping[str, Any]) -> dict[str, str]:
    """The run request's store: each value as the JSON text ``store()``
    writes. A key that is not a string or a value that is not JSON is left
    out (Pi ``serializeStore`` leaves out what does not serialize)."""

    snapshot: dict[str, str] = {}
    for key, value in store.items():
        if not isinstance(key, str):
            continue
        try:
            snapshot[key] = store_json(value)
        except (TypeError, ValueError, RecursionError):
            continue
    return snapshot


def _tool_records(tools: Iterable[ScriptTool]) -> list[dict[str, str]]:
    """The run request's tool records: the first entry of a name, in order."""

    records: dict[str, dict[str, str]] = {}
    for tool in tools:
        records.setdefault(
            tool.name,
            {
                "name": tool.name,
                "id": to_codemode_identifier(tool.name),
                "description": tool.description,
            },
        )
    return list(records.values())


def _precheck(
    code: str, limits: ScriptLimits, cancel: threading.Event
) -> ScriptError | None:
    size = len(code.encode("utf-8", "surrogatepass"))
    if size > limits.code_bytes:
        return ScriptError(
            ErrorKind.SCRIPT,
            f"the script is {size} bytes, more than the {limits.code_bytes}-byte limit",
        )
    if cancel.is_set():
        return ScriptError(ErrorKind.ABORTED, ABORTED_MESSAGE)
    return None


def _run_request(paths: CodemodePaths, pin: RuntimePin, limits: ScriptLimits) -> str:
    return json.dumps(
        {
            "runtime_dir": str(paths.runtime_dir(pin)),
            "cache_dir": str(paths.cache_dir),
            "memory_bytes": limits.memory_bytes,
            "cpu_seconds": limits.cpu_seconds,
            "host_pid": os.getpid(),
        }
    )


def _worker_argv(python: str, status_fd: int, request: str) -> list[str]:
    """The worker command line (tests replace this with a fake worker)."""

    return [python, "-I", str(WORKER_PATH), "run", str(status_fd), request]


def _encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


def _has_only_string_keys(value: object) -> bool:
    """For a value ``json.dumps`` accepted (so it is finite and acyclic)."""

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return True


def _error_reply(call_id: int, text: str) -> bytes:
    return _encode({"type": "result", "id": call_id, "ok": False, "error": text})


def _encode_reply(call_id: int, name: str, result: CallResult, limit: int) -> bytes:
    """The reply line for one settled call; never raises.

    A reply carries the tool's ``text``, or its structured ``value`` as JSON,
    or an ``error`` that the guest raises as ``ToolError``. A value that is
    not JSON, or a reply longer than ``limit``, is replaced by an error: the
    tool has run, and the script learns why it got no result.
    """

    head: dict[str, Any] = {"type": "result", "id": call_id, "ok": True}
    if result.value is not None:
        try:
            line = (
                json.dumps(
                    head | {"value": result.value},
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            # After encoding, which refuses a cyclic value: the encoder turns
            # the key 1 into "1" and would send duplicate keys.
            if not _has_only_string_keys(result.value):
                raise TypeError("object keys must be strings")
        except (TypeError, ValueError, RecursionError) as exc:
            return _error_reply(
                call_id,
                f'Tool "{name}" returned a structured result that is not JSON: '
                f"{type(exc).__name__}",
            )
    elif result.ok:
        line = _encode(head | {"text": str(result.text)})
    else:
        line = _error_reply(call_id, str(result.text))
    if len(line) > limit:
        return _error_reply(
            call_id,
            f'Tool "{name}" returned a result of {len(line)} bytes, more than the '
            f"{limit}-byte limit for one result; ask the tool for less",
        )
    return line


def _encode_discovery_reply(
    call_id: int, name: str, result: DiscoveryResult, limit: int
) -> bytes:
    """The reply line for one discovery call; never raises."""

    if result.error is not None:
        line = _error_reply(call_id, str(result.error))
    else:
        try:
            line = (
                json.dumps(
                    {
                        "type": "result",
                        "id": call_id,
                        "ok": True,
                        "value": result.value,
                    },
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            return _error_reply(
                call_id,
                f"{name}() returned a value that is not JSON: {type(exc).__name__}",
            )
    if len(line) > limit:
        return _error_reply(
            call_id,
            f"{name}() returned a result of {len(line)} bytes, more than the "
            f"{limit}-byte limit for one result; narrow the search",
        )
    return line


@dataclass(frozen=True, slots=True)
class _Callbacks:
    start_tool: ToolStarter
    discover: DiscoverCallback | None


@dataclass(frozen=True, slots=True)
class _Launch:
    run_line: bytes
    request: str
    python: str
    limits: ScriptLimits
    pin: RuntimePin
    names: frozenset[str]
    # What scripts write for the tools, in ``ALL_TOOLS`` order.
    identifiers: tuple[str, ...]
    deadline: float
    cancel: threading.Event
    tool_stop: threading.Event | None
    # What ``load()`` reads: each key's value as JSON text.
    snapshot: dict[str, str]

    @property
    def done_line_bytes(self) -> int:
        """How long a ``done`` line, with its store writes, may be."""

        chars = sum(len(key) + len(text) for key, text in self.snapshot.items())
        return self.limits.line_bytes + _STORE_BYTES_PER_CHAR * (
            MAX_STORE_TOTAL_CHARS + chars
        )


@dataclass(frozen=True, slots=True)
class _Call:
    id: int
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class _Discovery:
    id: int
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Finished:
    error: ScriptError | None
    output: tuple[str | ScriptImage, ...] = ()
    diagnostics: str = ""
    store_writes: StoreWrites = StoreWrites()


@dataclass(frozen=True, slots=True)
class _Reply:
    id: int
    line: bytes


@dataclass(frozen=True, slots=True)
class _StopRequest:
    error: ScriptError


def _launch(
    launch: _Launch,
    callbacks: _Callbacks,
    calls: list[ToolCallRecord],
    activity_waiter: ActivityWaiter | None,
) -> _Finished:
    try:
        status_read, status_write = os.pipe()
    except OSError as exc:
        return _start_failed(exc)
    worker: _Worker | None = None
    channel: _Channel | None = None
    try:
        try:
            worker = _Worker(
                _worker_argv(launch.python, status_write, launch.request), status_write
            )
        except OSError as exc:
            return _start_failed(exc)
        finally:
            os.close(status_write)
        try:
            channel = _Channel(launch, worker, status_read)
            channel.start()
        except (OSError, RuntimeError) as exc:  # no fd for the wake pipe, no thread
            return _start_failed(exc)
        return _serve(channel, callbacks, launch.cancel, calls, activity_waiter)
    finally:
        _cleanup(worker, channel, status_read)


def _start_failed(exc: Exception) -> _Finished:
    return _Finished(
        ScriptError(ErrorKind.SANDBOX, f"Failed to start the worker: {exc}")
    )


def _cleanup(
    worker: _Worker | None, channel: _Channel | None, status_read: int
) -> None:
    """Kill, reap, stop the I/O thread, then close every pipe (§4.1 order)."""

    if channel is not None:
        channel.stop(ScriptError(ErrorKind.ABORTED, ABORTED_MESSAGE))
    if worker is not None:
        worker.reap()
    if channel is not None and not channel.join():
        # The I/O thread still uses the pipes; closing them now could hand a
        # reused fd number to it. Leaking them is the lesser harm.
        return
    if worker is not None:
        worker.close_pipes()
    if channel is not None:
        channel.close()
    os.close(status_read)


def _serve(
    channel: _Channel,
    callbacks: _Callbacks,
    cancel: threading.Event,
    calls: list[ToolCallRecord],
    activity_waiter: ActivityWaiter | None,
) -> _Finished:
    """Run the callbacks on this thread until the I/O thread finishes."""

    while True:
        event = _take_live_activity(channel)
        if event is None:
            try:
                if activity_waiter is None or cancel.is_set():
                    channel.activity.wait(POLL_SECONDS)
                else:
                    activity_waiter(channel.activity, cancel)
            except KeyboardInterrupt:
                cancel.set()
            except Exception as exc:  # noqa: BLE001 - waiter failure still reaps the worker
                channel.stop(
                    ScriptError(
                        ErrorKind.SANDBOX,
                        f"activity waiter failed: {type(exc).__name__}: {exc}",
                    )
                )
                # Do not invoke the failing callback again during cleanup.
                activity_waiter = None
            continue
        if isinstance(event, _Finished):
            if event.error is None and channel.stopped_with is not None:
                # Stopped on this thread after the I/O thread saw the done,
                # while the calls made before it were started. A failed run
                # keeps no store writes.
                return _Finished(channel.stopped_with, event.output, event.diagnostics)
            return event
        if cancel.is_set() or (channel.finished.is_set() and not channel.done):
            continue  # the run is over; its _Finished follows
        if isinstance(event, _Discovery):
            _run_discovery(channel, callbacks.discover, cancel, event)
        else:
            _start_call(channel, callbacks.start_tool, cancel, calls, event)


def _take_live_activity(channel: _Channel) -> _Call | _Discovery | _Finished | None:
    event = channel.take_activity()
    if event is not None or channel.alive():
        return event
    # Completion may have been published after our empty read. A dead thread
    # without it is a host failure, never a reason to wait for more activity.
    return channel.take_activity() or _Finished(
        ScriptError(ErrorKind.SANDBOX, "host I/O exited without completion")
    )


def _start_call(
    channel: _Channel,
    start_tool: ToolStarter,
    cancel: threading.Event,
    calls: list[ToolCallRecord],
    call: _Call,
) -> None:
    """Start one call. Its record takes its place now, as cancelled until
    the call settles (Pi records a call as it starts)."""

    index = len(calls)
    calls.append(ToolCallRecord(call.name, CallStatus.CANCELLED))
    settled = False

    def failed(exc: Exception) -> None:
        if calls[index].status is CallStatus.CANCELLED:
            calls[index] = ToolCallRecord(call.name, CallStatus.ERROR)
        channel.stop(
            ScriptError(
                ErrorKind.SANDBOX,
                f"the {call.name} tool callback failed: {type(exc).__name__}: {exc}",
            )
        )

    def settle(result: CallResult) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        if not isinstance(result, CallResult):
            failed(TypeError(f"returned {type(result).__name__}, not CallResult"))
            return
        if result.cancelled:
            # A call the end of the run cancelled does not change its outcome.
            if not channel.finished.is_set():
                channel.stop(ScriptError(ErrorKind.ABORTED, ABORTED_MESSAGE))
            return
        calls[index] = ToolCallRecord(
            call.name, CallStatus.OK if result.ok else CallStatus.ERROR
        )
        if cancel.is_set():
            return  # the I/O thread ends the run as aborted; no reply
        # Encoded here, on the calling thread: the I/O thread only copies bytes.
        channel.reply(call.id, call.name, result)

    try:
        start_tool(call.name, call.arguments_json, settle)
    except Exception as exc:  # noqa: BLE001 - the runner never raises; a failing callback is a sandbox error
        settled = True
        failed(exc)


def _run_discovery(
    channel: _Channel,
    discover: DiscoverCallback | None,
    cancel: threading.Event,
    request: _Discovery,
) -> None:
    """Answer one discovery call; it runs no tool and leaves no call record."""

    try:
        if discover is None:
            result = DiscoveryResult(error=f"{request.name}() is not available")
        else:
            result = discover(request.name, request.args)
            if not isinstance(result, DiscoveryResult):
                raise TypeError(
                    f"returned {type(result).__name__}, not DiscoveryResult"
                )
    except Exception as exc:  # noqa: BLE001 - the runner never raises; a failing callback is a sandbox error
        channel.stop(
            ScriptError(
                ErrorKind.SANDBOX,
                f"the {request.name} discovery callback failed: "
                f"{type(exc).__name__}: {exc}",
            )
        )
        return
    if cancel.is_set():
        return  # the I/O thread ends the run as aborted; no reply
    channel.reply_discovery(request.id, request.name, result)


class _Worker:
    """The worker process; signals only its own group, only while unreaped."""

    def __init__(self, argv: list[str], status_write: int) -> None:
        self.process = subprocess.Popen(  # noqa: S603 - fixed argv: pipy's interpreter and its own worker file
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            pass_fds=(status_write,),
            env={"PATH": os.environ.get("PATH", os.defpath)},
            start_new_session=True,
        )
        self._lock = threading.Lock()
        self._reaped = False
        for stream in self._streams():
            os.set_blocking(stream.fileno(), False)

    def _streams(self) -> list[Any]:
        process = self.process
        return [s for s in (process.stdin, process.stdout, process.stderr) if s]

    def fd(self, name: str) -> int:
        stream = getattr(self.process, name)
        assert stream is not None
        return int(stream.fileno())

    def kill(self) -> None:
        with self._lock:
            if not self._reaped:
                self._killpg()

    def _killpg(self) -> None:
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            # A negative pid addresses the same owned process group. A signal
            # fault on the primary API must not skip kill and reap.
            try:
                os.kill(-self.process.pid, signal.SIGKILL)
            except OSError:
                pass  # Still attempt the bounded wait when signalling is denied.

    def reap(self) -> None:
        with self._lock:
            if self._reaped:
                return
            self._killpg()
            try:
                self.process.wait(timeout=REAP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return
            self._reaped = True

    def close_pipes(self) -> None:
        for stream in self._streams():
            stream.close()


class _Stop(Exception):  # noqa: N818 - control flow, not an error
    """Unwinds the I/O loop with the run's outcome (``None`` is success)."""

    def __init__(self, error: ScriptError | None) -> None:
        super().__init__()
        self.error = error


def _violation(reason: str) -> _Stop:
    return _Stop(ScriptError(ErrorKind.SANDBOX, f"protocol violation: {reason}"))


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} overflows a float")
    return value


class _Channel:
    """The I/O thread: owns the pipes, validates guest lines, enforces caps."""

    def __init__(self, launch: _Launch, worker: _Worker, status_read: int) -> None:
        self._launch = launch
        self._limits = launch.limits
        self._worker = worker
        self._stdin = worker.fd("stdin")
        self._stdout = worker.fd("stdout")
        self._stderr = worker.fd("stderr")
        self._status = status_read
        os.set_blocking(status_read, False)
        self._wake_read, self._wake_write = os.pipe()
        os.set_blocking(self._wake_read, False)
        os.set_blocking(self._wake_write, False)
        self.inbox: queue.SimpleQueue[_Call | _Discovery | _Finished] = (
            queue.SimpleQueue()
        )
        self.finished = threading.Event()
        # Whether the run ended with the guest's ``done``; read once
        # ``finished`` is set.
        self.done = False
        self.activity = threading.Event()
        self._activity_lock = threading.Lock()
        self._commands: queue.SimpleQueue[_Reply | _StopRequest] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run, name="codemode-io", daemon=True
        )
        self._started = False
        self._closed = False
        # The first stop the calling thread asked for.
        self.stopped_with: ScriptError | None = None
        # After a done: sets ``tool_stop`` at the deadline (see ``_run``).
        self._deadline_stop: threading.Timer | None = None
        # Everything below is owned by the I/O thread alone.
        self._out = bytearray(launch.run_line)
        # What ``_out`` holds, in order: [bytes left, whether it is a reply].
        self._out_parts: collections.deque[list[int]] = collections.deque(
            [[len(launch.run_line), 0]]
        )
        self._stdin_open = True
        self._line = bytearray()
        self._status_buf = bytearray()
        self._stderr_tail = bytearray()
        self._output: list[str | ScriptImage] = []
        self._output_bytes = 0
        self._image_bytes = 0
        # A request stays outstanding until its reply has been written in
        # full: the ids awaiting a reply, and the replies not written yet.
        # Their sum is capped, so the reply buffer stays bounded however
        # many calls a guest that does not read makes.
        self._awaiting: set[int] = set()
        self._unwritten = 0
        self._handoffs: list[_Call | _Discovery] = []
        self._next_id = 1
        self._ready = False
        self._exit: dict[str, Any] | None = None
        self._open = {"out": True, "status": True}
        self._done_line_bytes = launch.done_line_bytes
        # The writes of a successful ``done``.
        self._store_writes = StoreWrites()

    # --- the calling thread's side -------------------------------------------

    def post_activity(self, event: _Call | _Discovery | _Finished) -> None:
        # Publication and empty-queue acknowledgement share this lock. A post
        # cannot slip between the consumer's empty check and event clear.
        with self._activity_lock:
            self.inbox.put(event)
            self.activity.set()

    def take_activity(self) -> _Call | _Discovery | _Finished | None:
        with self._activity_lock:
            try:
                return self.inbox.get_nowait()
            except queue.Empty:
                self.activity.clear()
                return None

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def alive(self) -> bool:
        return self._thread.is_alive()

    def reply(self, call_id: int, name: str, result: CallResult) -> None:
        line = _encode_reply(call_id, name, result, self._limits.reply_bytes)
        self._commands.put(_Reply(call_id, line))
        self._wake()

    def reply_discovery(self, call_id: int, name: str, result: DiscoveryResult) -> None:
        line = _encode_discovery_reply(call_id, name, result, self._limits.reply_bytes)
        self._commands.put(_Reply(call_id, line))
        self._wake()

    def stop(self, error: ScriptError) -> None:
        """Ask the I/O thread to end the run; a no-op once it has finished,
        except that the run's outcome then reports it if it was a success."""

        if self.stopped_with is None:
            self.stopped_with = error
        self._commands.put(_StopRequest(error))
        self._wake()

    def join(self) -> bool:
        if not self._started:
            return True
        self._thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        return not self._thread.is_alive()

    def close(self) -> None:
        self._closed = True
        if self._deadline_stop is not None:
            self._deadline_stop.cancel()
        os.close(self._wake_read)
        os.close(self._wake_write)

    def _wake(self) -> None:
        if self._closed:
            return  # a call settled after the run: its fd may be reused
        try:
            os.write(self._wake_write, b"x")
        except (BlockingIOError, OSError):
            pass  # already awake (pipe full) or closed after the run

    # --- the I/O thread -------------------------------------------------------

    def _run(self) -> None:
        error: ScriptError | None = ScriptError(
            ErrorKind.SANDBOX, "host I/O exited without completion"
        )
        diagnostics = ""
        try:
            try:
                error = self._loop()
            except BaseException as exc:  # noqa: BLE001 - loop failures still kill immediately
                error = ScriptError(
                    ErrorKind.SANDBOX, f"host I/O failed: {type(exc).__name__}: {exc}"
                )
            self._worker.kill()
            diagnostics = self._diagnostics()
        except BaseException as exc:  # noqa: BLE001 - every I/O exit must signal the session and active tool
            error = ScriptError(
                ErrorKind.SANDBOX, f"host I/O failed: {type(exc).__name__}: {exc}"
            )
        finally:
            try:
                # After a ``done`` the calling thread first starts the calls
                # made before it, then the runner sets ``tool_stop``; the
                # deadline still sets it, should starting them take that long.
                stops: tuple[threading.Event | None, ...] = (
                    self.finished,
                    self._launch.tool_stop,
                )
                if self.done and self._launch.tool_stop is not None:
                    try:
                        self._deadline_stop = threading.Timer(
                            max(self._launch.deadline - time.monotonic(), 0),
                            self._launch.tool_stop.set,
                        )
                        self._deadline_stop.daemon = True
                        self._deadline_stop.start()
                        stops = (self.finished,)
                    except BaseException:  # noqa: BLE001 - no timer: stop at once
                        self._deadline_stop = None
                for event in stops:
                    if event is None:
                        continue
                    try:
                        event.set()
                    except BaseException as exc:  # noqa: BLE001 - signal faults cannot suppress completion
                        error = ScriptError(
                            ErrorKind.SANDBOX,
                            f"host I/O failed: {type(exc).__name__}: {exc}",
                        )
                        # These are host-owned Events. Bypass a failing setter
                        # so an active callback still observes the stop signal.
                        threading.Event.set(event)
            finally:
                # No diagnostics, process signals or overridable publication
                # helper here. Use the consumer's acknowledgement lock.
                with self._activity_lock:
                    self.inbox.put(
                        _Finished(
                            error,
                            tuple(self._output),
                            diagnostics,
                            self._store_writes if error is None else StoreWrites(),
                        )
                    )
                    self.activity.set()

    def _loop(self) -> ScriptError | None:
        selector = selectors.DefaultSelector()
        selector.register(self._stdout, selectors.EVENT_READ, "out")
        selector.register(self._stderr, selectors.EVENT_READ, "err")
        selector.register(self._status, selectors.EVENT_READ, "status")
        selector.register(self._wake_read, selectors.EVENT_READ, "wake")
        try:
            while True:
                self._take_commands()
                self._check_time()
                if not any(self._open.values()):
                    raise _Stop(self._exit_error())
                self._watch_stdin(selector)
                timeout = min(POLL_SECONDS, self._launch.deadline - time.monotonic())
                events = selector.select(timeout=max(timeout, 0))
                # Status first: ``ready`` precedes the guest's first line.
                for key, _ in sorted(events, key=lambda e: e[0].data != "status"):
                    self._service(selector, key)
        except _Stop as stop:
            return stop.error
        finally:
            selector.close()

    def _take_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(command, _StopRequest):
                raise _Stop(command.error)
            self._queue_reply(command.id, command.line)

    def _check_time(self) -> None:
        if self._launch.cancel.is_set():
            raise _Stop(ScriptError(ErrorKind.ABORTED, ABORTED_MESSAGE))
        if time.monotonic() >= self._launch.deadline:
            raise _Stop(
                ScriptError(
                    ErrorKind.TIMEOUT,
                    f"Execution timed out after {self._limits.wall_seconds:g} s",
                )
            )

    def _watch_stdin(self, selector: selectors.BaseSelector) -> None:
        registered = self._stdin in selector.get_map()
        wanted = bool(self._out) and self._stdin_open
        if wanted and not registered:
            selector.register(self._stdin, selectors.EVENT_WRITE, "in")
        elif registered and not wanted:
            selector.unregister(self._stdin)

    def _service(
        self, selector: selectors.BaseSelector, key: selectors.SelectorKey
    ) -> None:
        if key.data == "in":
            self._write_stdin()
            return
        try:
            chunk = os.read(key.fd, READ_CHUNK)
        except BlockingIOError:
            return
        if key.data == "wake":
            return
        if not chunk:
            selector.unregister(key.fd)
            self._open[key.data] = False
            return
        if key.data == "err":
            self._keep_stderr(chunk)
        elif key.data == "status":
            self._feed_status(chunk)
        else:
            self._feed_output(chunk)

    def _write_stdin(self) -> None:
        try:
            written = os.write(self._stdin, self._out)
        except BlockingIOError:
            return
        except OSError:
            # The guest closed its stdin or died; its stdout EOF follows.
            self._out.clear()
            self._out_parts.clear()
            self._unwritten = 0
            self._stdin_open = False
            return
        del self._out[:written]
        while written:
            part = self._out_parts[0]
            taken = min(written, part[0])
            part[0] -= taken
            written -= taken
            if part[0] == 0:
                self._out_parts.popleft()
                self._unwritten -= part[1]

    def _keep_stderr(self, chunk: bytes) -> None:
        self._stderr_tail += chunk
        excess = len(self._stderr_tail) - self._limits.stderr_bytes
        if excess > 0:
            del self._stderr_tail[:excess]

    # --- guest lines ------------------------------------------------------------

    def _feed_output(self, chunk: bytes) -> None:
        view = memoryview(chunk)
        start = 0
        try:
            while (newline := chunk.find(b"\n", start)) >= 0:
                self._append_line(view[start:newline])
                line = bytes(self._line)
                self._line.clear()
                self._handle_line(line)
                start = newline + 1
            self._append_line(view[start:])
        except _Stop:
            # The calls before a ``done`` were made; a violation or a cap in
            # their chunk ends the run before any of them runs.
            if self.done:
                self._post_handoffs()
            raise
        self._post_handoffs()

    def _post_handoffs(self) -> None:
        """Hand the requests of an accepted chunk to the calling thread."""
        for request in self._handoffs:
            self.post_activity(request)
        self._handoffs.clear()

    def _append_line(self, piece: memoryview) -> None:
        size = len(self._line) + len(piece)
        if size > self._limits.line_bytes:
            # Only an image line and a done line may be longer: as long as
            # the image cap or the store's limits allow. What the line is
            # is decided when it is parsed.
            head = bytes(self._line[: len(_IMAGE_LINE)])
            head += bytes(piece[: len(_IMAGE_LINE) - len(head)])
            kind = (
                "image"
                if head == _IMAGE_LINE
                else "done"
                if head.startswith(_DONE_LINE)
                else None
            )
            if size > self._line_limit(kind):
                raise _violation(
                    f"a guest line exceeded {self._limits.line_bytes} bytes"
                )
        self._line += piece

    def _line_limit(self, kind: object) -> int:
        """How long a guest line of this ``type`` may be."""

        if kind == "image":
            return self._limits.image_bytes + _IMAGE_FRAME
        if kind == "done":
            return self._done_line_bytes
        return self._limits.line_bytes

    def _handle_line(self, line: bytes) -> None:
        try:
            message = _parse_guest_line(line)
        except (ValueError, RecursionError):
            raise _violation("the guest sent a line that is not JSON") from None
        if not isinstance(message, dict):
            raise _violation("the guest sent a line that is not a JSON object")
        kind = message.get("type")
        if len(line) > max(self._limits.line_bytes, self._line_limit(kind)):
            # The longer allowances are an image's and a done's: a line that
            # only started like one (a second ``type`` key wins in JSON) gets
            # the limit its own type has.
            raise _violation(f"a guest line exceeded {self._limits.line_bytes} bytes")
        if kind == "text":
            self._on_text(message)
        elif kind == "image":
            self._on_image(message)
        elif kind == "call":
            self._on_call(message)
        elif kind == "discover":
            self._on_discover(message)
        elif kind == "done":
            self._on_done(message)
        else:
            raise _violation("the guest sent a message of unknown type")

    def _on_text(self, message: dict[str, Any]) -> None:
        value = message.get("value")
        if not isinstance(value, str):
            raise _violation("a text message had no string value")
        size = len(value.encode("utf-8"))
        if self._output_bytes + size > self._limits.output_bytes:
            raise self._output_capped(f"{self._limits.output_bytes} bytes")
        if len(self._output) >= self._limits.output_messages:
            raise self._output_capped(f"{self._limits.output_messages} messages")
        self._output.append(value)
        self._output_bytes += size

    def _on_image(self, message: dict[str, Any]) -> None:
        data = message.get("data")
        mime_type = message.get("mimeType")
        # The prelude checks an image before it sends it; a line that fails
        # the same check did not come from the prelude.
        if (
            not isinstance(data, str)
            or not isinstance(mime_type, str)
            or not is_base64(data)
            or image_type(data) != mime_type
        ):
            raise _violation("the guest sent an image message that is not an image")
        if self._image_bytes + len(data) > self._limits.image_bytes:
            raise self._output_capped(f"{self._limits.image_bytes} bytes of images")
        if len(self._output) >= self._limits.output_messages:
            raise self._output_capped(f"{self._limits.output_messages} messages")
        self._output.append(ScriptImage(data, mime_type))
        self._image_bytes += len(data)

    @staticmethod
    def _output_capped(limit: str) -> _Stop:
        return _Stop(ScriptError(ErrorKind.SCRIPT, OUTPUT_GUIDANCE.format(limit=limit)))

    def _on_call(self, message: dict[str, Any]) -> None:
        call_id, name, args = self._request(message, "tool")
        try:
            arguments_json = json.dumps(args, ensure_ascii=False, allow_nan=False)
        except ValueError:  # defensive: parsing already rejects non-finite numbers
            raise _violation("a tool call's arguments are not JSON") from None
        self._awaiting.add(call_id)
        self._next_id += 1
        if name not in self._launch.names:
            message_text = unknown_tool_message(name, self._launch.identifiers)
            self._queue_reply(call_id, _error_reply(call_id, message_text))
            return
        self._handoffs.append(_Call(call_id, name, arguments_json))

    def _on_discover(self, message: dict[str, Any]) -> None:
        call_id, name, args = self._request(message, "discovery")
        if name not in DISCOVERY_NAMES:
            raise _violation("a discovery call named an unknown helper")
        self._awaiting.add(call_id)
        self._next_id += 1
        self._handoffs.append(_Discovery(call_id, name, args))

    def _request(
        self, message: dict[str, Any], kind: str
    ) -> tuple[int, str, dict[str, Any]]:
        """The id, name and arguments of a ``call`` or ``discover`` message."""

        if not self._ready:
            raise _violation(f"a {kind} call arrived before the worker was ready")
        call_id = message.get("id")
        name = message.get("name")
        args = message.get("args")
        if type(call_id) is not int or call_id != self._next_id:
            raise _violation(f"a {kind} call had an unexpected id")
        if not isinstance(name, str) or not isinstance(args, dict):
            raise _violation(f"a {kind} call was malformed")
        if len(self._awaiting) + self._unwritten >= MAX_PENDING_REQUESTS:
            raise _violation(
                f"the guest had more than {MAX_PENDING_REQUESTS} requests outstanding"
            )
        return call_id, name, args

    def _queue_reply(self, call_id: int, line: bytes) -> None:
        if call_id not in self._awaiting:
            return  # stale: the run moved on
        self._awaiting.discard(call_id)
        self._unwritten += 1
        self._out_parts.append([len(line), 1])
        self._out += line

    def _on_done(self, message: dict[str, Any]) -> None:
        # ``done`` is terminal: nothing after it is read.
        if not self._ready:
            raise _violation("the script finished before the worker was ready")
        ok = message.get("ok")
        if ok is True:
            if "writes" in message:
                self._store_writes = _store_writes(
                    message["writes"], self._launch.snapshot
                )
            self.done = True
            raise _Stop(None)
        error = message.get("error")
        if ok is False and isinstance(error, dict):
            fields = [error.get(key) for key in ("type", "message", "traceback")]
            strings = [value for value in fields if isinstance(value, str)]
            if len(strings) == len(fields):
                self.done = True
                raise _Stop(ScriptError(ErrorKind.SCRIPT, _script_message(*strings)))
        raise _violation("the guest sent a malformed done message")

    # --- worker status ----------------------------------------------------------

    def _feed_status(self, chunk: bytes) -> None:
        self._status_buf += chunk
        if len(self._status_buf) > STATUS_LIMIT:
            raise _Stop(
                ScriptError(
                    ErrorKind.SANDBOX, "the worker's status output is too large"
                )
            )
        while (newline := self._status_buf.find(b"\n")) >= 0:
            line = bytes(self._status_buf[:newline])
            del self._status_buf[: newline + 1]
            self._on_status(line)

    def _on_status(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "ready" and not self._ready and self._exit is None:
            assert isinstance(message, dict)
            if (
                message.get("backend") != BACKEND
                or message.get("runtime_sha256") != self._launch.pin.sha256
            ):
                raise _Stop(
                    ScriptError(
                        ErrorKind.SANDBOX,
                        "the worker reported an unexpected backend or runtime",
                    )
                )
            self._ready = True
        elif kind == "exit" and self._exit is None:
            assert isinstance(message, dict)
            self._exit = message
        else:
            raise _Stop(
                ScriptError(
                    ErrorKind.SANDBOX, "the worker sent a malformed status line"
                )
            )

    def _exit_error(self) -> ScriptError:
        """The outcome when the worker ended without a ``done`` line."""

        report = self._exit
        if report is None:
            return ScriptError(
                ErrorKind.SANDBOX, "the worker exited without reporting its status"
            )
        detail = report.get("detail")
        detail = detail if isinstance(detail, str) else str(report.get("reason"))
        if not self._ready:
            return ScriptError(
                ErrorKind.SANDBOX, f"the worker could not start the script: {detail}"
            )
        return _exit_report_error(report, detail, self._limits)

    def _diagnostics(self) -> str:
        parts = []
        if self._stderr_tail:
            parts.append(self._stderr_tail.decode("utf-8", "replace"))
        if self._exit is not None:
            parts.append(f"worker exit: {json.dumps(self._exit)}")
        return "\n".join(parts)


def _exit_report_error(
    report: dict[str, Any], detail: str, limits: ScriptLimits
) -> ScriptError:
    reason = report.get("reason")
    code = report.get("code")
    if reason == "trap-interrupt":
        return ScriptError(
            ErrorKind.TIMEOUT,
            f"Execution exceeded the {limits.cpu_seconds:g} s CPU backstop",
        )
    if reason == "trap-stack":
        return ScriptError(
            ErrorKind.SCRIPT,
            "the script exhausted the interpreter's stack (wasm stack overflow)",
        )
    if code == EXIT_PROTOCOL:
        return ScriptError(ErrorKind.SANDBOX, "the guest rejected a host message")
    if type(code) is int:
        return ScriptError(
            ErrorKind.SCRIPT,
            f"the script exited with status {code} before it finished",
        )
    return ScriptError(ErrorKind.SANDBOX, f"the guest stopped unexpectedly: {detail}")


def _store_writes(writes: object, snapshot: Mapping[str, str]) -> StoreWrites:
    """The ``writes`` of a ``done``, checked as the prelude's ``store()``
    checks them: every value set is JSON of at most
    ``MAX_STORE_VALUE_CHARS``, and a script that set a value leaves at most
    ``MAX_STORE_TOTAL_CHARS`` in the store. Anything else is a violation.
    A deletion of a key the snapshot lacks is dropped, as the prelude never
    reports one."""

    if not isinstance(writes, list):
        raise _violation("the guest sent malformed store writes")
    # key -> (value, JSON text) of a set, or None for a deletion; last wins.
    changes = dict(_store_change(entry) for entry in writes)
    stored = dict(snapshot)
    values: dict[str, Any] = {}
    removed: list[str] = []
    for key, change in changes.items():
        if change is None:
            # As in the prelude, deleting a key the snapshot lacks is no
            # write: the deleted keys stay bounded by the snapshot.
            if key in snapshot:
                stored.pop(key, None)
                removed.append(key)
        else:
            values[key], stored[key] = change
    if values and sum(len(k) + len(t) for k, t in stored.items()) > (
        MAX_STORE_TOTAL_CHARS
    ):
        raise _violation("the guest's store writes exceed the store's limit")
    return StoreWrites(values, tuple(removed))


def _store_change(entry: object) -> tuple[str, tuple[Any, str] | None]:
    """One entry of a ``done``'s writes: the key and its new value with the
    value's JSON text, or ``None`` for a deletion."""

    if (
        not isinstance(entry, list)
        or len(entry) not in (1, 2)
        or not all(isinstance(part, str) for part in entry)
    ):
        raise _violation("the guest sent malformed store writes")
    if len(entry) == 1:
        return entry[0], None
    try:
        value = _parse_guest_json(entry[1])
        text = store_json(value)
    except (ValueError, RecursionError):
        raise _violation("the guest stored a value that is not JSON") from None
    if len(text) > MAX_STORE_VALUE_CHARS:
        raise _violation("the guest stored a value over the store's limit")
    return entry[0], (value, text)


def _parse_guest_line(line: bytes) -> Any:
    """Parse one guest line as UTF-8 JSON; lone surrogates become U+FFFD.

    The wire is UTF-8 only: ``json.loads`` would otherwise detect UTF-16 and
    UTF-32 input on its own. Raw surrogate bytes are let through the decode
    (``surrogatepass``) and scrubbed with escaped ones, because guest strings
    flow into tool arguments, output and error text, which pipy encodes as
    UTF-8 downstream.
    """

    return _parse_guest_json(line.decode("utf-8", "surrogatepass"))


def _parse_guest_json(text: str) -> Any:
    """Parse JSON text from the guest; lone surrogates become U+FFFD."""

    message = json.loads(
        text, parse_constant=_reject_constant, parse_float=_finite_float
    )
    if "\\u" not in text and _SURROGATE.search(text) is None:
        return message  # no escape and no raw surrogate: nothing to scrub
    dumped = json.dumps(message, ensure_ascii=False)
    if _SURROGATE.search(dumped) is None:
        return message
    # Surrogates in this JSON text can only sit inside strings.
    return json.loads(_SURROGATE.sub("\ufffd", dumped))


def _script_message(error_type: str, message: str, traceback: str) -> str:
    text = traceback.rstrip("\n")
    if text:
        return text
    return f"{error_type}: {message}" if message else error_type
