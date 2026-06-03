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
from typing import Any, TextIO

from pipy_harness.native.chrome import (
    chrome_style_for,
    discover_loaded_resource_names,
    pipy_version_label,
)
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
)


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
    "/settings",
    "/login",
    "/logout",
    "/copy",
    "/compact",
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
# Single-width glyph shown in the one-row input cell for a newline carried by a
# multi-line paste. The buffer keeps the literal "\n" (so the exact multi-line
# prompt is submitted on Enter); only the rendered cell substitutes the glyph,
# which keeps the live input row exactly one physical row tall. U+23CE has
# East-Asian-width "Narrow", so it occupies one terminal cell.
_INPUT_NEWLINE_GLYPH = "⏎"


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
    slash_menu_open: bool = False
    slash_menu_selection: int = 0
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
    _paint_lock: Any = field(default_factory=threading.Lock)
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
                    if self.slash_menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self.input_text not in matches:
                            self._accept_slash_menu_selection()
                    submitted = self.input_text
                    self._record_history(submitted)
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self._reset_line_editor_state()
                    self.paint()
                    return f"{submitted}\n"
                if key == "ctrl-c":
                    raise KeyboardInterrupt
                if key == "ctrl-d":
                    if not self.input_text:
                        return ""
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
                if key == "esc":
                    if self.slash_menu_open:
                        self.slash_menu_open = False
                        self.paint()
                    continue
                if key in {"up", "down"}:
                    if self.slash_menu_open:
                        self._navigate_slash_menu(key)
                    else:
                        self._navigate_history(key)
                    continue
                if key == "tab":
                    if self.slash_menu_open and self._filtered_commands():
                        self._accept_slash_menu_selection()
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
        self, done_event: Any, abort_event: Any, *, poll_seconds: float = 0.05
    ) -> bool:
        """Watch raw stdin for Pi-style active-turn Escape cancellation."""

        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while not done_event.is_set():
                # Keep the streaming frame coherent if the terminal is resized
                # mid-turn: streamed chunks repaint at the live size, but a
                # stalled stream would not, so poll here too.
                self._poll_resize_repaint()
                key = self._read_key_if_available(fd, poll_seconds)
                if key == "paste":
                    # A paste mid-turn is not editor input; drop it so its body
                    # does not linger and is never inserted on the next prompt.
                    self._pending_paste = ""
                    continue
                if key == "esc":
                    abort_event.set()
                    return True
                if key == "ctrl-c":
                    abort_event.set()
                    raise KeyboardInterrupt
            return False
        finally:
            self._restore_terminal_mode()

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
        self._history_blocks.append(
            ("reasoning", tuple(self.reasoning_text.splitlines() or [""]))
        )
        self.reasoning_text = ""

    def add_notice(self, text: str) -> None:
        self._settle_reasoning()
        self._history_blocks.append(("notice", tuple(text.splitlines() or [""])))
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
        if self.reasoning_text:
            history_lines.extend(
                self._block_frame_lines(
                    "reasoning",
                    self.reasoning_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.tool_output_text:
            stream_lines = (self.tool_output_text.splitlines() or [""])[
                -_TOOL_STREAM_LIVE_LINES:
            ]
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
        ):
            # The overlay replaces the input/menu region; keep as much trailing
            # history as fits above it so render_lines() agrees with the paint()
            # live region.
            if self.settings_dialog_open:
                selector = self._settings_dialog_region_lines(
                    width=width, height=height
                )
            elif self.tree_selector_open:
                selector = self._tree_selector_region_lines(
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
                _FrameLine(line.text[:width], line.kind, line.meta)
                for line in frame[:height]
            ]
        menu_lines = self._slash_menu_frame_lines(
            width=width,
            max_rows=max(1, height - 7),
        )
        has_tool_panel = any(
            kind in {"tool", "tool_read", "tool_result"}
            for kind, _block_lines in self._history_blocks
        )
        max_history_lines = max(0, height - 5 - len(menu_lines))
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

        separator = "─" * width
        input_line = self._clip(self._input_view(width)[0] + " ", width)
        if menu_lines:
            frame = [
                *history_lines,
                _FrameLine(separator, "separator"),
                _FrameLine(input_line, "input"),
                _FrameLine(separator, "separator"),
                *menu_lines,
                _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
                _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
            ]
        else:
            frame = [
                *history_lines,
                _FrameLine(separator, "separator"),
                _FrameLine(input_line, "input"),
                _FrameLine(separator, "separator"),
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
            _FrameLine(line.text[:width], line.kind, line.meta)
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
            (index for index, frame_line in enumerate(live) if frame_line.kind == "input"),
            last_index,
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
        ):
            # Park on the cursor's column *within the visible (possibly
            # horizontally scrolled) input slice*, so the hardware cursor and
            # the drawn cursor cell agree for over-wide input.
            cursor_col = min(max(0, width - 1), self._input_view(width)[1])
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

        if self.settings_dialog_open:
            return self._settings_dialog_region_lines(width=width, height=height)
        if self.tree_selector_open:
            return self._tree_selector_region_lines(width=width, height=height)
        if self.model_selector_open:
            return self._model_selector_region_lines(width=width, height=height)
        menu_lines = self._slash_menu_frame_lines(
            width=width,
            max_rows=max(1, height - 7),
        )
        # Chrome below the transient tail: two separators + input + menu rows
        # + two footer rows.
        chrome_height = 3 + len(menu_lines) + 2
        transient_budget = max(0, height - chrome_height - 1)
        transient = self._transient_tail_lines(width)
        if len(transient) > transient_budget:
            transient = transient[len(transient) - transient_budget :]
        separator = "─" * width
        input_line = self._clip(self._input_view(width)[0] + " ", width)
        lines: list[_FrameLine] = [
            *transient,
            _FrameLine(separator, "separator"),
            _FrameLine(input_line, "input"),
            _FrameLine(separator, "separator"),
            *menu_lines,
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
        if self.reasoning_text:
            lines.extend(
                self._block_frame_lines(
                    "reasoning",
                    self.reasoning_text.splitlines() or [""],
                    width=width,
                )
            )
        if self.tool_output_text:
            stream_lines = (self.tool_output_text.splitlines() or [""])[
                -_TOOL_STREAM_LIVE_LINES:
            ]
            lines.extend(
                self._block_frame_lines("tool_result", stream_lines, width=width)
            )
        if self.working_text:
            lines.extend(
                self._block_frame_lines("working", (self.working_text,), width=width)
            )
        return lines

    def _styled_line(self, line: _FrameLine, *, style: Any, width: int) -> str:
        text = line.text.rstrip()
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
            # Render from a width-bounded single-line view (see _input_view):
            # embedded newlines show as one glyph and over-wide input is
            # horizontally scrolled to keep the cursor visible, so the input
            # cell is always exactly one physical row and never wraps.
            visible, col = self._input_view(width)
            before = visible[:col]
            cursor_char = visible[col] if col < len(visible) else " "
            after = visible[col + 1 :] if col < len(visible) else ""
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
                    "ctrl+z/ctrl+y undo · / commands · ! bash · ctrl+o more",
                ),
            ),
            ("dim", (" Press ctrl+o to show full startup help and loaded resources.",)),
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

    def _input_view(self, width: int) -> tuple[str, int]:
        """Return the visible input slice and the cursor's column within it.

        The buffer is first projected to a single display row
        (:meth:`_display_input_text`), then horizontally scrolled so the cursor
        stays visible within ``width - 1`` columns (one trailing column is
        reserved so the end-of-text cursor never lands in the terminal's last
        column, which would arm autowrap). The returned column is the cursor's
        position *within the slice*; both the input renderer and the paint
        cursor-parking use this so the drawn text and the hardware cursor agree
        and the input cell is always exactly one physical row.
        """

        display = self._display_input_text(self.input_text)
        cursor = self._effective_input_cursor()
        capacity = max(1, width - 1)
        if len(display) <= capacity:
            return display, cursor
        start = 0
        if cursor > capacity - 1:
            start = cursor - (capacity - 1)
        start = min(start, len(display) - capacity)
        return display[start : start + capacity], cursor - start

    @staticmethod
    def _clip(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "…"

    @staticmethod
    def _pad(text: str, width: int) -> str:
        if len(text) >= width:
            return text[:width]
        return text + (" " * (width - len(text)))

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
        if next1 == "" or next1 != "[":
            return "esc"
        sequence = ""
        while True:
            byte = self._read_byte_with_timeout(fd, 0.05)
            if byte == "":
                break
            sequence += byte
            if "\x40" <= byte <= "\x7e":
                break
        if sequence == _BRACKETED_PASTE_START:
            self._pending_paste = self._read_bracketed_paste(fd)
            return "paste"
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

    @staticmethod
    def _read_byte(fd: int) -> str:
        try:
            data = os.read(fd, 1)
        except (OSError, InterruptedError):
            return ""
        if not data:
            return ""
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _read_byte_with_timeout(fd: int, timeout: float) -> str:
        readable, _, _ = select.select([fd], [], [], timeout)
        if fd not in readable:
            return ""
        return ToolLoopTerminalUi._read_byte(fd)

    def _read_key_if_available(self, fd: int, timeout: float) -> str | None:
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
        self._snapshot_for_undo()
        self._reset_history_nav()
        cursor = self._effective_input_cursor()
        self.input_text = self.input_text[:cursor] + text + self.input_text[cursor:]
        self.input_cursor = cursor + len(text)
        self._refresh_slash_menu_state()

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
