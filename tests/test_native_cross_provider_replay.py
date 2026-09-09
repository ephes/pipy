"""Cross-provider target-safe replay of durable tool correlations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness import sdk
from pipy_harness.native._provider_helpers import envelope_to_chat_message
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.http import JsonResponse, ProviderHTTPError
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.anthropic_messages_wire import envelope_to_message
from pipy_harness.native.providers.google_generate_content_wire import (
    envelope_to_content,
)
from pipy_harness.native.providers.openai_responses_wire import envelope_to_input_items
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_call_ids import portable_tool_correlation_id
from pipy_harness.native.tools.read import ReadTool

RAW_CODEX_CORRELATION = "call_abc|fc_abc"
PORTABLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class _FakeAnthropicHTTPClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        del cancel_token
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        return JsonResponse(
            status_code=200,
            body={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "continued"}],
                "usage": {},
            },
        )


def _tool_history(
    correlation: str = RAW_CODEX_CORRELATION,
) -> tuple[AgentAssistantMessage, AgentToolResultMessage]:
    return (
        AgentAssistantMessage(
            content=ProductContent("calling read"),
            tool_calls=(
                AgentToolCall(
                    provider_correlation_id=correlation,
                    tool_name="read",
                    arguments_json=ProductContent('{"path":"README.md"}'),
                ),
            ),
        ),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-history",
            tool_name="read",
            content=ProductContent("stored tool output"),
            provider_correlation_id=correlation,
        ),
    )


def test_portable_tool_correlation_id_preserves_safe_ids_and_hashes_unsafe_ids() -> (
    None
):
    safe = "Call_42-safe"
    unsafe = RAW_CODEX_CORRELATION

    assert portable_tool_correlation_id(safe) == safe
    mapped = portable_tool_correlation_id(unsafe)
    assert mapped == "tc_27XnRLF9g0mabGGhYhGHjtmQt15xoIGFbpkYklnGtrs"
    assert PORTABLE_ID_RE.fullmatch(mapped)
    assert len(mapped) <= 64
    assert portable_tool_correlation_id(mapped) == mapped
    assert portable_tool_correlation_id("call_def|fc_def") != mapped


def test_shared_wires_pair_the_same_portable_tool_correlation_id() -> None:
    assistant, result = _tool_history()
    expected = portable_tool_correlation_id(RAW_CODEX_CORRELATION)

    chat_call = envelope_to_chat_message(assistant)
    chat_result = envelope_to_chat_message(result)
    assert chat_call["tool_calls"][0]["id"] == expected
    assert chat_result["tool_call_id"] == expected

    anthropic_call = envelope_to_message(assistant, parse_error_class=ProviderHTTPError)
    anthropic_result = envelope_to_message(result, parse_error_class=ProviderHTTPError)
    anthropic_call_content = anthropic_call["content"]
    anthropic_result_content = anthropic_result["content"]
    assert isinstance(anthropic_call_content, list)
    assert isinstance(anthropic_result_content, list)
    assert isinstance(anthropic_call_content[1], dict)
    assert isinstance(anthropic_result_content[0], dict)
    assert anthropic_call_content[1]["id"] == expected
    assert anthropic_result_content[0]["tool_use_id"] == expected

    responses_call = envelope_to_input_items(
        assistant, parse_error_class=ProviderHTTPError
    )
    responses_result = envelope_to_input_items(
        result, parse_error_class=ProviderHTTPError
    )
    assert responses_call[1]["call_id"] == expected
    assert responses_result[0]["call_id"] == expected

    google_call = envelope_to_content(assistant, parse_error_class=ProviderHTTPError)
    google_result = envelope_to_content(result, parse_error_class=ProviderHTTPError)
    assert google_call["parts"][1]["functionCall"]["id"] == expected
    assert google_result["parts"][0]["functionResponse"]["id"] == expected


@pytest.mark.parametrize(
    ("returned_id", "expected"),
    [
        ("returned-call", "returned-call"),
        (None, "google-tool-0"),
        ("", "google-tool-0"),
        (12, "google-tool-0"),
    ],
)
def test_google_response_retains_returned_id_or_uses_existing_fallback(
    returned_id: object, expected: str
) -> None:
    from pipy_harness.native.providers.google_generate_content_wire import (
        extract_tool_calls,
    )

    calls = extract_tool_calls(
        [{"functionCall": {"id": returned_id, "name": "read", "args": {}}}],
        tool_call_provider_prefix="google",
    )
    assert calls[0].provider_correlation_id == expected


def test_reopened_codex_tool_history_projects_pair_for_anthropic_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(ProductContent("historical request")))
    assistant, result = _tool_history()
    tree.append_message(assistant)
    tree.append_message(result)
    assert tree.path is not None
    raw_before = tree.path.read_text(encoding="utf-8")

    def forbid_historical_execution(
        self: ReadTool, request: object, context: object
    ) -> object:
        del self, request, context
        raise AssertionError("reopening history must not execute historical tools")

    monkeypatch.setattr(ReadTool, "invoke", forbid_historical_execution)
    client = _FakeAnthropicHTTPClient()
    provider = AnthropicProvider(
        model_id="claude-test", api_key="test-key", http_client=client
    )

    with sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=provider
    ) as reopened:
        snapshot = reopened.submit("continue")

    payload = client.requests[0]["body"]["messages"]
    tool_use = payload[1]["content"][1]
    tool_result = payload[2]["content"][0]
    expected = portable_tool_correlation_id(RAW_CODEX_CORRELATION)
    assert tool_use["id"] == tool_result["tool_use_id"] == expected
    historical = snapshot.messages[1:3]
    assert historical[0] == assistant and historical[1] == result
    assert RAW_CODEX_CORRELATION in tree.path.read_text(encoding="utf-8")
    assert tree.path.read_text(encoding="utf-8").startswith(raw_before)
