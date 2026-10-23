"""One off-line provider attempt whose real worker lifetime belongs to its phase."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from pipy_harness.native.agent.assistant_blocks import PartialAssistantContent
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.events import AgentEvent
from pipy_harness.native.agent.provider_retry import ProviderManagedRetryPolicy
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    ProviderTurnInterruption,
    ProviderTurnOutcome,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider import ProviderPort

from .scheduler import TaskContext

T = TypeVar("T")


class _PrivateEvents:
    def emit(self, event: AgentEvent) -> None:
        """Streaming is private until a completed receipt has settled."""


def _no_reissue() -> None:
    raise RuntimeError("Durable provider retry requires DE17")


async def execute_provider(
    provider: ProviderPort, request: ProviderRequest, context: TaskContext
) -> tuple[ProviderTurnOutcome, tuple[TextContent | ThinkingContent, ...]]:
    def run() -> tuple[ProviderTurnOutcome, tuple[TextContent | ThinkingContent, ...]]:
        # Only this wrapper thread reads/writes workers. Its completion is the
        # handoff after all actual provider threads and their finalizers exit.
        workers: list[threading.Thread] = []

        def retain(**kwargs: Any) -> threading.Thread:
            worker = threading.Thread(**kwargs)
            workers.append(worker)
            return worker

        def wait(
            done: threading.Event, cancelled: threading.Event
        ) -> ProviderTurnInterruption:
            while not done.wait(0.01):
                if context.signal.cancelled:
                    return ProviderTurnInterruption.OPERATOR_ABORT
            return ProviderTurnInterruption.SETTLED

        buffered = PartialAssistantContent(
            _PrivateEvents(), request.provider_turn_index
        )
        executor = ProviderTurnExecutor(provider_thread_factory=retain)
        try:
            outcome = executor.complete(
                provider,
                request,
                buffered,
                turn_index=request.provider_turn_index,
                waiter=wait,
                # None selects a prepared provider's standalone retry loop.
                # The existing managed seam supplies its real (1, 1) bound.
                retry_policy=ProviderManagedRetryPolicy(1, 0, 0),
                before_reissue=_no_reissue,
            )
        finally:
            for worker in workers:
                # A failed start has no running thread; every successful start
                # is joined, even after executor cancellation/error/timeout.
                if worker.ident is not None:
                    worker.join()
        return outcome, buffered.blocks()

    return await owned_call(run)


async def owned_call(run: Callable[[], T]) -> T:
    """A phase cannot leave its real off-host work behind on cancellation."""
    work = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(work)
    finally:
        # Cancellation of an API/phase waiter cannot detach accepted transport.
        # Only this host loop inspects its asyncio task; foreign threads only
        # observe TaskSignal's concurrent.futures.Future and threading.Events.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
        # Retrieve an exception even when the first waiter was cancelled.
        if not work.cancelled():
            work.exception()
