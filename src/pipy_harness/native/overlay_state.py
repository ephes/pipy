"""Terminal-independent state and transitions for product TUI overlays.

``OverlayState`` is the single mutable owner for selector, dialog, session
picker, and custom-extension overlay state.  Exactly one ``active`` kind can be
rendered at a time; the terminal facade performs key decoding, raw-mode and
repaint effects, extension component execution, and filesystem/session writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    SessionPickerRow,
    build_session_picker_rows,
)

OverlayKind = Literal[
    "model",
    "settings",
    "project_trust",
    "tree",
    "scoped_models",
    "session_picker",
    "custom",
]
SettingsOverlayKind = Literal["settings", "project_trust"]
_SETTINGS_OVERLAY_KINDS = frozenset({"settings", "project_trust"})


@dataclass(frozen=True, slots=True)
class _SettingsOverlayPayload:
    """Restorable payload owned by one settings-family overlay frame."""

    rows: tuple[SettingsRow, ...]
    selection: int
    title: str


@dataclass(frozen=True, slots=True)
class _OverlayFrame:
    """One suspended overlay plus any payload shared with another kind."""

    kind: OverlayKind
    settings: _SettingsOverlayPayload | None = None


@dataclass(frozen=True, slots=True)
class ModelSelectorOption:
    """One row offered by the interactive provider/model selector.

    ``label`` is the fully composed display string (provider/model plus an
    availability annotation); ``selectable`` is ``False`` for rows that are
    visible-but-not-choosable (unavailable provider, or a provider that does
    not advertise tool-call support in tool-loop mode). The selector keeps
    such rows navigable so their reason stays readable, but ``Enter`` cannot
    choose them.
    """

    label: str
    selectable: bool


@dataclass(frozen=True, slots=True)
class SettingsRow:
    """One row in the interactive ``/settings`` dialog.

    ``kind`` is ``"header"`` (a non-selectable section label), ``"status"`` (a
    non-selectable read-only line), or ``"action"`` (an actionable row).
    ``action`` is the identifier handed back to the caller when an action row
    is activated with Enter/Space; it is ``None`` for headers/status rows.
    Only rows with a non-``None`` ``action`` are navigable and choosable, so the
    highlight always rests on something the user can act on while read-only
    status rows stay visible for context.
    """

    label: str
    kind: str = "status"
    action: str | None = None


@dataclass(frozen=True, slots=True)
class ScopedModelRow:
    """One row in the interactive ``/scoped-models`` multi-select overlay.

    ``reference`` is the ``provider/model`` reference; ``available`` marks
    auth-available rows (unavailable rows stay visible but are not togglable).
    """

    reference: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class TreeSelectorRow:
    """One visible row in the interactive ``/tree`` selector.

    ``entry_id`` identifies the session-tree entry; ``label`` is the rendered
    display text (already indented/prefixed); ``active`` marks entries on the
    current leaf path; ``labeled`` marks entries that carry a user label.
    """

    entry_id: str
    label: str
    active: bool = False
    labeled: bool = False


@dataclass(slots=True)
class OverlayState:
    """Cohesive state owner for nested, singly rendered TUI overlays."""

    _active: OverlayKind | None = None
    _stack: list[_OverlayFrame] = field(default_factory=list)

    model_options: tuple[ModelSelectorOption, ...] = ()
    model_selection: int = 0
    model_title: str | None = None

    settings_rows: tuple[SettingsRow, ...] = ()
    settings_selection: int = 0
    settings_title: str = "Settings"

    tree_rows: tuple[TreeSelectorRow, ...] = ()
    tree_selection: int = 0
    tree_filter: str = "default"

    scoped_rows: tuple[ScopedModelRow, ...] = ()
    scoped_selection: int = 0
    scoped_checked: set[int] = field(default_factory=set)

    session_rows: tuple[SessionPickerRow, ...] = ()
    session_selection: int = 0
    session_query: str = ""
    session_scope: str = "current"
    session_sort: str = "recent"
    session_named_only: bool = False
    session_show_path: bool = False
    session_mode: str = "list"
    session_input: str = ""
    session_status: str = ""
    session_current: Path | None = None
    session_project: list[SessionListEntry] = field(default_factory=list)
    session_all: list[SessionListEntry] = field(default_factory=list)
    session_now: float = 0.0

    custom_component: object | None = None
    custom_render_width: int | None = None
    custom_hidden: bool = False
    custom_focused: bool = False
    custom_done: bool = False
    custom_result: object = None

    @property
    def active(self) -> OverlayKind | None:
        return self._active

    def is_open(self, kind: OverlayKind) -> bool:
        return self._active == kind

    def activate(self, kind: OverlayKind) -> None:
        """Activate a nested overlay, saving a distinct outer owner once.

        Settings and project-trust are distinct render discriminators backed by
        one payload family. Suspending either therefore captures its rows,
        selection, and title as part of the frame rather than saving only the
        discriminator.
        """

        if self._active == kind:
            return
        if self._active is not None:
            settings = None
            if self._active in _SETTINGS_OVERLAY_KINDS:
                settings = _SettingsOverlayPayload(
                    rows=self.settings_rows,
                    selection=self.settings_selection,
                    title=self.settings_title,
                )
            self._stack.append(_OverlayFrame(self._active, settings))
        self._active = kind

    def supersede(self, kind: OverlayKind | None) -> None:
        """Replace the projection directly and discard stale nesting state."""

        self._stack.clear()
        self._active = kind

    def close(self, kind: OverlayKind) -> None:
        """Close ``kind`` and restore its saved outer overlay when active."""

        if self._active == kind:
            restored = self._stack.pop() if self._stack else None
            self._active = restored.kind if restored is not None else None
            if restored is not None and restored.settings is not None:
                self.settings_rows = restored.settings.rows
                self.settings_selection = restored.settings.selection
                self.settings_title = restored.settings.title
        else:
            self._stack = [saved for saved in self._stack if saved.kind != kind]

    def begin_model(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int,
        title: str | None,
    ) -> bool:
        self.model_options = tuple(options)
        self.model_title = title
        if not self.model_options:
            return False
        self.model_selection = max(0, min(current_index, len(self.model_options) - 1))
        self.activate("model")
        return True

    def navigate_model(self, delta: int) -> bool:
        if not self.model_options:
            return False
        self.model_selection = (self.model_selection + delta) % len(self.model_options)
        return True

    def end_model(self) -> None:
        self.close("model")
        self.model_options = ()
        self.model_selection = 0
        self.model_title = None

    def begin_scoped(
        self, rows: Sequence[ScopedModelRow], checked: Iterable[int]
    ) -> bool:
        self.scoped_rows = tuple(rows)
        if not self.scoped_rows:
            return False
        self.scoped_checked = {
            index
            for index in checked
            if 0 <= index < len(self.scoped_rows) and self.scoped_rows[index].available
        }
        self.scoped_selection = next(
            (i for i, row in enumerate(self.scoped_rows) if row.available), 0
        )
        self.activate("scoped_models")
        return True

    def navigate_scoped(self, delta: int) -> bool:
        """Handle non-empty scoped navigation, even when wrapping in place."""

        total = len(self.scoped_rows)
        if total == 0:
            return False
        index = self.scoped_selection
        for _ in range(total):
            index = (index + delta) % total
            if self.scoped_rows[index].available:
                break
        self.scoped_selection = index
        return True

    def toggle_scoped(self) -> bool:
        index = self.scoped_selection
        if not (0 <= index < len(self.scoped_rows)):
            return False
        if not self.scoped_rows[index].available:
            return False
        if index in self.scoped_checked:
            self.scoped_checked.remove(index)
        else:
            self.scoped_checked.add(index)
        return True

    def select_all_scoped(self) -> None:
        self.scoped_checked = {
            i for i, row in enumerate(self.scoped_rows) if row.available
        }

    def clear_scoped(self) -> None:
        self.scoped_checked = set()

    def selected_scoped_references(self) -> frozenset[str]:
        return frozenset(
            self.scoped_rows[i].reference for i in sorted(self.scoped_checked)
        )

    def end_scoped(self) -> None:
        self.close("scoped_models")
        self.scoped_rows = ()
        self.scoped_selection = 0
        self.scoped_checked = set()

    def begin_settings(
        self,
        rows: Sequence[SettingsRow],
        *,
        current_index: int | None,
        title: str,
        kind: SettingsOverlayKind = "settings",
    ) -> bool:
        new_rows = tuple(rows)
        if not new_rows:
            return False
        new_selection = self._initial_settings_selection(new_rows, current_index)
        # Activate before replacing the shared settings-family payload so a
        # different nested discriminator captures the exact outer owner.
        self.activate(kind)
        self.settings_title = title
        self.settings_rows = new_rows
        self.settings_selection = new_selection
        return True

    @staticmethod
    def _actionable_settings_indices(rows: Sequence[SettingsRow]) -> list[int]:
        return [index for index, row in enumerate(rows) if row.action is not None]

    def actionable_settings_indices(self) -> list[int]:
        return self._actionable_settings_indices(self.settings_rows)

    @classmethod
    def _initial_settings_selection(
        cls, rows: Sequence[SettingsRow], current_index: int | None
    ) -> int:
        actionable = cls._actionable_settings_indices(rows)
        if not actionable:
            return 0
        if current_index is not None and current_index in actionable:
            return current_index
        return actionable[0]

    def initial_settings_selection(self, current_index: int | None) -> int:
        return self._initial_settings_selection(self.settings_rows, current_index)

    def clamp_settings_selection(self, selection: int) -> int:
        actionable = self.actionable_settings_indices()
        if not actionable:
            return min(max(0, selection), max(0, len(self.settings_rows) - 1))
        if selection in actionable:
            return selection
        for index in actionable:
            if index >= selection:
                return index
        return actionable[-1]

    def replace_settings_rows(self, rows: Sequence[SettingsRow]) -> bool:
        old_selection = self.settings_selection
        self.settings_rows = tuple(rows)
        if not self.settings_rows:
            return False
        self.settings_selection = self.clamp_settings_selection(old_selection)
        return True

    def navigate_settings(self, delta: int) -> bool:
        """Handle actionable navigation, even when wrapping in place."""

        actionable = self.actionable_settings_indices()
        if not actionable:
            return False
        if self.settings_selection in actionable:
            position = actionable.index(self.settings_selection)
            position = (position + delta) % len(actionable)
        else:
            position = 0 if delta > 0 else len(actionable) - 1
        self.settings_selection = actionable[position]
        return True

    def end_settings(self) -> None:
        if self.active in _SETTINGS_OVERLAY_KINDS:
            self.close(self.active)
        else:
            self.close("settings")
            self.close("project_trust")
        if self.active not in _SETTINGS_OVERLAY_KINDS:
            self.settings_rows = ()
            self.settings_selection = 0
            self.settings_title = "Settings"

    def begin_tree(
        self,
        rows: Sequence[TreeSelectorRow],
        *,
        filter_mode: str,
    ) -> None:
        self.tree_filter = filter_mode
        self.tree_rows = tuple(rows)
        self.tree_selection = self.initial_tree_selection()
        self.activate("tree")

    def initial_tree_selection(self) -> int:
        active = [index for index, row in enumerate(self.tree_rows) if row.active]
        if active:
            return active[-1]
        return max(0, len(self.tree_rows) - 1)

    def replace_tree_rows(
        self, rows: Sequence[TreeSelectorRow], *, reset_to_active: bool = False
    ) -> None:
        old_selection = self.tree_selection
        self.tree_rows = tuple(rows)
        if reset_to_active:
            self.tree_selection = self.initial_tree_selection()
        else:
            self.tree_selection = min(old_selection, max(0, len(self.tree_rows) - 1))

    def navigate_tree(self, delta: int) -> bool:
        if not self.tree_rows:
            return False
        self.tree_selection = (self.tree_selection + delta) % len(self.tree_rows)
        return True

    def end_tree(self) -> None:
        self.close("tree")
        self.tree_rows = ()
        self.tree_selection = 0

    def begin_session(
        self,
        *,
        project_sessions: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry],
        current_path: Path | None,
        now: float,
    ) -> None:
        self.session_project = list(project_sessions)
        self.session_all = list(all_sessions)
        self.session_current = current_path
        self.session_query = ""
        self.session_scope = "current"
        self.session_sort = "recent"
        self.session_named_only = False
        self.session_show_path = False
        self.session_mode = "list"
        self.session_input = ""
        self.session_status = ""
        self.session_selection = 0
        self.session_now = now
        self.activate("session_picker")
        self.rebuild_session_rows()
        self.select_current_session()

    def rebuild_session_rows(self) -> None:
        self.session_rows = tuple(
            build_session_picker_rows(
                self.session_project,
                self.session_all,
                scope=self.session_scope,
                query=self.session_query,
                sort=self.session_sort,
                named_only=self.session_named_only,
                current_path=self.session_current,
            )
        )
        if self.session_selection >= len(self.session_rows):
            self.session_selection = max(0, len(self.session_rows) - 1)

    def select_current_session(self) -> None:
        for index, row in enumerate(self.session_rows):
            if row.is_current:
                self.session_selection = index
                return

    def selected_session_row(self) -> SessionPickerRow | None:
        if 0 <= self.session_selection < len(self.session_rows):
            return self.session_rows[self.session_selection]
        return None

    def navigate_session(self, delta: int) -> bool:
        if not self.session_rows:
            return False
        self.session_selection = (self.session_selection + delta) % len(
            self.session_rows
        )
        return True

    def apply_session_rename(self, path: Path, name: str) -> None:
        def relabel(entries: list[SessionListEntry]) -> list[SessionListEntry]:
            return [
                SessionListEntry(
                    path=entry.path,
                    session_id=entry.session_id,
                    name=name,
                    message_count=entry.message_count,
                    cwd=entry.cwd,
                    mtime=entry.mtime,
                )
                if entry.path == path
                else entry
                for entry in entries
            ]

        self.session_project = relabel(self.session_project)
        self.session_all = relabel(self.session_all)

    def remove_session_entry(self, path: Path) -> None:
        self.session_project = [e for e in self.session_project if e.path != path]
        self.session_all = [e for e in self.session_all if e.path != path]

    def end_session(self) -> None:
        self.close("session_picker")
        self.session_rows = ()
        self.session_selection = 0
        self.session_mode = "list"
        self.session_input = ""
        self.session_query = ""
        self.session_project = []
        self.session_all = []

    def begin_custom(self, component: object, *, render_width: int | None) -> None:
        self.custom_done = False
        self.custom_result = None
        self.custom_hidden = False
        self.custom_focused = True
        self.custom_component = component
        self.custom_render_width = render_width
        self.activate("custom")

    def finish_custom(self, result: object = None) -> bool:
        if self.custom_done:
            return False
        self.custom_done = True
        self.custom_result = result
        return True

    def end_custom(self, *, previous_width: int | None) -> object:
        result = self.custom_result
        self.close("custom")
        self.custom_component = None
        self.custom_render_width = previous_width
        self.custom_hidden = False
        self.custom_focused = False
        return result
