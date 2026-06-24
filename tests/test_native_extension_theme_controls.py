"""Extension `ctx.ui` theme controls (rich-UI item E).

Mirrors Pi's `ExtensionUIContext` theme surface (`theme` / `getAllThemes` /
`getTheme` / `setTheme`) through pipy's snake-cased `ui.theme` /
`get_all_themes` / `get_theme` / `set_theme`. Reads are ambient (work headless,
consulting the global theme registry + `PIPY_THEME`/store); only `set_theme`
mutates the live theme and so is gated on a live UI driver.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from pipy_harness.native import themes
from pipy_harness.native.extension_runtime import _CollectingUi
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.theme_files import build_theme_registry
from pipy_harness.native.themes import (
    DEFAULT_THEME_NAME,
    THEME_ENV_VAR,
    ChromePalette,
    available_theme_names,
    resolve_palette,
    set_active_theme_registry,
)


class _BaseDriver:
    """Untyped no-op `ExtensionUiDriver` stub (structural protocol match)."""

    def select(self, title, options):
        return None

    def input(self, title, placeholder=None):
        return None

    def editor(self, title, prefill=None):
        return None

    def confirm(self, title, message):
        return False

    def set_status(self, key, text):
        pass

    def set_working_message(self, message=None):
        pass

    def set_working_visible(self, visible):
        pass

    def set_widget(self, key, content, placement):
        pass

    def set_header(self, factory):
        pass

    def set_footer(self, factory):
        pass

    def set_title(self, title):
        pass

    def set_working_indicator(self, frames, interval_ms):
        pass

    def apply_theme(self, name):
        return True, None


class _ThemeDriver(_BaseDriver):
    """A live `ExtensionUiDriver` stub recording theme switches.

    `apply_theme` mirrors `_LiveExtensionUiDriver.apply_theme`: it sets
    `PIPY_THEME` so a real render would repaint, and returns `(ok, error)`.
    """

    def __init__(self, environ):
        self.environ = environ
        self.applied = []

    def apply_theme(self, name):
        self.applied.append(name)
        ok, message = themes.select_theme(name, environ=self.environ)
        return ok, None if ok else message


@pytest.fixture
def package_theme(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "themes"
    root.mkdir()
    (root / "neon.toml").write_text(
        'name = "neon"\naccent_truecolor = "38;2;7;7;7"\n', encoding="utf-8"
    )
    set_active_theme_registry(build_theme_registry([PackageRoot(root)]))
    try:
        yield "neon"
    finally:
        set_active_theme_registry(None)


@pytest.fixture(autouse=True)
def _isolated_theme_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Isolate ambient theme resolution from the developer/CI chrome store.

    `ctx.ui.theme`/`get_*`/`set_theme` resolve `PIPY_THEME` plus the persisted
    `NativeThemeStore`, so without isolation default-theme assertions would
    depend on (and could mutate) the real chrome store. Clearing the env var and
    pointing the store at a fresh, nonexistent tmp file makes resolution
    deterministically fall through to the built-in default.
    """
    monkeypatch.delenv(THEME_ENV_VAR, raising=False)
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))


# --- reads are ambient / deterministic (work headless) ---------------------


def test_theme_returns_active_palette_headless() -> None:
    ui = _CollectingUi(has_ui=False)
    palette = ui.theme
    assert isinstance(palette, ChromePalette)
    assert palette.name == DEFAULT_THEME_NAME


def test_theme_honors_env_override_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(THEME_ENV_VAR, "high-contrast")
    ui = _CollectingUi(has_ui=False)
    assert ui.theme.name == "high-contrast"


def test_get_all_themes_shape_and_order() -> None:
    ui = _CollectingUi(has_ui=False)
    entries = ui.get_all_themes()
    assert [e["name"] for e in entries] == list(available_theme_names())
    assert entries[0]["name"] == DEFAULT_THEME_NAME
    # path is intentionally None for every theme (name-only boundary).
    assert all(e["path"] is None for e in entries)


def test_get_all_themes_includes_package_theme(package_theme: str) -> None:
    ui = _CollectingUi(has_ui=False)
    names = [e["name"] for e in ui.get_all_themes()]
    assert package_theme in names
    assert names[0] == DEFAULT_THEME_NAME


def test_get_theme_known_and_unknown() -> None:
    ui = _CollectingUi(has_ui=False)
    palette = ui.get_theme("high-contrast")
    assert isinstance(palette, ChromePalette)
    assert palette.name == "high-contrast"
    assert ui.get_theme("does-not-exist") is None


def test_get_theme_does_not_switch_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(THEME_ENV_VAR, raising=False)
    ui = _CollectingUi(has_ui=False)
    ui.get_theme("high-contrast")
    assert ui.theme.name == DEFAULT_THEME_NAME


# --- set_theme: live (driver-gated) ----------------------------------------


def test_set_theme_live_success() -> None:
    environ: dict[str, str] = {}
    driver = _ThemeDriver(environ)
    ui = _CollectingUi(has_ui=True, ui_driver=driver)
    result = ui.set_theme("high-contrast")
    assert result == {"success": True, "error": None}
    assert driver.applied == ["high-contrast"]
    assert environ[THEME_ENV_VAR] == "high-contrast"


def test_set_theme_live_unknown_name() -> None:
    driver = _ThemeDriver({})
    ui = _CollectingUi(has_ui=True, ui_driver=driver)
    result = ui.set_theme("nope")
    assert result["success"] is False
    assert result["error"]


def test_set_theme_accepts_palette_object() -> None:
    environ: dict[str, str] = {}
    driver = _ThemeDriver(environ)
    ui = _CollectingUi(has_ui=True, ui_driver=driver)
    palette = resolve_palette("high-contrast")
    result = ui.set_theme(palette)
    assert result == {"success": True, "error": None}
    assert environ[THEME_ENV_VAR] == "high-contrast"


def test_set_theme_failsoft_when_driver_raises() -> None:
    class _Boom(_BaseDriver):
        def apply_theme(self, name):
            raise RuntimeError("boom")

    ui = _CollectingUi(has_ui=True, ui_driver=_Boom())
    result = ui.set_theme("high-contrast")
    assert result["success"] is False
    assert result["error"]


# --- set_theme: headless contract (Pi parity) ------------------------------


def test_set_theme_headless_no_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(THEME_ENV_VAR, raising=False)
    ui = _CollectingUi(has_ui=False)
    result = ui.set_theme("high-contrast")
    assert result == {"success": False, "error": "UI not available"}
    # No process-state mutation in the deterministic headless path.
    assert os.environ.get(THEME_ENV_VAR) is None


def test_set_theme_no_ui_even_with_driver() -> None:
    driver = _ThemeDriver({})
    ui = _CollectingUi(has_ui=False, ui_driver=driver)
    result = ui.set_theme("high-contrast")
    assert result == {"success": False, "error": "UI not available"}
    assert driver.applied == []
