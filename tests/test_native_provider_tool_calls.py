"""Slice 3 tests: ProviderPort extension for model-driven tool calls.

These tests pin the new surface added to the existing provider boundary
without changing the runtime behavior of real adapters:

- `ProviderToolCall` exists with the documented shape and rejects malformed
  inputs.
- `ProviderResult.tool_calls` defaults to an empty tuple, keeping older
  producers and consumers source-compatible.
- All real adapters (`openai`, `openai-codex`, `openrouter`) declare
  `supports_tool_calls == False` and do not emit any `tool_calls`.
- `FakeNativeProvider` accepts `supports_tool_calls=True` plus a
  `programmable_tool_calls` script that produces one tuple of provider tool
  calls per `complete()` invocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
    OpenRouterChatCompletionsProvider,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.provider import ProviderPort


# ----------------------------- ProviderToolCall ----------------------------


def test_provider_tool_call_round_trip():
    call = ProviderToolCall(
        provider_correlation_id="call_abc123",
        tool_name="read",
        arguments_json='{"path": "README.md"}',
    )

    assert call.provider_correlation_id == "call_abc123"
    assert call.tool_name == "read"
    assert call.arguments_json == '{"path": "README.md"}'


def test_provider_tool_call_rejects_empty_or_invalid_fields():
    with pytest.raises(ValueError, match="provider_correlation_id"):
        ProviderToolCall(
            provider_correlation_id="",
            tool_name="read",
            arguments_json="{}",
        )
    with pytest.raises(ValueError, match="tool_name"):
        ProviderToolCall(
            provider_correlation_id="call_abc",
            tool_name="",
            arguments_json="{}",
        )
    with pytest.raises(ValueError, match="arguments_json"):
        ProviderToolCall(
            provider_correlation_id="call_abc",
            tool_name="read",
            arguments_json=42,  # type: ignore[arg-type]
        )


def test_provider_tool_call_enforces_max_lengths():
    with pytest.raises(ValueError, match="provider_correlation_id exceeds"):
        ProviderToolCall(
            provider_correlation_id=(
                "x"
                * (
                    ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH + 1
                )
            ),
            tool_name="read",
            arguments_json="{}",
        )
    with pytest.raises(ValueError, match="tool_name exceeds"):
        ProviderToolCall(
            provider_correlation_id="call_abc",
            tool_name="x" * (ProviderToolCall.TOOL_NAME_MAX_LENGTH + 1),
            arguments_json="{}",
        )
    with pytest.raises(ValueError, match="arguments_json exceeds"):
        ProviderToolCall(
            provider_correlation_id="call_abc",
            tool_name="read",
            arguments_json="x" * (ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH + 1),
        )


# --------------------- ProviderResult.tool_calls default --------------------


def test_provider_result_tool_calls_defaults_to_empty_tuple(tmp_path):
    provider = FakeNativeProvider()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="USR",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.tool_calls == ()


def test_provider_result_can_carry_tool_calls():
    call = ProviderToolCall(
        provider_correlation_id="call_abc",
        tool_name="read",
        arguments_json="{}",
    )
    result = ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="fake",
        model_id="fake-native-bootstrap",
        started_at=__import__("datetime").datetime(
            2026, 5, 25, tzinfo=__import__("datetime").UTC
        ),
        ended_at=__import__("datetime").datetime(
            2026, 5, 25, tzinfo=__import__("datetime").UTC
        ),
        tool_calls=(call,),
    )

    assert result.tool_calls == (call,)


# -------------------- Real adapters stay inert --------------------


def test_real_providers_default_to_supports_tool_calls_false():
    openai = OpenAIResponsesProvider(model_id="gpt-test")
    codex = OpenAICodexResponsesProvider(model_id="gpt-5-codex")
    openrouter = OpenRouterChatCompletionsProvider(model_id="vendor/model")

    assert openai.supports_tool_calls is False
    assert codex.supports_tool_calls is False
    assert openrouter.supports_tool_calls is False


def test_real_providers_satisfy_provider_port_protocol():
    openai = OpenAIResponsesProvider(model_id="gpt-test")
    codex = OpenAICodexResponsesProvider(model_id="gpt-5-codex")
    openrouter = OpenRouterChatCompletionsProvider(model_id="vendor/model")
    fake = FakeNativeProvider()

    for provider in (openai, codex, openrouter, fake):
        assert isinstance(provider, ProviderPort)


# ------------------- FakeNativeProvider tool-call script -------------------


def test_fake_native_provider_defaults_no_tool_calls_when_unsupported(tmp_path):
    call = ProviderToolCall(
        provider_correlation_id="call_abc",
        tool_name="read",
        arguments_json="{}",
    )
    provider = FakeNativeProvider(
        supports_tool_calls=False,
        programmable_tool_calls=((call,),),
    )
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="USR",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    result = provider.complete(request)

    assert result.tool_calls == ()


def test_fake_native_provider_emits_programmed_tool_calls_in_order(tmp_path):
    first = (
        ProviderToolCall(
            provider_correlation_id="call_1",
            tool_name="read",
            arguments_json='{"path": "a.py"}',
        ),
    )
    second = (
        ProviderToolCall(
            provider_correlation_id="call_2",
            tool_name="write",
            arguments_json='{"path": "b.py", "content": "x"}',
        ),
    )
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=(first, second),
    )
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="USR",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=Path(tmp_path),
    )

    first_result = provider.complete(request)
    second_result = provider.complete(request)
    third_result = provider.complete(request)

    assert first_result.tool_calls == first
    assert second_result.tool_calls == second
    assert third_result.tool_calls == ()
