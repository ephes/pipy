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
import sys
import termios
import textwrap
import threading
import tty
from collections.abc import Iterable
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
_OVERFLOW_BOTTOM_GUTTER_LINES = 2
_OVERFLOW_CONTEXT_TARGET_LINES = 13
_OVERFLOW_CONTEXT_MIN_LINES = 4
TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS = (
    "/help",
    "/settings",
    "/copy",
    "/exit",
    "/quit",
)


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
    command_names: tuple[str, ...] = TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
    command_descriptions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )
    slash_menu_open: bool = False
    slash_menu_selection: int = 0
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
        self.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        if footer is not None:
            self.set_footer_text(footer)
        self.input_text = ""
        self.input_cursor = 0
        self.slash_menu_open = False
        self.slash_menu_selection = 0
        self.paint()
        fd = self.input_stream.fileno()
        try:
            self._enter_raw_mode()
            while True:
                key = self._read_key(fd)
                if key is None:
                    return ""
                if key == "enter":
                    if self.slash_menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self.input_text not in matches:
                            self._accept_slash_menu_selection()
                    submitted = self.input_text
                    self.input_text = ""
                    self.input_cursor = 0
                    self.slash_menu_open = False
                    self.paint()
                    return f"{submitted}\n"
                if key == "ctrl-c":
                    raise KeyboardInterrupt
                if key == "ctrl-d":
                    if not self.input_text:
                        return ""
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
                    self._navigate_slash_menu(key)
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
                    cursor = self._effective_input_cursor()
                    self.input_text = self.input_text[cursor:]
                    self.input_cursor = 0
                    self._refresh_slash_menu_state()
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
                key = self._read_key_if_available(fd, poll_seconds)
                if key == "esc":
                    abort_event.set()
                    return True
                if key == "ctrl-c":
                    abort_event.set()
                    raise KeyboardInterrupt
            return False
        finally:
            self._restore_terminal_mode()

    def close(self) -> None:
        self._restore_terminal_mode()
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
        if header.startswith("read ") or header.startswith("read resource "):
            self._history_blocks.append(("tool_read", (_compact_read_header(header),)))
        else:
            self._history_blocks.append(("tool", (header,)))
        self.paint()

    def add_tool_result(
        self,
        *,
        lines: Iterable[str],
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        self._settle_reasoning()
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
        if self.working_text:
            history_lines.extend(
                self._block_frame_lines("working", (self.working_text,), width=width)
            )
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
        input_line = self._clip(self.input_text + " ", width)
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
        cursor_col = min(max(0, width - 1), self._effective_input_cursor())
        if cursor_col > 0:
            output.append(f"\x1b[{cursor_col}C")
        output.append("\x1b[?25h")
        self._live_height = len(live)
        self._live_input_row = input_index
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
        """

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
        input_line = self._clip(self.input_text + " ", width)
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
            cursor = self._effective_input_cursor()
            before = self.input_text[:cursor]
            cursor_char = (
                self.input_text[cursor] if cursor < len(self.input_text) else " "
            )
            after = self.input_text[cursor + 1 :] if cursor < len(self.input_text) else ""
            return style.cursor_cell(before, cursor_char, after)
        if line.kind == "user":
            return style.user_message(text, width=width)
        return text

    def _restore_terminal_mode(self) -> None:
        if self._old_termios is None:
            return
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

    def _startup_blocks(self) -> list[tuple[str, tuple[str, ...]]]:
        blocks: list[tuple[str, tuple[str, ...]]] = [
            ("normal", ("",)),
            ("title", (f" pipy v{pipy_version_label()}",)),
            (
                "controls",
                (
                    " escape interrupt · ctrl+c/ctrl+d clear/exit · / commands · "
                    "! bash · ctrl+o more",
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
        if bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            size = shutil.get_terminal_size(_DEFAULT_SIZE)
            return max(_MIN_WIDTH, size.columns), max(_MIN_HEIGHT, size.lines)
        return (
            max(_MIN_WIDTH, width or _DEFAULT_SIZE[0]),
            max(_MIN_HEIGHT, height or _DEFAULT_SIZE[1]),
        )

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
            next1 = self._read_byte_with_timeout(fd, 0.05)
            if next1 == "":
                return "esc"
            if next1 not in {"["}:
                return "esc"
            next2 = self._read_byte_with_timeout(fd, 0.05)
            return {
                "A": "up",
                "B": "down",
                "C": "right",
                "D": "left",
                "H": "home",
                "F": "end",
            }.get(next2, "esc")
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
        if ch == "\x01":
            return "home"
        if ch == "\x05":
            return "end"
        return ch

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
        cursor = self._effective_input_cursor()
        self.input_text = self.input_text[:cursor] + text + self.input_text[cursor:]
        self.input_cursor = cursor + len(text)
        self._refresh_slash_menu_state()

    def _delete_before_cursor(self) -> None:
        cursor = self._effective_input_cursor()
        if cursor <= 0:
            return
        self.input_text = self.input_text[: cursor - 1] + self.input_text[cursor:]
        self.input_cursor = cursor - 1
        self._refresh_slash_menu_state()

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
        visible_count = min(len(matches), max_rows, 5)
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
