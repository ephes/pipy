"""Bounded native workspace patch application."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.models import (
    NATIVE_PATCH_APPLY_STORAGE_KEYS,
    NativePatchApplyOperation,
    NativePatchApplyOperationRequest,
    NativePatchApplyRequest,
    NativeToolApprovalMode,
    NativeToolSandboxMode,
    NativeToolStatus,
)
from pipy_harness.native.read_only_tool import (
    _duration_seconds,
    _is_ignored_or_generated,
    _is_relative_to,
    _line_count,
    _validate_safe_label,
    _validate_workspace_relative_path,
)

_HUMAN_REVIEWED_AUTHORITY = "pipy-owned-human-reviewed"
_TEXT_ENCODING = "utf-8"


class NativePatchApplyApprovalDecision(StrEnum):
    """Closed labels for pipy-owned patch apply approval decisions."""

    ALLOWED = "allowed"
    DENIED = "denied"
    SKIPPED = "skipped"
    FAILED = "failed"


class NativePatchApplyReason(StrEnum):
    """Safe reason labels for supervised patch apply outcomes."""

    PATCH_APPLIED = "patch_applied"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    APPROVAL_NOT_ALLOWED = "approval_not_allowed"
    UNSAFE_SANDBOX = "unsafe_sandbox"
    UNSAFE_TARGET = "unsafe_target"
    MISSING_FILE = "missing_file"
    MISSING_PARENT = "missing_parent"
    EXISTING_FILE = "existing_file"
    DIRECTORY_TARGET = "directory_target"
    NOT_REGULAR_FILE = "not_regular_file"
    UNREADABLE_FILE = "unreadable_file"
    IGNORED_OR_GENERATED_FILE = "ignored_or_generated_file"
    EXPECTED_HASH_REQUIRED = "expected_hash_required"
    EXPECTED_HASH_INVALID = "expected_hash_invalid"
    EXPECTED_HASH_MISMATCH = "expected_hash_mismatch"
    SECRET_LOOKING_CONTENT = "secret_looking_content"
    LIMIT_EXCEEDED = "limit_exceeded"
    WRITE_FAILED = "write_failed"
    WRITE_PARTIALLY_APPLIED = "write_partially_applied"


@dataclass(frozen=True, slots=True)
class NativePatchApplyGateDecision:
    """Pipy-owned approval gate data required before workspace mutation."""

    approval_decision: NativePatchApplyApprovalDecision
    decision_authority: str = _HUMAN_REVIEWED_AUTHORITY
    reason_label: str | None = None

    def __post_init__(self) -> None:
        if self.decision_authority != _HUMAN_REVIEWED_AUTHORITY:
            raise ValueError("patch apply gate decision must be pipy-owned and human-reviewed")
        if self.reason_label is not None:
            _validate_safe_label(self.reason_label, field_name="reason_label")

    @property
    def allowed(self) -> bool:
        return self.approval_decision == NativePatchApplyApprovalDecision.ALLOWED


@dataclass(frozen=True, slots=True)
class NativePatchApplyResult:
    """Result for one bounded supervised patch application."""

    status: NativeToolStatus
    reason_label: NativePatchApplyReason
    tool_request_id: str
    turn_index: int
    started_at: datetime
    ended_at: datetime
    file_count: int
    operation_count: int
    operation_labels: tuple[NativePatchApplyOperation, ...]
    approval_policy: NativeToolApprovalMode = NativeToolApprovalMode.REQUIRED
    approval_decision: NativePatchApplyApprovalDecision | None = None
    sandbox_policy: NativeToolSandboxMode = NativeToolSandboxMode.MUTATING_WORKSPACE
    workspace_read_allowed: bool = True
    filesystem_mutation_allowed: bool = True
    shell_execution_allowed: bool = False
    network_access_allowed: bool = False
    workspace_mutated: bool = False
    patch_text_stored: bool = False
    diffs_stored: bool = False
    file_contents_stored: bool = False
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    raw_transcript_imported: bool = False
    scope_label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason_label, NativePatchApplyReason):
            raise ValueError("patch apply result reason_label must be a native label")
        for operation_label in self.operation_labels:
            if not isinstance(operation_label, NativePatchApplyOperation):
                raise ValueError("patch apply result operation_labels must use native labels")
        for field_name in NATIVE_PATCH_APPLY_STORAGE_KEYS:
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false for patch apply results")
        if self.scope_label is not None:
            _validate_safe_label(self.scope_label, field_name="scope_label")

    def archive_metadata(self) -> dict[str, object]:
        """Return the metadata-only shape allowed for archive/event surfaces."""

        return {
            "tool_request_id": self.tool_request_id,
            "turn_index": self.turn_index,
            "status": self.status.value,
            "reason_label": self.reason_label.value,
            "duration_seconds": _duration_seconds(self.started_at, self.ended_at),
            "file_count": self.file_count,
            "operation_count": self.operation_count,
            "operation_labels": [label.value for label in self.operation_labels],
            "approval_policy": self.approval_policy.value,
            "approval_required": self.approval_policy == NativeToolApprovalMode.REQUIRED,
            "approval_resolved": self.approval_decision is not None,
            "approval_decision": self.approval_decision.value if self.approval_decision else None,
            "sandbox_policy": self.sandbox_policy.value,
            "workspace_read_allowed": self.workspace_read_allowed,
            "filesystem_mutation_allowed": self.filesystem_mutation_allowed,
            "shell_execution_allowed": self.shell_execution_allowed,
            "network_access_allowed": self.network_access_allowed,
            "workspace_mutated": self.workspace_mutated,
            "scope_label": self.scope_label,
            "patch_text_stored": False,
            "diffs_stored": False,
            "file_contents_stored": False,
            "prompt_stored": False,
            "model_output_stored": False,
            "provider_responses_stored": False,
            "raw_transcript_imported": False,
        }


@dataclass(frozen=True, slots=True)
class NativePatchApplyTool:
    """Apply one approved, human-reviewed patch request to a workspace."""

    workspace: Path

    MAX_FILE_BYTES: ClassVar[int] = 16 * 1024
    MAX_FILE_LINES: ClassVar[int] = 320

    @property
    def name(self) -> str:
        return "patch_apply"

    def invoke(
        self,
        request: NativePatchApplyRequest,
        gate_decision: NativePatchApplyGateDecision,
    ) -> NativePatchApplyResult:
        started_at = datetime.now(UTC)
        builder = _ResultBuilder(request=request, gate_decision=gate_decision, started_at=started_at)

        reason = _request_gate_reason(request)
        if reason is not None:
            return builder.skipped(reason)
        if not gate_decision.allowed:
            return builder.skipped(NativePatchApplyReason.APPROVAL_NOT_ALLOWED)

        workspace = self.workspace.resolve()
        plan: list[_PlannedOperation] = []
        for operation in request.operations:
            planned_or_reason = _plan_operation(operation, workspace)
            if isinstance(planned_or_reason, NativePatchApplyReason):
                return builder.skipped(planned_or_reason)
            plan.append(planned_or_reason)

        try:
            applied_count = 0
            for planned in plan:
                _apply_planned_operation(planned)
                applied_count += 1
        except OSError:
            if applied_count > 0:
                return builder.failed(
                    NativePatchApplyReason.WRITE_PARTIALLY_APPLIED,
                    workspace_mutated=True,
                )
            return builder.failed(NativePatchApplyReason.WRITE_FAILED)

        return builder.succeeded()


@dataclass(frozen=True, slots=True)
class _PlannedOperation:
    operation: NativePatchApplyOperation
    path: Path
    new_text: str | None = None
    target_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _ResultBuilder:
    request: NativePatchApplyRequest
    gate_decision: NativePatchApplyGateDecision
    started_at: datetime

    def succeeded(self) -> NativePatchApplyResult:
        return self._result(
            status=NativeToolStatus.SUCCEEDED,
            reason=NativePatchApplyReason.PATCH_APPLIED,
            workspace_mutated=True,
        )

    def skipped(self, reason: NativePatchApplyReason) -> NativePatchApplyResult:
        return self._result(status=NativeToolStatus.SKIPPED, reason=reason, workspace_mutated=False)

    def failed(
        self,
        reason: NativePatchApplyReason,
        *,
        workspace_mutated: bool = False,
    ) -> NativePatchApplyResult:
        return self._result(status=NativeToolStatus.FAILED, reason=reason, workspace_mutated=workspace_mutated)

    def _result(
        self,
        *,
        status: NativeToolStatus,
        reason: NativePatchApplyReason,
        workspace_mutated: bool,
    ) -> NativePatchApplyResult:
        return NativePatchApplyResult(
            status=status,
            reason_label=reason,
            tool_request_id=self.request.tool_request_id,
            turn_index=self.request.turn_index,
            started_at=self.started_at,
            ended_at=datetime.now(UTC),
            file_count=_file_count(self.request.operations),
            operation_count=len(self.request.operations),
            operation_labels=tuple(operation.operation for operation in self.request.operations),
            approval_policy=self.request.approval_policy.mode,
            approval_decision=self.gate_decision.approval_decision,
            sandbox_policy=self.request.sandbox_policy.mode,
            workspace_read_allowed=self.request.sandbox_policy.workspace_read_allowed,
            filesystem_mutation_allowed=self.request.sandbox_policy.filesystem_mutation_allowed,
            shell_execution_allowed=self.request.sandbox_policy.shell_execution_allowed,
            network_access_allowed=self.request.sandbox_policy.network_access_allowed,
            workspace_mutated=workspace_mutated,
            scope_label=self.request.scope_label,
        )


def _request_gate_reason(request: NativePatchApplyRequest) -> NativePatchApplyReason | None:
    if request.approval_policy.mode != NativeToolApprovalMode.REQUIRED:
        return NativePatchApplyReason.APPROVAL_NOT_ALLOWED
    sandbox = request.sandbox_policy
    if sandbox.mode != NativeToolSandboxMode.MUTATING_WORKSPACE:
        return NativePatchApplyReason.UNSAFE_SANDBOX
    if sandbox.workspace_read_allowed is not True:
        return NativePatchApplyReason.UNSAFE_SANDBOX
    if sandbox.filesystem_mutation_allowed is not True:
        return NativePatchApplyReason.UNSAFE_SANDBOX
    if sandbox.shell_execution_allowed is not False or sandbox.network_access_allowed is not False:
        return NativePatchApplyReason.UNSAFE_SANDBOX
    return None


def _plan_operation(
    operation: NativePatchApplyOperationRequest,
    workspace: Path,
) -> _PlannedOperation | NativePatchApplyReason:
    try:
        _validate_workspace_relative_path(operation.workspace_relative_path)
    except ValueError:
        return NativePatchApplyReason.UNSAFE_TARGET
    if _is_ignored_or_generated(operation.workspace_relative_path, workspace):
        return NativePatchApplyReason.IGNORED_OR_GENERATED_FILE

    path = (workspace / operation.workspace_relative_path).resolve()
    if not _is_relative_to(path, workspace):
        return NativePatchApplyReason.UNSAFE_TARGET

    if operation.operation == NativePatchApplyOperation.CREATE:
        return _plan_create(operation, workspace, path)
    if operation.operation == NativePatchApplyOperation.MODIFY:
        return _plan_modify(operation, path)
    if operation.operation == NativePatchApplyOperation.DELETE:
        return _plan_delete(operation, path)
    if operation.operation == NativePatchApplyOperation.RENAME:
        return _plan_rename(operation, workspace, path)
    return NativePatchApplyReason.UNSUPPORTED_OPERATION


def _plan_create(
    operation: NativePatchApplyOperationRequest,
    workspace: Path,
    path: Path,
) -> _PlannedOperation | NativePatchApplyReason:
    if path.exists():
        return NativePatchApplyReason.EXISTING_FILE
    if not path.parent.exists() or not path.parent.is_dir():
        return NativePatchApplyReason.MISSING_PARENT
    if not _is_relative_to(path.parent.resolve(), workspace):
        return NativePatchApplyReason.UNSAFE_TARGET
    reason = _new_text_reason(operation.new_text)
    if reason is not None:
        return reason
    return _PlannedOperation(operation=operation.operation, path=path, new_text=operation.new_text)


def _plan_modify(
    operation: NativePatchApplyOperationRequest,
    path: Path,
) -> _PlannedOperation | NativePatchApplyReason:
    reason = _existing_file_reason(path)
    if reason is not None:
        return reason
    hash_reason = _expected_hash_reason(path, operation.expected_sha256)
    if hash_reason is not None:
        return hash_reason
    text_reason = _new_text_reason(operation.new_text)
    if text_reason is not None:
        return text_reason
    return _PlannedOperation(operation=operation.operation, path=path, new_text=operation.new_text)


def _plan_delete(
    operation: NativePatchApplyOperationRequest,
    path: Path,
) -> _PlannedOperation | NativePatchApplyReason:
    reason = _existing_file_reason(path)
    if reason is not None:
        return reason
    hash_reason = _expected_hash_reason(path, operation.expected_sha256)
    if hash_reason is not None:
        return hash_reason
    return _PlannedOperation(operation=operation.operation, path=path)


def _plan_rename(
    operation: NativePatchApplyOperationRequest,
    workspace: Path,
    path: Path,
) -> _PlannedOperation | NativePatchApplyReason:
    source_reason = _existing_file_reason(path)
    if source_reason is not None:
        return source_reason
    hash_reason = _expected_hash_reason(path, operation.expected_sha256)
    if hash_reason is not None:
        return hash_reason
    target = operation.target_workspace_relative_path
    if target is None:
        return NativePatchApplyReason.UNSAFE_TARGET
    try:
        _validate_workspace_relative_path(target)
    except ValueError:
        return NativePatchApplyReason.UNSAFE_TARGET
    if _is_ignored_or_generated(target, workspace):
        return NativePatchApplyReason.IGNORED_OR_GENERATED_FILE
    target_path = (workspace / target).resolve()
    if not _is_relative_to(target_path, workspace):
        return NativePatchApplyReason.UNSAFE_TARGET
    if target_path.exists():
        return NativePatchApplyReason.EXISTING_FILE
    if not target_path.parent.exists() or not target_path.parent.is_dir():
        return NativePatchApplyReason.MISSING_PARENT
    return _PlannedOperation(operation=operation.operation, path=path, target_path=target_path)


def _apply_planned_operation(operation: _PlannedOperation) -> None:
    if operation.operation in {NativePatchApplyOperation.CREATE, NativePatchApplyOperation.MODIFY}:
        assert operation.new_text is not None
        operation.path.write_text(operation.new_text, encoding=_TEXT_ENCODING)
    elif operation.operation == NativePatchApplyOperation.DELETE:
        operation.path.unlink()
    elif operation.operation == NativePatchApplyOperation.RENAME:
        assert operation.target_path is not None
        operation.path.rename(operation.target_path)


def _existing_file_reason(path: Path) -> NativePatchApplyReason | None:
    if not path.exists():
        return NativePatchApplyReason.MISSING_FILE
    if path.is_dir():
        return NativePatchApplyReason.DIRECTORY_TARGET
    if not path.is_file():
        return NativePatchApplyReason.NOT_REGULAR_FILE
    return None


def _expected_hash_reason(path: Path, expected_sha256: str | None) -> NativePatchApplyReason | None:
    if expected_sha256 is None:
        return NativePatchApplyReason.EXPECTED_HASH_REQUIRED
    if not _is_sha256(expected_sha256):
        return NativePatchApplyReason.EXPECTED_HASH_INVALID
    try:
        current = path.read_bytes()
    except OSError:
        return NativePatchApplyReason.UNREADABLE_FILE
    if hashlib.sha256(current).hexdigest() != expected_sha256:
        return NativePatchApplyReason.EXPECTED_HASH_MISMATCH
    return None


def _new_text_reason(value: str | None) -> NativePatchApplyReason | None:
    if value is None:
        return NativePatchApplyReason.UNSUPPORTED_OPERATION
    encoded = value.encode(_TEXT_ENCODING)
    if len(encoded) > NativePatchApplyTool.MAX_FILE_BYTES:
        return NativePatchApplyReason.LIMIT_EXCEEDED
    if _line_count(value) > NativePatchApplyTool.MAX_FILE_LINES:
        return NativePatchApplyReason.LIMIT_EXCEEDED
    if looks_sensitive(value):
        return NativePatchApplyReason.SECRET_LOOKING_CONTENT
    return None


def _file_count(operations: tuple[NativePatchApplyOperationRequest, ...]) -> int:
    paths: set[str] = set()
    for operation in operations:
        paths.add(operation.workspace_relative_path)
        if operation.target_workspace_relative_path is not None:
            paths.add(operation.target_workspace_relative_path)
    return len(paths)


def _is_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)
