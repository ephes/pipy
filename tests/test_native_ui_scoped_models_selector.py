"""Unit tests for the ``/scoped-models`` selector component and its renderer."""

from __future__ import annotations

from pipy_harness.native.overlay_state import OverlayState, ScopedModelRow
from pipy_harness.native.ui.components.scoped_models_selector import (
    ScopedModelsClose,
    ScopedModelsSelectorComponent,
    scoped_models_region_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock


class _Harness:
    """A component wired to a plain overlay record and counting repaints."""

    def __init__(self) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.component = ScopedModelsSelectorComponent(
            self.overlays, PaintLock(), self._repaint
        )

    def _repaint(self) -> None:
        self.repaints += 1


def _rows(
    *references: str, unavailable: str | None = None
) -> tuple[ScopedModelRow, ...]:
    return tuple(
        ScopedModelRow(reference, available=reference != unavailable)
        for reference in references
    )


def test_open_activates_overlay_seeds_checked_and_repaints() -> None:
    harness = _Harness()

    assert harness.component.open(
        _rows("p/a", "p/b", "p/c", unavailable="p/b"), checked=(0, 1, 9)
    )

    assert harness.overlays.is_open("scoped_models")
    # Unavailable and out-of-range indices are dropped from the seed set.
    assert harness.overlays.scoped_checked == {0}
    assert harness.repaints == 1


def test_open_refuses_an_empty_pool_without_repainting() -> None:
    harness = _Harness()

    assert not harness.component.open((), checked=())

    assert not harness.overlays.is_open("scoped_models")
    assert harness.repaints == 0


def test_navigation_skips_unavailable_rows_and_repaints() -> None:
    harness = _Harness()
    assert harness.component.open(
        _rows("p/a", "p/b", "p/c", unavailable="p/b"), checked=()
    )

    assert harness.component.handle_key("down") is None
    assert harness.overlays.scoped_selection == 2  # skipped the unavailable row
    assert harness.component.handle_key("down") is None
    assert harness.overlays.scoped_selection == 0  # wrapped
    assert harness.repaints == 3


def test_space_toggles_a_all_and_c_clears() -> None:
    harness = _Harness()
    assert harness.component.open(
        _rows("p/a", "p/b", "p/c", unavailable="p/c"), checked=()
    )

    assert harness.component.handle_key(" ") is None
    assert harness.overlays.scoped_checked == {0}
    assert harness.component.handle_key(" ") is None
    assert harness.overlays.scoped_checked == set()
    assert harness.component.handle_key("a") is None
    assert harness.overlays.scoped_checked == {0, 1}  # available rows only
    assert harness.component.handle_key("c") is None
    assert harness.overlays.scoped_checked == set()
    assert harness.repaints == 5


def test_enter_saves_the_checked_references_and_closes() -> None:
    harness = _Harness()
    assert harness.component.open(_rows("p/a", "p/b"), checked=(1,))

    assert harness.component.handle_key(" ") is None  # check the highlighted row
    assert harness.component.handle_key("enter") == ScopedModelsClose(
        frozenset({"p/a", "p/b"})
    )
    assert not harness.overlays.is_open("scoped_models")
    assert harness.overlays.scoped_rows == ()
    assert harness.overlays.scoped_checked == set()


def test_cancel_keys_close_with_a_none_scope() -> None:
    for key in (None, "esc", "ctrl-c", "ctrl-d"):
        harness = _Harness()
        assert harness.component.open(_rows("p/a"), checked=(0,))

        assert harness.component.handle_key(key) == ScopedModelsClose(None)
        assert not harness.overlays.is_open("scoped_models")
        assert harness.repaints == 2  # open + close


def test_unknown_keys_leave_the_overlay_open() -> None:
    harness = _Harness()
    assert harness.component.open(_rows("p/a"), checked=())

    assert harness.component.handle_key("x") is None
    assert harness.overlays.is_open("scoped_models")
    assert harness.repaints == 1


def test_region_lines_render_boxes_markers_and_footer() -> None:
    overlays = OverlayState()
    assert overlays.begin_scoped(
        _rows("p/a", "p/b", "p/c", unavailable="p/c"), checked=(1,)
    )

    lines = scoped_models_region_lines(
        overlays, width=70, height=24, footer_lines=("left", "right")
    )

    assert "Scoped models" in lines[0].text
    assert lines[0].kind == "selector_title"
    by_text = {line.text.strip(): line.kind for line in lines[1:4]}
    assert by_text["→ [ ] p/a"] == "selector_option_selected"
    assert by_text["[x] p/b"] == "selector_option"
    assert by_text["[ ] p/c  [unavailable]"] == "selector_option_disabled"
    assert [line.text for line in lines[-2:]] == ["left", "right"]
    assert all(line.kind == "footer" for line in lines[-2:])


def test_region_lines_window_centers_selection_and_marks_overflow() -> None:
    overlays = OverlayState()
    assert overlays.begin_scoped(_rows(*(f"p/m-{i}" for i in range(20))), checked=())
    overlays.scoped_selection = 10

    lines = scoped_models_region_lines(
        overlays, width=70, height=10, footer_lines=("", "")
    )

    rows = [line for line in lines if "] p/m-" in line.text]
    assert len(rows) == 6  # height 10 - title - footer pair - scroll indicator
    assert any("→ [ ] p/m-10" in line.text for line in rows)
    scroll = [line for line in lines if line.kind == "slash_menu_scroll"]
    assert len(scroll) == 1
    assert "(11/20)" in scroll[0].text
