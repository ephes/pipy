"""Explicit generic orphan settlements on the Session line."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .ownership_query import require_unmarked_root_leaf
from .session import SessionClosedError, SessionFailed
from .session_core import SyncTx
from .task_values import identity

if TYPE_CHECKING:
    from .scheduler import TaskScheduler

OrphanResult = Literal["orphaned", "terminal"]
OrphanEligibility = Literal[
    "missing_task", "task_too_old", "migration_failed", "missing_migration"
]


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


def _require_eligibility(
    scheduler: TaskScheduler,
    task_id: int,
    record: dict[str, Any],
    eligibility: OrphanEligibility,
) -> None:
    from .scheduler import UnsupportedTaskExecution

    observed_failure = False
    missing_migration = False
    with scheduler._guard:
        generation = scheduler._definition_generation
        if eligibility == "migration_failed":
            binding = generation.bindings.get(identity(record["kind"]))
            observed_failure = (
                binding is not None
                and binding.captured.version > record["version"]
                and scheduler._migration_failures.get(task_id) is binding
            )
        elif eligibility == "missing_migration":
            binding = generation.bindings.get(identity(record["kind"]))
            missing_migration = (
                binding is not None
                and binding.captured.version > record["version"]
                and binding.captured.migrate is None
            )
    definition = generation.definitions.get(identity(record["kind"]))
    if eligibility == "missing_task":
        if definition is not None:
            raise UnsupportedTaskExecution("Task definition is present")
    elif eligibility == "task_too_old" and (
        definition is None or not definition.version < record["version"]
    ):
        raise UnsupportedTaskExecution(
            "Task definition is not older than the stored task"
        )
    elif eligibility == "migration_failed" and not observed_failure:
        raise UnsupportedTaskExecution(
            "Task has no observed failed migration for its current definition"
        )
    elif eligibility == "missing_migration" and not missing_migration:
        raise UnsupportedTaskExecution(
            "Task has no strictly newer definition without a migration handler"
        )


def orphan(
    scheduler: TaskScheduler,
    task_id: int,
    eligibility: OrphanEligibility,
) -> OrphanResult:
    from .scheduler import UnsupportedTaskExecution, _state

    reason = "migration_failed" if eligibility == "missing_migration" else eligibility

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
        require_unmarked_root_leaf(tx, record)
        _require_eligibility(scheduler, task_id, record, eligibility)
        candidate = _state(
            record,
            {
                "status": "terminal",
                "outcome": {"status": "orphaned", "reason": reason},
            },
        )
        # No condition spans I/O or reference disposal. After this admission the
        # occupied line excludes successor reservation/catalog publication until
        # this real commit settles. Existing Tx assembly owns document retirement.
        _admit(scheduler)
        tx.set_task(candidate)
        return "orphaned"

    return scheduler._session.commit(change)
