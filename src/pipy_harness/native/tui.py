"""Pipy-owned terminal UI shell for native tool-loop REPL sessions.

The line-oriented renderer prints prompt, loader, assistant text, tool
blocks, and footer as a stream of independent lines.  This module owns a
small stateful terminal frame instead: chat history, submitted user
messages, streaming assistant output, transient working state, input, and
footer are separate regions that are composed into one screen on each
paint.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import sys
import termios
import textwrap
import threading
import tty
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO, cast

from pipy_harness.native.chrome import (
    chrome_style_for,
    discover_loaded_resource_names,
    pipy_version_label,
)
from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.editor_completion import (
    CompletionItem,
    at_candidates,
    extract_at_token,
    extract_path_prefix,
    path_candidates,
)
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
)
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    SessionPickerRow,
    build_session_picker_rows,
    format_session_picker_label,
    sanitize_label_text,
)
from pipy_harness.native.terminal_input import read_terminal_utf8_char


# Sentinel returned by the session-picker key handler to mean "stay open"
# (distinct from ``None``, which cancels the picker).
_PICKER_CONTINUE = object()

TOOL_LOOP_TUI_RUNTIME_LABEL = "tool-loop-tui"
_MIN_WIDTH = 60
_MIN_HEIGHT = 12
_DEFAULT_SIZE = (88, 24)
_DEFAULT_HISTORY_VIEW_LINES = 21
_TOOL_PANEL_HISTORY_VIEW_LINES = 23
# Live streaming tool output (e.g. pytest dots): show a bounded tail while the
# command runs; the full bounded result is committed when it settles.
_TOOL_STREAM_LIVE_LINES = 12
_TOOL_STREAM_LIVE_MAX_CHARS = 8 * 1024
_OVERFLOW_BOTTOM_GUTTER_LINES = 2
_OVERFLOW_CONTEXT_TARGET_LINES = 13
_OVERFLOW_CONTEXT_MIN_LINES = 4
TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS = (
    "/help",
    "/hotkeys",
    "/model",
    "/scoped-models",
    "/settings",
    "/login",
    "/logout",
    "/copy",
    "/compact",
    "/export",
    "/import",
    "/share",
    "/reload",
    "/changelog",
    "/theme",
    "/exit",
    "/quit",
)
# How long the input loops block on stdin before checking for a terminal
# resize. Resize handling is poll-based (comparing the live terminal size to
# the last painted size) so it works on any thread, where installing a
# SIGWINCH handler is not possible; a best-effort SIGWINCH handler only sets a
# flag to make idle repaints snappier.
_RESIZE_POLL_SECONDS = 0.1
# Cap the per-line undo/redo history so a long editing session cannot grow the
# stacks without bound. Undo granularity is one edit operation (a single typed
# character, a delete, a kill-to-start, or a whole bracketed paste).
_MAX_UNDO_DEPTH = 200
# Cap the in-memory prompt-recall history so a long session cannot grow it
# without bound. History is session-scoped and never persisted.
_MAX_HISTORY_DEPTH = 500
# ANSI bracketed-paste mode toggles. While enabled the terminal wraps pasted
# text in ESC[200~ ... ESC[201~ so it can be inserted literally instead of
# being interpreted keystroke-by-keystroke (which would submit on embedded
# newlines).
_BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
_BRACKETED_PASTE_DISABLE = "\x1b[?2004l"
_BRACKETED_PASTE_START = "200~"
_BRACKETED_PASTE_END = "\x1b[201~"
# Single-width glyph shown in the input cell for a newline carried by a
# multi-line paste. The buffer keeps the literal "\n" (so the exact multi-line
# prompt is submitted on Enter); only the rendered cell substitutes the glyph,
# which keeps raw newlines from spilling into the terminal frame. U+23CE has
# East-Asian-width "Narrow", so it occupies one terminal cell.
_INPUT_NEWLINE_GLYPH = "⏎"

# Internal sentinel "commands" returned by ``read_line`` for in-editor hotkeys
# that the session dispatches without rendering a user-message bubble. The
# leading control byte cannot be produced by ordinary typing or paste, so these
# never collide with a real prompt. The session translates the model-cycle
# sentinels into the existing ``/scoped-models next``/``prev`` dispatch.
HOTKEY_THINKING_CYCLE = "\x00pipy-hotkey:thinking-cycle"
HOTKEY_MODEL_CYCLE_NEXT = "\x00pipy-hotkey:model-cycle-next"
HOTKEY_MODEL_CYCLE_PREV = "\x00pipy-hotkey:model-cycle-prev"
HOTKEY_TOGGLE_TOOLS = "\x00pipy-hotkey:toggle-tools"
HOTKEY_TOGGLE_THINKING = "\x00pipy-hotkey:toggle-thinking"
# An activated extension's registered keyboard shortcut fired; the normalized
# key follows the prefix so the session can look up and dispatch the handler.
HOTKEY_EXTENSION_SHORTCUT_PREFIX = "\x00pipy-hotkey:ext-shortcut:"

# Outcomes of the active-turn watcher / mid-turn editor.
TURN_SETTLED = "settled"  # the provider turn finished on its own
TURN_ABORTED = "aborted"  # Escape/Ctrl-C cancelled the turn
TURN_STEERED = "steered"  # a steering message interrupted the turn
TURN_LOCAL_COMMAND = "local_command"  # a /… or !… command interrupted the turn


@dataclass(frozen=True, slots=True)
class ModelSelectorOption:
    """One row offered by the interactive provider/model selector.

    ``label`` is the fully composed display string (provider/model plus an
    availability annotation); ``selectable`` is ``False`` for rows that are
    visible-but-not-choosable (unavailable provider, or a provider that does
    not advertise tool-call support in tool-loop mode). The selector keeps
    such rows navigable so their reason stays readable, but ``Enter`` cannot
    choose them.
    """

    label: str
    selectable: bool


@dataclass(frozen=True, slots=True)
class SettingsRow:
    """One row in the interactive ``/settings`` dialog.

    ``kind`` is ``"header"`` (a non-selectable section label), ``"status"`` (a
    non-selectable read-only line), or ``"action"`` (an actionable row).
    ``action`` is the identifier handed back to the caller when an action row
    is activated with Enter/Space; it is ``None`` for headers/status rows.
    Only rows with a non-``None`` ``action`` are navigable and choosable, so the
    highlight always rests on something the user can act on while read-only
    status rows stay visible for context.
    """

    label: str
    kind: str = "status"
    action: str | None = None


@dataclass(frozen=True, slots=True)
class ScopedModelRow:
    """One row in the interactive ``/scoped-models`` multi-select overlay.

    ``reference`` is the ``provider/model`` reference; ``available`` marks
    auth-available rows (unavailable rows stay visible but are not togglable).
    """

    reference: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class TreeSelectorRow:
    """One visible row in the interactive ``/tree`` selector.

    ``entry_id`` identifies the session-tree entry; ``label`` is the rendered
    display text (already indented/prefixed); ``active`` marks entries on the
    current leaf path; ``labeled`` marks entries that carry a user label.
    """

    entry_id: str
    label: str
    active: bool = False
    labeled: bool = False


@dataclass(frozen=True, slots=True)
class _FrameLine:
    text: str
    kind: str = "normal"
    meta: dict[str, Any] | None = None


class _ExtensionSelectComponent:
    """Simple string selector used by extension `ctx.ui.select`/`confirm`."""

    _MAX_VISIBLE_OPTIONS = 8

    def __init__(
        self, title: str, options: Sequence[str], done: Callable[..., None]
    ) -> None:
        self.title = title
        self.options = tuple(str(option) for option in options if str(option))
        self.selected = 0
        self._done = done

    def render(self, width: int) -> list[str]:
        lines = [
            _clip_plain(
                f" {sanitize_label_text(self.title)} - up/down move, enter select, esc cancel",
                width,
            )
        ]
        start, end = self._visible_window()
        for index, option in enumerate(self.options[start:end], start=start):
            prefix = "-> " if index == self.selected else "   "
            lines.append(_clip_plain(f"{prefix}{sanitize_label_text(option)}", width))
        if start > 0 or end < len(self.options):
            lines.append(
                _clip_plain(
                    f"   ({self.selected + 1}/{len(self.options)})",
                    width,
                )
            )
        return lines

    def _visible_window(self) -> tuple[int, int]:
        total = len(self.options)
        if total <= self._MAX_VISIBLE_OPTIONS:
            return 0, total
        start = max(
            0,
            min(
                self.selected - (self._MAX_VISIBLE_OPTIONS // 2),
                total - self._MAX_VISIBLE_OPTIONS,
            ),
        )
        return start, start + self._MAX_VISIBLE_OPTIONS

    def handle_input(self, key: str) -> None:
        if key in {"esc", "ctrl-c", "ctrl-d"}:
            self._done(None)
            return
        if not self.options:
            self._done(None)
            return
        if key == "up":
            self.selected = (self.selected - 1) % len(self.options)
            return
        if key == "down":
            self.selected = (self.selected + 1) % len(self.options)
            return
        if key == "enter":
            self._done(self.options[self.selected])


class _ExtensionConfirmComponent(_ExtensionSelectComponent):
    """Confirmation dialog with a bounded, visible message body."""

    _MAX_MESSAGE_LINES = 6

    def __init__(
        self,
        title: str,
        message: str,
        done: Callable[..., None],
    ) -> None:
        super().__init__(title, ("Yes", "No"), done)
        self.message = message

    def render(self, width: int) -> list[str]:
        lines = [
            _clip_plain(
                f" {sanitize_label_text(self.title)} - up/down move, enter select, esc cancel",
                width,
            )
        ]
        message_lines = self._message_lines(width)
        lines.extend(message_lines)
        if message_lines:
            lines.append("")
        start, end = self._visible_window()
        for index, option in enumerate(self.options[start:end], start=start):
            prefix = "-> " if index == self.selected else "   "
            lines.append(_clip_plain(f"{prefix}{option}", width))
        return lines

    def _message_lines(self, width: int) -> list[str]:
        all_lines: list[str] = []
        body_width = max(20, width - 3)
        raw_lines = str(self.message).splitlines() or [""]
        for raw_line in raw_lines:
            pieces = textwrap.wrap(
                sanitize_label_text(raw_line), width=body_width
            ) or [""]
            all_lines.extend(f"  {piece}" for piece in pieces)
        truncated = len(all_lines) > self._MAX_MESSAGE_LINES
        wrapped = all_lines[: self._MAX_MESSAGE_LINES]
        if truncated and wrapped:
            wrapped[-1] = _clip_plain(wrapped[-1] + " ...", width)
        return [_clip_plain(line, width) for line in wrapped]


class _ExtensionInputComponent:
    """Single-line input overlay used by extension `ctx.ui.input`."""

    def __init__(
        self, title: str, placeholder: str | None, done: Callable[..., None]
    ) -> None:
        self.title = title
        self.placeholder = placeholder or ""
        self.text = ""
        self._done = done

    def render(self, width: int) -> list[str]:
        shown = sanitize_label_text(self.text if self.text else self.placeholder)
        return [
            _clip_plain(
                f" {sanitize_label_text(self.title)} - enter submit, esc cancel",
                width,
            ),
            _clip_plain(f"> {shown}", width),
        ]

    def handle_input(self, key: str) -> None:
        if key in {"esc", "ctrl-c", "ctrl-d"}:
            self._done(None)
            return
        if key == "enter":
            self._done(self.text)
            return
        if key == "backspace":
            self.text = self.text[:-1]
            return
        if len(key) == 1 and key.isprintable():
            self.text += key


def _clip_plain(text: str, width: int) -> str:
    return sanitize_label_text(text)[: max(0, width)]


def _safe_extension_status_key(key: str) -> str | None:
    text = sanitize_label_text(str(key)).strip()
    if not text:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or None


_SAFE_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _sanitize_custom_overlay_text(text: str) -> str:
    """Sanitize custom overlay text while preserving simple SGR styling."""

    raw = str(text)
    cleaned: list[str] = []
    index = 0
    while index < len(raw):
        match = _SAFE_SGR_RE.match(raw, index)
        if match is not None:
            cleaned.append(match.group(0))
            index = match.end()
            continue
        ch = raw[index]
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
        index += 1
    return "".join(cleaned)


def _visible_len_allow_sgr(text: str) -> int:
    return len(_SAFE_SGR_RE.sub("", text))


def _clip_custom_overlay_text(text: str, width: int) -> str:
    """Clip text by visible width, retaining only safe SGR sequences."""

    safe = _sanitize_custom_overlay_text(text)
    if width <= 0:
        return ""
    if _visible_len_allow_sgr(safe) <= width:
        return safe
    if width <= 1:
        return "…"

    target = width - 1
    visible = 0
    clipped: list[str] = []
    index = 0
    while index < len(safe) and visible < target:
        match = _SAFE_SGR_RE.match(safe, index)
        if match is not None:
            clipped.append(match.group(0))
            index = match.end()
            continue
        clipped.append(safe[index])
        visible += 1
        index += 1
    clipped.append("…")
    return "".join(clipped)


@dataclass(slots=True)
class ToolLoopTerminalUi:
    """Stateful terminal frame for the native tool-loop REPL.

    The UI intentionally uses whole-frame repainting (`cursor home` +
    region composition) instead of relative row rewrites.  Tests can
    inspect :meth:`render_lines` directly, while real TTY sessions use
    :meth:`paint` to draw the current frame.
    """

    input_stream: TextIO
    terminal_stream: TextIO
    cwd: Path
    runtime_label: str = TOOL_LOOP_TUI_RUNTIME_LABEL
    footer_lines: tuple[str, str] = ("", "")
    input_text: str = ""
    input_cursor: int | None = None
    working_text: str = ""
    extension_working_message: str | None = None
    extension_working_visible: bool = True
    extension_status: dict[str, str] = field(default_factory=dict)
    assistant_text: str = ""
    reasoning_text: str = ""
    tool_output_text: str = ""
    command_names: tuple[str, ...] = TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
    command_descriptions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )
    # Max rows shown in the slash-command/autocomplete menu (Pi
    # ``autocompleteMaxVisible``; default 5, clamped 3..20 by the settings
    # getter). Overflow rows scroll behind a "… N more" tail.
    autocomplete_max_visible: int = 5
    # Decoded key strings (e.g. ``"ctrl-g"``) bound by activated extensions via
    # ``api.register_shortcut``. When the editor reads one of these keys it
    # returns the HOTKEY_EXTENSION_SHORTCUT sentinel so the session dispatches
    # the bound handler. Keys the decoder cannot produce (e.g. ``ctrl-.`` on a
    # non-kitty terminal) simply never fire — the registration is still valid.
    extension_shortcut_keys: frozenset[str] = frozenset()
    slash_menu_open: bool = False
    slash_menu_selection: int = 0
    # Editor autocomplete popup state (the ``@`` file picker and Tab path
    # completion). Mutually exclusive with the slash menu (which keeps priority
    # for a leading ``/``). ``autocomplete_mode`` is ``"at"`` or ``"path"``;
    # ``autocomplete_token_start`` is the index in ``input_text`` of the span
    # that an accepted candidate replaces.
    autocomplete_open: bool = False
    autocomplete_items: tuple[CompletionItem, ...] = ()
    autocomplete_selection: int = 0
    autocomplete_mode: str = "at"
    autocomplete_token_start: int = 0
    model_selector_open: bool = False
    model_selector_options: tuple[ModelSelectorOption, ...] = ()
    model_selector_selection: int = 0
    settings_dialog_open: bool = False
    settings_dialog_rows: tuple[SettingsRow, ...] = ()
    settings_dialog_selection: int = 0
    tree_selector_open: bool = False
    tree_selector_rows: tuple["TreeSelectorRow", ...] = ()
    tree_selector_selection: int = 0
    tree_selector_filter: str = "default"
    # /scoped-models multi-select overlay state.
    scoped_models_open: bool = False
    scoped_models_rows: tuple["ScopedModelRow", ...] = ()
    scoped_models_selection: int = 0
    scoped_models_checked: set[int] = field(default_factory=set)
    # Interactive session picker overlay state (the /resume + -r picker, Pi's
    # session-selector). The picker runs no provider turn. ``_mode`` is
    # ``list`` | ``rename`` | ``confirm-delete``; the underlying lists are the
    # current-project and all-projects session entries the rows are built from.
    # Custom interactive overlay state for an extension command handler
    # (`ctx.ui.custom`). The component is trusted local extension code that
    # renders its own full-screen lines and consumes keys; the driver only
    # paints its lines and routes keystrokes, running no provider turn.
    custom_overlay_open: bool = False
    _custom_component: object | None = None
    _custom_done: bool = False
    _custom_result: object = None
    session_picker_open: bool = False
    session_picker_rows: tuple["SessionPickerRow", ...] = ()
    session_picker_selection: int = 0
    session_picker_query: str = ""
    session_picker_scope: str = "current"
    session_picker_sort: str = "recent"
    session_picker_named_only: bool = False
    session_picker_show_path: bool = False
    session_picker_mode: str = "list"
    session_picker_input: str = ""
    session_picker_status: str = ""
    session_picker_current: Path | None = None
    _session_picker_project: list["SessionListEntry"] = field(default_factory=list)
    _session_picker_all: list["SessionListEntry"] = field(default_factory=list)
    _session_picker_now: float = 0.0
    # Folding/expansion view flags (Pi: Ctrl+O tool-output expansion, Ctrl+T
    # thinking-block fold). These govern how the live region and newly committed
    # blocks render; blocks already scrolled into native scrollback keep the
    # form they were committed with (inline-rendering limitation versus Pi's
    # full retro-rebuild, which would rewrite the host terminal's scrollback).
    tools_expanded: bool = False
    thinking_hidden: bool = False
    # Reasoning blocks that settled while thinking was folded (Ctrl+T). They are
    # retained rather than dropped so toggling visibility back reveals them
    # (committed fresh at toggle time, not retro-written into scrollback).
    _deferred_reasoning: list[str] = field(default_factory=list)
    # Queued steering / follow-up messages (Pi parity). While a provider turn
    # streams, a normal Enter enqueues a steering message (interrupts the turn at
    # the next safe point) and Alt+Enter enqueues a follow-up (runs after the
    # turn settles). They render in a pending region; Alt+Up restores them to the
    # editor. ``_pending_drain`` holds messages promoted for sequential delivery
    # (steering first, then follow-up) once the turn stops.
    _pending_steering: list[str] = field(default_factory=list)
    _pending_follow_up: list[str] = field(default_factory=list)
    _pending_drain: list[str] = field(default_factory=list)
    # A recognized local command (``/...`` or ``!...``) submitted with Enter
    # mid-turn is NOT queued for the provider: like Pi's editor, it interrupts
    # the turn and runs locally. It is held here for the session loop to pick up
    # and dispatch through the normal local-command path on the next iteration.
    _pending_command: str | None = None
    # Clipboard / drag image paste (Pi Ctrl+V). ``clipboard_image_read`` reads an
    # image from the OS clipboard; ``clipboard_temp_dir`` is an owner-only dir
    # (also registered as an image reference root by the session) where pasted
    # image bytes are written before an ``@image:`` reference is inserted.
    clipboard_image_read: Callable[[], ImageClipboardResult] | None = None
    clipboard_temp_dir: Path | None = None
    _clipboard_image_count: int = 0
    _history_blocks: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    _old_termios: Any = None
    _closed: bool = False
    # Inline scrollback rendering state: committed history is printed once into
    # the terminal's normal buffer (so native scrollback in Ghostty/zellij can
    # review it), and only the live region (transient stream + input/footer) is
    # redrawn in place below it.
    _painted_block_count: int = 0
    _live_height: int = 0
    _live_input_row: int = 0
    _paint_lock: Any = field(default_factory=threading.RLock)
    # Editor ergonomics state.
    #
    # ``input_history`` is an in-memory, session-scoped ring of submitted
    # prompts for Up/Down recall. It is never written to disk: keeping it in
    # process memory only honors the metadata-first archive contract (no
    # prompts, pasted text, or command bodies persisted by default).
    input_history: list[str] = field(default_factory=list)
    _history_nav_index: int | None = None
    _history_draft: str = ""
    # Per-line undo/redo stacks of ``(text, cursor)`` snapshots, reset when a
    # new line begins. Redo is cleared whenever a fresh edit is recorded.
    _undo_stack: list[tuple[str, int]] = field(default_factory=list)
    _redo_stack: list[tuple[str, int]] = field(default_factory=list)
    _pending_paste: str = ""
    _pending_input_bytes: bytearray = field(default_factory=bytearray)
    # Editor rehydration: a ``/tree`` user-message selection pre-fills the next
    # prompt with the selected text so the user can edit it into a new branch.
    _pending_initial_text: str | None = None
    # Resize handling.
    _resize_pending: bool = False
    _last_painted_size: tuple[int, int] = (0, 0)
    _prev_winch_handler: Any = None
    _bracketed_paste_active: bool = False

    @classmethod
    def is_supported(cls, input_stream: TextIO, terminal_stream: TextIO) -> bool:
        if input_stream is not sys.stdin or terminal_stream is not sys.stderr:
            return False
        if sys.platform.startswith("win"):
            return False
        if os.environ.get("TERM", "").lower() == "dumb":
            return False
        if not bool(getattr(input_stream, "isatty", lambda: False)()):
            return False
        if not bool(getattr(terminal_stream, "isatty", lambda: False)()):
            return False
        return hasattr(input_stream, "fileno")

    def start(self) -> None:
        """Initialize the shell history and paint the first frame.

        The TUI runs inline (no alternate screen): startup chrome and every
        finalized block are committed into the terminal's normal buffer so the
        host terminal/multiplexer keeps them in native scrollback.
        """

        if not self._history_blocks:
            self._history_blocks.extend(self._startup_blocks())
        self._install_resize_handler()
        self.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        if footer is not None:
            self.set_footer_text(footer)
        if self._pending_initial_text is not None:
            self.input_text = self._pending_initial_text
            self.input_cursor = len(self.input_text)
            self._pending_initial_text = None
        else:
            self.input_text = ""
            self.input_cursor = 0
        self.slash_menu_open = False
        self.slash_menu_selection = 0
        self._close_autocomplete()
        self._reset_line_editor_state()
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None:
                    return ""
                if key == "enter":
                    if self.autocomplete_open:
                        # Enter accepts the highlighted completion (Pi: Enter/Tab
                        # accept) and keeps editing rather than submitting.
                        self._accept_autocomplete_selection()
                        continue
                    if self.slash_menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self.input_text not in matches:
                            self._accept_slash_menu_selection()
                    submitted = self.input_text
                    self._record_history(submitted)
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._close_autocomplete()
                    self._reset_line_editor_state()
                    self.paint()
                    return f"{submitted}\n"
                if key == "ctrl-c":
                    raise KeyboardInterrupt
                if key == "ctrl-d":
                    if not self.input_text:
                        return ""
                    continue
                if key in {"ctrl-p", "shift-ctrl-p"}:
                    # app.model.cycleForward (ctrl+p) / cycleBackward
                    # (shift+ctrl+p): cycle the active model through the scoped
                    # set. Delegated to the session's /scoped-models dispatch so
                    # the live provider rebinds through the shared select_model
                    # boundary; no provider turn. Any partially-typed input is
                    # preserved and re-injected into the next prompt so the cycle
                    # never drops what the user was typing. (shift+ctrl+p is only
                    # decodable on terminals speaking the kitty keyboard
                    # protocol; legacy terminals send plain ctrl+p and cycle
                    # forward — a documented input-decoding limit.)
                    if self.input_text:
                        self._pending_initial_text = self.input_text
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._close_autocomplete()
                    self._reset_line_editor_state()
                    return (
                        f"{HOTKEY_MODEL_CYCLE_PREV}\n"
                        if key == "shift-ctrl-p"
                        else f"{HOTKEY_MODEL_CYCLE_NEXT}\n"
                    )
                if key == "shift-tab":
                    # app.thinking.cycle: cycle the reasoning level. Dispatched
                    # by the session without a provider turn; the partially-typed
                    # buffer is preserved into the next prompt.
                    if self.input_text:
                        self._pending_initial_text = self.input_text
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._close_autocomplete()
                    self._reset_line_editor_state()
                    return f"{HOTKEY_THINKING_CYCLE}\n"
                if key in {"ctrl-o", "ctrl-t"}:
                    # app.tools.expand (ctrl+o) / app.thinking.toggle (ctrl+t):
                    # renderer view-flag toggles dispatched by the session (so the
                    # thinking-visibility setting can be persisted and a status
                    # shown). The partially-typed buffer is preserved.
                    if self.input_text:
                        self._pending_initial_text = self.input_text
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._close_autocomplete()
                    self._reset_line_editor_state()
                    return (
                        f"{HOTKEY_TOGGLE_TOOLS}\n"
                        if key == "ctrl-o"
                        else f"{HOTKEY_TOGGLE_THINKING}\n"
                    )
                if key == "paste":
                    self._insert_paste(self._pending_paste)
                    self._pending_paste = ""
                    self.paint()
                    continue
                if key == "ctrl-v":
                    # app.clipboard.pasteImage: read an image from the OS
                    # clipboard, write it to an owner-only temp file, and insert
                    # an @image: reference. No provider turn.
                    self._paste_clipboard_image()
                    self.paint()
                    continue
                if key in self.extension_shortcut_keys:
                    # An activated extension bound this key via
                    # api.register_shortcut. Preserve any partially-typed input
                    # into the next prompt (like the app hotkeys) and hand the
                    # session the sentinel so it dispatches the bound handler.
                    if self.input_text:
                        self._pending_initial_text = self.input_text
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._close_autocomplete()
                    self._reset_line_editor_state()
                    return f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key}\n"
                if key == "backspace":
                    self._delete_before_cursor()
                    self.paint()
                    continue
                if key == "esc":
                    if self.slash_menu_open:
                        self.slash_menu_open = False
                        self.paint()
                    elif self.autocomplete_open:
                        self._close_autocomplete()
                        self.paint()
                    continue
                if key in {"up", "down"}:
                    if self.slash_menu_open:
                        self._navigate_slash_menu(key)
                    elif self.autocomplete_open:
                        self._navigate_autocomplete(key)
                    else:
                        self._navigate_history(key)
                    continue
                if key == "tab":
                    if self.slash_menu_open and self._filtered_commands():
                        self._accept_slash_menu_selection()
                    elif self.autocomplete_open:
                        self._accept_autocomplete_selection()
                    else:
                        self._attempt_path_completion()
                        self.paint()
                    continue
                if key in {"left", "right", "home", "end"}:
                    self._move_input_cursor(key)
                    self.paint()
                    continue
                if key == "ctrl-u":
                    self._kill_to_line_start()
                    self.paint()
                    continue
                if key == "ctrl-z":
                    self._undo_edit()
                    self.paint()
                    continue
                if key == "ctrl-y":
                    self._redo_edit()
                    self.paint()
                    continue
                if len(key) == 1 and key.isprintable():
                    self._insert_input_text(key)
                    self.paint()
        finally:
            self._restore_terminal_mode()

    def wait_for_active_turn_interrupt(
        self,
        done_event: Any,
        abort_event: Any,
        *,
        poll_seconds: float = 0.05,
        accept_queue: bool = False,
    ) -> str:
        """Watch stdin during an active turn; optionally a mid-turn editor.

        Returns one of :data:`TURN_SETTLED`, :data:`TURN_ABORTED`, or
        :data:`TURN_STEERED`. With ``accept_queue=False`` (e.g. a ``!`` shell
        run) it only watches for Escape (sets ``abort_event``, returns
        ``aborted``) and Ctrl-C (sets ``abort_event``, raises). With
        ``accept_queue=True`` (a provider turn) it also accepts editor input
        mid-turn: a normal Enter enqueues a steering message and interrupts the
        turn (returns ``steered``), Alt+Enter enqueues a follow-up without
        interrupting, Alt+Up restores queued messages to the editor, and
        Escape/Ctrl-C abort (the caller restores the queue to the editor).
        """

        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while not done_event.is_set():
                # Keep the streaming frame coherent if the terminal is resized
                # mid-turn: streamed chunks repaint at the live size, but a
                # stalled stream would not, so poll here too.
                self._poll_resize_repaint()
                key = self._read_key_if_available(fd, poll_seconds)
                if key is None:
                    continue
                if key == "esc":
                    abort_event.set()
                    return TURN_ABORTED
                if key == "ctrl-c":
                    abort_event.set()
                    raise KeyboardInterrupt
                if not accept_queue:
                    if key == "paste":
                        # A paste mid-turn is not editor input; drop it so its
                        # body never lingers into the next prompt.
                        self._pending_paste = ""
                    continue
                # accept_queue: a mid-turn editor for steering/follow-up.
                if key == "enter":
                    text = self.input_text
                    self._reset_mid_turn_input()
                    if not text.strip():
                        self.paint()
                        continue
                    # A recognized local command (`/…` or `!…`) is never queued
                    # for the provider: like Pi's editor, Enter runs it
                    # immediately rather than steering. It interrupts the turn
                    # and is handed to the session loop to dispatch locally.
                    if self._submitted_text_is_local_command(text):
                        self._pending_command = text
                        abort_event.set()
                        self.paint()
                        return TURN_LOCAL_COMMAND
                    self.enqueue_steering(text)
                    abort_event.set()
                    self.paint()
                    return TURN_STEERED
                if key == "alt-enter":
                    text = self.input_text
                    self._reset_mid_turn_input()
                    self.enqueue_follow_up(text)
                    self.paint()
                    continue
                if key == "alt-up":
                    self.restore_pending_to_editor()
                    self.paint()
                    continue
                if key == "paste":
                    self._insert_paste(self._pending_paste)
                    self._pending_paste = ""
                    self.paint()
                    continue
                if key == "backspace":
                    self._delete_before_cursor()
                    self.paint()
                    continue
                if key in {"left", "right", "home", "end"}:
                    self._move_input_cursor(key)
                    self.paint()
                    continue
                if len(key) == 1 and key.isprintable():
                    self._insert_input_text(key)
                    self.paint()
            return TURN_SETTLED
        finally:
            self._restore_terminal_mode()

    def _reset_mid_turn_input(self) -> None:
        self.input_text = ""
        self.input_cursor = 0
        self.slash_menu_open = False
        self._close_autocomplete()

    def run_model_selector(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int = 0,
    ) -> int | None:
        """Drive the interactive provider/model selector; return a chosen index.

        Renders the supplied rows in the live region and reads raw keys: up/down
        move the highlight (wrapping), ``Enter`` chooses the highlighted row when
        it is selectable, and ``Esc`` / ``Ctrl-C`` / ``Ctrl-D`` / EOF cancel.
        Returns the chosen index, or ``None`` when cancelled or when no row is
        selectable. This method never invokes the provider, tools, or a model
        turn; it is pure local navigation that the caller acts on afterwards.
        """

        self.model_selector_options = tuple(options)
        if not self.model_selector_options:
            return None
        self.model_selector_open = True
        self.model_selector_selection = max(
            0, min(current_index, len(self.model_selector_options) - 1)
        )
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
                    self._close_model_selector()
                    return None
                if key == "paste":
                    self._pending_paste = ""
                    continue
                if key in {"up", "down"}:
                    self._navigate_model_selector(key)
                    continue
                if key == "enter":
                    index = self.model_selector_selection
                    option = self.model_selector_options[index]
                    if option.selectable:
                        self._close_model_selector()
                        return index
                    continue
        finally:
            self._restore_terminal_mode()

    def _navigate_model_selector(self, key: str) -> None:
        total = len(self.model_selector_options)
        if total == 0:
            return
        delta = -1 if key == "up" else 1
        self.model_selector_selection = (
            self.model_selector_selection + delta
        ) % total
        self.paint()

    def _close_model_selector(self) -> None:
        self.model_selector_open = False
        self.model_selector_options = ()
        self.model_selector_selection = 0
        self.paint()

    def run_scoped_models_selector(
        self,
        rows: Sequence[ScopedModelRow],
        *,
        checked: Iterable[int] = (),
    ) -> frozenset[str] | None:
        """Drive the ``/scoped-models`` multi-select overlay; return the scope.

        Renders one checkbox row per available model. Up/Down move, Space toggles
        membership of the highlighted row, ``a`` enables all, ``c`` clears all,
        Enter saves and returns the chosen ``provider/model`` reference set, and
        Esc/Ctrl-C/Ctrl-D cancel (returning ``None``). Runs no provider turn.
        """

        self.scoped_models_rows = tuple(rows)
        if not self.scoped_models_rows:
            return None
        self.scoped_models_checked = {
            index
            for index in checked
            if 0 <= index < len(self.scoped_models_rows)
            and self.scoped_models_rows[index].available
        }
        self.scoped_models_selection = next(
            (i for i, row in enumerate(self.scoped_models_rows) if row.available), 0
        )
        self.scoped_models_open = True
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
                    self._close_scoped_models_selector()
                    return None
                if key == "paste":
                    self._pending_paste = ""
                    continue
                if key in {"up", "down"}:
                    self._navigate_scoped_models(key)
                    continue
                if key == " ":
                    self._toggle_scoped_models_row()
                    continue
                if key == "a":
                    self.scoped_models_checked = {
                        i for i, row in enumerate(self.scoped_models_rows) if row.available
                    }
                    self.paint()
                    continue
                if key == "c":
                    self.scoped_models_checked = set()
                    self.paint()
                    continue
                if key == "enter":
                    chosen = frozenset(
                        self.scoped_models_rows[i].reference
                        for i in sorted(self.scoped_models_checked)
                    )
                    self._close_scoped_models_selector()
                    return chosen
        finally:
            self._restore_terminal_mode()

    def _navigate_scoped_models(self, key: str) -> None:
        total = len(self.scoped_models_rows)
        if total == 0:
            return
        delta = -1 if key == "up" else 1
        index = self.scoped_models_selection
        for _ in range(total):
            index = (index + delta) % total
            if self.scoped_models_rows[index].available:
                break
        self.scoped_models_selection = index
        self.paint()

    def _toggle_scoped_models_row(self) -> None:
        index = self.scoped_models_selection
        if not (0 <= index < len(self.scoped_models_rows)):
            return
        if not self.scoped_models_rows[index].available:
            return
        if index in self.scoped_models_checked:
            self.scoped_models_checked.discard(index)
        else:
            self.scoped_models_checked.add(index)
        self.paint()

    def _close_scoped_models_selector(self) -> None:
        self.scoped_models_open = False
        self.scoped_models_rows = ()
        self.scoped_models_selection = 0
        self.scoped_models_checked = set()
        self.paint()

    def _scoped_models_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        rows = self.scoped_models_rows
        footer = [
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        title = _FrameLine(
            self._clip(
                " Scoped models — ↑/↓ move · space toggle · a all · c clear · "
                "enter save · esc cancel",
                width,
            ),
            "selector_title",
        )
        max_rows = max(1, height - 4)
        total = len(rows)
        visible_count = min(total, max_rows)
        start = max(
            0,
            min(
                self.scoped_models_selection - (visible_count // 2),
                max(0, total - visible_count),
            ),
        )
        rendered: list[_FrameLine] = []
        for offset in range(start, start + visible_count):
            row = rows[offset]
            selected = offset == self.scoped_models_selection
            box = "[x]" if offset in self.scoped_models_checked else "[ ]"
            suffix = "" if row.available else "  [unavailable]"
            prefix = "→ " if selected else "  "
            if selected:
                kind = "selector_option_selected"
            elif row.available:
                kind = "selector_option"
            else:
                kind = "selector_option_disabled"
            rendered.append(
                _FrameLine(
                    self._clip(f"{prefix}{box} {row.reference}{suffix}", width), kind
                )
            )
        lines = [title, *rendered]
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(
                        f"  ({self.scoped_models_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        lines.extend(footer)
        return lines

    def run_settings_dialog(
        self,
        rows: Sequence[SettingsRow],
        *,
        on_local_action: Callable[[str], Sequence[SettingsRow]],
        exit_actions: frozenset[str] = frozenset(),
        current_index: int | None = None,
    ) -> str | None:
        """Drive the interactive ``/settings`` dialog as a live overlay.

        Renders the supplied rows in the live region and reads raw keys: up/down
        move the highlight between actionable rows (wrapping, skipping headers
        and read-only status rows), and ``Enter``/``Space`` activate the
        highlighted action row. ``Esc`` / ``Ctrl-C`` / ``Ctrl-D`` / EOF close the
        dialog and return ``None``.

        Activating an action whose identifier is in ``exit_actions`` closes the
        dialog and returns that identifier so the caller can run a flow that
        needs the terminal itself (the provider/model selector, or interactive
        auth). Any other action is *local*: ``on_local_action`` is invoked with
        the identifier and must return the rebuilt rows, and the dialog stays
        open and re-renders in place. This method never invokes the provider,
        tools, or a model turn; it is pure local navigation/state toggling that
        the caller acts on afterwards.
        """

        self.settings_dialog_rows = tuple(rows)
        if not self.settings_dialog_rows:
            return None
        self.settings_dialog_open = True
        self.settings_dialog_selection = self._initial_settings_selection(
            current_index
        )
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
                    self._close_settings_dialog()
                    return None
                if key == "paste":
                    self._pending_paste = ""
                    continue
                if key in {"up", "down"}:
                    self._navigate_settings_dialog(key)
                    continue
                if key in {"enter", " "}:
                    if not (0 <= self.settings_dialog_selection < len(self.settings_dialog_rows)):
                        continue
                    row = self.settings_dialog_rows[self.settings_dialog_selection]
                    if row.action is None:
                        continue
                    if row.action in exit_actions:
                        self._close_settings_dialog()
                        return row.action
                    rebuilt = on_local_action(row.action)
                    self.settings_dialog_rows = tuple(rebuilt)
                    if not self.settings_dialog_rows:
                        self._close_settings_dialog()
                        return None
                    self.settings_dialog_selection = self._clamp_settings_selection(
                        self.settings_dialog_selection
                    )
                    self.paint()
                    continue
        finally:
            self._restore_terminal_mode()

    def _actionable_settings_indices(self) -> list[int]:
        return [
            index
            for index, row in enumerate(self.settings_dialog_rows)
            if row.action is not None
        ]

    def _initial_settings_selection(self, current_index: int | None) -> int:
        actionable = self._actionable_settings_indices()
        if not actionable:
            return 0
        if current_index is not None and current_index in actionable:
            return current_index
        return actionable[0]

    def _clamp_settings_selection(self, selection: int) -> int:
        actionable = self._actionable_settings_indices()
        if not actionable:
            return min(max(0, selection), max(0, len(self.settings_dialog_rows) - 1))
        if selection in actionable:
            return selection
        # The previously highlighted action may have shifted; land on the
        # nearest actionable row at or after the old position.
        for index in actionable:
            if index >= selection:
                return index
        return actionable[-1]

    def _navigate_settings_dialog(self, key: str) -> None:
        actionable = self._actionable_settings_indices()
        if not actionable:
            return
        delta = -1 if key == "up" else 1
        if self.settings_dialog_selection in actionable:
            position = actionable.index(self.settings_dialog_selection)
            position = (position + delta) % len(actionable)
        else:
            position = 0 if delta > 0 else len(actionable) - 1
        self.settings_dialog_selection = actionable[position]
        self.paint()

    def _close_settings_dialog(self) -> None:
        self.settings_dialog_open = False
        self.settings_dialog_rows = ()
        self.settings_dialog_selection = 0
        self.paint()

    def set_input_text(self, text: str) -> None:
        """Pre-fill the next ``read_line`` prompt with ``text``.

        Used by ``/tree`` to rehydrate the editor with a selected user message
        so the user can edit it into a new branch.
        """

        self._pending_initial_text = text

    def run_tree_selector(
        self,
        *,
        build_rows: Callable[[str], Sequence["TreeSelectorRow"]],
        filter_modes: Sequence[str],
        initial_filter: str,
        on_label_toggle: Callable[[str], None],
    ) -> str | None:
        """Drive the interactive ``/tree`` selector; return a chosen entry id.

        ``build_rows(filter_mode)`` returns the visible rows for a filter;
        up/down move the highlight, ``Ctrl-O`` cycles the filter mode, ``L``
        (Shift-L) toggles a label on the highlighted entry via
        ``on_label_toggle``, ``Enter`` selects the highlighted entry, and
        ``Esc``/``Ctrl-C``/``Ctrl-D``/EOF cancel. Runs no provider turn and no
        model-visible tool call; the caller applies the chosen entry's
        selection semantics afterward.
        """

        self.tree_selector_filter = (
            initial_filter if initial_filter in filter_modes else filter_modes[0]
        )
        self.tree_selector_rows = tuple(build_rows(self.tree_selector_filter))
        self.tree_selector_open = True
        self.tree_selector_selection = self._initial_tree_selection()
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
                    self._close_tree_selector()
                    return None
                if key == "paste":
                    self._pending_paste = ""
                    continue
                if key in {"up", "down"}:
                    self._navigate_tree_selector(key)
                    continue
                if key == "ctrl-o":
                    position = list(filter_modes).index(self.tree_selector_filter)
                    self.tree_selector_filter = filter_modes[
                        (position + 1) % len(filter_modes)
                    ]
                    self.tree_selector_rows = tuple(
                        build_rows(self.tree_selector_filter)
                    )
                    self.tree_selector_selection = self._initial_tree_selection()
                    self.paint()
                    continue
                if key == "L":
                    if 0 <= self.tree_selector_selection < len(
                        self.tree_selector_rows
                    ):
                        entry_id = self.tree_selector_rows[
                            self.tree_selector_selection
                        ].entry_id
                        on_label_toggle(entry_id)
                        self.tree_selector_rows = tuple(
                            build_rows(self.tree_selector_filter)
                        )
                        self.tree_selector_selection = min(
                            self.tree_selector_selection,
                            max(0, len(self.tree_selector_rows) - 1),
                        )
                        self.paint()
                    continue
                if key == "enter":
                    if not self.tree_selector_rows:
                        continue
                    entry_id = self.tree_selector_rows[
                        self.tree_selector_selection
                    ].entry_id
                    self._close_tree_selector()
                    return entry_id
        finally:
            self._restore_terminal_mode()

    def _initial_tree_selection(self) -> int:
        """Default the highlight to the last active-path row, else the last row."""

        active = [
            index
            for index, row in enumerate(self.tree_selector_rows)
            if row.active
        ]
        if active:
            return active[-1]
        return max(0, len(self.tree_selector_rows) - 1)

    def _navigate_tree_selector(self, key: str) -> None:
        total = len(self.tree_selector_rows)
        if total == 0:
            return
        delta = -1 if key == "up" else 1
        self.tree_selector_selection = (
            self.tree_selector_selection + delta
        ) % total
        self.paint()

    def _close_tree_selector(self) -> None:
        self.tree_selector_open = False
        self.tree_selector_rows = ()
        self.tree_selector_selection = 0
        self.paint()

    def _tree_selector_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        """Compose the interactive ``/tree`` selector overlay."""

        footer = [
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        title = _FrameLine(
            self._clip(
                " Session tree — ↑/↓ move · enter select · L label · "
                f"^O filter ({self.tree_selector_filter}) · esc cancel",
                width,
            ),
            "selector_title",
        )
        rows_data = self.tree_selector_rows
        max_rows = max(1, height - 4)
        total = len(rows_data)
        if total == 0:
            return [
                title,
                _FrameLine(self._clip("  (empty session tree)", width), "normal"),
                *footer,
            ]
        visible_count = min(total, max_rows)
        start = max(
            0,
            min(
                self.tree_selector_selection - (visible_count // 2),
                max(0, total - visible_count),
            ),
        )
        visible = rows_data[start : start + visible_count]
        rows: list[_FrameLine] = []
        for offset, row in enumerate(visible, start=start):
            selected = offset == self.tree_selector_selection
            prefix = "→ " if selected else "  "
            marker = "*" if row.active else " "
            kind = "selector_option_selected" if selected else "selector_option"
            rows.append(
                _FrameLine(
                    self._clip(f"{prefix}{marker} {row.label}", width), kind
                )
            )
        lines = [title, *rows]
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(
                        f"  ({self.tree_selector_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        lines.extend(footer)
        return lines

    # -- custom extension overlay (ctx.ui.custom) ---------------------------

    def run_custom_component(
        self, factory: "Callable[[Callable[..., None]], object]"
    ) -> object:
        """Drive a trusted extension custom component; return its result.

        `factory(done)` builds a component exposing `render(width) -> list[str]`
        and `handle_input(key) -> None`; the component calls `done(result)` to
        finish. The driver paints the component's lines as a full-screen inline
        overlay and routes decoded keys to it until it finishes (or the input
        stream ends / errors, which finishes with ``None``). Runs no provider
        turn. Returns the result passed to `done`, or ``None`` if cancelled.
        """

        self._custom_done = False
        self._custom_result = None

        def done(result: object = None) -> None:
            if not self._custom_done:
                self._custom_done = True
                self._custom_result = result

        component = factory(done)
        try:
            self._custom_component = component
            self.custom_overlay_open = True
            self.paint()
            fd = self.input_stream.fileno()
            self._enter_raw_mode()
            while not self._custom_done:
                key = self._read_key_polling_resize(fd)
                if key is None:
                    # Stream EOF / read error: cancel deterministically.
                    done(None)
                    break
                if key == "paste":
                    # A bracketed-paste marker carries no decoded text here;
                    # ignore it rather than forwarding a sentinel to the
                    # component.
                    continue
                try:
                    component.handle_input(key)  # type: ignore[attr-defined]
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:  # noqa: BLE001 - a bad component cancels
                    done(None)
                    break
                if not self._custom_done:
                    self.paint()
        finally:
            self.custom_overlay_open = False
            self._custom_component = None
            # Relinquish the screen immediately: repaint the normal frame so the
            # overlay does not linger until some unrelated later paint. Guarded
            # so a repaint failure never masks the in-flight result/exception.
            try:
                self.paint()
            except (OSError, ValueError):
                pass
            self._restore_terminal_mode()
        return self._custom_result

    def run_extension_select(
        self, title: str, options: Sequence[str]
    ) -> str | None:
        """Run a Pi-shaped extension selector over string options."""

        choices = tuple(str(option) for option in options if str(option))
        if not choices:
            return None
        result = self.run_custom_component(
            lambda done: _ExtensionSelectComponent(str(title), choices, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_input(
        self, title: str, placeholder: str | None = None
    ) -> str | None:
        """Run a Pi-shaped extension text input overlay."""

        result = self.run_custom_component(
            lambda done: _ExtensionInputComponent(str(title), placeholder, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_confirm(self, title: str, message: str) -> bool:
        """Run a Pi-shaped extension confirmation dialog."""

        result = self.run_custom_component(
            lambda done: _ExtensionConfirmComponent(str(title), str(message), done)
        )
        return result == "Yes"

    def set_extension_status(self, key: str, text: str | None) -> None:
        """Set or clear an extension status row in the live frame."""

        safe_key = _safe_extension_status_key(key)
        if safe_key is None:
            return
        with self._paint_lock:
            if text is None:
                self.extension_status.pop(safe_key, None)
            else:
                self.extension_status[safe_key] = sanitize_label_text(str(text))
        self.paint()

    def set_extension_working_message(self, message: str | None = None) -> None:
        """Set the sticky working label used by future provider turns."""

        with self._paint_lock:
            self.extension_working_message = (
                None if message is None else sanitize_label_text(str(message))
            )
        self.paint()

    def set_extension_working_visible(self, visible: bool) -> None:
        """Show or hide the sticky working row for future provider turns."""

        with self._paint_lock:
            self.extension_working_visible = bool(visible)
            if not self.extension_working_visible:
                self.working_text = ""
        self.paint()

    def _custom_overlay_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        """Compose the custom extension overlay from the component's lines.

        The component owns its own layout (it is trusted local code, matching
        the extension trust boundary), but the driver still sanitizes and clips
        rendered lines before they reach the terminal frame.
        """

        component = self._custom_component
        if component is None:
            return []
        try:
            raw = component.render(width)  # type: ignore[attr-defined]
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - never let a bad render crash paint
            raw = ["(custom component render error)"]
        lines = [
            _clip_custom_overlay_text(str(line), width) for line in (raw or [])
        ][: max(1, height)]
        return [_FrameLine(line, "normal") for line in lines]

    # -- interactive session picker (/resume + -r overlay) ------------------

    def run_session_picker(
        self,
        *,
        project_sessions: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry],
        current_path: Path | None = None,
        on_rename: Callable[[Path, str], None] | None = None,
        on_delete: Callable[[Path], tuple[bool, str]] | None = None,
        now: float | None = None,
    ) -> Path | None:
        """Drive the interactive session picker; return a chosen session file.

        Typing searches; ``↑/↓`` move; ``Enter`` opens the highlighted session;
        ``Tab`` toggles current-project / all-projects scope; ``Ctrl+P`` toggles
        the file-path column; ``Ctrl+S`` cycles the sort; ``Ctrl+N`` filters to
        named sessions; ``Ctrl+R`` renames and ``Ctrl+X`` deletes (each with an
        in-overlay confirmation/edit); ``Esc``/``Ctrl+C``/``Ctrl+D``/EOF cancel.
        Runs no provider turn and no model-visible tool call.
        """

        import time as _time

        self._session_picker_project = list(project_sessions)
        self._session_picker_all = list(all_sessions)
        self.session_picker_current = current_path
        self.session_picker_query = ""
        self.session_picker_scope = "current"
        self.session_picker_sort = "recent"
        self.session_picker_named_only = False
        self.session_picker_show_path = False
        self.session_picker_mode = "list"
        self.session_picker_input = ""
        self.session_picker_status = ""
        self.session_picker_selection = 0
        self._session_picker_now = now if now is not None else _time.time()
        self.session_picker_open = True
        self._rebuild_session_picker_rows()
        self._session_picker_select_current()
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key_polling_resize(fd)
                outcome = self._handle_session_picker_key(
                    key, on_rename=on_rename, on_delete=on_delete
                )
                if outcome is _PICKER_CONTINUE:
                    continue
                self._close_session_picker()
                # Past the sentinel, the outcome is the chosen path or a cancel.
                return cast("Path | None", outcome)
        finally:
            self._restore_terminal_mode()

    def _rebuild_session_picker_rows(self) -> None:
        rows = build_session_picker_rows(
            self._session_picker_project,
            self._session_picker_all,
            scope=self.session_picker_scope,
            query=self.session_picker_query,
            sort=self.session_picker_sort,
            named_only=self.session_picker_named_only,
            current_path=self.session_picker_current,
        )
        self.session_picker_rows = tuple(rows)
        if self.session_picker_selection >= len(rows):
            self.session_picker_selection = max(0, len(rows) - 1)

    def _session_picker_select_current(self) -> None:
        for index, row in enumerate(self.session_picker_rows):
            if row.is_current:
                self.session_picker_selection = index
                return

    def _selected_session_row(self) -> "SessionPickerRow | None":
        if 0 <= self.session_picker_selection < len(self.session_picker_rows):
            return self.session_picker_rows[self.session_picker_selection]
        return None

    def _handle_session_picker_key(
        self,
        key: str | None,
        *,
        on_rename: Callable[[Path, str], None] | None,
        on_delete: Callable[[Path], tuple[bool, str]] | None,
    ) -> "Path | None | object":
        if self.session_picker_mode == "rename":
            return self._handle_session_rename_key(key, on_rename)
        if self.session_picker_mode == "confirm-delete":
            return self._handle_session_delete_key(key, on_delete)
        # --- list mode ----------------------------------------------------
        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            return None
        if key == "paste":
            self._pending_paste = ""
            return _PICKER_CONTINUE
        if key in {"up", "down"}:
            self._navigate_session_picker(key)
            return _PICKER_CONTINUE
        if key == "enter":
            row = self._selected_session_row()
            if row is not None:
                return row.path
            return _PICKER_CONTINUE
        if key == "tab":
            self.session_picker_scope = (
                "all" if self.session_picker_scope == "current" else "current"
            )
            self.session_picker_selection = 0
            self.session_picker_status = ""
            self._rebuild_session_picker_rows()
            self.paint()
            return _PICKER_CONTINUE
        if key == "ctrl-p":
            self.session_picker_show_path = not self.session_picker_show_path
            self.paint()
            return _PICKER_CONTINUE
        if key == "\x13":  # Ctrl+S — cycle sort
            self.session_picker_sort = (
                "name" if self.session_picker_sort == "recent" else "recent"
            )
            self._rebuild_session_picker_rows()
            self.paint()
            return _PICKER_CONTINUE
        if key == "\x0e":  # Ctrl+N — named-only filter
            self.session_picker_named_only = not self.session_picker_named_only
            self.session_picker_selection = 0
            self._rebuild_session_picker_rows()
            self.paint()
            return _PICKER_CONTINUE
        if key == "\x12":  # Ctrl+R — rename
            row = self._selected_session_row()
            if on_rename is not None and row is not None:
                self.session_picker_mode = "rename"
                self.session_picker_input = row.name or ""
                self.session_picker_status = ""
                self.paint()
            return _PICKER_CONTINUE
        if key == "\x18":  # Ctrl+X — delete (confirm)
            row = self._selected_session_row()
            if on_delete is not None and row is not None:
                if row.is_current:
                    self.session_picker_status = "cannot delete the active session"
                else:
                    self.session_picker_mode = "confirm-delete"
                self.paint()
            return _PICKER_CONTINUE
        if key == "backspace":
            if self.session_picker_query:
                self.session_picker_query = self.session_picker_query[:-1]
                self.session_picker_selection = 0
                self._rebuild_session_picker_rows()
                self.paint()
            return _PICKER_CONTINUE
        if len(key) == 1 and key.isprintable():
            self.session_picker_query += key
            self.session_picker_selection = 0
            self._rebuild_session_picker_rows()
            self.paint()
        return _PICKER_CONTINUE

    def _handle_session_rename_key(
        self, key: str | None, on_rename: Callable[[Path, str], None] | None
    ) -> "Path | None | object":
        # Ctrl-C/Ctrl-D (and EOF) cancel the whole picker from any sub-mode;
        # Esc backs out of the rename to the list (see below).
        if key is None or key in {"ctrl-c", "ctrl-d"}:
            return None
        if key == "esc":
            self.session_picker_mode = "list"
            self.session_picker_input = ""
            self.paint()
            return _PICKER_CONTINUE
        if key == "enter":
            row = self._selected_session_row()
            name = self.session_picker_input.strip()
            if row is not None and name and on_rename is not None:
                on_rename(row.path, name)
                self._apply_session_rename(row.path, name)
                self.session_picker_status = f"renamed to {name}"
            self.session_picker_mode = "list"
            self.session_picker_input = ""
            self._rebuild_session_picker_rows()
            self.paint()
            return _PICKER_CONTINUE
        if key == "backspace":
            self.session_picker_input = self.session_picker_input[:-1]
            self.paint()
            return _PICKER_CONTINUE
        if len(key) == 1 and key.isprintable():
            self.session_picker_input += key
            self.paint()
        return _PICKER_CONTINUE

    def _handle_session_delete_key(
        self, key: str | None, on_delete: Callable[[Path], tuple[bool, str]] | None
    ) -> "Path | None | object":
        # Ctrl-C/Ctrl-D (and EOF) cancel the whole picker; Esc/Enter/n take the
        # safe [y/N] default and back out to the list.
        if key is None or key in {"ctrl-c", "ctrl-d"}:
            return None
        # The prompt is `[y/N]`: only an explicit `y` confirms; Enter (and Esc/n)
        # take the safe default and cancel the deletion.
        if key in {"y", "Y"}:
            row = self._selected_session_row()
            if row is not None and on_delete is not None:
                ok, detail = on_delete(row.path)
                if ok:
                    self._remove_session_entry(row.path)
                self.session_picker_status = detail
            self.session_picker_mode = "list"
            self.session_picker_selection = 0
            self._rebuild_session_picker_rows()
            self.paint()
            return _PICKER_CONTINUE
        if key in {"esc", "enter", "n", "N"}:
            self.session_picker_mode = "list"
            self.paint()
        return _PICKER_CONTINUE

    def _navigate_session_picker(self, key: str) -> None:
        total = len(self.session_picker_rows)
        if total == 0:
            return
        delta = -1 if key == "up" else 1
        self.session_picker_selection = (
            self.session_picker_selection + delta
        ) % total
        self.paint()

    def _apply_session_rename(self, path: Path, name: str) -> None:
        def relabel(entries: list[SessionListEntry]) -> list[SessionListEntry]:
            updated: list[SessionListEntry] = []
            for entry in entries:
                if entry.path == path:
                    updated.append(
                        SessionListEntry(
                            path=entry.path,
                            session_id=entry.session_id,
                            name=name,
                            message_count=entry.message_count,
                            cwd=entry.cwd,
                            mtime=entry.mtime,
                        )
                    )
                else:
                    updated.append(entry)
            return updated

        self._session_picker_project = relabel(self._session_picker_project)
        self._session_picker_all = relabel(self._session_picker_all)

    def _remove_session_entry(self, path: Path) -> None:
        self._session_picker_project = [
            e for e in self._session_picker_project if e.path != path
        ]
        self._session_picker_all = [
            e for e in self._session_picker_all if e.path != path
        ]

    def _close_session_picker(self) -> None:
        self.session_picker_open = False
        self.session_picker_rows = ()
        self.session_picker_selection = 0
        self.session_picker_mode = "list"
        self.session_picker_input = ""
        self.session_picker_query = ""
        self._session_picker_project = []
        self._session_picker_all = []
        self.paint()

    def _session_picker_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        """Compose the interactive session-picker overlay."""

        footer = [
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        scope_label = (
            "all projects" if self.session_picker_scope == "all" else "this project"
        )
        title = _FrameLine(
            self._clip(
                f" Resume session — {scope_label} · sort:{self.session_picker_sort}"
                + ("· named" if self.session_picker_named_only else "")
                + " · ↑/↓ enter · ^P path ^S sort ^N named tab scope"
                + " ^R rename ^X delete · esc cancel",
                width,
            ),
            "selector_title",
        )
        if self.session_picker_mode == "rename":
            # The rename buffer is seeded from the existing (user-controlled)
            # session name, so sanitize it before rendering to keep terminal
            # escape sequences out of the live frame.
            prompt = _FrameLine(
                self._clip(
                    f"  rename: {sanitize_label_text(self.session_picker_input)}▏",
                    width,
                ),
                "input",
            )
        elif self.session_picker_mode == "confirm-delete":
            row = self._selected_session_row()
            shown = (row.name or row.session_id[:8]) if row else ""
            prompt = _FrameLine(
                self._clip(
                    f"  delete {sanitize_label_text(shown)}? [y/N]", width
                ),
                "notice",
            )
        else:
            prompt = _FrameLine(
                self._clip(f"  search: {self.session_picker_query}", width),
                "normal",
            )
        status_lines: list[_FrameLine] = []
        if self.session_picker_status:
            # The status echoes user-controlled names / delete details, so
            # sanitize it against terminal escape injection like the row labels.
            status_lines.append(
                _FrameLine(
                    self._clip(
                        f"  {sanitize_label_text(self.session_picker_status)}",
                        width,
                    ),
                    "notice",
                )
            )

        rows_data = self.session_picker_rows
        # Reserve: title + prompt + status + scroll indicator + 2 footer rows.
        max_rows = max(1, height - 5 - len(status_lines))
        total = len(rows_data)
        if total == 0:
            empty = _FrameLine(
                self._clip("  (no native sessions)", width), "normal"
            )
            return [title, prompt, *status_lines, empty, *footer]
        visible_count = min(total, max_rows)
        start = max(
            0,
            min(
                self.session_picker_selection - (visible_count // 2),
                max(0, total - visible_count),
            ),
        )
        visible = rows_data[start : start + visible_count]
        rendered: list[_FrameLine] = []
        for offset, row in enumerate(visible, start=start):
            selected = offset == self.session_picker_selection
            prefix = "→ " if selected else "  "
            label = format_session_picker_label(
                row,
                show_path=self.session_picker_show_path,
                show_cwd=self.session_picker_scope == "all",
                now=self._session_picker_now,
            )
            kind = "selector_option_selected" if selected else "selector_option"
            rendered.append(_FrameLine(self._clip(f"{prefix}{label}", width), kind))
        lines = [title, prompt, *status_lines, *rendered]
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(
                        f"  ({self.session_picker_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        lines.extend(footer)
        return lines

    def close(self) -> None:
        self._restore_terminal_mode()
        self._remove_resize_handler()
        if self._closed:
            return
        self._closed = True
        try:
            out: list[str] = []
            # Move below the live region so the next shell prompt does not
            # overwrite the footer, then restore the cursor.
            if self._live_height > 0:
                lines_below = (self._live_height - 1) - self._live_input_row
                if lines_below > 0:
                    out.append(f"\x1b[{lines_below}B")
                out.append("\r")
            out.append("\x1b[?25h\n")
            self.terminal_stream.write("".join(out))
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

    def set_footer_text(self, text: str) -> None:
        lines = text.splitlines()
        if len(lines) >= 2:
            self.footer_lines = (lines[0], lines[1])
        elif lines:
            self.footer_lines = (lines[0], "")
        else:
            self.footer_lines = ("", "")
        self.paint()

    def submit_user_message(self, text: str) -> None:
        self._settle_reasoning()
        self.assistant_text = ""
        self.working_text = ""
        self._history_blocks.append(("user", tuple(text.splitlines() or [""])))
        self.paint()

    def begin_assistant_turn(self) -> None:
        self._settle_reasoning()
        self.assistant_text = ""
        self.working_text = ""
        self.paint()

    def set_working(self, text: str) -> None:
        self.working_text = text
        self.paint()

    def clear_working(self) -> None:
        if not self.working_text:
            return
        self.working_text = ""
        self.paint()

    def append_assistant(self, chunk: str) -> None:
        if not chunk:
            return
        self._settle_reasoning()
        self.assistant_text += chunk
        self.paint()

    def settle_assistant(self, final_text: str = "") -> None:
        self.working_text = ""
        self._settle_reasoning()
        if final_text and not self.assistant_text:
            self.assistant_text = final_text
        if self.assistant_text:
            self._history_blocks.append(
                ("assistant", tuple(self.assistant_text.splitlines() or [""]))
            )
            self.assistant_text = ""
        self.paint()

    def show_operation_aborted(self) -> None:
        self.working_text = ""
        self._settle_reasoning()
        if self.assistant_text:
            self._history_blocks.append(
                ("assistant", tuple(self.assistant_text.splitlines() or [""]))
            )
            self.assistant_text = ""
        self._history_blocks.append(("error", ("Operation aborted",)))
        self.paint()

    def append_reasoning(self, chunk: str) -> None:
        if not chunk:
            return
        self.working_text = ""
        cleaned = chunk.replace("**", "")
        self.reasoning_text += cleaned
        self.paint()

    def _settle_reasoning(self) -> None:
        if not self.reasoning_text:
            return
        # When thinking blocks are folded (Ctrl+T), the settled reasoning is
        # deferred (retained, not committed to scrollback) so the fold holds but
        # the content is not lost — toggling visibility back reveals it.
        if self.thinking_hidden:
            self._deferred_reasoning.append(self.reasoning_text)
        else:
            self._history_blocks.append(
                ("reasoning", tuple(self.reasoning_text.splitlines() or [""]))
            )
        self.reasoning_text = ""

    def set_thinking_hidden(self, hidden: bool) -> None:
        """Set the Ctrl+T thinking-fold flag, revealing deferred reasoning.

        Folding hides subsequent/live reasoning and defers settled blocks;
        unfolding commits any deferred reasoning into history so it becomes
        visible (committed fresh now rather than retro-written into the host
        terminal's existing scrollback, preserving the inline contract).
        """

        self.thinking_hidden = hidden
        if not hidden and self._deferred_reasoning:
            for text in self._deferred_reasoning:
                self._history_blocks.append(
                    ("reasoning", tuple(text.splitlines() or [""]))
                )
            self._deferred_reasoning.clear()
            self.paint()

    def add_notice(self, text: str) -> None:
        self._settle_reasoning()
        safe_lines = tuple(
            sanitize_label_text(line) for line in str(text).splitlines()
        ) or ("",)
        self._history_blocks.append(("notice", safe_lines))
        self.paint()

    def show_settings(self, lines: Iterable[str]) -> None:
        """Render a read-only settings/status overlay into the history region.

        The overlay is display-only: it shows safe provider/model/status
        information and never switches models, mutates auth state, invokes
        tools, or creates a provider turn. It is rendered through the same
        whole-frame paint path as every other history block.
        """

        self._settle_reasoning()
        self.working_text = ""
        self._history_blocks.append(("settings", tuple(lines) or ("",)))
        self.paint()

    def add_tool_call(self, header: str) -> None:
        self._settle_reasoning()
        self.working_text = ""
        self.tool_output_text = ""
        if header.startswith("read ") or header.startswith("read resource "):
            self._history_blocks.append(("tool_read", (_compact_read_header(header),)))
        else:
            self._history_blocks.append(("tool", (header,)))
        self.paint()

    def append_tool_output(self, chunk: str) -> None:
        """Stream incremental tool output into the live region as it is produced.

        Used by long-running tools (`bash`) so the live frame shows e.g. pytest
        dots scrolling in real time, matching Pi. Only a bounded tail is kept
        live; the full bounded result is committed by `add_tool_result` when the
        tool settles.
        """

        if not chunk:
            return
        self._settle_reasoning()
        self.working_text = ""
        self.tool_output_text += chunk
        if len(self.tool_output_text) > _TOOL_STREAM_LIVE_MAX_CHARS:
            self.tool_output_text = self.tool_output_text[-_TOOL_STREAM_LIVE_MAX_CHARS:]
        self.paint()

    def add_tool_result(
        self,
        *,
        lines: Iterable[str],
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        self._settle_reasoning()
        self.tool_output_text = ""
        rendered = list(lines)
        if is_error:
            rendered.append("[error] tool reported a failure")
        if duration_seconds is not None:
            rendered.extend(("", f"Took {duration_seconds:.1f}s"))
        self._history_blocks.append(("tool_result", tuple(rendered or [""])))
        self.paint()

    def render_lines(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        pad: bool = True,
    ) -> list[str]:
        return [
            line.text for line in self._frame_lines(width=width, height=height, pad=pad)
        ]

    def _frame_lines(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        pad: bool = True,
    ) -> list[_FrameLine]:
        width, height = self._dimensions(width=width, height=height)
        history_lines = self._history_region_lines(width)
        if self.assistant_text:
            history_lines.extend(
                self._block_frame_lines(
                    "assistant",
                    self.assistant_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.reasoning_text and not self.thinking_hidden:
            history_lines.extend(
                self._block_frame_lines(
                    "reasoning",
                    self.reasoning_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.tool_output_text:
            live_cap = (
                len(self.tool_output_text.splitlines()) + 1
                if self.tools_expanded
                else _TOOL_STREAM_LIVE_LINES
            )
            stream_lines = (self.tool_output_text.splitlines() or [""])[-live_cap:]
            history_lines.extend(
                self._block_frame_lines("tool_result", stream_lines, width=width)
            )
        if self.working_text:
            history_lines.extend(
                self._block_frame_lines("working", (self.working_text,), width=width)
            )
        if (
            self.settings_dialog_open
            or self.model_selector_open
            or self.tree_selector_open
            or self.scoped_models_open
            or self.custom_overlay_open
        ):
            # The overlay replaces the input/menu region; keep as much trailing
            # history as fits above it so render_lines() agrees with the paint()
            # live region.
            if self.custom_overlay_open:
                selector = self._custom_overlay_region_lines(
                    width=width, height=height
                )
            elif self.settings_dialog_open:
                selector = self._settings_dialog_region_lines(
                    width=width, height=height
                )
            elif self.tree_selector_open:
                selector = self._tree_selector_region_lines(
                    width=width, height=height
                )
            elif self.scoped_models_open:
                selector = self._scoped_models_region_lines(
                    width=width, height=height
                )
            else:
                selector = self._model_selector_region_lines(
                    width=width, height=height
                )
            max_history_lines = max(0, height - len(selector))
            if len(history_lines) > max_history_lines:
                history_lines = history_lines[len(history_lines) - max_history_lines :]
            frame = [*history_lines, *selector]
            if pad:
                padded = [
                    _FrameLine(self._pad(line.text, width), line.kind, line.meta)
                    for line in frame[:height]
                ]
                if len(padded) < height:
                    padded.extend(
                        _FrameLine(" " * width, "normal")
                        for _ in range(height - len(padded))
                    )
                return padded
            return [
                _FrameLine(
                    _clip_custom_overlay_text(line.text, width), line.kind, line.meta
                )
                for line in frame[:height]
            ]
        menu_lines = self._popup_menu_frame_lines(
            width=width,
            max_rows=max(1, height - 7),
        )
        # The pending steering/follow-up region sits between history and the
        # input frame, so it must be reserved in the history budget — otherwise
        # pending lines push the input/footer out of the returned frame when
        # history fills the viewport.
        pending_lines = self._pending_region_lines(width)
        status_lines = self._extension_status_lines(width)
        has_tool_panel = any(
            kind in {"tool", "tool_read", "tool_result"}
            for kind, _block_lines in self._history_blocks
        )
        input_lines = self._input_frame_lines(
            width,
            max_rows=max(
                1,
                height
                - len(menu_lines)
                - len(pending_lines)
                - len(status_lines)
                - 4,
            ),
        )
        max_history_lines = max(
            0,
            height
            - len(input_lines)
            - 4
            - len(menu_lines)
            - len(pending_lines)
            - len(status_lines),
        )
        if has_tool_panel:
            max_history_lines = min(
                max_history_lines, _TOOL_PANEL_HISTORY_VIEW_LINES
            )
        min_history_lines = min(_DEFAULT_HISTORY_VIEW_LINES, max_history_lines)
        history_overflowed = len(history_lines) > max_history_lines
        if len(history_lines) > max_history_lines:
            history_lines = self._tail_history_lines(
                history_lines,
                self._overflow_history_capacity(
                    height,
                    max_history_lines,
                    has_tool_panel=has_tool_panel,
                ),
            )
        if not history_overflowed and len(history_lines) < min_history_lines:
            history_lines.extend(
                _FrameLine("", "normal")
                for _ in range(min_history_lines - len(history_lines))
            )

        top_separator = self._input_frame_separator(width, label=False)
        bottom_separator = self._input_frame_separator(width, label=True)
        # ``pending_lines`` was computed above (reserved in the history budget).
        if menu_lines:
            frame = [
                *history_lines,
                *pending_lines,
                top_separator,
                *input_lines,
                bottom_separator,
                *menu_lines,
                *status_lines,
                _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
                _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
            ]
        else:
            frame = [
                *history_lines,
                *pending_lines,
                top_separator,
                *input_lines,
                bottom_separator,
                *status_lines,
                _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
                _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
            ]
        if pad:
            padded = [
                _FrameLine(self._pad(line.text, width), line.kind, line.meta)
                for line in frame[:height]
            ]
            if len(padded) < height:
                padded.extend(
                    _FrameLine(" " * width, "normal")
                    for _ in range(height - len(padded))
                )
            return padded
        return [
            _FrameLine(
                _clip_custom_overlay_text(line.text, width), line.kind, line.meta
            )
            for line in frame[:height]
        ]

    def paint(self) -> None:
        if self._closed:
            return
        with self._paint_lock:
            self._paint_locked()

    def _paint_locked(self) -> None:
        width, height = self._dimensions()
        style = chrome_style_for(self.terminal_stream)
        output: list[str] = ["\x1b[?25l"]
        # 1. Return to the top of the previously drawn live region and erase it
        #    (and anything below). Committed history above is left untouched so
        #    it stays in the terminal's native scrollback.
        if self._live_height > 0:
            if self._live_input_row > 0:
                output.append(f"\x1b[{self._live_input_row}A")
            output.append("\r\x1b[J")
        else:
            output.append("\r")
        # 2. Commit any newly finalized history blocks into the normal buffer.
        #    Raw-mode input disables LF-to-CRLF translation, so use explicit
        #    carriage returns to keep each row starting in column 1.
        for kind, block_lines in self._history_blocks[self._painted_block_count :]:
            for frame_line in self._block_frame_lines(kind, block_lines, width=width):
                output.append(self._styled_line(frame_line, style=style, width=width))
                output.append("\x1b[K\r\n")
        self._painted_block_count = len(self._history_blocks)
        # 3. Draw the live region (bounded transient stream + input/footer).
        live = self._live_region_lines(width=width, height=height)
        last_index = len(live) - 1
        for index, frame_line in enumerate(live):
            output.append(self._styled_line(frame_line, style=style, width=width))
            output.append("\x1b[K")
            if index != last_index:
                output.append("\r\n")
        # 4. Park the visible cursor on the input cell with relative moves; an
        #    absolute row would be wrong once the buffer has scrolled.
        input_index = next(
            (
                index
                for index, frame_line in enumerate(live)
                if frame_line.kind == "input"
                and isinstance((frame_line.meta or {}).get("cursor_col"), int)
            ),
            next(
                (
                    index
                    for index, frame_line in enumerate(live)
                    if frame_line.kind == "input"
                ),
                last_index,
            ),
        )
        lines_up = last_index - input_index
        if lines_up > 0:
            output.append(f"\x1b[{lines_up}A")
        output.append("\r")
        # The selector/settings overlays have no editable input cell, so keep
        # the terminal cursor hidden (it was hidden at the top of the paint)
        # instead of parking and revealing it on a non-input row.
        if not (
            self.model_selector_open
            or self.settings_dialog_open
            or self.tree_selector_open
            or self.scoped_models_open
            or self.session_picker_open
            or self.custom_overlay_open
        ):
            # Park on the cursor's wrapped input row/column, so the hardware
            # cursor and the drawn cursor cell agree for over-wide input.
            input_meta = live[input_index].meta or {}
            raw_cursor_col = input_meta.get("cursor_col")
            cursor_col = raw_cursor_col if isinstance(raw_cursor_col, int) else 0
            cursor_col = min(max(0, width - 1), cursor_col)
            if cursor_col > 0:
                output.append(f"\x1b[{cursor_col}C")
            output.append("\x1b[?25h")
        self._live_height = len(live)
        self._live_input_row = input_index
        self._last_painted_size = (width, height)
        try:
            self.terminal_stream.write("".join(output))
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

    def _live_region_lines(self, *, width: int, height: int) -> list[_FrameLine]:
        """Compose the pinned bottom region drawn below committed history.

        Layout (top to bottom): the in-progress streaming tail (assistant,
        reasoning, working spinner), a separator, the input line, a separator,
        an optional slash-command menu, and the two footer rows. The transient
        tail is bounded so the live region never exceeds the screen height; the
        full streamed answer commits to scrollback once it settles.

        While the interactive provider/model selector is open it replaces the
        transient/input/menu rows with the selector overlay (and keeps the two
        footer rows pinned at the bottom).
        """

        if self.custom_overlay_open:
            return self._custom_overlay_region_lines(width=width, height=height)
        if self.settings_dialog_open:
            return self._settings_dialog_region_lines(width=width, height=height)
        if self.session_picker_open:
            return self._session_picker_region_lines(width=width, height=height)
        if self.tree_selector_open:
            return self._tree_selector_region_lines(width=width, height=height)
        if self.scoped_models_open:
            return self._scoped_models_region_lines(width=width, height=height)
        if self.model_selector_open:
            return self._model_selector_region_lines(width=width, height=height)
        menu_lines = self._popup_menu_frame_lines(
            width=width,
            max_rows=max(1, height - 7),
        )
        pending_lines = self._pending_region_lines(width)
        status_lines = self._extension_status_lines(width)
        input_lines = self._input_frame_lines(
            width,
            max_rows=max(
                1,
                height
                - len(menu_lines)
                - len(pending_lines)
                - len(status_lines)
                - 4,
            ),
        )
        # Chrome below the transient tail: pending region + two separators +
        # wrapped input rows + menu rows + extension status + two footer rows.
        chrome_height = (
            len(input_lines)
            + 2
            + len(menu_lines)
            + len(pending_lines)
            + len(status_lines)
            + 2
        )
        transient_budget = max(0, height - chrome_height - 1)
        transient = self._transient_tail_lines(width)
        if len(transient) > transient_budget:
            transient = transient[len(transient) - transient_budget :]
        lines: list[_FrameLine] = [
            *transient,
            *pending_lines,
            self._input_frame_separator(width, label=False),
            *input_lines,
            self._input_frame_separator(width, label=True),
            *menu_lines,
            *status_lines,
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        return lines

    def _model_selector_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        """Compose the interactive provider/model selector overlay.

        Layout (top to bottom): a title/affordance row, a windowed list of
        provider/model rows (the highlighted row carries a ``→`` marker, an
        optional scroll indicator when the list overflows), and the two footer
        rows. Unselectable rows are dimmed; the highlighted row is accented.
        """

        options = self.model_selector_options
        footer = [
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        title = _FrameLine(
            self._clip(
                " Select provider/model — ↑/↓ move · enter select · esc cancel",
                width,
            ),
            "selector_title",
        )
        # Reserve the title, the two footer rows, and one row for the optional
        # scroll indicator so the visible window always fits the live region.
        max_rows = max(1, height - 4)
        total = len(options)
        visible_count = min(total, max_rows)
        start = max(
            0,
            min(
                self.model_selector_selection - (visible_count // 2),
                max(0, total - visible_count),
            ),
        )
        visible = options[start : start + visible_count]
        rows: list[_FrameLine] = []
        for offset, option in enumerate(visible, start=start):
            selected = offset == self.model_selector_selection
            prefix = "→ " if selected else "  "
            if selected:
                kind = "selector_option_selected"
            elif option.selectable:
                kind = "selector_option"
            else:
                kind = "selector_option_disabled"
            rows.append(
                _FrameLine(self._clip(f"{prefix}{option.label}", width), kind)
            )
        lines = [title, *rows]
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(
                        f"  ({self.model_selector_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        lines.extend(footer)
        return lines

    def _settings_dialog_region_lines(
        self, *, width: int, height: int
    ) -> list[_FrameLine]:
        """Compose the interactive ``/settings`` dialog overlay.

        Layout (top to bottom): a title/affordance row, a windowed list of
        rows (section headers as labels, read-only status rows dimmed, and
        actionable rows with a ``→`` marker on the highlighted one), an optional
        scroll indicator when the list overflows, and the two footer rows. The
        window is centered on the highlighted row so navigation/scroll stays
        coherent at any height, mirroring the provider/model selector overlay.
        """

        rows = self.settings_dialog_rows
        footer = [
            _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
            _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
        ]
        title = _FrameLine(
            self._clip(
                " Settings — ↑/↓ move · enter/space act · esc close",
                width,
            ),
            "selector_title",
        )
        # Reserve the title, the two footer rows, and one row for the optional
        # scroll indicator so the visible window always fits the live region.
        max_rows = max(1, height - 4)
        total = len(rows)
        visible_count = min(total, max_rows)
        start = max(
            0,
            min(
                self.settings_dialog_selection - (visible_count // 2),
                max(0, total - visible_count),
            ),
        )
        visible = rows[start : start + visible_count]
        rendered_rows: list[_FrameLine] = []
        for offset, row in enumerate(visible, start=start):
            selected = offset == self.settings_dialog_selection
            if row.kind == "header":
                rendered_rows.append(
                    _FrameLine(self._clip(f"  {row.label}", width), "selector_title")
                )
                continue
            prefix = "→ " if selected else "  "
            if selected:
                kind = "selector_option_selected"
            elif row.action is not None:
                kind = "selector_option"
            else:
                kind = "selector_option_disabled"
            rendered_rows.append(
                _FrameLine(self._clip(f"{prefix}{row.label}", width), kind)
            )
        lines = [title, *rendered_rows]
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(
                        f"  ({self.settings_dialog_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        lines.extend(footer)
        return lines

    # Max queued-message rows shown in the pending region. Bounded so a large
    # queue cannot grow the pinned chrome and push the input/footer out of the
    # live region; overflow is summarized in a single "+N more" row.
    _PENDING_REGION_MAX_ROWS = 6

    def _pending_region_lines(self, width: int) -> list[_FrameLine]:
        """Render the queued steering/follow-up messages (Pi pending area).

        Capped at :data:`_PENDING_REGION_MAX_ROWS` message rows so an unbounded
        queue cannot exceed the live region and push the input/footer out; the
        remainder is collapsed into a ``… +N more queued`` row.
        """

        if not self.has_pending_messages():
            return []
        labeled = [("Steering", t) for t in self._pending_steering]
        labeled += [("Follow-up", t) for t in self._pending_follow_up]
        cap = self._PENDING_REGION_MAX_ROWS
        visible = labeled[:cap]
        lines: list[_FrameLine] = []
        for kind, text in visible:
            label = text.replace("\n", " ")
            lines.append(_FrameLine(self._clip(f"  {kind}: {label}", width), "notice"))
        hidden = len(labeled) - len(visible)
        if hidden > 0:
            lines.append(
                _FrameLine(
                    self._clip(f"  … +{hidden} more queued", width),
                    "slash_menu_scroll",
                )
            )
        lines.append(
            _FrameLine(
                self._clip("  (alt+up to restore queued messages to the editor)", width),
                "slash_menu_scroll",
            )
        )
        return lines

    def _extension_status_lines(self, width: int) -> list[_FrameLine]:
        """Render bounded extension status rows above the footer."""

        if not self.extension_status:
            return []
        with self._paint_lock:
            items = tuple(sorted(self.extension_status.items()))
        rows: list[_FrameLine] = []
        for key, raw_value in items[:3]:
            value = sanitize_label_text(raw_value)
            rows.append(
                _FrameLine(self._clip(f"  {key}: {value}", width), "notice")
            )
        hidden = len(items) - len(rows)
        if hidden > 0:
            rows.append(
                _FrameLine(
                    self._clip(f"  ... +{hidden} extension status rows", width),
                    "slash_menu_scroll",
                )
            )
        return rows

    def _transient_tail_lines(self, width: int) -> list[_FrameLine]:
        lines: list[_FrameLine] = []
        if self.assistant_text:
            lines.extend(
                self._block_frame_lines(
                    "assistant",
                    self.assistant_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.reasoning_text and not self.thinking_hidden:
            lines.extend(
                self._block_frame_lines(
                    "reasoning",
                    self.reasoning_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.tool_output_text:
            # Ctrl+O expands the live tool-output tail from the bounded preview
            # to the full retained stream (still capped by the live char bound).
            live_cap = (
                len(self.tool_output_text.splitlines()) + 1
                if self.tools_expanded
                else _TOOL_STREAM_LIVE_LINES
            )
            stream_lines = (self.tool_output_text.splitlines() or [""])[-live_cap:]
            lines.extend(
                self._block_frame_lines("tool_result", stream_lines, width=width)
            )
        if self.working_text:
            lines.extend(
                self._block_frame_lines("working", (self.working_text,), width=width)
            )
        return lines

    def _styled_line(self, line: _FrameLine, *, style: Any, width: int) -> str:
        raw_text = line.text
        text = raw_text.rstrip()
        if line.kind == "title":
            if not style.enabled:
                return text
            if text.startswith(" pipy v"):
                return f" {style.title('pipy')}{style.dim(text[len(' pipy'):])}"
            return style.title(text)
        if line.kind in {"dim", "resource", "footer"}:
            return style.dim(text)
        if line.kind == "controls":
            return style.dim(text)
        if line.kind == "section":
            return style.section_label(text)
        if line.kind == "separator":
            return style.separator(text)
        if line.kind == "bash_separator":
            return style.error(text)
        if line.kind == "working":
            return style.secondary_dim(text)
        if line.kind == "reasoning":
            return style.dim_italic(text)
        if line.kind == "error":
            return style.error(text)
        if line.kind == "tool":
            return style.tool_command(text, width=width)
        if line.kind == "tool_read":
            return style.tool_read(text, width=width)
        if line.kind == "tool_result":
            return style.tool_result(text, width=width)
        if line.kind == "selector_title":
            return style.section_label(text)
        if line.kind == "selector_option_selected":
            return style.menu_selection(text)
        if line.kind == "selector_option":
            return text
        if line.kind == "selector_option_disabled":
            return style.secondary_dim(text)
        if line.kind == "slash_menu_selected":
            return style.menu_selection(text)
        if line.kind == "slash_menu":
            description_start = (
                line.meta.get("description_start") if line.meta is not None else None
            )
            if not isinstance(description_start, int) or description_start >= len(text):
                return style.menu_row(text)
            if style.enabled:
                return (
                    "\x1b[39m"
                    + text[:description_start]
                    + style.secondary_dim(text[description_start:])
                )
            return (
                text[:description_start]
                + style.secondary_dim(text[description_start:])
            )
        if line.kind == "slash_menu_scroll":
            return style.secondary_dim(text)
        if line.kind == "input":
            cursor_col = (line.meta or {}).get("cursor_col")
            if not isinstance(cursor_col, int):
                return raw_text
            col = min(max(0, cursor_col), max(0, width - 1))
            before = raw_text[:col]
            cursor_char = raw_text[col] if col < len(raw_text) else " "
            after = raw_text[col + 1 :] if col < len(raw_text) else ""
            return style.cursor_cell(before, cursor_char, after)
        if line.kind == "user":
            return style.user_message(text, width=width)
        return text

    def _restore_terminal_mode(self) -> None:
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

    def _enter_raw_mode(self) -> None:
        if self._old_termios is not None:
            return
        fd = self.input_stream.fileno()
        self._old_termios = termios.tcgetattr(fd)
        tty.setraw(fd)
        self._set_bracketed_paste(True)

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

    def _install_resize_handler(self) -> None:
        """Best-effort SIGWINCH handler that flags a pending resize.

        Resize *handling* is poll-based (see :meth:`_poll_resize_repaint`) so
        it works regardless of which thread runs the loop; installing a signal
        handler only makes idle repaints snappier. ``signal.signal`` raises
        ``ValueError`` when called off the main thread (e.g. the threaded test
        harness), which is caught and ignored — polling still covers it.
        """

        try:
            self._prev_winch_handler = signal.signal(
                signal.SIGWINCH, self._on_resize_signal
            )
        except (ValueError, OSError, AttributeError):
            self._prev_winch_handler = None

    def _remove_resize_handler(self) -> None:
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

    def _startup_blocks(self) -> list[tuple[str, tuple[str, ...]]]:
        blocks: list[tuple[str, tuple[str, ...]]] = [
            ("normal", ("",)),
            ("title", (f" pipy v{pipy_version_label()}",)),
            (
                "controls",
                (
                    " escape interrupt · ctrl+c/ctrl+d clear/exit · ↑↓ history · "
                    "/ commands · @ files · ! bash · tab paths",
                    " shift+tab thinking · ctrl+p model · ctrl+o tool output · "
                    "ctrl+t thinking fold · ctrl+v paste image · drop files to attach",
                ),
            ),
            ("dim", (" Type /hotkeys for the full key reference and loaded resources.",)),
            ("normal", ("",)),
            (
                "dim",
                (
                    " Pipy can explain its own features and look up its docs. "
                    "Ask it how to use or extend pipy.",
                ),
            ),
            ("normal", ("", "")),
        ]
        context = discover_loaded_resource_names(self.cwd, "context")
        if context:
            blocks.append(
                (
                    "section",
                    (
                        "[Context]",
                    ),
                )
            )
            blocks.append(
                (
                    "resource",
                    (
                        f"  {', '.join(context)}",
                        "",
                    ),
                )
            )
        skills = discover_loaded_resource_names(self.cwd, "skills")
        if skills:
            blocks.append(
                (
                    "section",
                    (
                        "[Skills]",
                    ),
                )
            )
            blocks.append(
                (
                    "resource",
                    (
                        f"  {', '.join(skills)}",
                        "",
                        "",
                    ),
                )
            )
        return blocks

    def _history_region_lines(self, width: int) -> list[_FrameLine]:
        lines: list[_FrameLine] = []
        for kind, block_lines in self._history_blocks:
            lines.extend(self._block_frame_lines(kind, block_lines, width=width))
        return lines

    def _block_frame_lines(
        self,
        kind: str,
        block_lines: Iterable[str],
        *,
        width: int | None = None,
    ) -> list[_FrameLine]:
        width = width or self._dimensions()[0]
        prefix = {
            "user": " ",
            "assistant": " ",
            "reasoning": " ",
            "working": " ",
            "error": " ",
            "tool": " $ ",
            "tool_read": " ",
            "tool_result": " ",
            "settings": " ",
            "notice": "pipy  ",
            "section": "",
            "title": "",
            "controls": "",
            "dim": "",
            "resource": "",
            "normal": "",
        }.get(kind, "")
        rendered: list[_FrameLine] = []
        if kind == "user":
            rendered.append(_FrameLine("", "user"))
        elif kind in {"tool", "tool_read"}:
            rendered.append(_FrameLine("", "tool_result"))
        elif kind in {"reasoning", "notice", "settings"}:
            rendered.append(_FrameLine(""))
        for line in block_lines:
            available = max(10, width - len(prefix))
            wrapped = textwrap.wrap(line, width=available) or [""]
            for wrapped_line in wrapped:
                rendered.append(
                    _FrameLine(
                        self._clip(f"{prefix}{wrapped_line}", width),
                        self._line_kind_for_block(kind),
                    )
                )
        if kind == "user":
            rendered.extend((_FrameLine("", "user"), _FrameLine("")))
        elif kind == "tool":
            rendered.append(_FrameLine("", "tool_result"))
        elif kind == "tool_read":
            rendered.extend((_FrameLine("", "tool_result"), _FrameLine("")))
        elif kind in {"assistant", "tool_result", "notice", "working", "settings"}:
            rendered.append(_FrameLine(""))
            if kind == "tool_result":
                rendered.append(_FrameLine(""))
        elif kind == "error":
            rendered.append(_FrameLine(""))
        return rendered

    @staticmethod
    def _line_kind_for_block(kind: str) -> str:
        return {
            "user": "user",
            "section": "section",
            "title": "title",
            "controls": "controls",
            "dim": "dim",
            "resource": "resource",
            "normal": "normal",
            "working": "working",
            "error": "error",
            "reasoning": "reasoning",
            "tool": "tool",
            "tool_read": "tool_read",
            "tool_result": "tool_result",
            "settings": "settings",
        }.get(kind, "normal")

    @staticmethod
    def _tail_history_lines(
        lines: list[_FrameLine], max_history_lines: int
    ) -> list[_FrameLine]:
        if max_history_lines <= 0:
            return []
        last_user_index = next(
            (
                index
                for index in range(len(lines) - 1, -1, -1)
                if lines[index].kind == "user"
            ),
            None,
        )
        if last_user_index is None:
            return lines[-max_history_lines:]
        user_start = last_user_index
        while user_start > 0 and lines[user_start - 1].kind == "user":
            user_start -= 1
        user_end = last_user_index + 1
        while user_end < len(lines) and lines[user_end].kind == "user":
            user_end += 1
        user_block = lines[user_start:user_end]
        if len(user_block) >= max_history_lines:
            return user_block[-max_history_lines:]
        before_user = lines[:user_start]
        after_user = lines[user_end:]
        available = max_history_lines - len(user_block)
        min_context = min(
            len(before_user), _OVERFLOW_CONTEXT_MIN_LINES, max(0, available)
        )
        after_capacity = max(0, available - min_context)
        after_tail = ToolLoopTerminalUi._history_tail(after_user, after_capacity)
        context_capacity = max_history_lines - len(user_block) - len(after_tail)
        context_target = min(
            len(before_user),
            _OVERFLOW_CONTEXT_TARGET_LINES,
            max(0, context_capacity),
        )
        context_before_user = before_user[-context_target:] if context_target else []
        remaining = max_history_lines - len(context_before_user) - len(user_block)
        if len(after_tail) > remaining:
            after_tail = after_tail[-remaining:] if remaining > 0 else []
        return [*context_before_user, *user_block, *after_tail]

    @staticmethod
    def _history_tail(lines: list[_FrameLine], capacity: int) -> list[_FrameLine]:
        if capacity <= 0:
            return []
        if len(lines) <= capacity:
            return lines
        compacted = [
            line
            for line in lines
            if line.text.strip() or line.kind in {"tool_result", "user"}
        ]
        if len(compacted) >= capacity:
            return compacted[-capacity:]
        return lines[-capacity:]

    @staticmethod
    def _overflow_history_capacity(
        height: int, max_history_lines: int, *, has_tool_panel: bool
    ) -> int:
        default_view_lines = (
            max_history_lines if has_tool_panel else _DEFAULT_HISTORY_VIEW_LINES
        )
        if has_tool_panel:
            return min(max_history_lines, default_view_lines)
        return min(
            max_history_lines,
            default_view_lines,
            max(0, height - 5 - _OVERFLOW_BOTTOM_GUTTER_LINES),
        )

    def _dimensions(
        self, *, width: int | None = None, height: int | None = None
    ) -> tuple[int, int]:
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
                size = os.get_terminal_size(fileno())
            except (OSError, ValueError):
                size = None
            if size is not None and size.columns > 0 and size.lines > 0:
                return size.columns, size.lines
        size = shutil.get_terminal_size(_DEFAULT_SIZE)
        return size.columns, size.lines

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

    @staticmethod
    def _display_input_text(text: str) -> str:
        """Project the literal input buffer onto a single display row.

        Each character maps to exactly one display character so the logical
        cursor index lines up with the displayed column: a newline becomes the
        single-width ``⏎`` glyph and any other control character becomes a
        space. The underlying ``input_text`` is left untouched, so Enter still
        submits the exact (possibly multi-line) prompt.
        """

        if not any(ch == "\n" or ord(ch) < 0x20 or ch == "\x7f" for ch in text):
            return text
        rendered: list[str] = []
        for ch in text:
            if ch == "\n":
                rendered.append(_INPUT_NEWLINE_GLYPH)
            elif ord(ch) < 0x20 or ch == "\x7f":
                rendered.append(" ")
            else:
                rendered.append(ch)
        return "".join(rendered)

    def _input_frame_lines(
        self, width: int, *, max_rows: int | None = None
    ) -> list[_FrameLine]:
        """Return soft-wrapped input rows with cursor metadata on one row.

        The literal buffer is first projected to display-safe single-cell
        characters, so pasted newlines remain visible as ``⏎`` while the
        submitted prompt keeps its exact text. Rows are hard-wrapped at
        ``width - 1`` cells to leave the same trailing safety column the old
        single-row renderer used. When the input is taller than the available
        live region, the visible window follows the cursor so the footer remains
        pinned.
        """

        rows, cursor_row, cursor_col = self._wrapped_input_rows(width)
        if max_rows is not None and max_rows > 0 and len(rows) > max_rows:
            start = min(
                max(0, cursor_row - max_rows + 1),
                max(0, len(rows) - max_rows),
            )
            rows = rows[start : start + max_rows]
            cursor_row -= start
        rendered: list[_FrameLine] = []
        for index, row in enumerate(rows):
            meta = {"cursor_col": cursor_col} if index == cursor_row else None
            rendered.append(_FrameLine(self._clip(row or " ", width), "input", meta))
        return rendered or [_FrameLine("", "input", {"cursor_col": 0})]

    def _wrapped_input_rows(self, width: int) -> tuple[list[str], int, int]:
        display = self._display_input_text(self.input_text)
        cursor = self._effective_input_cursor()
        capacity = max(1, width - 1)
        rows = [
            display[start : start + capacity]
            for start in range(0, len(display), capacity)
        ] or [""]
        cursor_row = cursor // capacity
        cursor_col = cursor % capacity
        if cursor_row >= len(rows):
            rows.append("")
        return rows, cursor_row, cursor_col

    @staticmethod
    def _clip(text: str, width: int) -> str:
        text = sanitize_label_text(str(text))
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "…"

    @staticmethod
    def _pad(text: str, width: int) -> str:
        visible_len = _visible_len_allow_sgr(text)
        if visible_len >= width:
            return _clip_custom_overlay_text(text, width)
        return text + (" " * (width - visible_len))

    def _read_key(self, fd: int) -> str | None:
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

    def _read_escape_sequence(self, fd: int) -> str:
        """Decode an escape sequence after the leading ESC has been read.

        Handles bare ``Esc``, the CSI arrow/home/end keys, and a CSI
        bracketed-paste introducer (``ESC[200~``). Parameterized CSI
        sequences are read up to their final byte (``0x40``–``0x7e``) so a
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
            # Stop at any CSI final byte in 0x40–0x7e. This covers the legacy
            # finals (``A``–``F``, ``Z``=0x5a), the bracketed-paste ``~`` (0x7e),
            # AND the kitty keyboard-protocol ``u`` (0x75) — so CSI-u sequences
            # like ``112;6u`` (Shift+Ctrl+P) are read whole and reach the
            # matchers below, not timed out as a bare Esc.
            if "\x40" <= byte <= "\x7e":
                break
        if sequence == _BRACKETED_PASTE_START:
            self._pending_paste = self._read_bracketed_paste(fd)
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

    def _read_key_polling_resize(self, fd: int) -> str | None:
        """Block for the next key, repainting when the terminal is resized.

        Returns the decoded key, or ``None`` on EOF. While waiting it polls the
        live terminal size every ``_RESIZE_POLL_SECONDS`` and repaints the frame
        if it changed (or a SIGWINCH flagged a pending resize), so the inline
        layout stays coherent without entering the alternate screen.
        """

        while True:
            self._poll_resize_repaint()
            if self._pending_input_bytes:
                return self._read_key(fd)
            readable, _, _ = select.select([fd], [], [], _RESIZE_POLL_SECONDS)
            if fd not in readable:
                continue
            return self._read_key(fd)

    def _poll_resize_repaint(self) -> bool:
        pending = self._resize_pending
        self._resize_pending = False
        if pending or self._dimensions() != self._last_painted_size:
            self._repaint_after_resize()
            return True
        return False

    def _repaint_after_resize(self) -> None:
        """Repaint after a terminal resize without relying on stale geometry.

        A width change can reflow the previously drawn frame (e.g. a
        full-width separator wraps when the terminal shrinks), so the cached
        physical live-height/input-row no longer describe the screen and the
        normal relative-cursor erase would leave stale rows. Instead, clear the
        visible screen, home the cursor, and redraw the full frame
        (committed history + live region) fresh at the new size. This is
        drift-independent and stays inline — it never enters the alternate
        screen, and committed history stays in native scrollback above
        (re-rendered at the new width). Resizes are infrequent, so the redraw
        cost is acceptable for the coherence guarantee.
        """

        with self._paint_lock:
            if self._closed:
                return
            try:
                # Clear the visible screen and home the cursor (no \x1b[3J, so
                # the terminal's scrollback is preserved). Then force a full
                # redraw by resetting the committed-block and live-region
                # bookkeeping so _paint_locked re-emits every history block.
                self.terminal_stream.write("\x1b[2J\x1b[H")
            except (OSError, ValueError):
                return
            self._painted_block_count = 0
            self._live_height = 0
            self._live_input_row = 0
            self._paint_locked()

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

    def _read_key_if_available(self, fd: int, timeout: float) -> str | None:
        if self._pending_input_bytes:
            return self._read_key(fd)
        readable, _, _ = select.select([fd], [], [], timeout)
        if fd not in readable:
            return None
        return self._read_key(fd)

    def _insert_input_text(self, text: str) -> None:
        self._snapshot_for_undo()
        self._reset_history_nav()
        cursor = self._effective_input_cursor()
        self.input_text = self.input_text[:cursor] + text + self.input_text[cursor:]
        self.input_cursor = cursor + len(text)
        self._refresh_slash_menu_state()

    def _insert_paste(self, text: str) -> None:
        """Insert pasted text literally as a single undo-able edit.

        Newlines are preserved in the buffer (so a multi-line paste is held
        verbatim) but never interpreted as Enter, so a paste cannot submit a
        command on its own. The slash menu only opens for a leading ``/`` with
        no whitespace, so pasted multi-token or multi-line text leaves it
        closed.
        """

        if not text:
            return
        # Terminal drag-drop arrives as a bracketed paste; a single existing
        # file path is treated as an attachment reference (Pi "drop files to
        # attach") — an image path becomes ``@image:``, any other existing path
        # becomes ``@path`` — so submit resolves it through the usual loaders.
        reference = self._as_drag_reference(text)
        if reference is not None:
            text = reference
        self._snapshot_for_undo()
        self._reset_history_nav()
        cursor = self._effective_input_cursor()
        self.input_text = self.input_text[:cursor] + text + self.input_text[cursor:]
        self.input_cursor = cursor + len(text)
        self._refresh_slash_menu_state()

    def _as_drag_reference(self, text: str) -> str | None:
        """Return an ``@image:``/``@path`` reference for a dropped file path.

        Returns ``None`` for ordinary pasted text (multi-line, or not an
        existing single file path), which is then inserted literally. Relative
        drops are resolved against the session workspace (``self.cwd``), not the
        process cwd, so a file dropped from the workspace resolves even when the
        two differ.
        """

        candidate = text.strip()
        if not candidate or "\n" in candidate:
            return None
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
            candidate = candidate[1:-1]
        if not candidate or "\x00" in candidate:
            return None
        try:
            resolved = Path(candidate).expanduser()
            if not resolved.is_absolute():
                resolved = self.cwd / resolved
            if not resolved.is_file():
                return None
        except OSError:
            return None
        # Re-quote a path containing a space so the reference resolves as a
        # single token (the @path/@image: resolvers accept @"…"); an unquoted
        # spaced path would otherwise break at the space.
        rendered = f'"{candidate}"' if " " in candidate else candidate
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        if Path(candidate).suffix.lower() in image_suffixes:
            return f"@image:{rendered} "
        return f"@{rendered} "

    def _paste_clipboard_image(self) -> None:
        """Insert an ``@image:`` reference for the OS clipboard image (Ctrl+V).

        Reads the clipboard image through the injected reader, writes it to an
        owner-only temp file under the session clipboard dir (registered as an
        image reference root), and inserts an ``@image:<path>`` reference so the
        existing attachment resolver loads it on submit. Reports a local notice
        when no image / no tool is available; no image bytes reach the archive.
        """

        if self.clipboard_image_read is None or self.clipboard_temp_dir is None:
            self.add_notice("pipy: clipboard image paste is not available here.")
            return
        result = self.clipboard_image_read()
        if not result.found:
            self.add_notice(f"pipy: {result.detail}.")
            return
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }.get(result.media_type, "png")
        try:
            self.clipboard_temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.clipboard_temp_dir.chmod(0o700)
            except OSError:
                pass
            self._clipboard_image_count += 1
            path = (
                self.clipboard_temp_dir
                / f"pipy-clipboard-{self._clipboard_image_count}.{extension}"
            )
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(result.data)
        except OSError:
            self.add_notice("pipy: could not save the pasted clipboard image.")
            return
        # Quote the reference when the temp path contains a space (e.g. a TMPDIR
        # with spaces) so the @image: resolver loads it as a single token.
        reference = f'"{path}"' if " " in str(path) else str(path)
        self._insert_input_text(f"@image:{reference} ")

    def _delete_before_cursor(self) -> None:
        cursor = self._effective_input_cursor()
        if cursor <= 0:
            return
        self._snapshot_for_undo()
        self._reset_history_nav()
        self.input_text = self.input_text[: cursor - 1] + self.input_text[cursor:]
        self.input_cursor = cursor - 1
        self._refresh_slash_menu_state()

    def _kill_to_line_start(self) -> None:
        cursor = self._effective_input_cursor()
        if cursor <= 0:
            return
        self._snapshot_for_undo()
        self._reset_history_nav()
        self.input_text = self.input_text[cursor:]
        self.input_cursor = 0
        self._refresh_slash_menu_state()

    def _reset_line_editor_state(self) -> None:
        """Clear per-line undo/redo and history-recall state for a fresh line."""

        self._undo_stack.clear()
        self._redo_stack.clear()
        self._history_nav_index = None
        self._history_draft = ""

    def _reset_history_nav(self) -> None:
        self._history_nav_index = None
        self._history_draft = ""

    def _snapshot_for_undo(self) -> None:
        self._undo_stack.append((self.input_text, self._effective_input_cursor()))
        if len(self._undo_stack) > _MAX_UNDO_DEPTH:
            del self._undo_stack[0]
        self._redo_stack.clear()

    def _undo_edit(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append((self.input_text, self._effective_input_cursor()))
        text, cursor = self._undo_stack.pop()
        self.input_text = text
        self.input_cursor = cursor
        self._reset_history_nav()
        self._refresh_slash_menu_state()

    def _redo_edit(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append((self.input_text, self._effective_input_cursor()))
        text, cursor = self._redo_stack.pop()
        self.input_text = text
        self.input_cursor = cursor
        self._reset_history_nav()
        self._refresh_slash_menu_state()

    def _record_history(self, submitted: str) -> None:
        entry = submitted.strip()
        if not entry:
            return
        if self.input_history and self.input_history[-1] == submitted:
            return
        self.input_history.append(submitted)
        if len(self.input_history) > _MAX_HISTORY_DEPTH:
            del self.input_history[0]

    def _navigate_history(self, key: str) -> None:
        if not self.input_history:
            return
        if key == "up":
            if self._history_nav_index is None:
                self._history_draft = self.input_text
                self._history_nav_index = len(self.input_history) - 1
            else:
                self._history_nav_index = max(0, self._history_nav_index - 1)
            self._load_history_entry(self.input_history[self._history_nav_index])
        else:  # down
            if self._history_nav_index is None:
                return
            self._history_nav_index += 1
            if self._history_nav_index >= len(self.input_history):
                self._history_nav_index = None
                self._load_history_entry(self._history_draft)
                self._history_draft = ""
            else:
                self._load_history_entry(self.input_history[self._history_nav_index])

    def _load_history_entry(self, text: str) -> None:
        # Recall replaces the buffer wholesale and parks the cursor at the end.
        # The slash menu stays closed during recall so an arrow press reviews
        # history instead of jumping into command completion.
        self.input_text = text
        self.input_cursor = len(text)
        self.slash_menu_open = False
        self.slash_menu_selection = 0
        self.paint()

    def suspend_for_external_io(self) -> None:
        """Tear down the live region for a blocking interactive flow.

        Used by ``/login`` so the OAuth manager can print its URL/prompt and
        read a line directly from the terminal in cooked mode. The committed
        history above is left in native scrollback; the old input/footer rows
        are erased and the live-region tracking is reset so the next
        :meth:`paint` redraws a fresh frame below whatever the external flow
        printed. No prompts, URLs, or credentials touch the session archive —
        they only render on the live terminal.
        """

        with self._paint_lock:
            self._restore_terminal_mode()
            output: list[str] = []
            if self._live_height > 0:
                if self._live_input_row > 0:
                    output.append(f"\x1b[{self._live_input_row}A")
                output.append("\r\x1b[J")
            output.append("\x1b[?25h")
            try:
                self.terminal_stream.write("".join(output))
                self.terminal_stream.flush()
            except (OSError, ValueError):
                pass
            self._live_height = 0
            self._live_input_row = 0

    def _move_input_cursor(self, key: str) -> None:
        cursor = self._effective_input_cursor()
        if key == "left":
            self.input_cursor = max(0, cursor - 1)
        elif key == "right":
            self.input_cursor = min(len(self.input_text), cursor + 1)
        elif key == "home":
            self.input_cursor = 0
        elif key == "end":
            self.input_cursor = len(self.input_text)
        # The @/path completion popup is anchored to the caret offset where it
        # opened (``autocomplete_token_start``). A caret move would leave that
        # anchor stale, so a subsequent Enter/Tab accept could splice the
        # candidate at the old offset against the new caret and duplicate or
        # corrupt the active token. Dismiss it on any move; it reopens on the
        # next edit (which re-derives the anchor from the current caret).
        self._close_autocomplete()

    def _effective_input_cursor(self) -> int:
        if self.input_cursor is None:
            return len(self.input_text)
        return min(len(self.input_text), max(0, self.input_cursor))

    def _refresh_slash_menu_state(self) -> None:
        before_cursor = self.input_text[: self._effective_input_cursor()]
        if before_cursor.startswith("/") and not any(
            char.isspace() for char in before_cursor
        ):
            self.slash_menu_open = True
            matches = self._filtered_commands()
            if not matches:
                self.slash_menu_open = False
                self.slash_menu_selection = 0
            elif self.slash_menu_selection >= len(matches):
                self.slash_menu_selection = 0
        else:
            self.slash_menu_open = False
            self.slash_menu_selection = 0
        self._refresh_autocomplete_state()

    def _refresh_autocomplete_state(self) -> None:
        """Open/refresh the ``@`` file picker as the editor content changes.

        The slash menu keeps priority for a leading ``/``; while it is open the
        autocomplete popup stays closed so the two never co-open. Otherwise an
        ``@``-prefixed token at the cursor opens a scored, workspace-bounded
        file picker (Pi's content trigger). Tab path completion is forced (not
        auto), so it is not opened here.
        """

        if self.slash_menu_open:
            self._close_autocomplete()
            return
        before_cursor = self.input_text[: self._effective_input_cursor()]
        token = extract_at_token(before_cursor)
        if token is None:
            self._close_autocomplete()
            return
        start, query = token
        items = at_candidates(self.cwd, query)
        if not items:
            self._close_autocomplete()
            return
        self.autocomplete_open = True
        self.autocomplete_mode = "at"
        self.autocomplete_items = tuple(items)
        self.autocomplete_token_start = start
        if self.autocomplete_selection >= len(items):
            self.autocomplete_selection = 0

    def _close_autocomplete(self) -> None:
        self.autocomplete_open = False
        self.autocomplete_items = ()
        self.autocomplete_selection = 0

    def enqueue_steering(self, text: str) -> None:
        if text.strip():
            self._pending_steering.append(text)

    def enqueue_follow_up(self, text: str) -> None:
        if text.strip():
            self._pending_follow_up.append(text)

    def has_pending_messages(self) -> bool:
        return bool(self._pending_steering or self._pending_follow_up)

    def promote_pending_to_drain(self) -> None:
        """Move queued messages into the sequential drain (steering first).

        Called once a turn stops with queued messages so the session delivers
        them in order — all steering, then all follow-up — as the next prompts.
        """

        self._pending_drain.extend(self._pending_steering)
        self._pending_drain.extend(self._pending_follow_up)
        self._pending_steering.clear()
        self._pending_follow_up.clear()

    def restore_pending_to_editor(self) -> None:
        """Restore queued messages into the editor joined by blank lines (Alt+Up
        / Escape-abort), then clear the lanes.

        Routed through ``_pending_initial_text`` as well as ``input_text``: an
        Escape-abort returns control to the outer loop, whose next ``read_line``
        resets ``input_text`` unless ``_pending_initial_text`` is set — so
        without this the restored messages would be wiped before the user saw
        them.

        Includes ``_pending_drain``: once a turn settles (or steering promotes)
        the lanes are emptied into the drain, so on an Escape-abort the
        not-yet-delivered drain entries must come back too — otherwise they stay
        hidden and keep auto-submitting to the provider after the cancellation.
        They lead (they are next to deliver) ahead of any steering/follow-up
        enqueued after promotion.
        """

        queued = [
            *self._pending_drain,
            *self._pending_steering,
            *self._pending_follow_up,
        ]
        self._pending_drain.clear()
        self._pending_steering.clear()
        self._pending_follow_up.clear()
        if not queued:
            return
        joined = "\n\n".join(queued)
        existing = (
            self._pending_initial_text
            if self._pending_initial_text is not None
            else self.input_text
        )
        combined = f"{joined}\n\n{existing}" if existing else joined
        # Reflect immediately and survive the next read_line reset.
        self._pending_initial_text = combined
        self.input_text = combined
        self.input_cursor = len(combined)
        self._refresh_slash_menu_state()

    def take_next_drain(self) -> str | None:
        """Pop the next queued message to deliver as a prompt, or None."""

        if not self._pending_drain:
            return None
        return self._pending_drain.pop(0)

    @staticmethod
    def _submitted_text_is_local_command(text: str) -> bool:
        """True when a mid-turn submission is a local command, not a prompt.

        Matches the session loop's local-command boundary: any line whose first
        non-space character is ``/`` (a slash command — known ones dispatch,
        unknown ones are reported, neither reaches the provider) or ``!`` (a
        bash shortcut). Such a line submitted with Enter mid-turn runs locally
        instead of being queued/steered to the model. Ordinary prose (which is
        what steering/follow-up actually carries) does not match.
        """

        stripped = text.strip()
        return stripped.startswith("/") or stripped.startswith("!")

    def take_pending_command(self) -> str | None:
        """Pop a local command submitted mid-turn (Enter), or None.

        The session loop reads this before the drain/read_line and dispatches it
        through the normal local-command path, so it is never sent to the
        provider (unlike a drained steering/follow-up message).
        """

        command = self._pending_command
        self._pending_command = None
        return command

    def _is_bash_mode(self) -> bool:
        """True when the editor buffer is a ``!``/``!!`` local-shell shortcut.

        Mirrors Pi's ``isBashMode`` editor border: while the first non-space
        character of the input is ``!`` the input frame paints a distinct
        bash-mode affordance (Enter runs a shell command, not a provider turn).
        """

        return self.input_text.lstrip().startswith("!")

    def _input_frame_separator(self, width: int, *, label: bool) -> _FrameLine:
        """Return an input-frame separator, bash-styled while in bash mode."""

        if not self._is_bash_mode():
            return _FrameLine("─" * width, "separator")
        text = "─" * width
        if label:
            tag = " ! bash "
            if width > len(tag) + 2:
                text = "─" + tag + "─" * (width - len(tag) - 1)
        return _FrameLine(text, "bash_separator")

    def _navigate_autocomplete(self, key: str) -> None:
        if not self.autocomplete_open or not self.autocomplete_items:
            return
        delta = -1 if key == "up" else 1
        self.autocomplete_selection = (
            self.autocomplete_selection + delta
        ) % len(self.autocomplete_items)
        self.paint()

    def _accept_autocomplete_selection(self) -> None:
        """Replace the active ``@``/path token with the highlighted candidate.

        Accepting an ``@`` candidate leaves a literal ``@path`` in the buffer so
        the existing ``file_references`` resolver loads its bounded excerpt on
        submit. Accepting a directory in path mode re-opens the popup for the
        next segment, mirroring Pi's progressive Tab completion.
        """

        if not self.autocomplete_open or not self.autocomplete_items:
            return
        item = self.autocomplete_items[self.autocomplete_selection]
        cursor = self._effective_input_cursor()
        start = self.autocomplete_token_start
        # Defensive: the replacement span is ``[start, cursor)``. If the anchor
        # is stale relative to the caret (start past the caret, or negative),
        # splicing would duplicate/corrupt the buffer — close instead. Caret
        # moves already dismiss the popup, so this only fires on an unexpected
        # stale state.
        if not 0 <= start <= cursor <= len(self.input_text):
            self._close_autocomplete()
            self.paint()
            return
        self._snapshot_for_undo()
        self._reset_history_nav()
        self.input_text = self.input_text[:start] + item.value + self.input_text[cursor:]
        self.input_cursor = start + len(item.value)
        self._close_autocomplete()
        if self.autocomplete_mode == "path" and item.value.rstrip('"').endswith("/"):
            # Directory accepted: re-open the popup for the next segment.
            self._attempt_path_completion()
        self.paint()

    def _attempt_path_completion(self) -> bool:
        """Forced Tab path completion against the prefix before the cursor.

        Returns ``True`` when the prefix produced candidates (and the editor was
        updated/opened), ``False`` for a no-op. Uses the forced-Tab prefix so
        bare workspace prefixes (``README``, ``scr``) complete, not just
        path-like ones; Tab stays a no-op in prose because the empty-token case
        (e.g. after a trailing space) is skipped and a non-path word that
        matches no workspace entry yields no candidates. Completes the longest
        unambiguous prefix and opens the popup when more than one remains.
        """

        before_cursor = self.input_text[: self._effective_input_cursor()]
        extracted = extract_path_prefix(before_cursor, force=True)
        if extracted is None:
            return False
        start, prefix = extracted
        # An empty token (empty buffer or trailing space) is a no-op rather than
        # a whole-working-directory dump.
        if prefix == "":
            return False
        items = path_candidates(self.cwd, prefix)
        if not items:
            return False
        cursor = self._effective_input_cursor()
        common = self._longest_common_value(items)
        if common and len(common) > len(prefix):
            self._snapshot_for_undo()
            self._reset_history_nav()
            self.input_text = (
                self.input_text[:start] + common + self.input_text[cursor:]
            )
            self.input_cursor = start + len(common)
            cursor = self.input_cursor
        if len(items) == 1:
            single = items[0].value
            self._snapshot_for_undo()
            self._reset_history_nav()
            self.input_text = (
                self.input_text[:start] + single + self.input_text[cursor:]
            )
            self.input_cursor = start + len(single)
            self._close_autocomplete()
            return True
        self.autocomplete_open = True
        self.autocomplete_mode = "path"
        self.autocomplete_items = tuple(items)
        self.autocomplete_token_start = start
        self.autocomplete_selection = 0
        return True

    @staticmethod
    def _longest_common_value(items: Sequence[CompletionItem]) -> str:
        values = [item.value for item in items]
        if not values:
            return ""
        shortest = min(values, key=len)
        for index, char in enumerate(shortest):
            if any(value[index] != char for value in values):
                return shortest[:index]
        return shortest

    def _filtered_commands(self) -> tuple[str, ...]:
        if not self.slash_menu_open:
            return ()
        prefix = self.input_text[: self._effective_input_cursor()]
        return tuple(command for command in self.command_names if command.startswith(prefix))

    def _accept_slash_menu_selection(self) -> None:
        matches = self._filtered_commands()
        if not matches:
            return
        selected = matches[self.slash_menu_selection]
        self.input_text = selected
        self.input_cursor = len(selected)
        self.slash_menu_open = False
        self.slash_menu_selection = 0
        self.paint()

    def _navigate_slash_menu(self, key: str) -> None:
        matches = self._filtered_commands()
        if not self.slash_menu_open or not matches:
            return
        delta = -1 if key == "up" else 1
        self.slash_menu_selection = (self.slash_menu_selection + delta) % len(matches)
        self.paint()

    def _popup_menu_frame_lines(self, *, width: int, max_rows: int) -> list[_FrameLine]:
        """Return the active in-frame completion popup (slash menu or editor).

        The slash menu keeps priority when it is open; otherwise the editor
        autocomplete popup (``@`` file picker or Tab path completion) draws in
        the same rows. The two never co-open, mirroring Pi.
        """

        if self.slash_menu_open:
            return self._slash_menu_frame_lines(width=width, max_rows=max_rows)
        if self.autocomplete_open:
            return self._autocomplete_frame_lines(width=width, max_rows=max_rows)
        return []

    def _autocomplete_frame_lines(
        self, *, width: int, max_rows: int
    ) -> list[_FrameLine]:
        items = self.autocomplete_items
        if not self.autocomplete_open or not items or max_rows <= 0:
            return []
        menu_cap = self.autocomplete_max_visible if self.autocomplete_max_visible > 0 else 5
        visible_count = min(len(items), max_rows, menu_cap)
        start = max(
            0,
            min(
                self.autocomplete_selection - (visible_count // 2),
                max(0, len(items) - visible_count),
            ),
        )
        visible = items[start : start + visible_count]
        total = len(items)
        lines: list[_FrameLine] = []
        for offset, item in enumerate(visible, start=start):
            prefix = "→ " if offset == self.autocomplete_selection else "  "
            label = item.label
            description_start = len(prefix) + len(label)
            line = f"{prefix}{label}"
            # Show the full inserted value (dimmed) when it differs from the
            # short label and the row has room, so a scoped/quoted path is
            # legible before acceptance.
            if item.value not in {label, f"@{label}"} and width > 40:
                spacing = " " * max(1, 24 - len(line))
                remaining = width - len(line) - len(spacing) - 2
                if remaining > 6:
                    line = f"{line}{spacing}{item.value[:remaining]}"
                    description_start = len(prefix) + len(label) + len(spacing)
            lines.append(
                _FrameLine(
                    self._clip(line, width),
                    "slash_menu_selected"
                    if offset == self.autocomplete_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(f"  ({self.autocomplete_selection + 1}/{total})", width),
                    "slash_menu_scroll",
                )
            )
        return lines

    def _slash_menu_frame_lines(self, *, width: int, max_rows: int) -> list[_FrameLine]:
        matches = self._filtered_commands()
        if not self.slash_menu_open or not matches or max_rows <= 0:
            return []
        menu_cap = self.autocomplete_max_visible if self.autocomplete_max_visible > 0 else 5
        visible_count = min(len(matches), max_rows, menu_cap)
        start = max(
            0,
            min(
                self.slash_menu_selection - (visible_count // 2),
                max(0, len(matches) - visible_count),
            ),
        )
        visible = matches[start : start + visible_count]
        lines: list[_FrameLine] = []
        total = len(matches)
        primary_width = self._slash_menu_primary_column_width(matches)
        for offset, command in enumerate(visible, start=start):
            description = self.command_descriptions.get(command, "")
            display_command = command[1:] if command.startswith("/") else command
            prefix = "→ " if offset == self.slash_menu_selection else "  "
            max_primary_width = max(1, primary_width - 2)
            display_command = display_command[:max_primary_width]
            spacing = " " * max(1, primary_width - len(display_command))
            description_start = len(prefix) + len(display_command)
            line = f"{prefix}{display_command}{spacing}"
            if description and width > 40:
                remaining = width - len(line) - 2
                if remaining > 10:
                    line = f"{line}{description[:remaining]}"
            lines.append(
                _FrameLine(
                    self._clip(line, width),
                    "slash_menu_selected"
                    if offset == self.slash_menu_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(f"  ({self.slash_menu_selection + 1}/{total})", width),
                    "slash_menu_scroll",
                )
            )
        return lines

    @staticmethod
    def _slash_menu_primary_column_width(matches: tuple[str, ...]) -> int:
        widest = 0
        for command in matches:
            display_command = command[1:] if command.startswith("/") else command
            widest = max(widest, len(display_command) + 2)
        return max(12, min(32, widest))


def _compact_read_header(header: str) -> str:
    return re.sub(r":\d+-\d+(?:\s+\(ctrl\+o to expand\))?$", "", header)


def run_startup_session_picker(
    *,
    project_sessions: Sequence[SessionListEntry],
    all_sessions: Sequence[SessionListEntry],
    current_cwd: str,
) -> Path | None:
    """Open the ``-r`` startup session picker on a real TTY.

    Constructs a standalone inline picker bound to ``sys.stdin``/``sys.stdout``
    and returns the chosen native session file (or ``None`` when there is no TTY
    or the user cancels). Rename/delete actions run through the same native
    boundaries as the in-session ``/resume`` picker; no provider turn runs.
    """

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (ValueError, OSError):
        return None

    from pipy_harness.native.session_tree import NativeSessionTree
    from pipy_harness.native.session_tree_commands import delete_native_session

    def on_rename(path: Path, new_name: str) -> None:
        NativeSessionTree.open(path).append_session_info(new_name)

    def on_delete(path: Path) -> tuple[bool, str]:
        return delete_native_session(path)

    ui = ToolLoopTerminalUi(
        input_stream=sys.stdin,
        terminal_stream=sys.stdout,
        cwd=Path(current_cwd),
    )
    try:
        return ui.run_session_picker(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=None,
            on_rename=on_rename,
            on_delete=on_delete,
        )
    finally:
        ui.close()
