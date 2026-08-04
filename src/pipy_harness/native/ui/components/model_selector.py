"""The interactive provider/model selector overlay: pool, keys, and rendering.

Same ownership contract as the sibling overlay components: the selector state
already lives on the shared :class:`OverlayState` record (`model_options`,
`model_selection`, `model_title`), so the component takes that record, the
shared :class:`PaintLock`, and a repaint callable instead of the terminal-UI
shell. The shell keeps only the raw-mode key loop and forwards each decoded key
to :meth:`ModelSelectorComponent.handle_key`; a :class:`ModelSelectorClose`
return is the only way a result leaves.

The paint lock guards only overlay-record transitions, so a concurrent painter
never observes a half-applied pool swap. Rendering is a pure function of the
overlay record plus the two footer rows the shell owns, so the shell's frame
dispatch calls :func:`model_selector_region_lines` without holding a component
instance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.overlay_state import ModelSelectorOption, OverlayState
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class ModelSelectorClose:
    """The selector finished: ``index`` is the chosen row, ``None`` a cancel."""

    index: int | None


class ModelSelectorComponent:
    """Navigation/selection state machine behind the provider/model overlay."""

    def __init__(
        self,
        overlays: OverlayState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
    ) -> None:
        self._overlays = overlays
        self._paint_lock = paint_lock
        self._repaint = repaint

    def open(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int,
        title: str | None,
    ) -> bool:
        """Activate the overlay over ``options``; ``False`` on an empty pool."""

        with self._paint_lock:
            opened = self._overlays.begin_model(
                options, current_index=current_index, title=title
            )
        if opened:
            self._repaint()
        return opened

    def handle_key(self, key: str | None) -> ModelSelectorClose | None:
        """Apply one decoded key; non-``None`` means the overlay closed.

        ``None``/``esc``/``ctrl-c``/``ctrl-d`` cancel, up/down move the
        highlight (wrapping), and ``enter`` chooses the highlighted row when it
        is selectable. Every other key is ignored and leaves the overlay open.
        """

        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            self._close()
            return ModelSelectorClose(None)
        if key in {"up", "down"}:
            self._navigate(-1 if key == "up" else 1)
            return None
        if key == "enter":
            return self._select()
        return None

    def _navigate(self, delta: int) -> None:
        with self._paint_lock:
            moved = self._overlays.navigate_model(delta)
        if moved:
            self._repaint()

    def _select(self) -> ModelSelectorClose | None:
        index = self._overlays.model_selection
        if not self._overlays.model_options[index].selectable:
            return None
        self._close()
        return ModelSelectorClose(index)

    def _close(self) -> None:
        with self._paint_lock:
            self._overlays.end_model()
        self._repaint()


def model_selector_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
) -> list[FrameLine]:
    """Compose the interactive provider/model selector overlay.

    Layout (top to bottom): a title/affordance row, a windowed list of
    provider/model rows (the highlighted row carries a ``→`` marker, an
    optional scroll indicator when the list overflows), and the two footer
    rows. Unselectable rows are dimmed; the highlighted row is accented.
    """

    options = overlays.model_options
    footer = [
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
    heading = overlays.model_title or "Select provider/model"
    title = FrameLine(
        clip_text(
            f" {heading} — ↑/↓ move · enter select · esc cancel",
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
            overlays.model_selection - (visible_count // 2),
            max(0, total - visible_count),
        ),
    )
    visible = options[start : start + visible_count]
    rows: list[FrameLine] = []
    for offset, option in enumerate(visible, start=start):
        selected = offset == overlays.model_selection
        prefix = "→ " if selected else "  "
        if selected:
            kind = "selector_option_selected"
        elif option.selectable:
            kind = "selector_option"
        else:
            kind = "selector_option_disabled"
        rows.append(FrameLine(clip_text(f"{prefix}{option.label}", width), kind))
    lines = [title, *rows]
    if start > 0 or start + visible_count < total:
        lines.append(
            FrameLine(
                clip_text(f"  ({overlays.model_selection + 1}/{total})", width),
                "slash_menu_scroll",
            )
        )
    lines.extend(footer)
    return lines
