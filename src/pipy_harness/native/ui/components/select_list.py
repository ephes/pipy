"""Pi's ``SelectList`` (pi-tui ``components/select-list.ts``): rows and keys.

A windowed list of ``SelectItem`` rows: ``→ `` marks the selection, a
primary column holds the label (or value), and a muted description follows
when the width allows. The window is centred on the selection; a muted
``(i/n)`` line appears when not every row is visible. Up/Down wrap; Enter
and Escape are reported to the owner. Mouse input is not ported (pipy's TUI
reads no mouse events). Widths count code points (pipy has no East Asian
width table).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pipy_harness.native.chrome import ChromeStyle

_DEFAULT_PRIMARY_COLUMN_WIDTH = 32
_PRIMARY_COLUMN_GAP = 2
_MIN_DESCRIPTION_WIDTH = 10
_LINE_BREAKS = re.compile(r"[\r\n]+")


@dataclass(frozen=True, slots=True)
class SelectItem:
    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SelectListLayout:
    min_primary_column_width: int | None = None
    max_primary_column_width: int | None = None


def _single_line(text: str) -> str:
    return _LINE_BREAKS.sub(" ", text).strip()


def _truncate(text: str, width: int) -> str:
    """pi-tui ``truncateToWidth(text, width, "")``: cut without an ellipsis."""

    return text[: max(0, width)]


class SelectList:
    """Selection state plus Pi's row rendering for one list of items."""

    def __init__(
        self,
        items: Sequence[SelectItem],
        max_visible: int,
        layout: SelectListLayout | None = None,
    ) -> None:
        self.items: tuple[SelectItem, ...] = tuple(items)
        self.max_visible = max_visible
        self.layout = layout or SelectListLayout()
        self.selected_index = 0

    def set_selected_index(self, index: int) -> None:
        self.selected_index = max(0, min(index, len(self.items) - 1))

    def selected_item(self) -> SelectItem | None:
        if 0 <= self.selected_index < len(self.items):
            return self.items[self.selected_index]
        return None

    def move(self, delta: int) -> None:
        """Pi ``tui.select.up``/``down``: move one row, wrapping at the ends."""

        count = len(self.items)
        if delta < 0:
            self.selected_index = (
                count - 1 if self.selected_index == 0 else self.selected_index - 1
            )
        else:
            self.selected_index = (
                0 if self.selected_index == count - 1 else self.selected_index + 1
            )

    def render(self, width: int, style: ChromeStyle) -> list[str]:
        if not self.items:
            return [style.muted("  No matching commands")]
        primary_width = self._primary_column_width()
        start, end = self._visible_range()
        lines = [
            self._render_item(
                self.items[index],
                index == self.selected_index,
                width,
                primary_width,
                style,
            )
            for index in range(start, end)
        ]
        if start > 0 or end < len(self.items):
            scroll = f"  ({self.selected_index + 1}/{len(self.items)})"
            lines.append(style.muted(_truncate(scroll, width - 2)))
        return lines

    def _visible_range(self) -> tuple[int, int]:
        count = len(self.items)
        start = max(
            0,
            min(self.selected_index - self.max_visible // 2, count - self.max_visible),
        )
        return start, min(start + self.max_visible, count)

    def _render_item(
        self,
        item: SelectItem,
        selected: bool,
        width: int,
        primary_width: int,
        style: ChromeStyle,
    ) -> str:
        prefix = "→ " if selected else "  "
        description = _single_line(item.description) if item.description else ""
        if description and width > 40:
            column = max(1, min(primary_width, width - len(prefix) - 4))
            max_primary = max(1, column - _PRIMARY_COLUMN_GAP)
            value = _truncate(self._display_value(item), max_primary)
            spacing = " " * max(1, column - len(value))
            remaining = width - (len(prefix) + len(value) + len(spacing)) - 2
            if remaining > _MIN_DESCRIPTION_WIDTH:
                shown = _truncate(description, remaining)
                if selected:
                    return style.accent(f"{prefix}{value}{spacing}{shown}")
                return prefix + value + style.muted(spacing + shown)
        value = _truncate(self._display_value(item), width - len(prefix) - 2)
        if selected:
            return style.accent(f"{prefix}{value}")
        return prefix + value

    def _primary_column_width(self) -> int:
        layout = self.layout
        raw_min = (
            layout.min_primary_column_width
            if layout.min_primary_column_width is not None
            else layout.max_primary_column_width
            if layout.max_primary_column_width is not None
            else _DEFAULT_PRIMARY_COLUMN_WIDTH
        )
        raw_max = (
            layout.max_primary_column_width
            if layout.max_primary_column_width is not None
            else layout.min_primary_column_width
            if layout.min_primary_column_width is not None
            else _DEFAULT_PRIMARY_COLUMN_WIDTH
        )
        low = max(1, min(raw_min, raw_max))
        high = max(1, max(raw_min, raw_max))
        widest = max(
            (
                len(self._display_value(item)) + _PRIMARY_COLUMN_GAP
                for item in self.items
            ),
            default=0,
        )
        return max(low, min(widest, high))

    @staticmethod
    def _display_value(item: SelectItem) -> str:
        return item.label or item.value
