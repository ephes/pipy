"""Unit tests for the ``/settings`` dialog component and its renderer."""

from __future__ import annotations

from collections.abc import Sequence

from pipy_harness.native.overlay_state import OverlayState, SettingsRow
from pipy_harness.native.ui.components.settings_dialog import (
    SettingsDialogClose,
    SettingsDialogComponent,
    settings_dialog_region_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock


def _rows() -> tuple[SettingsRow, ...]:
    return (
        SettingsRow(label="Provider / model", kind="header"),
        SettingsRow(label="active: fake/fake", kind="status"),
        SettingsRow(label="change provider/model…", kind="action", action="model"),
        SettingsRow(label="Prompt history", kind="header"),
        SettingsRow(label="history: off — toggle", kind="action", action="toggle"),
        SettingsRow(label="clear history", kind="action", action="clear"),
    )


class _Harness:
    """A component wired to a plain overlay record and counting repaints."""

    def __init__(
        self,
        local_rows: Sequence[SettingsRow] = (),
        exit_actions: frozenset[str] = frozenset(),
    ) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.local_actions: list[str] = []
        self._local_rows = local_rows
        self.component = SettingsDialogComponent(
            self.overlays,
            PaintLock(),
            self._repaint,
            on_local_action=self._on_local_action,
            exit_actions=exit_actions,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _on_local_action(self, action: str) -> Sequence[SettingsRow]:
        self.local_actions.append(action)
        return self._local_rows

    def open(self, current_index: int | None = None) -> bool:
        return self.component.open(
            _rows(), current_index=current_index, title="Settings", kind="settings"
        )


def test_open_activates_overlay_lands_on_first_actionable_row_and_repaints() -> None:
    harness = _Harness()

    assert harness.open()

    assert harness.overlays.active == "settings"
    assert harness.overlays.settings_rows == _rows()
    assert harness.overlays.settings_selection == 2  # skips header + status rows
    assert harness.overlays.settings_title == "Settings"
    assert harness.repaints == 1


def test_open_refuses_an_empty_pool_without_repainting() -> None:
    harness = _Harness()

    assert not harness.component.open(
        (), current_index=None, title="Settings", kind="settings"
    )

    assert harness.overlays.active is None
    assert harness.repaints == 0


def test_navigation_skips_non_actionable_rows_and_wraps() -> None:
    harness = _Harness()
    assert harness.open()
    assert harness.overlays.settings_selection == 2

    assert harness.component.handle_key("down") is None
    # Skips the "Prompt history" header to the toggle action.
    assert harness.overlays.settings_selection == 4
    assert harness.component.handle_key("down") is None
    assert harness.overlays.settings_selection == 5
    assert harness.component.handle_key("down") is None
    # Wraps back to the first actionable row.
    assert harness.overlays.settings_selection == 2
    assert harness.component.handle_key("up") is None
    # Wraps backward to the last actionable row.
    assert harness.overlays.settings_selection == 5
    assert harness.repaints == 5  # open + four moves


def test_cancel_keys_close_with_a_none_action() -> None:
    for key in (None, "esc", "ctrl-c", "ctrl-d"):
        harness = _Harness()
        assert harness.open()

        assert harness.component.handle_key(key) == SettingsDialogClose(None)
        assert harness.overlays.active is None
        assert harness.repaints == 2  # open + close


def test_exit_action_closes_carrying_the_identifier() -> None:
    harness = _Harness(exit_actions=frozenset({"model"}))
    assert harness.open(current_index=2)

    assert harness.component.handle_key("enter") == SettingsDialogClose("model")
    assert harness.overlays.active is None
    assert harness.local_actions == []


def test_local_action_rebuilds_rows_in_place_and_stays_open() -> None:
    rebuilt = (SettingsRow(label="history: on — toggle", kind="action", action="t"),)
    harness = _Harness(local_rows=rebuilt)
    assert harness.open(current_index=4)

    assert harness.component.handle_key(" ") is None

    assert harness.local_actions == ["toggle"]
    assert harness.overlays.active == "settings"
    assert harness.overlays.settings_rows == rebuilt
    assert harness.repaints == 2  # open + in-place re-render


def test_local_action_returning_no_rows_closes_with_a_none_action() -> None:
    harness = _Harness(local_rows=())
    assert harness.open(current_index=4)

    assert harness.component.handle_key("enter") == SettingsDialogClose(None)

    assert harness.local_actions == ["toggle"]
    assert harness.overlays.active is None


def test_enter_on_a_non_actionable_selection_is_a_no_op() -> None:
    harness = _Harness()
    assert harness.open()
    harness.overlays.settings_selection = 1  # a read-only status row

    assert harness.component.handle_key("enter") is None
    assert harness.overlays.active == "settings"

    harness.overlays.settings_selection = len(_rows())  # out of range

    assert harness.component.handle_key("enter") is None
    assert harness.overlays.active == "settings"
    assert harness.local_actions == []


def test_unknown_keys_leave_the_dialog_open() -> None:
    harness = _Harness()
    assert harness.open()

    assert harness.component.handle_key("x") is None
    assert harness.overlays.active == "settings"
    assert harness.repaints == 1


def test_region_lines_render_headers_statuses_and_the_highlighted_action() -> None:
    overlays = OverlayState()
    assert overlays.begin_settings(_rows(), current_index=4, title="Settings")

    lines = settings_dialog_region_lines(
        overlays, width=60, height=24, footer_lines=("left", "right")
    )

    assert "Settings" in lines[0].text
    assert lines[0].kind == "selector_title"
    by_text = {line.text.strip(): line.kind for line in lines[1:7]}
    assert by_text["Provider / model"] == "selector_title"
    assert by_text["active: fake/fake"] == "selector_option_disabled"
    assert by_text["change provider/model…"] == "selector_option"
    assert by_text["→ history: off — toggle"] == "selector_option_selected"
    assert [line.text for line in lines[-2:]] == ["left", "right"]
    assert all(line.kind == "footer" for line in lines[-2:])


def test_region_lines_window_centers_selection_and_marks_overflow() -> None:
    overlays = OverlayState()
    rows = (
        SettingsRow(label="header", kind="header"),
        *(
            SettingsRow(label=f"action {index}", kind="action", action=f"a{index}")
            for index in range(20)
        ),
    )
    assert overlays.begin_settings(rows, current_index=10, title="Long")

    lines = settings_dialog_region_lines(
        overlays, width=60, height=10, footer_lines=("", "")
    )

    assert "Long" in lines[0].text
    rendered = [line for line in lines if line.text.strip().startswith(("action", "→"))]
    assert len(rendered) <= 6  # height 10 - title - footer pair - scroll indicator
    assert any("→ action 9" in line.text for line in rendered)
    scroll = [line for line in lines if line.kind == "slash_menu_scroll"]
    assert len(scroll) == 1
    assert "(11/21)" in scroll[0].text
