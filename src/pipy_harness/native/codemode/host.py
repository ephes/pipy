"""Codemode host runner: run one script in a fresh sandboxed worker (CM1 T3).

:func:`run_script` starts the worker (:mod:`.worker`) in its own process
group, feeds it the script, serves its ``tools.<name>(args)`` calls through an
injected callback and always kills and reaps it. It never raises for anything
the guest does: every failure becomes a :class:`ScriptOutcome` with one of the
four error kinds (spike result §4.6).

Threads (spike result §4.1 host rules, §4.2 step 4):

- One I/O thread owns every pipe. All pipe I/O is non-blocking, and the
  deadline and the cancel event are checked on every loop iteration, so a
  guest that floods output or never reads its stdin cannot stall the host.
  It validates every guest line (untrusted) and enforces the line, output and
  stderr caps. On any protocol violation, cap, deadline or cancel it kills the
  worker's process group at once and reports the outcome.
- The calling (session) thread runs the tool callback. The I/O thread hands it
  each ``call`` through a queue; the calling thread runs the callback and posts
  the reply back. At most one call is outstanding: a second ``call`` before its
  reply is a protocol violation.

The worker's process group is the only thing ever signalled, and only while
its leader is unreaped (a reaped pid may be reused). Nothing in the guest can
fork, so the group contains only the worker.
"""

from __future__ import annotations

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pipy_harness.native.codemode.outcome import (
    CallResult,
    CallStatus,
    ErrorKind,
    ScriptError,
    ScriptLimits,
    ScriptOutcome,
    ToolCallRecord,
)
from pipy_harness.native.codemode.prelude import EXIT_PROTOCOL, unknown_tool_message
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

ABORTED_MESSAGE = "Execution aborted"
OUTPUT_GUIDANCE = (
    "the script's output exceeded {limit}; print a summary and write large "
    "data through a tool instead"
)


def run_script(
    code: str,
    *,
    call_tool: ToolCallback,
    tool_names: Iterable[str],
    limits: ScriptLimits | None = None,
    cancel: threading.Event | None = None,
    tool_stop: threading.Event | None = None,
    paths: CodemodePaths | None = None,
    pin: RuntimePin = RUNTIME_PIN,
    python: str = sys.executable,
    activity_waiter: ActivityWaiter | None = None,
) -> ScriptOutcome:
    """Run ``code`` in a fresh worker and return its outcome; never raises.

    ``call_tool`` runs on this (the calling) thread, one call at a time, and
    only for names in ``tool_names``; any other name gets an error result
    without reaching it. It must honour ``cancel`` and ``tool_stop`` itself:
    the runner cannot interrupt a callback. ``tool_stop`` (if given) is set as
    soon as the run ends for any reason (deadline, violation, cancel, done),
    so a callback still in flight can stop its tool. It must be a fresh event
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

    Availability (:func:`.selftest.availability`) is the caller's job; the
    runner still fails closed: the worker re-checks ``python.wasm`` against the
    manifest and must report the expected backend and pinned runtime hash.
    """

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
            call_tool,
            tool_names,
            limits or ScriptLimits(),
            cancel,
            tool_stop,
            paths,
            pin,
            python,
            activity_waiter,
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


def _run_script(
    code: str,
    call_tool: ToolCallback,
    tool_names: Iterable[str],
    limits: ScriptLimits,
    cancel: threading.Event | None,
    tool_stop: threading.Event | None,
    paths: CodemodePaths | None,
    pin: RuntimePin,
    python: str,
    activity_waiter: ActivityWaiter | None,
) -> ScriptOutcome:
    cancel = cancel or threading.Event()
    started = time.monotonic()
    early = _precheck(code, limits, cancel)
    if early is not None:
        return ScriptOutcome(early, wall_seconds=time.monotonic() - started)
    names = sorted(set(tool_names))
    run_line = _encode({"type": "run", "code": code, "tools": names})
    calls: list[ToolCallRecord] = []
    finished = _launch(
        _Launch(
            run_line=run_line,
            request=_run_request(paths or default_paths(), pin, limits),
            python=python,
            limits=limits,
            pin=pin,
            names=frozenset(names),
            deadline=started + limits.wall_seconds,
            cancel=cancel,
            tool_stop=tool_stop,
        ),
        call_tool,
        calls,
        activity_waiter,
    )
    return ScriptOutcome(
        finished.error,
        output=finished.output,
        calls=tuple(calls),
        wall_seconds=time.monotonic() - started,
        diagnostics=finished.diagnostics,
    )


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


@dataclass(frozen=True, slots=True)
class _Launch:
    run_line: bytes
    request: str
    python: str
    limits: ScriptLimits
    pin: RuntimePin
    names: frozenset[str]
    deadline: float
    cancel: threading.Event
    tool_stop: threading.Event | None


@dataclass(frozen=True, slots=True)
class _Call:
    id: int
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class _Finished:
    error: ScriptError | None
    output: tuple[str, ...] = ()
    diagnostics: str = ""


@dataclass(frozen=True, slots=True)
class _Reply:
    id: int
    result: CallResult


@dataclass(frozen=True, slots=True)
class _StopRequest:
    error: ScriptError


def _launch(
    launch: _Launch,
    call_tool: ToolCallback,
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
        return _serve(channel, call_tool, launch.cancel, calls, activity_waiter)
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
    call_tool: ToolCallback,
    cancel: threading.Event,
    calls: list[ToolCallRecord],
    activity_waiter: ActivityWaiter | None,
) -> _Finished:
    """Run tool callbacks on this thread until the I/O thread finishes."""

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
            return event
        if channel.finished.is_set() or cancel.is_set():
            continue  # the run is over; its _Finished follows
        _run_call(channel, call_tool, cancel, calls, event)


def _take_live_activity(channel: _Channel) -> _Call | _Finished | None:
    event = channel.take_activity()
    if event is not None or channel.alive():
        return event
    # Completion may have been published after our empty read. A dead thread
    # without it is a host failure, never a reason to wait for more activity.
    return channel.take_activity() or _Finished(
        ScriptError(ErrorKind.SANDBOX, "host I/O exited without completion")
    )


def _run_call(
    channel: _Channel,
    call_tool: ToolCallback,
    cancel: threading.Event,
    calls: list[ToolCallRecord],
    call: _Call,
) -> None:
    try:
        result = call_tool(call.name, call.arguments_json)
        if not isinstance(result, CallResult):
            raise TypeError(f"returned {type(result).__name__}, not CallResult")
    except Exception as exc:  # noqa: BLE001 - the runner never raises; a failing callback is a sandbox error
        calls.append(ToolCallRecord(call.name, CallStatus.ERROR))
        channel.stop(
            ScriptError(
                ErrorKind.SANDBOX,
                f"the {call.name} tool callback failed: {type(exc).__name__}: {exc}",
            )
        )
        return
    if result.cancelled:
        calls.append(ToolCallRecord(call.name, CallStatus.CANCELLED))
        channel.stop(ScriptError(ErrorKind.ABORTED, ABORTED_MESSAGE))
        return
    calls.append(
        ToolCallRecord(call.name, CallStatus.OK if result.ok else CallStatus.ERROR)
    )
    if cancel.is_set():
        return  # the I/O thread ends the run as aborted; no reply
    channel.reply(call.id, result)


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
        self.inbox: queue.SimpleQueue[_Call | _Finished] = queue.SimpleQueue()
        self.finished = threading.Event()
        self.activity = threading.Event()
        self._activity_lock = threading.Lock()
        self._commands: queue.SimpleQueue[_Reply | _StopRequest] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run, name="codemode-io", daemon=True
        )
        self._started = False
        # Everything below is owned by the I/O thread alone.
        self._out = bytearray(launch.run_line)
        self._stdin_open = True
        self._line = bytearray()
        self._status_buf = bytearray()
        self._stderr_tail = bytearray()
        self._output: list[str] = []
        self._output_bytes = 0
        # The call awaiting its reply; it stays outstanding until the reply
        # has been written in full, so an unread reply blocks further calls
        # and the reply buffer holds at most one reply.
        self._outstanding: int | None = None
        self._reply_queued = False
        self._handoff: _Call | None = None
        self._next_id = 1
        self._ready = False
        self._exit: dict[str, Any] | None = None
        self._open = {"out": True, "status": True}

    # --- the calling thread's side -------------------------------------------

    def post_activity(self, event: _Call | _Finished) -> None:
        # Publication and empty-queue acknowledgement share this lock. A post
        # cannot slip between the consumer's empty check and event clear.
        with self._activity_lock:
            self.inbox.put(event)
            self.activity.set()

    def take_activity(self) -> _Call | _Finished | None:
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

    def reply(self, call_id: int, result: CallResult) -> None:
        self._commands.put(_Reply(call_id, result))
        self._wake()

    def stop(self, error: ScriptError) -> None:
        """Ask the I/O thread to end the run; a no-op once it has finished."""

        self._commands.put(_StopRequest(error))
        self._wake()

    def join(self) -> bool:
        if not self._started:
            return True
        self._thread.join(timeout=JOIN_TIMEOUT_SECONDS)
        return not self._thread.is_alive()

    def close(self) -> None:
        os.close(self._wake_read)
        os.close(self._wake_write)

    def _wake(self) -> None:
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
                for event in (self.finished, self._launch.tool_stop):
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
                    self.inbox.put(_Finished(error, tuple(self._output), diagnostics))
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
            self._queue_reply(command.id, command.result)

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
            self._stdin_open = False
            return
        del self._out[:written]
        if not self._out and self._reply_queued:
            self._outstanding = None
            self._reply_queued = False

    def _keep_stderr(self, chunk: bytes) -> None:
        self._stderr_tail += chunk
        excess = len(self._stderr_tail) - self._limits.stderr_bytes
        if excess > 0:
            del self._stderr_tail[:excess]

    # --- guest lines ------------------------------------------------------------

    def _feed_output(self, chunk: bytes) -> None:
        view = memoryview(chunk)
        start = 0
        while (newline := chunk.find(b"\n", start)) >= 0:
            self._append_line(view[start:newline])
            line = bytes(self._line)
            self._line.clear()
            self._handle_line(line)
            start = newline + 1
        self._append_line(view[start:])
        # A call goes to the calling thread only once the rest of its chunk
        # was accepted: a violation in the same chunk (a ``done`` or garbage
        # right after the call) must end the run before any tool runs.
        if self._handoff is not None:
            self.post_activity(self._handoff)
            self._handoff = None

    def _append_line(self, piece: memoryview) -> None:
        if len(self._line) + len(piece) > self._limits.line_bytes:
            raise _violation(f"a guest line exceeded {self._limits.line_bytes} bytes")
        self._line += piece

    def _handle_line(self, line: bytes) -> None:
        try:
            message = _parse_guest_line(line)
        except (ValueError, RecursionError):
            raise _violation("the guest sent a line that is not JSON") from None
        if not isinstance(message, dict):
            raise _violation("the guest sent a line that is not a JSON object")
        if self._outstanding is not None:
            # The guest blocks on its reply, so nothing may arrive meanwhile.
            raise _violation("the guest sent a message while a tool call was pending")
        kind = message.get("type")
        if kind == "text":
            self._on_text(message)
        elif kind == "call":
            self._on_call(message)
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

    @staticmethod
    def _output_capped(limit: str) -> _Stop:
        return _Stop(ScriptError(ErrorKind.SCRIPT, OUTPUT_GUIDANCE.format(limit=limit)))

    def _on_call(self, message: dict[str, Any]) -> None:
        if not self._ready:
            raise _violation("a tool call arrived before the worker was ready")
        call_id = message.get("id")
        name = message.get("name")
        args = message.get("args")
        if type(call_id) is not int or call_id != self._next_id:
            raise _violation("a tool call had an unexpected id")
        if not isinstance(name, str) or not isinstance(args, dict):
            raise _violation("a tool call was malformed")
        try:
            arguments_json = json.dumps(args, ensure_ascii=False, allow_nan=False)
        except ValueError:  # defensive: parsing already rejects non-finite numbers
            raise _violation("a tool call's arguments are not JSON") from None
        self._outstanding = call_id
        self._next_id += 1
        if name not in self._launch.names:
            message_text = unknown_tool_message(name, self._launch.names)
            self._queue_reply(call_id, CallResult(ok=False, text=message_text))
            return
        self._handoff = _Call(call_id, name, arguments_json)

    def _queue_reply(self, call_id: int, result: CallResult) -> None:
        if call_id != self._outstanding or self._reply_queued:
            return  # stale: the run moved on
        self._reply_queued = True
        if result.ok:
            reply = {
                "type": "result",
                "id": call_id,
                "ok": True,
                "value": str(result.text),
            }
        else:
            reply = {
                "type": "result",
                "id": call_id,
                "ok": False,
                "error": str(result.text),
            }
        self._out += _encode(reply)

    def _on_done(self, message: dict[str, Any]) -> None:
        # ``done`` is terminal: nothing after it is read.
        if not self._ready:
            raise _violation("the script finished before the worker was ready")
        ok = message.get("ok")
        if ok is True:
            raise _Stop(None)
        error = message.get("error")
        if ok is False and isinstance(error, dict):
            fields = [error.get(key) for key in ("type", "message", "traceback")]
            strings = [value for value in fields if isinstance(value, str)]
            if len(strings) == len(fields):
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


def _parse_guest_line(line: bytes) -> Any:
    """Parse one guest line as UTF-8 JSON; lone surrogates become U+FFFD.

    The wire is UTF-8 only: ``json.loads`` would otherwise detect UTF-16 and
    UTF-32 input on its own. Raw surrogate bytes are let through the decode
    (``surrogatepass``) and scrubbed with escaped ones, because guest strings
    flow into tool arguments, output and error text, which pipy encodes as
    UTF-8 downstream.
    """

    text = line.decode("utf-8", "surrogatepass")
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
