"""Aborted and failed assistant turns are kept but never replayed (DF1-F6).

Pi (`4df157433`) records an assistant message with ``stopReason: "aborted"``
or ``"error"`` and the content streamed so far (``agent-loop.ts``), persists
it (``agent-session.ts`` on ``message_end``), draws ``Operation aborted`` /
``Error: …`` after it, and skips it in every provider request
(``transform-messages.ts``).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_native_agent_loop import (
    _make_loop,
    _provider_result,
    _run_input,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import envelope_to_chat_message
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEventSink,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    AssistantTextDelta,
    MessageCompleted,
    ProductContent,
    RetryScheduled,
    TurnCompleted,
    provider_replay_messages,
)
from pipy_harness.native.agent.messages import AgentMessageUsage
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import AgentFailure, AgentTurnOutcome
from pipy_harness.native.agent.usage_json import usage_to_json
from pipy_harness.native.agent_loop_policy import materialize_provider_request
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.coding.compaction import build_summary_request
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.coding.state import CodingProviderBinding
from pipy_harness.native.extensions.session_views import _ConversationView
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.openai_codex_provider import _responses_input_messages
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.providers.anthropic_messages_wire import messages_payload
from pipy_harness.native.providers.chat_completions_wire import chat_transcript
from pipy_harness.native.providers.google_generate_content_wire import (
    gemini_contents,
)
from pipy_harness.native.providers.openai_responses_wire import responses_input
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.session_tree_commands import entry_preview

# Every Pi assistant message carries ``usage`` (USAGE1); these carry none.
ZERO_USAGE = usage_to_json(AgentMessageUsage())


PARTIAL = "PARTIAL-ABORTED-TEXT"
FAILED_PARTIAL = "PARTIAL-FAILED-TEXT"


def _aborted(text: str = PARTIAL) -> AgentAssistantMessage:
    return AgentAssistantMessage(
        ProductContent(text), stop_reason=AgentStopReason.ABORTED
    )


def _failed(text: str = FAILED_PARTIAL) -> AgentAssistantMessage:
    return AgentAssistantMessage(
        ProductContent(text),
        stop_reason=AgentStopReason.ERROR,
        error_message="provider exploded",
    )


# -- message model -------------------------------------------------------------


def test_stopped_message_invariants() -> None:
    with pytest.raises(ValueError, match="requires a stop_reason"):
        AgentAssistantMessage(ProductContent("x"), error_message="boom")
    # Pi keeps a stopped turn's partial tool calls (DF1-F6b); they are never
    # executed or replayed.
    call = AgentToolCall("c1", "read", ProductContent("{}"))
    stopped = AgentAssistantMessage(
        ProductContent(""), (call,), stop_reason=AgentStopReason.ABORTED
    )
    assert provider_replay_messages((stopped,)) == ()
    with pytest.raises(TypeError, match="stop_reason"):
        AgentAssistantMessage(ProductContent(""), stop_reason="aborted")  # type: ignore[arg-type]


def test_replay_filter_drops_only_stopped_assistants() -> None:
    first = AgentUserMessage(ProductContent("first"))
    answer = AgentAssistantMessage(ProductContent("answer"))
    second = AgentUserMessage(ProductContent("second"))
    history = (first, answer, second, _aborted(), _failed())

    assert provider_replay_messages(history) == (first, answer, second)
    clean = (first, answer)
    assert provider_replay_messages(clean) is clean


# -- the agent loop ------------------------------------------------------------


def test_cancelled_turn_keeps_its_streamed_text_as_an_aborted_message() -> None:
    order: list[str] = []
    loop, _provider, events, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.OPERATOR_ABORT
            )
        ],
        emit_delta=True,
    )

    outcome = loop.run(_run_input())

    aborted = outcome.final_history[-1]
    assert isinstance(aborted, AgentAssistantMessage)
    assert aborted.content.value == "delta"
    assert aborted.stop_reason is AgentStopReason.ABORTED
    assert aborted.error_message is None
    assert outcome.result.messages[-1] is aborted
    completed = [e for e in events.events if isinstance(e, MessageCompleted)]
    assert completed[-1].message is aborted
    turn_end = [e for e in events.events if isinstance(e, TurnCompleted)][-1]
    assert turn_end.outcome is AgentTurnOutcome.CANCELLED
    assert turn_end.message is aborted


def test_failed_turn_keeps_its_streamed_text_and_error() -> None:
    order: list[str] = []
    loop, _provider, _events, _usage = _make_loop(
        order,
        [ProviderTurnOutcome(result=_provider_result(status=HarnessStatus.FAILED))],
        emit_delta=True,
    )

    outcome = loop.run(_run_input())

    failed = outcome.final_history[-1]
    assert isinstance(failed, AgentAssistantMessage)
    assert failed.content.value == "delta"
    assert failed.stop_reason is AgentStopReason.ERROR
    assert failed.error_message == "provider failed"


class _RetryingTurn:
    """Stream a failed attempt, schedule a retry, stream again, then cancel."""

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome:
        del snapshot
        event_sink.emit(AssistantTextDelta(turn_index, ProductContent("stale ")))
        event_sink.emit(
            RetryScheduled(
                1, 3, 0, AgentFailure("overloaded", ProductContent("overloaded"))
            )
        )
        event_sink.emit(AssistantTextDelta(turn_index, ProductContent("fresh")))
        return ProviderTurnOutcome(
            cancellation_reason=AgentCancellationReason.OPERATOR_ABORT
        )


def test_a_retry_discards_the_failed_attempts_partial_text() -> None:
    order: list[str] = []
    loop, _provider, _events, _usage = _make_loop(order, [])
    loop._provider_turn = _RetryingTurn()  # type: ignore[assignment]

    outcome = loop.run(_run_input())

    aborted = outcome.final_history[-1]
    assert isinstance(aborted, AgentAssistantMessage)
    assert aborted.content.value == "fresh"


# -- provider requests: one shared filter, every adapter family ----------------


def _request(messages: Sequence[Any]) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="next",
        provider_name="fake",
        model_id="fake-model",
        cwd=Path("/headless-fixture"),
        messages=tuple(messages),
    )


def _stopped_history() -> tuple[Any, ...]:
    return (
        AgentUserMessage(ProductContent("first")),
        _aborted(),
        AgentUserMessage(ProductContent("second")),
        _failed(),
        AgentUserMessage(ProductContent("next")),
    )


def test_materialized_request_drops_stopped_turns() -> None:
    snapshot = snapshot_provider_request(_request(_stopped_history()))

    request = materialize_provider_request(snapshot)

    assert [m.content.value for m in request.messages] == ["first", "second", "next"]
    # The snapshot (what extension hooks and the budget see) keeps them.
    assert len(snapshot.request.messages) == 5


def _wire_payloads(request: ProviderRequest) -> dict[str, object]:
    return {
        "chat-completions": chat_transcript(request).messages,
        "chat-envelopes": [envelope_to_chat_message(m) for m in request.messages],
        "anthropic": messages_payload(request, parse_error_class=ProviderHTTPError),
        "anthropic-coalesced": messages_payload(
            request, parse_error_class=ProviderHTTPError, coalesce_tool_results=True
        ),
        "openai-responses": responses_input(
            request, parse_error_class=ProviderHTTPError
        ),
        "openai-codex": _responses_input_messages(request),
        "google": gemini_contents(request, parse_error_class=ProviderHTTPError),
    }


def test_every_adapter_family_sends_no_stopped_turn_text() -> None:
    raw = _request(_stopped_history())
    materialized = materialize_provider_request(snapshot_provider_request(raw))

    for family, payload in _wire_payloads(materialized).items():
        text = repr(payload)
        assert PARTIAL not in text, family
        assert FAILED_PARTIAL not in text, family
        assert "second" in text, family
    # Without the shared filter the wire would replay them as assistant text.
    assert PARTIAL in repr(_wire_payloads(raw)["chat-completions"])


def test_summary_request_drops_stopped_turns() -> None:
    binding = CodingProviderBinding(_StreamThenCancelProvider(), "fake", "fake-model")

    request = build_summary_request(
        binding=binding,
        cwd=Path("/headless-fixture"),
        messages=_stopped_history(),
        instruction="summarize",
        user_prompt="now",
        header_callback=None,
    )

    assert PARTIAL not in repr(request.messages)
    assert FAILED_PARTIAL not in repr(request.messages)
    assert len(request.messages) == 3


# -- persistence, JSON, previews ----------------------------------------------


def test_session_tree_round_trips_the_stop_reason(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path)
    tree.append_message(AgentUserMessage(ProductContent("hi")))
    tree.append_message(_aborted())
    tree.append_message(AgentUserMessage(ProductContent("again")))
    tree.append_message(_failed())
    tree.append_message(AgentAssistantMessage(ProductContent("done")))
    assert tree.path is not None

    raw = tree.path.read_text()
    assert raw.count('"stop_reason"') == 2
    assert '"error_message": "provider exploded"' in raw

    reopened = NativeSessionTree.open(tree.path)
    messages = [
        entry.message
        for entry in reopened.get_entries()
        if isinstance(entry, MessageEntry)
    ]
    assert messages[1] == _aborted()
    assert messages[3] == _failed()
    assert messages[4] == AgentAssistantMessage(ProductContent("done"))


def test_serialized_assistant_carries_pi_stop_reason() -> None:
    call = AgentToolCall("c1", "read", ProductContent("{}"))
    assert serialize_message(_aborted())["stopReason"] == "aborted"
    assert "errorMessage" not in serialize_message(_aborted())
    failed = serialize_message(_failed())
    assert failed["stopReason"] == "error"
    assert failed["errorMessage"] == "provider exploded"
    assert serialize_message(AgentAssistantMessage(ProductContent("a")))[
        "stopReason"
    ] == ("stop")
    assert (
        serialize_message(AgentAssistantMessage(ProductContent(""), (call,)))[
            "stopReason"
        ]
        == "toolUse"
    )


def test_extension_view_treats_a_stopped_turn_as_incomplete() -> None:
    view = _ConversationView((_aborted("some text"),)).last_assistant_message()
    assert view is not None
    assert view.complete is False


def test_tree_preview_marks_stopped_turns_without_text(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(AgentUserMessage(ProductContent("hi")))
    aborted = tree.append_message(_aborted(""))
    failed = tree.append_message(_failed(""))
    partial = tree.append_message(_aborted("half"))

    labels = [entry_preview(tree, entry) for entry in (aborted, failed, partial)]
    assert labels[0].endswith("assistant: (aborted)")
    assert labels[1].endswith("assistant: provider exploded")
    assert labels[2].endswith("assistant: half")


# -- end to end through the coding session -------------------------------------


@dataclass(slots=True)
class _StreamThenCancelProvider:
    """Stream a partial answer and cancel, then answer the next prompt."""

    supports_tool_calls: bool = True
    name: str = "f6-fixture"
    model_id: str = "fixture-model"
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del reasoning_sink, cancel_token
        self.requests.append(request)
        if len(self.requests) == 1:
            assert stream_sink is not None
            stream_sink(PARTIAL)
            raise ProviderCancelledError("fixture cancellation")
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="second answer",
        )


class _Automation:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_session_persists_the_abort_and_the_next_request_skips_it(
    tmp_path: Path,
) -> None:
    provider = _StreamThenCancelProvider()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    automation = _Automation()

    CodingSession(
        provider=provider,
        native_session=tree,
        automation_observer=automation,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\nsecond\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    stored = [
        entry.message
        for entry in tree.get_entries()
        if isinstance(entry, MessageEntry)
        and not type(entry.message).__name__ == "AgentSystemMessage"
    ]
    assert [type(m).__name__ for m in stored] == [
        "AgentUserMessage",
        "AgentAssistantMessage",
        "AgentUserMessage",
        "AgentAssistantMessage",
    ]
    assert stored[1] == _aborted()
    assert len(provider.requests) == 2
    second = provider.requests[1]
    assert [m.content.value for m in second.messages] == ["first", "second"]
    assert all(not isinstance(m, AgentToolResultMessage) for m in second.messages)

    ends = [
        e
        for e in automation.events
        if e["type"] == "message_end" and e["message"]["role"] == "assistant"
    ]
    assert ends[0]["message"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": PARTIAL}],
        "provider": "f6-fixture",
        "model": "fixture-model",
        # An aborted turn has no usage yet (USAGE1).
        "usage": ZERO_USAGE,
        "stopReason": "aborted",
    }
    agent_end = next(e for e in automation.events if e["type"] == "agent_end")
    assert agent_end["messages"][-1]["stopReason"] == "aborted"


def test_fake_streamblock_streams_a_partial_before_waiting() -> None:
    from pipy_harness.native.fake import AutomationFakeProvider

    provider = AutomationFakeProvider(block_timeout_seconds=5.0)
    chunks: list[str] = []
    token = CancelToken()

    def _sink(chunk: str) -> None:
        chunks.append(chunk)
        token.cancel()

    with pytest.raises(ProviderCancelledError):
        provider.complete(
            _request([AgentUserMessage(ProductContent("STREAMBLOCK poem"))]),
            stream_sink=_sink,
            cancel_token=token,
        )
    assert chunks == ["PARTIAL:streamed-before-abort"]
    assert provider.cancel_observed
