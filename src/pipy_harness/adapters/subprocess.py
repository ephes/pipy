"""Generic subprocess adapter for the first pipy harness slice."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import (
    CapturePolicy,
    collect_changed_file_paths,
    safe_command_executable,
)
from pipy_harness.models import AdapterResult, HarnessStatus, PreparedRun, RunRequest


class SubprocessAdapter:
    """Run an arbitrary command as a child process."""

    name = "subprocess"

    def prepare(self, request: RunRequest) -> PreparedRun:
        if not request.command:
            raise ValueError("run requires a command after --")

        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")

        return PreparedRun(
            command=tuple(request.command),
            cwd=cwd,
            adapter=self.name,
            command_executable=safe_command_executable(request.command),
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        started_at = _utc_now()
        process = subprocess.Popen(prepared.command, cwd=prepared.cwd)
        try:
            event_sink.emit(
                "agent.process.started",
                summary=f"Agent process started: executable={prepared.command_executable}.",
                payload={
                    "adapter": self.name,
                    "command_executable": prepared.command_executable,
                    "argv_stored": capture_policy.record_argv,
                    "stdout_stored": capture_policy.record_stdout,
                    "stderr_stored": capture_policy.record_stderr,
                    "raw_transcript_imported": capture_policy.import_raw_transcript,
                },
            )

            exit_code = process.wait()
        except KeyboardInterrupt:
            _terminate_process(process)
            raise
        except Exception:
            _terminate_process(process)
            raise
        ended_at = _utc_now()
        status = HarnessStatus.SUCCEEDED if exit_code == 0 else HarnessStatus.FAILED
        event_sink.emit(
            "agent.process.exited",
            summary=f"Agent process exited: status={status.value}, exit_code={exit_code}.",
            payload={
                "adapter": self.name,
                "command_executable": prepared.command_executable,
                "status": status.value,
                "exit_code": exit_code,
            },
        )

        changed_paths: tuple[str, ...] = ()
        if capture_policy.record_file_paths:
            changed_paths = collect_changed_file_paths(prepared.cwd)

        return AdapterResult(
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            changed_paths=changed_paths,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        raise
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
