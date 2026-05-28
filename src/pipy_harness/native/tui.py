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
import shutil
import sys
import termios
import textwrap
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


TOOL_LOOP_TUI_RUNTIME_LABEL = "tool-loop-tui"
_MIN_WIDTH = 60
_MIN_HEIGHT = 12
_DEFAULT_SIZE = (88, 24)
_DEFAULT_HISTORY_VIEW_LINES = 21
_OVERFLOW_BOTTOM_GUTTER_LINES = 2
_OVERFLOW_CONTEXT_TARGET_LINES = 11
_OVERFLOW_CONTEXT_MIN_LINES = 4


@dataclass(frozen=True, slots=True)
class _FrameLine:
    text: str
    kind: str = "normal"


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
    working_text: str = ""
    assistant_text: str = ""
    reasoning_text: str = ""
    _history_blocks: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    _old_termios: Any = None
    _entered_alt_screen: bool = False
    _closed: bool = False

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
        """Initialize the shell history and paint the first frame."""

        if not self._history_blocks:
            self._history_blocks.extend(self._startup_blocks())
        self._enter_terminal_screen()
        self.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        if footer is not None:
            self.set_footer_text(footer)
        self.input_text = ""
        self.paint()
        fd = self.input_stream.fileno()
        old = termios.tcgetattr(fd)
        self._old_termios = old
        try:
            tty.setraw(fd)
            while True:
                char = self.input_stream.read(1)
                if char == "":
                    return ""
                if char in {"\r", "\n"}:
                    submitted = self.input_text
                    self.input_text = ""
                    self.paint()
                    return f"{submitted}\n"
                if char == "\x03":
                    raise KeyboardInterrupt
                if char == "\x04":
                    if not self.input_text:
                        return ""
                    continue
                if char in {"\x7f", "\b"}:
                    self.input_text = self.input_text[:-1]
                    self.paint()
                    continue
                if char == "\x1b":
                    # Ignore escape sequences for now.  The TUI shell
                    # owns a simple editor boundary; richer history and
                    # menu navigation can be layered onto this state
                    # model without changing provider/tool behavior.
                    continue
                if char.isprintable():
                    self.input_text += char
                    self.paint()
        finally:
            self._restore_terminal_mode()

    def close(self) -> None:
        self._restore_terminal_mode()
        if self._closed:
            return
        self._closed = True
        try:
            suffix = "\x1b[?1049l" if self._entered_alt_screen else ""
            self.terminal_stream.write(f"\x1b[?25h{suffix}\n")
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

    def add_tool_call(self, header: str) -> None:
        self._settle_reasoning()
        self.working_text = ""
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
        max_history_lines = max(0, height - 5)
        min_history_lines = min(_DEFAULT_HISTORY_VIEW_LINES, max_history_lines)
        history_overflowed = len(history_lines) > max_history_lines
        if len(history_lines) > max_history_lines:
            history_lines = self._tail_history_lines(
                history_lines,
                self._overflow_history_capacity(height, max_history_lines),
            )
        if not history_overflowed and len(history_lines) < min_history_lines:
            history_lines.extend(
                _FrameLine("", "normal")
                for _ in range(min_history_lines - len(history_lines))
            )

        separator = "─" * width
        input_line = self._clip(self.input_text + " ", width)
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
                _FrameLine(self._pad(line.text, width), line.kind)
                for line in frame[:height]
            ]
            if len(padded) < height:
                padded.extend(
                    _FrameLine(" " * width, "normal")
                    for _ in range(height - len(padded))
                )
            return padded
        return [_FrameLine(line.text[:width], line.kind) for line in frame[:height]]

    def paint(self) -> None:
        if self._closed:
            return
        width, height = self._dimensions()
        lines = self._frame_lines(width=width, height=height, pad=False)
        style = chrome_style_for(self.terminal_stream)
        output: list[str] = ["\x1b[?25l\x1b[H"]
        for index, line in enumerate(lines):
            rendered = self._styled_line(line, style=style, width=width)
            output.append(rendered)
            output.append("\x1b[K")
            if index != len(lines) - 1:
                # Raw-mode input disables the terminal's LF-to-CRLF output
                # translation. Use an explicit carriage return so each
                # repainted row starts in column 1 while the editor is active.
                output.append("\r\n")
        input_index = next(
            (index for index, line in enumerate(lines) if line.kind == "input"),
            len(lines) - 1,
        )
        cursor_row = min(height, input_index + 1)
        cursor_col = min(width, len(self.input_text) + 1)
        output.append(f"\x1b[J\x1b[{cursor_row};{cursor_col}H\x1b[?25h")
        try:
            self.terminal_stream.write("".join(output))
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

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
            return style.secondary_dim(text)
        if line.kind == "tool":
            return style.tool_command(text, width=width)
        if line.kind == "tool_result":
            return style.dim(text)
        if line.kind == "input":
            if self.input_text:
                return style.cursor_cell(self.input_text)
            return style.cursor_cell("")
        if line.kind == "user":
            return style.user_message(text, width=width)
        return text

    def _enter_terminal_screen(self) -> None:
        if self._entered_alt_screen:
            return
        self._entered_alt_screen = True
        try:
            self.terminal_stream.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

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
            "tool": " $ ",
            "tool_result": "      ",
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
        elif kind in {"tool", "reasoning", "notice"}:
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
        elif kind in {"assistant", "tool_result", "notice", "working"}:
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
            "reasoning": "reasoning",
            "tool": "tool",
            "tool_result": "tool_result",
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
        compacted = [line for line in lines if line.text.strip()]
        if len(compacted) >= capacity:
            return compacted[-capacity:]
        return lines[-capacity:]

    @staticmethod
    def _overflow_history_capacity(height: int, max_history_lines: int) -> int:
        return min(
            max_history_lines,
            _DEFAULT_HISTORY_VIEW_LINES,
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
