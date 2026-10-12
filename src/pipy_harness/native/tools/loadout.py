"""How a tool presents the session's tools to the model (Pi ``prepareLoadout``).

A loadout is what one capability value declares and lets other tools call. A
tool that presents other tools, as ``codemode`` does, reads it and answers
with the changes it wants in what the model sees. The changes never alter
which tools are registered, active or callable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class ToolLoadout:
    """Pi ``ToolLoadout``: the declared tools and the ones other tools may call.

    Both are registered definitions, with their exposure and namespace, in
    registration order.
    """

    declared_tools: tuple[ToolDefinition, ...]
    callable_tools: tuple[ToolDefinition, ...]


@dataclass(frozen=True, slots=True)
class ToolLoadoutChanges:
    """Pi ``ToolLoadoutChanges``.

    ``descriptions`` replaces the description the model sees of a declared
    tool, by tool name. ``hidden_declarations`` names declared tools that
    requests leave out: they stay active and callable from other tools.
    """

    descriptions: Mapping[str, str] = field(default_factory=dict)
    hidden_declarations: frozenset[str] = frozenset()


PrepareToolLoadout = Callable[[ToolLoadout], ToolLoadoutChanges]
