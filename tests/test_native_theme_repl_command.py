"""Parity row D7: the ``/theme`` command consumed by the real REPL chrome.

Drives the no-tool REPL product path with a TTY-like error stream and proves
the ``/theme`` command swaps the rendered chrome palette mid-session, lists the
registered themes, fails closed on an unknown name, and never overrides the
NO_COLOR / non-TTY fallback.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.themes import resolve_palette
from pipy_harness.runner import HarnessRunner

_PI_SEPARATOR = resolve_palette("pi").separator_truecolor
_OCEAN_SEPARATOR = resolve_palette("ocean").separator_truecolor


class _TTYStringIO(io.StringIO):
    def isatty(self) -> bool:  # noqa: D401 - test stub
        return True


def _run(
    script: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tty: bool,
    no_color: bool = False,
) -> str:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.delenv("PIPY_THEME", raising=False)
    # Color tests must be robust to an ambient NO_COLOR=1 in the developer's
    # environment (chrome disables ANSI whenever NO_COLOR is present), so clear
    # it by default. The fallback test opts back in via ``no_color=True``.
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    error_stream: io.StringIO = _TTYStringIO() if tty else io.StringIO()
    adapter = PipyNativeReplAdapter(
        provider=FakeNativeProvider(),
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="d7-unit",
            command=[],
            cwd=tmp_path,
            goal="d7 unit",
            root=tmp_path / "archive",
            capture_policy=CapturePolicy(),
        )
    )
    return error_stream.getvalue()


def test_theme_switch_changes_rendered_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _run("/theme ocean\n/exit\n", tmp_path, monkeypatch, tty=True)
    assert "selected theme ocean" in stderr
    # The separator before the switch is the default pi palette; the one after
    # the switch is the freshly selected ocean palette.
    assert _PI_SEPARATOR in stderr
    assert _OCEAN_SEPARATOR in stderr


def test_theme_status_lists_registered_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _run("/theme\n/exit\n", tmp_path, monkeypatch, tty=True)
    assert "active: pi" in stderr
    assert "ocean" in stderr
    assert "high-contrast" in stderr


def test_unknown_theme_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _run("/theme nope\n/exit\n", tmp_path, monkeypatch, tty=True)
    assert "unknown theme 'nope'" in stderr
    # A rejected theme never injects the ocean palette.
    assert _OCEAN_SEPARATOR not in stderr


def test_theme_never_overrides_no_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _run(
        "/theme ocean\n/exit\n", tmp_path, monkeypatch, tty=True, no_color=True
    )
    assert "selected theme ocean" in stderr
    # NO_COLOR wins: no ANSI styling regardless of the selected theme.
    assert "\x1b[" not in stderr


def test_theme_non_tty_is_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _run("/theme ocean\n/exit\n", tmp_path, monkeypatch, tty=False)
    assert "\x1b[" not in stderr
