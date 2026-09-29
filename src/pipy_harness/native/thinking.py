"""Thinking-level validation and per-model mapping (M5).

Pipy analogue of Pi's thinking-level handling (packages/ai/src/models.ts +
args.ts): the CLI surface validates the seven-value set
(``off|minimal|low|medium|high|xhigh|max``), warning on invalid input, and each
model maps a requested level to its provider-specific reasoning value through
``thinking_level_map``. ``off`` and unsupported models ignore the level; the two
extended levels ``xhigh`` and ``max`` are only honoured when the model maps them.

``available_thinking_levels`` / ``clamp_thinking_level`` port Pi's
``getSupportedThinkingLevels`` / ``clampThinkingLevel``;
``resolve_responses_reasoning`` is the Responses-family (OpenAI, Azure, Codex)
clamp-then-map, including Pi's ``map.off ?? "none"`` off-state.
"""

from __future__ import annotations

from collections.abc import Sequence

from pipy_harness.native.catalog import THINKING_LEVELS, NativeModelSpec

# Pi ``DEFAULT_THINKING_LEVEL`` (coding-agent/src/core/defaults.ts:3): the
# startup level when neither ``--thinking`` nor the settings
# ``defaultThinkingLevel`` names one.
DEFAULT_THINKING_LEVEL = "medium"

# Standard levels passed through for a reasoning model that declares no explicit
# thinking_level_map. xhigh and max are intentionally excluded: each is only
# available when a model maps it (Pi's models.ts).
_DEFAULT_REASONING_LEVELS = ("minimal", "low", "medium", "high")

# Pi's EXTENDED_THINKING_LEVELS order (models.ts) used for clamping. The two
# extended levels only appear for a model that explicitly maps them.
_EXTENDED_ORDER: tuple[str, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
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

    Mirrors Pi ``models.ts:1211-1220``: a non-reasoning model offers only
    ``off``; a reasoning model offers ``off`` and each ordinary level
    (``minimal|low|medium|high``) unless the map explicitly removes it with a
    ``None`` value (``off: None`` means thinking cannot be switched off, e.g.
    Claude Fable 5 or GPT-6 Astra), and offers ``xhigh``/``max`` only when the
    map assigns them a concrete value. Ordinary levels are identity-available
    even when unmapped, so a partial map (e.g. ``{"xhigh": "xhigh"}``) still
    offers the ordinary tier.
    """

    if not model.reasoning:
        return ["off"]
    level_map = model.thinking_level_map or {}
    levels: list[str] = []
    for level in _EXTENDED_ORDER:
        mapped = level_map.get(level, _UNSET)
        if mapped is None:
            continue
        if level in _EXTENDED_ONLY and mapped is _UNSET:
            continue
        levels.append(level)
    return levels


def next_thinking_level(levels: Sequence[str], current: str | None) -> str:
    """Next Shift+Tab level after ``current`` in the ordered ``levels`` cycle.

    A current level the model does not offer (e.g. a stored ``max`` after a
    switch, or ``off`` on an off-less row such as Claude Fable 5) takes Pi
    ``cycleThinkingLevel``'s index -1 path (``agent-session.ts:2552-2561``) to
    ``levels[0]``. pipy's unset level (``None``; Pi always has a level) counts as
    ``off`` when the model offers it.
    """

    if current in levels:
        index = levels.index(current)
    elif current is None and "off" in levels:
        index = levels.index("off")
    else:
        index = -1
    return levels[(index + 1) % len(levels)]


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


def responses_off_effort(model: NativeModelSpec) -> str | None:
    """Pi's Responses-family off-state effort (``map.off ?? "none"``).

    ``None`` means the model marks ``off`` unsupported (``off: None`` in the
    map), so Pi's ``map.off !== null`` gate sends no ``reasoning`` field. Key
    membership is checked first so a missing ``off`` (default ``"none"``) is not
    conflated with an explicit ``None``.
    """

    level_map = model.thinking_level_map or {}
    if "off" in level_map:
        return level_map["off"]
    return "none"


def resolve_responses_reasoning(
    model: NativeModelSpec, level: str | None
) -> tuple[str | None, bool]:
    """Clamp-then-map a level for the Responses families; return ``(effort, off)``.

    Ports the shared shape of Pi's ``openai-responses.ts:231-232,343-357``,
    ``azure-openai-responses.ts:182-183,330-344`` and
    ``openai-codex-responses.ts:514-515,582-597``: an on-state level is clamped
    to what the model offers (Pi ``clampThinkingLevel``) and sends
    ``map[level] ?? level``; ``off`` (Pi passes ``reasoning: undefined``) sends
    the off-state effort from :func:`responses_off_effort` (``off`` is
    ``True``).

    pipy-owned rule for an unset level (``None``): the request carries no
    thinking field at all (``(None, False)``), preserving the provider default.
    Pi never has an unset level; startup seeds ``medium``. A non-reasoning model
    also returns ``(None, False)`` — Pi gates both states on ``model.reasoning``.
    """

    if not level or not model.reasoning:
        return None, False
    # Pi passes ``reasoning: undefined`` for ``off``, so the adapters take the
    # off-state without clamping; only an on-state level is clamped.
    clamped = "off" if level == "off" else clamp_thinking_level(model, level)
    if clamped == "off":
        return responses_off_effort(model), True
    mapped = (model.thinking_level_map or {}).get(clamped)
    return (mapped if mapped is not None else clamped), False
