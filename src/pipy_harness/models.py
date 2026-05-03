"""Value objects for the pipy harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Sequence

from pipy_session.recorder import SessionRecord

from pipy_harness.capture import CapturePolicy


class HarnessStatus(StrEnum):
    """Small run status vocabulary shared by harness events."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class RunRequest:
    """User request passed from the CLI into the harness runner."""

    agent: str
    slug: str
    command: Sequence[str]
    cwd: Path
    goal: str | None = None
    root: Path | None = None
    capture_policy: CapturePolicy = CapturePolicy()


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Privacy-safe subprocess invocation details prepared by an adapter."""

    command: Sequence[str]
    cwd: Path
    adapter: str
    command_executable: str


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Result returned by an adapter after running a native command."""

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    changed_paths: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Final harness result after the pipy record has been finalized."""

    run_id: str
    status: HarnessStatus
    exit_code: int
    record: SessionRecord
    error_type: str | None = None
    error_message: str | None = None
