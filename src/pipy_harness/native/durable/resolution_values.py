"""Private expected state and bounded operator results, distinct from Pi retryTask."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .codec import OwnedJson
from .records import safe_integer

if TYPE_CHECKING:
    from .scheduler import TaskScheduler

Tree = dict[str, Any]
CODES = {
    "closed",
    "busy",
    "stale",
    "incompatible",
    "not_supported",
    "conflict",
    "receipt_held_needs_repair",
    "retained_control",
    "unavailable",
}


class ResolutionError(ValueError):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in CODES:
            code = "unavailable"
        self.code = code
        super().__init__("Durable operator resolution refused: " + code)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ResolutionTarget:
    """Same-scheduler private capability; no raw-record export or repr."""

    _scheduler: TaskScheduler = field(repr=False)
    _facts: OwnedJson = field(repr=False)

    @property
    def task_id(self) -> int:
        return cast(int, cast(Tree, self._facts.thaw())["task"]["id"])

    @property
    def conversation_id(self) -> int:
        return cast(int, cast(Tree, self._facts.thaw())["task"]["conversationId"])


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    source_task_id: int
    new_task_id: int

    def __post_init__(self) -> None:
        safe_integer(self.source_task_id)
        safe_integer(self.new_task_id)

    def to_tree(self) -> dict[str, int]:
        return {"source_task_id": self.source_task_id, "new_task_id": self.new_task_id}
