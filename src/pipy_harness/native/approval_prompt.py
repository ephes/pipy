"""Visible approval and sandbox prompt helpers for native read-only gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TextIO

from pipy_harness.native.models import (
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeToolApprovalMode,
    NativeToolSandboxMode,
)
from pipy_harness.native.read_only_tool import (
    NativeReadOnlyApprovalDecision,
    NativeReadOnlyGateDecision,
)

_PIPY_AUTHORITY = "pipy-owned"
_READ_ONLY_OPERATION_LABEL = "read_only_workspace_inspection"
_READ_ONLY_TOOL_NAME = "read_only_repo_inspection"
_READ_ONLY_TOOL_KIND = "read_only_workspace"


class NativeApprovalPromptStatus(StrEnum):
    """Closed labels for visible approval resolution."""

    # Reserved for future live prompt lifecycles. This slice resolves prompts
    # synchronously, so pending maps fail-closed if it reaches a gate.
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    SKIPPED = "skipped"
    FAILED = "failed"


class NativeApprovalPromptReason(StrEnum):
    """Safe reason labels for visible approval prompt outcomes."""

    APPROVED_BY_USER = "approved_by_user"
    DENIED_BY_USER = "denied_by_user"
    APPROVAL_UI_UNAVAILABLE = "approval_ui_unavailable"
    UNSUPPORTED_REQUEST_KIND = "unsupported_request_kind"
    UNSUPPORTED_APPROVAL_POLICY = "unsupported_approval_policy"
    UNSUPPORTED_SANDBOX_MODE = "unsupported_sandbox_mode"
    SANDBOX_MISMATCH = "sandbox_mismatch"
    CAPABILITY_ESCALATION = "capability_escalation"
    UNSAFE_REQUEST_DATA = "unsafe_request_data"
    RESOLUTION_FAILED = "resolution_failed"


@dataclass(frozen=True, slots=True)
class NativeApprovalSandboxPrompt:
    """Safe-label prompt data shown before native tool execution."""

    operation_label: str
    tool_name: str
    tool_kind: str
    approval_policy: str
    approval_required: bool
    sandbox_policy: str
    workspace_read_allowed: bool
    filesystem_mutation_allowed: bool
    shell_execution_allowed: bool
    network_access_allowed: bool
    scope_label: str | None = None
    reason_label: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "operation_label",
            "tool_name",
            "tool_kind",
            "approval_policy",
            "sandbox_policy",
        ):
            _validate_safe_label(getattr(self, field_name), field_name=field_name)
        if self.scope_label is not None:
            _validate_scope_label(self.scope_label)
        if self.reason_label is not None:
            _validate_safe_label(self.reason_label, field_name="reason_label")
        for field_name in (
            "approval_required",
            "workspace_read_allowed",
            "filesystem_mutation_allowed",
            "shell_execution_allowed",
            "network_access_allowed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean")

    @classmethod
    def for_read_only_request(cls, request: NativeReadOnlyToolRequest) -> "NativeApprovalSandboxPrompt":
        """Build the safe visible prompt posture for a read-only request."""

        return cls(
            operation_label=_READ_ONLY_OPERATION_LABEL,
            tool_name=request.tool_name,
            tool_kind=request.tool_kind,
            approval_policy=_policy_label(request.approval_policy.mode),
            approval_required=request.approval_policy.mode == NativeToolApprovalMode.REQUIRED,
            sandbox_policy=_policy_label(request.sandbox_policy.mode),
            workspace_read_allowed=request.sandbox_policy.workspace_read_allowed,
            filesystem_mutation_allowed=request.sandbox_policy.filesystem_mutation_allowed,
            shell_execution_allowed=request.sandbox_policy.shell_execution_allowed,
            network_access_allowed=request.sandbox_policy.network_access_allowed,
            scope_label=request.scope_label,
        )

    def safe_metadata(self) -> dict[str, object]:
        """Return the metadata-only shape for tests and future archive events."""

        return {
            "operation_label": self.operation_label,
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind,
            "approval_policy": self.approval_policy,
            "approval_required": self.approval_required,
            "sandbox_policy": self.sandbox_policy,
            "workspace_read_allowed": self.workspace_read_allowed,
            "filesystem_mutation_allowed": self.filesystem_mutation_allowed,
            "shell_execution_allowed": self.shell_execution_allowed,
            "network_access_allowed": self.network_access_allowed,
            "scope_label": self.scope_label,
            "reason_label": self.reason_label,
        }


@dataclass(frozen=True, slots=True)
class NativeApprovalSandboxDecision:
    """Pipy-owned visible approval decision before execution."""

    status: NativeApprovalPromptStatus
    reason_label: NativeApprovalPromptReason
    decision_authority: str = _PIPY_AUTHORITY

    def __post_init__(self) -> None:
        if self.decision_authority != _PIPY_AUTHORITY:
            raise ValueError("approval decisions must be pipy-owned")

    @property
    def allowed(self) -> bool:
        return self.status == NativeApprovalPromptStatus.ALLOWED

    def to_read_only_gate_decision(self) -> NativeReadOnlyGateDecision:
        """Map the visible decision onto the existing read-only gate shape."""

        return NativeReadOnlyGateDecision(
            approval_decision=_read_only_decision_for_status(self.status),
            decision_authority=self.decision_authority,
            reason_label=self.reason_label.value,
        )

    def safe_metadata(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason_label": self.reason_label.value,
            "decision_authority": self.decision_authority,
        }


@dataclass(frozen=True, slots=True)
class NativeReadOnlyApprovalResolution:
    """Read-only approval resolution plus the gate consumed by the tool."""

    decision: NativeApprovalSandboxDecision
    gate_decision: NativeReadOnlyGateDecision
    prompt: NativeApprovalSandboxPrompt | None = None

    @property
    def allowed(self) -> bool:
        return self.decision.allowed and self.gate_decision.allowed

    def safe_metadata(self) -> dict[str, object]:
        return {
            **(self.prompt.safe_metadata() if self.prompt is not None else {}),
            "approval_status": self.decision.status.value,
            "approval_decision": self.gate_decision.approval_decision.value,
            "decision_authority": self.decision.decision_authority,
            "reason_label": self.decision.reason_label.value,
        }


class NativeApprovalPromptResolver(Protocol):
    """Injected resolver for visible pipy-owned approval prompts."""

    def resolve(self, prompt: NativeApprovalSandboxPrompt) -> NativeApprovalSandboxDecision:
        """Return a pipy-owned approval decision for a visible prompt."""


@dataclass(slots=True)
class NativeInteractiveApprovalPromptResolver:
    """Minimal stream-based approval resolver suitable for tests and CLI wiring."""

    input_stream: TextIO | None
    output_stream: TextIO | None

    def resolve(self, prompt: NativeApprovalSandboxPrompt) -> NativeApprovalSandboxDecision:
        if self.input_stream is None or self.output_stream is None:
            return NativeApprovalSandboxDecision(
                status=NativeApprovalPromptStatus.SKIPPED,
                reason_label=NativeApprovalPromptReason.APPROVAL_UI_UNAVAILABLE,
            )
        try:
            _write_visible_prompt(prompt, self.output_stream)
            answer = self.input_stream.readline()
        except (OSError, ValueError):
            return NativeApprovalSandboxDecision(
                status=NativeApprovalPromptStatus.FAILED,
                reason_label=NativeApprovalPromptReason.RESOLUTION_FAILED,
            )
        if answer == "":
            return NativeApprovalSandboxDecision(
                status=NativeApprovalPromptStatus.SKIPPED,
                reason_label=NativeApprovalPromptReason.APPROVAL_UI_UNAVAILABLE,
            )
        normalized = answer.strip().lower()
        if normalized in {"y", "yes"}:
            return NativeApprovalSandboxDecision(
                status=NativeApprovalPromptStatus.ALLOWED,
                reason_label=NativeApprovalPromptReason.APPROVED_BY_USER,
            )
        return NativeApprovalSandboxDecision(
            status=NativeApprovalPromptStatus.DENIED,
            reason_label=NativeApprovalPromptReason.DENIED_BY_USER,
        )


def resolve_read_only_workspace_approval(
    request: NativeReadOnlyToolRequest,
    resolver: NativeApprovalPromptResolver | None,
) -> NativeReadOnlyApprovalResolution:
    """Resolve a visible approval prompt for one read-only workspace request."""

    prompt, closed = _read_only_prompt_or_fail_closed_decision(request)
    if closed is not None:
        return _read_only_resolution(decision=closed, prompt=None)
    if prompt is None:
        decision = NativeApprovalSandboxDecision(
            status=NativeApprovalPromptStatus.FAILED,
            reason_label=NativeApprovalPromptReason.UNSAFE_REQUEST_DATA,
        )
        return _read_only_resolution(decision=decision, prompt=None)
    if resolver is None:
        decision = NativeApprovalSandboxDecision(
            status=NativeApprovalPromptStatus.SKIPPED,
            reason_label=NativeApprovalPromptReason.APPROVAL_UI_UNAVAILABLE,
        )
        return _read_only_resolution(decision=decision, prompt=prompt)

    try:
        decision = resolver.resolve(prompt)
    except Exception:
        decision = NativeApprovalSandboxDecision(
            status=NativeApprovalPromptStatus.FAILED,
            reason_label=NativeApprovalPromptReason.RESOLUTION_FAILED,
        )
    return _read_only_resolution(decision=decision, prompt=prompt)


def _read_only_prompt_or_fail_closed_decision(
    request: NativeReadOnlyToolRequest,
) -> tuple[NativeApprovalSandboxPrompt | None, NativeApprovalSandboxDecision | None]:
    try:
        if request.request_kind != NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT:
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.SKIPPED,
                    NativeApprovalPromptReason.UNSUPPORTED_REQUEST_KIND,
                ),
            )
        if request.tool_name != _READ_ONLY_TOOL_NAME or request.tool_kind != _READ_ONLY_TOOL_KIND:
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.FAILED,
                    NativeApprovalPromptReason.UNSAFE_REQUEST_DATA,
                ),
            )
        if request.approval_policy.mode != NativeToolApprovalMode.REQUIRED:
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.FAILED,
                    NativeApprovalPromptReason.UNSUPPORTED_APPROVAL_POLICY,
                ),
            )
        sandbox = request.sandbox_policy
        if sandbox.mode != NativeToolSandboxMode.READ_ONLY_WORKSPACE:
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.FAILED,
                    NativeApprovalPromptReason.UNSUPPORTED_SANDBOX_MODE,
                ),
            )
        if sandbox.workspace_read_allowed is not True:
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.FAILED,
                    NativeApprovalPromptReason.SANDBOX_MISMATCH,
                ),
            )
        if (
            sandbox.filesystem_mutation_allowed is not False
            or sandbox.shell_execution_allowed is not False
            or sandbox.network_access_allowed is not False
        ):
            return (
                None,
                _closed(
                    NativeApprovalPromptStatus.FAILED,
                    NativeApprovalPromptReason.CAPABILITY_ESCALATION,
                ),
            )
        return NativeApprovalSandboxPrompt.for_read_only_request(request), None
    except (AttributeError, TypeError, ValueError):
        return (
            None,
            _closed(
                NativeApprovalPromptStatus.FAILED,
                NativeApprovalPromptReason.UNSAFE_REQUEST_DATA,
            ),
        )


def _read_only_resolution(
    *,
    decision: NativeApprovalSandboxDecision,
    prompt: NativeApprovalSandboxPrompt | None,
) -> NativeReadOnlyApprovalResolution:
    return NativeReadOnlyApprovalResolution(
        decision=decision,
        gate_decision=decision.to_read_only_gate_decision(),
        prompt=prompt,
    )


def _closed(
    status: NativeApprovalPromptStatus,
    reason: NativeApprovalPromptReason,
) -> NativeApprovalSandboxDecision:
    return NativeApprovalSandboxDecision(status=status, reason_label=reason)


def _read_only_decision_for_status(
    status: NativeApprovalPromptStatus,
) -> NativeReadOnlyApprovalDecision:
    if status == NativeApprovalPromptStatus.ALLOWED:
        return NativeReadOnlyApprovalDecision.ALLOWED
    if status == NativeApprovalPromptStatus.DENIED:
        return NativeReadOnlyApprovalDecision.DENIED
    if status == NativeApprovalPromptStatus.SKIPPED:
        return NativeReadOnlyApprovalDecision.SKIPPED
    return NativeReadOnlyApprovalDecision.FAILED


def _write_visible_prompt(prompt: NativeApprovalSandboxPrompt, stream: TextIO) -> None:
    print("pipy approval required", file=stream)
    print(f"operation: {prompt.operation_label}", file=stream)
    print(f"tool: {prompt.tool_name}", file=stream)
    print(f"tool_kind: {prompt.tool_kind}", file=stream)
    print(f"approval_policy: {prompt.approval_policy}", file=stream)
    print(f"sandbox_policy: {prompt.sandbox_policy}", file=stream)
    print(
        "capabilities: "
        f"workspace_read_allowed={_bool_label(prompt.workspace_read_allowed)}, "
        f"filesystem_mutation_allowed={_bool_label(prompt.filesystem_mutation_allowed)}, "
        f"shell_execution_allowed={_bool_label(prompt.shell_execution_allowed)}, "
        f"network_access_allowed={_bool_label(prompt.network_access_allowed)}",
        file=stream,
    )
    if prompt.scope_label is not None:
        print(f"scope: {prompt.scope_label}", file=stream)
    print("Approve? [y/N]: ", end="", file=stream, flush=True)


def _bool_label(value: bool) -> str:
    return "true" if value else "false"


def _policy_label(value: object) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _validate_safe_label(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value or len(value) > 80:
        raise ValueError(f"{field_name} must be a short non-empty label")
    if any(separator in value for separator in ("/", "\\", "~", " ")):
        raise ValueError(f"{field_name} must not be a filesystem path")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError(f"{field_name} must not be a filesystem path")


def _validate_scope_label(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("scope_label must be a string")
    if not value or len(value) > 80:
        raise ValueError("scope_label must be a short non-empty label")
    if any(separator in value for separator in ("/", "\\", "~")):
        raise ValueError("scope_label must not be a filesystem path")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError("scope_label must not be a filesystem path")
