"""Terminal output, raw-mode, and restoration driver for the native TUI.

``TerminalDriver`` owns the low-level terminal I/O for
:class:`~pipy_harness.native.tui.TerminalUi`: the error-swallowing
write/flush sink (with a deferred, unflushed variant for screen-clears that
must coalesce with the following frame's flush), the termios raw-mode
lifecycle, ANSI bracketed-paste toggling, the xterm terminal-title OSC
push/write/restore, and the fd-level input read primitives plus the key
decoder that turns raw bytes on the owned input fd into named keys
(``enter``/``up``/``ctrl-c``/``paste``/…), and the SIGWINCH resize lifecycle
plus live terminal-size resolution (:meth:`TerminalDriver.size`) against the
fd it paints to. The pure frame renderer decides *what* to draw; this driver
serializes its logical paint plan into physical ANSI cursor/erase sequences.
Every byte that reaches the terminal stream, every raw-mode or title transition,
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

Raw-mode transition typeahead policy: the outermost
:meth:`TerminalDriver.enter_raw_mode` call uses ``tty.setraw`` with the
standard-library default ``when`` of ``termios.TCSAFLUSH``, which *discards*
any input queued before the switch. Nested owners share that raw transition;
the original mode is restored only when the outermost owner releases it. This
preserves the flush policy without letting an inner overlay return its outer
input loop to cooked mode. Ordinary readers acquire through
:meth:`TerminalDriver.raw_mode`, whose scoped release is installed only after
entry succeeds, so a failed nested acquisition cannot consume an outer owner.
Foreign TTY consumers instead use
:meth:`TerminalDriver.suspend_terminal_mode` and
:meth:`TerminalDriver.resume_terminal_mode` through the UI's scoped external-I/O
contract: suspension restores the saved cooked mode without consuming logical
owners, and the final paired resume re-enters physical raw mode with the same
``TCSAFLUSH`` policy. Separate suspension depth makes nested foreign-consumer
scopes safe. Raw ownership cannot be acquired while such a scope is active,
and an unmatched resume fails loudly rather than fabricating a physical mode.
:meth:`TerminalDriver.force_restore_terminal_mode` is reserved for the actual
UI close boundary, where it clears abandoned ownership and suspension state
and restores the saved terminal state exactly once per close recovery. See
``tests/test_native_terminal_driver.py`` for the explicit characterization.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import termios
import tty
from collections.abc import Iterator
from contextlib import contextmanager
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
    terminal title has been pushed onto the xterm title stack). Paint and
    teardown operations are best-effort and swallow the usual closed-stream/
    invalid-fd errors. Raw entry and temporary suspend/resume surface transition
    failures so a caller never starts a foreign TTY consumer after a failed
    cooked-mode handoff; their state remains recoverable by forced close.
    """

    __slots__ = (
        "input_stream",
        "terminal_stream",
        "_old_termios",
        "_raw_mode_depth",
        "_terminal_mode_suspend_depth",
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
        # ``termios.tcgetattr`` result captured on the outermost raw-mode
        # entry; ``_raw_mode_depth`` counts balanced owners so a nested overlay
        # cannot restore cooked mode out from under its outer input loop.
        self._old_termios: Any = None
        self._raw_mode_depth = 0
        # Foreign TTY consumers temporarily restore the saved cooked mode
        # without consuming balanced raw owners. A depth (rather than a bool)
        # prevents an inner foreign-consumer scope from resuming raw mode while
        # an outer foreign consumer still owns the terminal.
        self._terminal_mode_suspend_depth = 0
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

    def write_frame(
        self,
        *,
        prior_live_height: int,
        prior_live_input_row: int,
        committed_rows: tuple[tuple[str, bool], ...],
        live_rows: tuple[tuple[str, bool], ...],
        cursor_lines_up: int,
        cursor_col: int,
        cursor_visible: bool,
    ) -> bool:
        """Serialize one pure paint plan and write it as a single frame.

        This driver retains ownership of ANSI physical cursor control and tail
        erasure. ``bool`` row values mean that a shorter row needs ``EL``;
        full-width styled rows must not erase their final cell.
        """

        output = ["\x1b[?25l"]
        if prior_live_height > 0:
            if prior_live_input_row > 0:
                output.append(f"\x1b[{prior_live_input_row}A")
            output.append("\r\x1b[J")
        else:
            output.append("\r")
        for text, erase_tail in committed_rows:
            output.append(text)
            output.append("\x1b[K\r\n" if erase_tail else "\r\n")
        last_index = len(live_rows) - 1
        for index, (text, erase_tail) in enumerate(live_rows):
            output.append(text)
            if erase_tail:
                output.append("\x1b[K")
            if index != last_index:
                output.append("\r\n")
        if cursor_lines_up > 0:
            output.append(f"\x1b[{cursor_lines_up}A")
        output.append("\r")
        if cursor_visible:
            if cursor_col > 0:
                output.append(f"\x1b[{cursor_col}C")
            output.append("\x1b[?25h")
        return self.write("".join(output))

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

    @contextmanager
    def raw_mode(self) -> Iterator[None]:
        """Scope one balanced raw-mode owner after successful acquisition.

        Entry happens before the generator yields, so context-manager cleanup is
        registered only after :meth:`enter_raw_mode` succeeds. A suspended or
        failed physical transition therefore cannot release another scope's
        existing owner.
        """

        self.enter_raw_mode()
        try:
            yield
        finally:
            self.restore_terminal_mode()

    def enter_raw_mode(self) -> None:
        """Acquire raw mode and enable bracketed paste for the outermost owner.

        ``tty.setraw`` uses the stdlib default ``termios.TCSAFLUSH`` ``when``,
        which flushes input queued before the outermost transition (the
        preserved typeahead policy documented at module level). Nested owners
        increment a depth counter without repeating that destructive flush.
        """

        if self._terminal_mode_suspend_depth > 0:
            raise RuntimeError("cannot enter raw mode while terminal I/O is suspended")
        if self._raw_mode_depth > 0:
            self._raw_mode_depth += 1
            return
        fd = self.input_stream.fileno()
        old_termios = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
        except (OSError, termios.error, ValueError):
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_termios)
            except (OSError, termios.error, ValueError):
                pass
            raise
        self._old_termios = old_termios
        self._raw_mode_depth = 1
        self._set_bracketed_paste(True)

    def suspend_terminal_mode(self) -> None:
        """Temporarily give a foreign TTY consumer the saved cooked terminal.

        Suspension is distinct from balanced ownership release: every logical
        raw owner remains counted. The first suspension immediately disables
        bracketed paste and restores the original attributes with ``TCSADRAIN``
        even when ownership depth exceeds one. Nested suspension calls only
        increment their own guard depth. If the physical restore fails, paste
        is re-enabled and suspension is not published, so a caller cannot
        unknowingly launch a foreign consumer against a half-transitioned
        logical state. A scope is still published when there is no raw owner;
        this prevents a concurrent or nested UI path from silently acquiring
        physically raw mode while the foreign consumer owns the terminal.
        """

        if self._terminal_mode_suspend_depth > 0:
            self._terminal_mode_suspend_depth += 1
            return
        if self._raw_mode_depth == 0:
            self._terminal_mode_suspend_depth = 1
            return
        old_termios = self._old_termios
        if old_termios is None:
            raise RuntimeError("raw terminal owner has no saved terminal mode")
        self._set_bracketed_paste(False)
        try:
            termios.tcsetattr(
                self.input_stream.fileno(), termios.TCSADRAIN, old_termios
            )
        except (OSError, termios.error, ValueError):
            self._set_bracketed_paste(True)
            raise
        self._terminal_mode_suspend_depth = 1

    def resume_terminal_mode(self) -> bool:
        """Release one temporary suspension and resume raw mode when unguarded.

        An unmatched resume raises ``RuntimeError`` so missing or misordered
        pairing cannot silently pass. The final matching resume uses
        ``tty.setraw`` without an explicit ``when``, preserving the documented
        ``TCSAFLUSH`` typeahead policy. Logical ownership depth is unchanged.
        A failed raw transition keeps suspension published and best-effort
        restores the saved cooked attributes, so forced-close recovery remains
        authoritative and a later explicit retry is safe. Returns ``True``
        only when the outermost scope was released, allowing the façade to
        repaint once after nested external-I/O scopes.
        """

        if self._terminal_mode_suspend_depth == 0:
            raise RuntimeError("terminal I/O suspension is not active")
        if self._terminal_mode_suspend_depth > 1:
            self._terminal_mode_suspend_depth -= 1
            return False
        if self._raw_mode_depth == 0:
            self._terminal_mode_suspend_depth = 0
            return True
        try:
            tty.setraw(self.input_stream.fileno())
        except (OSError, termios.error, ValueError):
            old_termios = self._old_termios
            if old_termios is not None:
                try:
                    termios.tcsetattr(
                        self.input_stream.fileno(), termios.TCSADRAIN, old_termios
                    )
                except (OSError, termios.error, ValueError):
                    pass
            raise
        self._terminal_mode_suspend_depth = 0
        self._set_bracketed_paste(True)
        return True

    def restore_terminal_mode(self) -> None:
        """Release raw mode and restore cooked mode after the outermost owner.

        Nested releases only decrement ownership. The final release disables
        bracketed paste, then drains and restores the original attributes with
        ``TCSADRAIN``. No-op when no raw-mode owner remains.
        """

        if self._raw_mode_depth == 0:
            return
        self._raw_mode_depth -= 1
        if self._raw_mode_depth > 0:
            return
        self._terminal_mode_suspend_depth = 0
        self._restore_saved_terminal_mode()

    def force_restore_terminal_mode(self) -> None:
        """Abandon all raw owners and restore terminal state exactly once.

        This is a shutdown recovery operation, not a balanced release. It is
        intentionally reserved for the actual terminal UI close boundary so an
        earlier unmatched acquisition cannot leave the host terminal raw.
        Repeated calls are idempotent.
        """

        self._raw_mode_depth = 0
        self._terminal_mode_suspend_depth = 0
        self._restore_saved_terminal_mode()

    def _restore_saved_terminal_mode(self) -> None:
        self._set_bracketed_paste(False)
        old_termios = self._old_termios
        self._old_termios = None
        if old_termios is None:
            return
        try:
            termios.tcsetattr(
                self.input_stream.fileno(), termios.TCSADRAIN, old_termios
            )
        except (OSError, termios.error, ValueError):
            pass

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
        return self._decode_control_or_scalar(ch)

    @staticmethod
    def _decode_control_or_scalar(ch: str) -> str:
        """Map C0 controls with explicit aliases before returning a scalar."""

        aliases = {
            "\r": "enter",
            "\n": "enter",
            "\t": "tab",
            "\x7f": "backspace",
            "\b": "backspace",
            "\x03": "ctrl-c",
            "\x04": "ctrl-d",
            "\x15": "ctrl-u",
            "\x19": "ctrl-y",
            "\x1a": "ctrl-z",
            "\x01": "home",
            "\x05": "end",
            "\x0f": "ctrl-o",
            "\x10": "ctrl-p",
            "\x14": "ctrl-t",
            "\x16": "ctrl-v",
        }
        alias = aliases.get(ch)
        if alias is not None:
            return alias
        # Decode any remaining C0 control byte (Ctrl+letter) to a named
        # "ctrl-<letter>" form. Explicit aliases above retain precedence.
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
        return self._decode_csi_sequence(fd, self._read_csi_sequence(fd))

    def _read_csi_sequence(self, fd: int) -> str:
        """Read CSI parameters through the required final byte, if present."""

        sequence = ""
        while True:
            byte = self._read_byte_with_timeout(fd, 0.05)
            if byte == "":
                break
            sequence += byte
            # Any CSI final byte in 0x40-0x7e closes the sequence, including
            # legacy keys, bracketed paste (~), and kitty keyboard protocol (u).
            if "\x40" <= byte <= "\x7e":
                break
        return sequence

    def _decode_csi_sequence(self, fd: int, sequence: str) -> str:
        """Classify bracketed paste, modifier aliases, and legacy CSI keys."""

        if sequence == _BRACKETED_PASTE_START:
            self._last_paste = self._read_bracketed_paste(fd)
            return "paste"
        # Alt-modified arrows differ in the modifier alias used by terminals.
        if sequence in {"1;3A", "1;9A"}:
            return "alt-up"
        if sequence == "Z":
            return "shift-tab"
        if sequence in {"13;2u", "27;2;13~"}:
            return "shift-enter"
        # Kitty CSI-u and xterm modifyOtherKeys may report lowercase or shifted
        # uppercase P for Shift+Ctrl+P, so preserve all accepted forms.
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
