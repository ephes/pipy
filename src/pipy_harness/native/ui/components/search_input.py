"""The single-line search input of Pi's selectors (pi-tui ``Input``).

Pi's model, thinking and session selectors put an ``Input`` above their list:
a ``> `` prompt, the query, and a reverse-video cursor cell, padded to the
width and scrolled horizontally around the cursor when the query does not
fit. This port keeps Pi's rendering and the editing keys pipy's decoder
produces: printable text, paste (line breaks dropped), Backspace, Left/Right,
Home/End (Ctrl+A/Ctrl+E), Ctrl+U (delete to start) and Ctrl+K (delete to
end).

Deviations (backlog DF1-F7b): no undo, yank, word motion or word deletion
(Pi segments words with ``Intl.Segmenter``), no forward Delete (pipy's decoder
has no Delete key), and editing and width count code points rather than
grapheme clusters, like pipy's main editor.
"""

from __future__ import annotations

from pipy_harness.native.chrome import ChromeStyle

_PROMPT = "> "


class SearchInput:
    """Editable single-line query with Pi ``Input`` rendering."""

    def __init__(self, value: str = "") -> None:
        self.value = value
        self.cursor = len(value)

    def set_value(self, value: str) -> None:
        self.value = value
        self.cursor = min(self.cursor, len(value))

    def handle_key(self, key: str) -> bool:
        """Apply one decoded key; ``True`` when the key was an edit key."""

        handler = {
            "backspace": self._backspace,
            "left": self._left,
            "right": self._right,
            "home": self._home,
            "end": self._end,
            "ctrl-u": self._delete_to_start,
            "ctrl-k": self._delete_to_end,
        }.get(key)
        if handler is not None:
            handler()
            return True
        if len(key) == 1 and key.isprintable():
            self.insert(key)
            return True
        return False

    def insert(self, text: str) -> None:
        """Insert ``text`` at the cursor (Pi drops line breaks and tabs)."""

        clean = "".join(ch for ch in text if ch not in "\r\n" and ch.isprintable())
        if not clean:
            return
        self.value = self.value[: self.cursor] + clean + self.value[self.cursor :]
        self.cursor += len(clean)

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
            self.cursor -= 1

    def _left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def _right(self) -> None:
        self.cursor = min(len(self.value), self.cursor + 1)

    def _home(self) -> None:
        self.cursor = 0

    def _end(self) -> None:
        self.cursor = len(self.value)

    def _delete_to_start(self) -> None:
        self.value = self.value[self.cursor :]
        self.cursor = 0

    def _delete_to_end(self) -> None:
        self.value = self.value[: self.cursor]

    def render(self, width: int, style: ChromeStyle) -> str:
        """Pi ``Input.render``: prompt, visible window, reverse-video cursor."""

        available = width - len(_PROMPT)
        if available <= 0:
            return _PROMPT[: max(0, width)]
        value = self.value
        cursor = self.cursor
        if len(value) < available:
            visible = value
            cursor_display = cursor
        else:
            # Reserve one column for the cursor when it sits at the end.
            scroll_width = available - 1 if cursor == len(value) else available
            if scroll_width > 0:
                half = scroll_width // 2
                if cursor < half:
                    start = 0
                elif cursor > len(value) - half:
                    start = max(0, len(value) - scroll_width)
                else:
                    start = max(0, cursor - half)
                visible = value[start : start + scroll_width]
                cursor_display = cursor - start
            else:
                visible = ""
                cursor_display = 0
        before = visible[:cursor_display]
        at_cursor = visible[cursor_display : cursor_display + 1] or " "
        after = visible[cursor_display + 1 :]
        padding = " " * max(0, available - (len(before) + 1 + len(after)))
        if not style.enabled:
            return f"{_PROMPT}{before}{at_cursor}{after}{padding}"
        return f"{_PROMPT}{before}\x1b[7m{at_cursor}\x1b[27m{after}{padding}"
