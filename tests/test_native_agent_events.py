"""Contracts for the mode-neutral native agent event seam."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, Callable, cast, get_args, get_type_hints

import pytest

from pipy_harness.native import agent as agent_package
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEvent,
    AgentEventSink,
    AgentFailure,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentRunResult,
    AgentRunStarted,
    AgentToolCall,
    AgentToolResultMessage,
    AgentTurnOutcome,
    AgentUsage,
    AgentUserMessage,
    AssistantReasoningDelta,
    AssistantTextDelta,
    FollowUpConsumed,
    MessageCompleted,
    MessageStarted,
    ProductContent,
    ProviderFailed,
    RetryCompleted,
    RetryScheduled,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    ToolCallStarted,
    ToolCallUpdated,
    TurnCompleted,
    TurnStarted,
    UsageUpdated,
)

EXPECTED_PUBLIC_EXPORTS = [
    "AGENT_TOOL_REQUEST_ID_PREFIX",
    "AgentAssistantMessage",
    "AgentCancellationReason",
    "AgentEvent",
    "AgentEventSink",
    "AgentFailure",
    "AgentMessage",
    "AgentRunCompleted",
    "AgentRunOutcome",
    "AgentRunResult",
    "AgentRunStarted",
    "AgentToolCall",
    "AgentToolResultMessage",
    "AgentTurnOutcome",
    "AgentUsage",
    "AgentUserMessage",
    "AssistantReasoningDelta",
    "AssistantTextDelta",
    "FollowUpConsumed",
    "MessageCompleted",
    "MessageStarted",
    "ProductContent",
    "ProviderFailed",
    "RetryCompleted",
    "RetryScheduled",
    "RunCancelled",
    "SteeringConsumed",
    "ToolCallCompleted",
    "ToolCallStarted",
    "ToolCallUpdated",
    "TurnCompleted",
    "TurnStarted",
    "UsageUpdated",
]


def _event_examples() -> tuple[AgentEvent, ...]:
    prompt = ProductContent("private prompt")
    delta = ProductContent("assistant delta")
    call = AgentToolCall("provider-call-1", "read", ProductContent('{"path":"x"}'))
    assistant = AgentAssistantMessage(delta, (call,))
    tool_result = AgentToolResultMessage(
        "pipy-tool-1",
        "read",
        ProductContent("private tool output"),
        provider_correlation_id="provider-call-1",
        added_tool_names=("write",),
    )
    usage = AgentUsage(input_tokens=4, output_tokens=2, reasoning_tokens=1)
    failure = AgentFailure("ProviderUnavailable", ProductContent("private error"), True)
    run_result = AgentRunResult(
        AgentRunOutcome.SUCCEEDED,
        (AgentUserMessage(prompt), assistant, tool_result),
        usage,
    )
    return (
        AgentRunStarted(),
        TurnStarted(0),
        MessageStarted(0, AgentAssistantMessage(ProductContent(""))),
        AssistantTextDelta(0, delta),
        AssistantReasoningDelta(0, ProductContent("private reasoning")),
        MessageCompleted(0, assistant),
        ToolCallStarted(0, call),
        ToolCallUpdated(0, call, ProductContent("partial output")),
        ToolCallCompleted(0, tool_result, 0.25),
        UsageUpdated(usage, 7),
        RetryScheduled(1, 3, 250, failure),
        RetryCompleted(1, False, failure),
        SteeringConsumed(ProductContent("steer now")),
        FollowUpConsumed(ProductContent("then continue")),
        ProviderFailed(failure, True),
        RunCancelled(
            AgentCancellationReason.OPERATOR_ABORT,
            ProductContent("operator abort"),
        ),
        TurnCompleted(0, AgentTurnOutcome.SUCCEEDED, assistant, (tool_result,)),
        AgentRunCompleted(run_result),
    )


def test_agent_event_union_covers_the_phase_one_vocabulary() -> None:
    events = _event_examples()

    assert {type(event) for event in events} == set(get_args(AgentEvent))
    assert len(events) == 18


def test_native_agent_public_exports_are_exact() -> None:
    assert agent_package.__all__ == EXPECTED_PUBLIC_EXPORTS
    assert len(agent_package.__all__) == len(set(agent_package.__all__))


def test_message_lifecycle_covers_non_streamed_and_streamed_messages() -> None:
    user = AgentUserMessage(ProductContent("private prompt"))
    empty_assistant = AgentAssistantMessage(ProductContent(""))

    assert MessageStarted(0, user).message is user
    assert MessageCompleted(0, user).message is user
    assert MessageStarted(0, empty_assistant).message is empty_assistant


def test_tool_identity_and_added_tool_history_remain_distinct() -> None:
    call = AgentToolCall("provider-call", "read", ProductContent("{}"))
    result = AgentToolResultMessage(
        "pipy-tool-1",
        "read",
        ProductContent("result"),
        provider_correlation_id=call.provider_correlation_id,
        added_tool_names=("write", "bash"),
    )

    assert call.provider_correlation_id == "provider-call"
    assert result.tool_request_id == "pipy-tool-1"
    assert result.provider_correlation_id == "provider-call"
    assert result.added_tool_names == ("write", "bash")


def test_usage_event_is_explicitly_cumulative_and_preserves_last_turn_total() -> None:
    cumulative = AgentUsage(input_tokens=10, output_tokens=4)
    event = UsageUpdated(cumulative, last_turn_total_tokens=6)

    assert event.cumulative_usage is cumulative
    assert event.last_turn_total_tokens == 6


def test_run_and_turn_outcomes_are_closed_to_current_terminal_states() -> None:
    assert set(AgentRunOutcome) == {
        AgentRunOutcome.SUCCEEDED,
        AgentRunOutcome.FAILED,
        AgentRunOutcome.CANCELLED,
    }
    assert set(AgentTurnOutcome) == {
        AgentTurnOutcome.SUCCEEDED,
        AgentTurnOutcome.FAILED,
        AgentTurnOutcome.CANCELLED,
    }


def test_agent_event_sink_is_synchronous_and_preserves_order() -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[AgentEvent] = []

        def emit(self, event: AgentEvent) -> None:
            self.events.append(event)

    sink = RecordingSink()
    events = _event_examples()

    assert isinstance(sink, AgentEventSink)
    for event in events:
        sink.emit(event)
        assert sink.events[-1] is event
    assert sink.events == list(events)


def test_agent_event_sink_failure_propagates_to_the_producer() -> None:
    class SinkFailure(RuntimeError):
        pass

    class FailingSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            raise SinkFailure("adapter failed")

    sink = FailingSink()

    assert isinstance(sink, AgentEventSink)
    with pytest.raises(SinkFailure, match="adapter failed"):
        sink.emit(AgentRunStarted())


def test_event_graph_is_immutable_and_has_no_archive_serializer() -> None:
    event = AssistantTextDelta(0, ProductContent("private model output"))

    with pytest.raises(FrozenInstanceError):
        event.turn_index = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        event.delta.value = "changed"  # type: ignore[misc]
    assert not hasattr(event, "to_dict")
    assert not hasattr(event, "asdict")


def test_product_content_is_explicit_and_archive_dtos_are_absent() -> None:
    event = AssistantTextDelta(0, ProductContent("archive-prohibited sentinel"))
    result = AgentRunResult(
        AgentRunOutcome.SUCCEEDED,
        (AgentUserMessage(ProductContent("private prompt sentinel")),),
    )

    assert get_type_hints(AssistantTextDelta)["delta"] is ProductContent
    assert event.delta.value == "archive-prohibited sentinel"
    assert "archive-prohibited sentinel" not in repr(event)
    assert "private prompt sentinel" not in repr(result)
    assert not any(
        "Summary" in name or "Archive" in name for name in agent_package.__all__
    )


def test_agent_run_result_enforces_terminal_failure_shape() -> None:
    failure = AgentFailure("ProviderError", ProductContent("private failure"))

    with pytest.raises(ValueError, match="requires failure details"):
        AgentRunResult(AgentRunOutcome.FAILED, ())
    with pytest.raises(ValueError, match="only failed"):
        AgentRunResult(AgentRunOutcome.CANCELLED, (), failure=failure)
    assert (
        AgentRunResult(AgentRunOutcome.FAILED, (), failure=failure).failure is failure
    )
    with pytest.raises(ValueError, match="only failed.*will_retry"):
        AgentRunResult(AgentRunOutcome.SUCCEEDED, (), will_retry=True)
    assert AgentRunResult(
        AgentRunOutcome.FAILED, (), failure=failure, will_retry=True
    ).will_retry


def test_cancelled_run_result_is_self_contained() -> None:
    detail = ProductContent("operator pressed escape")

    with pytest.raises(ValueError, match="requires a cancellation reason"):
        AgentRunResult(AgentRunOutcome.CANCELLED, ())
    with pytest.raises(ValueError, match="only cancelled"):
        AgentRunResult(
            AgentRunOutcome.SUCCEEDED,
            (),
            cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
        )

    result = AgentRunResult(
        AgentRunOutcome.CANCELLED,
        (),
        cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
        cancellation_detail=detail,
    )
    assert result.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert result.cancellation_detail is detail
    assert "operator pressed escape" not in repr(result)


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: AgentAssistantMessage(ProductContent("x"), cast(Any, [])),
        lambda: AgentAssistantMessage(ProductContent("x"), cast(Any, {})),
        lambda: AgentToolResultMessage(
            "call",
            "read",
            ProductContent("x"),
            "provider-call",
            added_tool_names=cast(Any, []),
        ),
        lambda: AgentToolResultMessage(
            "call",
            "read",
            ProductContent("x"),
            "provider-call",
            added_tool_names=cast(Any, {}),
        ),
        lambda: AgentRunResult(AgentRunOutcome.SUCCEEDED, cast(Any, [])),
        lambda: AgentRunResult(AgentRunOutcome.SUCCEEDED, cast(Any, {})),
        lambda: TurnCompleted(
            0,
            AgentTurnOutcome.SUCCEEDED,
            AgentAssistantMessage(ProductContent("x")),
            cast(Any, []),
        ),
        lambda: TurnCompleted(
            0,
            AgentTurnOutcome.SUCCEEDED,
            AgentAssistantMessage(ProductContent("x")),
            cast(Any, {}),
        ),
    ],
)
def test_tuple_collections_reject_mutable_substitutions(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(TypeError):
        constructor()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: AgentAssistantMessage(
            ProductContent("x"), cast(Any, (ProductContent("not a call"),))
        ),
        lambda: AgentRunResult(
            AgentRunOutcome.SUCCEEDED, cast(Any, (ProductContent("not a message"),))
        ),
        lambda: TurnCompleted(
            0,
            AgentTurnOutcome.SUCCEEDED,
            AgentAssistantMessage(ProductContent("x")),
            cast(Any, (AgentAssistantMessage(ProductContent("not a result")),)),
        ),
    ],
)
def test_tuple_collections_reject_invalid_elements(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(TypeError):
        constructor()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: ProductContent(cast(Any, 1)),
        lambda: AgentUserMessage(cast(Any, "prompt")),
        lambda: AgentAssistantMessage(cast(Any, "assistant")),
        lambda: AgentToolCall("call", "read", cast(Any, "{}")),
        lambda: AgentToolResultMessage(
            "call", "read", cast(Any, "result"), "provider-call"
        ),
        lambda: AgentRunResult(cast(Any, "succeeded"), ()),
        lambda: AgentRunResult(
            AgentRunOutcome.SUCCEEDED, (), usage=cast(Any, {"input_tokens": 1})
        ),
        lambda: AgentRunCompleted(cast(Any, {"outcome": "succeeded"})),
        lambda: MessageStarted(0, cast(Any, None)),
        lambda: ToolCallUpdated(0, cast(Any, "call"), ProductContent("partial")),
        lambda: TurnCompleted(
            0,
            cast(Any, "succeeded"),
            AgentAssistantMessage(ProductContent("assistant")),
        ),
        lambda: RunCancelled(cast(Any, "operator_abort")),
    ],
)
def test_content_message_and_result_shapes_reject_invalid_runtime_types(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(TypeError):
        constructor()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: AgentToolCall("", "read", ProductContent("{}")),
        lambda: AgentToolCall("call", "", ProductContent("{}")),
        lambda: AgentToolResultMessage(
            "", "read", ProductContent("result"), "provider-call"
        ),
        lambda: AgentToolResultMessage(
            "call", "", ProductContent("result"), "provider-call"
        ),
        lambda: AgentToolResultMessage("call", "read", ProductContent("result"), ""),
        lambda: AgentToolResultMessage(
            "call",
            "read",
            ProductContent("result"),
            "provider-call",
            added_tool_names=("",),
        ),
        lambda: AgentFailure("", ProductContent("error")),
    ],
)
def test_identifiers_names_and_error_types_must_not_be_empty(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        constructor()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: AgentToolResultMessage(
            "call",
            "read",
            ProductContent("result"),
            "provider-call",
            is_error=cast(Any, 1),
        ),
        lambda: AgentFailure("Error", ProductContent("error"), cast(Any, 1)),
        lambda: AgentRunResult(AgentRunOutcome.SUCCEEDED, (), will_retry=cast(Any, 1)),
        lambda: RetryCompleted(1, cast(Any, 1)),
        lambda: ProviderFailed(
            AgentFailure("Error", ProductContent("error")), cast(Any, 1)
        ),
    ],
)
def test_boolean_fields_reject_non_boolean_values(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(TypeError):
        constructor()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: TurnStarted(-1),
        lambda: MessageStarted(-1, AgentAssistantMessage(ProductContent(""))),
        lambda: AssistantTextDelta(-1, ProductContent("text")),
        lambda: AssistantReasoningDelta(-1, ProductContent("reasoning")),
        lambda: MessageCompleted(
            -1, AgentAssistantMessage(ProductContent("assistant"))
        ),
        lambda: ToolCallStarted(
            -1, AgentToolCall("call", "read", ProductContent("{}"))
        ),
        lambda: ToolCallUpdated(
            -1,
            AgentToolCall("call", "read", ProductContent("{}")),
            ProductContent("partial"),
        ),
        lambda: ToolCallCompleted(
            -1,
            AgentToolResultMessage(
                "call", "read", ProductContent("result"), "provider-call"
            ),
        ),
        lambda: TurnCompleted(
            -1,
            AgentTurnOutcome.SUCCEEDED,
            AgentAssistantMessage(ProductContent("assistant")),
        ),
        lambda: RetryScheduled(
            -1,
            3,
            0,
            AgentFailure("Error", ProductContent("error")),
        ),
        lambda: RetryScheduled(
            1,
            -1,
            0,
            AgentFailure("Error", ProductContent("error")),
        ),
        lambda: RetryScheduled(
            1,
            3,
            -1,
            AgentFailure("Error", ProductContent("error")),
        ),
        lambda: RetryCompleted(-1, True),
    ],
)
def test_turn_retry_and_delay_values_must_not_be_negative(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        constructor()


def test_retry_completed_requires_a_consistent_failure_shape() -> None:
    failure = AgentFailure("ProviderError", ProductContent("error"), True)

    with pytest.raises(ValueError, match="cannot carry failure"):
        RetryCompleted(1, True, failure)
    with pytest.raises(ValueError, match="requires failure"):
        RetryCompleted(1, False)
    assert RetryCompleted(1, True).failure is None
    assert RetryCompleted(1, False, failure).failure is failure


@pytest.mark.parametrize(
    "kwargs, error_type",
    [
        ({"input_tokens": -1}, ValueError),
        ({"input_tokens": True}, TypeError),
        ({"cost_usd": -0.01}, ValueError),
        ({"cost_usd": float("inf")}, ValueError),
        ({"cost_usd": float("nan")}, ValueError),
    ],
)
def test_agent_usage_rejects_invalid_totals(
    kwargs: dict[str, object], error_type: type[Exception]
) -> None:
    with pytest.raises(error_type):
        AgentUsage(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("duration", [-0.1, float("inf"), float("nan")])
def test_tool_duration_must_be_finite_and_nonnegative(duration: float) -> None:
    result = AgentToolResultMessage(
        "pipy-tool-call", "read", ProductContent("result"), "provider-call"
    )

    with pytest.raises(ValueError):
        ToolCallCompleted(0, result, duration)


def test_cancellation_reason_is_closed_with_optional_product_detail() -> None:
    detail = ProductContent("operator pressed escape")
    event = RunCancelled(AgentCancellationReason.OPERATOR_ABORT, detail)

    assert event.reason is AgentCancellationReason.OPERATOR_ABORT
    assert event.detail is detail
    assert set(AgentCancellationReason) == {
        AgentCancellationReason.OPERATOR_ABORT,
        AgentCancellationReason.STEERING,
        AgentCancellationReason.LOCAL_COMMAND,
        AgentCancellationReason.PROVIDER_CANCELLED,
    }
