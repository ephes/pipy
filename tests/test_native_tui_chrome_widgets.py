import io
import threading

import pytest

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeSnapshot,
)
from pipy_harness.native.tui import (
    ToolLoopTerminalUi,
    _LiveExtensionUiDriver,
    _TuiToolLoopRenderer,
)
from pathlib import Path


def _ui():
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=Path("."),
    )


def test_set_widget_stores_snapshot_and_clears():
    ui = _ui()
    ui.set_extension_widget("k", ["a", "b"], placement="above_editor")
    region = ui.extension_widgets_above["k"]
    assert isinstance(region, ChromeRegion)
    assert region.snapshot == ("a", "b")
    ui.set_extension_widget("k", None)
    assert "k" not in ui.extension_widgets_above


def test_widget_insertion_order_preserved():
    ui = _ui()
    ui.set_extension_widget("z", ["z"])
    ui.set_extension_widget("a", ["a"])
    assert list(ui.extension_widgets_above.keys()) == ["z", "a"]


def test_widget_factory_renders_at_width():
    ui = _ui()

    class _Comp:
        def render(self, width):
            return [f"w={width}"]

    ui.set_extension_widget("k", lambda theme: _Comp())
    assert ui.extension_widgets_above["k"].snapshot[0].startswith("w=")


def test_header_failsoft_drops_on_bad_factory():
    ui = _ui()

    def boom(theme):
        raise RuntimeError("x")

    ui.set_extension_header(boom)
    assert ui.extension_header is None  # fell back to built-in


def test_footer_replace_and_restore():
    ui = _ui()
    ui.set_extension_footer(
        lambda theme, footer_data: type("C", (), {"render": lambda self, w: ["f"]})()
    )
    assert ui.extension_footer is not None
    ui.set_extension_footer(None)
    assert ui.extension_footer is None


def test_widget_bounds_truncate():
    ui = _ui()
    ui.set_extension_widget("k", [f"l{i}" for i in range(50)])
    assert len(ui.extension_widgets_above["k"].snapshot) <= 11  # 10 + marker


def test_dispose_called_on_replace_and_clear():
    ui = _ui()
    disposed = []

    class _Comp:
        def render(self, width):
            return ["x"]

        def dispose(self):
            disposed.append(True)

    ui.set_extension_widget("k", lambda theme: _Comp())
    ui.set_extension_widget("k", ["plain"])  # replace -> dispose old
    ui.set_extension_widget("k", None)  # clear
    assert disposed == [True]


def test_widget_move_to_full_placement_keeps_original():
    ui = _ui()
    for i in range(16):  # fill above to _WIDGET_MAX_COUNT
        ui.set_extension_widget(f"a{i}", [f"a{i}"], placement="above_editor")
    ui.set_extension_widget("m", ["m"], placement="below_editor")
    # move "m" to the full "above" placement -> rejected, stays in "below"
    ui.set_extension_widget("m", ["m2"], placement="above_editor")
    assert "m" in ui.extension_widgets_below
    assert "m" not in ui.extension_widgets_above


def test_clear_extension_chrome_retires_generation_state_and_keeps_sticky_values():
    ui = _ui()
    ui.set_extension_widget("k", ["a"])
    ui.set_extension_header(
        lambda theme: type("C", (), {"render": lambda self, w: ["h"]})()
    )
    ui.set_extension_footer(lambda theme, data: ["f"])
    ui.set_extension_title("t")
    ui.set_extension_status("status", "value")
    ui.set_extension_working_message("sticky")
    ui.set_extension_working_visible(False)
    ui.add_extension_terminal_input_listener(lambda key: None)
    ui.add_extension_autocomplete_provider(lambda base: base)
    ui.set_editor_component(lambda *_args: object())
    ui.set_extension_hidden_thinking_label("Folded")
    ui.clear_extension_chrome()
    assert ui.extension_widgets_above == {}
    assert ui.extension_header is None
    assert ui.extension_footer is None
    assert ui._extension_footer_factory is None
    assert ui._extension_footer_branch is None
    assert ui.extension_title is None
    assert ui._extension_terminal_input_listeners == {}
    assert ui._autocomplete_provider_factories == []
    assert ui.get_editor_component() is None
    assert ui.hidden_thinking_label == "Thinking..."
    assert ui.extension_status == {"status": "value"}
    assert ui.extension_working_message == "sticky"
    assert ui.extension_working_visible is False


def test_clear_without_custom_editor_preserves_text_cursor_and_undo_state():
    ui = _ui()
    ui._editor.set_buffer("draft text", cursor=3)
    ui._editor.undo_stack[:] = [("older", 2)]
    ui._editor.redo_stack[:] = [("newer", 4)]
    ui._editor.pending_initial_text = "pending draft"
    before = (
        ui._editor.text,
        ui._editor.cursor,
        list(ui._editor.undo_stack),
        list(ui._editor.redo_stack),
        ui._editor.pending_initial_text,
    )

    ui.clear_extension_chrome()

    assert (
        ui._editor.text,
        ui._editor.cursor,
        ui._editor.undo_stack,
        ui._editor.redo_stack,
        ui._editor.pending_initial_text,
    ) == before


def test_reconcile_clears_active_custom_editor_and_round_trips_its_text():
    ui = _ui()
    disposed: list[str] = []

    class _Editor:
        text = ""

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

        def dispose(self):
            disposed.append(self.text)

    component = _Editor()
    ui.set_input_text("built-in draft")
    ui.set_editor_component(lambda *_args: component)
    component.text = "custom draft"

    ui.reconcile_extension_chrome(ExtensionChromeSnapshot())

    assert disposed == ["custom draft"]
    assert ui.get_editor_component() is None
    assert ui.get_input_text() == "custom draft"
    assert ui.hidden_thinking_label == "Thinking..."


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_clear_propagates_custom_editor_text_interrupt_before_detach(interrupt_type):
    ui = _ui()

    class _Editor:
        def get_text(self):
            raise interrupt_type()

        def set_text(self, _text):
            return None

        def render(self, _width):
            return ["editor"]

    factory = lambda *_args: _Editor()  # noqa: E731
    ui.set_editor_component(factory)
    ui.set_extension_widget("kept", ["kept"])
    generation = ui._chrome.generation

    with pytest.raises(interrupt_type):
        ui.clear_extension_chrome()

    assert ui._chrome.generation == generation
    assert ui.get_editor_component() is factory
    assert "kept" in ui.extension_widgets_above


def test_clear_serializes_title_restore_and_paint_but_unlocks_callbacks(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
):
    ui = _ui()
    lock_observations: list[tuple[str, bool]] = []

    def observe_lock(name: str) -> None:
        acquired: list[bool] = []

        def probe() -> None:
            available = ui._paint_lock.acquire(timeout=1.0)
            acquired.append(available)
            if available:
                ui._paint_lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        lock_observations.append((name, acquired == [True]))

    class _Region:
        def render(self, _width):
            return ["region"]

        def dispose(self):
            observe_lock("region-dispose")

    class _Editor:
        def get_text(self):
            observe_lock("editor-get-text")
            return "safe"

        def set_text(self, _text):
            return None

        def render(self, _width):
            return ["editor"]

        def dispose(self):
            observe_lock("editor-dispose")

    ui.set_extension_widget("region", lambda _theme: _Region())
    ui.set_editor_component(lambda *_args: _Editor())
    ui.set_extension_title("extension")
    driver_type = type(ui._driver)
    original_restore = driver_type.restore_title

    def restore_title(driver):
        observe_lock("restore-title")
        original_restore(driver)

    monkeypatch.setattr(driver_type, "restore_title", restore_title)
    ui_type = type(ui)
    original_paint_locked = ui_type._paint_locked

    def paint_locked(instance):
        if instance is ui:
            observe_lock("paint")
        original_paint_locked(instance)

    monkeypatch.setattr(ui_type, "_paint_locked", paint_locked)

    ui.clear_extension_chrome()

    assert lock_observations == [
        ("editor-get-text", True),
        ("region-dispose", True),
        ("editor-dispose", True),
        ("restore-title", False),
        ("paint", False),
    ]


def test_clear_discards_chrome_and_listeners_registered_during_dispose():
    ui = _ui()
    dispose_registered: list[object] = []
    old_listener_ids: list[int] = []

    class _Comp:
        def render(self, width):
            return ["original"]

        def dispose(self):
            ui.set_extension_widget("registered-during-dispose", ["late"])
            dispose_registered.append(
                ui.add_extension_terminal_input_listener(lambda key: f"late:{key}")
            )
            old_listener_ids.extend(ui._extension_terminal_input_listeners)

    ui.set_extension_widget("original", lambda theme: _Comp())
    ui.clear_extension_chrome()

    assert ui._chrome.generation == 1
    assert ui.extension_widgets_above == {}
    assert ui._extension_terminal_input_listeners == {}

    # A disposer created during the old generation stays stale even if its
    # numeric id is reused by a fresh registration.
    ui._extension_terminal_input_next_id = old_listener_ids[0]
    ui.add_extension_terminal_input_listener(lambda key: {"data": f"fresh:{key}"})
    stale_dispose = dispose_registered[0]
    assert callable(stale_dispose)
    stale_dispose()
    assert ui._apply_extension_terminal_input_listeners("x") == "fresh:x"


def test_driver_acceptance_drops_retiring_disposal_writes_and_replays_live_races():  # noqa: C901
    ui = _ui()
    driver = _LiveExtensionUiDriver(ui, Path("."))
    dispose_entered = threading.Event()
    dispose_release = threading.Event()
    paint_guard_was_free: list[bool] = []
    retiring_seen: list[str] = []
    candidate_seen: list[str] = []

    def retiring_listener(key: str) -> str:
        retiring_seen.append(key)
        return f"retiring:{key}"

    def retiring_header(_theme):
        return ["RETIRING_HEADER"]

    def retiring_editor(*_args):
        return object()

    def retiring_autocomplete(base):
        return base

    class _OldComponent:
        def render(self, _width):
            return ["old"]

        def dispose(self):
            driver.set_title("RETIRING_TITLE")
            driver.set_header(retiring_header)
            driver.set_editor_component(retiring_editor)
            driver.add_terminal_input_listener(retiring_listener)
            driver.add_autocomplete_provider(retiring_autocomplete)

            def probe_paint_guard() -> None:
                acquired = ui._paint_lock.acquire(timeout=1.0)
                paint_guard_was_free.append(acquired)
                if acquired:
                    ui._paint_lock.release()

            probe = threading.Thread(target=probe_paint_guard)
            probe.start()
            probe.join(timeout=2.0)
            dispose_entered.set()
            assert dispose_release.wait(timeout=2.0)
            raise RuntimeError("injected retiring disposal failure")

    driver.set_widget("old", lambda _theme: _OldComponent(), "above_editor")

    class _HeaderComponent:
        def render(self, _width):
            return ["candidate"]

    candidate_header = lambda _theme: _HeaderComponent()  # noqa: E731

    class _EditorComponent:
        text = ""

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

        def render(self, _width):
            return [self.text]

    candidate_editor = lambda *_args: _EditorComponent()  # noqa: E731

    def candidate_listener(key: str):
        candidate_seen.append(key)
        return {"data": f"candidate:{key}"}

    def candidate_autocomplete(base):
        return base

    candidate = driver.new_candidate_sink()
    candidate_driver = driver.candidate_driver(candidate)
    candidate_driver.set_title("CANDIDATE_TITLE")
    candidate_driver.set_header(candidate_header)
    candidate_driver.set_editor_component(candidate_editor)
    candidate_driver.add_terminal_input_listener(candidate_listener)
    candidate_driver.add_autocomplete_provider(candidate_autocomplete)

    results = []
    accept_thread = threading.Thread(
        target=lambda: results.append(driver.accept_candidate(candidate))
    )
    accept_thread.start()
    assert dispose_entered.wait(timeout=2.0)

    # A candidate write racing its snapshot stays candidate-owned, while an
    # unrelated retained write in another context follows the handoff queue.
    candidate_driver.set_title("CANDIDATE_RACED_TITLE")
    retained_writer = threading.Thread(
        target=lambda: driver.set_hidden_thinking_label("retained-race")
    )
    retained_writer.start()
    retained_writer.join(timeout=2.0)
    assert not retained_writer.is_alive()

    dispose_release.set()
    accept_thread.join(timeout=2.0)
    assert not accept_thread.is_alive()
    assert len(results) == 1 and results[0].accepted
    assert results[0].retired_sink is not None
    assert driver.dispose_retired_sink(results[0].retired_sink) is None

    snapshot = candidate.snapshot()
    assert paint_guard_was_free == [True]
    assert snapshot.title == "CANDIDATE_RACED_TITLE"
    assert snapshot.header is candidate_header
    assert snapshot.editor_component is candidate_editor
    assert snapshot.autocomplete_providers == (candidate_autocomplete,)
    assert [handler for _listener_id, handler in snapshot.terminal_input_listeners] == [
        candidate_listener
    ]
    assert snapshot.hidden_thinking_label == "retained-race"
    assert ui.extension_title == snapshot.title
    assert ui.extension_header is not None
    assert ui.extension_header.source is snapshot.header
    assert ui.get_editor_component() is snapshot.editor_component
    assert ui._autocomplete_provider_factories == [candidate_autocomplete]
    assert ui.hidden_thinking_label == snapshot.hidden_thinking_label
    assert ui._apply_extension_terminal_input_listeners("x") == "candidate:x"
    assert candidate_seen == ["x"]
    assert retiring_seen == []

    # The exception path reset the scoped lease; later live writes still reach
    # the accepted sink rather than the retirement drop sink.
    driver.set_title("POST_ACCEPT_TITLE")
    assert candidate.snapshot().title == "POST_ACCEPT_TITLE"
    assert ui.extension_title == "POST_ACCEPT_TITLE"


def test_clear_retires_state_even_when_dispose_propagates_interrupt():
    ui = _ui()

    class _Comp:
        def render(self, width):
            return ["original"]

        def dispose(self):
            raise KeyboardInterrupt

    ui.set_extension_widget("original", lambda theme: _Comp())

    with pytest.raises(KeyboardInterrupt):
        ui.clear_extension_chrome()

    assert ui._chrome.generation == 1
    assert ui.extension_widgets_above == {}


def test_stale_facade_disposers_cannot_remove_reused_fresh_registrations():
    ui = _ui()
    old_listener_dispose = ui.add_extension_terminal_input_listener(lambda key: key)
    old_callback_dispose = ui.register_footer_branch_change_callback(lambda: "old")
    old_listener_id = next(iter(ui._extension_terminal_input_listeners))
    old_callback_id = next(iter(ui._footer_branch_callbacks))

    ui.clear_extension_chrome()
    ui._extension_terminal_input_next_id = old_listener_id
    ui._footer_branch_callback_next_id = old_callback_id
    fresh_listener_dispose = ui.add_extension_terminal_input_listener(
        lambda key: {"data": f"fresh:{key}"}
    )
    fresh_callback_dispose = ui.register_footer_branch_change_callback(lambda: "fresh")
    assert set(ui._extension_terminal_input_listeners) == {old_listener_id}
    assert set(ui._footer_branch_callbacks) == {old_callback_id}

    old_listener_dispose()
    old_callback_dispose()
    assert set(ui._extension_terminal_input_listeners) == {old_listener_id}
    assert set(ui._footer_branch_callbacks) == {old_callback_id}
    assert ui._apply_extension_terminal_input_listeners("x") == "fresh:x"
    assert ui._footer_branch_callbacks[old_callback_id]() == "fresh"

    fresh_listener_dispose()
    fresh_callback_dispose()
    assert ui._extension_terminal_input_listeners == {}
    assert ui._footer_branch_callbacks == {}


def test_terminal_input_listeners_transform_consume_and_dispose():
    ui = _ui()
    seen: list[str] = []

    dispose_first = ui.add_extension_terminal_input_listener(
        lambda key: {"data": "b"} if key == "a" else None
    )
    ui.add_extension_terminal_input_listener(lambda key: seen.append(key) or None)

    assert ui._apply_extension_terminal_input_listeners("a") == "b"
    assert seen == ["b"]

    ui.add_extension_terminal_input_listener(
        lambda key: {"data": "xy"} if key == "b" else None
    )
    assert ui._apply_extension_terminal_input_listeners("a") == "xy"

    ui.add_extension_terminal_input_listener(lambda key: {"consume": True})
    assert ui._apply_extension_terminal_input_listeners("x") is None

    dispose_first()
    dispose_first()
    assert len(ui._extension_terminal_input_listeners) == 3


def test_terminal_input_symbolic_fallthrough_not_marked_replaced():
    ui = _ui()
    assert ui._apply_extension_terminal_input_listeners("pageup") == "pageup"
    assert ui._extension_terminal_input_last_replaced is False


def test_terminal_input_listener_failsoft_and_object_result():
    ui = _ui()

    class Result:
        data = "z"

    def boom(_key):
        raise RuntimeError("bad listener")

    ui.add_extension_terminal_input_listener(boom)
    ui.add_extension_terminal_input_listener(lambda key: Result())
    assert ui._apply_extension_terminal_input_listeners("a") == "z"


def _frame_text(ui, width=60, height=24):
    return [fl.text for fl in ui._frame_lines(width=width, height=height, pad=False)]


def test_header_renders_above_pending_and_input():
    ui = _ui()
    ui.set_extension_header(
        lambda theme: type("C", (), {"render": lambda self, w: ["HEADER_ROW"]})()
    )
    text = "\n".join(_frame_text(ui))
    assert "HEADER_ROW" in text


def test_above_widget_renders_in_frame():
    ui = _ui()
    ui.set_extension_widget("k", ["ABOVE_ROW"], placement="above_editor")
    assert any("ABOVE_ROW" in line for line in _frame_text(ui))


def test_below_widget_renders_in_frame():
    ui = _ui()
    ui.set_extension_widget("k", ["BELOW_ROW"], placement="below_editor")
    assert any("BELOW_ROW" in line for line in _frame_text(ui))


def test_footer_replaces_builtin_rows():
    ui = _ui()
    ui.footer_lines = ("builtin-a", "builtin-b")
    ui.set_extension_footer(
        lambda theme, fd: type("C", (), {"render": lambda self, w: ["EXT_FOOTER"]})()
    )
    text = "\n".join(_frame_text(ui))
    assert "EXT_FOOTER" in text and "builtin-a" not in text


def test_factory_widget_rerenders_on_width_change():
    ui = _ui()

    class _Comp:
        def render(self, width):
            return [f"W{width}"]

    # Widths must stay at/above the _MIN_WIDTH=60 floor that the driver's
    # size() clamps to (anything narrower renders at 60), so use 65/70 to
    # exercise re-render.
    ui.set_extension_widget("k", lambda theme: _Comp())
    _frame_text(ui, width=65)
    assert any("W65" in line for line in _frame_text(ui, width=65))
    assert any("W70" in line for line in _frame_text(ui, width=70))


def test_chrome_factory_gets_pi_shaped_tui_handle_and_theme():
    ui = _ui()
    seen = []

    class _Comp:
        def render(self, width):
            return ["ok"]

    def factory(tui, theme):
        seen.append(
            (hasattr(tui, "requestRender"), hasattr(tui, "request_render"), theme)
        )
        return _Comp()

    ui.set_extension_widget("k", factory)
    assert seen and seen[0][0] is True and seen[0][1] is True and seen[0][2] is not None


def test_footer_factory_gets_pi_shaped_tui_theme_and_footer_data():
    ui = _ui()
    seen = []

    class _Comp:
        def render(self, width):
            return ["footer"]

    def factory(tui, theme, footer_data):
        seen.append((hasattr(tui, "requestRender"), theme, footer_data))
        return _Comp()

    ui.set_extension_footer(factory)
    assert (
        seen
        and seen[0][0] is True
        and seen[0][1] is not None
        and seen[0][2] is not None
    )


def test_factory_widget_rerenders_each_frame_at_same_width():
    ui = _ui()
    state = {"value": "one"}
    renders = []

    class _Comp:
        def render(self, width):
            renders.append(width)
            return [state["value"]]

    ui.set_extension_widget("k", lambda tui, theme: _Comp())
    assert any("one" in line for line in _frame_text(ui, width=70))
    state["value"] = "two"
    assert any("two" in line for line in _frame_text(ui, width=70))
    assert len(renders) >= 3  # initial set + two same-width frame renders


def test_chrome_request_render_repaints_live_frame():
    ui = _ui()
    handles = []
    renders = []

    class _Comp:
        def render(self, width):
            renders.append(width)
            return [f"render-{len(renders)}"]

    def factory(tui, theme):
        handles.append(tui)
        return _Comp()

    ui.set_extension_widget("k", factory)
    before = len(renders)
    handles[0].requestRender()
    handles[0].request_render(True)
    assert len(renders) >= before + 2


def test_request_render_inside_render_is_coalesced_not_recursive():
    ui = _ui()
    renders = []

    class _Comp:
        def __init__(self, tui):
            self.tui = tui

        def render(self, width):
            renders.append(width)
            if len(renders) == 1:
                self.tui.requestRender()
            return ["ok"]

    ui.set_extension_widget("k", lambda tui, theme: _Comp(tui))
    assert len(renders) == 2


def test_chrome_factory_typeerror_body_is_not_reinvoked_with_legacy_arity():
    ui = _ui()
    calls = []

    def factory(tui, theme):
        calls.append("called")
        raise TypeError("body failure")

    ui.set_extension_widget("k", factory)
    assert calls == ["called"]
    assert "k" not in ui.extension_widgets_above


def test_tall_chrome_clamped_and_input_preserved():
    ui = _ui()
    for i in range(16):  # _WIDGET_MAX_COUNT widgets, each _WIDGET_MAX_LINES tall
        ui.set_extension_widget(
            f"w{i}", [f"r{i}-{j}" for j in range(10)], placement="above_editor"
        )
    frame = ui._frame_lines(width=60, height=24, pad=False)
    assert len(frame) <= 24  # fits the viewport
    assert any(fl.kind == "input" for fl in frame)  # input not starved
    assert any(fl.kind == "footer" for fl in frame)  # footer survives
    assert any("chrome clipped" in fl.text for fl in frame)  # truncation marker


def _fill_tall_chrome(ui, *, custom_footer=False):
    ui.set_extension_header(
        lambda theme: type(
            "C", (), {"render": lambda self, w: [f"H{i}" for i in range(8)]}
        )()
    )
    for i in range(16):  # _WIDGET_MAX_COUNT widgets, each _WIDGET_MAX_LINES tall
        ui.set_extension_widget(
            f"a{i}", [f"a{i}-{j}" for j in range(10)], placement="above_editor"
        )
        ui.set_extension_widget(
            f"b{i}", [f"b{i}-{j}" for j in range(10)], placement="below_editor"
        )
    if custom_footer:
        # A custom footer taller than the two built-in rows (4 rows).
        ui.set_extension_footer(
            lambda theme, fd: type(
                "C", (), {"render": lambda self, w: [f"F{i}" for i in range(4)]}
            )()
        )


@pytest.mark.parametrize("height", [12, 16, 24, 40])
def test_frame_clamp_never_overflows_or_starves(height):
    ui = _ui()
    # Include a tall custom footer (>2 rows) in one representative case. When a
    # custom footer is set its rows carry the "chrome_custom" kind; otherwise the
    # built-in footer rows carry "footer".
    custom_footer = height == 24
    _fill_tall_chrome(ui, custom_footer=custom_footer)
    frame = ui._frame_lines(width=60, height=height, pad=False)
    footer_kind = "chrome_custom" if custom_footer else "footer"
    assert len(frame) <= height  # fits the viewport
    assert any(fl.kind == "input" for fl in frame)  # input never starved
    if custom_footer:
        assert any(fl.text.startswith("F") for fl in frame)  # custom footer survives
    else:
        assert any(fl.kind == footer_kind for fl in frame)  # footer always survives


@pytest.mark.parametrize("height", [12, 16, 24, 40])
def test_live_region_clamp_never_overflows_or_starves(height):
    ui = _ui()
    _fill_tall_chrome(ui, custom_footer=(height == 24))
    lines = ui._live_region_lines(width=60, height=height)
    assert len(lines) <= height  # fits the viewport
    assert any(fl.kind == "input" for fl in lines)  # input never starved


def test_indicator_frames_override_used_by_tui_renderer():
    ui = _ui()
    ui.set_extension_working_indicator(["★"], 50)
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, interval = renderer._effective_spinner()
    assert frames == ("★",) and interval == 0.05


def test_indicator_default_when_unset():
    ui = _ui()
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, interval = renderer._effective_spinner()
    assert frames == _TuiToolLoopRenderer._SPINNER_FRAMES
    assert interval == _TuiToolLoopRenderer._SPINNER_INTERVAL_SECONDS


def test_indicator_empty_frames_hides_glyph():
    ui = _ui()
    ui.set_extension_working_indicator([], None)
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, _interval = renderer._effective_spinner()
    assert frames == ("",)  # blank glyph -> hidden spinner


def test_indicator_bad_frames_is_failsoft():
    ui = _ui()
    ui.set_extension_working_indicator(["a"], 50)  # establish a known value
    ui.set_extension_working_indicator(123, None)  # non-iterable frames must not raise
    # left unchanged (still the previously-set frames), and interval handled normally
    assert ui.extension_indicator_frames == ("a",)


@pytest.mark.parametrize("h", [12, 13, 14, 16, 20, 24])
def test_tiny_viewport_with_pending_status_and_tall_footer_no_overflow(h):
    ui = _ui()
    ui.footer_lines = ("a", "b")
    ui.enqueue_steering("pending one")
    for i in range(5):
        ui.set_extension_status(f"k{i}", f"v{i}")
    ui.set_extension_footer(
        lambda theme, fd: type(
            "C", (), {"render": lambda self, w: ["F1", "F2", "F3", "F4"]}
        )()
    )
    live = ui._live_region_lines(width=80, height=h)
    assert len(live) <= h  # live region never exceeds the viewport
    assert any(fl.kind == "input" for fl in live)  # input survives
    frame = ui._frame_lines(width=80, height=h, pad=False)
    assert len(frame) <= h
    assert any(fl.kind == "input" for fl in frame)


def test_footer_branch_change_rebuilds_and_invokes_callbacks(tmp_path):
    import subprocess

    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )
    seen = []
    rendered = []
    disposers = []

    class _Footer:
        def __init__(self, branch):
            self.branch = branch

        def render(self, width):
            return [f"branch={self.branch}"]

    def factory(theme, footer_data):
        rendered.append(footer_data.getGitBranch())
        disposers.append(
            footer_data.onBranchChange(lambda: seen.append(footer_data.getGitBranch()))
        )
        return _Footer(footer_data.getGitBranch())

    ui.set_extension_footer(factory)
    assert rendered == ["main"]

    subprocess.run(
        ["git", "checkout", "-b", "next"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui._footer_branch_last_check = 0.0
    ui.poll_extension_footer_branch()

    assert seen == ["next"]
    assert rendered[-1] == "next"
    assert ui.extension_footer is not None
    assert ui.extension_footer.snapshot == ("branch=next",)


def test_footer_branch_change_disposer_and_clear_suppress_callbacks(tmp_path):
    import subprocess

    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )
    seen = []
    disposers = []

    def factory(theme, footer_data):
        disposers.append(footer_data.onBranchChange(lambda: seen.append("changed")))
        return [footer_data.getGitBranch() or "none"]

    ui.set_extension_footer(factory)
    disposers[-1]()
    disposers[-1]()
    subprocess.run(
        ["git", "checkout", "-b", "next"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui._footer_branch_last_check = 0.0
    ui.poll_extension_footer_branch()
    assert seen == []

    ui.set_extension_footer(None)
    subprocess.run(
        ["git", "checkout", "-b", "third"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui._footer_branch_last_check = 0.0
    ui.poll_extension_footer_branch()
    assert seen == []


def test_footer_branch_change_detached_head_uses_stable_label(tmp_path):
    import subprocess

    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "--detach"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )
    seen = []
    rendered = []

    def factory(theme, footer_data):
        rendered.append(footer_data.getGitBranch())
        footer_data.onBranchChange(lambda: seen.append(footer_data.getGitBranch()))
        return [footer_data.getGitBranch() or "none"]

    ui.set_extension_footer(factory)
    assert rendered == ["detached"]

    subprocess.run(
        ["git", "checkout", "-b", "next"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui._footer_branch_last_check = 0.0
    ui.poll_extension_footer_branch()

    assert rendered[-1] == "next"
    assert seen == ["next"]


def test_footer_branch_change_preserves_disposed_callback_slots(tmp_path):
    import subprocess

    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )
    seen = []
    disposers = []

    def factory(theme, footer_data):
        branch = footer_data.getGitBranch()
        disposers.append(footer_data.onBranchChange(lambda: seen.append(f"a:{branch}")))
        disposers.append(footer_data.onBranchChange(lambda: seen.append(f"b:{branch}")))
        return [branch or "none"]

    ui.set_extension_footer(factory)
    disposers[0]()

    subprocess.run(
        ["git", "checkout", "-b", "next"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    ui._footer_branch_last_check = 0.0
    ui.poll_extension_footer_branch()

    assert seen == ["b:next"]
