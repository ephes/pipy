"""Resource enable/disable patterns (Pi `pi config` model).

Enablement is persisted by writing ``-pattern`` (disable) and ``+pattern``
(re-enable) entries into the relevant settings array (``skills`` / ``prompts`` /
``themes`` / ``extensions``) rather than removing the discovered resource paths.
The original discovered resources stay; these directives control what is
actually registered. A resource is enabled by default; among the directives that
match its name, the last one wins. Bare entries (resource source paths) are
additive sources and do not affect enablement.

Patterns match a resource name exactly or as an fnmatch glob (e.g. ``draft-*``).
"""

from __future__ import annotations

from fnmatch import fnmatch


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
