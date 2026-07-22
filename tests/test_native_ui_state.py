"""Pure, PTY-free contracts for the assistant-lifecycle UI reducer.

These tests pin every rendering decision produced by
:func:`pipy_harness.native.ui.state.reduce` and the exact :class:`UiState`
transitions, independent of any renderer or terminal.
"""

from __future__ import annotations

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEvent,
    AgentFailure,
    AgentRunStarted,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    AssistantReasoningDelta,
    AssistantTextDelta,
    MessageCompleted,
    MessageStarted,
    ProductContent,
    ProviderFailed,
    RunCancelled,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
    TurnStarted,
)
from pipy_harness.native.ui.state import (
    CancelAssistantMessage,
    CompleteAssistantMessage,
    FailAssistantMessage,
    RenderBufferedAssistantText,
    StartAssistantMessage,
    StreamAssistantReasoning,
    StreamAssistantText,
    UiState,
    reduce,
)


def _assistant(text: str, *calls: AgentToolCall) -> AgentAssistantMessage:
    return AgentAssistantMessage(ProductContent(text), tuple(calls))


def test_message_started_starts_assistant_and_resets_flags() -> None:
    dirty = UiState(
        assistant_active=True,
        assistant_streamed=True,
        assistant_completion_suppressed=True,
    )

    state, decisions = reduce(dirty, MessageStarted(0, _assistant("")))

    assert state == UiState(assistant_active=True)
    assert decisions == (StartAssistantMessage(),)


def test_non_assistant_message_started_is_ignored() -> None:
    start = UiState()

    state, decisions = reduce(
        start, MessageStarted(0, AgentUserMessage(ProductContent("hi")))
    )

    assert state is start
    assert decisions == ()


def test_text_delta_streams_and_records_non_empty_accumulation() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True), AssistantTextDelta(0, ProductContent("hello"))
    )

    assert state == UiState(assistant_active=True, assistant_streamed=True)
    assert decisions == (StreamAssistantText("hello"),)


def test_empty_text_delta_streams_but_does_not_mark_streamed() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True), AssistantTextDelta(0, ProductContent(""))
    )

    assert state == UiState(assistant_active=True, assistant_streamed=False)
    assert decisions == (StreamAssistantText(""),)


def test_text_delta_keeps_streamed_true_once_set() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True, assistant_streamed=True),
        AssistantTextDelta(0, ProductContent("")),
    )

    assert state.assistant_streamed is True
    assert decisions == (StreamAssistantText(""),)


def test_reasoning_delta_is_a_stateless_passthrough() -> None:
    start = UiState(assistant_active=True)

    state, decisions = reduce(
        start, AssistantReasoningDelta(0, ProductContent("thinking"))
    )

    assert state == start
    assert decisions == (StreamAssistantReasoning("thinking"),)


def test_provider_failed_while_active_fails_and_suppresses() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True),
        ProviderFailed(AgentFailure("boom", ProductContent("no")), will_retry=False),
    )

    assert state == UiState(
        assistant_active=True, assistant_completion_suppressed=True
    )
    assert decisions == (FailAssistantMessage(),)


def test_provider_failed_while_inactive_only_suppresses() -> None:
    state, decisions = reduce(
        UiState(),
        ProviderFailed(AgentFailure("boom", ProductContent("no")), will_retry=True),
    )

    assert state == UiState(assistant_completion_suppressed=True)
    assert decisions == ()


@pytest.mark.parametrize("reason", tuple(AgentCancellationReason))
def test_run_cancelled_while_active_cancels_with_each_reason(
    reason: AgentCancellationReason,
) -> None:
    state, decisions = reduce(UiState(assistant_active=True), RunCancelled(reason))

    assert state == UiState(
        assistant_active=True, assistant_completion_suppressed=True
    )
    assert decisions == (CancelAssistantMessage(reason),)


@pytest.mark.parametrize("reason", tuple(AgentCancellationReason))
def test_run_cancelled_while_inactive_only_suppresses(
    reason: AgentCancellationReason,
) -> None:
    state, decisions = reduce(UiState(), RunCancelled(reason))

    assert state == UiState(assistant_completion_suppressed=True)
    assert decisions == ()


def test_completion_renders_buffered_body_when_not_streamed() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True), MessageCompleted(0, _assistant("buffered"))
    )

    assert state == UiState()
    assert decisions == (
        RenderBufferedAssistantText("buffered", has_tool_calls=False),
        CompleteAssistantMessage(has_tool_calls=False),
    )


def test_completion_skips_buffered_body_when_already_streamed() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True, assistant_streamed=True),
        MessageCompleted(0, _assistant("streamed")),
    )

    assert state == UiState(assistant_streamed=True)
    assert decisions == (CompleteAssistantMessage(has_tool_calls=False),)


def test_completion_skips_buffered_body_for_empty_content() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True), MessageCompleted(0, _assistant(""))
    )

    assert state == UiState()
    assert decisions == (CompleteAssistantMessage(has_tool_calls=False),)


def test_completion_reports_has_tool_calls_on_buffered_and_complete() -> None:
    call = AgentToolCall("provider-call", "read", ProductContent("{}"))

    state, decisions = reduce(
        UiState(assistant_active=True),
        MessageCompleted(0, _assistant("tool preamble", call)),
    )

    assert state == UiState()
    assert decisions == (
        RenderBufferedAssistantText("tool preamble", has_tool_calls=True),
        CompleteAssistantMessage(has_tool_calls=True),
    )


def test_completion_after_suppression_is_silent_but_deactivates() -> None:
    state, decisions = reduce(
        UiState(assistant_active=True, assistant_completion_suppressed=True),
        MessageCompleted(0, _assistant("ignored")),
    )

    assert state == UiState(assistant_completion_suppressed=True)
    assert decisions == ()


def test_completion_without_active_assistant_is_ignored_completely() -> None:
    start = UiState()

    state, decisions = reduce(start, MessageCompleted(0, _assistant("late")))

    assert state is start
    assert decisions == ()


def test_second_completion_is_a_no_op_so_completion_happens_once() -> None:
    active = UiState(assistant_active=True)
    first_state, first_decisions = reduce(
        active, MessageCompleted(0, _assistant("done"))
    )
    second_state, second_decisions = reduce(
        first_state, MessageCompleted(0, _assistant("done"))
    )

    assert first_decisions == (
        RenderBufferedAssistantText("done", has_tool_calls=False),
        CompleteAssistantMessage(has_tool_calls=False),
    )
    assert second_state == first_state == UiState()
    assert second_decisions == ()


def test_non_assistant_completion_is_ignored() -> None:
    active = UiState(assistant_active=True)
    result = AgentToolResultMessage(
        "pipy-tool-000001",
        "read",
        ProductContent("out"),
        "provider-call",
        is_error=False,
    )

    state, decisions = reduce(active, MessageCompleted(0, result))

    assert state is active
    assert decisions == ()


@pytest.mark.parametrize(
    "event",
    [
        AgentRunStarted(),
        TurnStarted(0),
        ToolCallStarted(0, AgentToolCall("c", "read", ProductContent("{}"))),
        ToolCallUpdated(
            0, AgentToolCall("c", "read", ProductContent("{}")), ProductContent("p")
        ),
        ToolCallCompleted(
            0,
            AgentToolResultMessage(
                "pipy-tool-000001",
                "read",
                ProductContent("out"),
                "c",
                is_error=False,
            ),
            duration_seconds=0.1,
        ),
    ],
)
def test_unowned_events_leave_state_untouched_without_decisions(
    event: AgentEvent,
) -> None:
    start = UiState(assistant_active=True, assistant_streamed=True)

    state, decisions = reduce(start, event)

    assert state is start
    assert decisions == ()
