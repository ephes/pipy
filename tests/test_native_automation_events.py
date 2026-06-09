"""Tests for the Pi-shaped session-event surface of the real tool loop.

`NativeToolReplSession` accepts an optional ``automation_observer`` sink. When
present, the *real* tool loop (the same loop the CLI/TUI drive) emits Pi's
``AgentSessionEvent`` vocabulary — ``agent_start``/``turn_start``/
``message_start``/``message_update`` (with an ``assistantMessageEvent``
``text_delta``)/``message_end``/``turn_end``/``agent_end`` and the
``tool_execution_*`` events — derived from observed run behavior, not a parallel
fake model. These tests pin the canonical sequences against the deterministic
fake provider.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


@dataclass(frozen=True, slots=True)
class _EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Return the provided text verbatim.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 1024}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=str(request.arguments["text"]),
            provider_correlation_id=request.provider_correlation_id,
        )


@dataclass(frozen=True, slots=True)
class _StreamingTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="stream",
            description="Stream partial output then finish.",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        if context.output_sink is not None:
            context.output_sink("partial-1")
            context.output_sink("partial-2")
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="streamed done",
            provider_correlation_id=request.provider_correlation_id,
        )


def _drive(session: NativeToolReplSession, prompt: str, tmp_path: Path) -> None:
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(prompt + "\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )


def test_emits_canonical_no_tool_event_sequence(tmp_path: Path) -> None:
    sink = _CollectingSink()
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_text_chunks=("SEEN:", "ROOT"),
    )
    session = NativeToolReplSession(provider=provider, automation_observer=sink)

    _drive(session, "ROOT", tmp_path)

    types = [event["type"] for event in sink.events]
    # Matches Pi's grammar: agent_start, turn_start, the user message_start/
    # message_end pair, then the streamed assistant message, turn_end, agent_end.
    assert types == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    # First message lifecycle is the user message; second is the assistant.
    message_starts = [e for e in sink.events if e["type"] == "message_start"]
    assert message_starts[0]["message"]["role"] == "user"
    assert message_starts[0]["message"]["content"] == [{"type": "text", "text": "ROOT"}]
    assert message_starts[1]["message"]["role"] == "assistant"


def test_text_deltas_concatenate_to_final_message(tmp_path: Path) -> None:
    sink = _CollectingSink()
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_text_chunks=("SEEN:", "ROOT"),
    )
    session = NativeToolReplSession(provider=provider, automation_observer=sink)

    _drive(session, "ROOT", tmp_path)

    deltas = [
        event["assistantMessageEvent"]["delta"]
        for event in sink.events
        if event["type"] == "message_update"
    ]
    assert "".join(deltas) == "SEEN:ROOT"
    for event in sink.events:
        if event["type"] == "message_update":
            ame = event["assistantMessageEvent"]
            assert ame["type"] == "text_delta"
            assert ame["contentIndex"] == 0

    message_end = next(
        e
        for e in sink.events
        if e["type"] == "message_end" and e["message"]["role"] == "assistant"
    )
    assert message_end["message"]["content"] == [
        {"type": "text", "text": "SEEN:ROOT"}
    ]


def test_message_start_has_empty_assistant_content(tmp_path: Path) -> None:
    sink = _CollectingSink()
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_text_chunks=("hi",),
    )
    session = NativeToolReplSession(provider=provider, automation_observer=sink)

    _drive(session, "ROOT", tmp_path)

    message_start = next(
        e
        for e in sink.events
        if e["type"] == "message_start" and e["message"]["role"] == "assistant"
    )
    assert message_start["message"] == {"role": "assistant", "content": []}


def test_agent_end_carries_messages_and_will_retry_false(tmp_path: Path) -> None:
    sink = _CollectingSink()
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_text_chunks=("done",),
    )
    session = NativeToolReplSession(provider=provider, automation_observer=sink)

    _drive(session, "ROOT", tmp_path)

    agent_end = sink.events[-1]
    assert agent_end["type"] == "agent_end"
    assert agent_end["willRetry"] is False
    roles = [message["role"] for message in agent_end["messages"]]
    assert "assistant" in roles


def test_emits_tool_execution_events_in_order(tmp_path: Path) -> None:
    sink = _CollectingSink()
    call = ProviderToolCall(
        provider_correlation_id="call-1",
        tool_name="echo",
        arguments_json='{"text":"hi"}',
    )
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        # First provider turn requests the echo tool; second turn settles.
        programmable_tool_calls=((call,), ()),
        final_text="all done",
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _EchoTool()},
        automation_observer=sink,
    )

    _drive(session, "use the tool", tmp_path)

    types = [event["type"] for event in sink.events]
    assert types.count("agent_start") == 1
    assert types.count("agent_end") == 1
    # Two turns: one with the tool call, one settling turn.
    assert types.count("turn_start") == 2

    start = next(e for e in sink.events if e["type"] == "tool_execution_start")
    end = next(e for e in sink.events if e["type"] == "tool_execution_end")
    assert start["toolName"] == "echo"
    assert start["toolCallId"] == "call-1"
    assert start["args"] == {"text": "hi"}
    assert end["toolCallId"] == "call-1"
    assert end["isError"] is False
    assert "hi" in str(end["result"])
    # tool_execution_start precedes its end, and both fall between the
    # assistant message_end and the turn_end of the tool turn.
    assert types.index("tool_execution_start") < types.index("tool_execution_end")


def test_emits_tool_execution_update_for_streaming_tool(tmp_path: Path) -> None:
    sink = _CollectingSink()
    call = ProviderToolCall(
        provider_correlation_id="call-1",
        tool_name="stream",
        arguments_json='{"x":1}',
    )
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((call,), ()),
        final_text="all done",
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={"stream": _StreamingTool()},
        automation_observer=sink,
    )

    _drive(session, "use the streaming tool", tmp_path)

    updates = [e for e in sink.events if e["type"] == "tool_execution_update"]
    assert len(updates) >= 1
    assert updates[0]["toolCallId"] == "call-1"
    assert updates[0]["toolName"] == "stream"
    assert updates[0]["args"] == {"x": 1}
    assert "partial" in updates[0]["partialResult"]
    types = [e["type"] for e in sink.events]
    # Bounded progress falls between the tool's start and end events.
    assert (
        types.index("tool_execution_start")
        < types.index("tool_execution_update")
        < types.index("tool_execution_end")
    )
