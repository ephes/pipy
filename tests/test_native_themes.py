"""Parity row D7: theme / color-scheme selection consumed by the chrome.

Covers the pure theme registry/store/resolution in ``themes.py`` and its
consumption by ``chrome_style_for``: a selected theme changes the rendered
ANSI styling, unknown names fail safe to the default, and the NO_COLOR / non
-TTY fallback always wins (plain text regardless of theme).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from pipy_harness.cli import _apply_settings_theme_env
from pipy_harness.native.chrome import ChromeStyle, chrome_style_for
from pipy_harness.native.themes import (
    DEFAULT_THEME_NAME,
    NativeThemeStore,
    THEME_ENV_VAR,
    available_theme_names,
    resolve_active_theme_name,
    resolve_palette,
)


class _TTYStream(io.StringIO):
    def isatty(self) -> bool:  # noqa: D401 - test stub
        return True


def test_registry_has_default_and_distinct_alternates() -> None:
    names = available_theme_names()
    assert DEFAULT_THEME_NAME in names
    assert len(names) >= 2
    palettes = {name: resolve_palette(name) for name in names}
    # Every theme is a distinct palette (no accidental duplicates).
    assert len({p.title_truecolor for p in palettes.values()}) >= 2


def test_unknown_theme_falls_back_to_default() -> None:
    assert resolve_palette("does-not-exist") == resolve_palette(DEFAULT_THEME_NAME)


def test_selected_theme_changes_rendered_styling() -> None:
    default_palette = resolve_palette(DEFAULT_THEME_NAME)
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    alt_palette = resolve_palette(alternates[0])
    default_style = ChromeStyle(enabled=True, truecolor=True, palette=default_palette)
    alt_style = ChromeStyle(enabled=True, truecolor=True, palette=alt_palette)
    text = "─" * 8
    # Same UI element, different theme -> different rendered ANSI styling.
    assert default_style.separator(text) != alt_style.separator(text)
    # The alternate's separator carries its own palette code.
    assert alt_palette.separator_truecolor in alt_style.separator(text)


def test_no_color_fallback_ignores_theme() -> None:
    text = "─" * 8
    default_plain = ChromeStyle(
        enabled=False, truecolor=False, palette=resolve_palette(DEFAULT_THEME_NAME)
    )
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    alt_plain = ChromeStyle(
        enabled=False, truecolor=False, palette=resolve_palette(alternates[0])
    )
    # Plain (disabled) output is identical and ANSI-free regardless of theme.
    assert default_plain.separator(text) == alt_plain.separator(text) == text
    assert "\x1b[" not in alt_plain.title("pipy")


def test_chrome_style_for_resolves_env_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    monkeypatch.setenv("PIPY_THEME", alternates[0])
    style = chrome_style_for(_TTYStream())
    assert style.enabled
    assert style.truecolor
    assert style.palette == resolve_palette(alternates[0])


def test_settings_theme_pi_renders_pi_yellow_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a stale non-pi settings theme turns section labels blue."""

    monkeypatch.delenv(THEME_ENV_VAR, raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")

    _apply_settings_theme_env({"theme": DEFAULT_THEME_NAME})
    style = chrome_style_for(_TTYStream())

    assert os.environ[THEME_ENV_VAR] == DEFAULT_THEME_NAME
    assert style.section_label("[Context]") == "\x1b[38;2;240;198;116m[Context]\x1b[0m"


def test_chrome_style_for_uses_fallback_for_plain_256color_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    style = chrome_style_for(_TTYStream())

    assert style.enabled
    assert style.truecolor is False
    assert style.title("pipy") == "\x1b[38;5;109mpipy\x1b[0m"
    assert style.section_label("[Context]") == "\x1b[38;5;222m[Context]\x1b[0m"
    assert style.dim("details") == "\x1b[38;5;241mdetails\x1b[0m"
    assert style.secondary_dim("more") == "\x1b[38;5;244mmore\x1b[0m"
    assert style.separator("──") == "\x1b[38;5;139m──\x1b[0m"
    assert style.user_message("hi", width=4).startswith("\x1b[48;5;237m")
    assert style.tool_command("$ ls", width=4).startswith("\x1b[48;5;235m")


def test_chrome_style_for_uses_truecolor_when_explicitly_advertised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "screen-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")

    style = chrome_style_for(_TTYStream())

    assert style.enabled
    assert style.truecolor
    assert style.separator("──") == "\x1b[38;2;178;148;187m──\x1b[0m"
    assert style.user_message("hi", width=4).startswith("\x1b[48;2;52;53;65m")
    assert style.tool_command("$ ls", width=4).startswith("\x1b[48;2;40;50;40m")


def test_chrome_style_for_uses_truecolor_for_direct_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm-direct")

    style = chrome_style_for(_TTYStream())

    assert style.enabled
    assert style.truecolor
    assert style.separator("──") == "\x1b[38;2;178;148;187m──\x1b[0m"


def test_chrome_style_for_no_color_forces_plain_even_with_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    monkeypatch.setenv("PIPY_THEME", alternates[0])
    style = chrome_style_for(_TTYStream())
    assert style.enabled is False
    assert style.separator("──") == "──"


def test_chrome_style_for_non_tty_is_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    monkeypatch.setenv("PIPY_THEME", alternates[0])
    style = chrome_style_for(io.StringIO())  # not a TTY
    assert style.enabled is False
    assert style.title("pipy") == "pipy"


def test_theme_store_round_trip(tmp_path: Path) -> None:
    store = NativeThemeStore(path=tmp_path / "theme.json")
    assert store.load() is None
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    store.save(alternates[0])
    assert store.load() == alternates[0]


def test_theme_store_rejects_unknown_name(tmp_path: Path) -> None:
    store = NativeThemeStore(path=tmp_path / "theme.json")
    with pytest.raises(ValueError):
        store.save("not-a-theme")


def test_resolve_active_theme_prefers_env_over_store(tmp_path: Path) -> None:
    store = NativeThemeStore(path=tmp_path / "theme.json")
    alternates = [n for n in available_theme_names() if n != DEFAULT_THEME_NAME]
    store.save(alternates[0])
    # Explicit env wins over the persisted store.
    assert (
        resolve_active_theme_name(env={"PIPY_THEME": DEFAULT_THEME_NAME}, store=store)
        == DEFAULT_THEME_NAME
    )
    # With no env override, the store value is used.
    assert resolve_active_theme_name(env={}, store=store) == alternates[0]
    # Unknown env value falls back to the default.
    assert (
        resolve_active_theme_name(env={"PIPY_THEME": "bogus"}, store=store)
        == alternates[0]
    )
