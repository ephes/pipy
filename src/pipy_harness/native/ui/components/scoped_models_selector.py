"""The ``/scoped-models`` multi-select overlay: pool, keys, and rendering.

Same ownership contract as the sibling overlay components: the selector state
already lives on the shared :class:`OverlayState` record (`scoped_rows`,
`scoped_selection`, `scoped_checked`), so the component takes that record, the
shared :class:`PaintLock`, and a repaint callable instead of the terminal-UI
shell. The shell keeps only the raw-mode key loop and forwards each decoded key
to :meth:`ScopedModelsSelectorComponent.handle_key`; a
:class:`ScopedModelsClose` return is the only way a result leaves.

The paint lock guards only overlay-record transitions, so a concurrent painter
never observes a half-applied row or check-set swap. Rendering is a pure
function of the overlay record plus the two footer rows the shell owns, so the
shell's frame dispatch calls :func:`scoped_models_region_lines` without holding
a component instance.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.overlay_state import OverlayState, ScopedModelRow
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class ScopedModelsClose:
    """The overlay finished: ``references`` is the saved scope, ``None`` a cancel."""

    references: frozenset[str] | None


class ScopedModelsSelectorComponent:
    """Checkbox/navigation state machine behind the ``/scoped-models`` overlay."""

    def __init__(
        self,
        overlays: OverlayState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
    ) -> None:
        self._overlays = overlays
        self._paint_lock = paint_lock
        self._repaint = repaint

    def open(self, rows: Sequence[ScopedModelRow], checked: Iterable[int]) -> bool:
        """Activate the overlay over ``rows``; ``False`` on an empty pool."""

        with self._paint_lock:
            opened = self._overlays.begin_scoped(rows, checked)
        if opened:
            self._repaint()
        return opened

    def handle_key(self, key: str | None) -> ScopedModelsClose | None:
        """Apply one decoded key; non-``None`` means the overlay closed.

        ``None``/``esc``/``ctrl-c``/``ctrl-d`` cancel, up/down move the
        highlight (skipping unavailable rows), space toggles membership of the
        highlighted row, ``a`` enables all, ``c`` clears all, and ``enter``
        saves and returns the chosen ``provider/model`` reference set. Every
        other key is ignored and leaves the overlay open.
        """

        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            self._close()
            return ScopedModelsClose(None)
        if key in {"up", "down"}:
            self._navigate(-1 if key == "up" else 1)
            return None
        if key == " ":
            self._toggle()
            return None
        if key == "a":
            with self._paint_lock:
                self._overlays.select_all_scoped()
            self._repaint()
            return None
        if key == "c":
            with self._paint_lock:
                self._overlays.clear_scoped()
            self._repaint()
            return None
        if key == "enter":
            chosen = self._overlays.selected_scoped_references()
            self._close()
            return ScopedModelsClose(chosen)
        return None

    def _navigate(self, delta: int) -> None:
        with self._paint_lock:
            moved = self._overlays.navigate_scoped(delta)
        if moved:
            self._repaint()

    def _toggle(self) -> None:
        with self._paint_lock:
            toggled = self._overlays.toggle_scoped()
        if toggled:
            self._repaint()

    def _close(self) -> None:
        with self._paint_lock:
            self._overlays.end_scoped()
        self._repaint()


def scoped_models_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
) -> list[FrameLine]:
    """Compose the interactive ``/scoped-models`` multi-select overlay."""

    rows_data = overlays.scoped_rows
    footer = [
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
    title = FrameLine(
        clip_text(
            " Scoped models — ↑/↓ move · space toggle · a all · c clear · "
            "enter save · esc cancel",
            width,
        ),
        "selector_title",
    )
    max_rows = max(1, height - 4)
    total = len(rows_data)
    visible_count = min(total, max_rows)
    start = max(
        0,
        min(
            overlays.scoped_selection - (visible_count // 2),
            max(0, total - visible_count),
        ),
    )
    rendered: list[FrameLine] = []
    for offset in range(start, start + visible_count):
        row = rows_data[offset]
        selected = offset == overlays.scoped_selection
        box = "[x]" if offset in overlays.scoped_checked else "[ ]"
        suffix = "" if row.available else "  [unavailable]"
        prefix = "→ " if selected else "  "
        if selected:
            kind = "selector_option_selected"
        elif row.available:
            kind = "selector_option"
        else:
            kind = "selector_option_disabled"
        rendered.append(
            FrameLine(clip_text(f"{prefix}{box} {row.reference}{suffix}", width), kind)
        )
    lines = [title, *rendered]
    if start > 0 or start + visible_count < total:
        lines.append(
            FrameLine(
                clip_text(f"  ({overlays.scoped_selection + 1}/{total})", width),
                "slash_menu_scroll",
            )
        )
    lines.extend(footer)
    return lines
