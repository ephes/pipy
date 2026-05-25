"""Slice 3 tests: the provider-agnostic message envelope shapes.

These tests pin the small `UserMessage`/`AssistantMessage`/`ToolResultMessage`
shapes exposed by `pipy_harness.native.tools.messages`. They do not exercise
any provider serialization: that happens in later slices when real adapters
gain tool-call serialization.
"""

from __future__ import annotations

import pytest

from pipy_harness.native import ProviderToolCall
from pipy_harness.native.tools import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
    UserMessage,
    make_tool_request_id,
)


def test_user_message_round_trip():
    message = UserMessage(content="hello")

    assert message.content == "hello"


def test_user_message_rejects_non_string_content():
    with pytest.raises(ValueError, match="must be a string"):
        UserMessage(content=42)  # type: ignore[arg-type]


def test_user_message_rejects_oversized_content():
    with pytest.raises(ValueError, match="exceeds"):
        UserMessage(content="x" * (UserMessage.CONTENT_MAX_LENGTH + 1))


def test_assistant_message_defaults_to_empty_content_and_tool_calls():
    message = AssistantMessage()

    assert message.content == ""
    assert message.tool_calls == ()


def test_assistant_message_round_trip_with_tool_calls():
    call = ProviderToolCall(
        provider_correlation_id="call_abc",
        tool_name="read",
        arguments_json='{"path": "x.py"}',
    )

    message = AssistantMessage(content="thinking", tool_calls=(call,))

    assert message.content == "thinking"
    assert message.tool_calls == (call,)


def test_assistant_message_rejects_non_tuple_tool_calls():
    call = ProviderToolCall(
        provider_correlation_id="call_abc",
        tool_name="read",
        arguments_json="{}",
    )
    with pytest.raises(ValueError, match="must be a tuple"):
        AssistantMessage(tool_calls=[call])  # type: ignore[arg-type]


def test_assistant_message_rejects_non_provider_tool_call_entries():
    with pytest.raises(ValueError, match="must be a ProviderToolCall"):
        AssistantMessage(tool_calls=("not a tool call",))  # type: ignore[arg-type]


def test_tool_result_message_round_trip():
    request_id = make_tool_request_id()
    message = ToolResultMessage(
        tool_request_id=request_id,
        output_text="ok",
        provider_correlation_id="call_abc",
    )

    assert message.tool_request_id == request_id
    assert message.output_text == "ok"
    assert message.is_error is False
    assert message.provider_correlation_id == "call_abc"


def test_tool_result_message_rejects_non_pipy_owned_id():
    with pytest.raises(ValueError, match="pipy-owned"):
        ToolResultMessage(
            tool_request_id="call_provider_xyz",
            output_text="",
        )


def test_tool_result_message_rejects_non_string_output():
    with pytest.raises(ValueError, match="output_text must be a string"):
        ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text=None,  # type: ignore[arg-type]
        )


def test_tool_result_message_rejects_oversized_output():
    with pytest.raises(ValueError, match="exceeds"):
        ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text="x" * (ToolResultMessage.OUTPUT_TEXT_MAX_LENGTH + 1),
        )


def test_tool_result_message_rejects_non_bool_is_error():
    with pytest.raises(ValueError, match="is_error"):
        ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text="",
            is_error="yes",  # type: ignore[arg-type]
        )


def test_tool_result_message_rejects_empty_provider_correlation_id():
    with pytest.raises(ValueError, match="provider_correlation_id"):
        ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text="",
            provider_correlation_id="",
        )


def test_loop_message_union_accepts_each_kind():
    user = UserMessage(content="hi")
    assistant = AssistantMessage(content="hello")
    tool_result = ToolResultMessage(
        tool_request_id=make_tool_request_id(),
        output_text="ok",
    )

    messages: tuple[LoopMessage, ...] = (user, assistant, tool_result)

    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert isinstance(messages[2], ToolResultMessage)
