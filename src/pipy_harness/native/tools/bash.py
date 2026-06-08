"""The model-visible ``bash`` tool — a real shell, matching Pi.

``BashTool`` runs an arbitrary bash command in the workspace, the same way Pi's
bash tool does (``pi-mono/packages/coding-agent/src/core/tools/bash.ts``): a
real shell with full features — pipes, redirection, command substitution,
globbing, chaining, and any executable on ``PATH`` — an optional timeout in
seconds, and combined stdout/stderr returned to the model bounded to a byte
ceiling. The command runs in the workspace root with the inherited environment.

Like Pi, output is *streamed* as it is produced: when the loop supplies a
:attr:`~pipy_harness.native.tools.base.ToolContext.output_sink`, the tool emits
incremental chunks (throttled) so the live UI shows e.g. pytest dots scrolling
in real time. Streaming uses a single-thread ``selectors`` poll loop on the
calling thread (no reader thread): it reads whatever the process has flushed,
emits it, and enforces the timeout from one monotonic deadline — so the
timeout/kill path stays free of thread-join races. The full (bounded) output is
still returned as the tool result regardless of whether a sink is present.

Contract with the tool loop:

- A timeout or a failure to start the shell is surfaced as ``is_error=True``
  with a safe reason label. The loop counts these toward its malformed-call
  streak, matching the other tools.
- A command that runs to completion — even with a non-zero exit code — is
  ``is_error=False``. A failing build or test is a normal observation the model
  should reason about, not a malformed tool call; the exit code is reported in
  the observation so the model can react to it.

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
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

_COMMAND_MAX_LENGTH = 4 * 1024
_MAX_TIMEOUT_SECONDS = 1800
_STREAM_THROTTLE_SECONDS = 0.1
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class BashTool:
    """Run one arbitrary bash command in the workspace, Pi-style."""

    timeout_seconds: int | None = None
    max_output_bytes: int = 16 * 1024
    shell_path: str | None = None

    # Kept safely under ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH (64 KiB) so a
    # configured ceiling can never produce a result that fails to construct;
    # the byte count is an upper bound on the shaped character count.
    HARD_MAX_OUTPUT_BYTES: ClassVar[int] = 60 * 1024

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
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes < 1
            or self.max_output_bytes > self.HARD_MAX_OUTPUT_BYTES
        ):
            raise ValueError(
                f"BashTool max_output_bytes must be in [1, {self.HARD_MAX_OUTPUT_BYTES}]"
            )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Execute a bash command in the workspace directory. Returns the "
                "combined stdout and stderr. This is a real shell: pipes, "
                "redirection, command substitution, globbing, chaining, and any "
                "executable on PATH are allowed (ls, grep, find, git, just, uv, "
                "pytest, ...). Output streams back as it is produced and is "
                "bounded to the last portion when large. Optionally provide a "
                "timeout in seconds (no default timeout)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _COMMAND_MAX_LENGTH,
                        "description": (
                            "The bash command to execute, e.g. 'just test' or "
                            "'grep -rn TODO src | head'."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_TIMEOUT_SECONDS,
                        "description": (
                            "Optional timeout in seconds. The whole process "
                            "group is killed when it elapses."
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

        output, truncated, timed_out, _cancelled = _stream_output(
            proc,
            sink=context.output_sink,
            timeout=timeout,
            max_output_bytes=self.max_output_bytes,
        )
        return self._result(
            request,
            _shape(
                output,
                proc.returncode,
                truncated=truncated,
                timed_out=timed_out,
                timeout=timeout,
            ),
            is_error=timed_out,
        )

    def _resolve_shell(self) -> str | None:
        if self.shell_path is not None:
            return self.shell_path if os.path.exists(self.shell_path) else None
        return shutil.which("bash") or ("/bin/sh" if os.path.exists("/bin/sh") else None)

    def _result(
        self, request: ToolRequest, output_text: str, *, is_error: bool
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output_text,
            is_error=is_error,
            provider_correlation_id=request.provider_correlation_id,
        )


def _stream_output(
    proc: subprocess.Popen[bytes],
    *,
    sink: Callable[[str], None] | None,
    timeout: int | None,
    max_output_bytes: int,
    cancel_event: "threading.Event | None" = None,
) -> tuple[str, bool, bool, bool]:
    """Drain ``proc`` stdout on the calling thread, emitting chunks as they come.

    A single ``selectors`` poll loop reads whatever the process has flushed and,
    if ``sink`` is set, emits it to the live UI throttled to
    ``_STREAM_THROTTLE_SECONDS``. The returned result keeps only a bounded *tail*
    of the raw output (the last ``max_output_bytes`` bytes), so an unbounded
    producer (``yes``, a noisy build) cannot grow memory without limit; the live
    sink streams everything (the UI bounds its own view).

    The timeout is one monotonic deadline. It is enforced on the read loop *and*
    after stdout EOF: a command that closes its stdout but keeps running cannot
    hang ``invoke()`` — once the deadline passes the whole process group is
    killed. No reader thread, so there is no join race on the timeout path.

    When ``cancel_event`` is supplied and becomes set (the user pressed Escape
    while a ``!`` shortcut runs), the whole process group is killed and the
    partial output returned with ``cancelled=True``. Returns
    ``(output, truncated, timed_out, cancelled)``.
    """

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)

    raw_tail = bytearray()
    truncated = False
    pending: list[str] = []
    last_emit = time.monotonic()
    deadline = None if timeout is None else time.monotonic() + timeout
    timed_out = False
    cancelled = False
    eof = False

    def emit(force: bool) -> None:
        nonlocal last_emit
        if sink is None or not pending:
            return
        if force or (time.monotonic() - last_emit) >= _STREAM_THROTTLE_SECONDS:
            sink("".join(pending))
            pending.clear()
            last_emit = time.monotonic()

    def absorb(data: bytes) -> None:
        nonlocal truncated
        if sink is not None:
            text = decoder.decode(data)
            if text:
                pending.append(text)
        raw_tail.extend(data)
        if len(raw_tail) > max_output_bytes:
            del raw_tail[: len(raw_tail) - max_output_bytes]
            truncated = True

    def drain_nonblocking() -> None:
        try:
            while selector.select(timeout=0):
                data = os.read(fd, _READ_CHUNK_BYTES)
                if not data:
                    break
                absorb(data)
        except OSError:
            pass

    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            wait = min(_STREAM_THROTTLE_SECONDS, remaining)
        else:
            wait = _STREAM_THROTTLE_SECONDS
        if selector.select(timeout=wait):
            data = os.read(fd, _READ_CHUNK_BYTES)
            if not data:  # EOF: the process closed stdout.
                eof = True
                break
            absorb(data)
        emit(force=False)

    if not timed_out and eof:
        # stdout is closed, but the process may still be running (it can close
        # or redirect its fds and keep working). Keep enforcing the deadline so
        # such a child cannot hang invoke().
        try:
            wait_for = None if deadline is None else max(0.0, deadline - time.monotonic())
            proc.wait(timeout=wait_for)
        except subprocess.TimeoutExpired:
            timed_out = True

    if timed_out or cancelled:
        _kill_process_group(proc)
        drain_nonblocking()

    if sink is not None:
        final = decoder.decode(b"", final=True)
        if final:
            pending.append(final)
    emit(force=True)

    selector.close()
    try:
        proc.stdout.close()
    except OSError:
        pass
    proc.wait()
    return bytes(raw_tail).decode("utf-8", "replace"), truncated, timed_out, cancelled


@dataclass(frozen=True, slots=True)
class LocalShellResult:
    """Outcome of a local ``!``/``!!`` editor shell shortcut run."""

    output: str
    exit_code: int | None
    truncated: bool
    timed_out: bool
    cancelled: bool
    started: bool


def run_local_command(
    command: str,
    *,
    workspace_root: Path,
    output_sink: Callable[[str], None] | None = None,
    timeout: int | None = None,
    max_output_bytes: int = 16 * 1024,
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
    output, truncated, timed_out, cancelled = _stream_output(
        proc,
        sink=output_sink,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        cancel_event=cancel_event,
    )
    return LocalShellResult(
        output=output,
        exit_code=proc.returncode,
        truncated=truncated,
        timed_out=timed_out,
        cancelled=cancelled,
        started=True,
    )


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _shape(
    output: str,
    exit_code: int | None,
    *,
    truncated: bool,
    timed_out: bool,
    timeout: int | None,
) -> str:
    if timed_out:
        sections = [f"bash: command timed out after {timeout}s"]
    else:
        sections = [f"exit code: {exit_code}"]
    if output:
        sections.append("[output]\n" + output)
    else:
        sections.append("(no output)")
    if truncated:
        sections.append("(output truncated)")
    return "\n".join(sections)


__all__ = ["BashTool", "LocalShellResult", "run_local_command"]
