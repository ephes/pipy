"""Focused tests for :mod:`pipy_harness.native.terminal_driver`.

Covers the terminal I/O ownership boundary extracted in Slice 4.2: the
error-swallowing write/flush sink, the termios raw-mode lifecycle,
bracketed-paste toggling, and the terminal-title OSC push/write/restore.

Includes the explicit characterization the migration requires of the
raw-mode typeahead policy: ``tty.setraw`` transitions with the standard
``termios.TCSAFLUSH`` ``when``, which discards input queued before the
switch. The extraction preserves that flush unchanged, so consumers
synchronize on prompt readiness rather than on bytes typed ahead of the
transition.
"""

from __future__ import annotations

import inspect
import termios
import tty
from typing import Any, TextIO, cast

import pytest

from pipy_harness.native import terminal_driver
from pipy_harness.native.terminal_driver import (
    _BRACKETED_PASTE_DISABLE,
    _BRACKETED_PASTE_ENABLE,
    _TITLE_MAX_CHARS,
    TerminalDriver,
)


class _RecordingTerminal:
    """Fake terminal stream that records writes/flushes and can raise."""

    def __init__(self, *, isatty: bool = True, fail: bool = False) -> None:
        self.chunks: list[str] = []
        self.flushes = 0
        self._isatty = isatty
        self._fail = fail

    def write(self, text: str) -> int:
        if self._fail:
            raise OSError("stream closed")
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        if self._fail:
            raise OSError("stream closed")
        self.flushes += 1

    def isatty(self) -> bool:
        return self._isatty

    @property
    def value(self) -> str:
        return "".join(self.chunks)


class _FakeInput:
    def __init__(self, fd: int = 7) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


def _driver(
    *, isatty: bool = True, fail: bool = False, fd: int = 7
) -> tuple[TerminalDriver, _RecordingTerminal]:
    terminal = _RecordingTerminal(isatty=isatty, fail=fail)
    driver = TerminalDriver(
        cast(TextIO, _FakeInput(fd)), cast(TextIO, terminal)
    )
    return driver, terminal


# --- write/flush sink -------------------------------------------------------


def test_write_writes_then_flushes_and_reports_success() -> None:
    driver, terminal = _driver()
    assert driver.write("hello") is True
    assert terminal.value == "hello"
    assert terminal.flushes == 1


def test_write_swallows_errors_and_reports_failure() -> None:
    driver, terminal = _driver(fail=True)
    assert driver.write("hello") is False


def test_write_deferred_writes_without_flushing() -> None:
    """The screen-clear write must NOT flush on its own.

    ``_force_full_redraw`` and ``_repaint_after_resize`` emit
    ``\\x1b[2J\\x1b[H`` and then paint; the pre-extraction code deferred the
    clear's transmission so it coalesced with the flush of the following
    paint. ``write_deferred`` preserves that: it writes the bytes but leaves
    the flush to the caller's next frame, so no separate flush reaches the
    terminal between the clear and the redraw.
    """

    driver, terminal = _driver()
    assert driver.write_deferred("\x1b[2J\x1b[H") is True
    assert terminal.value == "\x1b[2J\x1b[H"
    assert terminal.flushes == 0
    # A subsequent flushing write coalesces both into a single flush, exactly
    # as the deferred clear + following paint did before the extraction.
    assert driver.write("frame") is True
    assert terminal.value == "\x1b[2J\x1b[Hframe"
    assert terminal.flushes == 1


def test_write_deferred_swallows_errors_and_reports_failure() -> None:
    driver, terminal = _driver(fail=True)
    assert driver.write_deferred("\x1b[2J\x1b[H") is False


# --- raw-mode lifecycle + typeahead policy ----------------------------------


def test_enter_raw_mode_uses_tcsaflush_typeahead_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly characterize the preserved raw-mode typeahead policy.

    ``enter_raw_mode`` calls ``tty.setraw(fd)`` with no explicit ``when``,
    relying on the standard-library default ``termios.TCSAFLUSH`` which
    discards input queued before the raw-mode switch. This test pins both
    that default and the driver's reliance on it; consumers synchronize on a
    fresh prompt rather than on typed-ahead bytes.
    """

    # The stdlib default `when` is the flushing TCSAFLUSH.
    assert (
        inspect.signature(tty.setraw).parameters["when"].default
        == termios.TCSAFLUSH
    )

    captured: dict[str, Any] = {}

    def fake_setraw(fd: int, when: int = termios.TCSAFLUSH) -> None:
        captured["fd"] = fd
        captured["when"] = when

    monkeypatch.setattr(terminal_driver.termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(terminal_driver.tty, "setraw", fake_setraw)

    driver, terminal = _driver(fd=11)
    driver.enter_raw_mode()

    # The driver passes only the fd, so the flushing default applies.
    assert captured["fd"] == 11
    assert captured["when"] == termios.TCSAFLUSH
    # Entering raw mode enables bracketed paste.
    assert terminal.value == _BRACKETED_PASTE_ENABLE


def test_enter_raw_mode_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(terminal_driver.termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(
        terminal_driver.tty, "setraw", lambda fd: calls.append(fd)
    )
    driver, _terminal = _driver()
    driver.enter_raw_mode()
    driver.enter_raw_mode()
    assert calls == [7]


def test_restore_terminal_mode_disables_paste_and_restores_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        terminal_driver.termios, "tcgetattr", lambda fd: "SAVED-ATTRS"
    )
    monkeypatch.setattr(terminal_driver.tty, "setraw", lambda fd: None)
    restored: dict[str, Any] = {}

    def fake_tcsetattr(fd: int, when: int, attrs: object) -> None:
        restored["fd"] = fd
        restored["when"] = when
        restored["attrs"] = attrs

    monkeypatch.setattr(terminal_driver.termios, "tcsetattr", fake_tcsetattr)

    driver, terminal = _driver(fd=9)
    driver.enter_raw_mode()
    terminal.chunks.clear()
    driver.restore_terminal_mode()

    assert restored == {
        "fd": 9,
        "when": termios.TCSADRAIN,
        "attrs": "SAVED-ATTRS",
    }
    # Restoring disables bracketed paste.
    assert terminal.value == _BRACKETED_PASTE_DISABLE


def test_restore_terminal_mode_noop_when_not_raw() -> None:
    driver, terminal = _driver()
    driver.restore_terminal_mode()
    assert terminal.value == ""


# --- bracketed-paste toggle -------------------------------------------------


def test_set_bracketed_paste_toggles_once_per_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, terminal = _driver()
    driver._set_bracketed_paste(True)
    driver._set_bracketed_paste(True)
    driver._set_bracketed_paste(False)
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE


# --- terminal-title OSC -----------------------------------------------------


def test_write_title_emits_osc_and_sanitizes() -> None:
    driver, terminal = _driver()
    driver.write_title("build\x07\x1b[31mred")
    # Control bytes (BEL, ESC) are stripped to spaces before the OSC 0 write.
    assert terminal.value == "\x1b]0;build  [31mred\x07"


def test_write_title_noop_for_non_tty() -> None:
    driver, terminal = _driver(isatty=False)
    driver.write_title("build")
    assert terminal.value == ""


def test_write_title_caps_length() -> None:
    driver, terminal = _driver()
    driver.write_title("x" * (_TITLE_MAX_CHARS + 50))
    body = terminal.value[len("\x1b]0;") : -len("\x07")]
    assert len(body) == _TITLE_MAX_CHARS


def test_push_title_is_idempotent_and_restore_pops() -> None:
    driver, terminal = _driver()
    driver.push_title()
    driver.push_title()
    assert terminal.value == "\x1b[22;2t"
    terminal.chunks.clear()
    driver.restore_title()
    assert terminal.value == "\x1b[23;2t"


def test_restore_title_noop_without_push() -> None:
    driver, terminal = _driver()
    driver.restore_title()
    assert terminal.value == ""


def test_title_ops_noop_for_non_tty() -> None:
    driver, terminal = _driver(isatty=False)
    driver.push_title()
    driver.restore_title()
    assert terminal.value == ""
