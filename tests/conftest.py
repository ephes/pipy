"""Shared pytest fixtures for the pipy test suite.

The native REPL chrome now discovers global resources under the user's
home directory (``~/.claude/skills``, ``~/.claude/CLAUDE.md``, …) when
rendering ``[Context]`` and ``[Skills]`` sections in the startup chrome.
To keep unit/integration tests deterministic, this conftest reroutes the
home directory to a per-test temporary path so test runs don't pick up
the developer's real Claude/Codex resources.

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
def _isolated_chrome_theme():  # type: ignore[no-untyped-def]
    """Keep the chrome theme selection from leaking across tests.

    The ``/theme`` command intentionally mutates ``os.environ["PIPY_THEME"]``
    so the next chrome render (which re-reads the ambient env) picks up the new
    palette. ``monkeypatch`` cannot undo a value *set* during a test when the
    key was originally absent, so a test that drives ``/theme`` would otherwise
    leak the selected palette into every later test's rendered ANSI. Snapshot
    and restore the relevant env vars around each test to contain that.
    """

    saved = {
        key: os.environ.get(key)
        for key in ("PIPY_THEME", "PIPY_NATIVE_THEME_PATH")
    }
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
