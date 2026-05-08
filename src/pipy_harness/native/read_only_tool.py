"""Bounded read-only native workspace tools."""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import ClassVar

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.models import (
    NativeReadOnlyToolLimits,
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeToolApprovalMode,
    NativeToolSandboxMode,
    NativeToolStatus,
)

_PIPY_AUTHORITY = "pipy-owned"
_TEXT_ENCODING = "utf-8"
_SHELLISH_MARKERS = frozenset({"~", "$", "`", "*", "?", "[", "]", "{", "}", "|", ";", "&", "<", ">"})
_GENERATED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pipy",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_GENERATED_SUFFIXES = frozenset(
    {
        ".a",
        ".class",
        ".db",
        ".dll",
        ".dylib",
        ".gif",
        ".gz",
        ".jar",
        ".jpeg",
        ".jpg",
        ".lock",
        ".map",
        ".o",
        ".pdf",
        ".png",
        ".pyc",
        ".pyo",
        ".so",
        ".sqlite",
        ".tar",
        ".tgz",
        ".webp",
        ".zip",
    }
)
_CONTROL_CHARS = frozenset(chr(value) for value in range(32)) - frozenset({"\n", "\r", "\t"})


class NativeReadOnlyApprovalDecision(StrEnum):
    """Closed labels for pipy-owned read approval decisions."""

    ALLOWED = "allowed"
    DENIED = "denied"
    SKIPPED = "skipped"
    FAILED = "failed"


class NativeExplicitFileExcerptReason(StrEnum):
    """Safe reason labels for explicit file excerpt outcomes."""

    READ_SUCCEEDED = "read_succeeded"
    UNSUPPORTED_REQUEST_KIND = "unsupported_request_kind"
    APPROVAL_NOT_ALLOWED = "approval_not_allowed"
    UNSAFE_SANDBOX = "unsafe_sandbox"
    UNSAFE_TARGET = "unsafe_target"
    MISSING_FILE = "missing_file"
    DIRECTORY_TARGET = "directory_target"
    NOT_REGULAR_FILE = "not_regular_file"
    UNREADABLE_FILE = "unreadable_file"
    IGNORED_OR_GENERATED_FILE = "ignored_or_generated_file"
    OVERSIZED_FILE = "oversized_file"
    BINARY_FILE = "binary_file"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    SECRET_LOOKING_CONTENT = "secret_looking_content"
    LIMIT_EXCEEDED = "limit_exceeded"


@dataclass(frozen=True, slots=True)
class NativeReadOnlyGateDecision:
    """Pipy-owned approval gate data required before a read can occur."""

    approval_decision: NativeReadOnlyApprovalDecision
    decision_authority: str = _PIPY_AUTHORITY
    reason_label: str | None = None

    def __post_init__(self) -> None:
        if self.decision_authority != _PIPY_AUTHORITY:
            raise ValueError("read-only gate decision must be pipy-owned")
        if self.reason_label is not None:
            _validate_safe_label(self.reason_label, field_name="reason_label")

    @property
    def allowed(self) -> bool:
        return self.approval_decision == NativeReadOnlyApprovalDecision.ALLOWED


@dataclass(frozen=True, slots=True)
class NativeExplicitFileExcerptTarget:
    """Pipy-owned explicit file target before workspace resolution."""

    workspace_relative_path: str
    target_authority: str = _PIPY_AUTHORITY

    def __post_init__(self) -> None:
        if self.target_authority != _PIPY_AUTHORITY:
            raise ValueError("explicit file target must be pipy-owned")
        _validate_workspace_relative_path(self.workspace_relative_path)


@dataclass(frozen=True, slots=True)
class NativeInMemoryFileExcerpt:
    """Sanitized excerpt text kept in memory for a future provider turn."""

    text: str
    source_label: str
    byte_count: int
    line_count: int
    encoding: str = _TEXT_ENCODING


@dataclass(frozen=True, slots=True)
class NativeExplicitFileExcerptResult:
    """Result for one bounded explicit file excerpt request."""

    status: NativeToolStatus
    reason_label: NativeExplicitFileExcerptReason
    tool_request_id: str
    turn_index: int
    request_kind: NativeReadOnlyToolRequestKind
    started_at: datetime
    ended_at: datetime
    source_label: str | None = None
    source_sha256: str | None = None
    byte_count: int = 0
    line_count: int = 0
    excerpt: NativeInMemoryFileExcerpt | None = None
    approval_policy: NativeToolApprovalMode = NativeToolApprovalMode.REQUIRED
    approval_decision: NativeReadOnlyApprovalDecision | None = None
    sandbox_policy: NativeToolSandboxMode = NativeToolSandboxMode.READ_ONLY_WORKSPACE
    workspace_read_allowed: bool = True
    filesystem_mutation_allowed: bool = False
    shell_execution_allowed: bool = False
    network_access_allowed: bool = False
    tool_payloads_stored: bool = False
    stdout_stored: bool = False
    stderr_stored: bool = False
    diffs_stored: bool = False
    file_contents_stored: bool = False
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    raw_transcript_imported: bool = False

    def archive_metadata(self) -> dict[str, object]:
        """Return the metadata-only shape allowed for archive/event surfaces."""

        return {
            "tool_request_id": self.tool_request_id,
            "turn_index": self.turn_index,
            "tool_name": "read_only_repo_inspection",
            "tool_kind": "read_only_workspace",
            "request_kind": self.request_kind.value,
            "status": self.status.value,
            "reason_label": self.reason_label.value,
            "duration_seconds": _duration_seconds(self.started_at, self.ended_at),
            "approval_policy": self.approval_policy.value,
            "approval_required": self.approval_policy == NativeToolApprovalMode.REQUIRED,
            "approval_resolved": self.approval_decision is not None,
            "approval_decision": self.approval_decision.value if self.approval_decision else None,
            "sandbox_policy": self.sandbox_policy.value,
            "workspace_read_allowed": self.workspace_read_allowed,
            "filesystem_mutation_allowed": self.filesystem_mutation_allowed,
            "shell_execution_allowed": self.shell_execution_allowed,
            "network_access_allowed": self.network_access_allowed,
            "source_label": self.source_label,
            "source_sha256": self.source_sha256,
            "byte_count": self.byte_count,
            "line_count": self.line_count,
            "excerpt_count": 1 if self.status == NativeToolStatus.SUCCEEDED else 0,
            "distinct_source_file_count": 1 if self.status == NativeToolStatus.SUCCEEDED else 0,
            # Keep archive-facing storage flags literal false even if a result is
            # constructed incorrectly; excerpt text is in-memory only.
            "tool_payloads_stored": False,
            "stdout_stored": False,
            "stderr_stored": False,
            "diffs_stored": False,
            "file_contents_stored": False,
            "prompt_stored": False,
            "model_output_stored": False,
            "provider_responses_stored": False,
            "raw_transcript_imported": False,
        }


@dataclass(frozen=True, slots=True)
class NativeExplicitFileExcerptTool:
    """Read one approved, bounded text file excerpt from a workspace."""

    workspace: Path

    SAFE_METADATA_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "approval_decision",
            "approval_policy",
            "approval_required",
            "approval_resolved",
            "byte_count",
            "diffs_stored",
            "distinct_source_file_count",
            "duration_seconds",
            "excerpt_count",
            "file_contents_stored",
            "filesystem_mutation_allowed",
            "line_count",
            "model_output_stored",
            "network_access_allowed",
            "prompt_stored",
            "provider_responses_stored",
            "raw_transcript_imported",
            "reason_label",
            "request_kind",
            "sandbox_policy",
            "shell_execution_allowed",
            "source_label",
            "source_sha256",
            "status",
            "stderr_stored",
            "stdout_stored",
            "tool_kind",
            "tool_name",
            "tool_payloads_stored",
            "tool_request_id",
            "turn_index",
            "workspace_read_allowed",
        }
    )

    @property
    def name(self) -> str:
        return "read_only_repo_inspection"

    def invoke(
        self,
        request: NativeReadOnlyToolRequest,
        gate_decision: NativeReadOnlyGateDecision,
        target: NativeExplicitFileExcerptTarget,
    ) -> NativeExplicitFileExcerptResult:
        started_at = datetime.now(UTC)
        base_result = _ResultBuilder(
            request=request,
            started_at=started_at,
            approval_decision=gate_decision.approval_decision,
            target=target,
        )

        reason = _request_gate_reason(request)
        if reason is not None:
            return base_result.skipped(reason)
        if request.approval_policy.mode == NativeToolApprovalMode.REQUIRED and not gate_decision.allowed:
            return base_result.skipped(NativeExplicitFileExcerptReason.APPROVAL_NOT_ALLOWED)

        workspace = self.workspace.resolve()
        candidate = (workspace / target.workspace_relative_path).resolve()
        if not _is_relative_to(candidate, workspace):
            return base_result.skipped(NativeExplicitFileExcerptReason.UNSAFE_TARGET)
        if _is_ignored_or_generated(target.workspace_relative_path, workspace):
            return base_result.skipped(NativeExplicitFileExcerptReason.IGNORED_OR_GENERATED_FILE)
        if not candidate.exists():
            return base_result.skipped(NativeExplicitFileExcerptReason.MISSING_FILE)
        if candidate.is_dir():
            return base_result.skipped(NativeExplicitFileExcerptReason.DIRECTORY_TARGET)
        if not candidate.is_file():
            return base_result.skipped(NativeExplicitFileExcerptReason.NOT_REGULAR_FILE)
        try:
            stat_result = candidate.stat()
        except OSError:
            return base_result.skipped(NativeExplicitFileExcerptReason.UNREADABLE_FILE)
        if stat_result.st_mode & 0o444 == 0:
            return base_result.skipped(NativeExplicitFileExcerptReason.UNREADABLE_FILE)

        byte_limit = _byte_limit(request.limits)
        line_limit = _line_limit(request.limits)
        if byte_limit <= 0 or line_limit <= 0:
            return base_result.skipped(NativeExplicitFileExcerptReason.LIMIT_EXCEEDED)
        if stat_result.st_size > byte_limit:
            return base_result.skipped(NativeExplicitFileExcerptReason.OVERSIZED_FILE)

        try:
            raw = candidate.read_bytes()
        except OSError:
            return base_result.skipped(NativeExplicitFileExcerptReason.UNREADABLE_FILE)
        if b"\0" in raw:
            return base_result.skipped(NativeExplicitFileExcerptReason.BINARY_FILE)
        try:
            text = raw.decode(_TEXT_ENCODING)
        except UnicodeDecodeError:
            return base_result.skipped(NativeExplicitFileExcerptReason.UNSUPPORTED_ENCODING)
        if any(char in _CONTROL_CHARS for char in text):
            return base_result.skipped(NativeExplicitFileExcerptReason.BINARY_FILE)
        if looks_sensitive(text):
            return base_result.skipped(NativeExplicitFileExcerptReason.SECRET_LOOKING_CONTENT)

        line_count = _line_count(text)
        byte_count = len(raw)
        if byte_count > byte_limit or line_count > line_limit:
            return base_result.skipped(NativeExplicitFileExcerptReason.LIMIT_EXCEEDED)

        source_label = _source_label(target.workspace_relative_path)
        source_sha256 = _source_hash(target.workspace_relative_path)
        excerpt = NativeInMemoryFileExcerpt(
            text=text,
            source_label=source_label,
            byte_count=byte_count,
            line_count=line_count,
        )
        return NativeExplicitFileExcerptResult(
            status=NativeToolStatus.SUCCEEDED,
            reason_label=NativeExplicitFileExcerptReason.READ_SUCCEEDED,
            tool_request_id=request.tool_request_id,
            turn_index=request.turn_index,
            request_kind=request.request_kind,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            source_label=source_label,
            source_sha256=source_sha256,
            byte_count=byte_count,
            line_count=line_count,
            excerpt=excerpt,
            approval_policy=request.approval_policy.mode,
            approval_decision=gate_decision.approval_decision,
            sandbox_policy=request.sandbox_policy.mode,
            workspace_read_allowed=request.sandbox_policy.workspace_read_allowed,
            filesystem_mutation_allowed=request.sandbox_policy.filesystem_mutation_allowed,
            shell_execution_allowed=request.sandbox_policy.shell_execution_allowed,
            network_access_allowed=request.sandbox_policy.network_access_allowed,
        )


@dataclass(frozen=True, slots=True)
class _ResultBuilder:
    request: NativeReadOnlyToolRequest
    started_at: datetime
    approval_decision: NativeReadOnlyApprovalDecision | None = None
    target: NativeExplicitFileExcerptTarget | None = None

    def skipped(self, reason: NativeExplicitFileExcerptReason) -> NativeExplicitFileExcerptResult:
        source_label = _source_label(self.target.workspace_relative_path) if self.target else None
        source_sha256 = _source_hash(self.target.workspace_relative_path) if self.target else None
        return NativeExplicitFileExcerptResult(
            status=NativeToolStatus.SKIPPED,
            reason_label=reason,
            tool_request_id=self.request.tool_request_id,
            turn_index=self.request.turn_index,
            request_kind=self.request.request_kind,
            started_at=self.started_at,
            ended_at=datetime.now(UTC),
            source_label=source_label,
            source_sha256=source_sha256,
            approval_policy=self.request.approval_policy.mode,
            approval_decision=self.approval_decision,
            sandbox_policy=self.request.sandbox_policy.mode,
            workspace_read_allowed=self.request.sandbox_policy.workspace_read_allowed,
            filesystem_mutation_allowed=self.request.sandbox_policy.filesystem_mutation_allowed,
            shell_execution_allowed=self.request.sandbox_policy.shell_execution_allowed,
            network_access_allowed=self.request.sandbox_policy.network_access_allowed,
        )


def _request_gate_reason(
    request: NativeReadOnlyToolRequest,
) -> NativeExplicitFileExcerptReason | None:
    if request.request_kind != NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT:
        return NativeExplicitFileExcerptReason.UNSUPPORTED_REQUEST_KIND
    if request.approval_policy.mode not in {
        NativeToolApprovalMode.NOT_REQUIRED,
        NativeToolApprovalMode.REQUIRED,
    }:
        return NativeExplicitFileExcerptReason.APPROVAL_NOT_ALLOWED
    sandbox = request.sandbox_policy
    if sandbox.mode != NativeToolSandboxMode.READ_ONLY_WORKSPACE:
        return NativeExplicitFileExcerptReason.UNSAFE_SANDBOX
    if sandbox.workspace_read_allowed is not True:
        return NativeExplicitFileExcerptReason.UNSAFE_SANDBOX
    if (
        sandbox.filesystem_mutation_allowed is not False
        or sandbox.shell_execution_allowed is not False
        or sandbox.network_access_allowed is not False
    ):
        return NativeExplicitFileExcerptReason.UNSAFE_SANDBOX
    if request.limits.max_excerpts < 1 or request.limits.max_distinct_source_files < 1:
        return NativeExplicitFileExcerptReason.LIMIT_EXCEEDED
    return None


def _validate_workspace_relative_path(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("workspace_relative_path must be a string")
    if value != value.strip() or not value:
        raise ValueError("workspace_relative_path must be non-empty and normalized")
    if any(char in value for char in _SHELLISH_MARKERS):
        raise ValueError("workspace_relative_path must not use shell expansion")
    if "\\" in value or "\x00" in value:
        raise ValueError("workspace_relative_path must use normalized separators")
    if any(ord(char) < 32 for char in value):
        raise ValueError("workspace_relative_path must not contain control characters")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("workspace_relative_path must be relative")
    if value.startswith("./"):
        raise ValueError("workspace_relative_path must be normalized")
    parts = posix_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("workspace_relative_path must stay inside the workspace")
    if any(looks_sensitive(part) for part in parts):
        raise ValueError("workspace_relative_path must not look sensitive")


def _validate_safe_label(value: str, *, field_name: str) -> None:
    if not value or len(value) > 80:
        raise ValueError(f"{field_name} must be a short non-empty label")
    if any(separator in value for separator in ("/", "\\", "~", " ")):
        raise ValueError(f"{field_name} must not be a filesystem path")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError(f"{field_name} must not be a filesystem path")


def _is_ignored_or_generated(relative_path: str, workspace: Path) -> bool:
    posix_path = PurePosixPath(relative_path)
    if any(part in _GENERATED_PARTS for part in posix_path.parts):
        return True
    name = posix_path.name
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return True
    if "".join(posix_path.suffixes[-2:]) == ".d.ts":
        return True
    if any(suffix in _GENERATED_SUFFIXES for suffix in posix_path.suffixes):
        return True
    return _matches_root_ignore(relative_path, workspace)


def _matches_root_ignore(relative_path: str, workspace: Path) -> bool:
    ignore_file = workspace / ".gitignore"
    try:
        lines = ignore_file.read_text(encoding=_TEXT_ENCODING).splitlines()
    except OSError:
        return False
    normalized = relative_path.strip("/")
    parts = PurePosixPath(normalized).parts
    for raw_line in lines:
        pattern = raw_line.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue
        pattern = pattern.rstrip()
        if pattern.endswith("/"):
            directory = pattern.strip("/")
            if directory and any(fnmatch.fnmatch(part, directory) for part in parts):
                return True
            continue
        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        if "/" in pattern or anchored:
            if fnmatch.fnmatch(normalized, pattern):
                return True
            continue
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def _byte_limit(limits: NativeReadOnlyToolLimits) -> int:
    return min(limits.per_excerpt_bytes, limits.per_source_file_bytes, limits.total_context_bytes)


def _line_limit(limits: NativeReadOnlyToolLimits) -> int:
    return min(limits.per_excerpt_lines, limits.per_source_file_lines, limits.total_context_lines)


def _line_count(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _source_label(relative_path: str) -> str:
    name = PurePosixPath(relative_path).name
    if not name or looks_sensitive(name):
        return "workspace-file"
    return name


def _source_hash(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _is_relative_to(candidate: Path, workspace: Path) -> bool:
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return False
    return True


def _duration_seconds(started_at: datetime, ended_at: datetime) -> float:
    return max(0.0, (ended_at.astimezone(UTC) - started_at.astimezone(UTC)).total_seconds())
