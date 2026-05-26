from __future__ import annotations

import pytest

from pipy_harness.native.themes import (
    BUILTIN_THEMES,
    Theme,
    ThemeColors,
    list_theme_names,
    resolve_theme,
    style,
)


def test_builtin_themes_registered() -> None:
    assert set(BUILTIN_THEMES) == {"default", "quiet", "mono"}
    for name, theme in BUILTIN_THEMES.items():
        assert isinstance(theme, Theme)
        assert theme.name == name
        assert isinstance(theme.colors, ThemeColors)


def test_resolve_default_when_name_none() -> None:
    theme = resolve_theme(None, tty=True)
    assert theme.name == "default"


def test_resolve_mono_when_tty_false_regardless_of_name() -> None:
    for requested in (None, "default", "quiet", "mono", "unknown", ""):
        theme = resolve_theme(requested, tty=False)
        assert theme.name == "mono", f"requested={requested!r}"


def test_resolve_unknown_name_raises_value_error_listing_supported() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_theme("nope", tty=True)
    message = str(exc_info.value)
    assert "nope" in message
    for name in ("default", "quiet", "mono"):
        assert name in message


def test_resolve_empty_string_raises_value_error() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_theme("", tty=True)
    message = str(exc_info.value)
    assert "non-empty" in message
    for name in ("default", "quiet", "mono"):
        assert name in message


def test_mono_theme_uses_empty_ansi_codes() -> None:
    mono = BUILTIN_THEMES["mono"]
    colors = mono.colors
    assert colors.heading == ""
    assert colors.label == ""
    assert colors.value == ""
    assert colors.note == ""
    assert colors.error == ""
    assert colors.reset == ""


def test_default_theme_has_ansi_prefix_with_escape_byte() -> None:
    default = BUILTIN_THEMES["default"]
    colors = default.colors
    assert colors.heading.startswith("\x1b[")
    assert colors.label.startswith("\x1b[")
    assert colors.note.startswith("\x1b[")
    assert colors.error.startswith("\x1b[")
    assert colors.reset == "\x1b[0m"


def test_style_with_empty_prefix_returns_input_unchanged() -> None:
    assert style("hello", "") == "hello"
    assert style("hello", "", "\x1b[0m") == "hello"


def test_style_wraps_with_reset_suffix() -> None:
    wrapped = style("hello", "\x1b[1;36m", "\x1b[0m")
    assert wrapped == "\x1b[1;36mhello\x1b[0m"


def test_list_theme_names_sorted_and_contains_all_three() -> None:
    names = list_theme_names()
    assert names == sorted(names)
    assert names == ["default", "mono", "quiet"]
