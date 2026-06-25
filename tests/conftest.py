"""Shared pytest fixtures for the pipy test suite.

The native REPL chrome discovers global pipy resources under the user's
home/config directory (for example ``~/.pipy/AGENTS.md`` and
``~/.pipy/skills``) when rendering ``[Context]`` and ``[Skills]`` sections
in the startup chrome. To keep unit/integration tests deterministic, this
conftest reroutes the home directory to a per-test temporary path so test
runs don't pick up the developer's real pipy resources.

Tests that want to assert global-discovery behaviour create their own
home directory layout inside the fixture-provided tmp path.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_home(tmp_path_factory, monkeypatch):  # type: ignore[no-untyped-def]
    fake_home = tmp_path_factory.mktemp("isolated-home")
    # Only set HOME via the env so individual tests can still override it via
    # ``monkeypatch.setenv("HOME", ...)``. ``Path.home()`` reads ``HOME`` on
    # POSIX, so this keeps both the chrome's global resource discovery and
    # ``resolve_session_root()`` aligned with the per-test isolation.
    monkeypatch.setenv("HOME", str(fake_home))
    # Isolate the settings/config home too. The global config root resolves via
    # PIPY_CONFIG_HOME -> ${XDG_CONFIG_HOME}/pipy -> ~/.pipy (when present) ->
    # ~/.config/pipy, so a test that builds a session without injecting a
    # SettingsManager (and the first-run /changelog version write) must not touch
    # the developer's real config via an exported PIPY_CONFIG_HOME/XDG_CONFIG_HOME.
    # Clear those env overrides so resolution falls back to the isolated HOME;
    # tests that need a specific config home still set PIPY_CONFIG_HOME themselves.
    monkeypatch.delenv("PIPY_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return fake_home


@pytest.fixture(autouse=True)
def _isolated_chrome_theme(tmp_path_factory):  # type: ignore[no-untyped-def]
    """Pin chrome theme resolution to a deterministic, empty baseline per test.

    The theme picker (the ``/settings`` Theme row, via ``select_theme``)
    intentionally mutates ``os.environ["PIPY_THEME"]`` so the next chrome render
    (which re-reads the ambient env) picks up the new palette. ``monkeypatch``
    cannot undo a value *set* during a test when the key was originally absent,
    so a test that switches the theme would otherwise leak the selected palette
    into every later test's rendered ANSI. Snapshot and restore the relevant env
    vars around each test to contain that.

    Snapshotting alone is not enough for default-theme assertions, though:
    ``resolve_active_theme_name`` consults ``PIPY_THEME``, then the persisted
    ``NativeThemeStore``, then the built-in default, and extension ``ctx.ui``
    theme reads go through that same path. An exported ``PIPY_THEME`` or
    ``PIPY_NATIVE_THEME_PATH`` (or a developer/CI store under ``$HOME``) would
    therefore make "resolves to the default theme" flaky and let a test mutate
    real chrome state. So clear ``PIPY_THEME`` and redirect the store at a fresh,
    nonexistent tmp file: resolution deterministically falls through to the
    default and nothing persists. A test that needs a specific theme/store sets
    these itself — its ``monkeypatch`` runs after this autouse fixture and wins.
    """

    saved = {
        key: os.environ.get(key)
        for key in ("PIPY_THEME", "PIPY_NATIVE_THEME_PATH")
    }
    os.environ.pop("PIPY_THEME", None)
    fresh_store = tmp_path_factory.mktemp("native-theme") / "native-theme.json"
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(fresh_store)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
