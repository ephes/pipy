"""Direct contracts for the identity-safe active-input overlay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.history import compact_agent_history


def _user(text: str) -> AgentUserMessage:
    return AgentUserMessage(ProductContent(text))


def _assistant(text: str) -> AgentAssistantMessage:
    return AgentAssistantMessage(ProductContent(text))


@dataclass(frozen=True, slots=True)
class _UserMessageLookalike:
    """Immutable structural impostor that is not a canonical agent message."""

    content: ProductContent
    role: str = "user"


def test_overlay_anchors_by_identity_after_equal_content_history_is_compacted() -> None:
    older_equal = _user("same")
    accepted = _user("same")
    transient_a = _user("TRANSIENT-A")
    transient_b = _user("TRANSIENT-B")
    history = (
        _user("old"),
        _assistant("old answer"),
        older_equal,
        _assistant("prior same answer"),
        accepted,
    )

    compacted = compact_agent_history(history, keep_recent_groups=2)
    active_input = AgentActiveInput(accepted, (transient_a, transient_b))

    assert compacted.changed is True
    assert compacted.messages == history[2:]
    request_messages = active_input.request_messages(compacted.messages)
    assert request_messages == (
        older_equal,
        history[3],
        accepted,
        transient_a,
        transient_b,
    )
    assert request_messages[0] is older_equal
    assert request_messages[2] is accepted
    assert active_input.result_messages(compacted.messages) == (accepted,)


def test_overlay_is_stable_across_later_tool_loop_messages_but_result_is_durable() -> (
    None
):
    accepted = _user("active")
    transient = _user("TRANSIENT")
    assistant = _assistant("calling")
    result = AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="echo",
        content=ProductContent("result"),
        provider_correlation_id="provider-call-1",
    )
    settled = _assistant("settled")
    history = (_user("prior"), _assistant("prior answer"), accepted)
    active_input = AgentActiveInput(accepted, (transient,))

    assert active_input.request_messages(history) == (*history, transient)
    later_history = (*history, assistant, result, settled)
    assert active_input.request_messages(later_history) == (
        *history,
        transient,
        assistant,
        result,
        settled,
    )
    assert active_input.result_messages(later_history) == (
        accepted,
        assistant,
        result,
        settled,
    )


def test_transformed_request_rewrites_only_accepted_identity_among_equal_messages() -> (
    None
):
    older_equal = _user("same")
    accepted = _user("same")
    overlay_equal = _user("same")
    later = _assistant("later")
    active_input = AgentActiveInput(accepted, (overlay_equal,))
    durable_history = (older_equal, _assistant("prior"), accepted, later)
    request_messages = active_input.request_messages(durable_history)
    caller_snapshot = tuple(request_messages)

    transformed = active_input.transformed_request_messages(
        request_messages,
        "transformed",
    )

    assert transformed != request_messages
    assert transformed[0] is older_equal
    assert transformed[1] is durable_history[1]
    assert isinstance(transformed[2], AgentUserMessage)
    assert transformed[2] is not accepted
    assert transformed[2].content.value == "transformed"
    assert transformed[3] is overlay_equal
    assert transformed[4] is later
    assert [message.content.value for message in transformed] == [
        "same",
        "prior",
        "transformed",
        "same",
        "later",
    ]
    assert request_messages == caller_snapshot
    assert request_messages[0] is older_equal
    assert request_messages[2] is accepted
    assert request_messages[3] is overlay_equal
    assert accepted.content.value == "same"


def test_transformed_request_preserves_exact_view_when_prompt_is_unchanged() -> None:
    accepted = _user("same")
    overlay_equal = _user("same")
    active_input = AgentActiveInput(accepted, (overlay_equal,))
    request_messages = active_input.request_messages((_user("same"), accepted))

    transformed = active_input.transformed_request_messages(request_messages, "same")

    assert transformed == request_messages
    assert all(
        transformed_message is request_message
        for transformed_message, request_message in zip(
            transformed, request_messages, strict=True
        )
    )


@pytest.mark.parametrize("history", [(), (_user("other"),)])
def test_overlay_fails_closed_when_identity_anchor_is_absent(history) -> None:
    active_input = AgentActiveInput(_user("same"), (_user("TRANSIENT"),))

    with pytest.raises(ValueError, match="exactly once"):
        active_input.request_messages(history)
    with pytest.raises(ValueError, match="exactly once"):
        active_input.result_messages(history)
    with pytest.raises(ValueError, match="exactly once"):
        active_input.transformed_request_messages(history, "transformed")


def test_overlay_fails_closed_when_same_anchor_object_occurs_twice() -> None:
    accepted = _user("same")
    active_input = AgentActiveInput(accepted, (_user("TRANSIENT"),))

    with pytest.raises(ValueError, match="exactly once"):
        active_input.request_messages((accepted, accepted))
    with pytest.raises(ValueError, match="exactly once"):
        active_input.transformed_request_messages(
            (accepted, accepted),
            "transformed",
        )


def test_overlay_fails_closed_when_history_is_outside_canonical_union() -> None:
    accepted = _user("same")
    active_input = AgentActiveInput(accepted)
    lookalike = _UserMessageLookalike(ProductContent("same"))
    invalid_history = cast(tuple[AgentMessage, ...], (accepted, lookalike))

    with pytest.raises(TypeError, match="only AgentMessage"):
        active_input.request_messages(invalid_history)
