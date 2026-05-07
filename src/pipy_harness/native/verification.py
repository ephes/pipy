"""Allowlisted native verification command boundary."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pipy_harness.native.models import (
    NATIVE_VERIFICATION_STORAGE_KEYS,
    NativeToolApprovalMode,
    NativeToolSandboxMode,
    NativeToolStatus,
    NativeVerificationCommand,
    NativeVerificationRequest,
)
from pipy_harness.native.read_only_tool import _duration_seconds, _validate_safe_label

_HUMAN_REVIEWED_AUTHORITY = "pipy-owned-human-reviewed"
_JUST_CHECK_ARGV = ("just", "check")
_SAFE_COMMAND_LABEL_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class NativeVerificationApprovalDecision(StrEnum):
    """Closed labels for pipy-owned verification approval decisions."""

    ALLOWED = "allowed"
    DENIED = "denied"
    SKIPPED = "skipped"
    FAILED = "failed"


class NativeVerificationReason(StrEnum):
    """Safe reason labels for supervised verification outcomes."""

    VERIFICATION_SUCCEEDED = "verification_succeeded"
    UNSUPPORTED_COMMAND = "unsupported_command"
    UNSAFE_COMMAND = "unsafe_command"
    APPROVAL_NOT_ALLOWED = "approval_not_allowed"
    UNSAFE_SANDBOX = "unsafe_sandbox"
    MISSING_EXECUTABLE = "missing_executable"
    COMMAND_FAILED = "command_failed"
    EXECUTION_FAILED = "execution_failed"


@dataclass(frozen=True, slots=True)
class NativeVerificationGateDecision:
    """Pipy-owned approval gate data required before command execution."""

    approval_decision: NativeVerificationApprovalDecision
    decision_authority: str = _HUMAN_REVIEWED_AUTHORITY
    reason_label: str | None = None

    def __post_init__(self) -> None:
        if self.decision_authority != _HUMAN_REVIEWED_AUTHORITY:
            raise ValueError("verification gate decision must be pipy-owned and human-reviewed")
        if self.reason_label is not None:
            _validate_safe_label(self.reason_label, field_name="reason_label")

    @property
    def allowed(self) -> bool:
        return self.approval_decision == NativeVerificationApprovalDecision.ALLOWED


@dataclass(frozen=True, slots=True)
class NativeVerificationResult:
    """Result for one allowlisted supervised verification command."""

    status: NativeToolStatus
    reason_label: NativeVerificationReason
    tool_request_id: str
    turn_index: int
    command_label: str
    started_at: datetime
    ended_at: datetime
    exit_code: int | None = None
    approval_policy: NativeToolApprovalMode = NativeToolApprovalMode.REQUIRED
    approval_decision: NativeVerificationApprovalDecision | None = None
    sandbox_policy: NativeToolSandboxMode = NativeToolSandboxMode.READ_ONLY_WORKSPACE
    workspace_read_allowed: bool = True
    filesystem_mutation_allowed: bool = False
    shell_execution_allowed: bool = True
    network_access_allowed: bool = False
    stdout_stored: bool = False
    stderr_stored: bool = False
    command_output_stored: bool = False
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    raw_transcript_imported: bool = False
    scope_label: str | None = None
    error_label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason_label, NativeVerificationReason):
            raise ValueError("verification result reason_label must be a native label")
        if self.command_label not in {
            NativeVerificationCommand.JUST_CHECK.value,
            "unsupported",
            "unsafe",
        }:
            raise ValueError("verification result command_label must be a safe label")
        if self.error_label is not None:
            _validate_safe_label(self.error_label, field_name="error_label")
        for field_name in NATIVE_VERIFICATION_STORAGE_KEYS:
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false for verification results")
        if self.scope_label is not None:
            _validate_safe_label(self.scope_label, field_name="scope_label")

    def archive_metadata(self) -> dict[str, object]:
        """Return the metadata-only shape allowed for archive/event surfaces."""

        return {
            "tool_request_id": self.tool_request_id,
            "turn_index": self.turn_index,
            "command_label": self.command_label,
            "status": self.status.value,
            "reason_label": self.reason_label.value,
            "error_label": self.error_label,
            "exit_code": self.exit_code,
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
            "scope_label": self.scope_label,
            "stdout_stored": False,
            "stderr_stored": False,
            "command_output_stored": False,
            "prompt_stored": False,
            "model_output_stored": False,
            "provider_responses_stored": False,
            "raw_transcript_imported": False,
        }


@dataclass(frozen=True, slots=True)
class NativeVerificationTool:
    """Run one approved allowlisted verification command in a workspace."""

    workspace: Path
    executable_resolver: Callable[[str], str | None] = shutil.which
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run

    @property
    def name(self) -> str:
        return "verification"

    def invoke(
        self,
        request: NativeVerificationRequest,
        gate_decision: NativeVerificationGateDecision,
    ) -> NativeVerificationResult:
        started_at = datetime.now(UTC)
        builder = _ResultBuilder(request=request, gate_decision=gate_decision, started_at=started_at)

        command_reason = _command_reason(request.command_label)
        if command_reason is not None:
            return builder.skipped(command_reason)
        policy_reason = _request_gate_reason(request)
        if policy_reason is not None:
            return builder.skipped(policy_reason)
        if not gate_decision.allowed:
            return builder.skipped(NativeVerificationReason.APPROVAL_NOT_ALLOWED)
        if self.executable_resolver(_JUST_CHECK_ARGV[0]) is None:
            return builder.skipped(
                NativeVerificationReason.MISSING_EXECUTABLE,
                error_label=NativeVerificationReason.MISSING_EXECUTABLE.value,
            )

        try:
            completed = self.runner(
                _JUST_CHECK_ARGV,
                cwd=self.workspace.resolve(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return builder.failed(
                NativeVerificationReason.EXECUTION_FAILED,
                error_label=NativeVerificationReason.EXECUTION_FAILED.value,
            )

        exit_code = int(completed.returncode)
        if exit_code != 0:
            return builder.failed(
                NativeVerificationReason.COMMAND_FAILED,
                exit_code=exit_code,
                error_label=NativeVerificationReason.COMMAND_FAILED.value,
            )
        return builder.succeeded(exit_code=exit_code)


@dataclass(frozen=True, slots=True)
class _ResultBuilder:
    request: NativeVerificationRequest
    gate_decision: NativeVerificationGateDecision
    started_at: datetime

    def succeeded(self, *, exit_code: int) -> NativeVerificationResult:
        return self._result(
            status=NativeToolStatus.SUCCEEDED,
            reason=NativeVerificationReason.VERIFICATION_SUCCEEDED,
            exit_code=exit_code,
        )

    def skipped(
        self,
        reason: NativeVerificationReason,
        *,
        error_label: str | None = None,
    ) -> NativeVerificationResult:
        return self._result(
            status=NativeToolStatus.SKIPPED,
            reason=reason,
            exit_code=None,
            error_label=error_label,
        )

    def failed(
        self,
        reason: NativeVerificationReason,
        *,
        exit_code: int | None = None,
        error_label: str | None = None,
    ) -> NativeVerificationResult:
        return self._result(
            status=NativeToolStatus.FAILED,
            reason=reason,
            exit_code=exit_code,
            error_label=error_label,
        )

    def _result(
        self,
        *,
        status: NativeToolStatus,
        reason: NativeVerificationReason,
        exit_code: int | None,
        error_label: str | None = None,
    ) -> NativeVerificationResult:
        return NativeVerificationResult(
            status=status,
            reason_label=reason,
            tool_request_id=self.request.tool_request_id,
            turn_index=self.request.turn_index,
            command_label=safe_verification_command_label(self.request.command_label),
            started_at=self.started_at,
            ended_at=datetime.now(UTC),
            exit_code=exit_code,
            approval_policy=self.request.approval_policy.mode,
            approval_decision=self.gate_decision.approval_decision,
            sandbox_policy=self.request.sandbox_policy.mode,
            workspace_read_allowed=self.request.sandbox_policy.workspace_read_allowed,
            filesystem_mutation_allowed=self.request.sandbox_policy.filesystem_mutation_allowed,
            shell_execution_allowed=self.request.sandbox_policy.shell_execution_allowed,
            network_access_allowed=self.request.sandbox_policy.network_access_allowed,
            scope_label=self.request.scope_label,
            error_label=error_label,
        )


def _command_reason(command_label: NativeVerificationCommand | str) -> NativeVerificationReason | None:
    if command_label == NativeVerificationCommand.JUST_CHECK:
        return None
    if command_label == NativeVerificationCommand.JUST_CHECK.value:
        return None
    if not isinstance(command_label, str):
        return NativeVerificationReason.UNSAFE_COMMAND
    # This classifies archive-safe labels only. The execution boundary is the
    # exact allowlist above plus the hardcoded argv passed without shell=True.
    if not _SAFE_COMMAND_LABEL_PATTERN.fullmatch(command_label):
        return NativeVerificationReason.UNSAFE_COMMAND
    return NativeVerificationReason.UNSUPPORTED_COMMAND


def safe_verification_command_label(command_label: NativeVerificationCommand | str) -> str:
    """Return the archive-safe command label for any supported or rejected input."""

    reason = _command_reason(command_label)
    if reason == NativeVerificationReason.UNSAFE_COMMAND:
        return "unsafe"
    if reason == NativeVerificationReason.UNSUPPORTED_COMMAND:
        return "unsupported"
    return NativeVerificationCommand.JUST_CHECK.value


def _request_gate_reason(request: NativeVerificationRequest) -> NativeVerificationReason | None:
    if request.approval_policy.mode != NativeToolApprovalMode.REQUIRED:
        return NativeVerificationReason.APPROVAL_NOT_ALLOWED
    sandbox = request.sandbox_policy
    if sandbox.mode != NativeToolSandboxMode.READ_ONLY_WORKSPACE:
        return NativeVerificationReason.UNSAFE_SANDBOX
    if sandbox.workspace_read_allowed is not True:
        return NativeVerificationReason.UNSAFE_SANDBOX
    if sandbox.filesystem_mutation_allowed is not False:
        return NativeVerificationReason.UNSAFE_SANDBOX
    if sandbox.shell_execution_allowed is not True:
        return NativeVerificationReason.UNSAFE_SANDBOX
    if sandbox.network_access_allowed is not False:
        return NativeVerificationReason.UNSAFE_SANDBOX
    return None
