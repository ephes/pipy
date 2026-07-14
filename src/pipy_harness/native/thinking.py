"""Thinking-level validation and per-model mapping (M5).

Pipy analogue of Pi's thinking-level handling (packages/ai/src/models.ts +
args.ts): the CLI surface validates the seven-value set
(``off|minimal|low|medium|high|xhigh|max``), warning on invalid input, and each
model maps a requested level to its provider-specific reasoning value through
``thinking_level_map``. ``off`` and unsupported models ignore the level; the two
extended levels ``xhigh`` and ``max`` are only honoured when the model maps them.

``available_thinking_levels`` / ``clamp_thinking_level`` port Pi's
``getSupportedThinkingLevels`` / ``clampThinkingLevel``; ``resolve_codex_effort``
is the Codex-scoped clamp-then-map used at the REPL provider boundary.
"""

from __future__ import annotations

from pipy_harness.native.catalog import THINKING_LEVELS, NativeModelSpec


# Standard levels passed through for a reasoning model that declares no explicit
# thinking_level_map. xhigh and max are intentionally excluded: each is only
# available when a model maps it (Pi's models.ts).
_DEFAULT_REASONING_LEVELS = ("minimal", "low", "medium", "high")

# Pi's EXTENDED_THINKING_LEVELS order (models.ts) used for clamping. The two
# extended levels only appear for a model that explicitly maps them.
_EXTENDED_ORDER: tuple[str, ...] = (
    "off", "minimal", "low", "medium", "high", "xhigh", "max",
)
_ORDINARY_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high")
_EXTENDED_ONLY: frozenset[str] = frozenset({"xhigh", "max"})
_UNSET = object()


def validate_thinking_level(value: str) -> tuple[str | None, str | None]:
    """Return ``(level, None)`` if valid, else ``(None, warning)``."""

    if value in THINKING_LEVELS:
        return value, None
    return None, (
        f'Invalid thinking level "{value}". '
        f"Expected one of: {', '.join(THINKING_LEVELS)}."
    )


def supported_thinking_levels(model: NativeModelSpec) -> set[str]:
    """Levels (excluding ``off``) the model actually supports."""

    if model.thinking_level_map:
        return {
            level
            for level, value in model.thinking_level_map.items()
            if value is not None and level != "off"
        }
    if model.reasoning:
        return set(_DEFAULT_REASONING_LEVELS)
    return set()


def map_thinking_level(model: NativeModelSpec, level: str | None) -> str | None:
    """Map a requested level to the model's provider reasoning value, or ``None``.

    Returns ``None`` (reasoning disabled for this request) when ``level`` is
    ``None``/``off``, the model is non-reasoning, or the level is unsupported.
    """

    if not level or level == "off":
        return None
    if level not in supported_thinking_levels(model):
        return None
    if model.thinking_level_map:
        return model.thinking_level_map.get(level)
    return level


def available_thinking_levels(model: NativeModelSpec) -> list[str]:
    """Ordered levels the model offers (Pi's ``getSupportedThinkingLevels``).

    Mirrors Pi ``models.ts:410-419``: a non-reasoning model offers only ``off``;
    a reasoning model always offers ``off`` plus each ordinary level
    (``minimal|low|medium|high``) unless the map explicitly removes it with a
    ``None`` value, and offers ``xhigh``/``max`` only when the map assigns them a
    concrete value. Ordinary levels are identity-available even when unmapped, so
    a partial map (e.g. ``{"xhigh": "xhigh"}``) still offers the ordinary tier.
    """

    if not model.reasoning:
        return ["off"]
    level_map = model.thinking_level_map or {}
    levels = ["off"]
    for level in _EXTENDED_ORDER[1:]:
        mapped = level_map.get(level, _UNSET)
        if mapped is None:
            continue
        if level in _EXTENDED_ONLY and mapped is _UNSET:
            continue
        levels.append(level)
    return levels


def clamp_thinking_level(model: NativeModelSpec, level: str) -> str:
    """Clamp ``level`` to the nearest level the model offers (Pi ``clampThinkingLevel``).

    Port of ``models.ts:421-440``: returns ``level`` when already available;
    otherwise walks the extended order forward from the requested index, then
    backward, and falls back to the first available level (``off``).
    """

    available = available_thinking_levels(model)
    if level in available:
        return level
    if level not in _EXTENDED_ORDER:
        return available[0] if available else "off"
    requested = _EXTENDED_ORDER.index(level)
    for i in range(requested, len(_EXTENDED_ORDER)):
        if _EXTENDED_ORDER[i] in available:
            return _EXTENDED_ORDER[i]
    for i in range(requested - 1, -1, -1):
        if _EXTENDED_ORDER[i] in available:
            return _EXTENDED_ORDER[i]
    return available[0] if available else "off"


def resolve_codex_effort(model: NativeModelSpec, level: str | None) -> str | None:
    """Codex-scoped clamp-then-map for the ``reasoning.effort`` request field.

    Omits effort (returns ``None``) only when no level is selected or the level
    is ``off``; any other stored level is first clamped to what the model offers
    (matching Pi's per-request ``clampThinkingLevel`` in
    ``openai-codex-responses.ts:468``) and then mapped, using the level's map
    value when present and identity otherwise (``codex-responses.ts:521``). The
    ``None``/``off`` guard runs before ``clamp_thinking_level``.
    """

    if not level or level == "off":
        return None
    clamped = clamp_thinking_level(model, level)
    if clamped == "off":
        return None
    mapped = (model.thinking_level_map or {}).get(clamped)
    return mapped if mapped is not None else clamped
