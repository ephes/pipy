"""Adapter protocol for the pipy harness."""

from __future__ import annotations

from typing import Mapping, Protocol

from pipy_harness.capture import CapturePolicy
from pipy_harness.models import AdapterResult, PreparedRun, RunRequest


class EventSink(Protocol):
    """Runner-owned sink that serializes harness event writes."""

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Append one privacy-safe event to the active run record."""


class AgentPort(Protocol):
    """Protocol implemented by concrete agent adapters."""

    @property
    def name(self) -> str:
        """Adapter name stored in harness metadata."""

    def prepare(self, request: RunRequest) -> PreparedRun:
        """Validate and prepare a run without mutating session records."""

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        """Run the prepared command and report privacy-safe events."""
