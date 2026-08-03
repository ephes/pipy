"""Modal overlays backing the extension `ctx.ui.select`/`confirm`/`input` API.

Three components with the same contract: construct with a `done` callback,
`render(width)` to lines, and feed keys to `handle_input`. None of them touches
the terminal, reads session state, or knows the shell exists -- `done` is the
only way a result leaves. That is what makes them testable with a plain list of
keystrokes, and what keeps a misbehaving extension prompt from reaching
anything but its own overlay.

Every rendered line is clipped and label-sanitized: the title, options, and
message all originate in extension code, so an unclipped line could otherwise
tear the frame or smuggle escape sequences into the terminal.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence

from pipy_harness.native.session_tree_commands import sanitize_label_text


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
