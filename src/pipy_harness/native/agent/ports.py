"""Ports owned by the reusable native agent boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pipy_harness.native.agent.events import AgentEvent


@runtime_checkable
class AgentEventSink(Protocol):
    """Receives one immutable event synchronously.

    ``emit`` completes before the producer constructs or emits the next event.
    Implementations that cross threads or processes own their buffering and
    serialization outside this boundary.
    """

    def emit(self, event: AgentEvent) -> None: ...
