"""How a tool presents the session's tools to the model (Pi ``prepareLoadout``).

A loadout is what one capability value registers, declares and lets other
tools call. A tool that presents other tools, as ``codemode`` does, reads it
and answers with the changes it wants in what the model sees. The changes
never alter which tools are registered, active or callable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class ToolLoadout:
    """Pi ``ToolLoadout``: the declared tools, the ones other tools may call
    and every registered tool.

    All are registered definitions, with their exposure and namespace, in
    registration order. ``declared_tools`` have their original descriptions.
    """

    declared_tools: tuple[ToolDefinition, ...]
    callable_tools: tuple[ToolDefinition, ...]
    registered_tools: tuple[ToolDefinition, ...] = ()


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


def checked_loadout_changes(value: object) -> ToolLoadoutChanges | None:
    """What a tool's ``prepare_loadout`` returned, as changes that can be
    applied: ``None`` for no changes, and a ``ValueError`` for anything that
    is not a ``ToolLoadoutChanges`` of names and texts."""

    if value is None:
        return None
    if not isinstance(value, ToolLoadoutChanges):
        raise ValueError("prepare_loadout must return ToolLoadoutChanges or None")
    descriptions = value.descriptions
    if not isinstance(descriptions, Mapping) or not all(
        isinstance(name, str) and isinstance(text, str)
        for name, text in descriptions.items()
    ):
        raise ValueError("descriptions must map tool names to texts")
    hidden = value.hidden_declarations
    if isinstance(hidden, (str, bytes)) or not isinstance(
        hidden, (frozenset, set, tuple, list)
    ):
        raise ValueError("hidden_declarations must be a collection of tool names")
    if not all(isinstance(name, str) for name in hidden):
        raise ValueError("hidden_declarations must be a collection of tool names")
    return ToolLoadoutChanges(dict(descriptions), frozenset(hidden))
