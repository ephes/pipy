"""Pi prompt-cache breakpoints for Anthropic Messages and Bedrock (PC2).

Pins the exact request shapes Pi ``4df157433`` sends:
``ai/src/api/anthropic-messages.ts`` (``getCacheControl``, system/tool/last-user
breakpoints, session-affinity headers) and ``ai/src/api/bedrock-converse-stream.ts``
(``supportsPromptCaching`` gate, cache points after the system text and on the
last user message, no tool cache point), plus the 1h cache-write usage split
and Pi ``calculateCost`` pricing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentUsageAccumulator,
)
from pipy_harness.native.auth_store import AuthStore, ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec
from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS
from pipy_harness.native.coding.compaction import build_summary_request
from pipy_harness.native.coding.state import CodingProviderBinding
from pipy_harness.native.http import JsonResponse, extract_anthropic_usage
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_anthropic_prompt_cache,
    resolve_construction,
)
from pipy_harness.native.providers.anthropic_messages import AnthropicProvider
from pipy_harness.native.providers.bedrock import (
    AmazonBedrockProvider,
    bedrock_supports_prompt_caching,
)
from pipy_harness.native.tools import ToolDefinition

EPHEMERAL = {"type": "ephemeral"}
EPHEMERAL_1H = {"type": "ephemeral", "ttl": "1h"}


class _Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: object = None,
    ) -> JsonResponse:
        self.requests.append({"url": url, "headers": dict(headers), "body": body})
        return JsonResponse(
            status_code=200,
            body={
                "type": "message",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )


@pytest.fixture(autouse=True)
def _no_cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPY_CACHE_RETENTION", raising=False)
    monkeypatch.delenv("AWS_BEDROCK_FORCE_CACHE", raising=False)


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


def _tool_turn() -> tuple[AgentMessage, ...]:
    return (
        AgentUserMessage(content=ProductContent("inspect")),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(AgentToolCall("call_1", "read", ProductContent("{}")),),
        ),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-1",
            tool_name="read",
            content=ProductContent("file body"),
            provider_correlation_id="call_1",
        ),
    )


def _request(
    tmp_path: Path,
    *,
    messages: tuple[AgentMessage, ...] | None = None,
    tools: tuple[ToolDefinition, ...] = (_tool("read"), _tool("write")),
    system_prompt: str = "SYSTEM",
    cache_retention: Any = None,
    session_id: str | None = None,
    attachments: tuple[ProviderImageAttachment, ...] = (),
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt=system_prompt,
        user_prompt="inspect",
        provider_name="anthropic",
        model_id="claude-sonnet-4-5",
        cwd=tmp_path,
        messages=_tool_turn() if messages is None else messages,
        available_tools=tools,
        cache_retention=cache_retention,
        session_id=session_id,
        attachments=attachments,
    )


def _anthropic(**fields: Any) -> tuple[AnthropicProvider, _Client]:
    client = _Client()
    fields.setdefault("model_id", "claude-sonnet-4-5")
    provider = AnthropicProvider(api_key="sk-test", http_client=client, **fields)
    return provider, client


def _markers(body: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Every ``cache_control`` in the body, by location."""

    found: list[tuple[str, Any]] = []
    system = body.get("system")
    if isinstance(system, list):
        for index, block in enumerate(system):
            if "cache_control" in block:
                found.append((f"system[{index}]", block["cache_control"]))
    for index, tool in enumerate(body.get("tools", [])):
        if "cache_control" in tool:
            found.append((f"tools[{index}]", tool["cache_control"]))
    for m_index, message in enumerate(body["messages"]):
        content = message["content"]
        if not isinstance(content, list):
            continue
        for b_index, block in enumerate(content):
            if "cache_control" in block:
                found.append(
                    (f"messages[{m_index}][{b_index}]", block["cache_control"])
                )
    return found


# ---- anthropic-messages ----------------------------------------------------


def test_anthropic_short_retention_marks_system_last_tool_and_last_user_block(
    tmp_path: Path,
) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path))
    body = client.requests[0]["body"]
    assert body["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": EPHEMERAL}
    ]
    assert _markers(body) == [
        ("system[0]", EPHEMERAL),
        ("tools[1]", EPHEMERAL),
        ("messages[2][0]", EPHEMERAL),
    ]
    assert body["messages"][2]["content"][0]["type"] == "tool_result"


def test_anthropic_long_retention_adds_one_hour_ttl(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path, cache_retention="long"))
    assert [marker for _, marker in _markers(client.requests[0]["body"])] == [
        EPHEMERAL_1H
    ] * 3


def test_anthropic_long_retention_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CACHE_RETENTION", "long")
    provider, client = _anthropic()
    provider.complete(_request(tmp_path))
    provider.complete(_request(tmp_path, cache_retention="short"))
    assert _markers(client.requests[0]["body"])[0] == ("system[0]", EPHEMERAL_1H)
    # An explicit request retention wins over the environment.
    assert _markers(client.requests[1]["body"])[0] == ("system[0]", EPHEMERAL)


def test_anthropic_long_retention_without_compat_support_has_no_ttl(
    tmp_path: Path,
) -> None:
    provider, client = _anthropic(supports_long_cache_retention=False)
    provider.complete(_request(tmp_path, cache_retention="long"))
    assert [marker for _, marker in _markers(client.requests[0]["body"])] == [
        EPHEMERAL
    ] * 3


def test_anthropic_retention_none_sends_no_markers_but_keeps_system_block(
    tmp_path: Path,
) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path, cache_retention="none"))
    body = client.requests[0]["body"]
    assert _markers(body) == []
    assert body["system"] == [{"type": "text", "text": "SYSTEM"}]


def test_anthropic_summary_request_is_uncached(tmp_path: Path) -> None:
    provider, client = _anthropic()
    request = build_summary_request(
        binding=CodingProviderBinding(
            provider=provider,
            provider_name="anthropic",
            model_id="claude-sonnet-4-5",
        ),
        instruction="Summarize.",
        cwd=tmp_path,
        messages=_tool_turn(),
        user_prompt="Summarize.",
        header_callback=None,
    )
    assert request.cache_retention == "none"
    provider.complete(request)
    assert _markers(client.requests[0]["body"]) == []


def test_anthropic_empty_system_prompt_is_omitted(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path, system_prompt=""))
    body = client.requests[0]["body"]
    assert "system" not in body
    assert [where for where, _ in _markers(body)] == ["tools[1]", "messages[2][0]"]


def test_anthropic_tool_breakpoint_respects_compat(tmp_path: Path) -> None:
    provider, client = _anthropic(supports_cache_control_on_tools=False)
    provider.complete(_request(tmp_path))
    assert [where for where, _ in _markers(client.requests[0]["body"])] == [
        "system[0]",
        "messages[2][0]",
    ]


def test_anthropic_without_tools_has_no_tool_breakpoint(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(
        _request(
            tmp_path,
            messages=(AgentUserMessage(content=ProductContent("hi")),),
            tools=(),
        )
    )
    body = client.requests[0]["body"]
    assert "tools" not in body
    assert [where for where, _ in _markers(body)] == ["system[0]", "messages[0][0]"]


def test_anthropic_trailing_assistant_message_gets_no_message_breakpoint(
    tmp_path: Path,
) -> None:
    provider, client = _anthropic()
    provider.complete(
        _request(
            tmp_path,
            messages=(
                AgentUserMessage(content=ProductContent("hi")),
                AgentAssistantMessage(content=ProductContent("prefill")),
            ),
        )
    )
    assert [where for where, _ in _markers(client.requests[0]["body"])] == [
        "system[0]",
        "tools[1]",
    ]


def test_anthropic_image_last_block_takes_the_breakpoint(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(
        _request(
            tmp_path,
            messages=(AgentUserMessage(content=ProductContent("look")),),
            attachments=(
                ProviderImageAttachment(
                    media_type="image/png",
                    data_base64="aGk=",
                    byte_count=2,
                    sha256="0" * 64,
                    source_label="shot.png",
                ),
            ),
        )
    )
    content = client.requests[0]["body"]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image"]
    assert "cache_control" not in content[0]
    assert content[1]["cache_control"] == EPHEMERAL


def test_anthropic_deferred_tools_mark_the_last_immediate_tool(
    tmp_path: Path,
) -> None:
    messages = (
        AgentUserMessage(content=ProductContent("load")),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(AgentToolCall("call_base", "base", ProductContent("{}")),),
        ),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-load",
            tool_name="base",
            content=ProductContent("loaded"),
            provider_correlation_id="call_base",
            added_tool_names=("late",),
        ),
    )
    provider, client = _anthropic(
        model_id="claude-opus-4-7", supports_tool_references=True
    )
    provider.complete(
        _request(
            tmp_path,
            messages=messages,
            tools=(_tool("base"), _tool("other"), _tool("late")),
        )
    )
    body = client.requests[0]["body"]
    assert [
        (tool["name"], tool.get("defer_loading"), tool.get("cache_control"))
        for tool in body["tools"]
    ] == [("base", None, None), ("other", None, EPHEMERAL), ("late", True, None)]
    # The displaced result text is the last block and carries the breakpoint.
    assert body["messages"][-1]["content"][-1] == {
        "type": "text",
        "text": "loaded",
        "cache_control": EPHEMERAL,
    }


def test_anthropic_never_exceeds_four_breakpoints(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path, cache_retention="long"))
    assert len(_markers(client.requests[0]["body"])) <= 4


# ---- anthropic session affinity -------------------------------------------


def test_anthropic_first_party_sends_no_affinity_header(tmp_path: Path) -> None:
    provider, client = _anthropic()
    provider.complete(_request(tmp_path, session_id="sess-1"))
    headers = client.requests[0]["headers"]
    assert "x-session-affinity" not in headers
    assert "x-session-id" not in headers


def test_anthropic_openrouter_endpoint_sends_x_session_id(tmp_path: Path) -> None:
    provider, client = _anthropic(
        endpoint="https://openrouter.ai/api/v1/messages", provider_name="custom"
    )
    provider.complete(_request(tmp_path, session_id="sess-1"))
    provider.complete(_request(tmp_path, session_id="sess-1", cache_retention="none"))
    provider.complete(_request(tmp_path))
    assert client.requests[0]["headers"]["x-session-id"] == "sess-1"
    assert "x-session-id" not in client.requests[1]["headers"]
    assert "x-session-id" not in client.requests[2]["headers"]


def test_anthropic_explicit_affinity_uses_x_session_affinity(tmp_path: Path) -> None:
    provider, client = _anthropic(send_session_affinity_headers=True)
    provider.complete(_request(tmp_path, session_id="sess-1"))
    assert client.requests[0]["headers"]["x-session-affinity"] == "sess-1"


def test_anthropic_model_headers_override_affinity(tmp_path: Path) -> None:
    provider, client = _anthropic(
        send_session_affinity_headers=True,
        extra_headers={"x-session-affinity": "from-model"},
    )
    provider.complete(_request(tmp_path, session_id="sess-1"))
    assert client.requests[0]["headers"]["x-session-affinity"] == "from-model"


# ---- construction ------------------------------------------------------------


def _anthropic_row() -> NativeModelSpec:
    return next(
        row
        for row in BUILTIN_MODEL_ROWS
        if row.provider_name == "anthropic" and row.api == "anthropic-messages"
    )


def test_resolve_anthropic_prompt_cache_defaults_and_explicit_compat() -> None:
    row = _anthropic_row()
    default = resolve_anthropic_prompt_cache(row, row.base_url)
    assert default.supports_long_cache_retention is True
    assert default.supports_cache_control_on_tools is True
    assert default.send_session_affinity_headers is False
    assert default.session_affinity_format is None

    via_url = resolve_anthropic_prompt_cache(row, "https://openrouter.ai/api")
    assert via_url.send_session_affinity_headers is True
    assert via_url.session_affinity_format == "openrouter"

    explicit = resolve_anthropic_prompt_cache(
        replace(
            row,
            compat={
                "supportsLongCacheRetention": False,
                "supportsCacheControlOnTools": False,
                "sendSessionAffinityHeaders": False,
                "sessionAffinityFormat": "custom",
            },
        ),
        "https://openrouter.ai/api",
    )
    assert explicit.supports_long_cache_retention is False
    assert explicit.supports_cache_control_on_tools is False
    assert explicit.send_session_affinity_headers is False
    assert explicit.session_affinity_format == "custom"


def test_built_anthropic_provider_carries_compat(tmp_path: Path) -> None:
    spec = NativeModelSpec(
        provider_name="custom-claude",
        model_id="claude-sonnet-4-5",
        display_name="Custom Claude",
        api="anthropic-messages",
        base_url="https://gateway.example/anthropic",
        compat={
            "supportsCacheControlOnTools": False,
            "supportsLongCacheRetention": False,
            "sendSessionAffinityHeaders": True,
        },
        cost=NativeModelCost(),
    )
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(api_key="k"),
        thinking_level=None,
    )
    client = _Client()
    provider = build_provider(resolved, spec=spec, http_client=client)
    assert isinstance(provider, AnthropicProvider)
    provider.complete(_request(tmp_path, session_id="sess-1", cache_retention="long"))
    request = client.requests[0]
    assert [where for where, _ in _markers(request["body"])] == [
        "system[0]",
        "messages[2][0]",
    ]
    assert request["body"]["system"][0]["cache_control"] == EPHEMERAL
    assert request["headers"]["x-session-affinity"] == "sess-1"


# ---- bedrock -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("us.anthropic.claude-opus-5-5", True),
        ("us.anthropic.claude-sonnet-5", True),
        ("global.anthropic.claude-fable-5", True),
        ("us.anthropic.claude-opus-4-6-v1", True),
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", True),
        ("anthropic.claude-3-7-sonnet-20250219-v1:0", True),
        ("anthropic.claude-3-5-haiku-20241022-v1:0", True),
        ("anthropic.claude-3-5-sonnet-20240620-v1:0", False),
        ("amazon.nova-pro-v1:0", False),
        ("arn:aws:bedrock:us-east-1:1:application-inference-profile/abc", False),
    ],
)
def test_bedrock_prompt_caching_gate(model_id: str, expected: bool) -> None:
    assert bedrock_supports_prompt_caching(model_id, env={}) is expected


def test_bedrock_force_cache_env_only_applies_to_non_claude_ids() -> None:
    env = {"AWS_BEDROCK_FORCE_CACHE": "1"}
    assert bedrock_supports_prompt_caching("amazon.nova-pro-v1:0", env=env)
    assert not bedrock_supports_prompt_caching(
        "anthropic.claude-3-5-sonnet-20240620-v1:0", env=env
    )
    assert not bedrock_supports_prompt_caching(
        "amazon.nova-pro-v1:0", env={"AWS_BEDROCK_FORCE_CACHE": "true"}
    )


def _bedrock(model_id: str) -> tuple[AmazonBedrockProvider, _Client]:
    client = _Client()
    provider = AmazonBedrockProvider(
        model_id=model_id,
        region="us-east-1",
        access_key="AKIDEXAMPLE",
        secret_key="secret",
        session_token=None,
        http_client=client,
        _clock=lambda: datetime(2024, 1, 15, tzinfo=UTC),
    )
    return provider, client


def test_bedrock_marks_system_and_last_user_block_but_not_tools(
    tmp_path: Path,
) -> None:
    provider, client = _bedrock("us.anthropic.claude-sonnet-5")
    provider.complete(_request(tmp_path))
    provider.complete(_request(tmp_path, cache_retention="long"))
    provider.complete(_request(tmp_path, cache_retention="none"))
    short, long, none = (request["body"] for request in client.requests)
    assert _markers(short) == [
        ("system[0]", EPHEMERAL),
        ("messages[2][0]", EPHEMERAL),
    ]
    assert [marker for _, marker in _markers(long)] == [EPHEMERAL_1H] * 2
    assert _markers(none) == []
    assert none["system"] == [{"type": "text", "text": "SYSTEM"}]


def test_bedrock_long_retention_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CACHE_RETENTION", "long")
    provider, client = _bedrock("us.anthropic.claude-opus-5-5")
    provider.complete(_request(tmp_path))
    assert _markers(client.requests[0]["body"])[0] == ("system[0]", EPHEMERAL_1H)


def test_bedrock_uncached_model_and_trailing_assistant(tmp_path: Path) -> None:
    provider, client = _bedrock("anthropic.claude-3-5-sonnet-20240620-v1:0")
    provider.complete(_request(tmp_path))
    assert _markers(client.requests[0]["body"]) == []

    provider, client = _bedrock("us.anthropic.claude-sonnet-5")
    provider.complete(
        _request(
            tmp_path,
            messages=(
                AgentUserMessage(content=ProductContent("hi")),
                AgentAssistantMessage(content=ProductContent("prefill")),
            ),
            system_prompt="",
        )
    )
    body = client.requests[0]["body"]
    assert "system" not in body
    assert _markers(body) == []


# ---- usage and cost ------------------------------------------------------------


def test_extract_anthropic_usage_reads_one_hour_write_split() -> None:
    usage = extract_anthropic_usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 300,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 100,
                "ephemeral_1h_input_tokens": 200,
            },
        }
    )
    assert usage["cache_write_tokens"] == 300
    assert usage["cache_write_1h_tokens"] == 200
    sample = AgentProviderUsageSample.from_mapping(usage)
    assert sample.cache_write_1h_tokens == 200
    assert "cache_write_1h_tokens" not in extract_anthropic_usage(
        {"input_tokens": 1, "output_tokens": 1}
    )


def test_turn_cost_prices_one_hour_writes_at_twice_input() -> None:
    pricing = AgentTokenPricing(
        input_per_million=3.0,
        output_per_million=15.0,
        reasoning_per_million=15.0,
        cache_read_per_million=0.3,
        cache_write_per_million=3.75,
    )
    accumulator = AgentUsageAccumulator(pricing)
    accumulator.absorb(
        AgentProviderUsageSample(
            input_tokens=1_000_000,
            cache_write_tokens=3_000_000,
            cache_write_1h_tokens=2_000_000,
        )
    )
    # Pi calculateCost: 1M input at 3 + 1M short write at 3.75 + 2M 1h write
    # at 2 x 3 input.
    assert accumulator.cost_usd == pytest.approx(3.0 + 3.75 + 12.0)
    assert accumulator.cache_write_tokens == 3_000_000
