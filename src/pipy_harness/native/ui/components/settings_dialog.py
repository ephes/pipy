"""The interactive ``/settings`` dialog overlay: rows, keys, and rendering.

Same ownership contract as the sibling overlay components: the dialog state
already lives on the shared :class:`OverlayState` record (`settings_rows`,
`settings_selection`, `settings_title`, plus the settings-family overlay
discriminator), so the component takes that record, the shared
:class:`PaintLock`, and a repaint callable instead of the terminal-UI shell.
The shell keeps only the raw-mode key loop and forwards each decoded key to
:meth:`SettingsDialogComponent.handle_key`; a :class:`SettingsDialogClose`
return is the only way a result leaves.

Unlike the pick-one selectors, activating a row can stay *inside* the dialog:
any action not named in ``exit_actions`` runs the caller's ``on_local_action``
callback, which must return the rebuilt rows, and the dialog re-renders in
place. That callback runs outside the paint lock on purpose — it may host a
nested overlay (the ``/settings`` → project-trust nesting) that paints and
drives keys of its own before the outer dialog resumes.

The paint lock guards only overlay-record transitions, so a concurrent painter
never observes a half-applied row swap. Rendering is a pure function of the
overlay record plus the two footer rows the shell owns, so the shell's frame
dispatch calls :func:`settings_dialog_region_lines` without holding a component
instance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.overlay_state import (
    OverlayState,
    SettingsOverlayKind,
    SettingsRow,
)
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class SettingsDialogClose:
    """The dialog finished: ``action`` is the chosen exit action, ``None`` a close."""

    action: str | None


class SettingsDialogComponent:
    """Navigation/activation state machine behind the ``/settings`` dialog."""

    def __init__(
        self,
        overlays: OverlayState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        on_local_action: Callable[[str], Sequence[SettingsRow]],
        exit_actions: frozenset[str] = frozenset(),
    ) -> None:
        self._overlays = overlays
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._on_local_action = on_local_action
        self._exit_actions = exit_actions

    def open(
        self,
        rows: Sequence[SettingsRow],
        *,
        current_index: int | None,
        title: str,
        kind: SettingsOverlayKind,
    ) -> bool:
        """Activate the overlay over ``rows``; ``False`` on an empty pool."""

        with self._paint_lock:
            opened = self._overlays.begin_settings(
                rows, current_index=current_index, title=title, kind=kind
            )
        if opened:
            self._repaint()
        return opened

    def handle_key(self, key: str | None) -> SettingsDialogClose | None:
        """Apply one decoded key; non-``None`` means the dialog closed.

        ``None``/``esc``/``ctrl-c``/``ctrl-d`` close (carrying ``None``),
        up/down move the highlight between actionable rows (wrapping, skipping
        headers and read-only status rows), and ``Enter``/``Space`` activate
        the highlighted action row: an ``exit_actions`` member closes the
        dialog carrying that identifier, anything else runs ``on_local_action``
        and re-renders the rebuilt rows in place. Every other key is ignored
        and leaves the dialog open.
        """

        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            self._close()
            return SettingsDialogClose(None)
        if key in {"up", "down"}:
            self._navigate(-1 if key == "up" else 1)
            return None
        if key in {"enter", " "}:
            return self._activate()
        return None

    def _navigate(self, delta: int) -> None:
        with self._paint_lock:
            moved = self._overlays.navigate_settings(delta)
        if moved:
            self._repaint()

    def _activate(self) -> SettingsDialogClose | None:
        rows = self._overlays.settings_rows
        selection = self._overlays.settings_selection
        if not 0 <= selection < len(rows):
            return None
        action = rows[selection].action
        if action is None:
            return None
        if action in self._exit_actions:
            self._close()
            return SettingsDialogClose(action)
        rebuilt = self._on_local_action(action)
        with self._paint_lock:
            replaced = self._overlays.replace_settings_rows(rebuilt)
        if not replaced:
            self._close()
            return SettingsDialogClose(None)
        self._repaint()
        return None

    def _close(self) -> None:
        with self._paint_lock:
            self._overlays.end_settings()
        self._repaint()


def settings_dialog_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
) -> list[FrameLine]:
    """Compose the interactive ``/settings`` dialog overlay.

    Layout (top to bottom): a title/affordance row, a windowed list of
    rows (section headers as labels, read-only status rows dimmed, and
    actionable rows with a ``→`` marker on the highlighted one), an optional
    scroll indicator when the list overflows, and the two footer rows. The
    window is centered on the highlighted row so navigation/scroll stays
    coherent at any height, mirroring the provider/model selector overlay.
    """

    rows = overlays.settings_rows
    footer = [
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
    title = FrameLine(
        clip_text(
            f" {overlays.settings_title} — ↑/↓ move · enter/space act · esc close",
            width,
        ),
        "selector_title",
    )
    # Reserve the title, the two footer rows, and one row for the optional
    # scroll indicator so the visible window always fits the live region.
    max_rows = max(1, height - 4)
    total = len(rows)
    visible_count = min(total, max_rows)
    start = max(
        0,
        min(
            overlays.settings_selection - (visible_count // 2),
            max(0, total - visible_count),
        ),
    )
    visible = rows[start : start + visible_count]
    rendered_rows: list[FrameLine] = []
    for offset, row in enumerate(visible, start=start):
        selected = offset == overlays.settings_selection
        if row.kind == "header":
            rendered_rows.append(
                FrameLine(clip_text(f"  {row.label}", width), "selector_title")
            )
            continue
        prefix = "→ " if selected else "  "
        if selected:
            kind = "selector_option_selected"
        elif row.action is not None:
            kind = "selector_option"
        else:
            kind = "selector_option_disabled"
        rendered_rows.append(FrameLine(clip_text(f"{prefix}{row.label}", width), kind))
    lines = [title, *rendered_rows]
    if start > 0 or start + visible_count < total:
        lines.append(
            FrameLine(
                clip_text(f"  ({overlays.settings_selection + 1}/{total})", width),
                "slash_menu_scroll",
            )
        )
    lines.extend(footer)
    return lines
