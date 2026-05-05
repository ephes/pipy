"""Value objects for the native pipy runtime bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus

PROVIDER_TOOL_INTENT_METADATA_KEY = "pipy_native_tool_intent"


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
class NativeRunOutput:
    """Native session result before adaptation into the harness result shape."""

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    final_text: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
