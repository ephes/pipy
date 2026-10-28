"""Private immutable task declarations and their source-identity authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .codec import OwnedJson
from .inspection_values import InspectionSupport
from .task_values import identity
from .tasks import Task, capture

RESERVED = frozenset(
    identity(name) for name in ("pi.generation", "pi.tool", "pi.compaction")
)


def publication_name(name: str) -> bytes:
    if type(name) is not str:
        raise TypeError("Task name must be an exact string")
    key = identity(name)
    if key in RESERVED:
        raise ValueError("Built-in task definitions cannot be published")
    return key


@dataclass(frozen=True, slots=True, eq=False)
class DefinitionBinding:
    source: Task[Any, Any, Any]
    captured: Task[Any, Any, Any]

    @classmethod
    def own(cls, source: Task[Any, Any, Any]) -> DefinitionBinding:
        return cls(source, capture(source))

    def matches(self, other: DefinitionBinding) -> bool:
        """Compare detached data and callback identity, never callback equality."""
        left, right = self.captured, other.captured

        def data(task: Task[Any, Any, Any]) -> OwnedJson:
            return OwnedJson(
                {
                    "name": task.name,
                    "version": task.version,
                    "phases": list(task.phases),
                    "support": None if task.support is None else task.support.to_tree(),
                }
            )

        return (
            data(left) == data(right)
            and left.initial is right.initial
            and left.abort is right.abort
            and left.migrate is right.migrate
            and all(
                a is b
                for a, b in zip(
                    left.phases.values(), right.phases.values(), strict=True
                )
            )
        )


@dataclass(frozen=True, slots=True, eq=False, init=False)
class DefinitionGeneration:
    bindings: Mapping[bytes, DefinitionBinding]
    definitions: Mapping[bytes, Task[Any, Any, Any]]
    support: InspectionSupport

    def __init__(self, bindings: Iterable[DefinitionBinding]) -> None:
        owned: dict[bytes, DefinitionBinding] = {}
        for binding in bindings:
            key = identity(binding.captured.name)
            if key in owned:
                raise ValueError("Duplicate task definition UTF-16 identity")
            owned[key] = binding
        definitions = {key: binding.captured for key, binding in owned.items()}
        object.__setattr__(self, "bindings", MappingProxyType(owned))
        object.__setattr__(self, "definitions", MappingProxyType(definitions))
        object.__setattr__(
            self, "support", InspectionSupport(tuple(definitions.values()))
        )
