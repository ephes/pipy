"""Typed synchronous ports for reusable agent runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.usage import AgentProviderUsageSample


@dataclass(frozen=True, slots=True)
class AgentUsagePublication:
    """One provider sample paired with its cumulative run projection."""

    sample: AgentProviderUsageSample
    cumulative_usage: AgentUsage
    context_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.sample, AgentProviderUsageSample):
            raise TypeError(
                "AgentUsagePublication.sample must be AgentProviderUsageSample"
            )
        if not isinstance(self.cumulative_usage, AgentUsage):
            raise TypeError("AgentUsagePublication.cumulative_usage must be AgentUsage")
        if not isinstance(self.context_tokens, int) or isinstance(
            self.context_tokens, bool
        ):
            raise TypeError("AgentUsagePublication.context_tokens must be an integer")
        if self.context_tokens != self.sample.effective_total_tokens:
            raise ValueError(
                "AgentUsagePublication.context_tokens must match the sample's "
                "effective total"
            )


class AgentQueuedInputKind(StrEnum):
    """Closed kinds of controller-owned input eligible during an active run."""

    STEERING = "steering"
    FOLLOW_UP = "follow_up"


@dataclass(frozen=True, slots=True)
class AgentQueuedInput:
    """One controller-selected input awaiting canonical user acceptance."""

    content: ProductContent
    kind: AgentQueuedInputKind

    def __post_init__(self) -> None:
        if not isinstance(self.content, ProductContent):
            raise TypeError("AgentQueuedInput.content must be ProductContent")
        if not isinstance(self.kind, AgentQueuedInputKind):
            raise TypeError("AgentQueuedInput.kind must be AgentQueuedInputKind")


@runtime_checkable
class AgentUsagePublisher(Protocol):
    """Publishes one normalized usage update synchronously."""

    def publish(self, publication: AgentUsagePublication) -> None: ...


@runtime_checkable
class AgentQueuedInputPort(Protocol):
    """Takes the next controller-selected input, if one is eligible."""

    def take_next(self) -> AgentQueuedInput | None: ...
