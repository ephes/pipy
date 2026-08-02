"""Integration contracts for product callback agent runtime adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import AgentEvent, UsageUpdated
from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
    AgentUsagePublication,
    AgentUsagePublisher,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentQueuedInputPort,
    NativeAgentUsagePublisher,
)


@dataclass(slots=True)
class _EventSink:
    trace: list[object]
    failure: Exception | None = None
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.trace.append(("emit", event))
        if self.failure is not None:
            raise self.failure
        self.events.append(event)


def _publication() -> AgentUsagePublication:
    sample = AgentProviderUsageSample(input_tokens=4, output_tokens=2)
    return AgentUsagePublication(
        sample,
        AgentUsage(input_tokens=9, output_tokens=3),
        sample.effective_total_tokens,
    )


def test_usage_publisher_absorbs_before_exact_event_and_tracks_current_target() -> None:
    trace: list[object] = []
    first = AgentUsageAccumulator()
    second = AgentUsageAccumulator()
    current = {"usage": first}

    def absorb(sample: AgentProviderUsageSample) -> None:
        trace.append(("absorb", sample))
        current["usage"].absorb(sample)

    event_sink = _EventSink(trace)
    publisher = NativeAgentUsagePublisher(absorb, event_sink)
    publication = _publication()

    publisher.publish(publication)
    current["usage"] = second
    publisher.publish(publication)

    expected_event = UsageUpdated(
        publication.cumulative_usage, publication.context_tokens
    )
    assert trace == [
        ("absorb", publication.sample),
        ("emit", expected_event),
        ("absorb", publication.sample),
        ("emit", expected_event),
    ]
    assert first.agent_usage() == AgentUsage(input_tokens=4, output_tokens=2)
    assert second.agent_usage() == AgentUsage(input_tokens=4, output_tokens=2)
    assert event_sink.events == [expected_event, expected_event]
    assert isinstance(publisher, AgentUsagePublisher)


def test_usage_publisher_stops_before_event_when_absorb_fails() -> None:
    trace: list[object] = []

    def fail(sample: AgentProviderUsageSample) -> None:
        trace.append(("absorb", sample))
        raise RuntimeError("absorb failed")

    publisher = NativeAgentUsagePublisher(fail, _EventSink(trace))

    with pytest.raises(RuntimeError, match="absorb failed"):
        publisher.publish(_publication())
    assert [item[0] for item in trace if isinstance(item, tuple)] == ["absorb"]


def test_usage_publisher_propagates_event_backpressure_failure_after_absorb() -> None:
    trace: list[object] = []
    samples: list[AgentProviderUsageSample] = []
    event_failure = RuntimeError("sink blocked")
    publisher = NativeAgentUsagePublisher(
        lambda sample: samples.append(sample),
        _EventSink(trace, failure=event_failure),
    )

    with pytest.raises(RuntimeError, match="sink blocked") as raised:
        publisher.publish(_publication())
    assert raised.value is event_failure
    assert samples == [_publication().sample]
    assert len(trace) == 1


def test_queue_port_takes_one_callback_item_without_reordering() -> None:
    queued = [
        AgentQueuedInput(ProductContent("steer"), AgentQueuedInputKind.STEERING),
        AgentQueuedInput(ProductContent("later"), AgentQueuedInputKind.FOLLOW_UP),
    ]
    callback_count = 0

    def take_next() -> AgentQueuedInput | None:
        nonlocal callback_count
        callback_count += 1
        return queued.pop(0) if queued else None

    port = NativeAgentQueuedInputPort(take_next)

    assert port.take_next() == AgentQueuedInput(
        ProductContent("steer"), AgentQueuedInputKind.STEERING
    )
    assert port.take_next() == AgentQueuedInput(
        ProductContent("later"), AgentQueuedInputKind.FOLLOW_UP
    )
    assert port.take_next() is None
    assert callback_count == 3
    assert isinstance(port, AgentQueuedInputPort)
    for forbidden in ("reserve", "settle", "mark_idle", "peek", "put"):
        assert not hasattr(port, forbidden)


def test_queue_port_rejects_untyped_callback_result() -> None:
    port = NativeAgentQueuedInputPort(
        cast(
            Callable[[], AgentQueuedInput | None],
            lambda: ProductContent("not queued input"),
        )
    )

    with pytest.raises(TypeError, match="must return AgentQueuedInput or None"):
        port.take_next()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: NativeAgentUsagePublisher(
                cast(Callable[[AgentProviderUsageSample], None], None),
                _EventSink([]),
            ),
            "absorb_usage",
        ),
        (
            lambda: NativeAgentQueuedInputPort(
                cast(Callable[[], AgentQueuedInput | None], None)
            ),
            "take_next",
        ),
    ],
)
def test_product_adapters_reject_non_callable_callbacks(
    factory: Callable[[], object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        factory()
