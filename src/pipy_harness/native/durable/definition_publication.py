"""Idle private definition publication on the existing Session line."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .definition_generation import DefinitionBinding, DefinitionGeneration
from .session import SessionClosedError, SessionFailed

if TYPE_CHECKING:
    from .scheduler import TaskScheduler


def _ready(failure: BaseException | None, closed: bool, opened: bool) -> None:
    if failure is not None:
        raise SessionFailed(failure) from failure
    if closed:
        raise SessionClosedError()
    if not opened:
        raise RuntimeError("Open the scheduler before publishing definitions")


def publish(
    scheduler: TaskScheduler, key: bytes, incoming: DefinitionBinding | None
) -> bool:
    """Incoming data is already detached; no source Task is reread on the line."""

    def change() -> bool:
        with scheduler._guard:
            failure = scheduler._owner._failure
            closed = scheduler._owner._line_sealed or scheduler._host_done
            opened = scheduler._opened
            previous = scheduler._definition_generation
            failures = tuple(scheduler._migration_failures.values())
        _ready(failure, closed, opened)
        binding = incoming
        if incoming is not None:
            known = next(
                (
                    item
                    for item in (*previous.bindings.values(), *failures)
                    if item.source is incoming.source
                ),
                None,
            )
            if known is not None:
                if not known.matches(incoming):
                    raise ValueError("Known task declaration changed; use a new Task")
                binding = known
        with scheduler._guard:
            busy = scheduler._draining or scheduler._invocation is not None
        if busy:
            raise RuntimeError("Task scheduler is busy")
        entries = dict(previous.bindings)
        changed = entries.get(key) is not binding
        if binding is None:
            entries.pop(key, None)
        else:
            entries[key] = binding
        candidate = DefinitionGeneration(entries.values()) if changed else previous
        # The line serializes catalog readers/writers. The condition additionally
        # excludes service admission, which can happen while preparing a value.
        with scheduler._guard:
            failure = scheduler._owner._failure
            closed = scheduler._owner._line_sealed or scheduler._host_done
            opened = scheduler._opened
            busy = scheduler._draining or scheduler._invocation is not None
            accepted = failure is None and not closed and opened and not busy
            if accepted:
                scheduler._definition_generation = candidate
            wake = accepted and scheduler._enabled and (incoming is not None or changed)
        _ready(failure, closed, opened)
        if busy:
            raise RuntimeError("Task scheduler is busy")
        # Keep all displaced references alive until outside the condition; their
        # destructors may still be on the line and must obey reentry refusal.
        del previous, failures, entries
        if wake:
            scheduler._notify()
        return changed

    method = "remove_definition" if incoming is None else "install_definition"
    return scheduler._session._on_line_sync(method, change)
