from __future__ import annotations

from dataclasses import asdict, fields

import pytest

from pipy_harness.native import (
    NATIVE_TURN_METADATA_KEYS,
    NATIVE_TURN_PAYLOAD_KEYS,
    NATIVE_TURN_STORAGE_KEYS,
    NativeConversationIdentity,
    NativeConversationState,
    NativeConversationTurn,
    NativeTurnIdentity,
    NativeTurnMetadata,
    NativeTurnRole,
    NativeTurnStatus,
)


def test_native_conversation_identity_is_pipy_owned_and_safe():
    identity = NativeConversationIdentity.bootstrap()
    boundary_identity = NativeConversationIdentity("c" * NativeConversationIdentity.MAX_LENGTH)
    boundary_turn_identity = NativeTurnIdentity(
        conversation_id=boundary_identity,
        turn_index=0,
    )
    boundary_metadata = NativeTurnMetadata.from_identity(
        boundary_turn_identity,
        role=NativeTurnRole.PROVIDER,
    )

    assert identity.value == "native-conversation-0001"
    assert len(boundary_turn_identity.turn_id) == 128
    assert boundary_metadata.turn_id == boundary_turn_identity.turn_id
    with pytest.raises(ValueError, match="filesystem path"):
        NativeConversationIdentity("../provider-derived")
    with pytest.raises(ValueError, match="sensitive data"):
        NativeConversationIdentity("token=SECRET123")
    with pytest.raises(ValueError, match="short non-empty label"):
        NativeConversationIdentity("c" * (NativeConversationIdentity.MAX_LENGTH + 1))


def test_native_turn_identity_is_bounded_and_correlates_with_conversation():
    conversation_id = NativeConversationIdentity.bootstrap()
    identity = NativeTurnIdentity(conversation_id=conversation_id, turn_index=1)

    assert identity.turn_index == 1
    assert identity.turn_id == "native-conversation-0001-turn-0001"
    with pytest.raises(ValueError, match="turn_index"):
        NativeTurnIdentity(conversation_id=conversation_id, turn_index=-1)
    with pytest.raises(ValueError, match="turn_index"):
        NativeTurnIdentity(conversation_id=conversation_id, turn_index=8)


def test_native_turn_metadata_is_metadata_only_and_allowlisted():
    identity = NativeTurnIdentity(
        conversation_id=NativeConversationIdentity.bootstrap(),
        turn_index=0,
    )
    metadata = NativeTurnMetadata.from_identity(
        identity,
        role=NativeTurnRole.PROVIDER,
        status=NativeTurnStatus.RUNNING,
        provider_turn_label="initial",
        provider_name="openrouter",
        model_id="provider-model",
    )

    metadata_fields = asdict(metadata)
    payload = metadata.archive_payload()

    assert set(metadata_fields) == NATIVE_TURN_METADATA_KEYS
    assert payload == {
        "conversation_id": "native-conversation-0001",
        "turn_id": "native-conversation-0001-turn-0000",
        "turn_index": 0,
        "role": "provider",
        "status": "running",
        "provider_turn_label": "initial",
        "provider_name": "openrouter",
        "model_id": "provider-model",
        "tool_name": None,
        "tool_kind": None,
        "duration_seconds": None,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "raw_transcript_imported": False,
    }
    assert set(payload) == NATIVE_TURN_PAYLOAD_KEYS
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
    assert forbidden_fields.isdisjoint(metadata_fields)
    assert forbidden_fields.isdisjoint(payload)


def test_native_turn_metadata_storage_booleans_must_remain_false():
    identity = NativeTurnIdentity(
        conversation_id=NativeConversationIdentity.bootstrap(),
        turn_index=0,
    )

    for field_name in NATIVE_TURN_STORAGE_KEYS:
        with pytest.raises(ValueError, match=field_name):
            NativeTurnMetadata(
                conversation_id=identity.conversation_id.value,
                turn_id=identity.turn_id,
                turn_index=identity.turn_index,
                role=NativeTurnRole.PROVIDER,
                status=NativeTurnStatus.PENDING,
                **{field_name: True},
            )


def test_native_turn_contract_has_closed_role_and_status_labels():
    assert {field.name for field in fields(NativeTurnMetadata)} == NATIVE_TURN_METADATA_KEYS
    assert NATIVE_TURN_PAYLOAD_KEYS == NATIVE_TURN_METADATA_KEYS
    assert {role.value for role in NativeTurnRole} == {
        "system",
        "user",
        "provider",
        "tool",
    }
    assert {status.value for status in NativeTurnStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "skipped",
    }


def test_native_conversation_state_appends_bounded_provider_turns():
    state = NativeConversationState(max_turns=2)

    state = state.append_provider_turn(
        provider_turn_label="initial",
        provider_name="fake",
        model_id="fake-native-bootstrap",
    )
    state = state.append_provider_turn(
        provider_turn_label="post_tool_observation",
        status=NativeTurnStatus.PENDING,
    )

    assert state.turn_count == 2
    assert state.provider_turn_count == 2
    assert [payload["turn_index"] for payload in state.metadata_payloads()] == [0, 1]
    with pytest.raises(ValueError, match="turn bound"):
        state.append_provider_turn(provider_turn_label="extra")


def test_native_conversation_state_rejects_non_contiguous_or_foreign_turns():
    state = NativeConversationState()
    foreign_identity = NativeTurnIdentity(
        conversation_id=NativeConversationIdentity("other-conversation"),
        turn_index=0,
    )
    foreign_turn = NativeConversationTurn(
        identity=foreign_identity,
        metadata=NativeTurnMetadata.from_identity(
            foreign_identity,
            role=NativeTurnRole.PROVIDER,
        ),
    )

    with pytest.raises(ValueError, match="match conversation state"):
        state.append_turn(foreign_turn)

    skipped_identity = NativeTurnIdentity(
        conversation_id=state.conversation_id,
        turn_index=1,
    )
    skipped_turn = NativeConversationTurn(
        identity=skipped_identity,
        metadata=NativeTurnMetadata.from_identity(
            skipped_identity,
            role=NativeTurnRole.PROVIDER,
        ),
    )
    with pytest.raises(ValueError, match="next pipy-owned turn identity"):
        state.append_turn(skipped_turn)
