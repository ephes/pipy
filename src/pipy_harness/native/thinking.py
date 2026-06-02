"""Thinking-level validation and per-model mapping (M5).

Pipy analogue of Pi's thinking-level handling (packages/ai/src/models.ts +
args.ts): the CLI surface validates the six-value set
(``off|minimal|low|medium|high|xhigh``), warning on invalid input, and each
model maps a requested level to its provider-specific reasoning value through
``thinking_level_map``, clamped to what the model actually supports. ``off`` and
unsupported models ignore the level; ``xhigh`` is only honoured when the model
maps it.
"""

from __future__ import annotations

from pipy_harness.native.catalog import THINKING_LEVELS, NativeModelSpec


# Standard levels passed through for a reasoning model that declares no explicit
# thinking_level_map. xhigh is intentionally excluded: it is only available when
# a model maps it (Pi's models.ts).
_DEFAULT_REASONING_LEVELS = ("minimal", "low", "medium", "high")


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
