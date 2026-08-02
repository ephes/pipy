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
from pipy_harness.native.http import JsonHTTPClient, JsonResponse
from pipy_harness.native.providers.cloudflare import (
    CloudflareHTTPStatusError,
    CloudflareWorkersAIProvider,
)

ACCOUNT_ID = "acct-test-1234"
EXPECTED_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1/chat/completions"
)


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="cloudflare",
        model_id="@cf/meta/llama-3.1-8b-instruct",
        cwd=tmp_path,
    )


def _make_provider(http_client: JsonHTTPClient, model_id: str) -> ProviderPort:
    return CloudflareWorkersAIProvider(
        model_id=model_id,
        account_id=ACCOUNT_ID,
        api_token="cf-token-test",
        http_client=http_client,
    )


def _assert_success_wire(posted: Mapping[str, Any]) -> None:
    assert posted["url"] == EXPECTED_URL
    assert posted["headers"]["Authorization"] == "Bearer cf-token-test"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "@cf/meta/llama-3.1-8b-instruct",
        "messages": [
            {"role": "system", "content": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED"},
            {"role": "user", "content": "SAFE_GOAL_METADATA"},
        ],
    }


def _assert_tool_result_wire(posted: Mapping[str, Any]) -> None:
    assert posted["url"] == EXPECTED_URL
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
    provider_id="cloudflare",
    model_id="@cf/meta/llama-3.1-8b-instruct",
    http_error_url=EXPECTED_URL,
    make_provider=_make_provider,
    make_request=_provider_request,
    make_status_error=CloudflareHTTPStatusError.from_http_error,
    http_error_type="CloudflareHTTPStatusError",
    http_error_message="Cloudflare Workers AI request failed with HTTP status 429.",
    configuration_error_type="CloudflareConfigurationError",
    parse_error_type="CloudflareResponseParseError",
    parse_error_message=(
        "Cloudflare Workers AI response did not include a completion choice."
    ),
    assert_success_wire=_assert_success_wire,
    assert_tool_result_wire=_assert_tool_result_wire,
)


@pytest.mark.parametrize(
    "scenario",
    CHAT_COMPLETIONS_SCENARIOS,
    ids=scenario_ids("cloudflare"),
)
def test_chat_completions_contract(
    scenario: ChatCompletionsScenario,
    tmp_path: Path,
) -> None:
    scenario.run(_CONTRACT, tmp_path)


def test_missing_account_id_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = CloudflareWorkersAIProvider(
        model_id="@cf/meta/llama-3.1-8b-instruct",
        account_id=None,
        api_token="cf-token-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "CloudflareAuthError"
    assert "account id is required" in (result.error_message or "")
    assert client.requests == []


def test_missing_api_token_returns_failed_result(tmp_path):
    client = FakeJsonHTTPClient()
    provider = CloudflareWorkersAIProvider(
        model_id="@cf/meta/llama-3.1-8b-instruct",
        account_id=ACCOUNT_ID,
        api_token=None,
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "CloudflareAuthError"
    assert "API auth is required" in (result.error_message or "")
    assert client.requests == []


def test_url_includes_account_id(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    provider = CloudflareWorkersAIProvider(
        model_id="@cf/meta/llama-3.1-8b-instruct",
        account_id="unique-acct-id-xyz",
        api_token="cf-token-test",
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    posted = client.requests[0]
    assert posted["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/unique-acct-id-xyz/ai/v1/chat/completions"
    )
    assert "unique-acct-id-xyz" in posted["url"]
