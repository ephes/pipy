"""Concrete tool-render theme + fail-soft dispatch for extension tool renderers."""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.extension_runtime import (
    ThemeColor,
    ToolRenderContext,
    ToolRenderTheme,
    coerce_tool_render_lines,
)


class _PaletteToolRenderTheme:
    """A ToolRenderTheme backed by a ChromeStyle's palette."""

    def __init__(self, style: ChromeStyle) -> None:
        self._style = style

    def _code(self, color: ThemeColor) -> str:
        p = self._style.palette
        table = {
            "text": (p.user_message_text_truecolor, "39"),
            "accent": (p.accent_truecolor, p.accent_fallback),
            "success": (p.success_truecolor, p.success_fallback),
            "warning": (p.warning_truecolor, p.warning_fallback),
            "error": (p.error_truecolor, p.error_fallback),
            "dim": (p.dim_truecolor, p.dim_fallback),
        }
        truecolor_code, fallback_code = table.get(color, table["text"])
        return self._style.palette_code(truecolor_code, fallback_code)

    def fg(self, color: ThemeColor, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[{self._code(color)}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[1m{text}\x1b[0m"

    def dim(self, text: str) -> str:
        return self.fg("dim", text)


def build_tool_render_theme(style: ChromeStyle) -> ToolRenderTheme:
    return _PaletteToolRenderTheme(style)


def render_tool_phase(
    renderer: Callable[[ToolRenderContext], object],
    ctx: ToolRenderContext,
) -> list[str] | None:
    """Run one extension tool renderer fail-soft.

    Returns the rendered lines, or None to signal the caller should fall back
    to pipy's default rendering. A renderer that raises, returns a non-
    component, whose render() raises, or returns an uncoercible value all
    yield None. KeyboardInterrupt/SystemExit propagate."""

    try:
        component = renderer(ctx)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad renderer falls back
        return None
    render = getattr(component, "render", None)
    if not callable(render):
        return None
    try:
        produced = render(ctx.width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad render() falls back
        return None
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    return list(coerced)


_CHROME_TRUNCATION_MARKER = "  … (chrome truncated)"


def render_chrome_component(
    source: object,
    *,
    width: int,
    max_lines: int,
) -> list[str] | None:
    """Render a chrome source (lines, str, or zero-arg factory) fail-soft.

    Coercion order mirrors ``coerce_tool_render_lines`` (str special-cased
    before the generic Sequence path). ``source`` may be:
      * a callable factory taking no args and returning a component with
        ``render(width) -> Sequence[str]``;
      * a bare ``str`` (split on newlines) or any other ``Sequence[str]``.
    Returns the bounded lines, or ``None`` to signal the caller to fall back
    (clear the region / use the built-in). KeyboardInterrupt/SystemExit
    propagate."""

    component: object | None = None
    if callable(source):
        try:
            component = source()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad factory falls back
            return None
    elif not isinstance(source, (str, bytes, bytearray)) and callable(
        getattr(source, "render", None)
    ):
        # A direct ChromeComponent object (e.g. lines_component(...)).
        component = source
    if component is not None:
        render = getattr(component, "render", None)
        if not callable(render):
            return None
        try:
            produced = render(width)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad render() falls back
            return None
    else:
        produced = source
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    lines = list(coerced)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(_CHROME_TRUNCATION_MARKER)
    return lines
