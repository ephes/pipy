"""Regression: `/verify just-check` routes through the shared substrate.

These tests prove the verification boundary delegates execution to the shared
``command_sandbox.execute_allowlisted_argv`` (rather than calling subprocess
directly), still runs only the allowlisted ``just check`` argv, and keeps
command output out of the metadata-first archive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pipy_harness.native.verification as verification
from pipy_harness.native.models import (
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
    NativeToolStatus,
    NativeVerificationCommand,
)
from pipy_harness.native.verification import (
    NativeVerificationApprovalDecision,
    NativeVerificationGateDecision,
    NativeVerificationRequest,
    NativeVerificationTool,
)


def _request() -> NativeVerificationRequest:
    return NativeVerificationRequest(
        tool_request_id="native-tool-0001",
        turn_index=0,
        command_label=NativeVerificationCommand.JUST_CHECK,
        approval_policy=NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
        sandbox_policy=NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            filesystem_mutation_allowed=False,
            shell_execution_allowed=True,
            network_access_allowed=False,
        ),
    )


def _gate() -> NativeVerificationGateDecision:
    return NativeVerificationGateDecision(
        approval_decision=NativeVerificationApprovalDecision.ALLOWED
    )


def test_verification_delegates_to_shared_substrate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_exec(
        argv: Any, *, cwd: Path, runner: Any
    ) -> subprocess.CompletedProcess[Any]:
        calls.append({"argv": tuple(argv), "cwd": cwd})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(verification, "execute_allowlisted_argv", fake_exec)

    tool = NativeVerificationTool(
        workspace=tmp_path,
        executable_resolver=lambda name: "/usr/bin/just",
    )
    result = tool.invoke(_request(), _gate())

    assert len(calls) == 1
    # Only the hardcoded allowlisted argv is ever executed.
    assert calls[0]["argv"] == ("just", "check")
    assert calls[0]["cwd"] == tmp_path
    assert result.status is NativeToolStatus.SUCCEEDED


def test_verification_archive_metadata_never_stores_output(tmp_path: Path) -> None:
    tool = NativeVerificationTool(
        workspace=tmp_path,
        executable_resolver=lambda name: "/usr/bin/just",
        runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )
    result = tool.invoke(_request(), _gate())
    metadata = result.archive_metadata()
    for key in (
        "stdout_stored",
        "stderr_stored",
        "command_output_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
    ):
        assert metadata[key] is False
    assert metadata["command_label"] == NativeVerificationCommand.JUST_CHECK.value
