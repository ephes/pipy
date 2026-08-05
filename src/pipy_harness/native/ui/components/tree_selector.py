"""The interactive ``/tree`` selector overlay: pool, keys, and rendering.

Same ownership contract as the sibling overlay components: the selector state
already lives on the shared :class:`OverlayState` record (`tree_rows`,
`tree_selection`, `tree_filter`), so the component takes that record, the shared
:class:`PaintLock`, and a repaint callable instead of the terminal-UI shell. The
shell keeps only the raw-mode key loop and forwards each decoded key to
:meth:`TreeSelectorComponent.handle_key`; a :class:`TreeSelectorClose` return is
the only way a result leaves.

`build_rows` and `on_label_toggle` are session-side callbacks (they read and
mutate the session tree). They are invoked *outside* the paint lock -- the lock
guards only overlay-record transitions, so a concurrent painter never observes a
half-applied row swap, while session code never runs under the frame mutex.

Rendering is a pure function of the overlay record plus the two footer rows the
shell owns, so the shell's frame dispatch calls
:func:`tree_selector_region_lines` without holding a component instance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.overlay_state import OverlayState, TreeSelectorRow
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class TreeSelectorClose:
    """The selector finished: ``entry_id`` is the choice, ``None`` a cancel.

    ``filter_mode`` is the filter the overlay closed with; the session-side
    ``/tree`` handler persists it for the next invocation, so it travels with
    the close result instead of being re-read from overlay state afterward.
    """

    entry_id: str | None
    filter_mode: str


class TreeSelectorComponent:
    """Filter/label/navigation state machine behind the ``/tree`` overlay."""

    def __init__(
        self,
        overlays: OverlayState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        build_rows: Callable[[str], Sequence[TreeSelectorRow]],
        filter_modes: Sequence[str],
        on_label_toggle: Callable[[str], None],
    ) -> None:
        self._overlays = overlays
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._build_rows = build_rows
        self._filter_modes = tuple(filter_modes)
        self._on_label_toggle = on_label_toggle

    def open(self, initial_filter: str) -> None:
        """Activate the overlay with rows for ``initial_filter`` and repaint."""

        filter_mode = (
            initial_filter
            if initial_filter in self._filter_modes
            else self._filter_modes[0]
        )
        rows = self._build_rows(filter_mode)
        with self._paint_lock:
            self._overlays.begin_tree(rows, filter_mode=filter_mode)
        self._repaint()

    def handle_key(self, key: str | None) -> TreeSelectorClose | None:
        """Apply one decoded key; non-``None`` means the overlay closed.

        ``None``/``esc``/``ctrl-c``/``ctrl-d`` cancel, up/down move the
        highlight, ``ctrl-o`` cycles the filter mode, ``L`` toggles a label on
        the highlighted entry, and ``enter`` selects it (a no-op while the row
        pool is empty). Every other key is ignored and leaves the overlay open.
        """

        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            return TreeSelectorClose(None, self._close())
        if key in {"up", "down"}:
            self._navigate(-1 if key == "up" else 1)
            return None
        if key == "ctrl-o":
            self._cycle_filter()
            return None
        if key == "L":
            self._toggle_label()
            return None
        if key == "enter":
            return self._select()
        return None

    def _navigate(self, delta: int) -> None:
        with self._paint_lock:
            moved = self._overlays.navigate_tree(delta)
        if moved:
            self._repaint()

    def _cycle_filter(self) -> None:
        position = self._filter_modes.index(self._overlays.tree_filter)
        next_mode = self._filter_modes[(position + 1) % len(self._filter_modes)]
        rows = self._build_rows(next_mode)
        with self._paint_lock:
            self._overlays.tree_filter = next_mode
            self._overlays.replace_tree_rows(rows, reset_to_active=True)
        self._repaint()

    def _toggle_label(self) -> None:
        rows = self._overlays.tree_rows
        selection = self._overlays.tree_selection
        if not 0 <= selection < len(rows):
            return
        self._on_label_toggle(rows[selection].entry_id)
        refreshed = self._build_rows(self._overlays.tree_filter)
        with self._paint_lock:
            self._overlays.replace_tree_rows(refreshed)
        self._repaint()

    def _select(self) -> TreeSelectorClose | None:
        rows = self._overlays.tree_rows
        if not rows:
            return None
        entry_id = rows[self._overlays.tree_selection].entry_id
        return TreeSelectorClose(entry_id, self._close())

    def _close(self) -> str:
        """Deactivate the overlay; return the filter mode it closed with."""

        with self._paint_lock:
            filter_mode = self._overlays.tree_filter
            self._overlays.end_tree()
        self._repaint()
        return filter_mode


def tree_selector_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
) -> list[FrameLine]:
    """Compose the interactive ``/tree`` selector overlay."""

    footer = [
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
    title = FrameLine(
        clip_text(
            " Session tree — ↑/↓ move · enter select · L label · "
            f"^O filter ({overlays.tree_filter}) · esc cancel",
            width,
        ),
        "selector_title",
    )
    rows_data = overlays.tree_rows
    max_rows = max(1, height - 4)
    total = len(rows_data)
    if total == 0:
        return [
            title,
            FrameLine(clip_text("  (empty session tree)", width), "normal"),
            *footer,
        ]
    visible_count = min(total, max_rows)
    start = max(
        0,
        min(
            overlays.tree_selection - (visible_count // 2),
            max(0, total - visible_count),
        ),
    )
    visible = rows_data[start : start + visible_count]
    rows: list[FrameLine] = []
    for offset, row in enumerate(visible, start=start):
        selected = offset == overlays.tree_selection
        prefix = "→ " if selected else "  "
        marker = "*" if row.active else " "
        kind = "selector_option_selected" if selected else "selector_option"
        rows.append(FrameLine(clip_text(f"{prefix}{marker} {row.label}", width), kind))
    lines = [title, *rows]
    if start > 0 or start + visible_count < total:
        lines.append(
            FrameLine(
                clip_text(f"  ({overlays.tree_selection + 1}/{total})", width),
                "slash_menu_scroll",
            )
        )
    lines.extend(footer)
    return lines
