"""Concrete modal orchestration over the screen's single key loop."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TypeVar, cast

from pipy_harness.native.extension_types import (
    CustomComponentFactory,
    CustomComponentOptions,
)
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.overlay_state import (
    ModelSelectorOption,
    OverlayState,
    ScopedModelRow,
    SettingsOverlayKind,
    SettingsRow,
    TreeSelectorRow,
)
from pipy_harness.native.session_tree_commands import SessionListEntry
from pipy_harness.native.ui.components.custom_editor import ExtensionEditorComponent
from pipy_harness.native.ui.components.custom_overlay import CustomComponentRunner
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionConfirmComponent,
    ExtensionExternalEditor,
    ExtensionInputComponent,
    ExtensionSelectComponent,
)
from pipy_harness.native.ui.components.input_editor import InputEditor
from pipy_harness.native.ui.components.model_selector import ModelSelectorComponent
from pipy_harness.native.ui.components.scoped_models_selector import (
    ScopedModelsSelectorComponent,
)
from pipy_harness.native.ui.components.session_picker import SessionPickerComponent
from pipy_harness.native.ui.components.settings_dialog import SettingsDialogComponent
from pipy_harness.native.ui.components.tree_selector import (
    TreeSelectorClose,
    TreeSelectorComponent,
)
from pipy_harness.native.ui.key_specs import resolved_key_specs
from pipy_harness.native.ui.screen import DriveOwner, DriveResult, Screen

_CloseT = TypeVar("_CloseT")
_ResultT = TypeVar("_ResultT")


def _open_result(opened: bool, cancelled: _ResultT) -> DriveResult[_ResultT] | None:
    return None if opened else DriveResult(cancelled)


def _open_void(callback: Callable[..., None], *args: object, **kwargs: object) -> None:
    callback(*args, **kwargs)


def _drive_result(
    closed: _CloseT | None, project: Callable[[_CloseT], _ResultT]
) -> DriveResult[_ResultT] | None:
    return None if closed is None else DriveResult(project(closed))


class TerminalModalDriver:
    """Own the six screen-driven modals and four extension dialog projections."""

    def __init__(
        self,
        overlays: OverlayState,
        screen: Screen,
        input_editor: InputEditor,
        external_editor: ExtensionExternalEditor,
        keybindings_manager: Callable[[], KeybindingsManager | None],
    ) -> None:
        self._overlays = overlays
        self._screen = screen
        self._input_editor = input_editor
        self._external_editor = external_editor
        self._keybindings_manager = keybindings_manager

    def run_model_selector(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int = 0,
        title: str | None = None,
    ) -> int | None:
        selector = ModelSelectorComponent(
            self._overlays, self._screen.paint_lock, self._screen.paint
        )
        owner: DriveOwner[int | None] = DriveOwner(
            open=lambda: cast(
                DriveResult[int | None] | None,
                _open_result(
                    selector.open(options, current_index=current_index, title=title),
                    None,
                ),
            ),
            handle_key=lambda key: cast(
                DriveResult[int | None] | None,
                _drive_result(selector.handle_key(key), lambda closed: closed.index),
            ),
            consume_paste=self._input_editor.consume_paste,
        )
        return self._screen.drive(owner)

    def run_scoped_models_selector(
        self,
        rows: Sequence[ScopedModelRow],
        *,
        checked: Iterable[int] = (),
    ) -> frozenset[str] | None:
        selector = ScopedModelsSelectorComponent(
            self._overlays, self._screen.paint_lock, self._screen.paint
        )
        owner: DriveOwner[frozenset[str] | None] = DriveOwner(
            open=lambda: cast(
                DriveResult[frozenset[str] | None] | None,
                _open_result(selector.open(rows, checked), None),
            ),
            handle_key=lambda key: cast(
                DriveResult[frozenset[str] | None] | None,
                _drive_result(
                    selector.handle_key(key), lambda closed: closed.references
                ),
            ),
            consume_paste=self._input_editor.consume_paste,
        )
        return self._screen.drive(owner)

    def run_settings_dialog(
        self,
        rows: Sequence[SettingsRow],
        *,
        on_local_action: Callable[[str], Sequence[SettingsRow]],
        exit_actions: frozenset[str] = frozenset(),
        current_index: int | None = None,
        title: str = "Settings",
        overlay_kind: SettingsOverlayKind = "settings",
    ) -> str | None:
        dialog = SettingsDialogComponent(
            self._overlays,
            self._screen.paint_lock,
            self._screen.paint,
            on_local_action=on_local_action,
            exit_actions=exit_actions,
        )
        owner: DriveOwner[str | None] = DriveOwner(
            open=lambda: cast(
                DriveResult[str | None] | None,
                _open_result(
                    dialog.open(
                        rows,
                        current_index=current_index,
                        title=title,
                        kind=overlay_kind,
                    ),
                    None,
                ),
            ),
            handle_key=lambda key: cast(
                DriveResult[str | None] | None,
                _drive_result(dialog.handle_key(key), lambda closed: closed.action),
            ),
            consume_paste=self._input_editor.consume_paste,
        )
        return self._screen.drive(owner)

    def run_tree_selector(
        self,
        *,
        build_rows: Callable[[str], Sequence[TreeSelectorRow]],
        filter_modes: Sequence[str],
        initial_filter: str,
        on_label_toggle: Callable[[str], None],
    ) -> TreeSelectorClose:
        selector = TreeSelectorComponent(
            self._overlays,
            self._screen.paint_lock,
            self._screen.paint,
            build_rows=build_rows,
            filter_modes=filter_modes,
            on_label_toggle=on_label_toggle,
        )
        owner: DriveOwner[TreeSelectorClose] = DriveOwner(
            open=lambda: _open_void(selector.open, initial_filter),
            handle_key=lambda key: cast(
                DriveResult[TreeSelectorClose] | None,
                _drive_result(selector.handle_key(key), lambda closed: closed),
            ),
            consume_paste=self._input_editor.consume_paste,
        )
        return self._screen.drive(owner)

    def run_custom_component(
        self,
        factory: CustomComponentFactory,
        options: CustomComponentOptions | None = None,
    ) -> object:
        runner = CustomComponentRunner(self._overlays, self._screen.paint)
        runner.create(factory, options)
        owner: DriveOwner[object] = DriveOwner(
            open=lambda: _open_void(runner.begin),
            handle_key=lambda key: (
                DriveResult(None) if runner.handle_key(key) else None
            ),
            is_finished=lambda: runner.finished,
            dispose=runner.dispose,
        )
        return self._screen.drive(owner)

    def run_extension_select(self, title: str, options: Sequence[str]) -> str | None:
        choices = tuple(str(option) for option in options if str(option))
        if not choices:
            return None
        result = self.run_custom_component(
            lambda done: ExtensionSelectComponent(str(title), choices, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_input(
        self, title: str, placeholder: str | None = None
    ) -> str | None:
        result = self.run_custom_component(
            lambda done: ExtensionInputComponent(str(title), placeholder, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_editor(
        self, title: str, prefill: str | None = None
    ) -> str | None:
        result = self.run_custom_component(
            lambda done: ExtensionEditorComponent(
                str(title),
                prefill,
                done,
                self._external_editor.callback(),
                resolved_key_specs("app.editor.external", self._keybindings_manager()),
            )
        )
        return result if isinstance(result, str) else None

    def run_extension_confirm(self, title: str, message: str) -> bool:
        result = self.run_custom_component(
            lambda done: ExtensionConfirmComponent(str(title), str(message), done)
        )
        return result == "Yes"

    def run_session_picker(
        self,
        *,
        project_sessions: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry],
        current_path: Path | None = None,
        on_rename: Callable[[Path, str], None] | None = None,
        on_delete: Callable[[Path], tuple[bool, str]] | None = None,
        now: float | None = None,
    ) -> Path | None:
        picker = SessionPickerComponent(
            self._overlays,
            self._screen.paint_lock,
            self._screen.paint,
            on_rename=on_rename,
            on_delete=on_delete,
            consume_paste=self._input_editor.consume_paste,
        )
        owner: DriveOwner[Path | None] = DriveOwner(
            open=lambda: _open_void(
                picker.open,
                project_sessions=project_sessions,
                all_sessions=all_sessions,
                current_path=current_path,
                now=now,
            ),
            handle_key=lambda key: cast(
                DriveResult[Path | None] | None,
                _drive_result(picker.handle_key(key), lambda closed: closed.path),
            ),
        )
        return self._screen.drive(owner)
