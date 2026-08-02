from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from provider_chat_completions_contract import (
    CHAT_COMPLETIONS_SCENARIOS,
    ChatCompletionsContract,
    ChatCompletionsScenario,
    FakeJsonHTTPClient,
    scenario_ids,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderPort, ProviderRequest
from pipy_harness.native.http import JsonHTTPClient
from pipy_harness.native.providers.mistral import (
    MistralHTTPStatusError,
    MistralProvider,
)


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="mistral",
        model_id="mistral-large-latest",
        cwd=tmp_path,
    )


def _make_provider(http_client: JsonHTTPClient, model_id: str) -> ProviderPort:
    return MistralProvider(
        model_id=model_id,
        api_key="sk-mistral-test",
        http_client=http_client,
    )


def _assert_success_wire(posted: Mapping[str, Any]) -> None:
    assert posted["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert posted["headers"]["Authorization"] == "Bearer sk-mistral-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "mistral-large-latest",
        "messages": [
            {"role": "system", "content": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"},
            {"role": "user", "content": "SAFE_GOAL_METADATA"},
        ],
    }


def _assert_tool_result_wire(posted: Mapping[str, Any]) -> None:
    assert posted["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "please read README"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": "file contents",
        },
    ]


_CONTRACT = ChatCompletionsContract(
    provider_id="mistral",
    model_id="mistral-large-latest",
    http_error_url="https://api.mistral.ai/v1/chat/completions",
    make_provider=_make_provider,
    make_request=_provider_request,
    make_status_error=MistralHTTPStatusError.from_http_error,
    http_error_type="MistralHTTPStatusError",
    http_error_message="Mistral API request failed with HTTP status 429.",
    configuration_error_type="MistralConfigurationError",
    parse_error_type="MistralResponseParseError",
    parse_error_message="Mistral response did not include a completion choice.",
    assert_success_wire=_assert_success_wire,
    assert_tool_result_wire=_assert_tool_result_wire,
)


@pytest.mark.parametrize(
    "scenario",
    CHAT_COMPLETIONS_SCENARIOS,
    ids=scenario_ids("mistral"),
)
def test_chat_completions_contract(
    scenario: ChatCompletionsScenario,
    tmp_path: Path,
) -> None:
    scenario.run(_CONTRACT, tmp_path)


def test_missing_api_key_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = MistralProvider(
        model_id="mistral-large-latest",
        api_key=None,
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "MistralAuthError"
    assert "API key is required" in (result.error_message or "")
    assert client.requests == []
