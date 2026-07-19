"""Slice A: extension command access to the last assistant message.

A `/answer`-style extension needs to read the most recent assistant message
text (and whether it was a complete text answer) from the live conversation.
This is exposed read-only through `ctx.conversation.last_assistant_message()`.
"""

from __future__ import annotations

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.extension_runtime import (
    AssistantMessageView,
    dispatch_extension_command,
)


def _command_map(handler):
    from pipy_harness.native.extension_runtime import RegisteredCommand

    return {
        "probe": RegisteredCommand(
            name="probe", description="probe", handler=handler, extension="t"
        )
    }


def test_last_assistant_message_exposes_recent_text() -> None:
    seen: dict[str, object] = {}

    def handler(ctx, args):
        msg = ctx.conversation.last_assistant_message()
        seen["msg"] = msg

    messages = [
        AgentUserMessage(content=ProductContent("hello")),
        AgentAssistantMessage(content=ProductContent("older answer")),
        AgentUserMessage(content=ProductContent("and then?")),
        AgentAssistantMessage(
            content=ProductContent("Which database: MySQL or Postgres?")
        ),
    ]
    dispatch = dispatch_extension_command(
        "/probe",
        _command_map(handler),
        cwd="/tmp",
        has_ui=True,
        messages=messages,
    )
    assert dispatch is not None and dispatch.ran
    view = seen["msg"]
    assert isinstance(view, AssistantMessageView)
    assert view.text == "Which database: MySQL or Postgres?"
    assert view.complete is True


def test_last_assistant_message_reports_incomplete_when_tool_calls_pending() -> None:
    seen: dict[str, object] = {}

    def handler(ctx, args):
        seen["msg"] = ctx.conversation.last_assistant_message()

    messages = [
        AgentUserMessage(content=ProductContent("do it")),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall(
                    provider_correlation_id="c1",
                    tool_name="bash",
                    arguments_json=ProductContent("{}"),
                ),
            ),
        ),
    ]
    dispatch = dispatch_extension_command(
        "/probe", _command_map(handler), cwd="/tmp", has_ui=True, messages=messages
    )
    assert dispatch is not None and dispatch.ran
    view = seen["msg"]
    assert isinstance(view, AssistantMessageView)
    assert view.complete is False


def test_last_assistant_message_is_none_without_assistant_turn() -> None:
    seen: dict[str, object] = {}

    def handler(ctx, args):
        seen["msg"] = ctx.conversation.last_assistant_message()

    dispatch = dispatch_extension_command(
        "/probe",
        _command_map(handler),
        cwd="/tmp",
        has_ui=True,
        messages=[AgentUserMessage(content=ProductContent("only user text"))],
    )
    assert dispatch is not None and dispatch.ran
    assert seen["msg"] is None


def test_conversation_defaults_to_empty_when_not_provided() -> None:
    seen: dict[str, object] = {}

    def handler(ctx, args):
        seen["msg"] = ctx.conversation.last_assistant_message()

    # Older call sites that do not pass messages still get a usable, empty view.
    dispatch = dispatch_extension_command(
        "/probe", _command_map(handler), cwd="/tmp", has_ui=True
    )
    assert dispatch is not None and dispatch.ran
    assert seen["msg"] is None
