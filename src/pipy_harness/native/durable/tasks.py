"""Immutable executable task definitions for the private durable Session.

Adapted from Pi durable tasks.ts/types.ts at
2ec55d808d455b1a64a2bf7888198207a096120a, MIT Copyright (c) 2025
Mario Zechner; see UPSTREAM-LICENSE.txt. Dispatch and compatibility resolution
belong to the scheduler, not task creation.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import CoroutineType, MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar, cast

from .codec import OwnedJson
from .execution_support import ExecutionSupport

if TYPE_CHECKING:
    from .scheduler import TaskContext, TaskRuntime

TInput = TypeVar("TInput")
TCheckpoint = TypeVar("TCheckpoint")
TResult = TypeVar("TResult")
PhaseHandler: TypeAlias = Callable[
    [dict[str, Any], "TaskRuntime", "TaskContext"], Awaitable[None]
]
MigrationHandler: TypeAlias = Callable[[Any, Any, int | float], object]


@dataclass(frozen=True, slots=True, eq=False)
class Task(Generic[TInput, TCheckpoint, TResult]):
    """Identity token with captured name, numeric version and callbacks."""

    name: str
    version: int | float
    initial: Callable[[TInput], TCheckpoint]
    phases: Mapping[str, PhaseHandler]
    abort: PhaseHandler
    support: ExecutionSupport | None = field(default=None, kw_only=True, repr=False)
    migrate: MigrationHandler | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if type(self.name) is not str:
            raise TypeError("Task name must be an exact string")
        if type(self.version) not in (int, float):
            raise TypeError("Task version must be a finite JSON number")
        OwnedJson(self.version)
        if not callable(self.initial) or not callable(self.abort):
            raise TypeError("Task initial and abort handlers must be callable")
        if self.migrate is not None and not callable(self.migrate):
            raise TypeError("Task migration handler must be callable")
        if type(self.phases) not in (dict, MappingProxyType):
            raise TypeError("Task phases must be a plain dictionary")
        phases = dict(self.phases)
        # Own keys with the same UTF-16 identity rules as persisted JSON.
        OwnedJson(dict.fromkeys(phases))
        if any(not callable(handler) for handler in phases.values()):
            raise TypeError("Task phase handlers must be callable")
        object.__setattr__(self, "phases", MappingProxyType(phases))
        if self.support is not None:
            if type(self.support) is not ExecutionSupport:
                raise TypeError("Expected finite execution support")
            object.__setattr__(
                self, "support", ExecutionSupport(self.support.to_tree())
            )


def define_task(
    name: str,
    version: int | float,
    initial: Callable[[TInput], TCheckpoint],
    phases: dict[str, PhaseHandler],
    abort: PhaseHandler,
    *,
    support: ExecutionSupport | None = None,
    migrate: MigrationHandler | None = None,
) -> Task[TInput, TCheckpoint, Any]:
    """Define a task; creating it persists initial(input), without dispatch."""
    return Task(name, version, initial, phases, abort, support=support, migrate=migrate)


def capture(task: object) -> Task[Any, Any, Any]:
    """Revalidate and snapshot before suspension, including hostile reflection."""
    if type(task) is not Task:
        raise TypeError("Expected a task definition")
    return Task(
        task.name,
        task.version,
        task.initial,
        task.phases,
        task.abort,
        support=task.support,
        migrate=task.migrate,
    )


def initialize(definition: Task[Any, Any, Any], owned_input: OwnedJson) -> OwnedJson:
    """Execute a synchronous initializer over detached input, before ID mint."""
    input = owned_input.thaw()
    checkpoint = definition.initial(input)
    if type(checkpoint) is CoroutineType:
        if inspect.getcoroutinestate(checkpoint) == inspect.CORO_CREATED:
            checkpoint.close()
        raise TypeError("Task initial() must return synchronous JSON")
    # The finite JSON copier uses exact types. It rejects Futures and foreign
    # awaitables without isinstance/__class__, __await__, or cleanup hooks.
    return OwnedJson({"input": input, "checkpoint": checkpoint})


def migrate(definition: Task[Any, Any, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Detach callback arguments and own both outputs before reservation writes."""
    callback = definition.migrate
    if callback is None:
        raise TypeError("Task has no migration handler")
    arguments = cast(
        dict[str, Any],
        OwnedJson(
            {"input": record["input"], "checkpoint": record["state"]["checkpoint"]}
        ).thaw(),
    )
    result = callback(arguments["input"], arguments["checkpoint"], record["version"])
    if type(result) is CoroutineType:
        if inspect.getcoroutinestate(result) == inspect.CORO_CREATED:
            result.close()
        raise TypeError("Task migrate() must return synchronous JSON")
    # Exact JSON ownership rejects foreign awaitables without running hooks.
    owned = OwnedJson(result).thaw()
    if type(owned) is not dict or set(owned) != {"input", "checkpoint"}:
        raise TypeError("Task migrate() must return input and checkpoint")
    return {
        **record,
        "version": definition.version,
        "input": owned["input"],
        "state": {**record["state"], "checkpoint": owned["checkpoint"]},
    }
