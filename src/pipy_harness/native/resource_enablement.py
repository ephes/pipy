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
``extensions`` array and on the command line. Only ``mcp`` exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch
from typing import Final

BUILTIN_EXTENSION_PREFIX: Final = "builtin:"
# The parts of pipy that ``-builtin:<name>`` in ``extensions`` turns off.
BUILTIN_EXTENSIONS: Final = ("mcp",)


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

    Enabled unless the user-level array turns ``builtin:<name>`` off. A
    directive in the project array that matches it decides instead (Pi
    ``resolve`` in ``package-manager.ts``).
    """

    resource = BUILTIN_EXTENSION_PREFIX + name
    project = [entry for entry in project_patterns if isinstance(entry, str)]
    if any(
        entry[:1] in ("+", "-") and _matches(resource, entry[1:]) for entry in project
    ):
        return is_resource_enabled(resource, project)
    return is_resource_enabled(
        resource, [entry for entry in user_patterns if isinstance(entry, str)]
    )
