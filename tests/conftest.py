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

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_home(tmp_path_factory, monkeypatch):  # type: ignore[no-untyped-def]
    fake_home = tmp_path_factory.mktemp("isolated-home")
    # Only set HOME via the env so individual tests can still override it via
    # ``monkeypatch.setenv("HOME", ...)``. ``Path.home()`` reads ``HOME`` on
    # POSIX, so this keeps both the chrome's global resource discovery and
    # ``resolve_session_root()`` aligned with the per-test isolation.
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home
