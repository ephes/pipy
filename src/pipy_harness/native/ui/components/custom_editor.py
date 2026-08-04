"""The multi-line editor overlay backing extension `ctx.ui.editor`.

Same contract as the prompt overlays in `extension_prompts`: construct with a
`done` callback, `render(width)` to lines, feed keys to `handle_input`. It owns
its own buffer, cursor, and scroll window and never reaches the terminal-UI
shell.

The one thing it does that the simpler prompts do not is hand off to an external
editor: `external_editor` is a callable the shell supplies, invoked on a
configured key, returning replacement text or `None` if the user abandoned the
edit. Keeping that a plain callable is what lets this component stay free of the
shell -- suspending the terminal and spawning `$EDITOR` is the caller's problem,
not the overlay's.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.ui.components.extension_prompts import clip_plain
from pipy_harness.native.ui.key_specs import matches_key_specs


class ExtensionEditorComponent:
    """Multi-line editor overlay used by extension `ctx.ui.editor`."""

    _MAX_VISIBLE_LINES = 10

    def __init__(
        self,
        title: str,
        prefill: str | None,
        done: Callable[..., None],
        external_editor: Callable[[str], str | None] | None = None,
        external_editor_keys: Sequence[str] = ("ctrl+g",),
    ) -> None:
        self.title = title
        self.text = "" if prefill is None else str(prefill)
        self.cursor = len(self.text)
        self._done = done
        self._external_editor = external_editor
        self._external_editor_keys = tuple(external_editor_keys)

    def render(self, width: int) -> list[str]:
        hint = "enter submit, shift/alt-enter newline, esc cancel"
        if self._external_editor is not None and self._external_editor_keys:
            key_hint = self._external_editor_keys[0].replace("+", "-")
            hint += f", {key_hint} external edit"
        lines = [
            clip_plain(
                f" {sanitize_label_text(self.title)} - {hint}",
                width,
            )
        ]
        rows, cursor_row, cursor_col = self._rows()
        start = max(
            0,
            min(
                cursor_row - (self._MAX_VISIBLE_LINES // 2),
                max(0, len(rows) - self._MAX_VISIBLE_LINES),
            ),
        )
        end = start + self._MAX_VISIBLE_LINES
        for row_index, row in enumerate(rows[start:end], start=start):
            marker = ">" if row_index == cursor_row else " "
            safe = sanitize_label_text(row)
            if row_index == cursor_row:
                safe = safe[:cursor_col] + "▏" + safe[cursor_col:]
            lines.append(clip_plain(f"{marker} {safe}", width))
        if start > 0 or end < len(rows):
            lines.append(clip_plain(f"  ({cursor_row + 1}/{len(rows)})", width))
        return lines

    def handle_input(self, key: str) -> None:
        if key in {"esc", "ctrl-c"}:
            self._done(None)
            return
        if key == "enter":
            self._done(self.text)
            return
        if key in {"shift-enter", "alt-enter"}:
            self._insert("\n")
            return
        if self._external_editor is not None and matches_key_specs(
            key, self._external_editor_keys
        ):
            self._apply_external_edit(self._external_editor)
            return
        if self._apply_motion_key(key):
            return
        self._apply_editing_key(key)

    def _apply_external_edit(self, editor: Callable[[str], str | None]) -> None:
        edited = editor(self.text)
        if edited is not None:
            self.text = edited
            self.cursor = len(self.text)

    def _apply_motion_key(self, key: str) -> bool:
        """Move the cursor for a motion key; ``False`` if it is not one.

        Motion and editing keys are disjoint, so checking motions first does not
        change which branch any key takes.
        """

        if key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "home":
            self.cursor = self._index_for_row_col(self._cursor_row_col()[0], 0)
        elif key == "end":
            row, _col = self._cursor_row_col()
            self.cursor = self._index_for_row_col(row, len(self._rows()[0][row]))
        elif key in {"up", "down"}:
            row, col = self._cursor_row_col()
            target_row = row - 1 if key == "up" else row + 1
            self.cursor = self._index_for_row_col(target_row, col)
        else:
            return False
        return True

    def _apply_editing_key(self, key: str) -> None:
        if key == "backspace":
            if self.cursor > 0:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
            return
        if len(key) == 1 and key.isprintable():
            self._insert(key)

    def _insert(self, value: str) -> None:
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)

    def _rows(self) -> tuple[list[str], int, int]:
        rows = self.text.split("\n")
        if not rows:
            rows = [""]
        row, col = self._cursor_row_col(rows)
        return rows, row, col

    def _cursor_row_col(self, rows: list[str] | None = None) -> tuple[int, int]:
        if rows is None:
            rows = self.text.split("\n") or [""]
        remaining = max(0, min(self.cursor, len(self.text)))
        for index, row in enumerate(rows):
            if remaining <= len(row):
                return index, remaining
            remaining -= len(row) + 1
        return len(rows) - 1, len(rows[-1])

    def _index_for_row_col(self, target_row: int, target_col: int) -> int:
        rows = self.text.split("\n") or [""]
        row = min(max(0, target_row), len(rows) - 1)
        index = sum(len(item) + 1 for item in rows[:row])
        return min(index + max(0, min(target_col, len(rows[row]))), len(self.text))
