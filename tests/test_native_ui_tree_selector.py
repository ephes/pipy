"""Unit tests for the ``/tree`` selector component and its overlay renderer."""

from __future__ import annotations

from collections.abc import Sequence

from pipy_harness.native.overlay_state import OverlayState, TreeSelectorRow
from pipy_harness.native.ui.components.tree_selector import (
    TreeSelectorClose,
    TreeSelectorComponent,
    tree_selector_region_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock

_FILTER_MODES = ("default", "all", "labeled")


class _Harness:
    """A component wired to a plain overlay record and counting repaints."""

    def __init__(self, rows_by_mode: dict[str, Sequence[TreeSelectorRow]]) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.built_modes: list[str] = []
        self.toggled: list[str] = []
        self._rows_by_mode = rows_by_mode
        self.component = TreeSelectorComponent(
            self.overlays,
            PaintLock(),
            self._repaint,
            build_rows=self._build_rows,
            filter_modes=_FILTER_MODES,
            on_label_toggle=self.toggled.append,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _build_rows(self, mode: str) -> Sequence[TreeSelectorRow]:
        self.built_modes.append(mode)
        return self._rows_by_mode.get(mode, ())


def _rows(*entry_ids: str, active: str | None = None) -> tuple[TreeSelectorRow, ...]:
    return tuple(
        TreeSelectorRow(entry_id, f"label-{entry_id}", active=entry_id == active)
        for entry_id in entry_ids
    )


def test_open_activates_overlay_and_coerces_unknown_filter() -> None:
    harness = _Harness({"default": _rows("a", "b", active="b")})

    harness.component.open("bogus")

    assert harness.built_modes == ["default"]
    assert harness.overlays.is_open("tree")
    assert harness.overlays.tree_filter == "default"
    assert harness.overlays.tree_rows == _rows("a", "b", active="b")
    assert harness.overlays.tree_selection == 1  # last active row
    assert harness.repaints == 1


def test_navigation_wraps_and_repaints() -> None:
    harness = _Harness({"default": _rows("a", "b", "c")})
    harness.component.open("default")

    assert harness.component.handle_key("down") is None
    assert harness.overlays.tree_selection == 0  # wrapped past the end
    assert harness.component.handle_key("up") is None
    assert harness.overlays.tree_selection == 2
    assert harness.repaints == 3


def test_ctrl_o_cycles_filter_and_rebuilds_rows() -> None:
    harness = _Harness(
        {
            "default": _rows("a", "b"),
            "all": _rows("a", "b", "c", active="a"),
        }
    )
    harness.component.open("default")

    assert harness.component.handle_key("ctrl-o") is None

    assert harness.overlays.tree_filter == "all"
    assert harness.built_modes == ["default", "all"]
    assert harness.overlays.tree_rows == _rows("a", "b", "c", active="a")
    assert harness.overlays.tree_selection == 0  # reset to the active row


def test_label_toggle_refreshes_rows_for_current_filter() -> None:
    harness = _Harness({"all": _rows("a", "b")})
    harness.component.open("all")
    harness.overlays.tree_selection = 1

    assert harness.component.handle_key("L") is None

    assert harness.toggled == ["b"]
    assert harness.built_modes == ["all", "all"]
    assert harness.overlays.tree_selection == 1  # no reset on label toggle


def test_label_toggle_out_of_range_is_a_no_op() -> None:
    harness = _Harness({"default": ()})
    harness.component.open("default")
    repaints = harness.repaints

    assert harness.component.handle_key("L") is None

    assert harness.toggled == []
    assert harness.built_modes == ["default"]
    assert harness.repaints == repaints


def test_enter_selects_the_highlighted_entry_and_closes() -> None:
    harness = _Harness({"default": _rows("a", "b", active="b")})
    harness.component.open("default")

    closed = harness.component.handle_key("enter")

    assert closed == TreeSelectorClose("b")
    assert not harness.overlays.is_open("tree")
    assert harness.overlays.tree_rows == ()


def test_enter_with_empty_rows_keeps_the_overlay_open() -> None:
    harness = _Harness({"default": ()})
    harness.component.open("default")

    assert harness.component.handle_key("enter") is None
    assert harness.overlays.is_open("tree")


def test_cancel_keys_and_eof_close_without_a_choice() -> None:
    for key in (None, "esc", "ctrl-c", "ctrl-d"):
        harness = _Harness({"default": _rows("a")})
        harness.component.open("default")

        closed = harness.component.handle_key(key)

        assert closed == TreeSelectorClose(None)
        assert not harness.overlays.is_open("tree")


def test_unknown_keys_are_ignored() -> None:
    harness = _Harness({"default": _rows("a")})
    harness.component.open("default")
    repaints = harness.repaints

    assert harness.component.handle_key("x") is None
    assert harness.overlays.is_open("tree")
    assert harness.repaints == repaints


def test_region_lines_render_empty_tree_with_title_and_footer() -> None:
    overlays = OverlayState()
    overlays.begin_tree((), filter_mode="all")

    lines = tree_selector_region_lines(
        overlays, width=80, height=10, footer_lines=("left", "right")
    )

    assert lines[0].kind == "selector_title"
    assert "^O filter (all)" in lines[0].text
    assert lines[1].text == "  (empty session tree)"
    assert [line.kind for line in lines[-2:]] == ["footer", "footer"]
    assert [line.text for line in lines[-2:]] == ["left", "right"]


def test_region_lines_mark_selection_and_active_rows() -> None:
    overlays = OverlayState()
    overlays.begin_tree(_rows("a", "b", active="a"), filter_mode="default")
    overlays.tree_selection = 1

    lines = tree_selector_region_lines(
        overlays, width=80, height=10, footer_lines=("", "")
    )

    assert lines[1].text == "  * label-a"
    assert lines[1].kind == "selector_option"
    assert lines[2].text == "→   label-b"
    assert lines[2].kind == "selector_option_selected"


def test_region_lines_window_overflow_and_report_position() -> None:
    overlays = OverlayState()
    overlays.begin_tree(
        _rows(*[str(index) for index in range(20)]), filter_mode="default"
    )
    overlays.tree_selection = 10

    lines = tree_selector_region_lines(
        overlays, width=80, height=9, footer_lines=("", "")
    )

    option_lines = [line for line in lines if line.kind.startswith("selector_option")]
    assert len(option_lines) == 5  # height 9 - title/footer chrome = 5 rows
    assert any(line.kind == "selector_option_selected" for line in option_lines)
    scroll = [line for line in lines if line.kind == "slash_menu_scroll"]
    assert [line.text for line in scroll] == ["  (11/20)"]
