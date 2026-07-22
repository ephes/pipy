"""Terminal output, raw-mode, and restoration driver for the native TUI.

``TerminalDriver`` owns the low-level terminal I/O for
:class:`~pipy_harness.native.tui.ToolLoopTerminalUi`: the error-swallowing
write/flush sink (with a deferred, unflushed variant for screen-clears that
must coalesce with the following frame's flush), the termios raw-mode
lifecycle, ANSI bracketed-paste toggling, and the xterm terminal-title OSC
push/write/restore. The UI shell composes frames and decides *what* to draw;
every byte that reaches the terminal stream, and every raw-mode or title
transition, flows through this driver.

Raw-mode transition typeahead policy: :meth:`TerminalDriver.enter_raw_mode`
calls ``tty.setraw`` with the standard-library default ``when`` of
``termios.TCSAFLUSH``, which *discards* any input queued before the switch.
The extraction deliberately preserves that flush rather than silently
changing terminal semantics, so consumers synchronize on a fresh prompt
(prompt readiness) instead of relying on bytes typed ahead of the raw-mode
transition. See ``tests/test_native_terminal_driver.py`` for the explicit
characterization.
"""

from __future__ import annotations

import termios
import tty
from typing import Any, TextIO

from pipy_harness.native.session_tree_commands import sanitize_label_text

# ANSI bracketed-paste mode toggles. While enabled the terminal wraps pasted
# text in ESC[200~ ... ESC[201~ so it can be inserted literally instead of
# being interpreted keystroke-by-keystroke (which would submit on embedded
# newlines). The matching start/end *decoding* markers stay with the key
# decoder in ``tui`` (Slice 4.2b); the driver only owns the enable/disable
# toggle it writes to the terminal.
_BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
_BRACKETED_PASTE_DISABLE = "\x1b[?2004l"

# Cap terminal-title text so a hostile or accidental long title cannot flood
# the terminal's title buffer. Owned here because the driver performs the
# title OSC write; the UI imports it for its cached title-state cap.
_TITLE_MAX_CHARS = 256


class TerminalDriver:
    """Low-level terminal I/O owner for the native tool-loop TUI.

    Holds the input/terminal streams and the terminal-side lifecycle state
    (saved termios attributes, bracketed-paste enabled flag, and whether a
    terminal title has been pushed onto the xterm title stack). All methods
    are best-effort and swallow the usual closed-stream/invalid-fd errors so a
    disconnected terminal never turns a paint or teardown into a crash.
    """

    __slots__ = (
        "input_stream",
        "terminal_stream",
        "_old_termios",
        "_bracketed_paste_active",
        "_title_pushed",
    )

    def __init__(self, input_stream: TextIO, terminal_stream: TextIO) -> None:
        self.input_stream = input_stream
        self.terminal_stream = terminal_stream
        # ``termios.tcgetattr`` result captured on entering raw mode; ``None``
        # means the terminal is in its original (cooked) mode.
        self._old_termios: Any = None
        self._bracketed_paste_active = False
        self._title_pushed = False

    def write(self, text: str) -> bool:
        """Write ``text`` to the terminal stream and flush.

        Swallows ``OSError``/``ValueError`` (closed stream, invalid fd) and
        returns ``True`` only when both the write and the flush succeeded, so
        callers that must skip follow-up bookkeeping on a failed frame can
        branch on the result.
        """

        try:
            self.terminal_stream.write(text)
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return False
        return True

    def write_deferred(self, text: str) -> bool:
        """Write ``text`` without flushing, deferring transmission.

        Unlike :meth:`write`, this does *not* flush, so ``text`` stays in the
        stream buffer until the next flush (typically the flush of the
        immediately-following frame paint). Used for a screen-clear
        (``\\x1b[2J\\x1b[H``) that must coalesce with the redraw that follows,
        so no separate flush reaches the terminal between the clear and the
        repaint -- preserving the pre-extraction behavior where the clear and
        the paint transmitted together and avoiding a resize/full-redraw
        flash. Swallows the usual closed-stream/invalid-fd errors and returns
        ``True`` only when the write itself succeeded, so callers can still
        branch on the result to skip follow-up bookkeeping on a failed frame.
        """

        try:
            self.terminal_stream.write(text)
        except (OSError, ValueError):
            return False
        return True

    def enter_raw_mode(self) -> None:
        """Switch the input terminal into raw mode and enable bracketed paste.

        ``tty.setraw`` uses the stdlib default ``termios.TCSAFLUSH`` ``when``,
        which flushes input queued before the transition (the preserved
        typeahead policy documented at module level). No-op when already in
        raw mode.
        """

        if self._old_termios is not None:
            return
        fd = self.input_stream.fileno()
        self._old_termios = termios.tcgetattr(fd)
        tty.setraw(fd)
        self._set_bracketed_paste(True)

    def restore_terminal_mode(self) -> None:
        """Restore the saved cooked-mode termios attributes.

        Disables bracketed paste first, then drains and restores the original
        attributes with ``TCSADRAIN``. No-op when not in raw mode.
        """

        if self._old_termios is None:
            return
        self._set_bracketed_paste(False)
        try:
            termios.tcsetattr(
                self.input_stream.fileno(), termios.TCSADRAIN, self._old_termios
            )
        except (OSError, termios.error, ValueError):
            pass
        self._old_termios = None

    def _set_bracketed_paste(self, enabled: bool) -> None:
        if enabled == self._bracketed_paste_active:
            return
        self._bracketed_paste_active = enabled
        try:
            self.terminal_stream.write(
                _BRACKETED_PASTE_ENABLE if enabled else _BRACKETED_PASTE_DISABLE
            )
            self.terminal_stream.flush()
        except (OSError, ValueError):
            pass

    def write_title(self, title: str) -> None:
        """Write an OSC 0 title sequence to a TTY; no-op for non-TTY streams.

        Sanitizes ``title`` (control-character stripping prevents terminal
        escape-sequence injection) and caps its length before emitting.
        """

        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        safe = sanitize_label_text(title).replace("\x07", "")[:_TITLE_MAX_CHARS]
        try:
            self.terminal_stream.write(f"\x1b]0;{safe}\x07")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

    def push_title(self) -> None:
        """Save the current terminal title on the xterm title stack (OSC 22).

        Idempotent: only the first push emits the sequence, matching the
        pre-extraction single-push guard the UI used to hold.
        """

        if self._title_pushed:
            return
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        try:
            self.terminal_stream.write("\x1b[22;2t")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return
        self._title_pushed = True

    def restore_title(self) -> None:
        """Restore the saved title from the xterm title stack (OSC 23).

        Best-effort: this pops the title saved by :meth:`push_title` so the
        pre-extension title returns (not a blank title). Only acts when a save
        was pushed; terminals that ignore the title stack simply keep the last
        title set.
        """

        if not self._title_pushed:
            return
        self._title_pushed = False
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        try:
            self.terminal_stream.write("\x1b[23;2t")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return
