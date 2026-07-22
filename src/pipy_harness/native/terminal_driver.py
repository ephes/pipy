"""Terminal output, raw-mode, and restoration driver for the native TUI.

``TerminalDriver`` owns the low-level terminal I/O for
:class:`~pipy_harness.native.tui.ToolLoopTerminalUi`: the error-swallowing
write/flush sink (with a deferred, unflushed variant for screen-clears that
must coalesce with the following frame's flush), the termios raw-mode
lifecycle, ANSI bracketed-paste toggling, the xterm terminal-title OSC
push/write/restore, and the fd-level input read primitives plus the key
decoder that turns raw bytes on the owned input fd into named keys
(``enter``/``up``/``ctrl-c``/``paste``/…), and the SIGWINCH resize lifecycle
plus live terminal-size resolution (:meth:`TerminalDriver.size`) against the
fd it paints to. The UI shell composes frames and decides *what* to draw; every
byte that reaches the terminal stream, every raw-mode or title transition,
every decoded key read from the input fd, and the terminal geometry each frame
lays out against flow through this driver. The UI keeps the layout-coupled
resize *repaint* (clear-and-redraw) but drains the pending-resize flag and
reads the current size from the driver.

Key decoding returns a bracketed paste's body to the caller rather than
storing it: :meth:`read_key`/:meth:`read_key_if_available` return the string
``"paste"`` and stash the decoded body, which the caller retrieves with
:meth:`consume_paste`. The UI keeps ownership of the durable ``_pending_paste``
buffer that survives across the read loop; the driver only performs the
transient decode handoff.

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

import os
import select
import shutil
import signal
import termios
import tty
from typing import Any, TextIO

from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.terminal_input import read_terminal_utf8_char

# ANSI bracketed-paste mode toggles. While enabled the terminal wraps pasted
# text in ESC[200~ ... ESC[201~ so it can be inserted literally instead of
# being interpreted keystroke-by-keystroke (which would submit on embedded
# newlines). The driver owns both the enable/disable toggle it writes to the
# terminal and the matching start/end *decoding* markers used by the relocated
# key decoder (Slice 4.2b).
_BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
_BRACKETED_PASTE_DISABLE = "\x1b[?2004l"

# Bracketed-paste *decoding* markers. After the leading ``ESC[`` the terminal
# sends ``200~`` to open a paste and ``ESC[201~`` to close it; the decoder
# collects everything between them as a literal paste body rather than a stream
# of keystrokes.
_BRACKETED_PASTE_START = "200~"
_BRACKETED_PASTE_END = "\x1b[201~"

# Cap terminal-title text so a hostile or accidental long title cannot flood
# the terminal's title buffer. Owned here because the driver performs the
# title OSC write; the UI imports it for its cached title-state cap.
_TITLE_MAX_CHARS = 256

# Terminal-size floors, default, and resize-poll cadence. The driver resolves
# the live terminal size against the fd it paints to, so it owns the geometry
# clamps and defaults. ``_MIN_WIDTH``/``_MIN_HEIGHT`` keep the inline layout
# usable on a tiny terminal; ``_DEFAULT_SIZE`` is the captured-stream/no-TTY
# fallback. ``_RESIZE_POLL_SECONDS`` is how long the UI input loops block on
# stdin before re-checking the size: resize *handling* is poll-based (comparing
# the live terminal size to the last painted size) so it works on any thread,
# where installing a SIGWINCH handler is not possible; the best-effort SIGWINCH
# handler only sets a flag to make idle repaints snappier. The UI imports
# ``_RESIZE_POLL_SECONDS`` for its resize-polling select timeout.
_MIN_WIDTH = 60
_MIN_HEIGHT = 12
_DEFAULT_SIZE = (88, 24)
_RESIZE_POLL_SECONDS = 0.1


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
        "_pending_input_bytes",
        "_last_paste",
        "_resize_pending",
        "_prev_winch_handler",
    )

    def __init__(self, input_stream: TextIO, terminal_stream: TextIO) -> None:
        self.input_stream = input_stream
        self.terminal_stream = terminal_stream
        # ``termios.tcgetattr`` result captured on entering raw mode; ``None``
        # means the terminal is in its original (cooked) mode.
        self._old_termios: Any = None
        self._bracketed_paste_active = False
        self._title_pushed = False
        # Bytes read from the fd but not yet decoded (a UTF-8 continuation
        # over-read pushes the stray leading byte back here for the next
        # decode). ``read_terminal_utf8_char`` drains it before touching the
        # fd, so a decoded scalar can already be waiting even when ``select``
        # would report the fd as not ready.
        self._pending_input_bytes: bytearray = bytearray()
        # Body of the most recently decoded bracketed paste, handed to the
        # caller via :meth:`consume_paste`. The UI keeps the durable
        # ``_pending_paste`` field; this is only the transient decode handoff.
        self._last_paste = ""
        # Set by the best-effort SIGWINCH handler and drained by
        # :meth:`take_resize_pending`; the UI's resize poll uses it to force a
        # repaint even when the polled size has not visibly changed yet.
        self._resize_pending = False
        # ``signal.signal`` result saved on install so :meth:`remove_resize_handler`
        # can restore the previous SIGWINCH disposition; ``None`` means no
        # handler is installed (or install was refused off the main thread).
        self._prev_winch_handler: Any = None

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

    def install_resize_handler(self) -> None:
        """Best-effort SIGWINCH handler that flags a pending resize.

        Resize *handling* is poll-based (the UI's resize poll compares the live
        :meth:`size` to the last painted size) so it works regardless of which
        thread runs the loop; installing a signal handler only makes idle
        repaints snappier. ``signal.signal`` raises ``ValueError`` when called
        off the main thread (e.g. the threaded test harness), which is caught
        and ignored -- polling still covers it.
        """

        try:
            self._prev_winch_handler = signal.signal(
                signal.SIGWINCH, self._on_resize_signal
            )
        except (ValueError, OSError, AttributeError):
            self._prev_winch_handler = None

    def remove_resize_handler(self) -> None:
        """Restore the SIGWINCH disposition saved by :meth:`install_resize_handler`."""

        if self._prev_winch_handler is None:
            return
        try:
            signal.signal(signal.SIGWINCH, self._prev_winch_handler)
        except (ValueError, OSError, AttributeError):
            pass
        self._prev_winch_handler = None

    def _on_resize_signal(self, signum: int, frame: Any) -> None:
        del signum, frame
        # Signal handlers must stay async-signal-safe: only flip a flag; the
        # input loops repaint when they next poll.
        self._resize_pending = True

    def take_resize_pending(self) -> bool:
        """Return the pending-resize flag and clear it.

        The UI's resize poll uses the returned value to force a repaint even
        when the polled size has not yet changed (a SIGWINCH can arrive before
        the new ``winsize`` is observable).
        """

        pending = self._resize_pending
        self._resize_pending = False
        return pending

    def size(
        self, *, width: int | None = None, height: int | None = None
    ) -> tuple[int, int]:
        """Resolve the terminal size clamped to the layout floors.

        An explicit ``width``/``height`` pair overrides live resolution; a live
        terminal size (see :meth:`_terminal_size`) is used when available;
        otherwise the caller's partial override or ``_DEFAULT_SIZE`` fills in.
        Both dimensions are clamped to ``_MIN_WIDTH``/``_MIN_HEIGHT``.
        """

        if width is not None and height is not None:
            return max(_MIN_WIDTH, width), max(_MIN_HEIGHT, height)
        live = self._terminal_size()
        if live is not None:
            columns, rows = live
            return max(_MIN_WIDTH, columns), max(_MIN_HEIGHT, rows)
        return (
            max(_MIN_WIDTH, width or _DEFAULT_SIZE[0]),
            max(_MIN_HEIGHT, height or _DEFAULT_SIZE[1]),
        )

    def _terminal_size(self) -> tuple[int, int] | None:
        """Resolve the live size of the terminal this frame paints to.

        Precedence: an explicit ``COLUMNS``/``LINES`` pair (honored for
        deterministic tests and CI), then the real ``winsize`` of the output
        terminal we actually write to (so a SIGWINCH/resize is observed on the
        very fd we paint, which the resize poll compares against), then the
        shared ``shutil`` fallback. Returns ``None`` when no size is available
        (non-TTY capture), so the caller uses its defaults.
        """

        # Only resolve a live size for a real terminal; a non-TTY capture
        # stream keeps the caller's defaults (matching the prior behavior and
        # avoiding COLUMNS/LINES leaking into captured-stream rendering).
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return None
        env_size = self._env_terminal_size()
        if env_size is not None:
            return env_size
        fileno = getattr(self.terminal_stream, "fileno", None)
        if callable(fileno):
            try:
                winsize = os.get_terminal_size(fileno())
            except (OSError, ValueError):
                winsize = None
            if winsize is not None and winsize.columns > 0 and winsize.lines > 0:
                return winsize.columns, winsize.lines
        fallback = shutil.get_terminal_size(_DEFAULT_SIZE)
        return fallback.columns, fallback.lines

    @staticmethod
    def _env_terminal_size() -> tuple[int, int] | None:
        try:
            columns = int(os.environ.get("COLUMNS", ""))
            lines = int(os.environ.get("LINES", ""))
        except ValueError:
            return None
        if columns > 0 and lines > 0:
            return columns, lines
        return None

    def has_pending_input(self) -> bool:
        """Report whether an already-read byte is waiting to be decoded.

        A UTF-8 continuation over-read can leave a stray leading byte buffered;
        when it is present a decoded scalar is available immediately, so the
        caller must decode instead of blocking on ``select``.
        """

        return bool(self._pending_input_bytes)

    def consume_paste(self) -> str:
        """Return and clear the body decoded by the last ``"paste"`` read.

        The UI copies this into its own ``_pending_paste`` buffer; the driver
        holds it only for the moment between decode and hand-off.
        """

        body = self._last_paste
        self._last_paste = ""
        return body

    def read_key(self, fd: int) -> str | None:
        """Block for and decode the next key from ``fd``.

        Returns the named key (``enter``/``backspace``/``ctrl-c``/``up``/…), a
        length-1 printable scalar, ``"paste"`` (body available via
        :meth:`consume_paste`), or ``None`` on EOF.
        """

        ch = self._read_byte(fd)
        if ch == "":
            return None
        if ch == "\x1b":
            return self._read_escape_sequence(fd)
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == "\t":
            return "tab"
        if ch in {"\x7f", "\b"}:
            return "backspace"
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x04":
            return "ctrl-d"
        if ch == "\x15":
            return "ctrl-u"
        if ch == "\x19":
            return "ctrl-y"
        if ch == "\x1a":
            return "ctrl-z"
        if ch == "\x01":
            return "home"
        if ch == "\x05":
            return "end"
        if ch == "\x0f":
            return "ctrl-o"
        if ch == "\x10":
            return "ctrl-p"
        if ch == "\x14":
            return "ctrl-t"
        if ch == "\x16":
            return "ctrl-v"
        # Decode any remaining C0 control byte (Ctrl+letter) to a named
        # "ctrl-<letter>" form so extension shortcuts can bind it. The explicit
        # aliases above (home/end and the app/editor hotkeys) take precedence;
        # an unbound control key still does nothing (it is not a length-1
        # printable, so it is never inserted as text).
        code = ord(ch)
        if 1 <= code <= 26:
            return f"ctrl-{chr(code + 96)}"
        return ch

    def read_key_if_available(self, fd: int, timeout: float) -> str | None:
        """Decode the next key if one arrives within ``timeout`` seconds.

        Returns the decoded key as :meth:`read_key`, or ``None`` when the fd
        stays idle for the whole poll. A buffered continuation byte is decoded
        immediately without polling.
        """

        if self._pending_input_bytes:
            return self.read_key(fd)
        readable, _, _ = select.select([fd], [], [], timeout)
        if fd not in readable:
            return None
        return self.read_key(fd)

    def _read_escape_sequence(self, fd: int) -> str:
        """Decode an escape sequence after the leading ESC has been read.

        Handles bare ``Esc``, the CSI arrow/home/end keys, and a CSI
        bracketed-paste introducer (``ESC[200~``). Parameterized CSI
        sequences are read up to their final byte (``0x40``-``0x7e``) so a
        multi-byte introducer like ``200~`` is consumed whole rather than
        being mistaken for an arrow key.
        """

        next1 = self._read_byte_with_timeout(fd, 0.05)
        if next1 == "":
            return "esc"
        # Alt+Enter (queue a follow-up) arrives as ESC followed by CR/LF.
        if next1 in {"\r", "\n"}:
            return "alt-enter"
        if next1 != "[":
            return "esc"
        sequence = ""
        while True:
            byte = self._read_byte_with_timeout(fd, 0.05)
            if byte == "":
                break
            sequence += byte
            # Stop at any CSI final byte in 0x40-0x7e. This covers the legacy
            # finals (``A``-``F``, ``Z``=0x5a), the bracketed-paste ``~`` (0x7e),
            # AND the kitty keyboard-protocol ``u`` (0x75) - so CSI-u sequences
            # like ``112;6u`` (Shift+Ctrl+P) are read whole and reach the
            # matchers below, not timed out as a bare Esc.
            if "\x40" <= byte <= "\x7e":
                break
        if sequence == _BRACKETED_PASTE_START:
            self._last_paste = self._read_bracketed_paste(fd)
            return "paste"
        # Alt-modified arrows / Enter arrive as CSI sequences with a `;3`
        # (alt) modifier; map the ones this track binds. ``alt+up`` dequeues
        # queued messages; ``alt+enter`` queues a follow-up.
        if sequence in {"1;3A", "1;9A"}:
            return "alt-up"
        # Shift+Tab (CSI Z) cycles the thinking level. Shift+Ctrl+P arrives as
        # CSI u with a ctrl+shift modifier (6) under the kitty keyboard protocol
        # or as a modifyOtherKeys CSI ``~`` sequence under xterm. Terminals
        # differ on whether the codepoint is the base lowercase ``p`` (112) or
        # the shifted uppercase ``P`` (80), so accept all four forms. Legacy
        # terminals with neither protocol cannot distinguish it from Ctrl+P and
        # fall through to forward cycling; reverse cycling stays available via
        # ``/scoped-models prev`` (documented limit).
        if sequence == "Z":
            return "shift-tab"
        if sequence in {"13;2u", "27;2;13~"}:
            return "shift-enter"
        if sequence in {"112;6u", "27;6;112~", "80;6u", "27;6;80~"}:
            return "shift-ctrl-p"
        return {
            "A": "up",
            "B": "down",
            "C": "right",
            "D": "left",
            "H": "home",
            "F": "end",
        }.get(sequence, "esc")

    def _read_bracketed_paste(self, fd: int) -> str:
        """Collect pasted bytes until the ``ESC[201~`` end marker.

        Carriage returns are normalized to newlines so multi-line pastes hold
        consistent line separators; the result is inserted literally and never
        triggers command submission.
        """

        buffer = ""
        while True:
            # Pastes arrive as a burst; a bounded read keeps a truncated paste
            # (no end marker) from blocking an active-turn watcher indefinitely.
            byte = self._read_byte_with_timeout(fd, 2.0)
            if byte == "":
                break
            buffer += byte
            if buffer.endswith(_BRACKETED_PASTE_END):
                buffer = buffer[: -len(_BRACKETED_PASTE_END)]
                break
        return buffer.replace("\r\n", "\n").replace("\r", "\n")

    def _read_byte(self, fd: int) -> str:
        return read_terminal_utf8_char(
            fd,
            pending_bytes=self._pending_input_bytes,
        )

    def _read_byte_with_timeout(self, fd: int, timeout: float) -> str:
        if self._pending_input_bytes:
            return self._read_byte(fd)
        readable, _, _ = select.select([fd], [], [], timeout)
        if fd not in readable:
            return ""
        return self._read_byte(fd)
