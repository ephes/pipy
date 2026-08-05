"""Extension prompt overlays and their configured external-editor effect.

The three overlay components share one contract: construct with a ``done``
callback, render lines, and accept decoded keys. None touches terminal or
session state. The external-editor owner likewise receives only dependency-
neutral terminal capabilities: a cooked-I/O suspension factory, a write
callable, and the inherited process streams.

Every rendered line is clipped and label-sanitized: the title, options, and
message all originate in extension code, so an unclipped line could otherwise
tear the frame or smuggle escape sequences into the terminal.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import termios
import textwrap
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO

from pipy_harness.native.session_tree_commands import sanitize_label_text


class ExtensionExternalEditor:
    """Run the configured editor while a caller-owned terminal is suspended."""

    def __init__(
        self,
        *,
        external_io_suspension: Callable[[], AbstractContextManager[None]],
        terminal_write: Callable[[str], object],
        input_stream: TextIO,
        terminal_stream: TextIO,
    ) -> None:
        self._external_io_suspension = external_io_suspension
        self._terminal_write = terminal_write
        self._input_stream = input_stream
        self._terminal_stream = terminal_stream

    @staticmethod
    def command() -> str | None:
        """Return the configured command, preferring ``VISUAL`` over ``EDITOR``."""

        return os.environ.get("VISUAL") or os.environ.get("EDITOR")

    def callback(self) -> Callable[[str], str | None] | None:
        """Return an editor callback only when a command is configured."""

        if not self.command():
            return None
        return self.run_configured

    def run_configured(self, current_text: str) -> str | None:
        """Run the current environment-selected editor, if any."""

        editor_cmd = self.command()
        if not editor_cmd:
            return None
        return self.run(editor_cmd, current_text)

    def run(self, editor_cmd: str, current_text: str) -> str | None:
        """Round-trip ``current_text`` through one external editor process."""

        argv = _external_editor_argv(editor_cmd)
        if argv is None:
            return None

        path = ""
        try:
            fd, path = tempfile.mkstemp(
                prefix="pipy-extension-editor-", suffix=".md", text=True
            )
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(current_text)

            updated = self._launch(argv, path, editor_cmd)
            return None if updated is None else updated.removesuffix("\n")
        except OSError:
            return None
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _launch(self, argv: list[str], path: str, editor_cmd: str) -> str | None:
        updated: str | None = None
        launched = False
        try:
            with self._external_io_suspension():
                self._terminal_write(
                    f"Launching external editor: {editor_cmd}\n"
                    "Pipy will resume when the editor exits.\n"
                )
                launched = True
                completed = subprocess.run(
                    [*argv, path],
                    stdin=self._input_stream,
                    stdout=self._terminal_stream,
                    stderr=self._terminal_stream,
                    check=False,
                )
                if completed.returncode == 0:
                    updated = _read_external_editor_file(path)
        except (OSError, termios.error, ValueError):
            # A failed cooked-mode handoff occurs before ``launched`` and must
            # not start a foreign terminal consumer. If the editor did run,
            # retain a successful read even when raw-mode resumption fails.
            if not launched:
                return None
        return updated


def _external_editor_argv(editor_cmd: str) -> list[str] | None:
    try:
        argv = shlex.split(editor_cmd)
    except ValueError:
        return None
    return argv or None


def _read_external_editor_file(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def clip_plain(text: str, width: int) -> str:
    return sanitize_label_text(text)[: max(0, width)]


class ExtensionSelectComponent:
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
            clip_plain(
                f" {sanitize_label_text(self.title)} - up/down move, enter select, esc cancel",
                width,
            )
        ]
        start, end = self._visible_window()
        for index, option in enumerate(self.options[start:end], start=start):
            prefix = "-> " if index == self.selected else "   "
            lines.append(clip_plain(f"{prefix}{sanitize_label_text(option)}", width))
        if start > 0 or end < len(self.options):
            lines.append(
                clip_plain(
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


class ExtensionConfirmComponent(ExtensionSelectComponent):
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
            clip_plain(
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
            lines.append(clip_plain(f"{prefix}{option}", width))
        return lines

    def _message_lines(self, width: int) -> list[str]:
        all_lines: list[str] = []
        body_width = max(20, width - 3)
        raw_lines = str(self.message).splitlines() or [""]
        for raw_line in raw_lines:
            pieces = textwrap.wrap(sanitize_label_text(raw_line), width=body_width) or [
                ""
            ]
            all_lines.extend(f"  {piece}" for piece in pieces)
        truncated = len(all_lines) > self._MAX_MESSAGE_LINES
        wrapped = all_lines[: self._MAX_MESSAGE_LINES]
        if truncated and wrapped:
            wrapped[-1] = clip_plain(wrapped[-1] + " ...", width)
        return [clip_plain(line, width) for line in wrapped]


class ExtensionInputComponent:
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
            clip_plain(
                f" {sanitize_label_text(self.title)} - enter submit, esc cancel",
                width,
            ),
            clip_plain(f"> {shown}", width),
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
