"""Product callback adapters for canonical synchronous agent runtime ports."""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.agent.events import UsageUpdated
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentUsagePublication,
)
from pipy_harness.native.agent.usage import AgentProviderUsageSample


class NativeAgentUsagePublisher:
    """Update current session usage before emitting canonical run usage."""

    __slots__ = ("_absorb_usage", "_event_sink")

    def __init__(
        self,
        absorb_usage: Callable[[AgentProviderUsageSample], None],
        event_sink: AgentEventSink,
    ) -> None:
        if not callable(absorb_usage):
            raise TypeError("absorb_usage must be callable")
        if not isinstance(event_sink, AgentEventSink):
            raise TypeError("event_sink must implement AgentEventSink")
        self._absorb_usage = absorb_usage
        self._event_sink = event_sink

    def publish(self, publication: AgentUsagePublication) -> None:
        if not isinstance(publication, AgentUsagePublication):
            raise TypeError("publication must be AgentUsagePublication")
        self._absorb_usage(publication.sample)
        self._event_sink.emit(
            UsageUpdated(publication.cumulative_usage, publication.context_tokens)
        )


class NativeAgentQueuedInputPort:
    """Expose one current controller-owned queue selection callback."""

    __slots__ = ("_take_next",)

    def __init__(self, take_next: Callable[[], AgentQueuedInput | None]) -> None:
        if not callable(take_next):
            raise TypeError("take_next must be callable")
        self._take_next = take_next

    def take_next(self) -> AgentQueuedInput | None:
        queued_input = self._take_next()
        if queued_input is not None and not isinstance(queued_input, AgentQueuedInput):
            raise TypeError("take_next callback must return AgentQueuedInput or None")
        return queued_input
