"""Direct contracts for canonical agent runtime effects and ports."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
    AgentRunEffect,
    AgentRunEffectSink,
    AgentUsagePublication,
    AgentUsagePublisher,
    AppendAgentMessage,
)
from pipy_harness.native.agent.usage import AgentProviderUsageSample


def _message(value: str = "hello") -> AgentUserMessage:
    return AgentUserMessage(ProductContent(value))


def _publication() -> AgentUsagePublication:
    sample = AgentProviderUsageSample(input_tokens=3, output_tokens=2)
    return AgentUsagePublication(
        sample=sample,
        cumulative_usage=AgentUsage(input_tokens=3, output_tokens=2),
        context_tokens=5,
    )


def test_run_effect_is_a_closed_frozen_slotted_message_append() -> None:
    effect = AppendAgentMessage(_message())

    assert AgentRunEffect is AppendAgentMessage
    assert not hasattr(effect, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(effect, "message", _message("changed"))
    with pytest.raises(TypeError, match="must be an AgentMessage"):
        AppendAgentMessage(cast(AgentUserMessage, object()))


def test_usage_publication_is_frozen_slotted_and_matches_effective_total() -> None:
    publication = _publication()

    assert not hasattr(publication, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(publication, "context_tokens", 4)
    with pytest.raises(TypeError, match="sample must be"):
        AgentUsagePublication(cast(AgentProviderUsageSample, object()), AgentUsage(), 0)
    with pytest.raises(TypeError, match="cumulative_usage must be"):
        AgentUsagePublication(AgentProviderUsageSample(), cast(AgentUsage, object()), 0)
    with pytest.raises(TypeError, match="context_tokens must be an integer"):
        AgentUsagePublication(AgentProviderUsageSample(), AgentUsage(), cast(int, True))
    with pytest.raises(ValueError, match="must match.*effective total"):
        AgentUsagePublication(
            AgentProviderUsageSample(input_tokens=3), AgentUsage(input_tokens=3), 2
        )


def test_usage_publication_accepts_explicit_total_over_counter_fallback() -> None:
    sample = AgentProviderUsageSample(input_tokens=3, output_tokens=2, total_tokens=9)

    assert AgentUsagePublication(sample, AgentUsage(input_tokens=3), 9).sample is sample


@pytest.mark.parametrize("kind", list(AgentQueuedInputKind))
def test_queued_input_is_pre_acceptance_content_and_closed_kind(
    kind: AgentQueuedInputKind,
) -> None:
    queued_input = AgentQueuedInput(ProductContent("next"), kind)

    assert queued_input.content == ProductContent("next")
    assert not hasattr(queued_input, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(queued_input, "kind", AgentQueuedInputKind.STEERING)
    with pytest.raises(TypeError, match="content must be ProductContent"):
        AgentQueuedInput(cast(ProductContent, "next"), kind)
    with pytest.raises(TypeError, match="kind must be AgentQueuedInputKind"):
        AgentQueuedInput(ProductContent("next"), cast(AgentQueuedInputKind, "other"))


def test_runtime_ports_are_structural_and_expose_only_narrow_operations() -> None:
    class EffectSink:
        def emit(self, effect: AgentRunEffect) -> None:
            del effect

    class UsagePublisher:
        def publish(self, publication: AgentUsagePublication) -> None:
            del publication

    class QueuedInputPort:
        def take_next(self) -> AgentQueuedInput | None:
            return None

    effect_sink = EffectSink()
    usage_publisher = UsagePublisher()
    queued_input_port = QueuedInputPort()

    assert isinstance(effect_sink, AgentRunEffectSink)
    assert isinstance(usage_publisher, AgentUsagePublisher)
    assert isinstance(queued_input_port, AgentQueuedInputPort)
    assert not isinstance(object(), AgentRunEffectSink)
    assert not isinstance(object(), AgentUsagePublisher)
    assert not isinstance(object(), AgentQueuedInputPort)
    for forbidden in ("append", "reserve", "settle", "mark_idle", "peek", "put"):
        assert not hasattr(queued_input_port, forbidden)
