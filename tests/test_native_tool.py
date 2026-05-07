from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

import pytest

from pipy_harness.native import (
    NATIVE_PATCH_PROPOSAL_PAYLOAD_KEYS,
    NATIVE_PATCH_PROPOSAL_RECORDED_EVENT,
    NATIVE_PATCH_PROPOSAL_STORAGE_KEYS,
    FakeNoOpNativeTool,
    NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS,
    NATIVE_TOOL_OBSERVATION_RECORDED_EVENT,
    NATIVE_TOOL_OBSERVATION_STORAGE_KEYS,
    NativePatchProposal,
    NativePatchProposalOperation,
    NativePatchProposalReason,
    NativePatchProposalStatus,
    NativeToolApprovalPolicy,
    NativeToolIntent,
    NativeToolObservation,
    NativeToolObservationReason,
    NativeToolObservationStatus,
    NativeToolRequest,
    NativeToolRequestIdentity,
    NativeToolResult,
    NativeToolSandboxPolicy,
    NativeToolStatus,
)


def test_native_tool_request_identity_is_pipy_owned_and_bounded():
    identity = NativeToolRequestIdentity.current_noop()

    assert identity.turn_index == 0
    assert identity.request_position == 0
    assert identity.request_id == "native-tool-0001"
    with pytest.raises(ValueError, match="turn_index"):
        NativeToolRequestIdentity(turn_index=1, request_position=0)
    with pytest.raises(ValueError, match="request_position"):
        NativeToolRequestIdentity(turn_index=0, request_position=1)


def test_native_tool_value_objects_do_not_model_payload_or_output_storage():
    request = NativeToolRequest(
        request_id="tool-1",
        tool_name="noop",
        tool_kind="internal_noop",
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
        metadata={"safe": True},
    )

    request_fields = asdict(request)

    assert request.approval_policy.label == "not-required"
    assert request.sandbox_policy.label == "no-workspace-access"
    assert request_fields["approval_policy"]["mode"] == "not-required"
    assert request_fields["sandbox_policy"]["mode"] == "no-workspace-access"
    assert request_fields["sandbox_policy"]["workspace_read_allowed"] is False
    assert request_fields["sandbox_policy"]["filesystem_mutation_allowed"] is False
    assert request_fields["sandbox_policy"]["shell_execution_allowed"] is False
    assert request_fields["sandbox_policy"]["network_access_allowed"] is False
    for forbidden in ("arguments", "payload", "stdout", "stderr", "diff", "file_content"):
        assert forbidden not in request_fields


def test_native_tool_intent_value_object_is_metadata_only():
    intent = NativeToolIntent(
        request_id="native-tool-0001",
        tool_name="noop",
        tool_kind="internal_noop",
        turn_index=0,
        intent_source="fake_provider",
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
        metadata={"safe_count": 1, "internal_noop": True},
    )

    intent_fields = asdict(intent)

    assert intent_fields["turn_index"] == 0
    assert intent_fields["intent_source"] == "fake_provider"
    for forbidden in ("arguments", "payload", "stdout", "stderr", "diff", "file_content", "command", "path"):
        assert forbidden not in intent_fields


def test_native_tool_observation_stub_is_metadata_only_and_inert():
    observation = NativeToolObservation(
        tool_request_id="native-tool-0001",
        turn_index=0,
        tool_name="noop",
        tool_kind="internal_noop",
        status=NativeToolObservationStatus.SUCCEEDED,
        reason_label=NativeToolObservationReason.TOOL_RESULT_SUCCEEDED,
        duration_seconds=0.003,
    )

    observation_fields = asdict(observation)

    assert observation_fields == {
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "status": NativeToolObservationStatus.SUCCEEDED,
        "reason_label": NativeToolObservationReason.TOOL_RESULT_SUCCEEDED,
        "duration_seconds": 0.003,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
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
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_fields.isdisjoint(observation_fields)


def test_native_tool_observation_storage_booleans_default_false():
    observation = NativeToolObservation(
        tool_request_id="native-tool-0001",
        turn_index=0,
        tool_name="noop",
        tool_kind="internal_noop",
        status=NativeToolObservationStatus.SKIPPED,
    )

    observation_fields = asdict(observation)

    storage_fields = {key: observation_fields[key] for key in NATIVE_TOOL_OBSERVATION_STORAGE_KEYS}
    assert set(storage_fields.values()) == {False}


def test_native_tool_observation_event_contract_is_closed_and_metadata_only():
    observation_field_names = {field.name for field in fields(NativeToolObservation)}

    assert NATIVE_TOOL_OBSERVATION_RECORDED_EVENT == "native.tool.observation.recorded"
    assert observation_field_names == NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS
    assert NATIVE_TOOL_OBSERVATION_STORAGE_KEYS == {
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
    assert {status.value for status in NativeToolObservationStatus} == {
        "succeeded",
        "failed",
        "skipped",
    }
    assert {reason.value for reason in NativeToolObservationReason} == {
        "tool_result_succeeded",
        "tool_result_failed",
        "tool_result_skipped",
        "unsupported_observation",
        "unsafe_observation",
    }
    forbidden_payload_keys = {
        "args",
        "arguments",
        "command",
        "credentials",
        "diff",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_payload_keys.isdisjoint(NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS)


def test_native_patch_proposal_value_object_is_metadata_only_and_bounded():
    proposal = NativePatchProposal(
        tool_request_id="native-tool-0001",
        turn_index=0,
        status=NativePatchProposalStatus.PROPOSED,
        reason_label=NativePatchProposalReason.STRUCTURED_PROPOSAL_ACCEPTED,
        file_count=2,
        operation_count=3,
        operation_labels=(NativePatchProposalOperation.MODIFY, NativePatchProposalOperation.CREATE),
    )

    proposal_fields = asdict(proposal)

    assert proposal_fields == {
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "status": NativePatchProposalStatus.PROPOSED,
        "reason_label": NativePatchProposalReason.STRUCTURED_PROPOSAL_ACCEPTED,
        "file_count": 2,
        "operation_count": 3,
        "operation_labels": (NativePatchProposalOperation.MODIFY, NativePatchProposalOperation.CREATE),
        "patch_text_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
        "workspace_mutated": False,
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
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_fields.isdisjoint(proposal_fields)


def test_native_patch_proposal_event_contract_is_closed_and_metadata_only():
    proposal_field_names = {field.name for field in fields(NativePatchProposal)}

    assert NATIVE_PATCH_PROPOSAL_RECORDED_EVENT == "native.patch.proposal.recorded"
    assert proposal_field_names == NATIVE_PATCH_PROPOSAL_PAYLOAD_KEYS
    assert NATIVE_PATCH_PROPOSAL_STORAGE_KEYS == {
        "patch_text_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
        "workspace_mutated",
    }
    assert {status.value for status in NativePatchProposalStatus} == {"proposed", "skipped"}
    assert {reason.value for reason in NativePatchProposalReason} == {
        "structured_proposal_accepted",
        "unsupported_proposal",
        "unsafe_proposal",
    }
    assert {operation.value for operation in NativePatchProposalOperation} == {
        "create",
        "modify",
        "delete",
        "rename",
    }
    forbidden_payload_keys = {
        "args",
        "arguments",
        "command",
        "credentials",
        "diff",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_payload_keys.isdisjoint(NATIVE_PATCH_PROPOSAL_PAYLOAD_KEYS)


def test_native_tool_observation_contract_is_threaded_only_as_sanitized_session_metadata():
    """The runtime may use sanitized observations, but not raw result surfaces."""

    session_source = (Path(__file__).parents[1] / "src/pipy_harness/native/session.py").read_text(
        encoding="utf-8"
    )

    assert "NativeToolObservation" in session_source
    assert "NATIVE_TOOL_OBSERVATION_RECORDED_EVENT" in session_source
    for forbidden in ("raw_tool_observation", "provider_response_body", "file_contents_text", "stdout_text", "stderr_text"):
        assert forbidden not in session_source


def test_fake_noop_native_tool_is_deterministic_and_side_effect_free():
    tool = FakeNoOpNativeTool()
    request = NativeToolRequest(
        request_id="tool-1",
        tool_name="noop",
        tool_kind="internal_noop",
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
    )

    result = tool.invoke(request)

    assert isinstance(result, NativeToolResult)
    assert result.request_id == "tool-1"
    assert result.tool_name == "noop"
    assert result.status == NativeToolStatus.SUCCEEDED
    assert result.metadata == {
        "workspace_mutated": False,
        "workspace_inspected": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "tool_payloads_stored": False,
    }
    assert result.error_type is None
    assert result.error_message is None
