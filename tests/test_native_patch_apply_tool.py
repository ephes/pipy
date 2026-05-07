from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.native import (
    NativePatchApplyApprovalDecision,
    NativePatchApplyGateDecision,
    NativePatchApplyOperation,
    NativePatchApplyOperationRequest,
    NativePatchApplyReason,
    NativePatchApplyRequest,
    NativePatchApplyResult,
    NativePatchApplyTool,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequestIdentity,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
    NativeToolStatus,
)
from pipy_harness.native.patch_apply import _apply_planned_operation as original_apply


def current_request(
    *operations: NativePatchApplyOperationRequest,
    sandbox_policy: NativeToolSandboxPolicy | None = None,
    approval_policy: NativeToolApprovalPolicy | None = None,
    scope_label: str | None = None,
) -> NativePatchApplyRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativePatchApplyRequest(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        operations=operations,
        sandbox_policy=sandbox_policy
        or NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.MUTATING_WORKSPACE,
            workspace_read_allowed=True,
            filesystem_mutation_allowed=True,
        ),
        approval_policy=approval_policy
        or NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
        scope_label=scope_label,
    )


def allowed_gate() -> NativePatchApplyGateDecision:
    return NativePatchApplyGateDecision(approval_decision=NativePatchApplyApprovalDecision.ALLOWED)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_patch_apply_modifies_existing_file_with_expected_hash(tmp_path: Path):
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    target.write_text("old = 1\n", encoding="utf-8")

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="src/example.py",
                expected_sha256=sha256_text("old = 1\n"),
                new_text="old = 2\n",
            )
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.SUCCEEDED
    assert result.reason_label == NativePatchApplyReason.PATCH_APPLIED
    assert target.read_text(encoding="utf-8") == "old = 2\n"
    metadata = result.archive_metadata()
    assert metadata["workspace_mutated"] is True
    assert metadata["operation_labels"] == ["modify"]
    assert metadata["file_count"] == 1
    assert "old = 2" not in json.dumps(metadata)


def test_patch_apply_records_scope_label_as_metadata_only(tmp_path: Path):
    target = tmp_path / "example.py"
    target.write_text("old = 1\n", encoding="utf-8")

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="example.py",
                expected_sha256=sha256_text("old = 1\n"),
                new_text="old = 2\n",
            ),
            scope_label="single-file",
        ),
        allowed_gate(),
    )

    metadata = result.archive_metadata()
    assert metadata["scope_label"] == "single-file"
    assert "old = 2" not in json.dumps(metadata)


def test_patch_apply_validates_full_plan_before_mutating(tmp_path: Path):
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir()
    target.write_text("old = 1\n", encoding="utf-8")

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="src/example.py",
                expected_sha256=sha256_text("old = 1\n"),
                new_text="old = 2\n",
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="../outside.py",
                new_text="outside = True\n",
            ),
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativePatchApplyReason.UNSAFE_TARGET
    assert result.workspace_mutated is False
    assert target.read_text(encoding="utf-8") == "old = 1\n"


def test_patch_apply_requires_human_reviewed_gate_and_expected_hash(tmp_path: Path):
    target = tmp_path / "example.py"
    target.write_text("old = 1\n", encoding="utf-8")

    denied = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="example.py",
                expected_sha256=sha256_text("old = 1\n"),
                new_text="old = 2\n",
            )
        ),
        NativePatchApplyGateDecision(approval_decision=NativePatchApplyApprovalDecision.DENIED),
    )
    missing_hash = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="example.py",
                new_text="old = 2\n",
            )
        ),
        allowed_gate(),
    )

    assert denied.status == NativeToolStatus.SKIPPED
    assert denied.reason_label == NativePatchApplyReason.APPROVAL_NOT_ALLOWED
    assert missing_hash.status == NativeToolStatus.SKIPPED
    assert missing_hash.reason_label == NativePatchApplyReason.EXPECTED_HASH_REQUIRED
    assert target.read_text(encoding="utf-8") == "old = 1\n"


def test_patch_apply_reports_invalid_hash_distinct_from_missing_hash(tmp_path: Path):
    target = tmp_path / "example.py"
    target.write_text("old = 1\n", encoding="utf-8")

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.DELETE,
                workspace_relative_path="example.py",
                expected_sha256="not-a-sha",
            )
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativePatchApplyReason.EXPECTED_HASH_INVALID
    assert target.exists()


def test_patch_apply_request_rejects_unsafe_policy():
    with pytest.raises(ValueError, match="approval"):
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                new_text="value = 1\n",
            ),
            approval_policy=NativeToolApprovalPolicy(mode=NativeToolApprovalMode.NOT_REQUIRED),
        )
    with pytest.raises(ValueError, match="mutating-workspace"):
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                new_text="value = 1\n",
            ),
            sandbox_policy=NativeToolSandboxPolicy(mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE),
        )


@pytest.mark.parametrize(
    "operations",
    [
        (
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                new_text="value = 1\n",
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                new_text="value = 2\n",
            ),
        ),
        (
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.RENAME,
                workspace_relative_path="old.py",
                target_workspace_relative_path="new.py",
                expected_sha256="0" * 64,
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="new.py",
                new_text="value = 1\n",
            ),
        ),
    ],
)
def test_patch_apply_request_rejects_overlapping_operation_paths(
    operations: tuple[NativePatchApplyOperationRequest, ...],
):
    with pytest.raises(ValueError, match="overlap"):
        current_request(*operations)


def test_patch_apply_request_rejects_target_path_on_non_rename():
    with pytest.raises(ValueError, match="target paths"):
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                target_workspace_relative_path="ignored.py",
                new_text="value = 1\n",
            )
        )


def test_patch_apply_request_rejects_non_native_operation_label():
    identity = NativeToolRequestIdentity.current_noop()
    with pytest.raises(ValueError, match="operation labels"):
        NativePatchApplyRequest(
            tool_request_id=identity.request_id,
            turn_index=identity.turn_index,
            operations=(
                NativePatchApplyOperationRequest(
                    operation="shell",  # type: ignore[arg-type]
                    workspace_relative_path="example.py",
                    new_text="value = 1\n",
                ),
            ),
        )


def test_patch_apply_creates_deletes_and_renames_files(tmp_path: Path):
    create_parent = tmp_path / "src"
    create_parent.mkdir()
    delete_target = tmp_path / "remove.py"
    rename_target = tmp_path / "old.py"
    delete_target.write_text("remove_me = True\n", encoding="utf-8")
    rename_target.write_text("keep_me = True\n", encoding="utf-8")

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="src/new.py",
                new_text="created = True\n",
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.DELETE,
                workspace_relative_path="remove.py",
                expected_sha256=sha256_text("remove_me = True\n"),
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.RENAME,
                workspace_relative_path="old.py",
                target_workspace_relative_path="renamed.py",
                expected_sha256=sha256_text("keep_me = True\n"),
            ),
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.SUCCEEDED
    assert (tmp_path / "src" / "new.py").read_text(encoding="utf-8") == "created = True\n"
    assert not delete_target.exists()
    assert not rename_target.exists()
    assert (tmp_path / "renamed.py").read_text(encoding="utf-8") == "keep_me = True\n"
    assert result.archive_metadata()["operation_labels"] == ["create", "delete", "rename"]


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        (
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="missing-parent/new.py",
                new_text="created = True\n",
            ),
            NativePatchApplyReason.MISSING_PARENT,
        ),
        (
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path=".pipy/new.py",
                new_text="created = True\n",
            ),
            NativePatchApplyReason.IGNORED_OR_GENERATED_FILE,
        ),
        (
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="oversized.py",
                new_text="x" * (NativePatchApplyTool.MAX_FILE_BYTES + 1),
            ),
            NativePatchApplyReason.LIMIT_EXCEEDED,
        ),
    ],
)
def test_patch_apply_rejects_unsafe_create_targets_and_content(
    tmp_path: Path,
    operation: NativePatchApplyOperationRequest,
    reason: NativePatchApplyReason,
):
    result = NativePatchApplyTool(tmp_path).invoke(current_request(operation), allowed_gate())

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == reason
    assert result.workspace_mutated is False


def test_patch_apply_rejects_secret_looking_new_content(tmp_path: Path):
    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.CREATE,
                workspace_relative_path="example.py",
                new_text="OPENAI_API_KEY='sk-test-secret-token'\n",
            )
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativePatchApplyReason.SECRET_LOOKING_CONTENT
    assert not (tmp_path / "example.py").exists()


def test_patch_apply_result_rejects_true_storage_booleans():
    with pytest.raises(ValueError, match="patch_text_stored"):
        NativePatchApplyResult(
            status=NativeToolStatus.SUCCEEDED,
            reason_label=NativePatchApplyReason.PATCH_APPLIED,
            tool_request_id="native-tool-0001",
            turn_index=0,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            file_count=1,
            operation_count=1,
            operation_labels=(NativePatchApplyOperation.MODIFY,),
            patch_text_stored=True,
        )


def test_patch_apply_records_partial_mutation_when_write_fails_mid_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    calls = 0

    def apply_then_fail(operation):
        nonlocal calls
        calls += 1
        if calls == 1:
            original_apply(operation)
            return None
        raise OSError("simulated write failure")

    monkeypatch.setattr("pipy_harness.native.patch_apply._apply_planned_operation", apply_then_fail)

    result = NativePatchApplyTool(tmp_path).invoke(
        current_request(
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="first.py",
                expected_sha256=sha256_text("first = 1\n"),
                new_text="first = 2\n",
            ),
            NativePatchApplyOperationRequest(
                operation=NativePatchApplyOperation.MODIFY,
                workspace_relative_path="second.py",
                expected_sha256=sha256_text("second = 1\n"),
                new_text="second = 2\n",
            ),
        ),
        allowed_gate(),
    )

    assert result.status == NativeToolStatus.FAILED
    assert result.reason_label == NativePatchApplyReason.WRITE_PARTIALLY_APPLIED
    assert result.workspace_mutated is True
    assert result.archive_metadata()["workspace_mutated"] is True
    assert first.read_text(encoding="utf-8") == "first = 2\n"
    assert second.read_text(encoding="utf-8") == "second = 1\n"
