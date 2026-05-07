from __future__ import annotations

from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY,
    ProviderRequest,
)


def test_fake_native_provider_is_deterministic_without_echoing_prompt(tmp_path):
    provider = FakeNativeProvider()
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_RETURNED",
        user_prompt="USER_PROMPT_SHOULD_NOT_BE_RETURNED",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "fake"
    assert result.model_id == "fake-native-bootstrap"
    assert result.final_text == "pipy native fake provider completed."
    assert "SYSTEM_PROMPT" not in result.final_text
    assert "USER_PROMPT" not in result.final_text
    assert result.usage == {}
    assert result.metadata is None


def test_fake_native_provider_uses_explicit_tool_intent_fixture(tmp_path):
    provider = FakeNativeProvider(
        tool_intent={
            "tool_name": "noop",
            "tool_kind": "internal_noop",
            "turn_index": 0,
            "intent_source": "fake_provider",
        }
    )
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_RETURNED",
        user_prompt="USER_PROMPT_SHOULD_NOT_BE_RETURNED",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.metadata == {
        PROVIDER_TOOL_INTENT_METADATA_KEY: {
            "tool_name": "noop",
            "tool_kind": "internal_noop",
            "turn_index": 0,
            "intent_source": "fake_provider",
        }
    }
    assert "SYSTEM_PROMPT" not in str(result.metadata)
    assert "USER_PROMPT" not in str(result.metadata)


def test_fake_native_provider_uses_explicit_observation_fixture(tmp_path):
    fixture = {
        "fixture_source": "synthetic_safe_noop",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "status": "succeeded",
        "reason_label": "tool_result_succeeded",
    }
    provider = FakeNativeProvider(tool_observation_fixture=fixture)
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_RETURNED",
        user_prompt="USER_PROMPT_SHOULD_NOT_BE_RETURNED",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.metadata == {PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY: fixture}
    assert "SYSTEM_PROMPT" not in str(result.metadata)
    assert "USER_PROMPT" not in str(result.metadata)


def test_fake_native_provider_uses_explicit_read_only_tool_fixture(tmp_path):
    fixture = {
        "fixture_source": "pipy_owned_explicit_file_excerpt",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "request_kind": "explicit-file-excerpt",
        "approval_decision": "allowed",
        "workspace_relative_path": "README.md",
    }
    provider = FakeNativeProvider(read_only_tool_fixture=fixture)
    request = ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_RETURNED",
        user_prompt="USER_PROMPT_SHOULD_NOT_BE_RETURNED",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.metadata == {PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: fixture}
    assert "SYSTEM_PROMPT" not in str(result.metadata)
    assert "USER_PROMPT" not in str(result.metadata)
