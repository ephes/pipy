"""Contribution-name reservation records and collision checks.

An activation pass reserves every accepted contribution name (commands,
tools, providers, shortcuts, flags, message/entry renderers) before it
commits an extension, so two extensions can never claim the same name and
a reserved built-in name can never be shadowed. This module owns the two
immutable records of that protocol — :class:`_ContributionNames` (the
names one extension contributes, grouped in collision-check order) and
:class:`_TakenContributionState` (the reservation snapshot an activation
pass threads through) — plus the pure functions over them: normalization
(exact-string copies, intra-extension duplicate rejection), the
reserved/duplicate collision checks, and the non-mutating successor-state
builder a commit publishes.

Everything here is value-level: no host, no activation state, no I/O. The
projections that *produce* a :class:`_ContributionNames` from an activated
or staged extension (`_activated_contribution_names`,
`_staged_contribution_names`) stay with the activation band in
`pipy_harness.native.extensions.activation` — they take activation-owned types.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pipy_harness.native.extension_types import (
    REASON_DUPLICATE_COMMAND,
    REASON_DUPLICATE_ENTRY_RENDERER,
    REASON_DUPLICATE_FLAG,
    REASON_DUPLICATE_MESSAGE_RENDERER,
    REASON_DUPLICATE_PROVIDER,
    REASON_DUPLICATE_SHORTCUT,
    REASON_DUPLICATE_TOOL,
    REASON_RESERVED_COMMAND,
    REASON_RESERVED_TOOL,
)


@dataclass(frozen=True, slots=True)
class _ContributionNames:
    """Accepted contribution names, grouped in collision-check order."""

    commands: tuple[str, ...]
    tools: tuple[str, ...]
    providers: tuple[str, ...]
    shortcuts: tuple[str, ...]
    flags: tuple[str, ...]
    message_renderers: tuple[str, ...]
    entry_renderers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TakenContributionState:
    """One immutable reservation snapshot for an activation pass."""

    commands: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    shortcuts: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()
    message_renderers: frozenset[str] = frozenset()
    entry_renderers: frozenset[str] = frozenset()


def _reserved_or_taken_collision(
    names: Iterable[str],
    *,
    reserved: frozenset[str],
    taken: frozenset[str],
    reserved_reason: str,
    duplicate_reason: str,
) -> str | None:
    for name in names:
        if name in reserved:
            return reserved_reason
        if name in taken:
            return duplicate_reason
    return None


def _taken_collision(
    names: Iterable[str],
    *,
    taken: frozenset[str],
    duplicate_reason: str,
) -> str | None:
    for name in names:
        if name in taken:
            return duplicate_reason
    return None


def _preloaded_collision_reason(
    names: _ContributionNames,
    *,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
) -> str | None:
    reason = _reserved_or_taken_collision(
        names.commands,
        reserved=reserved,
        taken=taken.commands,
        reserved_reason=REASON_RESERVED_COMMAND,
        duplicate_reason=REASON_DUPLICATE_COMMAND,
    )
    if reason is not None:
        return reason
    reason = _reserved_or_taken_collision(
        names.tools,
        reserved=reserved_tools,
        taken=taken.tools,
        reserved_reason=REASON_RESERVED_TOOL,
        duplicate_reason=REASON_DUPLICATE_TOOL,
    )
    if reason is not None:
        return reason
    collision_categories = (
        (names.providers, taken.providers, REASON_DUPLICATE_PROVIDER),
        (names.shortcuts, taken.shortcuts, REASON_DUPLICATE_SHORTCUT),
        (names.flags, taken.flags, REASON_DUPLICATE_FLAG),
        (
            names.message_renderers,
            taken.message_renderers,
            REASON_DUPLICATE_MESSAGE_RENDERER,
        ),
        (
            names.entry_renderers,
            taken.entry_renderers,
            REASON_DUPLICATE_ENTRY_RENDERER,
        ),
    )
    for category_names, category_taken, duplicate_reason in collision_categories:
        reason = _taken_collision(
            category_names,
            taken=category_taken,
            duplicate_reason=duplicate_reason,
        )
        if reason is not None:
            return reason
    return None


def _normalize_contribution_name_category(
    category: tuple[str, ...],
) -> tuple[str, ...]:
    if any(type(name) is not str for name in category):
        raise TypeError("extension contribution name is not an exact string")
    copied = tuple(category)
    if len(frozenset(copied)) != len(copied):
        raise ValueError("duplicate extension contribution name")
    return copied


def _normalize_contribution_names(names: _ContributionNames) -> _ContributionNames:
    """Copy exact immutable strings while preserving named category binding."""

    return _ContributionNames(
        commands=_normalize_contribution_name_category(names.commands),
        tools=_normalize_contribution_name_category(names.tools),
        providers=_normalize_contribution_name_category(names.providers),
        shortcuts=_normalize_contribution_name_category(names.shortcuts),
        flags=_normalize_contribution_name_category(names.flags),
        message_renderers=_normalize_contribution_name_category(
            names.message_renderers
        ),
        entry_renderers=_normalize_contribution_name_category(names.entry_renderers),
    )


def _prepare_contribution_names_commit(
    names: _ContributionNames,
    taken: _TakenContributionState,
) -> _TakenContributionState:
    """Build the complete successor reservation state without mutating ``taken``."""

    return _TakenContributionState(
        commands=taken.commands.union(names.commands),
        tools=taken.tools.union(names.tools),
        providers=taken.providers.union(names.providers),
        shortcuts=taken.shortcuts.union(names.shortcuts),
        flags=taken.flags.union(names.flags),
        message_renderers=taken.message_renderers.union(names.message_renderers),
        entry_renderers=taken.entry_renderers.union(names.entry_renderers),
    )
