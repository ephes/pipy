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


RESUME_RELATIONSHIP_RESUME = "resume"
RESUME_RELATIONSHIP_BRANCH = "branch"
_RESUME_RELATIONSHIPS = frozenset({RESUME_RELATIONSHIP_RESUME, RESUME_RELATIONSHIP_BRANCH})

BRANCH_LABEL_MAX_LENGTH = 48


def validate_branch_label(label: str) -> str:
    """Validate and return a safe branch label.

    A branch label is a short, single-line, archive-safe identifier. It must
    not be a filesystem path, must not contain control characters, and must not
    look like a secret. The same defenses keep an unsafe label out of the
    metadata-first archive and the resumed-state UI.
    """

    if not isinstance(label, str):
        raise ValueError("branch label must be a string")
    stripped = label.strip()
    if not stripped:
        raise ValueError("branch label must not be empty")
    if len(stripped) > BRANCH_LABEL_MAX_LENGTH:
        raise ValueError(
            f"branch label must be at most {BRANCH_LABEL_MAX_LENGTH} characters"
        )
    if any(ord(character) < 32 for character in stripped):
        raise ValueError("branch label must be a single-line label")
    if any(separator in stripped for separator in ("/", "\\", "~")):
        raise ValueError("branch label must not be a filesystem path")
    if stripped in {".", ".."} or stripped.startswith("."):
        raise ValueError("branch label must not be a filesystem path")
    # Defense in depth against secret-shaped labels reaching the archive/UI.
    from pipy_harness.capture import sanitize_text

    if sanitize_text(stripped) == "[REDACTED]":
        raise ValueError("branch label must not contain sensitive data")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    if any(character not in allowed for character in stripped):
        raise ValueError(
            "branch label may contain only letters, digits, spaces, '.', '_', and '-'"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class SessionLineage:
    """Safe parent/branch lineage metadata for a resumed or forked session.

    Carries only allowlisted labels and counters — a parent record stem, the
    relationship kind, an optional validated branch label, the fork timestamp,
    and the prior provider/model/turn counters. It never carries prompts, model
    text, tool payloads, file contents, diffs, or summary text. The new session
    records this; the parent record is never mutated.
    """

    parent_session_id: str
    relationship: str
    fork_timestamp: str
    branch_label: str | None = None
    prior_provider_name: str | None = None
    prior_model_id: str | None = None
    prior_turn_count: int = 0

    def __post_init__(self) -> None:
        if not self.parent_session_id or "/" in self.parent_session_id or "\\" in self.parent_session_id:
            raise ValueError("parent_session_id must be a bare record stem")
        if self.relationship not in _RESUME_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(_RESUME_RELATIONSHIPS)}"
            )
        if self.relationship == RESUME_RELATIONSHIP_BRANCH:
            if self.branch_label is None:
                raise ValueError("a branch relationship requires a branch_label")
            object.__setattr__(
                self, "branch_label", validate_branch_label(self.branch_label)
            )
        elif self.branch_label is not None:
            object.__setattr__(
                self, "branch_label", validate_branch_label(self.branch_label)
            )
        if not isinstance(self.prior_turn_count, int) or isinstance(
            self.prior_turn_count, bool
        ) or self.prior_turn_count < 0:
            raise ValueError("prior_turn_count must be a non-negative integer")

    def archive_payload(self) -> dict[str, Any]:
        """Return the metadata-only mapping recorded into the new archive."""

        return {
            "parent_session_id": self.parent_session_id,
            "relationship": self.relationship,
            "fork_timestamp": self.fork_timestamp,
            "branch_label": self.branch_label,
            "prior_provider_name": self.prior_provider_name,
            "prior_model_id": self.prior_model_id,
            "prior_turn_count": self.prior_turn_count,
        }


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
    native_provider: str | None = None
    native_model: str | None = None
    resume: SessionLineage | None = None


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Privacy-safe invocation details prepared by an adapter."""

    command: Sequence[str]
    cwd: Path
    adapter: str
    command_executable: str
    goal: str | None = None
    native_provider: str | None = None
    native_model: str | None = None


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
    duration_seconds: float | None = None
    metadata: dict[str, Any] | None = None
