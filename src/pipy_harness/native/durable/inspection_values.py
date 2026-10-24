"""Closed operator metadata; no product payload or execution capability escapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from .codec import OwnedJson
from .records import safe_integer
from .storage import Cursor
from .task_values import identity
from .tasks import Task, capture

Tree = dict[str, Any]
STATUSES = {"pending", "running", "waiting", "sleeping", "completing", "terminal"}
PHASES = {
    "prepare",
    "request",
    "settle",
    "tools",
    "call",
    "execute",
    "blocked",
    "unknown",
}
OUTCOMES = {"completed", "failed", "aborted", "orphaned", "faulted"}
REASONS = {
    "abort_requested",
    "definition_missing",
    "stored_version_newer",
    "definition_version_mismatch",
    "unsupported_phase",
    "unsupported_format",
    "unsupported_graph",
    "incompatible",
    "uncertain",
    "invalid_result",
    "retained_terminate",
    "receipt_held_needs_repair",
}
ERRORS = {
    "closed",
    "unavailable",
    "invalid_request",
    "busy",
    "missing_store",
    "unsupported_store_version",
    "invalid_store",
}


class InspectionError(ValueError):
    def __init__(self, code: str = "unavailable") -> None:
        if type(code) is not str or code not in ERRORS:
            code = "unavailable"
        self.code = code
        super().__init__("Durable inspection unavailable: " + code)


@dataclass(frozen=True, slots=True)
class TaskInspection:
    task_id: int
    conversation_id: int
    owner_id: int | None
    family: str
    version: int | float
    status: str
    phase: str | None
    outcome: str | None
    reason: str | None
    compatibility: str
    dependencies: tuple[int, ...] = ()
    dependency_count: int = 0
    invocation_ownership: str = "unknown"
    execution_material: str = "not_checked"

    def __post_init__(self) -> None:
        for numeric in (self.task_id, self.conversation_id):
            safe_integer(numeric)
        if self.owner_id is not None:
            safe_integer(self.owner_id)
        if type(self.version) not in (int, float):
            raise InspectionError("invalid_request")
        OwnedJson(self.version)
        for value in (
            self.family,
            self.status,
            self.compatibility,
            self.invocation_ownership,
            self.execution_material,
        ):
            if type(value) is not str:
                raise InspectionError("invalid_request")
        for optional in (self.phase, self.outcome, self.reason):
            if optional is not None and type(optional) is not str:
                raise InspectionError("invalid_request")
        if (
            self.family not in ("generation", "model_tool", "custom")
            or self.status not in STATUSES
            or self.phase is not None
            and self.phase not in PHASES
            or self.outcome is not None
            and self.outcome not in OUTCOMES
            or self.reason is not None
            and self.reason not in REASONS
            or self.compatibility
            not in ("supported", "unsupported", "unknown", "terminal_readonly")
            or self.invocation_ownership != "unknown"
            or self.execution_material != "not_checked"
            or type(self.dependencies) is not tuple
            or len(self.dependencies) > 128
            or type(self.dependency_count) is not int
            or not len(self.dependencies) <= self.dependency_count <= 2**53 - 1
        ):
            raise InspectionError("invalid_request")
        for dependency in self.dependencies:
            safe_integer(dependency)

    def to_tree(self) -> Tree:
        # This allowlist is also the human/JSON operator boundary. Never asdict
        # internal captures, TaskRecords or callback-bearing definitions.
        return {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "owner_id": self.owner_id,
            "family": self.family,
            "version": self.version,
            "status": self.status,
            "phase": self.phase,
            "outcome": self.outcome,
            "reason": self.reason,
            "compatibility": self.compatibility,
            "dependencies": list(self.dependencies),
            "dependency_count": self.dependency_count,
            "invocation_ownership": self.invocation_ownership,
            "execution_material": self.execution_material,
        }


@dataclass(frozen=True, slots=True)
class InspectionPage:
    tasks: tuple[TaskInspection, ...]
    next: Cursor | None = None

    def __post_init__(self) -> None:
        if type(self.tasks) is not tuple or any(
            type(row) is not TaskInspection for row in self.tasks
        ):
            raise InspectionError("invalid_request")
        if self.next is not None and type(self.next) is not Cursor:
            raise InspectionError("invalid_request")

    def to_tree(self) -> Tree:
        return {
            "tasks": [task.to_tree() for task in self.tasks],
            "next": None
            if self.next is None
            else {"after": self.next.after, "order": self.next.order},
        }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class InspectionSupport:
    """A callback-free value admitted BEFORE calling any inspection API."""

    _owned: OwnedJson = field(repr=False)

    def __init__(self, definitions: tuple[Task[Any, Any, Any], ...]) -> None:
        if type(definitions) is not tuple:
            raise InspectionError("invalid_request")
        rows = []
        names: set[bytes] = set()
        for raw in definitions:
            definition = capture(raw)
            key = identity(definition.name)
            if key in names:
                raise InspectionError("invalid_request")
            names.add(key)
            rows.append(
                {
                    "name": definition.name,
                    "version": definition.version,
                    "phases": list(definition.phases),
                    "support": None
                    if definition.support is None
                    else definition.support.to_tree(),
                }
            )
        object.__setattr__(self, "_owned", OwnedJson(rows))

    def definition(self, name: str) -> Tree | None:
        if type(self._owned) is not OwnedJson:
            raise InspectionError("invalid_request")
        key = identity(name)
        for row in cast(list[Tree], self._owned.thaw()):
            if identity(row["name"]) == key:
                return row
        return None
