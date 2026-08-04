"""Unit tests for the provider/model selector component and its renderer."""

from __future__ import annotations

from pipy_harness.native.overlay_state import ModelSelectorOption, OverlayState
from pipy_harness.native.ui.components.model_selector import (
    ModelSelectorClose,
    ModelSelectorComponent,
    model_selector_region_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock


class _Harness:
    """A component wired to a plain overlay record and counting repaints."""

    def __init__(self) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.component = ModelSelectorComponent(
            self.overlays, PaintLock(), self._repaint
        )

    def _repaint(self) -> None:
        self.repaints += 1


def _options(
    *labels: str, unselectable: str | None = None
) -> tuple[ModelSelectorOption, ...]:
    return tuple(ModelSelectorOption(label, label != unselectable) for label in labels)


def test_open_activates_overlay_clamps_index_and_repaints() -> None:
    harness = _Harness()

    assert harness.component.open(_options("a", "b"), current_index=9, title="Pick one")

    assert harness.overlays.is_open("model")
    assert harness.overlays.model_selection == 1  # clamped to the last row
    assert harness.overlays.model_title == "Pick one"
    assert harness.repaints == 1


def test_open_refuses_an_empty_pool_without_repainting() -> None:
    harness = _Harness()

    assert not harness.component.open((), current_index=0, title=None)

    assert not harness.overlays.is_open("model")
    assert harness.repaints == 0


def test_navigation_wraps_and_repaints() -> None:
    harness = _Harness()
    assert harness.component.open(
        _options("a", "b", "c", unselectable="c"), current_index=0, title=None
    )

    assert harness.component.handle_key("down") is None
    assert harness.overlays.model_selection == 1
    assert harness.component.handle_key("up") is None
    assert harness.component.handle_key("up") is None
    # Wrapping: up from index 0 lands on the last row.
    assert harness.overlays.model_selection == 2
    assert harness.repaints == 4


def test_enter_selects_only_a_selectable_row() -> None:
    harness = _Harness()
    assert harness.component.open(
        _options("a", "b", unselectable="b"), current_index=1, title=None
    )

    # The highlighted row is unselectable: enter is a no-op, the overlay stays.
    assert harness.component.handle_key("enter") is None
    assert harness.overlays.is_open("model")

    assert harness.component.handle_key("up") is None
    assert harness.component.handle_key("enter") == ModelSelectorClose(0)
    assert not harness.overlays.is_open("model")
    assert harness.overlays.model_options == ()


def test_cancel_keys_close_with_a_none_index() -> None:
    for key in (None, "esc", "ctrl-c", "ctrl-d"):
        harness = _Harness()
        assert harness.component.open(_options("a"), current_index=0, title=None)

        assert harness.component.handle_key(key) == ModelSelectorClose(None)
        assert not harness.overlays.is_open("model")
        assert harness.repaints == 2  # open + close


def test_unknown_keys_leave_the_overlay_open() -> None:
    harness = _Harness()
    assert harness.component.open(_options("a"), current_index=0, title=None)

    assert harness.component.handle_key("x") is None
    assert harness.overlays.is_open("model")
    assert harness.repaints == 1


def test_region_lines_highlight_dim_and_window() -> None:
    overlays = OverlayState()
    assert overlays.begin_model(
        _options("alpha", "beta", "gamma", unselectable="gamma"),
        current_index=1,
        title=None,
    )

    lines = model_selector_region_lines(
        overlays, width=60, height=24, footer_lines=("left", "right")
    )

    assert "Select provider/model" in lines[0].text
    assert lines[0].kind == "selector_title"
    by_text = {line.text.strip(): line.kind for line in lines[1:4]}
    assert by_text["alpha"] == "selector_option"
    assert by_text["→ beta"] == "selector_option_selected"
    assert by_text["gamma"] == "selector_option_disabled"
    assert [line.text for line in lines[-2:]] == ["left", "right"]
    assert all(line.kind == "footer" for line in lines[-2:])


def test_region_lines_window_centers_selection_and_marks_overflow() -> None:
    overlays = OverlayState()
    assert overlays.begin_model(
        _options(*(f"model-{i}" for i in range(20))), current_index=10, title="Rows"
    )

    lines = model_selector_region_lines(
        overlays, width=60, height=10, footer_lines=("", "")
    )

    assert "Rows" in lines[0].text
    rows = [line for line in lines if line.text.strip().startswith(("model", "→"))]
    assert len(rows) == 6  # height 10 - title - footer pair - scroll indicator
    assert any("→ model-10" in line.text for line in rows)
    scroll = [line for line in lines if line.kind == "slash_menu_scroll"]
    assert len(scroll) == 1
    assert "(11/20)" in scroll[0].text
