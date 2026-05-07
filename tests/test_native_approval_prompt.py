from __future__ import annotations

import json
from dataclasses import fields
from io import StringIO

from pipy_harness.native import (
    NativeApprovalPromptReason,
    NativeApprovalPromptStatus,
    NativeApprovalSandboxDecision,
    NativeApprovalSandboxPrompt,
    NativeInteractiveApprovalPromptResolver,
    NativeReadOnlyApprovalDecision,
    NativeReadOnlyToolLimits,
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequestIdentity,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
    resolve_read_only_workspace_approval,
)


def current_request(
    *,
    request_kind: NativeReadOnlyToolRequestKind = NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
    scope_label: str | None = "single-explicit-file",
) -> NativeReadOnlyToolRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativeReadOnlyToolRequest(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        request_kind=request_kind,
        limits=NativeReadOnlyToolLimits(),
        scope_label=scope_label,
    )


def unsafe_request(
    *,
    request_kind: NativeReadOnlyToolRequestKind = NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
    tool_name: str = "read_only_repo_inspection",
    tool_kind: str = "read_only_workspace",
    approval_policy: NativeToolApprovalPolicy | None = None,
    sandbox_policy: NativeToolSandboxPolicy | None = None,
    scope_label: str | None = "single-explicit-file",
) -> NativeReadOnlyToolRequest:
    request = object.__new__(NativeReadOnlyToolRequest)
    identity = NativeToolRequestIdentity.current_noop()
    object.__setattr__(request, "tool_request_id", identity.request_id)
    object.__setattr__(request, "turn_index", identity.turn_index)
    object.__setattr__(request, "request_kind", request_kind)
    object.__setattr__(request, "tool_name", tool_name)
    object.__setattr__(request, "tool_kind", tool_kind)
    object.__setattr__(
        request,
        "approval_policy",
        approval_policy or NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
    )
    object.__setattr__(
        request,
        "sandbox_policy",
        sandbox_policy
        or NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
        ),
    )
    object.__setattr__(request, "limits", NativeReadOnlyToolLimits())
    object.__setattr__(request, "scope_label", scope_label)
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
        object.__setattr__(request, field_name, False)
    return request


def test_interactive_prompt_displays_safe_approval_and_sandbox_posture():
    output_stream = StringIO()

    resolution = resolve_read_only_workspace_approval(
        current_request(),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=output_stream,
        ),
    )

    assert resolution.allowed is True
    assert resolution.decision.status == NativeApprovalPromptStatus.ALLOWED
    assert resolution.decision.reason_label == NativeApprovalPromptReason.APPROVED_BY_USER
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.ALLOWED
    prompt_text = output_stream.getvalue()
    assert "pipy approval required" in prompt_text
    assert "operation: read_only_workspace_inspection" in prompt_text
    assert "tool: read_only_repo_inspection" in prompt_text
    assert "tool_kind: read_only_workspace" in prompt_text
    assert "approval_policy: required" in prompt_text
    assert "sandbox_policy: read-only-workspace" in prompt_text
    assert "workspace_read_allowed=true" in prompt_text
    assert "filesystem_mutation_allowed=false" in prompt_text
    assert "shell_execution_allowed=false" in prompt_text
    assert "network_access_allowed=false" in prompt_text
    assert "scope: single-explicit-file" in prompt_text
    assert "Approve? [y/N]:" in prompt_text
    assert "src/example.py" not in prompt_text


def test_denied_prompt_maps_to_denied_gate_and_fails_closed():
    resolution = resolve_read_only_workspace_approval(
        current_request(),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("no\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.decision.status == NativeApprovalPromptStatus.DENIED
    assert resolution.decision.reason_label == NativeApprovalPromptReason.DENIED_BY_USER
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.DENIED
    assert resolution.gate_decision.allowed is False


def test_missing_or_unavailable_prompt_ui_fails_closed_before_execution():
    no_resolver = resolve_read_only_workspace_approval(current_request(), None)
    no_input = resolve_read_only_workspace_approval(
        current_request(),
        NativeInteractiveApprovalPromptResolver(
            input_stream=None,
            output_stream=StringIO(),
        ),
    )
    eof = resolve_read_only_workspace_approval(
        current_request(),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO(""),
            output_stream=StringIO(),
        ),
    )

    for resolution in (no_resolver, no_input, eof):
        assert resolution.allowed is False
        assert resolution.decision.status == NativeApprovalPromptStatus.SKIPPED
        assert resolution.decision.reason_label == NativeApprovalPromptReason.APPROVAL_UI_UNAVAILABLE
        assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.SKIPPED


def test_unsupported_approval_policy_fails_closed_without_prompt():
    resolution = resolve_read_only_workspace_approval(
        unsafe_request(
            approval_policy=NativeToolApprovalPolicy(mode=NativeToolApprovalMode.NOT_REQUIRED)
        ),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.prompt is None
    assert resolution.decision.status == NativeApprovalPromptStatus.FAILED
    assert resolution.decision.reason_label == NativeApprovalPromptReason.UNSUPPORTED_APPROVAL_POLICY
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED


def test_unsupported_sandbox_policy_fails_closed_without_prompt():
    resolution = resolve_read_only_workspace_approval(
        unsafe_request(
            sandbox_policy=NativeToolSandboxPolicy(
                mode=NativeToolSandboxMode.NO_WORKSPACE_ACCESS,
                workspace_read_allowed=True,
            )
        ),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.prompt is None
    assert resolution.decision.reason_label == NativeApprovalPromptReason.UNSUPPORTED_SANDBOX_MODE
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED


def test_sandbox_mismatch_fails_closed_without_prompt():
    resolution = resolve_read_only_workspace_approval(
        unsafe_request(
            sandbox_policy=NativeToolSandboxPolicy(
                mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
                workspace_read_allowed=False,
            )
        ),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.prompt is None
    assert resolution.decision.reason_label == NativeApprovalPromptReason.SANDBOX_MISMATCH
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED


def test_capability_escalation_fails_closed_without_prompt():
    for sandbox in (
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
    ):
        resolution = resolve_read_only_workspace_approval(
            unsafe_request(sandbox_policy=sandbox),
            NativeInteractiveApprovalPromptResolver(
                input_stream=StringIO("yes\n"),
                output_stream=StringIO(),
            ),
        )

        assert resolution.allowed is False
        assert resolution.prompt is None
        assert resolution.decision.reason_label == NativeApprovalPromptReason.CAPABILITY_ESCALATION
        assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED


def test_unsafe_request_data_fails_closed_without_prompt():
    resolution = resolve_read_only_workspace_approval(
        unsafe_request(tool_name="provider_selected_tool"),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.prompt is None
    assert resolution.decision.reason_label == NativeApprovalPromptReason.UNSAFE_REQUEST_DATA
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED


def test_unsupported_read_only_request_kind_fails_closed_without_prompt():
    resolution = resolve_read_only_workspace_approval(
        current_request(request_kind=NativeReadOnlyToolRequestKind.SEARCH_EXCERPT),
        NativeInteractiveApprovalPromptResolver(
            input_stream=StringIO("yes\n"),
            output_stream=StringIO(),
        ),
    )

    assert resolution.allowed is False
    assert resolution.prompt is None
    assert resolution.decision.status == NativeApprovalPromptStatus.SKIPPED
    assert resolution.decision.reason_label == NativeApprovalPromptReason.UNSUPPORTED_REQUEST_KIND
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.SKIPPED


def test_resolver_exception_fails_closed_with_failed_gate():
    class ExplodingResolver:
        def resolve(self, prompt: NativeApprovalSandboxPrompt) -> NativeApprovalSandboxDecision:
            raise RuntimeError("SHOULD_NOT_PERSIST")

    resolution = resolve_read_only_workspace_approval(current_request(), ExplodingResolver())

    assert resolution.allowed is False
    assert resolution.decision.status == NativeApprovalPromptStatus.FAILED
    assert resolution.decision.reason_label == NativeApprovalPromptReason.RESOLUTION_FAILED
    assert resolution.gate_decision.approval_decision == NativeReadOnlyApprovalDecision.FAILED
    assert "SHOULD_NOT_PERSIST" not in json.dumps(resolution.safe_metadata())


def test_prompt_and_decision_metadata_are_closed_and_metadata_only():
    prompt = NativeApprovalSandboxPrompt.for_read_only_request(current_request())
    decision = NativeApprovalSandboxDecision(
        status=NativeApprovalPromptStatus.ALLOWED,
        reason_label=NativeApprovalPromptReason.APPROVED_BY_USER,
    )

    assert {field.name for field in fields(NativeApprovalSandboxPrompt)} == {
        "operation_label",
        "tool_name",
        "tool_kind",
        "approval_policy",
        "approval_required",
        "sandbox_policy",
        "workspace_read_allowed",
        "filesystem_mutation_allowed",
        "shell_execution_allowed",
        "network_access_allowed",
        "scope_label",
        "reason_label",
    }
    assert set(prompt.safe_metadata()) == {field.name for field in fields(NativeApprovalSandboxPrompt)}
    assert decision.safe_metadata() == {
        "status": "allowed",
        "reason_label": "approved_by_user",
        "decision_authority": "pipy-owned",
    }
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
    assert forbidden_fields.isdisjoint(prompt.safe_metadata())
    assert forbidden_fields.isdisjoint(decision.safe_metadata())
