"""Finite native Mistral reasoning capture, request projection and durable replay."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from provider_chat_completions_contract import FakeJsonHTTPClient

from pipy_harness import sdk
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.providers.mistral import MistralProvider
from pipy_harness.native.providers.mistral_wire import MISTRAL_CONVERSATIONS_API
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_call_ids import portable_tool_correlation_id
from pipy_harness.native.tools.read import ReadTool


def _body(content: Any, *, tools: bool = False) -> dict[str, Any]:
    message = {
        "content": content,
        "reasoning_content": "ignored scalar",
        "reasoning_details": [{"type": "reasoning.text", "text": "ignored details"}],
    }
    if tools:
        message["tool_calls"] = [
            {
                "id": "call|native",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"README.md"}'},
            }
        ]
    return {
        "object": "chat.completion",
        "choices": [
            {"message": message, "finish_reason": "tool_calls" if tools else "stop"}
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 4,
            "total_tokens": 24,
            "num_cached_tokens": 30,
        },
    }


def _request(path: Path, messages: tuple[Any, ...] = ()) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="continue",
        provider_name="mistral",
        model_id="native-model",
        cwd=path,
        messages=messages,
    )


def _provider(
    body: dict[str, Any], *, model: str = "native-model", provider: str = "mistral"
) -> tuple[MistralProvider, FakeJsonHTTPClient]:
    client = FakeJsonHTTPClient(JsonResponse(200, body))
    return MistralProvider(
        model_id=model, provider_name=provider, api_key="test-key", http_client=client
    ), client


def test_native_capture_order_coalescing_unsigned_tools_usage_and_continuation(
    tmp_path: Path,
) -> None:
    content = [
        "",
        {
            "type": "thinking",
            "thinking": [
                {"text": "plan"},
                {"type": "other", "text": "+more"},
                {"text": 3},
            ],
            "signature": "not imported",
        },
        "",
        {"type": "text", "text": ""},
        {"type": "thinking", "thinking": [{"text": "+last"}]},
        {"type": "text", "text": "first"},
        " second",
        {"type": "thinking", "thinking": [{"text": "again"}]},
        {"type": "text", "text": "final"},
        None,
        {"type": "unknown", "text": "not displayed"},
        {"type": "thinking", "thinking": "bad"},
    ]
    body = _body(content, tools=True)
    original = copy.deepcopy(body)
    provider, client = _provider(body)
    text: list[str] = []
    thinking: list[str] = []
    result = provider.complete(
        _request(tmp_path), stream_sink=text.append, reasoning_sink=thinking.append
    )
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.api == MISTRAL_CONVERSATIONS_API
    assert result.final_text == "first secondfinal"
    assert result.content_blocks == (
        ThinkingContent("plan+more+last"),
        TextContent("first second"),
        ThinkingContent("again"),
        TextContent("final"),
        *result.tool_calls,
    )
    assert result.tool_calls[0].provider_correlation_id == "call|native"
    assert result.tool_calls[0].arguments_json == '{"path":"README.md"}'
    assert result.usage == {
        "input_tokens": 20,
        "output_tokens": 4,
        "total_tokens": 24,
        "cached_tokens": 20,
    }
    assert (
        not text and not thinking
    )  # buffered transport emits no incremental callbacks
    assert body == original
    assistant = AgentAssistantMessage.from_blocks(
        [
            *(
                block
                for block in result.content_blocks
                if isinstance(block, TextContent | ThinkingContent)
            ),
            AgentToolCall(
                "call|native", "read", ProductContent('{"path":"README.md"}')
            ),
        ],
        provider="mistral",
        api=result.api,
        model="native-model",
    )
    tool = AgentToolResultMessage(
        tool_request_id="pipy-tool-stored-call",
        tool_name="read",
        content=ProductContent("stored output"),
        provider_correlation_id="call|native",
    )
    provider.complete(_request(tmp_path, (assistant, tool)))
    messages = client.requests[1]["body"]["messages"]
    assert messages[1] == {
        "role": "assistant",
        "prefix": False,
        "content": [
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": "plan+more+last"}],
            },
            {"type": "text", "text": "first second"},
            {"type": "thinking", "thinking": [{"type": "text", "text": "again"}]},
            {"type": "text", "text": "final"},
        ],
        "tool_calls": [
            {
                "id": portable_tool_correlation_id("call|native"),
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"README.md"}'},
            }
        ],
    }
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": portable_tool_correlation_id("call|native"),
        "content": "stored output",
    }


@pytest.mark.parametrize(
    ("content", "tools", "success", "expected"),
    [
        ("plain", False, True, (TextContent("plain"),)),
        ([{"type": "text", "text": "typed"}], False, True, (TextContent("typed"),)),
        (
            [{"type": "thinking", "thinking": [{"text": "only"}]}],
            False,
            True,
            (ThinkingContent("only"),),
        ),
        (None, True, True, ()),
        ([], False, False, ()),
        (
            [
                {"type": "text", "text": 42},
                {"type": "thinking", "thinking": [None, {"text": False}]},
            ],
            False,
            False,
            (),
        ),
        (None, False, False, ()),
    ],
)
def test_native_content_finite_shapes(
    tmp_path: Path, content: Any, tools: bool, success: bool, expected: tuple[Any, ...]
) -> None:
    provider, _ = _provider(_body(content, tools=tools))
    result = provider.complete(_request(tmp_path))
    assert (result.status == HarnessStatus.SUCCEEDED) is success
    if success:
        assert result.content_blocks == (*expected, *result.tool_calls)
        assert result.api == MISTRAL_CONVERSATIONS_API
    else:
        assert result.error_type == "MistralResponseParseError"


@pytest.mark.parametrize(
    ("provider", "api", "model", "retained"),
    [
        ("mistral", "mistral-conversations", "native-model", True),
        ("other", "mistral-conversations", "native-model", False),
        ("mistral", "openai-completions", "native-model", False),
        ("mistral", "mistral", "native-model", False),
        ("mistral", None, "native-model", False),
        ("mistral", "mistral-conversations", "other", False),
    ],
)
def test_native_replay_target_and_signature_nonforwarding(
    tmp_path: Path, provider: str, api: str | None, model: str, retained: bool
) -> None:
    assistant = AgentAssistantMessage.from_blocks(
        [
            ThinkingContent("unsigned"),
            TextContent("visible"),
            ThinkingContent(" signed ", "opaque-secret"),
            ThinkingContent("", "encrypted"),
            ThinkingContent("  "),
        ],
        provider=provider,
        api=api,
        model=model,
    )
    original = copy.deepcopy(assistant)
    target, client = _provider(_body("done"))
    target.complete(_request(tmp_path, (assistant,)))
    expected: list[dict[str, Any]] = [{"type": "text", "text": "visible"}]
    if retained:
        expected.insert(
            0, {"type": "thinking", "thinking": [{"type": "text", "text": "unsigned"}]}
        )
        expected.append(
            {"type": "thinking", "thinking": [{"type": "text", "text": " signed "}]}
        )
    assert client.requests[0]["body"]["messages"][1] == {
        "role": "assistant",
        "content": expected,
        "prefix": False,
    }
    assert "opaque-secret" not in json.dumps(client.requests)
    assert assistant == original


def test_native_empty_assistant_omitted(tmp_path: Path) -> None:
    provider, client = _provider(_body("done"))
    assistant = AgentAssistantMessage.from_blocks(
        [ThinkingContent("", "encrypted"), TextContent("  ")],
        provider="mistral",
        api=MISTRAL_CONVERSATIONS_API,
        model="native-model",
    )
    provider.complete(_request(tmp_path, (assistant,)))
    assert client.requests[0]["body"]["messages"] == [
        {"role": "system", "content": "SYS"}
    ]


@pytest.mark.parametrize("mismatch", [None, "provider", "api", "model", "missing-api"])
def test_durable_native_capture_reopen_and_target_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str | None
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    capture, _ = _provider(
        _body(
            [
                {"type": "thinking", "thinking": [{"text": "captured plan"}]},
                {"type": "text", "text": "captured answer"},
            ]
        )
    )
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    assert source.path is not None
    with sdk.open_product_session(
        workspace=tmp_path, session_path=source.path, provider=capture
    ) as session:
        first = session.submit("capture")
    captured = first.messages[-1]
    assert isinstance(captured, AgentAssistantMessage)
    assert captured.api == MISTRAL_CONVERSATIONS_API
    assert captured.ordered_content() == (
        ThinkingContent("captured plan"),
        TextContent("captured answer"),
    )
    source = NativeSessionTree.open(source.path)
    assert source.path is not None
    imported = AgentAssistantMessage.from_blocks(
        [
            ThinkingContent("imported Pi plan", "private-signature"),
            TextContent("reading"),
            AgentToolCall(
                "native|stored", "read", ProductContent('{"path":"README.md"}')
            ),
        ],
        provider="mistral",
        api=MISTRAL_CONVERSATIONS_API,
        model="native-model",
    )
    if mismatch == "missing-api":
        imported = replace(imported, api=None)
    elif mismatch == "api":
        imported = replace(imported, api="openai-completions")
    tool = AgentToolResultMessage(
        tool_request_id="pipy-tool-stored",
        tool_name="read",
        content=ProductContent("historical result"),
        provider_correlation_id="native|stored",
    )
    source.append_message(AgentUserMessage(ProductContent("historical tool request")))
    source.append_message(imported)
    source.append_message(tool)
    original = source.path.read_bytes()
    old_messages = source.build_context().messages
    replay = tmp_path / "replay.jsonl"
    replay.write_bytes(original)

    def forbidden(self: ReadTool, request: object, context: object) -> object:
        raise AssertionError("historical tool must never execute")

    monkeypatch.setattr(ReadTool, "invoke", forbidden)
    target, client = _provider(
        _body(
            [
                {"type": "thinking", "thinking": [{"text": "new plan"}]},
                {"type": "text", "text": "continued"},
            ]
        ),
        model="changed" if mismatch == "model" else "native-model",
        provider="other" if mismatch == "provider" else "mistral",
    )
    with sdk.open_product_session(
        workspace=tmp_path, session_path=replay, provider=target
    ) as session:
        snapshot = session.submit("continue")
    outbound = client.requests[0]["body"]["messages"]
    native_history = next(m for m in outbound if m.get("tool_calls"))
    expected: list[dict[str, Any]] = [{"type": "text", "text": "reading"}]
    if mismatch is None:
        expected.insert(
            0,
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": "imported Pi plan"}],
            },
        )
    assert native_history["content"] == expected
    assert native_history["prefix"] is False
    wire_id = portable_tool_correlation_id("native|stored")
    assert native_history["tool_calls"][0]["id"] == wire_id
    assert next(m for m in outbound if m["role"] == "tool")["tool_call_id"] == wire_id
    assert "private-signature" not in json.dumps(outbound)
    old_agent_messages = tuple(
        message
        for message in old_messages
        if not isinstance(message, AgentSystemMessage)
    )
    assert snapshot.messages[: len(old_agent_messages)] == old_agent_messages
    assert replay.read_bytes().startswith(original)
    assert source.path.read_bytes() == original
    assert source.build_context().messages == old_messages
    durable = NativeSessionTree.open(replay).build_context().messages
    assert durable[: len(old_messages)] == old_messages
    newly_captured = durable[-1]
    assert isinstance(newly_captured, AgentAssistantMessage)
    assert newly_captured.api == MISTRAL_CONVERSATIONS_API
    # A second real SDK reopen sends the newly captured unsigned thinking.
    second, second_client = _provider(
        _body("second"), model=target.model_id, provider=target.name
    )
    with sdk.open_product_session(
        workspace=tmp_path, session_path=replay, provider=second
    ) as session:
        session.submit("continue again")
    last_assistant = [
        m
        for m in second_client.requests[0]["body"]["messages"]
        if m["role"] == "assistant"
    ][-1]
    assert last_assistant["content"] == [
        {"type": "thinking", "thinking": [{"type": "text", "text": "new plan"}]},
        {"type": "text", "text": "continued"},
    ]


def test_current_tool_executes_once_and_pairs_native_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    (tmp_path / "README.md").write_text("current file contents\n")
    provider, client = _provider(
        _body(
            [{"type": "thinking", "thinking": [{"text": "read current file"}]}],
            tools=True,
        )
    )
    original_post = client.post_json

    def post(*args: Any, **kwargs: Any) -> JsonResponse:
        response = original_post(*args, **kwargs)
        client.response = JsonResponse(200, _body("finished"))
        return response

    monkeypatch.setattr(client, "post_json", post)
    invocations: list[str] = []
    original_invoke = ReadTool.invoke

    def invoke(self: ReadTool, request: Any, context: Any) -> Any:
        invocations.append("read")
        return original_invoke(self, request, context)

    monkeypatch.setattr(ReadTool, "invoke", invoke)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    assert tree.path is not None
    with sdk.open_product_session(
        workspace=tmp_path, session_path=tree.path, provider=provider
    ) as session:
        snapshot = session.submit("read README.md")
    assert invocations == ["read"]
    assert len(client.requests) == 2
    followup = client.requests[1]["body"]["messages"]
    assistant = next(m for m in followup if m.get("tool_calls"))
    assert assistant["content"] == [
        {
            "type": "thinking",
            "thinking": [{"type": "text", "text": "read current file"}],
        }
    ]
    assert assistant["prefix"] is False
    result = next(m for m in followup if m["role"] == "tool")
    assert (
        result["tool_call_id"]
        == assistant["tool_calls"][0]["id"]
        == portable_tool_correlation_id("call|native")
    )
    assert "current file contents" in result["content"]
    assert snapshot.messages[-1].content.value == "finished"


def test_native_assistant_keeps_existing_tool_image_and_system_composition(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.agent.content import ImageContent

    image = ImageContent("aGVsbG8=", "image/png")
    assistant = AgentAssistantMessage.from_blocks(
        [
            ThinkingContent("native plan"),
            AgentToolCall("image-call", "read", ProductContent("{}")),
        ],
        provider="mistral",
        api=MISTRAL_CONVERSATIONS_API,
        model="native-model",
    )
    result = AgentToolResultMessage(
        tool_request_id="pipy-tool-image",
        tool_name="read",
        content=ProductContent("image output"),
        provider_correlation_id="image-call",
        blocks=(TextContent("image output"), image),
    )
    request = _request(
        tmp_path,
        (
            AgentUserMessage(ProductContent("look")),
            assistant,
            result,
            AgentSystemMessage(ProductContent("later system")),
            AgentUserMessage(ProductContent("continue")),
        ),
    )
    original = copy.deepcopy(request)
    provider, client = _provider(_body("done"))
    provider = replace(provider, supports_mid_convo_system_messages=True)
    provider.complete(request)
    assert client.requests[0]["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "look"},
        {
            "role": "assistant",
            "prefix": False,
            "content": [
                {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": "native plan"}],
                }
            ],
            "tool_calls": [
                {
                    "id": "image-call",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "image-call", "content": "image output"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        },
        {"role": "system", "content": "later system"},
        {"role": "user", "content": "continue"},
    ]
    assert request == original
