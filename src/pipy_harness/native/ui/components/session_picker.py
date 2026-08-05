"""The interactive session-picker overlay (``/resume`` + ``-r``): modes and keys.

Same ownership contract as the sibling overlay components: the picker state
already lives on the shared :class:`OverlayState` record (``session_rows``,
``session_query``, ``session_mode``, ...), so the component takes that record,
the shared :class:`PaintLock`, and a repaint callable instead of the terminal-UI
shell. The shell keeps only the raw-mode key loop and forwards each decoded key
to :meth:`SessionPickerComponent.handle_key`; a :class:`SessionPickerClose`
return is the only way a result leaves.

The picker is a three-mode state machine on ``session_mode``: ``list`` (search,
navigate, toggle scope/sort/named/path), ``rename`` (edit a name for the
highlighted row), and ``confirm-delete`` (a ``[y/N]`` prompt whose default is
No). ``on_rename``/``on_delete`` are session-side callbacks (they write session
files) and run *outside* the paint lock -- the lock guards only overlay-record
transitions, so a concurrent painter never observes a half-applied row swap,
while session code never runs under the frame mutex. ``consume_paste`` drains
the editor's bracketed-paste buffer when a paste arrives in list mode; the
sub-modes deliberately ignore the ``paste`` key, matching the pre-component
behavior.

Rendering is a pure function of the overlay record plus the two footer rows the
shell owns, so the shell's frame dispatch calls
:func:`session_picker_region_lines` without holding a component instance.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.frame_renderer import FrameLine, clip_text
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    format_session_picker_label,
    sanitize_label_text,
)
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class SessionPickerClose:
    """The picker finished: ``path`` is the chosen session, ``None`` a cancel."""

    path: Path | None


class SessionPickerComponent:
    """Search/rename/delete state machine behind the session-picker overlay."""

    def __init__(
        self,
        overlays: OverlayState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        on_rename: Callable[[Path, str], None] | None,
        on_delete: Callable[[Path], tuple[bool, str]] | None,
        consume_paste: Callable[[], object],
    ) -> None:
        self._overlays = overlays
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._on_rename = on_rename
        self._on_delete = on_delete
        self._consume_paste = consume_paste

    def open(
        self,
        *,
        project_sessions: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry],
        current_path: Path | None = None,
        now: float | None = None,
    ) -> None:
        """Activate the overlay over the session pools and repaint."""

        with self._paint_lock:
            self._overlays.begin_session(
                project_sessions=project_sessions,
                all_sessions=all_sessions,
                current_path=current_path,
                now=now if now is not None else time.time(),
            )
        self._repaint()

    def handle_key(self, key: str | None) -> SessionPickerClose | None:
        """Apply one decoded key; non-``None`` means the overlay closed.

        Typing searches; up/down move; ``enter`` opens the highlighted session;
        ``tab`` toggles current-project / all-projects scope; ``ctrl-p`` toggles
        the file-path column; ``Ctrl+S`` cycles the sort; ``Ctrl+N`` filters to
        named sessions; ``Ctrl+R`` renames and ``Ctrl+X`` deletes (each with an
        in-overlay confirmation/edit); ``esc``/``ctrl-c``/``ctrl-d``/EOF cancel.
        """

        if self._overlays.session_mode == "rename":
            return self._handle_rename_key(key)
        if self._overlays.session_mode == "confirm-delete":
            return self._handle_delete_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str | None) -> SessionPickerClose | None:
        if key is None or key in {"esc", "ctrl-c", "ctrl-d"}:
            return self._close(None)
        if key == "paste":
            self._consume_paste()
            return None
        if key in {"up", "down"}:
            self._navigate(-1 if key == "up" else 1)
            return None
        if key == "enter":
            row = self._overlays.selected_session_row()
            if row is not None:
                return self._close(row.path)
            return None
        self._apply_list_action(key)
        return None

    def _apply_list_action(self, key: str) -> None:
        if key == "tab":
            self._toggle_scope()
        elif key == "ctrl-p":
            self._toggle_show_path()
        elif key == "\x13":  # Ctrl+S — cycle sort
            self._cycle_sort()
        elif key == "\x0e":  # Ctrl+N — named-only filter
            self._toggle_named_only()
        elif key == "\x12":  # Ctrl+R — rename
            self._begin_rename()
        elif key == "\x18":  # Ctrl+X — delete (confirm)
            self._begin_delete()
        else:
            self._edit_query(key)

    def _toggle_scope(self) -> None:
        with self._paint_lock:
            self._overlays.session_scope = (
                "all" if self._overlays.session_scope == "current" else "current"
            )
            self._overlays.session_selection = 0
            self._overlays.session_status = ""
            self._overlays.rebuild_session_rows()
        self._repaint()

    def _toggle_show_path(self) -> None:
        with self._paint_lock:
            self._overlays.session_show_path = not self._overlays.session_show_path
        self._repaint()

    def _cycle_sort(self) -> None:
        with self._paint_lock:
            self._overlays.session_sort = (
                "name" if self._overlays.session_sort == "recent" else "recent"
            )
            self._overlays.rebuild_session_rows()
        self._repaint()

    def _toggle_named_only(self) -> None:
        with self._paint_lock:
            self._overlays.session_named_only = not self._overlays.session_named_only
            self._overlays.session_selection = 0
            self._overlays.rebuild_session_rows()
        self._repaint()

    def _begin_rename(self) -> None:
        row = self._overlays.selected_session_row()
        if self._on_rename is None or row is None:
            return
        with self._paint_lock:
            self._overlays.session_mode = "rename"
            self._overlays.session_input = row.name or ""
            self._overlays.session_status = ""
        self._repaint()

    def _begin_delete(self) -> None:
        row = self._overlays.selected_session_row()
        if self._on_delete is None or row is None:
            return
        with self._paint_lock:
            if row.is_current:
                self._overlays.session_status = "cannot delete the active session"
            else:
                self._overlays.session_mode = "confirm-delete"
        self._repaint()

    def _edit_query(self, key: str) -> None:
        if key == "backspace":
            if self._overlays.session_query:
                with self._paint_lock:
                    self._overlays.session_query = self._overlays.session_query[:-1]
                    self._overlays.session_selection = 0
                    self._overlays.rebuild_session_rows()
                self._repaint()
            return
        if len(key) == 1 and key.isprintable():
            with self._paint_lock:
                self._overlays.session_query += key
                self._overlays.session_selection = 0
                self._overlays.rebuild_session_rows()
            self._repaint()

    def _handle_rename_key(self, key: str | None) -> SessionPickerClose | None:
        # Ctrl-C/Ctrl-D (and EOF) cancel the whole picker from any sub-mode;
        # Esc backs out of the rename to the list (see below).
        if key is None or key in {"ctrl-c", "ctrl-d"}:
            return self._close(None)
        if key == "esc":
            with self._paint_lock:
                self._overlays.session_mode = "list"
                self._overlays.session_input = ""
            self._repaint()
            return None
        if key == "enter":
            self._commit_rename()
            return None
        if key == "backspace":
            with self._paint_lock:
                self._overlays.session_input = self._overlays.session_input[:-1]
            self._repaint()
            return None
        if len(key) == 1 and key.isprintable():
            with self._paint_lock:
                self._overlays.session_input += key
            self._repaint()
        return None

    def _commit_rename(self) -> None:
        # Run the session-side callback outside the paint lock, then apply the
        # whole post-callback transition atomically so a concurrent painter
        # never renders a half-updated frame (renamed rows under a stale mode).
        on_rename = self._on_rename
        row = self._overlays.selected_session_row()
        name = self._overlays.session_input.strip()
        renamed: Path | None = None
        if row is not None and name and on_rename is not None:
            on_rename(row.path, name)
            renamed = row.path
        with self._paint_lock:
            if renamed is not None:
                self._overlays.apply_session_rename(renamed, name)
                self._overlays.session_status = f"renamed to {name}"
            self._overlays.session_mode = "list"
            self._overlays.session_input = ""
            self._overlays.rebuild_session_rows()
        self._repaint()

    def _handle_delete_key(self, key: str | None) -> SessionPickerClose | None:
        # Ctrl-C/Ctrl-D (and EOF) cancel the whole picker; Esc/Enter/n take the
        # safe [y/N] default and back out to the list.
        if key is None or key in {"ctrl-c", "ctrl-d"}:
            return self._close(None)
        # The prompt is `[y/N]`: only an explicit `y` confirms; Enter (and
        # Esc/n) take the safe default and cancel the deletion.
        if key in {"y", "Y"}:
            self._commit_delete()
            return None
        if key in {"esc", "enter", "n", "N"}:
            with self._paint_lock:
                self._overlays.session_mode = "list"
            self._repaint()
        return None

    def _commit_delete(self) -> None:
        # Run the session-side callback outside the paint lock, then apply the
        # whole post-callback transition atomically so a concurrent painter
        # never renders a half-updated frame (a removed row under a still-open
        # delete confirmation).
        on_delete = self._on_delete
        row = self._overlays.selected_session_row()
        outcome: tuple[Path, bool, str] | None = None
        if row is not None and on_delete is not None:
            ok, detail = on_delete(row.path)
            outcome = (row.path, ok, detail)
        with self._paint_lock:
            if outcome is not None:
                path, ok, detail = outcome
                if ok:
                    self._overlays.remove_session_entry(path)
                self._overlays.session_status = detail
            self._overlays.session_mode = "list"
            self._overlays.session_selection = 0
            self._overlays.rebuild_session_rows()
        self._repaint()

    def _navigate(self, delta: int) -> None:
        with self._paint_lock:
            moved = self._overlays.navigate_session(delta)
        if moved:
            self._repaint()

    def _close(self, path: Path | None) -> SessionPickerClose:
        with self._paint_lock:
            self._overlays.end_session()
        self._repaint()
        return SessionPickerClose(path)


def _prompt_line(overlays: OverlayState, width: int) -> FrameLine:
    """Render the mode-specific prompt row under the picker title."""

    if overlays.session_mode == "rename":
        # The rename buffer is seeded from the existing (user-controlled)
        # session name, so sanitize it before rendering to keep terminal
        # escape sequences out of the live frame.
        return FrameLine(
            clip_text(
                f"  rename: {sanitize_label_text(overlays.session_input)}▏",
                width,
            ),
            "input",
        )
    if overlays.session_mode == "confirm-delete":
        row = overlays.selected_session_row()
        shown = (row.name or row.session_id[:8]) if row else ""
        return FrameLine(
            clip_text(f"  delete {sanitize_label_text(shown)}? [y/N]", width),
            "notice",
        )
    return FrameLine(
        clip_text(f"  search: {overlays.session_query}", width),
        "normal",
    )


def session_picker_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
) -> list[FrameLine]:
    """Compose the interactive session-picker overlay."""

    footer = [
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
    scope_label = "all projects" if overlays.session_scope == "all" else "this project"
    title = FrameLine(
        clip_text(
            f" Resume session — {scope_label} · sort:{overlays.session_sort}"
            + ("· named" if overlays.session_named_only else "")
            + " · ↑/↓ enter · ^P path ^S sort ^N named tab scope"
            + " ^R rename ^X delete · esc cancel",
            width,
        ),
        "selector_title",
    )
    prompt = _prompt_line(overlays, width)
    status_lines: list[FrameLine] = []
    if overlays.session_status:
        # The status echoes user-controlled names / delete details, so
        # sanitize it against terminal escape injection like the row labels.
        status_lines.append(
            FrameLine(
                clip_text(f"  {sanitize_label_text(overlays.session_status)}", width),
                "notice",
            )
        )

    rows_data = overlays.session_rows
    # Reserve: title + prompt + status + scroll indicator + 2 footer rows.
    max_rows = max(1, height - 5 - len(status_lines))
    total = len(rows_data)
    if total == 0:
        empty = FrameLine(clip_text("  (no native sessions)", width), "normal")
        return [title, prompt, *status_lines, empty, *footer]
    visible_count = min(total, max_rows)
    start = max(
        0,
        min(
            overlays.session_selection - (visible_count // 2),
            max(0, total - visible_count),
        ),
    )
    visible = rows_data[start : start + visible_count]
    rendered: list[FrameLine] = []
    for offset, row in enumerate(visible, start=start):
        selected = offset == overlays.session_selection
        prefix = "→ " if selected else "  "
        label = format_session_picker_label(
            row,
            show_path=overlays.session_show_path,
            show_cwd=overlays.session_scope == "all",
            now=overlays.session_now,
        )
        kind = "selector_option_selected" if selected else "selector_option"
        rendered.append(FrameLine(clip_text(f"{prefix}{label}", width), kind))
    lines = [title, prompt, *status_lines, *rendered]
    if start > 0 or start + visible_count < total:
        lines.append(
            FrameLine(
                clip_text(f"  ({overlays.session_selection + 1}/{total})", width),
                "slash_menu_scroll",
            )
        )
    lines.extend(footer)
    return lines
