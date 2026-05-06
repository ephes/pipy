from __future__ import annotations

from dataclasses import asdict

import pytest

from pipy_harness.native import (
    FakeNoOpNativeTool,
    NativeToolApprovalPolicy,
    NativeToolIntent,
    NativeToolObservation,
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
        status=NativeToolStatus.SUCCEEDED,
        reason_label="safe_noop_completed",
        duration_seconds=0.003,
    )

    observation_fields = asdict(observation)

    assert observation_fields == {
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "status": NativeToolStatus.SUCCEEDED,
        "reason_label": "safe_noop_completed",
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
        status=NativeToolStatus.SKIPPED,
    )

    observation_fields = asdict(observation)

    storage_fields = {key: value for key, value in observation_fields.items() if key.endswith("_stored")}
    storage_fields["raw_transcript_imported"] = observation_fields["raw_transcript_imported"]
    assert storage_fields
    assert set(storage_fields.values()) == {False}


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
