"""Contracts for the canonical provider-neutral agent message shapes."""

from __future__ import annotations

from typing import cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.tools import make_tool_request_id


def test_user_message_round_trip() -> None:
    message = AgentUserMessage(content=ProductContent("hello"))

    assert message.content.value == "hello"


def test_product_content_rejects_non_string_content() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        ProductContent(cast(str, 42))


def test_assistant_message_explicit_empty_content_and_tool_calls() -> None:
    message = AgentAssistantMessage(content=ProductContent(""))

    assert message.content.value == ""
    assert message.tool_calls == ()


def test_assistant_message_round_trip_with_tool_calls() -> None:
    call = AgentToolCall(
        provider_correlation_id="call_abc",
        tool_name="read",
        arguments_json=ProductContent('{"path": "x.py"}'),
    )

    message = AgentAssistantMessage(
        content=ProductContent("thinking"), tool_calls=(call,)
    )

    assert message.content.value == "thinking"
    assert message.tool_calls == (call,)


def test_assistant_message_rejects_non_tuple_tool_calls() -> None:
    call = AgentToolCall("call_abc", "read", ProductContent("{}"))
    with pytest.raises(TypeError, match="must be a tuple"):
        AgentAssistantMessage(
            ProductContent(""), cast(tuple[AgentToolCall, ...], [call])
        )


def test_assistant_message_rejects_non_agent_tool_call_entries() -> None:
    with pytest.raises(TypeError, match="must contain AgentToolCall"):
        AgentAssistantMessage(
            ProductContent(""), cast(tuple[AgentToolCall, ...], ("not a tool call",))
        )


def test_tool_result_message_round_trip() -> None:
    request_id = make_tool_request_id()
    message = AgentToolResultMessage(
        tool_request_id=request_id,
        tool_name="read",
        content=ProductContent("ok"),
        provider_correlation_id="call_abc",
    )

    assert message.tool_request_id == request_id
    assert message.tool_name == "read"
    assert message.content.value == "ok"
    assert message.is_error is False
    assert message.provider_correlation_id == "call_abc"


@pytest.mark.parametrize("field", ["tool_request_id", "tool_name"])
def test_tool_result_message_rejects_empty_identity(field: str) -> None:
    values = {
        "tool_request_id": make_tool_request_id(),
        "tool_name": "read",
    }
    values[field] = ""

    with pytest.raises(ValueError, match=field):
        AgentToolResultMessage(
            tool_request_id=values["tool_request_id"],
            tool_name=values["tool_name"],
            content=ProductContent(""),
            provider_correlation_id="call_abc",
        )


def test_tool_result_message_rejects_non_bool_is_error() -> None:
    with pytest.raises(TypeError, match="is_error"):
        AgentToolResultMessage(
            tool_request_id=make_tool_request_id(),
            tool_name="read",
            content=ProductContent(""),
            provider_correlation_id="call_abc",
            is_error=cast(bool, "yes"),
        )


def test_tool_result_message_rejects_empty_provider_correlation_id() -> None:
    with pytest.raises(ValueError, match="provider_correlation_id"):
        AgentToolResultMessage(
            tool_request_id=make_tool_request_id(),
            tool_name="read",
            content=ProductContent(""),
            provider_correlation_id="",
        )


def test_canonical_messages_preserve_legacy_content_bounds() -> None:
    with pytest.raises(ValueError, match="AgentUserMessage.content exceeds"):
        AgentUserMessage(
            ProductContent("x" * (AgentUserMessage.CONTENT_MAX_LENGTH + 1))
        )
    with pytest.raises(ValueError, match="AgentAssistantMessage.content exceeds"):
        AgentAssistantMessage(
            ProductContent("x" * (AgentAssistantMessage.CONTENT_MAX_LENGTH + 1))
        )
    with pytest.raises(ValueError, match="AgentToolResultMessage.content exceeds"):
        AgentToolResultMessage(
            tool_request_id=make_tool_request_id(),
            tool_name="read",
            content=ProductContent(
                "x" * (AgentToolResultMessage.CONTENT_MAX_LENGTH + 1)
            ),
            provider_correlation_id="call_abc",
        )


def test_tool_result_message_requires_pipy_owned_request_id() -> None:
    with pytest.raises(ValueError, match="must be pipy-owned"):
        AgentToolResultMessage(
            tool_request_id="provider-owned-id",
            tool_name="read",
            content=ProductContent("result"),
            provider_correlation_id="call_abc",
        )


def test_agent_message_union_accepts_each_kind() -> None:
    user = AgentUserMessage(content=ProductContent("hi"))
    assistant = AgentAssistantMessage(content=ProductContent("hello"))
    tool_result = AgentToolResultMessage(
        tool_request_id=make_tool_request_id(),
        tool_name="read",
        content=ProductContent("ok"),
        provider_correlation_id="call_abc",
    )

    messages: tuple[AgentMessage, ...] = (user, assistant, tool_result)

    assert isinstance(messages[0], AgentUserMessage)
    assert isinstance(messages[1], AgentAssistantMessage)
    assert isinstance(messages[2], AgentToolResultMessage)
