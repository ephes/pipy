from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

import pytest

from pipy_harness.native import (
    NativeReadOnlyToolLimits,
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequestIdentity,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
)


def current_read_only_request(
    *,
    request_kind: NativeReadOnlyToolRequestKind = NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
    limits: NativeReadOnlyToolLimits | None = None,
    approval_policy: NativeToolApprovalPolicy | None = None,
    sandbox_policy: NativeToolSandboxPolicy | None = None,
    scope_label: str | None = None,
) -> NativeReadOnlyToolRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativeReadOnlyToolRequest(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        request_kind=request_kind,
        limits=limits or NativeReadOnlyToolLimits(),
        approval_policy=approval_policy
        or NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
        sandbox_policy=sandbox_policy
        or NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
        ),
        scope_label=scope_label,
    )


def test_read_only_tool_request_default_is_metadata_only_and_side_effect_free():
    request = current_read_only_request(scope_label="docs-scope")

    request_fields = asdict(request)

    assert request_fields["tool_request_id"] == "native-tool-0001"
    assert request_fields["turn_index"] == 0
    assert request_fields["request_kind"] == "explicit-file-excerpt"
    assert request_fields["tool_name"] == "read_only_repo_inspection"
    assert request_fields["tool_kind"] == "read_only_workspace"
    assert request_fields["approval_policy"]["mode"] == "required"
    assert request.approval_policy.label == "required"
    assert request_fields["sandbox_policy"] == {
        "mode": "read-only-workspace",
        "workspace_read_allowed": True,
        "filesystem_mutation_allowed": False,
        "shell_execution_allowed": False,
        "network_access_allowed": False,
    }
    assert request.sandbox_policy.label == "read-only-workspace"
    assert request_fields["limits"] == {
        "per_excerpt_bytes": 4096,
        "per_excerpt_lines": 80,
        "per_source_file_bytes": 8192,
        "per_source_file_lines": 160,
        "total_context_bytes": 24576,
        "total_context_lines": 480,
        "max_excerpts": 12,
        "max_distinct_source_files": 6,
    }
    storage_values = {
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
    assert {request_fields[name] for name in storage_values} == {False}

    forbidden_fields = {
        "args",
        "arguments",
        "command",
        "credentials",
        "diff",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "path",
        "paths",
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "query",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_fields.isdisjoint(request_fields)


def test_read_only_tool_request_supports_only_safe_kind_labels():
    assert {kind.value for kind in NativeReadOnlyToolRequestKind} == {
        "explicit-file-excerpt",
        "search-excerpt",
    }


def test_read_only_tool_request_requires_current_pipy_owned_identity():
    with pytest.raises(ValueError, match="tool_request_id"):
        NativeReadOnlyToolRequest(
            tool_request_id="provider-owned-id",
            turn_index=NativeToolRequestIdentity.CURRENT_TURN_INDEX,
            request_kind=NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
        )
    with pytest.raises(ValueError, match="turn_index"):
        NativeReadOnlyToolRequest(
            tool_request_id=NativeToolRequestIdentity.current_noop().request_id,
            turn_index=1,
            request_kind=NativeReadOnlyToolRequestKind.SEARCH_EXCERPT,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("per_excerpt_bytes", NativeReadOnlyToolLimits.MAX_PER_EXCERPT_BYTES + 1),
        ("per_excerpt_lines", NativeReadOnlyToolLimits.MAX_PER_EXCERPT_LINES + 1),
        ("per_source_file_bytes", NativeReadOnlyToolLimits.MAX_PER_SOURCE_FILE_BYTES + 1),
        ("per_source_file_lines", NativeReadOnlyToolLimits.MAX_PER_SOURCE_FILE_LINES + 1),
        ("total_context_bytes", NativeReadOnlyToolLimits.MAX_TOTAL_CONTEXT_BYTES + 1),
        ("total_context_lines", NativeReadOnlyToolLimits.MAX_TOTAL_CONTEXT_LINES + 1),
        ("max_excerpts", NativeReadOnlyToolLimits.MAX_EXCERPTS + 1),
        (
            "max_distinct_source_files",
            NativeReadOnlyToolLimits.MAX_DISTINCT_SOURCE_FILES + 1,
        ),
        ("per_excerpt_bytes", -1),
    ],
)
def test_read_only_tool_limits_cannot_exceed_policy_bounds(field_name: str, value: int):
    with pytest.raises(ValueError, match=field_name):
        NativeReadOnlyToolLimits(**{field_name: value})


@pytest.mark.parametrize(
    "bad_sandbox",
    [
        NativeToolSandboxPolicy(mode=NativeToolSandboxMode.NO_WORKSPACE_ACCESS),
        NativeToolSandboxPolicy(mode=NativeToolSandboxMode.MUTATING_WORKSPACE, workspace_read_allowed=True),
        NativeToolSandboxPolicy(mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE),
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            filesystem_mutation_allowed=True,
        ),
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            shell_execution_allowed=True,
        ),
        NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
            network_access_allowed=True,
        ),
    ],
)
def test_read_only_tool_request_rejects_non_read_only_capabilities(
    bad_sandbox: NativeToolSandboxPolicy,
):
    with pytest.raises(ValueError):
        current_read_only_request(sandbox_policy=bad_sandbox)


def test_read_only_tool_request_requires_future_approval():
    with pytest.raises(ValueError, match="requires approval"):
        current_read_only_request(
            approval_policy=NativeToolApprovalPolicy(mode=NativeToolApprovalMode.NOT_REQUIRED)
        )


@pytest.mark.parametrize("scope_label", ["src/pipy_harness/native/models.py", "../models.py", "~/.ssh"])
def test_read_only_tool_request_scope_label_is_not_path_authority(scope_label: str):
    with pytest.raises(ValueError, match="scope_label"):
        current_read_only_request(scope_label=scope_label)


def test_read_only_tool_request_contract_is_threaded_only_through_fixture_gated_session_path():
    session_source = (Path(__file__).parents[1] / "src/pipy_harness/native/session.py").read_text(
        encoding="utf-8"
    )

    assert "NativeReadOnlyToolRequest" in session_source
    assert "PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY" in session_source
    assert "NativeExplicitFileExcerptTool(run_input.cwd).invoke" in session_source
    assert "read_only_repo_inspection" in session_source
    assert "workspace_read_allowed" in session_source
    assert "shell_execution_allowed" in session_source
    assert "network_access_allowed" in session_source
    assert "filesystem_mutation_allowed" in session_source


def test_read_only_tool_request_field_names_are_closed_for_first_inert_shape():
    assert {field.name for field in fields(NativeReadOnlyToolRequest)} == {
        "tool_request_id",
        "turn_index",
        "request_kind",
        "tool_name",
        "tool_kind",
        "approval_policy",
        "sandbox_policy",
        "limits",
        "scope_label",
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
