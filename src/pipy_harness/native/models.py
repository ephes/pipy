"""Value objects for the native pipy runtime bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
