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
import shutil
import signal
import termios
import tty
from typing import Any, TextIO, cast

import pytest

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

    monkeypatch.setattr(termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(tty, "setraw", fake_setraw)

    driver, terminal = _driver(fd=11)
    driver.enter_raw_mode()

    # The driver passes only the fd, so the flushing default applies.
    assert captured["fd"] == 11
    assert captured["when"] == termios.TCSAFLUSH
    # Entering raw mode enables bracketed paste.
    assert terminal.value == _BRACKETED_PASTE_ENABLE


def test_nested_raw_mode_ownership_restores_only_after_outer_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_calls: list[int] = []
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda fd: raw_calls.append(fd))
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver()

    driver.enter_raw_mode()
    driver.enter_raw_mode()
    assert raw_calls == [7]
    assert terminal.value == _BRACKETED_PASTE_ENABLE

    driver.restore_terminal_mode()
    assert restore_calls == []
    assert terminal.value == _BRACKETED_PASTE_ENABLE

    driver.restore_terminal_mode()
    assert restore_calls == [(7, termios.TCSADRAIN, "saved")]
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE


def test_temporary_suspension_preserves_nested_owners_and_tcsaflush_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_calls: list[tuple[int, int]] = []
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")

    def fake_setraw(fd: int, when: int = termios.TCSAFLUSH) -> None:
        raw_calls.append((fd, when))

    monkeypatch.setattr(tty, "setraw", fake_setraw)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver(fd=17)

    driver.enter_raw_mode()
    driver.enter_raw_mode()
    driver.suspend_terminal_mode()

    assert driver._raw_mode_depth == 2
    assert driver._terminal_mode_suspend_depth == 1
    assert raw_calls == [(17, termios.TCSAFLUSH)]
    assert restore_calls == [(17, termios.TCSADRAIN, "saved")]
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE

    # A nested foreign consumer cannot resume raw mode under its outer owner.
    driver.suspend_terminal_mode()
    driver.resume_terminal_mode()
    assert driver._terminal_mode_suspend_depth == 1
    assert raw_calls == [(17, termios.TCSAFLUSH)]

    assert driver.resume_terminal_mode() is True
    with pytest.raises(RuntimeError, match="suspension is not active"):
        driver.resume_terminal_mode()
    assert driver._raw_mode_depth == 2
    assert driver._terminal_mode_suspend_depth == 0
    assert raw_calls == [
        (17, termios.TCSAFLUSH),
        (17, termios.TCSAFLUSH),
    ]
    assert terminal.value == (
        _BRACKETED_PASTE_ENABLE
        + _BRACKETED_PASTE_DISABLE
        + _BRACKETED_PASTE_ENABLE
    )

    driver.restore_terminal_mode()
    assert restore_calls == [(17, termios.TCSADRAIN, "saved")]
    driver.restore_terminal_mode()
    assert restore_calls == [
        (17, termios.TCSADRAIN, "saved"),
        (17, termios.TCSADRAIN, "saved"),
    ]
    assert driver._old_termios is None
    assert terminal.value.endswith(_BRACKETED_PASTE_DISABLE)


def test_raw_mode_scope_failed_nested_entry_does_not_release_outer_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, _terminal = _driver()

    driver.enter_raw_mode()
    driver.suspend_terminal_mode()
    with pytest.raises(RuntimeError, match="while terminal I/O is suspended"):
        with driver.raw_mode():
            raise AssertionError("failed entry must not yield")

    assert driver._raw_mode_depth == 1
    assert driver._terminal_mode_suspend_depth == 1
    assert restore_calls == [(7, termios.TCSADRAIN, "saved")]

    assert driver.resume_terminal_mode() is True
    driver.restore_terminal_mode()
    assert restore_calls == [
        (7, termios.TCSADRAIN, "saved"),
        (7, termios.TCSADRAIN, "saved"),
    ]


def test_suspension_without_raw_owner_blocks_entry_and_pairs_nested_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_calls: list[int] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda fd: raw_calls.append(fd))
    driver, _terminal = _driver()

    driver.suspend_terminal_mode()
    driver.suspend_terminal_mode()
    assert driver._raw_mode_depth == 0
    assert driver._terminal_mode_suspend_depth == 2
    with pytest.raises(RuntimeError, match="while terminal I/O is suspended"):
        driver.enter_raw_mode()
    assert raw_calls == []

    assert driver.resume_terminal_mode() is False
    assert driver._terminal_mode_suspend_depth == 1
    assert driver.resume_terminal_mode() is True
    assert driver._terminal_mode_suspend_depth == 0

    driver.enter_raw_mode()
    assert raw_calls == [7]


def test_failed_suspend_does_not_publish_cooked_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls = 0
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)

    def fail_restore(_fd: int, _when: int, _attrs: object) -> None:
        nonlocal restore_calls
        restore_calls += 1
        if restore_calls == 1:
            raise OSError("cooked transition failed")

    monkeypatch.setattr(termios, "tcsetattr", fail_restore)
    driver, terminal = _driver()
    driver.enter_raw_mode()

    with pytest.raises(OSError, match="cooked transition failed"):
        driver.suspend_terminal_mode()

    assert driver._raw_mode_depth == 1
    assert driver._terminal_mode_suspend_depth == 0
    assert driver._bracketed_paste_active is True
    assert terminal.value == (
        _BRACKETED_PASTE_ENABLE
        + _BRACKETED_PASTE_DISABLE
        + _BRACKETED_PASTE_ENABLE
    )


def test_raw_mode_scope_failed_physical_entry_does_not_release_an_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")

    def fail_raw(_fd: int) -> None:
        raise termios.error("raw transition failed")

    monkeypatch.setattr(tty, "setraw", fail_raw)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver(fd=29)

    with pytest.raises(termios.error, match="raw transition failed"):
        with driver.raw_mode():
            raise AssertionError("failed entry must not yield")

    assert driver._raw_mode_depth == 0
    assert driver._terminal_mode_suspend_depth == 0
    assert driver._old_termios is None
    assert restore_calls == [(29, termios.TCSADRAIN, "saved")]
    assert terminal.value == ""


def test_failed_initial_raw_entry_restores_cooked_state_without_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")

    def fail_raw(_fd: int) -> None:
        raise OSError("raw transition failed")

    monkeypatch.setattr(tty, "setraw", fail_raw)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver(fd=23)

    with pytest.raises(OSError, match="raw transition failed"):
        driver.enter_raw_mode()

    assert driver._raw_mode_depth == 0
    assert driver._terminal_mode_suspend_depth == 0
    assert driver._old_termios is None
    assert restore_calls == [(23, termios.TCSADRAIN, "saved")]
    assert terminal.value == ""


def test_failed_resume_stays_suspended_until_forced_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    raw_calls = 0
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")

    def fail_second_setraw(_fd: int, when: int = termios.TCSAFLUSH) -> None:
        nonlocal raw_calls
        del when
        raw_calls += 1
        if raw_calls == 2:
            raise OSError("raw transition failed")

    monkeypatch.setattr(tty, "setraw", fail_second_setraw)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver(fd=19)

    driver.enter_raw_mode()
    driver.enter_raw_mode()
    driver.suspend_terminal_mode()
    with pytest.raises(OSError, match="raw transition failed"):
        driver.resume_terminal_mode()

    assert driver._raw_mode_depth == 2
    assert driver._terminal_mode_suspend_depth == 1
    assert driver._bracketed_paste_active is False
    driver.force_restore_terminal_mode()
    driver.force_restore_terminal_mode()

    assert driver._raw_mode_depth == 0
    assert driver._terminal_mode_suspend_depth == 0
    assert driver._old_termios is None
    # Suspension, failed-resume rollback, and one forced close recovery each
    # restore the saved attrs; repeated recovery is a no-op.
    assert restore_calls == [
        (19, termios.TCSADRAIN, "saved"),
        (19, termios.TCSADRAIN, "saved"),
        (19, termios.TCSADRAIN, "saved"),
    ]
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE


def test_restore_terminal_mode_disables_paste_and_restores_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        termios, "tcgetattr", lambda fd: "SAVED-ATTRS"
    )
    monkeypatch.setattr(tty, "setraw", lambda fd: None)
    restored: dict[str, Any] = {}

    def fake_tcsetattr(fd: int, when: int, attrs: object) -> None:
        restored["fd"] = fd
        restored["when"] = when
        restored["attrs"] = attrs

    monkeypatch.setattr(termios, "tcsetattr", fake_tcsetattr)

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


def test_force_restore_terminal_mode_recovers_unbalanced_owners_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    driver, terminal = _driver(fd=13)

    driver.enter_raw_mode()
    driver.enter_raw_mode()
    driver.force_restore_terminal_mode()

    assert driver._raw_mode_depth == 0
    assert driver._terminal_mode_suspend_depth == 0
    assert driver._old_termios is None
    assert driver._bracketed_paste_active is False
    assert restore_calls == [(13, termios.TCSADRAIN, "saved")]
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE

    driver.force_restore_terminal_mode()
    driver.restore_terminal_mode()
    assert restore_calls == [(13, termios.TCSADRAIN, "saved")]
    assert terminal.value == _BRACKETED_PASTE_ENABLE + _BRACKETED_PASTE_DISABLE


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


# --- input read primitives + key decoder ------------------------------------


def _decode(driver: TerminalDriver, data: bytes) -> str | None:
    """Feed ``data`` through the driver's decoder over a real OS pipe fd."""

    import os

    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    try:
        return driver.read_key(read_fd)
    finally:
        os.close(read_fd)


def test_read_key_decodes_named_and_control_keys() -> None:
    driver, _ = _driver()
    assert _decode(driver, b"\r") == "enter"
    assert _decode(driver, b"\t") == "tab"
    assert _decode(driver, b"\x7f") == "backspace"
    assert _decode(driver, b"\x03") == "ctrl-c"
    assert _decode(driver, b"\x04") == "ctrl-d"
    assert _decode(driver, b"\x10") == "ctrl-p"
    # An unaliased C0 control byte decodes to its ctrl-<letter> name.
    assert _decode(driver, b"\x02") == "ctrl-b"
    assert _decode(driver, b"a") == "a"


def test_read_key_returns_none_on_eof() -> None:
    import os

    driver, _ = _driver()
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        assert driver.read_key(read_fd) is None
    finally:
        os.close(read_fd)


def test_read_key_decodes_complete_utf8_scalar() -> None:
    driver, _ = _driver()
    assert _decode(driver, "ö".encode()) == "ö"


def test_read_key_decodes_escape_arrows_and_modified_keys() -> None:
    driver, _ = _driver()
    assert _decode(driver, b"\x1b[A") == "up"
    assert _decode(driver, b"\x1b[D") == "left"
    assert _decode(driver, b"\x1b[Z") == "shift-tab"
    assert _decode(driver, b"\x1b[112;6u") == "shift-ctrl-p"
    assert _decode(driver, b"\x1b[1;3A") == "alt-up"
    assert _decode(driver, b"\x1b") == "esc"


def test_read_key_paste_body_normalizes_and_is_consumed() -> None:
    driver, _ = _driver()
    assert _decode(driver, b"\x1b[200~a\r\nb\rc\x1b[201~") == "paste"
    # The body is held for a single hand-off and cleared on consume.
    assert driver.consume_paste() == "a\nb\nc"
    assert driver.consume_paste() == ""


def test_read_key_if_available_reads_pending_byte_without_fd_activity() -> None:
    import os

    driver, _ = _driver()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\xc3(")
    try:
        # The malformed lead byte over-reads and pushes back the stray "(",
        # so a decoded scalar is waiting even though the fd is now idle.
        assert driver.read_key(read_fd) == "�"
        assert driver.has_pending_input() is True
        assert driver.read_key_if_available(read_fd, 0.0) == "("
        assert driver.has_pending_input() is False
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_read_key_if_available_returns_none_when_idle() -> None:
    import os

    driver, _ = _driver()
    read_fd, write_fd = os.pipe()
    try:
        assert driver.read_key_if_available(read_fd, 0.0) is None
    finally:
        os.close(write_fd)
        os.close(read_fd)


# --- resize lifecycle + size resolution -------------------------------------


def test_take_resize_pending_reports_and_clears_flag() -> None:
    driver, _ = _driver()
    assert driver.take_resize_pending() is False
    driver._on_resize_signal(28, None)  # SIGWINCH-style flag flip
    assert driver._resize_pending is True
    assert driver.take_resize_pending() is True
    # Draining clears the flag so the next poll does not repaint again.
    assert driver._resize_pending is False
    assert driver.take_resize_pending() is False


def test_install_and_remove_resize_handler_restore_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, Any]] = []
    sentinel = object()

    def fake_signal(signum: int, handler: Any) -> Any:
        calls.append((signum, handler))
        return sentinel

    monkeypatch.setattr(signal, "signal", fake_signal)
    driver, _ = _driver()
    driver.install_resize_handler()
    assert calls[0][0] == signal.SIGWINCH
    assert calls[0][1] == driver._on_resize_signal
    assert driver._prev_winch_handler is sentinel
    driver.remove_resize_handler()
    # The previously-saved disposition is restored, then forgotten.
    assert calls[-1] == (signal.SIGWINCH, sentinel)
    assert driver._prev_winch_handler is None


def test_install_resize_handler_off_main_thread_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_signal(signum: int, handler: Any) -> Any:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", raising_signal)
    driver, _ = _driver()
    driver.install_resize_handler()  # must not raise
    assert driver._prev_winch_handler is None
    driver.remove_resize_handler()  # no-op, must not raise


def test_size_honors_explicit_override_and_clamps_to_floor() -> None:
    driver, _ = _driver()
    assert driver.size(width=100, height=40) == (100, 40)
    # Both dimensions clamp up to the layout floors.
    assert driver.size(width=10, height=2) == (60, 12)


def test_size_prefers_env_columns_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "133")
    monkeypatch.setenv("LINES", "47")
    driver, _ = _driver(isatty=True)
    assert driver.size() == (133, 47)


def test_size_falls_back_to_shutil_when_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os as _os

    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.setattr(
        shutil,
        "get_terminal_size",
        lambda *a, **k: _os.terminal_size((90, 30)),
    )
    driver, _ = _driver(isatty=True)
    assert driver.size() == (90, 30)


def test_size_returns_defaults_for_non_tty_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-TTY capture stream never resolves a live size, so COLUMNS/LINES do
    # not leak into captured-stream rendering: the caller's defaults win.
    monkeypatch.setenv("COLUMNS", "133")
    monkeypatch.setenv("LINES", "47")
    driver, _ = _driver(isatty=False)
    assert driver.size() == (88, 24)
