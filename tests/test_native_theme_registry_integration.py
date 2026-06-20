"""The ambient theme functions consult the active package theme registry.

Once a session installs a `ThemeRegistry` built from package theme roots
via `set_active_theme_registry`, the no-argument theme functions
(`available_theme_names`, `is_known_theme`, `resolve_palette`) and the
chrome render path see package-contributed themes, so the `/settings` theme
picker can select one and `chrome_style_for` re-colors with its palette.
Resetting the registry restores the built-in-only behavior.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from pipy_harness.native import themes
from pipy_harness.native.chrome import chrome_style_for
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.theme_files import build_theme_registry
from pipy_harness.native.themes import (
    DEFAULT_THEME_NAME,
    available_theme_names,
    is_known_theme,
    resolve_palette,
    select_theme,
    set_active_theme_registry,
)


@pytest.fixture
def package_theme(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "themes"
    root.mkdir()
    (root / "neon.toml").write_text(
        'name = "neon"\naccent_truecolor = "38;2;7;7;7"\n', encoding="utf-8"
    )
    registry = build_theme_registry([PackageRoot(root)])
    set_active_theme_registry(registry)
    try:
        yield "neon"
    finally:
        set_active_theme_registry(None)


def test_is_known_theme_sees_package_theme(package_theme: str) -> None:
    assert is_known_theme("neon")
    assert is_known_theme(DEFAULT_THEME_NAME)


def test_available_theme_names_includes_package_theme(package_theme: str) -> None:
    names = available_theme_names()
    assert "neon" in names
    assert names[0] == DEFAULT_THEME_NAME


def test_resolve_palette_returns_package_palette(package_theme: str) -> None:
    assert resolve_palette("neon").accent_truecolor == "38;2;7;7;7"


def test_after_reset_package_theme_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    root.mkdir()
    (root / "neon.toml").write_text('name = "neon"\n', encoding="utf-8")
    set_active_theme_registry(build_theme_registry([PackageRoot(root)]))
    set_active_theme_registry(None)

    assert not is_known_theme("neon")


def test_select_theme_accepts_package_theme(package_theme: str) -> None:
    environ: dict[str, str] = {}
    ok, _message = select_theme("neon", environ=environ, store=None)

    assert ok
    assert environ[themes.THEME_ENV_VAR] == "neon"


def test_chrome_style_renders_package_palette(
    package_theme: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(themes.THEME_ENV_VAR, "neon")
    style = chrome_style_for(io.StringIO())

    assert style.palette.name == "neon"
    assert style.palette.accent_truecolor == "38;2;7;7;7"
