"""Executable ownership and transition invariants for Slice 12 TUI state."""

from __future__ import annotations

import io
import termios
import tty
from dataclasses import fields
from pathlib import Path

import pytest

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeState,
)
from pipy_harness.native.overlay_state import (
    ModelSelectorOption,
    OverlayState,
    ScopedModelRow,
    SettingsRow,
    TreeSelectorRow,
)
from pipy_harness.native.session_tree_commands import SessionListEntry
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.scoped_models_selector import (
    ScopedModelsSelectorComponent,
)
from pipy_harness.native.ui.components.session_picker import SessionPickerComponent
from pipy_harness.native.ui.components.settings_dialog import SettingsDialogComponent
from pipy_harness.native.ui.screen import Screen


def _session(path: Path) -> SessionListEntry:
    return SessionListEntry(
        path=path,
        session_id=path.stem,
        name=path.stem,
        message_count=1,
        cwd=str(path.parent),
        mtime=10.0,
    )


def test_overlay_owner_restores_nested_kind_without_duplicate_same_kind_frames() -> (
    None
):
    state = OverlayState()
    assert state.begin_model(
        (ModelSelectorOption("a", True), ModelSelectorOption("b", True)),
        current_index=9,
        title=None,
    )
    assert state.active == "model"
    assert state.model_selection == 1

    state.begin_tree((TreeSelectorRow("root", "root", active=True),), filter_mode="all")
    state.activate("tree")

    assert state.active == "tree"
    assert [frame.kind for frame in state._stack] == ["model"]
    assert not state.is_open("model")
    assert state.is_open("tree")
    assert sum(state.is_open(kind) for kind in ("model", "tree", "custom")) == 1

    state.end_tree()
    assert state.active == "model"
    assert state._stack == []

    # Direct compatibility projection writes intentionally supersede nesting.
    state.begin_tree((TreeSelectorRow("root", "root"),), filter_mode="all")
    state.supersede("custom")
    assert state.active == "custom"
    assert state._stack == []


def test_overlay_navigation_wraps_and_scoped_selection_rejects_unavailable() -> None:
    state = OverlayState()
    state.begin_model(
        (ModelSelectorOption("a", True), ModelSelectorOption("b", True)),
        current_index=0,
        title="pick",
    )
    assert state.navigate_model(-1)
    assert state.model_selection == 1

    assert state.begin_scoped(
        (
            ScopedModelRow("unavailable", available=False),
            ScopedModelRow("one"),
            ScopedModelRow("two"),
        ),
        checked=(0, 2, 99),
    )
    assert state.active == "scoped_models"
    assert state.scoped_checked == {2}
    assert state.scoped_selection == 1
    assert state.navigate_scoped(-1)
    assert state.scoped_selection == 2
    assert state.toggle_scoped()
    assert state.selected_scoped_references() == frozenset()


def test_settings_and_session_transitions_are_terminal_independent(
    tmp_path: Path,
) -> None:
    state = OverlayState()
    rows = (
        SettingsRow("header", kind="header"),
        SettingsRow("first", kind="action", action="first"),
        SettingsRow("status", kind="status"),
        SettingsRow("second", kind="action", action="second"),
    )
    assert state.begin_settings(
        rows, current_index=None, title="Project trust", kind="project_trust"
    )
    assert state.active == "project_trust"
    assert state.settings_selection == 1
    assert state.navigate_settings(-1)
    assert state.settings_selection == 3

    current = tmp_path / "current.jsonl"
    other = tmp_path / "other.jsonl"
    state.begin_session(
        project_sessions=(_session(current), _session(other)),
        all_sessions=(_session(current), _session(other)),
        current_path=current,
        now=100.0,
    )
    assert state.active == "session_picker"
    selected = state.selected_session_row()
    assert selected is not None
    assert selected.path == current
    assert state.navigate_session(-1)
    selected = state.selected_session_row()
    assert selected is not None
    assert selected.path == other
    state.apply_session_rename(other, "renamed")
    state.rebuild_session_rows()
    assert any(row.name == "renamed" for row in state.session_rows)
    state.remove_session_entry(other)
    state.rebuild_session_rows()
    assert all(row.path != other for row in state.session_rows)
    old_project = state.session_project
    old_all = state.session_all
    project_snapshot = list(old_project)
    all_snapshot = list(old_all)
    state.end_session()
    assert state.active == "project_trust"
    state.end_settings()
    assert state.active is None
    assert old_project == project_snapshot
    assert old_all == all_snapshot
    assert state.session_project == []
    assert state.session_project is not old_project
    assert state.session_all == []
    assert state.session_all is not old_all


def test_nested_settings_family_restores_exact_outer_payload() -> None:
    state = OverlayState()
    outer_rows = (
        SettingsRow("outer header", kind="header"),
        SettingsRow("outer first", kind="action", action="first"),
        SettingsRow("outer status", kind="status"),
        SettingsRow("outer second", kind="action", action="second"),
    )
    inner_rows = (SettingsRow("inner", kind="action", action="inner"),)

    assert state.begin_settings(
        outer_rows, current_index=3, title="Outer settings", kind="settings"
    )
    assert state.begin_settings(
        inner_rows,
        current_index=0,
        title="Inner project trust",
        kind="project_trust",
    )
    assert state.active == "project_trust"
    assert state.settings_rows == inner_rows

    state.end_settings()
    assert state.active == "settings"
    assert state.settings_rows == outer_rows
    assert state.settings_title == "Outer settings"
    assert state.settings_selection == 3
    assert state.navigate_settings(1)
    assert state.settings_selection == 1

    state.end_settings()
    assert state.active is None
    assert state.settings_rows == ()
    assert state.settings_title == "Settings"
    assert state.settings_selection == 0


def test_empty_nested_settings_candidate_preserves_exact_outer_payload() -> None:
    state = OverlayState()
    outer_rows = (
        SettingsRow("outer first", kind="action", action="first"),
        SettingsRow("outer second", kind="action", action="second"),
    )
    assert state.begin_settings(
        outer_rows, current_index=1, title="Outer settings", kind="settings"
    )

    assert not state.begin_settings(
        (),
        current_index=0,
        title="Empty project trust",
        kind="project_trust",
    )

    assert state.active == "settings"
    assert state.settings_rows == outer_rows
    assert state.settings_selection == 1
    assert state.settings_title == "Outer settings"
    state.end_settings()
    assert state.active is None


def test_settings_overlay_identity_is_explicit_not_title_coupled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    seen: list[str | None] = []

    monkeypatch.setattr(Screen, "paint", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    def cancel(ui: Screen, _fd: int) -> str:
        seen.append(ui._overlays.active)
        return "esc"

    monkeypatch.setattr(Screen, "read_key_polling_resize", cancel)
    with input_path.open(encoding="utf-8") as input_stream:
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=io.StringIO(),
            cwd=tmp_path,
        )
        rows = (SettingsRow("choose", kind="action", action="choose"),)
        ui.run_settings_dialog(
            rows,
            on_local_action=lambda _action: rows,
            title="A title chosen by the caller",
            overlay_kind="project_trust",
        )
        ui.run_settings_dialog(
            rows,
            on_local_action=lambda _action: rows,
            title="Project trust",
        )

    assert seen == ["project_trust", "settings"]


def test_nested_facade_driver_keeps_raw_mode_and_restores_outer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    keys = iter(("enter", "esc", "down", "esc"))
    painted: list[tuple[str | None, str]] = []
    raw_calls: list[int] = []
    restore_calls: list[tuple[int, int, object]] = []
    outer_selections_before_close: list[int] = []

    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda fd: raw_calls.append(fd))
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )

    def read_key(ui: Screen, _fd: int) -> str:
        key = next(keys)
        if key == "esc" and ui._overlays.active == "settings":
            outer_selections_before_close.append(ui._overlays.settings_selection)
        return key

    monkeypatch.setattr(Screen, "read_key_polling_resize", read_key)

    def capture_paint(ui: Screen) -> None:
        lines = ui._live_region_lines(width=72, height=14)
        painted.append((ui._overlays.active, "\n".join(line.text for line in lines)))

    monkeypatch.setattr(Screen, "paint", capture_paint)
    with input_path.open(encoding="utf-8") as input_stream:
        input_fd = input_stream.fileno()
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=io.StringIO(),
            cwd=tmp_path,
        )
        rows = (
            SettingsRow("open model", kind="action", action="open"),
            SettingsRow("outer still handles keys", kind="action", action="other"),
        )

        def open_inner(_action: str) -> tuple[SettingsRow, ...]:
            assert ui._overlays.active == "settings"
            assert (
                ui.run_model_selector(
                    (ModelSelectorOption("inner model", True),), title="Inner"
                )
                is None
            )
            assert ui._overlays.active == "settings"
            assert ui._driver._raw_mode_depth == 1
            assert restore_calls == []
            return rows

        assert ui.run_settings_dialog(rows, on_local_action=open_inner) is None
        assert ui._overlays.settings_selection == 0
        assert ui._driver._raw_mode_depth == 0
        assert ui._driver._old_termios is None

    assert outer_selections_before_close == [1]
    assert raw_calls == [input_fd]
    assert restore_calls == [(input_fd, termios.TCSADRAIN, "saved")]
    assert any(kind == "model" and "inner model" in frame for kind, frame in painted)
    restored = [frame for kind, frame in painted if kind == "settings"]
    assert len(restored) >= 3
    assert any("outer still handles keys" in frame for frame in restored)
    assert painted[-1][0] is None


def test_nested_settings_project_trust_facade_restores_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    keys = iter(("enter", "esc", "down", "enter"))
    paints: list[
        tuple[
            str | None,
            str,
            int,
            tuple[SettingsRow, ...],
            str,
        ]
    ] = []
    restore_calls: list[tuple[int, int, object]] = []

    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    monkeypatch.setattr(
        Screen,
        "read_key_polling_resize",
        lambda _ui, _fd: next(keys),
    )

    def capture_paint(ui: Screen) -> None:
        frame = "\n".join(
            line.text for line in ui._live_region_lines(width=72, height=14)
        )
        paints.append(
            (
                ui._overlays.active,
                ui._overlays.settings_title,
                ui._overlays.settings_selection,
                ui._overlays.settings_rows,
                frame,
            )
        )

    monkeypatch.setattr(Screen, "paint", capture_paint)
    outer_rows = (
        SettingsRow("Outer header", kind="header"),
        SettingsRow("Open project trust", kind="action", action="open"),
        SettingsRow("Outer status", kind="status"),
        SettingsRow("Continue outer", kind="action", action="finish"),
    )
    inner_rows = (SettingsRow("Inner trust only", kind="action", action="inner"),)

    with input_path.open(encoding="utf-8") as input_stream:
        input_fd = input_stream.fileno()
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=io.StringIO(),
            cwd=tmp_path,
        )

        def open_project_trust(action: str) -> tuple[SettingsRow, ...]:
            assert action == "open"
            assert (
                ui.run_settings_dialog(
                    inner_rows,
                    on_local_action=lambda _action: inner_rows,
                    title="Inner project trust",
                    overlay_kind="project_trust",
                )
                is None
            )
            assert ui._overlays.active == "settings"
            assert ui._overlays.settings_rows == outer_rows
            assert ui._overlays.settings_title == "Outer settings"
            assert ui._overlays.settings_selection == 1
            assert ui._driver._raw_mode_depth == 1
            assert restore_calls == []
            return outer_rows

        chosen = ui.run_settings_dialog(
            outer_rows,
            on_local_action=open_project_trust,
            exit_actions=frozenset({"finish"}),
            current_index=1,
            title="Outer settings",
        )

        assert chosen == "finish"
        assert ui._overlays.active is None
        assert ui._overlays.settings_rows == ()
        assert ui._overlays.settings_title == "Settings"
        assert ui._overlays.settings_selection == 0
        assert ui._driver._raw_mode_depth == 0

    assert restore_calls == [(input_fd, termios.TCSADRAIN, "saved")]
    assert any(
        active == "project_trust"
        and title == "Inner project trust"
        and rows == inner_rows
        and "Inner trust only" in frame
        for active, title, _selection, rows, frame in paints
    )
    assert any(
        active == "settings"
        and title == "Outer settings"
        and selection == 1
        and rows == outer_rows
        and "Open project trust" in frame
        for active, title, selection, rows, frame in paints
    )
    assert any(
        active == "settings" and selection == 3 and "Continue outer" in frame
        for active, _title, selection, _rows, frame in paints
    )
    assert paints[-1][0] is None


def test_external_io_scope_pairs_nested_exception_and_repaints_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    raw_calls: list[int] = []
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda fd: raw_calls.append(fd))
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )

    with input_path.open(encoding="utf-8") as input_stream:
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=io.StringIO(),
            cwd=tmp_path,
        )
        paints: list[None] = []
        monkeypatch.setattr(Screen, "paint", lambda _self: paints.append(None))
        ui._driver.enter_raw_mode()
        ui._driver.enter_raw_mode()

        with pytest.raises(ValueError, match="foreign failure"):
            with ui.external_io_suspension():
                assert ui._driver._terminal_mode_suspend_depth == 1
                with ui.external_io_suspension():
                    assert ui._driver._terminal_mode_suspend_depth == 2
                    raise ValueError("foreign failure")

        assert ui._driver._raw_mode_depth == 2
        assert ui._driver._terminal_mode_suspend_depth == 0
        assert raw_calls == [input_stream.fileno(), input_stream.fileno()]
        assert restore_calls == [(input_stream.fileno(), termios.TCSADRAIN, "saved")]
        assert paints == [None]
        ui._driver.force_restore_terminal_mode()


def test_tui_close_forces_unbalanced_raw_mode_restoration_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )

    with input_path.open(encoding="utf-8") as input_stream:
        input_fd = input_stream.fileno()
        terminal = io.StringIO()
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=terminal,
            cwd=tmp_path,
        )
        ui._driver.enter_raw_mode()
        ui._driver.enter_raw_mode()

        ui.close()
        first_close = terminal.getvalue()
        ui.close()

        assert ui._driver._raw_mode_depth == 0
        assert ui._driver._old_termios is None

    assert restore_calls == [(input_fd, termios.TCSADRAIN, "saved")]
    assert first_close.count("\x1b[?2004h") == 1
    assert first_close.count("\x1b[?2004l") == 1
    assert terminal.getvalue() == first_close


def test_tui_close_forces_suspended_raw_owners_safe_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )

    with input_path.open(encoding="utf-8") as input_stream:
        input_fd = input_stream.fileno()
        terminal = io.StringIO()
        ui = ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=terminal,
            cwd=tmp_path,
        )
        ui._driver.enter_raw_mode()
        ui._driver.enter_raw_mode()
        ui._driver.suspend_terminal_mode()

        assert ui._driver._raw_mode_depth == 2
        assert ui._driver._terminal_mode_suspend_depth == 1
        assert restore_calls == [(input_fd, termios.TCSADRAIN, "saved")]

        ui.close()
        first_close = terminal.getvalue()
        ui.close()

        assert ui._driver._raw_mode_depth == 0
        assert ui._driver._terminal_mode_suspend_depth == 0
        assert ui._driver._old_termios is None

    # The suspend transition and one forced recovery each restore saved attrs;
    # repeated close emits no further terminal transition or teardown frame.
    assert restore_calls == [
        (input_fd, termios.TCSADRAIN, "saved"),
        (input_fd, termios.TCSADRAIN, "saved"),
    ]
    assert first_close.count("\x1b[?2004h") == 1
    assert first_close.count("\x1b[?2004l") == 1
    assert terminal.getvalue() == first_close


def test_single_row_navigation_is_handled_for_repaint_parity() -> None:
    state = OverlayState()
    assert state.begin_scoped((ScopedModelRow("only"),), checked=())
    assert state.navigate_scoped(1)
    assert state.scoped_selection == 0

    state.end_scoped()
    assert state.begin_settings(
        (SettingsRow("only", kind="action", action="only"),),
        current_index=None,
        title="Settings",
    )
    assert state.navigate_settings(-1)
    assert state.settings_selection == 0


def test_scoped_clear_and_end_detach_previous_checked_sets() -> None:
    state = OverlayState()
    assert state.begin_scoped((ScopedModelRow("only"),), checked=(0,))
    initial_checked = state.scoped_checked

    state.clear_scoped()

    assert initial_checked == {0}
    assert state.scoped_checked == set()
    assert state.scoped_checked is not initial_checked

    assert state.toggle_scoped()
    checked_after_clear = state.scoped_checked
    state.end_scoped()

    assert checked_after_clear == {0}
    assert state.scoped_checked == set()
    assert state.scoped_checked is not checked_after_clear


def test_single_row_facade_navigation_repaints_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    paints: list[None] = []
    monkeypatch.setattr(Screen, "paint", lambda _self: paints.append(None))

    scoped = ScopedModelsSelectorComponent(
        ui._overlays, ui._screen.paint_lock, ui._screen.paint
    )
    assert ui._overlays.begin_scoped((ScopedModelRow("only"),), checked=())
    assert scoped.handle_key("down") is None
    assert len(paints) == 1

    ui._overlays.end_scoped()
    dialog = SettingsDialogComponent(
        ui._overlays,
        ui._screen.paint_lock,
        ui._screen.paint,
        on_local_action=lambda _action: (),
    )
    assert ui._overlays.begin_settings(
        (SettingsRow("only", kind="action", action="only"),),
        current_index=None,
        title="Settings",
    )
    assert dialog.handle_key("up") is None
    assert len(paints) == 2


def test_custom_transition_initializes_and_activates_once() -> None:
    state = OverlayState(custom_render_width=90, custom_hidden=True)
    component = object()

    state.begin_custom(component, render_width=31)

    assert state.active == "custom"
    assert state.custom_component is component
    assert state.custom_render_width == 31
    assert state.custom_done is False
    assert state.custom_result is None
    assert state.custom_hidden is False
    assert state.custom_focused is True


def test_chrome_clear_retires_generation_hooks_but_retains_sticky_values() -> None:
    state = ExtensionChromeState()
    region = ChromeRegion(["line"], None, ("line",), 80, False)
    state.header = region
    state.footer_factory = object()
    state.footer_branch = "old"
    state.statuses["old"] = "status"
    state.working_message = "old working"
    state.working_visible = False
    state.title = "old title"
    state.indicator_frames = (".", "o")
    listener_generation, listener_id = state.register_terminal_input_listener(
        lambda key: key
    )
    callback_generation, callback_id = state.register_footer_branch_callback(
        lambda: None
    )
    state.footer_branch_rebuild_slots = (callback_id,)
    state.footer_branch_rebuild_index = 1
    state.footer_branch_rebuild_active_ids = frozenset({callback_id})
    state.footer_branch_rebuild_new_slots = [callback_id]
    state.footer_branch_rebuild_fire_ids = [callback_id]

    detached = state.regions_for_clear()
    assert detached == (region,)
    assert state.generation == 0

    state.retire_generation()

    assert state.generation == 1
    assert state.header is None
    assert state.footer_factory is None
    assert state.footer_branch is None
    assert state.statuses == {"old": "status"}
    assert state.working_message == "old working"
    assert state.working_visible is False
    assert state.title is None
    assert state.indicator_frames is None
    assert state.terminal_input_listeners == {}
    assert state.footer_branch_callbacks == {}
    assert state.footer_branch_rebuild_slots is None
    assert state.footer_branch_rebuild_index == 0
    assert state.footer_branch_rebuild_active_ids == frozenset()
    assert state.footer_branch_rebuild_new_slots == []
    assert state.footer_branch_rebuild_fire_ids == []

    # Force numeric-id reuse, then prove old-generation removal identities
    # cannot delete fresh registrations from the current generation.
    state.terminal_input_next_id = listener_id
    state.footer_branch_callback_next_id = callback_id
    _fresh_listener_generation, fresh_listener_id = (
        state.register_terminal_input_listener(lambda key: f"fresh:{key}")
    )
    _fresh_callback_generation, fresh_callback_id = (
        state.register_footer_branch_callback(lambda: "fresh")
    )
    assert fresh_listener_id == listener_id
    assert fresh_callback_id == callback_id

    state.remove_terminal_input_listener(listener_generation, listener_id)
    state.remove_footer_branch_callback(callback_generation, callback_id)

    assert fresh_listener_id in state.terminal_input_listeners
    assert fresh_callback_id in state.footer_branch_callbacks


def test_chrome_record_projections_are_deleted_from_the_facade(tmp_path: Path) -> None:
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )

    assert not {
        "extension_status",
        "extension_widgets_above",
        "extension_widgets_below",
        "extension_header",
        "extension_footer",
        "extension_title",
        "_footer_branch_callbacks",
        "_extension_terminal_input_listeners",
    } & set(dir(ui))


def test_facade_overlay_end_detaches_assignable_live_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    monkeypatch.setattr(Screen, "paint", lambda _self: None)
    checked = {0}
    project = [_session(tmp_path / "project.jsonl")]
    all_sessions = [*project, _session(tmp_path / "other.jsonl")]
    scoped = ScopedModelsSelectorComponent(
        ui._overlays, ui._screen.paint_lock, ui._screen.paint
    )
    picker = SessionPickerComponent(
        ui._overlays,
        ui._screen.paint_lock,
        ui._screen.paint,
        on_rename=None,
        on_delete=None,
        consume_paste=lambda: None,
    )
    ui._overlays.scoped_checked = checked
    ui._overlays.session_project = project
    ui._overlays.session_all = all_sessions

    assert scoped.handle_key("esc") is not None
    assert picker.handle_key("esc") is not None

    assert checked == {0}
    assert ui._overlays.scoped_checked == set()
    assert ui._overlays.scoped_checked is not checked
    assert project == [_session(tmp_path / "project.jsonl")]
    assert ui._overlays.session_project == []
    assert ui._overlays.session_project is not project
    assert all_sessions == [
        _session(tmp_path / "project.jsonl"),
        _session(tmp_path / "other.jsonl"),
    ]
    assert ui._overlays.session_all == []
    assert ui._overlays.session_all is not all_sessions


def _assert_footer_rebuild_reset(state: ExtensionChromeState) -> None:
    assert state.footer_branch_rebuild_slots is None
    assert state.footer_branch_rebuild_index == 0
    assert state.footer_branch_rebuild_active_ids == frozenset()
    assert state.footer_branch_rebuild_new_slots == []
    assert state.footer_branch_rebuild_fire_ids == []


def test_footer_rebuild_preserves_slots_and_returns_only_live_callbacks() -> None:
    state = ExtensionChromeState()
    fired: list[str] = []
    _generation, first_id = state.register_footer_branch_callback(
        lambda: fired.append("old")
    )
    assert state.footer_branch_slots == (first_id,)

    state.begin_footer_rebuild("next")
    state.register_footer_branch_callback(lambda: fired.append("new"))
    callbacks = state.finish_footer_rebuild()

    assert len(callbacks) == 1
    callbacks[0]()
    assert fired == ["new"]
    assert state.footer_branch_slots == (first_id,)
    _assert_footer_rebuild_reset(state)

    # The facade aborts unconditionally in its finally block after success.
    state.abort_footer_rebuild()
    assert state.footer_branch_slots == (first_id,)
    _assert_footer_rebuild_reset(state)


def test_footer_rebuild_abort_resets_without_publishing_partial_slots() -> None:
    state = ExtensionChromeState()
    _generation, first_id = state.register_footer_branch_callback(lambda: None)
    state.begin_footer_rebuild("next")
    state.register_footer_branch_callback(lambda: None)
    state.register_footer_branch_callback(lambda: None)

    state.abort_footer_rebuild()

    assert state.footer_branch_slots == (first_id,)
    _assert_footer_rebuild_reset(state)


def test_terminal_facade_stores_extension_owners_in_one_composition_handle(
    tmp_path: Path,
) -> None:
    names = {item.name for item in fields(ToolLoopTerminalUi)}
    assert {
        "input_editor",
        "_overlays",
        "_chrome",
        "pending_messages",
        "clipboard_images",
    } <= names
    assert not names & {
        "_extension_chrome",
        "_extension_footer",
        "_terminal_input_listeners",
        "_extension_generation",
        "model_selector_open",
        "settings_dialog_rows",
        "tree_selector_rows",
        "scoped_models_checked",
        "session_picker_rows",
        "custom_overlay_open",
        "extension_header",
        "extension_footer",
        "extension_status",
        "extension_widgets_above",
        "extension_title",
        "clipboard_image_read",
        "clipboard_temp_dir",
        "_clipboard_image_count",
    }

    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    assert ui.input_editor._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    assert ui.pending_messages._editor is ui.input_editor.editor_state  # noqa: SLF001
    assert ui.pending_messages._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    assert ui.clipboard_images._editor is ui.input_editor.editor_state  # noqa: SLF001
    assert ui.clipboard_images._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    owners = ui._chrome
    assert owners.component._record is owners.record  # noqa: SLF001
    assert owners.footer._record is owners.record  # noqa: SLF001
    assert owners.listeners._record is owners.record  # noqa: SLF001
    assert owners.generation._record is owners.record  # noqa: SLF001
    assert owners.component._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    assert owners.footer._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    assert owners.listeners._paint_lock is ui._screen.paint_lock  # noqa: SLF001
    assert owners.generation._paint_lock is ui._screen.paint_lock  # noqa: SLF001
