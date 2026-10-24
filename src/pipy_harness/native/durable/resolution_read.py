"""Private expected-state capture, only while the existing Session line is held."""

from __future__ import annotations

from typing import Any, cast

from .generation import ROUND_DOC
from .inspection_read import conversations, entries, peek_document, tasks
from .records import TaskId
from .resolution_values import ResolutionError
from .storage import Storage
from .tool_execution import RECEIPT_DOC

Tree = dict[str, Any]


def capture_facts(storage: Storage, task_id: int) -> Tree:
    found = storage.task(TaskId(task_id))
    if found is None:
        raise ResolutionError("unavailable")
    task = cast(Tree, found.to_tree())
    cid = task["conversationId"]
    rows = tasks(storage, cid)
    parent = next((r for r in rows if r["id"] == task.get("owner")), None)
    related = {r["id"] for r in rows}
    convs = [
        c
        for c in conversations(storage)
        if c["id"] == cid or c.get("owner", {}).get("taskId") in related
    ]
    return {
        "task": task,
        "tasks": rows,
        "conversations": convs,
        "entries": [entry.to_tree() for entry in entries(storage, cid).values()],
        # ANY receipt, including a newer/invalid payload, fences a blocked retry.
        "receipt": peek_document(storage, RECEIPT_DOC, cid, task_id, opaque=True),
        "round": None
        if parent is None
        else peek_document(storage, ROUND_DOC, cid, parent["id"], opaque=True),
    }
