"""Private cooperative ordinary leaf aborts on the existing Session line.

Literal Pi durable 1.4 scheduler.ts542–573, 1521–1554, 1858–1935 and
2390–2397 supply the mark, interrupted-attempt and callback rules. This bounded
consumer settles marked missing code, holds version drift and refuses
nonterminal cleanup.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal, cast

from .codec import OwnedJson
from .definition_generation import DefinitionGeneration
from .ordinary_ownership import _AsyncOwnedLive, _capture_async
from .records import TaskRecord
from .restart_policy import RestartPolicy
from .session import SessionClosedError, SessionFailed
from .session_core import AsyncTx
from .task_values import identity
from .tasks import Task

if TYPE_CHECKING:
    from .scheduler import TaskRuntime, TaskScheduler, TaskTx, _Invocation

Tree = dict[str, Any]


def _record(row: Tree, status: str, requested: bool) -> bool:
    from .generation_tasks import GENERATION_NAME, TOOL_NAME

    return (
        row["state"]["status"] == status
        and row["abortRequested"] is requested
        and row["kind"] not in (GENERATION_NAME, TOOL_NAME)
        and not row.get("abandonOnRestart", False)
        and "abortReason" not in row
    )


def _waiting(row: Tree, requested: bool) -> bool:
    return _record(row, "waiting", requested) and row["state"]["policy"] == "allSettled"


def ready(scheduler: TaskScheduler) -> tuple[BaseException | None, bool, bool]:
    """Read only while the caller owns the scheduler/Session condition."""
    return (
        scheduler._owner._failure,
        scheduler._owner._line_sealed or scheduler._host_done,
        scheduler._opened,
    )


def _healthy(scheduler: TaskScheduler) -> None:
    with scheduler._guard:
        failure, closed, _opened = ready(scheduler)
    if failure is not None:
        raise SessionFailed(failure) from failure
    if closed:
        raise SessionClosedError()


def _admission(scheduler: TaskScheduler, row: Tree) -> _Invocation | None:
    # This final guarded snapshot is the shared admission/capture step. The
    # already-owned Session transaction retains its admitted-commit semantics
    # if close begins after it. No arbitrary callback runs in this condition.
    with scheduler._guard:
        failure, closed, opened = ready(scheduler)
        invocation = scheduler._invocation
        same = (
            invocation is not None
            and invocation.task_id == row["id"]
            and invocation.execution.mode == "run"
        )
        busy = scheduler._enabled or scheduler._draining or invocation is not None
        generation = scheduler._definition_generation
    if failure is not None:
        raise SessionFailed(failure) from failure
    if closed:
        raise SessionClosedError()
    if not opened:
        raise RuntimeError("Open the scheduler before marking an abort")
    if busy and not same:
        raise RuntimeError("Pause and retire the scheduler before marking an abort")
    if row["state"]["status"] != "terminal":
        from .scheduler import UnsupportedTaskExecution

        definition = generation.definitions.get(identity(row["kind"]))
        if definition is None or definition.version != row["version"]:
            raise UnsupportedTaskExecution(
                "Abort requires the current exact definition"
            )
    return invocation if same else None


async def _mark(
    scheduler: TaskScheduler, task_id: int
) -> tuple[str, _Invocation | None]:
    from .scheduler import UnsupportedTaskExecution

    async def change(tx: AsyncTx) -> tuple[str, _Invocation | None]:
        record = await tx.task(task_id)
        if record is None:
            raise ValueError("Unknown task")
        row = cast(Tree, record.to_tree())
        if row["state"]["status"] == "terminal":
            _admission(scheduler, row)
            return "terminal", None
        ownership = await _capture_async(tx)
        status = row["state"]["status"]
        if not (
            (_record(row, "pending", row["abortRequested"]))
            or _record(row, "running", row["abortRequested"])
            or _waiting(row, row["abortRequested"])
        ) or not ownership.root_leaf(task_id):
            raise UnsupportedTaskExecution(
                "Abort requires an ordinary strict root leaf"
            )
        invocation = _admission(scheduler, row)
        if status == "waiting" and invocation is not None:
            raise RuntimeError("Pause and retire the scheduler before marking an abort")
        if status == "running" and invocation is None:
            raise UnsupportedTaskExecution("Running abort requires its actual run slot")
        if invocation is not None and status == "pending" and not row["abortRequested"]:
            raise UnsupportedTaskExecution(
                "Only a marked handoff may join its run slot"
            )
        if not row["abortRequested"]:
            marked = {**row, "abortRequested": True}
            marked.pop("restarts", None)
            tx.set_task(marked)
        return "marked", invocation

    return await scheduler._session.acommit(change)


async def _retirement(
    scheduler: TaskScheduler,
    invocation: _Invocation,
    closed: concurrent.futures.Future[None],
) -> None:
    from .scheduler import _observed

    physical = asyncio.wrap_future(invocation.retired)
    close_wake = asyncio.wrap_future(closed)
    physical.add_done_callback(_observed)
    observers = (asyncio.shield(physical), asyncio.shield(close_wake))
    try:
        await asyncio.wait(observers, return_when=asyncio.FIRST_COMPLETED)
        _healthy(scheduler)
        await observers[0]
        _healthy(scheduler)
    except asyncio.CancelledError:
        # A genuine escaping run CancelledError is a native failure, not an
        # observer cancellation. C7 requires first-failure priority here.
        _healthy(scheduler)
        raise
    finally:
        for observer in observers:
            observer.cancel()  # shield only: never cancel either owned Future


async def request(
    scheduler: TaskScheduler, task_id: int
) -> Literal["marked", "terminal"]:
    closed: concurrent.futures.Future[None] = concurrent.futures.Future()
    # Atomic usability/registration; close takes each listener only once.
    unsubscribe = scheduler._session.subscribe_close(lambda: closed.set_result(None))
    try:
        result, invocation = await _mark(scheduler, task_id)
        if invocation is not None:
            await _retirement(scheduler, invocation, closed)
        _healthy(scheduler)
        return cast(Literal["marked", "terminal"], result)
    except asyncio.CancelledError:
        _healthy(scheduler)
        raise
    finally:
        unsubscribe()


async def handoff(scheduler: TaskScheduler, invocation: _Invocation) -> None:
    """Drain-only physical retirement; logical ended is intentionally allowed."""
    from .scheduler import _state

    if invocation.execution.mode != "run":
        return
    with scheduler._guard:
        failure, closed, _opened = ready(scheduler)
        indexed = scheduler._index.get(invocation.task_id)
    if failure is not None:
        raise SessionFailed(failure) from failure
    if closed:
        return  # Healthy close rewrites stopped rows only after service join.
    if indexed is None or not _record(cast(Tree, indexed.to_tree()), "running", True):
        return

    async def change(tx: AsyncTx) -> None:
        record = await tx.task(invocation.task_id)
        with scheduler._guard:
            same = scheduler._invocation is invocation
        if not same:
            raise RuntimeError("Run handoff lost its physical slot")
        if record is None:
            return
        row = cast(Tree, record.to_tree())
        if not _record(row, "running", True):
            return
        # Capture validates the whole graph, but a legitimate late child does
        # not grant abort authority: pending candidate rechecks strict leaf.
        await _capture_async(tx)
        tx.set_task(
            _state(row, {"status": "pending", "checkpoint": row["state"]["checkpoint"]})
        )

    try:
        settlement: Awaitable[None] = scheduler._session.acommit(change)
        await settlement
    except SessionClosedError:
        _healthy_close(scheduler)


def _healthy_close(scheduler: TaskScheduler) -> None:
    with scheduler._guard:
        failure = scheduler._owner._failure
    if failure is not None:
        raise SessionFailed(failure) from failure


def candidate(
    scheduler: TaskScheduler,
    tx: AsyncTx,
    row: Tree,
    ownership: _AsyncOwnedLive | None,
    policy: RestartPolicy,
    generation: DefinitionGeneration,
) -> tuple[Task[Any, Any, Any], Tree] | None:
    from .scheduler import _state

    if (
        ownership is None
        or not (_record(row, "pending", True) or _waiting(row, True))
        or not ownership.root_leaf(row["id"])
    ):
        return None
    with scheduler._guard:
        admitted = not scheduler._owner._line_sealed and scheduler._enabled
    if not admitted:
        return None
    if policy.limit is not None and row.get("restarts", 0) >= policy.limit:
        # The actual abort-attempt cutoff precedes code resolution. It grants
        # no termination authority for non-leaves, built-ins or restart marks.
        tx.set_task(
            _state(
                row,
                {
                    "status": "terminal",
                    "outcome": {
                        "status": "faulted",
                        "error": {
                            "message": f"Abort handler of task {row['id']} ({row['kind']}) was cut off {row['restarts']} times",
                            "detail": {"reason": "restart_limit"},
                        },
                    },
                },
            )
        )
        return None
    definition = generation.definitions.get(identity(row["kind"]))
    if definition is None:
        # A genuine earlier mark grants this decision, not a new request right.
        # Keep normal Tx terminal retirement without reserving an invocation.
        tx.set_task(
            _state(
                row,
                {
                    "status": "terminal",
                    "outcome": {"status": "orphaned", "reason": "missing_task"},
                },
            )
        )
        return None
    if definition.version != row["version"]:
        # No reservation => no counter growth; this hold can be indefinite.
        # Present drift still requires exact-version restoration, not migration.
        return None
    return definition, row


def running(
    invocation: _Invocation, row: Tree, ownership: _AsyncOwnedLive | None
) -> bool:
    definition = invocation.execution.definition
    return (
        ownership is not None
        and _record(row, "running", True)
        and identity(row["kind"]) == identity(definition.name)
        and row["version"] == definition.version
        and ownership.root_leaf(row["id"])
    )


async def commit(
    runtime: TaskRuntime,
    change: Callable[[TaskTx, Tree], object | Awaitable[object]],
) -> None:
    from .scheduler import TaskTx, UnsupportedTaskExecution, _state

    async def apply(tx: AsyncTx, current: Tree) -> None:
        ownership = await _capture_async(tx)
        value = change(
            TaskTx(tx, runtime.conversation_id, runtime.task_id),
            cast(Tree, OwnedJson(current).thaw()),
        )
        while inspect.isawaitable(value):
            value = await value
        # A raw internal replacement cannot remove the mark or rewrite the
        # captured record behind this invocation's state-return contract.
        if any(record.id == runtime.task_id for record in tx.staged_tasks()):
            raise UnsupportedTaskExecution("Abort cannot replace its invocation record")
        if not ownership.root_leaf(runtime.task_id):
            raise UnsupportedTaskExecution(
                "Abort cleanup must retain a strict root leaf"
            )
        runtime._scheduler._check(runtime._invocation)
        if value is None:
            return
        state = cast(Tree, OwnedJson(value).thaw())
        if not isinstance(state, dict) or state.get("status") != "terminal":
            raise UnsupportedTaskExecution("Abort cleanup requires a terminal outcome")
        TaskRecord(_state(current, state))
        tx.set_task(_state(current, state))

    ignored: object = await runtime._scheduler._gated(
        runtime._invocation, cast(Callable[[AsyncTx, Tree], Awaitable[object]], apply)
    )
    del ignored


async def _step(
    scheduler: TaskScheduler,
    invocation: _Invocation,
    *,
    finished: bool = False,
    failure: Exception | None = None,
) -> Tree | None:
    from .scheduler import _state

    async def change(tx: AsyncTx) -> Tree | None:
        record = await tx.task(invocation.task_id)
        row = None if record is None else cast(Tree, record.to_tree())
        with scheduler._guard:
            stopped = (
                scheduler._owner._line_sealed
                or invocation.ended
                or scheduler._invocation is not invocation
            )
        if stopped or row is None or row["state"]["status"] != "running":
            scheduler._end(invocation)
            return None
        ownership = await _capture_async(tx)
        if not running(invocation, row, ownership):
            scheduler._end(invocation)
            # External graph changes cannot turn a caught refusal into forced
            # termination. Retain intent; only a future eligible pass may run.
            if _record(row, "running", True):
                tx.set_task(
                    _state(
                        row,
                        {"status": "pending", "checkpoint": row["state"]["checkpoint"]},
                    )
                )
            return None
        if not finished:
            return row
        scheduler._end(invocation)
        message = (
            str(failure)
            if failure is not None
            else f"Abort handler of task {row['id']} returned without a terminal outcome"
        )
        tx.set_task(
            _state(
                row,
                {
                    "status": "terminal",
                    "outcome": {"status": "faulted", "error": {"message": message}},
                },
            )
        )
        return None

    try:
        return await scheduler._session.acommit(change)
    except BaseException as error:  # noqa: BLE001 - failed decision cannot retry
        scheduler._end(invocation)
        with scheduler._guard:
            closing = scheduler._owner._line_sealed
        if not closing:
            scheduler._session.fail(error)
        return None


async def run(scheduler: TaskScheduler, invocation: _Invocation) -> None:
    from .scheduler import TaskRuntime

    current = await _step(scheduler, invocation)
    if current is None:
        return
    with scheduler._guard:
        admitted = not scheduler._owner._line_sealed and not invocation.ended
    if not admitted:
        return
    failure = None
    try:
        await invocation.execution.definition.abort(
            current, TaskRuntime(scheduler, invocation), invocation.context
        )
    except Exception as error:  # noqa: BLE001 - real callback fault commits on line
        failure = error
    await _step(scheduler, invocation, finished=True, failure=failure)
