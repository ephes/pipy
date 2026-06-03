"""Scoped-model cycling helpers (Pi parity).

``enabledModels`` (a list of model patterns in ``settings``) constrains the
Ctrl+P / Ctrl+Shift+P model cycle set; when empty the cycle uses the full
available catalog. A pattern matches a model reference (``provider/model``)
exactly or as an fnmatch glob (e.g. ``openai/*``). These helpers are pure so the
``/scoped-models`` command and the keybinding-driven cycle share one tested
implementation; the session layer applies the chosen reference through the
existing ``NativeReplProviderState.select_model`` boundary (no provider turn).
"""

from __future__ import annotations

from fnmatch import fnmatch


def filter_scoped_references(
    references: list[str], patterns: list[str]
) -> list[str]:
    """Return ``references`` constrained to those matching any pattern.

    Order follows ``references`` (not the pattern list). Empty ``patterns``
    keeps the full list (no scoping). A pattern matches a reference exactly or
    as an fnmatch glob.
    """

    if not patterns:
        return list(references)
    return [
        ref
        for ref in references
        if any(ref == pat or fnmatch(ref, pat) for pat in patterns)
    ]


def next_reference(
    references: list[str], current: str, *, forward: bool
) -> str | None:
    """Return the next/previous reference in the cycle, wrapping.

    When ``current`` is not in ``references`` the cycle starts at the first
    entry (forward) or the last entry (backward). Returns ``None`` for an empty
    set.
    """

    if not references:
        return None
    try:
        index = references.index(current)
    except ValueError:
        return references[0] if forward else references[-1]
    step = 1 if forward else -1
    return references[(index + step) % len(references)]
