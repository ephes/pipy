"""Harness runner that owns lifecycle and session-record writes."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Protocol

from pipy_session.recorder import SessionRecord, append_event, finalize_session, init_session

from pipy_harness.adapters.base import AgentPort
from pipy_harness.capture import (
    WorkspaceDisplay,
    sanitize_path,
    sanitize_metadata,
    sanitize_text,
    workspace_display,
)
from pipy_harness.models import AdapterResult, HarnessStatus, RunRequest, RunResult

HARNESS_PROTOCOL_VERSION = 1
_SAFE_USAGE_COUNTERS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
}


class FileSessionRecorder:
    """Small adapter over the existing pipy-session recorder functions."""

    def init(
        self,
        request: RunRequest,
        *,
        run_id: str,
        started_at: datetime,
        initial_fields: Mapping[str, Any],
    ) -> Path:
        return init_session(
            agent=request.agent,
            slug=request.slug,
            root=request.root,
            goal=sanitize_text(request.goal) if request.goal else None,
            partial=True,
            now=started_at,
            initial_fields=initial_fields,
        )

    def append(self, active_path: Path, event: Mapping[str, Any], *, root: Path | None) -> None:
        append_event(active_path, root=root, event=event)

    def finalize(self, active_path: Path, *, root: Path | None, summary: str) -> SessionRecord:
        return finalize_session(active_path, root=root, summary_text=summary)


class RecorderPort(Protocol):
    """Recorder interface used by the runner."""

    def init(
        self,
        request: RunRequest,
        *,
        run_id: str,
        started_at: datetime,
        initial_fields: Mapping[str, Any],
    ) -> Path:
        """Create an active session record."""

    def append(self, active_path: Path, event: Mapping[str, Any], *, root: Path | None) -> None:
        """Append one event to the active record."""

    def finalize(self, active_path: Path, *, root: Path | None, summary: str) -> SessionRecord:
        """Finalize the active record."""


@dataclass(slots=True)
class HarnessRunner:
    """Run an agent task and record a conservative partial session."""

    adapter: AgentPort
    recorder: RecorderPort = field(default_factory=FileSessionRecorder)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex

    def run(self, request: RunRequest) -> RunResult:
        run_id = self.id_factory()
        started_at = _ensure_utc(self.clock())
        workspace = workspace_display(request.cwd)
        initial_fields = _initial_session_fields(run_id, started_at)
        active_path = self.recorder.init(
            request,
            run_id=run_id,
            started_at=started_at,
            initial_fields=initial_fields,
        )
        sink = _RecorderEventSink(
            recorder=self.recorder,
            active_path=active_path,
            root=request.root,
            run_id=run_id,
            agent=request.agent,
            adapter=self.adapter.name,
            clock=self.clock,
        )

        status = HarnessStatus.FAILED
        exit_code = 1
        error_type: str | None = None
        error_message: str | None = None
        adapter_result: AdapterResult | None = None

        try:
            sink.emit(
                "harness.run.started",
                summary=(
                    "Harness run started: "
                    f"agent={sanitize_text(request.agent)}, adapter={self.adapter.name}, "
                    f"cwd={workspace.name}."
                ),
                payload=_base_payload(request, self.adapter.name, workspace, HarnessStatus.RUNNING),
            )
            prepared = self.adapter.prepare(request)
            adapter_result = self.adapter.run(
                prepared,
                event_sink=sink,
                capture_policy=request.capture_policy,
            )
            status = adapter_result.status
            exit_code = adapter_result.exit_code
            if status != HarnessStatus.SUCCEEDED and adapter_result.metadata is not None:
                adapter_error_type = adapter_result.metadata.get("error_type")
                adapter_error_message = adapter_result.metadata.get("error_message")
                if isinstance(adapter_error_type, str):
                    error_type = sanitize_text(adapter_error_type)
                if isinstance(adapter_error_message, str):
                    error_message = sanitize_text(adapter_error_message)

            if request.capture_policy.record_file_paths and adapter_result.changed_paths:
                sink.emit(
                    "workspace.files.changed",
                    summary=(
                        "Workspace changed files recorded: "
                        f"count={len(adapter_result.changed_paths)}."
                    ),
                    payload={
                        "adapter": self.adapter.name,
                        "changed_file_count": len(adapter_result.changed_paths),
                        "paths": list(adapter_result.changed_paths),
                        "diffs_stored": False,
                        "file_contents_stored": False,
                    },
                )

            completion_event = (
                "harness.run.completed"
                if status == HarnessStatus.SUCCEEDED
                else "harness.run.failed"
            )
            sink.emit(
                completion_event,
                summary=f"Harness run finished: status={status.value}, exit_code={exit_code}.",
                payload={
                    **_base_payload(request, self.adapter.name, workspace, status),
                    "exit_code": exit_code,
                    "duration_seconds": _duration_seconds(started_at, self.clock()),
                },
            )
        except KeyboardInterrupt:
            status = HarnessStatus.ABORTED
            exit_code = 130
            error_type = "KeyboardInterrupt"
            sink.emit(
                "harness.run.aborted",
                summary="Harness run aborted before completion.",
                payload={
                    **_base_payload(request, self.adapter.name, workspace, status),
                    "exit_code": exit_code,
                    "error_type": error_type,
                    "duration_seconds": _duration_seconds(started_at, self.clock()),
                },
            )
        except Exception as exc:
            status = HarnessStatus.FAILED
            exit_code = 1
            error_type = type(exc).__name__
            error_message = _error_message(exc)
            sink.emit(
                "harness.run.failed",
                summary="Harness run failed before a native process result was available.",
                payload={
                    **_base_payload(request, self.adapter.name, workspace, status),
                    "exit_code": exit_code,
                    "error_type": error_type,
                    "error_message": error_message,
                    "duration_seconds": _duration_seconds(started_at, self.clock()),
                },
            )

        sink.emit(
            "session.finalized",
            summary="Session finalization requested for the harness run.",
            payload={
                **_base_payload(request, self.adapter.name, workspace, status),
                "exit_code": exit_code,
                "duration_seconds": _duration_seconds(started_at, self.clock()),
            },
        )
        record = self.recorder.finalize(
            active_path,
            root=request.root,
            summary=_markdown_summary(
                request=request,
                adapter=self.adapter.name,
                run_id=run_id,
                workspace=workspace,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                ended_at=self.clock(),
                changed_paths=adapter_result.changed_paths if adapter_result is not None else (),
                error_type=error_type,
                error_message=error_message,
            ),
        )
        duration_seconds = _duration_seconds(started_at, self.clock())
        return RunResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            record=record,
            error_type=error_type,
            error_message=error_message,
            duration_seconds=duration_seconds,
            metadata=_adapter_result_metadata(adapter_result),
        )


@dataclass(slots=True)
class _RecorderEventSink:
    recorder: RecorderPort
    active_path: Path
    root: Path | None
    run_id: str
    agent: str
    adapter: str
    clock: Callable[[], datetime]
    sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            event = {
                "type": event_type,
                "timestamp": _ensure_utc(self.clock()).isoformat(),
                "agent": sanitize_text(self.agent),
                "run_id": self.run_id,
                "event_id": f"{self.run_id}-{sequence:04d}",
                "sequence": sequence,
                "harness_protocol_version": HARNESS_PROTOCOL_VERSION,
                "summary": sanitize_text(summary),
            }
            if payload:
                event["payload"] = _event_payload_metadata(dict(payload))
            self.recorder.append(self.active_path, event, root=self.root)


def _initial_session_fields(run_id: str, timestamp: datetime) -> dict[str, Any]:
    return {
        "timestamp": timestamp.isoformat(),
        "run_id": run_id,
        "event_id": f"{run_id}-0000",
        "sequence": 0,
        "harness_protocol_version": HARNESS_PROTOCOL_VERSION,
    }


def _adapter_result_metadata(adapter_result: AdapterResult | None) -> dict[str, Any] | None:
    if adapter_result is None or adapter_result.metadata is None:
        return None
    metadata = sanitize_metadata(adapter_result.metadata)
    usage = adapter_result.metadata.get("usage")
    if isinstance(usage, Mapping):
        metadata["usage"] = _safe_usage_metadata(usage)
    return metadata


def _event_payload_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = sanitize_metadata(payload)
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        metadata["usage"] = _safe_usage_metadata(usage)
    return metadata


def _safe_usage_metadata(usage: Mapping[str, Any]) -> dict[str, int | float]:
    safe_usage: dict[str, int | float] = {}
    for key, value in usage.items():
        if (
            isinstance(key, str)
            and key in _SAFE_USAGE_COUNTERS
            and isinstance(value, int | float)
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
        ):
            safe_usage[key] = value
    return safe_usage


def _base_payload(
    request: RunRequest,
    adapter: str,
    workspace: WorkspaceDisplay,
    status: HarnessStatus,
) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "agent": request.agent,
        "status": status.value,
        "cwd_name": workspace.name,
        "cwd_sha256": workspace.sha256,
        "argv_stored": request.capture_policy.record_argv,
        "stdout_stored": request.capture_policy.record_stdout,
        "stderr_stored": request.capture_policy.record_stderr,
        "raw_transcript_imported": request.capture_policy.import_raw_transcript,
        "record_file_paths": request.capture_policy.record_file_paths,
    }


def _markdown_summary(
    *,
    request: RunRequest,
    adapter: str,
    run_id: str,
    workspace: WorkspaceDisplay,
    status: HarnessStatus,
    exit_code: int,
    started_at: datetime,
    ended_at: datetime,
    changed_paths: tuple[str, ...],
    error_type: str | None,
    error_message: str | None,
) -> str:
    lines = [
        "# Summary",
        "",
        "Pipy harness run finalized.",
        "",
        f"- Run id: {run_id}",
        f"- Status: {status.value}",
        f"- Agent: {sanitize_text(request.agent)}",
        f"- Adapter: {adapter}",
        f"- Workspace: {workspace.name} ({workspace.sha256[:12]})",
        f"- Started: {started_at.isoformat()}",
        f"- Ended: {_ensure_utc(ended_at).isoformat()}",
        f"- Exit code: {exit_code}",
        "- Capture: partial lifecycle metadata only",
        "- Raw transcript imported: no",
        "- Stdout stored: no",
        "- Stderr stored: no",
        "- Full argv stored: no",
        "- Prompt or model output stored: no",
    ]
    if error_type is not None:
        lines.append(f"- Error type: {sanitize_text(error_type)}")
    if error_message is not None:
        lines.append(f"- Error detail: {error_message}")

    if request.capture_policy.record_file_paths:
        lines.append("- Changed file paths recorded: yes")
        if changed_paths:
            lines.extend(f"  - {sanitize_path(path)}" for path in changed_paths)
        elif error_type is not None:
            lines.append("  - none collected because the run did not complete")
        else:
            lines.append("  - none")
    else:
        lines.append("- Changed file paths recorded: no")

    lines.append("")
    return "\n".join(lines)


def _duration_seconds(started_at: datetime, ended_at: datetime) -> float:
    return max(0.0, (_ensure_utc(ended_at) - _ensure_utc(started_at)).total_seconds())


def _error_message(exc: Exception) -> str:
    message = sanitize_text(str(exc))
    return message or type(exc).__name__


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
