"""Value objects for the native pipy runtime bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pipy_harness.models import HarnessStatus

PROVIDER_TOOL_INTENT_METADATA_KEY = "pipy_native_tool_intent"
NATIVE_TOOL_OBSERVATION_RECORDED_EVENT = "native.tool.observation.recorded"
NATIVE_TOOL_OBSERVATION_STORAGE_KEYS = frozenset(
    {
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
    }
)
NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS = frozenset(
    {
        "tool_request_id",
        "turn_index",
        "tool_name",
        "tool_kind",
        "status",
        "reason_label",
        "duration_seconds",
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
    }
)


@dataclass(frozen=True, slots=True)
class NativeRunInput:
    """One native pipy turn request owned by the native runtime boundary."""

    goal: str
    cwd: Path
    provider_name: str
    model_id: str
    system_prompt_id: str
    system_prompt_version: str


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Request sent across the native provider port."""

    system_prompt: str
    user_prompt: str
    provider_name: str
    model_id: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Result returned by a native provider."""

    status: HarnessStatus
    provider_name: str
    model_id: str
    started_at: datetime
    ended_at: datetime
    final_text: str | None = None
    usage: Mapping[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


class NativeToolStatus(StrEnum):
    """Lifecycle vocabulary for one native tool boundary invocation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class NativeToolObservationStatus(StrEnum):
    """Terminal status labels allowed on future sanitized tool observations."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class NativeToolObservationReason(StrEnum):
    """Closed safe reason labels for future sanitized tool observation events."""

    TOOL_RESULT_SUCCEEDED = "tool_result_succeeded"
    TOOL_RESULT_FAILED = "tool_result_failed"
    TOOL_RESULT_SKIPPED = "tool_result_skipped"
    UNSUPPORTED_OBSERVATION = "unsupported_observation"
    UNSAFE_OBSERVATION = "unsafe_observation"


class NativeToolApprovalMode(StrEnum):
    """Approval posture represented as data before enforcement exists."""

    NOT_REQUIRED = "not-required"
    REQUIRED = "required"


class NativeToolSandboxMode(StrEnum):
    """Sandbox posture represented as data before enforcement exists."""

    NO_WORKSPACE_ACCESS = "no-workspace-access"
    READ_ONLY_WORKSPACE = "read-only-workspace"
    MUTATING_WORKSPACE = "mutating-workspace"


class NativeReadOnlyToolRequestKind(StrEnum):
    """Safe labels for future bounded read-only workspace inspection requests."""

    EXPLICIT_FILE_EXCERPT = "explicit-file-excerpt"
    SEARCH_EXCERPT = "search-excerpt"


@dataclass(frozen=True, slots=True)
class NativeToolRequestIdentity:
    """Pipy-owned identity for the current bounded native tool request.

    The native runtime currently has exactly one provider turn and at most one
    no-op tool request. Provider-owned ids and turn indexes are parsed only as
    unsafe/unsupported input; they are not the identity source.
    """

    turn_index: int
    request_position: int

    CURRENT_TURN_INDEX: ClassVar[int] = 0
    CURRENT_REQUEST_POSITION: ClassVar[int] = 0

    @classmethod
    def current_noop(cls) -> "NativeToolRequestIdentity":
        return cls(
            turn_index=cls.CURRENT_TURN_INDEX,
            request_position=cls.CURRENT_REQUEST_POSITION,
        )

    def __post_init__(self) -> None:
        if self.turn_index != self.CURRENT_TURN_INDEX:
            raise ValueError("native tool turn_index is bounded to 0")
        if self.request_position != self.CURRENT_REQUEST_POSITION:
            raise ValueError("native tool request_position is bounded to 0")

    @property
    def request_id(self) -> str:
        # This formula is valid only under the current one-turn/one-request bound.
        # Future multi-turn or multi-request work must replace the identity shape.
        request_number = self.turn_index + self.request_position + 1
        return f"native-tool-{request_number:04d}"


@dataclass(frozen=True, slots=True)
class NativeToolApprovalPolicy:
    """Approval policy attached to a native tool request."""

    mode: NativeToolApprovalMode = NativeToolApprovalMode.NOT_REQUIRED

    @property
    def label(self) -> str:
        return self.mode.value


@dataclass(frozen=True, slots=True)
class NativeToolSandboxPolicy:
    """Sandbox policy attached to a native tool request."""

    mode: NativeToolSandboxMode = NativeToolSandboxMode.NO_WORKSPACE_ACCESS
    workspace_read_allowed: bool = False
    filesystem_mutation_allowed: bool = False
    shell_execution_allowed: bool = False
    network_access_allowed: bool = False

    @property
    def label(self) -> str:
        return self.mode.value


@dataclass(frozen=True, slots=True)
class NativeToolRequest:
    """Privacy-safe request sent across the native tool port."""

    request_id: str
    tool_name: str
    tool_kind: str
    approval_policy: NativeToolApprovalPolicy
    sandbox_policy: NativeToolSandboxPolicy
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NativeReadOnlyToolLimits:
    """Upper-bound metadata for future bounded read-only workspace inspection."""

    per_excerpt_bytes: int = 4 * 1024
    per_excerpt_lines: int = 80
    per_source_file_bytes: int = 8 * 1024
    per_source_file_lines: int = 160
    total_context_bytes: int = 24 * 1024
    total_context_lines: int = 480
    max_excerpts: int = 12
    max_distinct_source_files: int = 6

    MAX_PER_EXCERPT_BYTES: ClassVar[int] = 4 * 1024
    MAX_PER_EXCERPT_LINES: ClassVar[int] = 80
    MAX_PER_SOURCE_FILE_BYTES: ClassVar[int] = 8 * 1024
    MAX_PER_SOURCE_FILE_LINES: ClassVar[int] = 160
    MAX_TOTAL_CONTEXT_BYTES: ClassVar[int] = 24 * 1024
    MAX_TOTAL_CONTEXT_LINES: ClassVar[int] = 480
    MAX_EXCERPTS: ClassVar[int] = 12
    MAX_DISTINCT_SOURCE_FILES: ClassVar[int] = 6

    def __post_init__(self) -> None:
        for field_name, upper_bound in (
            ("per_excerpt_bytes", self.MAX_PER_EXCERPT_BYTES),
            ("per_excerpt_lines", self.MAX_PER_EXCERPT_LINES),
            ("per_source_file_bytes", self.MAX_PER_SOURCE_FILE_BYTES),
            ("per_source_file_lines", self.MAX_PER_SOURCE_FILE_LINES),
            ("total_context_bytes", self.MAX_TOTAL_CONTEXT_BYTES),
            ("total_context_lines", self.MAX_TOTAL_CONTEXT_LINES),
            ("max_excerpts", self.MAX_EXCERPTS),
            ("max_distinct_source_files", self.MAX_DISTINCT_SOURCE_FILES),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be an integer")
            if value < 0 or value > upper_bound:
                raise ValueError(f"{field_name} must be between 0 and {upper_bound}")


@dataclass(frozen=True, slots=True)
class NativeReadOnlyToolRequest:
    """Inert metadata-only request shape for future read-only workspace tools."""

    tool_request_id: str
    turn_index: int
    request_kind: NativeReadOnlyToolRequestKind
    tool_name: str = "read_only_repo_inspection"
    tool_kind: str = "read_only_workspace"
    approval_policy: NativeToolApprovalPolicy = field(
        default_factory=lambda: NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED)
    )
    sandbox_policy: NativeToolSandboxPolicy = field(
        default_factory=lambda: NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
        )
    )
    limits: NativeReadOnlyToolLimits = field(default_factory=NativeReadOnlyToolLimits)
    scope_label: str | None = None
    tool_payloads_stored: bool = False
    stdout_stored: bool = False
    stderr_stored: bool = False
    diffs_stored: bool = False
    file_contents_stored: bool = False
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    raw_transcript_imported: bool = False

    def __post_init__(self) -> None:
        identity = NativeToolRequestIdentity.current_noop()
        if self.tool_request_id != identity.request_id:
            raise ValueError("read-only workspace inspection requires pipy-owned tool_request_id")
        if self.turn_index != identity.turn_index:
            raise ValueError("read-only workspace inspection requires pipy-owned turn_index")
        if self.approval_policy.mode != NativeToolApprovalMode.REQUIRED:
            raise ValueError("read-only workspace inspection requires approval")
        if self.sandbox_policy.mode != NativeToolSandboxMode.READ_ONLY_WORKSPACE:
            raise ValueError("read-only workspace inspection requires read-only-workspace sandbox")
        if self.sandbox_policy.workspace_read_allowed is not True:
            raise ValueError("read-only workspace inspection requires workspace_read_allowed")
        for field_name in (
            "filesystem_mutation_allowed",
            "shell_execution_allowed",
            "network_access_allowed",
        ):
            if getattr(self.sandbox_policy, field_name) is not False:
                raise ValueError(f"read-only workspace inspection forbids {field_name}")
        for field_name in (
            "tool_payloads_stored",
            "stdout_stored",
            "stderr_stored",
            "diffs_stored",
            "file_contents_stored",
            "prompt_stored",
            "model_output_stored",
            "provider_responses_stored",
            "raw_transcript_imported",
        ):
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false for inert read-only requests")
        if self.scope_label is not None:
            _validate_scope_label(self.scope_label)


@dataclass(frozen=True, slots=True)
class NativeToolIntent:
    """Sanitized internal provider-to-tool intent.

    This is not a raw provider tool-call object. It carries only safe labels,
    policy booleans, and optional sanitized metadata for one bounded native
    no-op invocation.
    """

    request_id: str
    tool_name: str
    tool_kind: str
    turn_index: int
    intent_source: str
    approval_policy: NativeToolApprovalPolicy
    sandbox_policy: NativeToolSandboxPolicy
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NativeToolResult:
    """Privacy-safe result returned by a native tool."""

    request_id: str
    tool_name: str
    status: NativeToolStatus
    started_at: datetime
    ended_at: datetime
    metadata: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class NativeToolObservation:
    """Sanitized internal observation shape for a future post-tool turn.

    The runtime does not emit, archive, or provider-forward this value yet.
    """

    tool_request_id: str
    turn_index: int
    tool_name: str
    tool_kind: str
    status: NativeToolObservationStatus
    reason_label: NativeToolObservationReason | None = None
    duration_seconds: float | None = None
    tool_payloads_stored: bool = False
    stdout_stored: bool = False
    stderr_stored: bool = False
    diffs_stored: bool = False
    file_contents_stored: bool = False
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    raw_transcript_imported: bool = False


@dataclass(frozen=True, slots=True)
class NativeRunOutput:
    """Native session result before adaptation into the harness result shape."""

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    final_text: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    usage: Mapping[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


def _validate_scope_label(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("scope_label must be a string")
    if not value or len(value) > 80:
        raise ValueError("scope_label must be a short non-empty label")
    if any(separator in value for separator in ("/", "\\", "~")):
        raise ValueError("scope_label must not be a filesystem path")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError("scope_label must not be a filesystem path")
