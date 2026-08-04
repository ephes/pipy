"""Unit tests for the custom-overlay runner and its overlay renderer."""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.ui.components.custom_overlay import (
    CustomComponentRunner,
    custom_overlay_region_lines,
)


class _Component:
    """A scripted component that finishes with ``key:<key>`` on any key."""

    def __init__(self, done: Callable[..., None]) -> None:
        self._done = done
        self.keys: list[str] = []
        self.disposed = False

    def render(self, width: int) -> list[str]:
        return [f"width={width}"]

    def handle_input(self, key: str) -> None:
        self.keys.append(key)
        self._done(f"key:{key}")

    def dispose(self) -> None:
        self.disposed = True


class _Harness:
    """A runner wired to a plain overlay record and counting repaints."""

    def __init__(self) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.runner = CustomComponentRunner(self.overlays, self._repaint)

    def _repaint(self) -> None:
        self.repaints += 1


def test_runner_routes_keys_and_returns_the_done_result() -> None:
    harness = _Harness()
    components: list[_Component] = []

    def factory(done: Callable[..., None]) -> _Component:
        component = _Component(done)
        components.append(component)
        return component

    harness.runner.create(factory, None)
    harness.runner.begin()

    assert harness.overlays.is_open("custom")
    assert not harness.runner.finished
    assert harness.runner.handle_key("enter") is False
    assert harness.runner.finished
    assert components[0].keys == ["enter"]

    result = harness.runner.dispose()

    assert result == "key:enter"
    assert components[0].disposed is True
    assert not harness.overlays.is_open("custom")
    assert harness.overlays.custom_component is None


def test_done_before_begin_stays_pending_and_flushes_on_begin() -> None:
    harness = _Harness()

    def factory(done: Callable[..., None]) -> _Component:
        component = _Component(done)
        done("early")
        done("late")  # only the first pending result wins
        return component

    harness.runner.create(factory, None)
    harness.runner.begin()

    assert harness.runner.finished
    assert harness.runner.dispose() == "early"


def test_eof_key_cancels_with_none() -> None:
    harness = _Harness()
    harness.runner.create(_Component, None)
    harness.runner.begin()

    assert harness.runner.handle_key(None) is True
    assert harness.runner.finished
    assert harness.runner.dispose() is None


def test_paste_marker_is_ignored_without_reaching_the_component() -> None:
    harness = _Harness()
    components: list[_Component] = []

    def factory(done: Callable[..., None]) -> _Component:
        component = _Component(done)
        components.append(component)
        return component

    harness.runner.create(factory, None)
    harness.runner.begin()

    assert harness.runner.handle_key("paste") is False
    assert components[0].keys == []
    assert not harness.runner.finished


def test_hidden_or_unfocused_overlay_swallows_keys_and_repaints() -> None:
    harness = _Harness()
    components: list[_Component] = []

    def factory(done: Callable[..., None]) -> _Component:
        component = _Component(done)
        components.append(component)
        return component

    harness.runner.create(factory, None)
    harness.runner.begin()
    harness.overlays.custom_hidden = True
    repaints_before = harness.repaints

    assert harness.runner.handle_key("enter") is False
    assert components[0].keys == []
    assert harness.repaints == repaints_before + 1


def test_component_exception_cancels_with_none() -> None:
    harness = _Harness()

    class _Bad:
        def __init__(self, done: Callable[..., None]) -> None:
            del done

        def render(self, width: int) -> list[str]:
            return []

        def handle_input(self, key: str) -> None:
            raise RuntimeError("bad component")

    harness.runner.create(lambda done: _Bad(done), None)
    harness.runner.begin()

    assert harness.runner.handle_key("enter") is True
    assert harness.runner.dispose() is None


def test_dispose_restores_previous_render_width_and_survives_failures() -> None:
    harness = _Harness()
    harness.overlays.custom_render_width = 55

    class _FailingDispose(_Component):
        def dispose(self) -> None:
            raise RuntimeError("dispose failed")

    harness.runner.create(
        lambda done: _FailingDispose(done), {"overlayOptions": {"width": 23}}
    )
    harness.runner.begin()
    assert harness.overlays.custom_render_width == 23
    harness.runner.handle_key("enter")

    def failing_repaint() -> None:
        raise OSError("gone")

    harness.runner._repaint = failing_repaint  # the dispose-repaint seam under test

    assert harness.runner.dispose() == "key:enter"
    assert harness.overlays.custom_render_width == 55


def test_overlay_options_width_parsing_and_handle_notification() -> None:
    harness = _Harness()
    seen: dict[str, object] = {}

    def on_handle(handle: object) -> None:
        seen["handle"] = handle

    harness.runner.create(
        _Component,
        {"overlay_options": lambda: {"width": 31.8}, "onHandle": on_handle},
    )
    harness.runner.begin()

    assert harness.overlays.custom_render_width == 31
    handle = seen["handle"]
    for method in (
        "hide",
        "setHidden",
        "isHidden",
        "focus",
        "unfocus",
        "isFocused",
        "requestRender",
        "request_render",
    ):
        assert callable(getattr(handle, method))


def test_bad_width_hints_degrade_to_the_default() -> None:
    for options in (
        None,
        {"overlayOptions": {"width": True}},
        {"overlayOptions": {"width": 0}},
        {"overlayOptions": {"width": "nope"}},
        {"overlayOptions": {"width": "-3"}},
        {"overlay_options": lambda: (_ for _ in ()).throw(RuntimeError("bad"))},
    ):
        harness = _Harness()
        harness.runner.create(_Component, options)
        harness.runner.begin()
        assert harness.overlays.custom_render_width is None


def test_handle_hide_finishes_the_runner() -> None:
    harness = _Harness()
    seen: dict[str, object] = {}
    harness.runner.create(_Component, {"onHandle": lambda h: seen.update(handle=h)})
    harness.runner.begin()
    handle = seen["handle"]
    handle.hide()  # type: ignore[attr-defined]

    assert harness.runner.finished
    assert harness.runner.dispose() is None


def test_region_lines_render_clip_and_hide() -> None:
    overlays = OverlayState()
    assert custom_overlay_region_lines(overlays, width=40, height=5) == []

    overlays.begin_custom(_Component(lambda _v=None: None), render_width=23)
    lines = custom_overlay_region_lines(overlays, width=40, height=5)
    assert [line.text for line in lines] == ["width=23"]

    overlays.custom_render_width = None
    lines = custom_overlay_region_lines(overlays, width=40, height=5)
    assert [line.text for line in lines] == ["width=40"]

    overlays.custom_hidden = True
    assert custom_overlay_region_lines(overlays, width=40, height=5) == []


def test_region_lines_surface_a_render_error_line() -> None:
    overlays = OverlayState()

    class _BadRender:
        def render(self, width: int) -> list[str]:
            raise RuntimeError("render failed")

    overlays.begin_custom(_BadRender(), render_width=None)
    lines = custom_overlay_region_lines(overlays, width=60, height=5)

    assert [line.text for line in lines] == ["(custom component render error)"]
