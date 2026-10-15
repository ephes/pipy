"""Resource enable/disable patterns (Pi `pi config` model).

Enablement is persisted by writing ``-pattern`` (disable) and ``+pattern``
(re-enable) entries into the relevant settings array (``skills`` / ``prompts`` /
``themes`` / ``extensions``) rather than removing the discovered resource paths.
The original discovered resources stay; these directives control what is
actually registered. A resource is enabled by default; among the directives that
match its name, the last one wins. Bare entries (resource source paths) are
additive sources and do not affect enablement.

Patterns match a resource name exactly or as an fnmatch glob (e.g. ``draft-*``).

Built-in extensions are parts of pipy that can be turned off like an
extension (Pi's built-in extensions). One is named ``builtin:<name>`` in the
``extensions`` array and on the command line: ``mcp``, ``codemode`` (the
``codemode`` tool) and ``tool-search`` (the ``tool_search`` tool). Pi's
``llama.cpp`` has no counterpart. Their entries follow Pi's override rules
rather than the ones above: ``!pattern`` is a glob that turns them off,
``+builtin:<name>`` and ``-builtin:<name>`` name one exactly
(:func:`is_builtin_extension_enabled`).
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch
from typing import Final

BUILTIN_EXTENSION_PREFIX: Final = "builtin:"
# The parts of pipy that ``-builtin:<name>`` in ``extensions`` turns off.
BUILTIN_EXTENSIONS: Final = ("codemode", "tool-search", "mcp")


def _matches(name: str, pattern: str) -> bool:
    return name == pattern or fnmatch(name, pattern)


def is_resource_enabled(name: str, patterns: list[str]) -> bool:
    """Return whether ``name`` is enabled given a settings enable/disable array.

    Enabled by default; ``-pat`` disables a matching name and ``+pat`` re-enables
    it, with the last matching directive winning. Bare (sign-less) entries are
    treated as additive source paths and ignored for enablement.
    """

    enabled = True
    for entry in patterns:
        if not isinstance(entry, str) or not entry:
            continue
        sign, pattern = entry[0], entry[1:]
        if sign == "-" and _matches(name, pattern):
            enabled = False
        elif sign == "+" and _matches(name, pattern):
            enabled = True
    return enabled


def filter_enabled(names: list[str], patterns: list[str]) -> list[str]:
    """Return ``names`` constrained to those enabled by ``patterns`` (in order)."""

    return [name for name in names if is_resource_enabled(name, patterns)]


def _without_directives(patterns: list[str], name: str) -> list[str]:
    """Drop any exact ``+name`` / ``-name`` directives for ``name``."""

    return [p for p in patterns if p not in (f"+{name}", f"-{name}")]


def disable_entry(patterns: list[str], name: str) -> list[str]:
    """Return ``patterns`` updated to disable ``name`` (append ``-name``).

    Removes a redundant existing ``+name`` directive first; idempotent.
    """

    result = _without_directives(patterns, name)
    result.append(f"-{name}")
    return result


def enable_entry(patterns: list[str], name: str) -> list[str]:
    """Return ``patterns`` updated to enable ``name`` (append ``+name``).

    Removes a redundant existing ``-name`` directive first; idempotent.
    """

    result = _without_directives(patterns, name)
    result.append(f"+{name}")
    return result


def builtin_extension_name(value: str) -> str | None:
    """The name in ``builtin:<name>``, or ``None`` for anything else."""

    if not value.startswith(BUILTIN_EXTENSION_PREFIX):
        return None
    return value[len(BUILTIN_EXTENSION_PREFIX) :]


def is_builtin_extension_enabled(
    name: str, *, user_patterns: Sequence[object], project_patterns: Sequence[object]
) -> bool:
    """Whether the ``extensions`` settings leave a built-in extension on.

    Pi ``resolve`` in ``package-manager.ts``: the project array decides when
    one of its ``!``, ``+`` or ``-`` entries matches ``builtin:<name>``, the
    last matching entry winning (``applyAutoloadDisabledPatterns``).
    Otherwise the user-level array decides (``isEnabledByOverrides``).
    """

    resource = BUILTIN_EXTENSION_PREFIX + name
    decided = _last_matching_override(resource, project_patterns)
    if decided is not None:
        return decided
    return _enabled_by_overrides(resource, user_patterns)


def _override_entries(patterns: Sequence[object]) -> list[str]:
    """Pi ``getOverridePatterns``: the ``!``, ``+`` and ``-`` entries."""

    return [
        entry
        for entry in patterns
        if isinstance(entry, str) and entry[:1] in ("!", "+", "-")
    ]


def _matches_exactly(resource: str, target: str) -> bool:
    """Pi ``matchesAnyExactPattern`` for a built-in extension's path: the
    target as written, without a leading ``./``."""

    if target.startswith(("./", ".\\")):
        target = target[2:]
    return target == resource


def set_builtin_extension_entry(
    patterns: list[str], name: str, *, enabled: bool
) -> list[str]:
    """``patterns`` with ``+builtin:<name>`` (or ``-``) appended in place of
    every ``+`` or ``-`` entry that names it exactly, also as ``./builtin:<name>``.

    Removing those keeps an earlier ``-`` entry from winning over the new
    ``+`` one in the user array (:func:`is_builtin_extension_enabled`).
    """

    resource = BUILTIN_EXTENSION_PREFIX + name
    result = [
        entry
        for entry in patterns
        if not (entry[:1] in ("+", "-") and _matches_exactly(resource, entry[1:]))
    ]
    result.append(("+" if enabled else "-") + resource)
    return result


def _override_matches(resource: str, entry: str) -> bool:
    """A ``!`` entry is a glob, a ``+`` or ``-`` entry an exact name (Pi)."""

    target = entry[1:]
    if entry[0] == "!":
        return _matches(resource, target)
    return _matches_exactly(resource, target)


def _last_matching_override(resource: str, patterns: Sequence[object]) -> bool | None:
    """Pi ``applyAutoloadDisabledPatterns``: the last matching entry decides,
    ``+`` turning on and ``!`` or ``-`` off; ``None`` when none matches."""

    decided: bool | None = None
    for entry in _override_entries(patterns):
        if _override_matches(resource, entry):
            decided = entry[0] == "+"
    return decided


def _enabled_by_overrides(resource: str, patterns: Sequence[object]) -> bool:
    """Pi ``isEnabledByOverrides``: a ``-`` entry turns it off over a ``+``
    entry, which turns it on over a ``!`` entry, whatever their order."""

    signs = {
        entry[0]
        for entry in _override_entries(patterns)
        if _override_matches(resource, entry)
    }
    if "-" in signs:
        return False
    return "+" in signs or "!" not in signs
