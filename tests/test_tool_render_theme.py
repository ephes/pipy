from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.themes import DEFAULT_PALETTE, resolve_palette
from pipy_harness.native.tool_renderers import build_tool_render_theme


def _truecolor_style():
    return ChromeStyle(enabled=True, truecolor=True, palette=DEFAULT_PALETTE)


def test_disabled_theme_is_plain_text():
    theme = build_tool_render_theme(ChromeStyle(enabled=False))
    assert theme.fg("success", "ok") == "ok"
    assert theme.bold("ok") == "ok"
    assert theme.dim("ok") == "ok"


def test_truecolor_success_emits_palette_code_and_resets():
    theme = build_tool_render_theme(_truecolor_style())
    out = theme.fg("success", "ok")
    assert out.startswith("\x1b[") and out.endswith("\x1b[0m") and "ok" in out


def test_fallback_uses_indexed_color_code_when_not_truecolor():
    theme = build_tool_render_theme(
        ChromeStyle(enabled=True, truecolor=False, palette=DEFAULT_PALETTE)
    )
    out = theme.fg("error", "bad")
    # Indexed-color error fallback is used; truecolor "38;2;..." must NOT appear.
    assert "38;2;" not in out and "bad" in out


def test_success_and_warning_resolve_on_all_builtin_palettes():
    for name in ("pi", "high-contrast", "ocean"):
        palette = resolve_palette(name)
        assert palette.success_truecolor and palette.warning_truecolor
        assert palette.success_fallback and palette.warning_fallback
