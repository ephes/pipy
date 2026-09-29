"""The model-visible ``bash`` tool — a real shell, matching Pi.

``BashTool`` runs an arbitrary bash command in the workspace, the same way Pi's
bash tool does (``pi-mono/packages/coding-agent/src/core/tools/bash.ts``): a
real shell with full features — pipes, redirection, command substitution,
globbing, chaining, and any executable on ``PATH`` — an optional timeout in
seconds, and combined stdout/stderr returned to the model truncated like Pi's:
the last 2000 lines or 50 KB (whichever is hit first), with the full output
saved to a temp file that the ``[Showing lines …. Full output: <path>]`` notice
names (see :mod:`pipy_harness.native.tools.output_accumulator`). The command
runs in the workspace root with the inherited environment.

Like Pi, output is *streamed* as it is produced: when the loop supplies a
:attr:`~pipy_harness.native.tools.base.ToolContext.output_sink`, the tool emits
incremental chunks (throttled) so the live UI shows e.g. pytest dots scrolling
in real time. Streaming uses a single-thread ``selectors`` poll loop on the
calling thread (no reader thread): it reads whatever the process has flushed,
emits it, and enforces the timeout from one monotonic deadline — so the
timeout/kill path stays free of thread-join races. The truncated output is
still returned as the tool result regardless of whether a sink is present.

Contract with the tool loop:

- A timeout or a failure to start the shell is surfaced as ``is_error=True``
  with a safe reason label. The loop treats these as valid tool execution
  errors, not malformed provider tool calls.
- A command that runs to completion — even with a non-zero exit code — is
  ``is_error=False``. A failing build or test is a normal observation the model
  should reason about, not a malformed tool call; the exit code is reported in
  the observation so the model can react to it. (Pi appends
  ``Command exited with code N`` and marks it an error; that framing is the
  TOOLS2 follow-on.)

The combined output is returned to the model only. The loop's archive boundary
records counters and labels alone; no raw command string or output body is ever
archived.
"""

from __future__ import annotations

import codecs
import os
import selectors
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.output_accumulator import (
    OutputAccumulator,
    OutputSnapshot,
)
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
)

# Pi's MAX_TIMEOUT_MS (2^31 - 1 ms) in whole seconds.
_MAX_TIMEOUT_SECONDS = 2_147_483
_LOCAL_SHELL_MAX_OUTPUT_BYTES = 16 * 1024
_STREAM_THROTTLE_SECONDS = 0.1
_READ_CHUNK_BYTES = 64 * 1024


BASH_TOOL_DESCRIPTION = (
    "Execute a bash command in the current working directory. Returns stdout "
    f"and stderr. Output is truncated to last {DEFAULT_MAX_LINES} lines or "
    f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). If truncated, full "
    "output is saved to a temp file. Optionally provide a timeout in seconds."
)


@dataclass(frozen=True, slots=True)
class BashTool:
    """Run one arbitrary bash command in the workspace, Pi-style."""

    timeout_seconds: int | None = None
    shell_path: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"BashTool timeout_seconds must be None or in [1, {_MAX_TIMEOUT_SECONDS}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=BASH_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_TIMEOUT_SECONDS,
                        "description": (
                            "Timeout in seconds (optional, no default timeout)"
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        command = request.arguments["command"]
        timeout = request.arguments.get("timeout", self.timeout_seconds)
        shell = self._resolve_shell()
        if shell is None:
            return self._result(
                request, "bash: no shell available to run the command", is_error=True
            )

        cwd = context.workspace_root.resolve()
        try:
            proc = subprocess.Popen(  # noqa: S603 - intentional real shell, Pi parity
                [shell, "-c", command],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=dict(os.environ),
            )
        except OSError:
            return self._result(request, "bash: failed to start command", is_error=True)

        accumulator = OutputAccumulator(temp_file_prefix="pipy-bash")
        timed_out, cancelled = _stream_output(
            proc,
            sink=context.output_sink,
            timeout=timeout,
            store=accumulator,
            cancel_event=context.cancel_event,
        )
        accumulator.finish()
        accumulator.snapshot(persist_if_truncated=True)
        accumulator.close_temp_file()
        # Taken after the close so a file that failed to flush is not named.
        snapshot = accumulator.snapshot()
        return self._result(
            request,
            _shape(
                _output_with_notice(snapshot, accumulator.last_line_bytes),
                proc.returncode,
                timed_out=timed_out,
                cancelled=cancelled,
                timeout=timeout,
            ),
            is_error=timed_out or cancelled,
        )

    def _resolve_shell(self) -> str | None:
        if self.shell_path is not None:
            return self.shell_path if os.path.exists(self.shell_path) else None
        return shutil.which("bash") or (
            "/bin/sh" if os.path.exists("/bin/sh") else None
        )

    def _result(
        self, request: ToolRequest, output_text: str, *, is_error: bool
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output_text,
            is_error=is_error,
            provider_correlation_id=request.provider_correlation_id,
        )


class _OutputStore(Protocol):
    def append(self, data: bytes) -> None: ...


@dataclass(slots=True)
class _ByteTail:
    """The last ``max_output_bytes`` bytes, for the ``!`` shell shortcut."""

    max_output_bytes: int
    raw_tail: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    def append(self, data: bytes) -> None:
        self.raw_tail.extend(data)
        if len(self.raw_tail) > self.max_output_bytes:
            del self.raw_tail[: len(self.raw_tail) - self.max_output_bytes]
            self.truncated = True

    def output(self) -> str:
        return bytes(self.raw_tail).decode("utf-8", "replace")


@dataclass(slots=True)
class _OutputState:
    """The result store plus incremental sink-decoder state."""

    sink: Callable[[str], None] | None
    store: _OutputStore
    pending: list[str] = field(default_factory=list)
    decoder: codecs.IncrementalDecoder = field(init=False)
    last_emit: float = field(init=False)

    def __post_init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.last_emit = time.monotonic()

    def absorb(self, data: bytes) -> None:
        if self.sink is not None:
            text = self.decoder.decode(data)
            if text:
                self.pending.append(text)
        self.store.append(data)

    def emit(self, *, force: bool) -> None:
        if self.sink is None or not self.pending:
            return
        if force or (time.monotonic() - self.last_emit) >= _STREAM_THROTTLE_SECONDS:
            self.sink("".join(self.pending))
            self.pending.clear()
            self.last_emit = time.monotonic()

    def finish_decoder(self) -> None:
        if self.sink is None:
            return
        final = self.decoder.decode(b"", final=True)
        if final:
            self.pending.append(final)


@dataclass(frozen=True, slots=True)
class _StreamOutcome:
    eof: bool = False
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class _WaitDecision:
    wait_seconds: float
    interruption: _StreamOutcome | None = None


def _next_wait(
    cancel_event: "threading.Event | None", deadline: float | None
) -> _WaitDecision:
    # Cancellation intentionally wins when it races the deadline.
    if cancel_event is not None and cancel_event.is_set():
        return _WaitDecision(0, _StreamOutcome(cancelled=True))
    if deadline is None:
        return _WaitDecision(_STREAM_THROTTLE_SECONDS)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _WaitDecision(0, _StreamOutcome(timed_out=True))
    return _WaitDecision(min(_STREAM_THROTTLE_SECONDS, remaining))


def _read_output_phase(
    selector: selectors.BaseSelector,
    fd: int,
    state: _OutputState,
    *,
    cancel_event: "threading.Event | None",
    deadline: float | None,
) -> _StreamOutcome:
    while True:
        decision = _next_wait(cancel_event, deadline)
        if decision.interruption is not None:
            return decision.interruption
        if selector.select(timeout=decision.wait_seconds):
            data = os.read(fd, _READ_CHUNK_BYTES)
            if not data:
                return _StreamOutcome(eof=True)
            state.absorb(data)
        state.emit(force=False)


def _wait_after_stdout_eof(
    proc: subprocess.Popen[bytes],
    *,
    cancel_event: "threading.Event | None",
    deadline: float | None,
) -> _StreamOutcome:
    # A process can close or redirect stdout and keep running. Continue using
    # the original deadline and cancellation token until it exits.
    while proc.poll() is None:
        decision = _next_wait(cancel_event, deadline)
        if decision.interruption is not None:
            return decision.interruption
        try:
            proc.wait(timeout=decision.wait_seconds)
        except subprocess.TimeoutExpired:
            pass
    return _StreamOutcome(eof=True)


def _drain_nonblocking(
    selector: selectors.BaseSelector, fd: int, state: _OutputState
) -> None:
    try:
        while selector.select(timeout=0):
            data = os.read(fd, _READ_CHUNK_BYTES)
            if not data:
                break
            state.absorb(data)
    except OSError:
        pass


def _stream_output(
    proc: subprocess.Popen[bytes],
    *,
    sink: Callable[[str], None] | None,
    timeout: int | None,
    store: _OutputStore,
    cancel_event: "threading.Event | None" = None,
) -> tuple[bool, bool]:
    """Drain stdout into ``store``, stream chunks, and enforce one deadline.

    ``store`` bounds what is kept while the optional live sink receives all
    decoded chunks in order. Timeout and cancellation kill the process group and
    drain bytes already available from the pipe before final decoder flush.
    Returns ``(timed_out, cancelled)``.
    """

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    state = _OutputState(sink=sink, store=store)
    deadline = None if timeout is None else time.monotonic() + timeout

    outcome = _read_output_phase(
        selector,
        fd,
        state,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    if outcome.eof:
        outcome = _wait_after_stdout_eof(
            proc, cancel_event=cancel_event, deadline=deadline
        )
    if outcome.timed_out or outcome.cancelled:
        _kill_process_group(proc)
        _drain_nonblocking(selector, fd, state)

    state.finish_decoder()
    state.emit(force=True)
    selector.close()
    try:
        proc.stdout.close()
    except OSError:
        pass
    proc.wait()
    return outcome.timed_out, outcome.cancelled


@dataclass(slots=True)
class LocalShellResult:
    """Outcome of a local ``!``/``!!`` editor shell shortcut run."""

    output: str
    exit_code: int | None
    truncated: bool
    timed_out: bool
    cancelled: bool
    started: bool
    cancel_reason: str | None = None


def run_local_command(
    command: str,
    *,
    workspace_root: Path,
    output_sink: Callable[[str], None] | None = None,
    timeout: int | None = None,
    max_output_bytes: int = _LOCAL_SHELL_MAX_OUTPUT_BYTES,
    cancel_event: "threading.Event | None" = None,
    shell_path: str | None = None,
) -> LocalShellResult:
    """Run one bash command for an editor ``!``/``!!`` shortcut (Pi parity).

    Reuses the same real-shell streaming substrate as the model-visible
    ``bash`` tool — combined bounded stdout/stderr, optional timeout, live
    streaming through ``output_sink`` — and adds cooperative cancellation: when
    ``cancel_event`` is set (Escape during the run) the whole process group is
    killed and the partial output returned with ``cancelled=True``. This runs
    no provider turn; it is a local diagnostic the caller renders and
    (for ``!``) records into the conversation context itself.
    """

    shell = (
        shell_path
        if shell_path is not None and os.path.exists(shell_path)
        else shutil.which("bash") or ("/bin/sh" if os.path.exists("/bin/sh") else None)
    )
    if shell is None:
        return LocalShellResult(
            output="bash: no shell available to run the command",
            exit_code=None,
            truncated=False,
            timed_out=False,
            cancelled=False,
            started=False,
        )
    try:
        proc = subprocess.Popen(  # noqa: S603 - intentional real shell, Pi parity
            [shell, "-c", command],
            cwd=workspace_root.resolve(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
        )
    except OSError:
        return LocalShellResult(
            output="bash: failed to start command",
            exit_code=None,
            truncated=False,
            timed_out=False,
            cancelled=False,
            started=False,
        )
    store = _ByteTail(max_output_bytes)
    timed_out, cancelled = _stream_output(
        proc,
        sink=output_sink,
        timeout=timeout,
        store=store,
        cancel_event=cancel_event,
    )
    return LocalShellResult(
        output=store.output(),
        exit_code=proc.returncode,
        truncated=store.truncated,
        timed_out=timed_out,
        cancelled=cancelled,
        started=True,
    )


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _output_with_notice(snapshot: OutputSnapshot, last_line_bytes: int) -> str:
    """Return the kept tail plus Pi's truncation notice (Pi ``formatOutput``)."""

    truncation = snapshot.truncation
    text = snapshot.content
    if not truncation.truncated:
        return text
    path = snapshot.full_output_path
    where = f"Full output: {path}" if path else "Full output unavailable"
    start_line = truncation.total_lines - truncation.output_lines + 1
    end_line = truncation.total_lines
    if truncation.last_line_partial:
        notice = (
            f"[Showing last {format_size(truncation.output_bytes)} of line "
            f"{end_line} (line is {format_size(last_line_bytes)}). {where}]"
        )
    elif truncation.truncated_by == "lines":
        notice = (
            f"[Showing lines {start_line}-{end_line} of {truncation.total_lines}. "
            f"{where}]"
        )
    else:
        notice = (
            f"[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
            f"({format_size(DEFAULT_MAX_BYTES)} limit). {where}]"
        )
    return f"{text}\n\n{notice}"


def _shape(
    output: str,
    exit_code: int | None,
    *,
    timed_out: bool,
    timeout: int | None,
    cancelled: bool = False,
) -> str:
    if cancelled:
        sections = ["bash: command cancelled"]
    elif timed_out:
        sections = [f"bash: command timed out after {timeout}s"]
    else:
        sections = [f"exit code: {exit_code}"]
    if output:
        sections.append("[output]\n" + output)
    else:
        sections.append("(no output)")
    return "\n".join(sections)


__all__ = [
    "BASH_TOOL_DESCRIPTION",
    "BashTool",
    "LocalShellResult",
    "run_local_command",
]
