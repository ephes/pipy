"""Typed task definitions for predecessor creation-only transaction fixtures."""

from typing import Any

from pipy_harness.native.durable.tasks import Task, define_task


async def unexecuted(*args: Any) -> None:
    raise AssertionError("Creation-only fixture cannot be dispatched")


def task_definition(
    kind: str, version: int | float, checkpoint: object
) -> Task[Any, Any, Any]:
    return define_task(kind, version, lambda _input: checkpoint, {}, unexecuted)
