"""Product contracts for transient active input across compaction and outcomes."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentToolResultMessage,
    AgentUserMessage,
    MessageCompleted,
    ProductContent,
)
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_types import QueuedCustomMessage
from pipy_harness.native.extensions.contracts import (
    ActivatedExtension,
    ExtensionActivationBatch,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.session_tree import (
    CustomMessageEntry,
    MessageEntry,
    NativeSessionTree,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

_TRANSIENTS = ("TRANSIENT-A", "TRANSIENT-B")


@dataclass(slots=True)
class _EventSink:
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class _ScriptProvider:
    calls: tuple[tuple[ProviderToolCall, ...], ...]
    statuses: tuple[HarnessStatus, ...] = ()
    supports_tool_calls: bool = True
    name: str = "active-input"
    model_id: str = "active-input-model"
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        index = len(self.requests) - 1
        status = (
            self.statuses[index]
            if index < len(self.statuses)
            else HarnessStatus.SUCCEEDED
        )
        tool_calls = self.calls[index] if index < len(self.calls) else ()
        now = datetime.now(UTC)
        return ProviderResult(
            status=status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="" if tool_calls else f"answer-{index + 1}",
            tool_calls=tool_calls,
            error_type="FixtureProviderFailed"
            if status is HarnessStatus.FAILED
            else None,
            error_message="fixture provider failure"
            if status is HarnessStatus.FAILED
            else None,
        )


@dataclass(slots=True)
class _CancelledProvider:
    supports_tool_calls: bool = True
    name: str = "active-input-cancelled"
    model_id: str = "active-input-model"
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        raise ProviderCancelledError("fixture cancellation")


@dataclass(frozen=True, slots=True)
class _EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Return fixture text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=str(request.arguments["text"]),
            provider_correlation_id=request.provider_correlation_id,
        )


def _call(arguments: str, correlation_id: str = "provider-call-1") -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation_id,
        tool_name="echo",
        arguments_json=arguments,
    )


def _activation_batch() -> ExtensionActivationBatch:
    custom_messages = tuple(
        QueuedCustomMessage(
            custom_type="note",
            content=content,
            display=False,
            details=None,
            options={"deliverAs": "nextTurn"},
        )
        for content in _TRANSIENTS
    )
    activated = ActivatedExtension(
        name="active-input",
        version="1",
        path_label="active-input",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        custom_messages=custom_messages,
    )
    return ExtensionActivationBatch(
        activated=(activated,),
        message_outbox=[],
        custom_message_outbox=[],
    )


def _seed_tree(tmp_path: Path, *, equal_last_user: bool = False) -> NativeSessionTree:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    for index in range(3):
        user = "same" if equal_last_user and index == 2 else f"old-{index}"
        tree.append_message(AgentUserMessage(ProductContent(user)))
        tree.append_message(AgentAssistantMessage(ProductContent(f"answer-{index}")))
    return tree


def _contents(messages) -> list[str]:
    return [message.content.value for message in messages]


def _completed(sink: _EventSink) -> list[AgentRunCompleted]:
    return [event for event in sink.events if isinstance(event, AgentRunCompleted)]


def _assert_no_transient_events(sink: _EventSink) -> None:
    completed_messages = [
        message for event in _completed(sink) for message in event.result.messages
    ]
    completed_message_events = [
        event.message for event in sink.events if isinstance(event, MessageCompleted)
    ]
    assert not any(
        marker in message.content.value
        for marker in _TRANSIENTS
        for message in (*completed_messages, *completed_message_events)
    )


def test_auto_compaction_keeps_identity_overlay_on_every_provider_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.loop_step as loop_step

    threshold_histories: list[tuple[str, ...]] = []

    def compact_first(messages, **_kwargs: object) -> bool:
        threshold_histories.append(tuple(message.content.value for message in messages))
        return len(threshold_histories) == 1

    monkeypatch.setattr(loop_step, "should_compact_agent_history", compact_first)
    tree = _seed_tree(tmp_path, equal_last_user=True)
    provider = _ScriptProvider(calls=((_call('{"text":"tool-result"}'),), (), ()))
    sink = _EventSink()

    result = CodingSession(
        provider=provider,
        tool_registry={"echo": _EchoTool()},
        native_session=tree,
        initial_extension_batch=_activation_batch(),
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("same\nsecond\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.compaction_count == 1
    assert result.compaction_dropped_group_count == 2
    assert len(provider.requests) == 3
    first, tool_follow_up, next_run = provider.requests
    assert _contents(first.messages) == [
        "same",
        "answer-2",
        "same",
        *_TRANSIENTS,
    ]
    accepted_entry = next(
        entry
        for entry in reversed(tree.entries)
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentUserMessage)
        and entry.message.content.value == "same"
    )
    assert first.messages[0] is not accepted_entry.message
    assert first.messages[2] is accepted_entry.message
    assert _contents(tool_follow_up.messages) == [
        "same",
        "answer-2",
        "same",
        *_TRANSIENTS,
        "",
        "tool-result",
    ]
    assert _contents(next_run.messages) == [
        "same",
        "answer-2",
        "same",
        "",
        "tool-result",
        "answer-2",
        "second",
    ]
    assert all(
        marker not in content
        for history in threshold_histories
        for marker in _TRANSIENTS
        for content in history
    )

    completed = _completed(sink)
    assert len(completed) == 2
    assert _contents(completed[0].result.messages) == [
        "same",
        "",
        "tool-result",
        "answer-2",
    ]
    assert _contents(completed[1].result.messages) == ["second", "answer-3"]
    _assert_no_transient_events(sink)

    canonical_tree_messages = [
        entry.message
        for entry in tree.entries
        if isinstance(entry, MessageEntry)
        and isinstance(
            entry.message,
            (AgentUserMessage, AgentAssistantMessage, AgentToolResultMessage),
        )
    ]
    assert all(
        marker not in message.content.value
        for marker in _TRANSIENTS
        for message in canonical_tree_messages
    )
    # `send_message` persistence is a distinct extension contract: this slice
    # overlays those entries without duplicating them as canonical messages.
    assert [
        entry.content for entry in tree.entries if isinstance(entry, CustomMessageEntry)
    ] == list(_TRANSIENTS)


@pytest.mark.parametrize(
    ("case", "expected_outcome", "expected_contents"),
    [
        ("success", AgentRunOutcome.SUCCEEDED, ["active", "answer-1"]),
        ("failure", AgentRunOutcome.FAILED, ["active"]),
        ("cancel", AgentRunOutcome.CANCELLED, ["active"]),
        (
            "fatal",
            AgentRunOutcome.FAILED,
            [
                "active",
                "",
                "echo: missing required argument(s): text",
                "echo: missing required argument(s): text",
                "echo: missing required argument(s): text",
            ],
        ),
    ],
)
def test_compacted_run_result_is_anchored_and_excludes_overlay_for_every_outcome(
    case: str,
    expected_outcome: AgentRunOutcome,
    expected_contents: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.loop_step as loop_step

    monkeypatch.setattr(
        loop_step,
        "should_compact_agent_history",
        lambda _messages, **_kwargs: True,
    )
    provider: ProviderPort
    if case == "success":
        provider = _ScriptProvider(calls=((),))
    elif case == "failure":
        provider = _ScriptProvider(calls=((),), statuses=(HarnessStatus.FAILED,))
    elif case == "cancel":
        provider = _CancelledProvider()
    else:
        provider = _ScriptProvider(
            calls=(
                (
                    _call("{}", "malformed-1"),
                    _call("{}", "malformed-2"),
                    _call("{}", "malformed-3"),
                ),
            )
        )
    sink = _EventSink()

    CodingSession(
        provider=provider,
        tool_registry={"echo": _EchoTool()},
        native_session=_seed_tree(tmp_path),
        initial_extension_batch=_activation_batch(),
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("active\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    completed = _completed(sink)
    assert len(completed) == 1
    assert completed[0].result.outcome is expected_outcome
    assert _contents(completed[0].result.messages) == expected_contents
    if case == "fatal":
        assert (
            sum(
                isinstance(message, AgentToolResultMessage)
                for message in completed[0].result.messages
            )
            == 3
        )
    _assert_no_transient_events(sink)
