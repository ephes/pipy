"""The `bash` tool: bounded shell command execution.

`BashTool` runs a shell command through `subprocess.Popen` with
`shell=True`, a workspace-relative `cwd`, a hard timeout, bounded stdout
and stderr collection, and a default-deny check for paths that contain the
`.git` substring or the `--git-dir` flag. The command text and capped raw
output are kept in memory for the model-visible tool result; the pipy
session archive (`pipy_session.recorder`) is never touched from inside
the tool, so it only ever sees a metadata-safe summary through the
existing `NativeToolResult` shape produced by the tool-loop session.

Unlike `read`, `ls`, `grep`, `find`, `write`, and `edit`, this tool is
deliberately broader: the model can run any single shell command. The
remaining safeties are: workspace-relative cwd, bounded output, bounded
timeout, a default `.git` substring refusal, and no shell history
mutation. The tool does not export environment variables, parse the
command for further sandboxing, or spawn a process tree manager.
"""

from __future__ import annotations

import subprocess
import selectors
import time
from dataclasses import dataclass, field
from typing import BinaryIO, ClassVar, cast

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

TRUNCATION_MARKER = "... (truncated)"
_DEFAULT_DENY_SUBSTRINGS: tuple[str, ...] = (".git/", " .git ", "--git-dir")


@dataclass(frozen=True, slots=True)
class BashTool:
    """Run a bounded shell command in the workspace root."""

    default_timeout_seconds: float = 30.0
    max_timeout_seconds: float = 120.0
    max_stdout_bytes: int = 32 * 1024
    max_stderr_bytes: int = 32 * 1024

    HARD_MAX_TIMEOUT_SECONDS: ClassVar[float] = 600.0
    HARD_MAX_OUTPUT_BYTES: ClassVar[int] = 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.default_timeout_seconds, (int, float))
            or isinstance(self.default_timeout_seconds, bool)
            or self.default_timeout_seconds <= 0
            or self.default_timeout_seconds > self.HARD_MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "BashTool default_timeout_seconds must be in "
                f"(0, {self.HARD_MAX_TIMEOUT_SECONDS}]"
            )
        if (
            not isinstance(self.max_timeout_seconds, (int, float))
            or isinstance(self.max_timeout_seconds, bool)
            or self.max_timeout_seconds < self.default_timeout_seconds
            or self.max_timeout_seconds > self.HARD_MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "BashTool max_timeout_seconds must be in "
                f"[default_timeout_seconds, {self.HARD_MAX_TIMEOUT_SECONDS}]"
            )
        for name, value in (
            ("max_stdout_bytes", self.max_stdout_bytes),
            ("max_stderr_bytes", self.max_stderr_bytes),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
                or value > self.HARD_MAX_OUTPUT_BYTES
            ):
                raise ValueError(
                    f"BashTool {name} must be in [1, {self.HARD_MAX_OUTPUT_BYTES}]"
                )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Run one bounded shell command in the workspace root. "
                "Output is capped and a hard timeout applies. Commands that "
                "include the '.git/' path or '--git-dir' flag are refused. "
                "The command text and output stay in memory; they are not "
                "written to the pipy session archive."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 8 * 1024,
                        "description": (
                            "Shell command line to execute. Pipes and "
                            "redirects work because the command runs through "
                            "/bin/sh -c."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": int(self.max_timeout_seconds),
                        "description": (
                            "Optional per-command timeout in seconds. "
                            f"Defaults to {int(self.default_timeout_seconds)}; "
                            f"capped at {int(self.max_timeout_seconds)}."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        command = request.arguments["command"]
        if not isinstance(command, str) or not command.strip():
            raise ToolArgumentError(
                "bash", "command must be a non-empty string",
                field_path=("command",),
            )
        if any(deny in command for deny in _DEFAULT_DENY_SUBSTRINGS):
            return self._error(
                request,
                "command refused: contains a .git path or --git-dir flag",
            )

        timeout_arg = request.arguments.get("timeout_seconds")
        timeout = self.default_timeout_seconds
        if timeout_arg is not None:
            if (
                not isinstance(timeout_arg, int)
                or isinstance(timeout_arg, bool)
                or timeout_arg < 1
            ):
                raise ToolArgumentError(
                    "bash",
                    "timeout_seconds must be a positive integer",
                    field_path=("timeout_seconds",),
                )
            timeout = min(float(timeout_arg), self.max_timeout_seconds)

        workspace = context.workspace_root.resolve()
        try:
            completed = self._run_bounded(
                command=command,
                workspace=workspace,
                timeout_seconds=timeout,
            )
        except OSError as exc:
            return self._error(request, f"failed to spawn shell: {exc}")

        output = self._format_output(
            exit_code=completed.exit_code,
            stdout=completed.stdout,
            stderr=completed.stderr,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
            timed_out=completed.timed_out,
            timeout_seconds=timeout,
        )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output,
            is_error=completed.timed_out or completed.exit_code != 0,
            provider_correlation_id=request.provider_correlation_id,
        )

    def _run_bounded(
        self,
        *,
        command: str,
        workspace,
        timeout_seconds: float,
    ) -> "_BoundedProcessResult":
        process = subprocess.Popen(  # noqa: S602 - shell=True is the bash tool point
            command,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )
        assert process.stdout is not None  # noqa: S101
        assert process.stderr is not None  # noqa: S101
        process_stdout = cast(BinaryIO, process.stdout)
        process_stderr = cast(BinaryIO, process.stderr)

        stdout = _BoundedBuffer(self.max_stdout_bytes)
        stderr = _BoundedBuffer(self.max_stderr_bytes)
        stream_for_key: dict[BinaryIO, _BoundedBuffer] = {}
        selector = selectors.DefaultSelector()
        selector.register(process_stdout, selectors.EVENT_READ)
        stream_for_key[process_stdout] = stdout
        selector.register(process_stderr, selectors.EVENT_READ)
        stream_for_key[process_stderr] = stderr

        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        try:
            while stream_for_key:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    process.kill()
                    break
                events = selector.select(timeout=min(0.1, remaining))
                if not events:
                    if process.poll() is not None:
                        continue
                    continue
                for key, _ in events:
                    stream = cast(BinaryIO, key.fileobj)
                    buffer = stream_for_key[stream]
                    chunk = stream.read(8192)
                    if chunk:
                        buffer.append(chunk)
                        continue
                    selector.unregister(stream)
                    del stream_for_key[stream]
            if timed_out:
                stdout_tail, stderr_tail = process.communicate(timeout=1)
                stdout.append(stdout_tail or b"")
                stderr.append(stderr_tail or b"")
            else:
                process.wait()
        finally:
            selector.close()
            for stream in tuple(stream_for_key):
                try:
                    stream.close()
                except OSError:
                    pass

        return _BoundedProcessResult(
            exit_code=process.returncode,
            stdout=stdout.text(),
            stderr=stderr.text(),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            timed_out=timed_out,
        )

    @staticmethod
    def _format_output(
        *,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        timed_out: bool,
        timeout_seconds: float,
    ) -> str:
        header_parts: list[str] = []
        if timed_out:
            header_parts.append(
                f"bash timed out after {timeout_seconds:.0f}s"
            )
        else:
            header_parts.append(f"exit_code={exit_code}")
        header = " ".join(header_parts)

        sections: list[str] = [header]
        if stdout:
            section = stdout
            if stdout_truncated:
                section = section + "\n" + TRUNCATION_MARKER
            sections.append(f"stdout:\n{section}")
        elif not timed_out:
            sections.append("stdout: (empty)")
        if stderr:
            section = stderr
            if stderr_truncated:
                section = section + "\n" + TRUNCATION_MARKER
            sections.append(f"stderr:\n{section}")
        elif not timed_out and exit_code != 0:
            sections.append("stderr: (empty)")
        return "\n".join(sections)

    def _error(
        self, request: ToolRequest, message: str
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"bash error: {message}",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


__all__ = ["BashTool", "TRUNCATION_MARKER"]


@dataclass(slots=True)
class _BoundedBuffer:
    cap: int
    payload: bytearray = field(init=False)
    truncated: bool = False

    def __post_init__(self) -> None:
        self.payload = bytearray()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        remaining = self.cap - len(self.payload)
        if remaining > 0:
            self.payload.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True

    def text(self) -> str:
        return bytes(self.payload).decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class _BoundedProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
