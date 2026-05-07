from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.native import (
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequestIdentity,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
    NativeToolStatus,
    NativeVerificationApprovalDecision,
    NativeVerificationCommand,
    NativeVerificationGateDecision,
    NativeVerificationReason,
    NativeVerificationRequest,
    NativeVerificationResult,
    NativeVerificationTool,
)


def current_request(
    command_label: NativeVerificationCommand | str = NativeVerificationCommand.JUST_CHECK,
    *,
    sandbox_policy: NativeToolSandboxPolicy | None = None,
    approval_policy: NativeToolApprovalPolicy | None = None,
    scope_label: str | None = None,
) -> NativeVerificationRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativeVerificationRequest(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        command_label=command_label,
        sandbox_policy=sandbox_policy
        or NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            shell_execution_allowed=True,
        ),
        approval_policy=approval_policy
        or NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
        scope_label=scope_label,
    )


def allowed_gate() -> NativeVerificationGateDecision:
    return NativeVerificationGateDecision(
        approval_decision=NativeVerificationApprovalDecision.ALLOWED
    )


def test_verification_runs_exactly_just_check_from_workspace_without_storing_output(tmp_path: Path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def runner(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="SHOULD_NOT_PERSIST",
            stderr="SHOULD_NOT_PERSIST",
        )

    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=runner,
    ).invoke(current_request(scope_label="post-patch-check"), allowed_gate())

    assert result.status == NativeToolStatus.SUCCEEDED
    assert result.reason_label == NativeVerificationReason.VERIFICATION_SUCCEEDED
    assert result.exit_code == 0
    assert calls == [
        (
            ("just", "check"),
            {
                "cwd": tmp_path.resolve(),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "check": False,
            },
        )
    ]
    metadata = result.archive_metadata()
    assert metadata["command_label"] == "just-check"
    assert metadata["status"] == "succeeded"
    assert metadata["scope_label"] == "post-patch-check"
    assert metadata["stdout_stored"] is False
    assert metadata["stderr_stored"] is False
    assert metadata["command_output_stored"] is False
    assert "SHOULD_NOT_PERSIST" not in json.dumps(metadata)


@pytest.mark.parametrize(
    ("command_label", "expected_reason", "expected_label"),
    [
        ("pytest", NativeVerificationReason.UNSUPPORTED_COMMAND, "unsupported"),
        ("just check", NativeVerificationReason.UNSAFE_COMMAND, "unsafe"),
        ("pytest\n-q", NativeVerificationReason.UNSAFE_COMMAND, "unsafe"),
        ("pytest*", NativeVerificationReason.UNSAFE_COMMAND, "unsafe"),
    ],
)
def test_verification_unsupported_or_unsafe_command_fails_closed_before_execution(
    tmp_path: Path,
    command_label: str,
    expected_reason: NativeVerificationReason,
    expected_label: str,
):
    calls = 0

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args=argv, returncode=0)

    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=runner,
    ).invoke(current_request(command_label), allowed_gate())

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == expected_reason
    assert result.archive_metadata()["command_label"] == expected_label
    assert calls == 0


@pytest.mark.parametrize(
    "approval_decision",
    [
        NativeVerificationApprovalDecision.DENIED,
        NativeVerificationApprovalDecision.SKIPPED,
    ],
)
def test_verification_requires_allowed_gate_before_execution(
    tmp_path: Path,
    approval_decision: NativeVerificationApprovalDecision,
):
    calls = 0

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args=argv, returncode=0)

    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=runner,
    ).invoke(
        current_request(),
        NativeVerificationGateDecision(approval_decision=approval_decision),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeVerificationReason.APPROVAL_NOT_ALLOWED
    assert result.approval_decision == approval_decision
    assert calls == 0


@pytest.mark.parametrize(
    "sandbox_policy",
    [
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            shell_execution_allowed=False,
        ),
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.MUTATING_WORKSPACE,
            workspace_read_allowed=True,
            filesystem_mutation_allowed=True,
            shell_execution_allowed=True,
        ),
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            shell_execution_allowed=True,
            network_access_allowed=True,
        ),
    ],
)
def test_verification_rejects_unsafe_policy_before_execution(
    tmp_path: Path,
    sandbox_policy: NativeToolSandboxPolicy,
):
    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=lambda argv, **kwargs: subprocess.CompletedProcess(args=argv, returncode=0),
    ).invoke(current_request(sandbox_policy=sandbox_policy), allowed_gate())

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeVerificationReason.UNSAFE_SANDBOX


def test_verification_missing_executable_skips_without_running(tmp_path: Path):
    calls = 0

    def runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args=argv, returncode=0)

    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: None,
        runner=runner,
    ).invoke(current_request(), allowed_gate())

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeVerificationReason.MISSING_EXECUTABLE
    assert result.error_label == "missing_executable"
    assert calls == 0


def test_verification_command_failure_records_exit_code_only(tmp_path: Path):
    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            args=argv,
            returncode=7,
            stdout="SHOULD_NOT_PERSIST",
            stderr="SHOULD_NOT_PERSIST",
        ),
    ).invoke(current_request(), allowed_gate())

    assert result.status == NativeToolStatus.FAILED
    assert result.reason_label == NativeVerificationReason.COMMAND_FAILED
    assert result.exit_code == 7
    metadata = result.archive_metadata()
    assert metadata["exit_code"] == 7
    assert metadata["error_label"] == "command_failed"
    assert "SHOULD_NOT_PERSIST" not in json.dumps(metadata)


def test_verification_execution_failure_records_safe_error_label(tmp_path: Path):
    def runner(argv, **kwargs):
        raise OSError("SHOULD_NOT_PERSIST")

    result = NativeVerificationTool(
        tmp_path,
        executable_resolver=lambda executable: f"/safe/bin/{executable}",
        runner=runner,
    ).invoke(current_request(), allowed_gate())

    assert result.status == NativeToolStatus.FAILED
    assert result.reason_label == NativeVerificationReason.EXECUTION_FAILED
    assert result.error_label == "execution_failed"
    assert "SHOULD_NOT_PERSIST" not in json.dumps(result.archive_metadata())


def test_verification_request_and_result_reject_true_storage_booleans():
    identity = NativeToolRequestIdentity.current_noop()
    with pytest.raises(ValueError, match="stdout_stored"):
        NativeVerificationRequest(
            tool_request_id=identity.request_id,
            turn_index=identity.turn_index,
            command_label=NativeVerificationCommand.JUST_CHECK,
            stdout_stored=True,
        )

    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="stdout_stored"):
        NativeVerificationResult(
            status=NativeToolStatus.SUCCEEDED,
            reason_label=NativeVerificationReason.VERIFICATION_SUCCEEDED,
            tool_request_id="native-tool-0001",
            turn_index=0,
            command_label="just-check",
            started_at=now,
            ended_at=now,
            exit_code=0,
            stdout_stored=True,
        )
