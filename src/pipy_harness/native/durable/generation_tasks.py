"""Generation's real model-issued tool admission contract, before DE08 dispatch.

Adapted from Pi durable harness/tool.ts at
2ec55d808d455b1a64a2bf7888198207a096120a, MIT Copyright (c) 2025
Mario Zechner; see UPSTREAM-LICENSE.txt. Executable tool phases are deliberately
absent, rather than a placeholder handler claiming successful execution.
"""

from __future__ import annotations

from typing import Any

from .records import safe_integer, shape
from .tasks import Task, define_task

GENERATION_NAME = "pi.generation"
GENERATION_VERSION = 1
TOOL_NAME = "pi.tool"
TOOL_VERSION = 2


def tool_input(value: object) -> dict[str, object]:
    result = shape(value, {"kind", "assistant", "callId"})
    if (
        result["kind"] != "model"
        or type(result["callId"]) is not str
        or not result["callId"]
    ):
        raise ValueError("Durable model tool admission is incompatible")
    safe_integer(result["assistant"])
    return result


def _initial(value: object) -> dict[str, object]:
    tool_input(value)
    return {"phase": "call"}


async def _abort(*args: Any) -> None:
    raise RuntimeError("Durable tool execution requires DE08")


TOOL_TASK: Task[Any, Any, Any] = define_task(
    TOOL_NAME, TOOL_VERSION, _initial, {}, _abort
)
