"""One explicit generic missing-definition settlement on the Session line."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .ownership_query import require_missing_root_leaf
from .session import SessionClosedError, SessionFailed
from .session_core import SyncTx
from .task_values import identity

if TYPE_CHECKING:
    from .scheduler import TaskScheduler

OrphanResult = Literal["orphaned", "terminal"]


def _admit(scheduler: TaskScheduler) -> None:
    with scheduler._guard:
        failure = scheduler._owner._failure
        closed = scheduler._owner._line_sealed or scheduler._host_done
        opened = scheduler._opened
        busy = scheduler._draining or scheduler._invocation is not None
    if failure is not None:
        raise SessionFailed(failure) from failure
    if closed:
        raise SessionClosedError()
    if not opened:
        raise RuntimeError("Open the scheduler before orphaning a task")
    if busy:
        raise RuntimeError("Task scheduler is busy")


def orphan(scheduler: TaskScheduler, task_id: int) -> OrphanResult:
    from .scheduler import UnsupportedTaskExecution, _state

    def change(tx: SyncTx) -> OrphanResult:
        _admit(scheduler)
        saved = tx.task(task_id)
        if saved is None:
            raise ValueError("Unknown task")
        record = cast(dict[str, Any], saved.to_tree())
        if record["kind"].startswith("pi."):
            raise UnsupportedTaskExecution("Built-in tasks cannot be orphaned here")
        if record["state"]["status"] == "terminal":
            return "terminal"
        require_missing_root_leaf(tx, record)
        with scheduler._guard:
            generation = scheduler._definition_generation
        if identity(record["kind"]) in generation.definitions:
            raise UnsupportedTaskExecution("Task definition is present")
        candidate = _state(
            record,
            {
                "status": "terminal",
                "outcome": {"status": "orphaned", "reason": "missing_task"},
            },
        )
        # No condition spans I/O or reference disposal. After this admission the
        # occupied line excludes successor reservation/catalog publication until
        # this real commit settles. Existing Tx assembly owns document retirement.
        _admit(scheduler)
        tx.set_task(candidate)
        return "orphaned"

    return scheduler._session.commit(change)
