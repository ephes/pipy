import io

import pytest

from pipy_harness.native.tui import ToolLoopTerminalUi, _ChromeRegion
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
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
    assert isinstance(region, _ChromeRegion)
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
    ui.set_extension_footer(lambda theme, footer_data: type("C", (), {"render": lambda self, w: ["f"]})())
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
    ui.set_extension_widget("k", None)       # clear
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


def test_clear_extension_chrome_resets_all():
    ui = _ui()
    ui.set_extension_widget("k", ["a"])
    ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["h"]})())
    ui.set_extension_title("t")
    ui.add_extension_terminal_input_listener(lambda key: None)
    ui.clear_extension_chrome()
    assert ui.extension_widgets_above == {}
    assert ui.extension_header is None
    assert ui.extension_title is None
    assert ui._extension_terminal_input_listeners == {}


def test_terminal_input_listeners_transform_consume_and_dispose():
    ui = _ui()
    seen: list[str] = []

    dispose_first = ui.add_extension_terminal_input_listener(
        lambda key: {"data": "b"} if key == "a" else None
    )
    ui.add_extension_terminal_input_listener(lambda key: seen.append(key) or None)

    assert ui._apply_extension_terminal_input_listeners("a") == "b"
    assert seen == ["b"]

    ui.add_extension_terminal_input_listener(lambda key: {"data": "xy"} if key == "b" else None)
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
    ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["HEADER_ROW"]})())
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
    ui.set_extension_footer(lambda theme, fd: type("C", (), {"render": lambda self, w: ["EXT_FOOTER"]})())
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
        seen.append((hasattr(tui, "requestRender"), hasattr(tui, "request_render"), theme))
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
    assert seen and seen[0][0] is True and seen[0][1] is not None and seen[0][2] is not None


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
    assert len(frame) <= 24                                  # fits the viewport
    assert any(fl.kind == "input" for fl in frame)           # input not starved
    assert any(fl.kind == "footer" for fl in frame)          # footer survives
    assert any("chrome clipped" in fl.text for fl in frame)  # truncation marker


def _fill_tall_chrome(ui, *, custom_footer=False):
    ui.set_extension_header(
        lambda theme: type("C", (), {"render": lambda self, w: [f"H{i}" for i in range(8)]})()
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
    assert len(frame) <= height                              # fits the viewport
    assert any(fl.kind == "input" for fl in frame)           # input never starved
    if custom_footer:
        assert any(fl.text.startswith("F") for fl in frame)  # custom footer survives
    else:
        assert any(fl.kind == footer_kind for fl in frame)   # footer always survives


@pytest.mark.parametrize("height", [12, 16, 24, 40])
def test_live_region_clamp_never_overflows_or_starves(height):
    ui = _ui()
    _fill_tall_chrome(ui, custom_footer=(height == 24))
    lines = ui._live_region_lines(width=60, height=height)
    assert len(lines) <= height                          # fits the viewport
    assert any(fl.kind == "input" for fl in lines)       # input never starved


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
    ui._pending_steering = ["pending one"]
    for i in range(5):
        ui.set_extension_status(f"k{i}", f"v{i}")
    ui.set_extension_footer(
        lambda theme, fd: type(
            "C", (), {"render": lambda self, w: ["F1", "F2", "F3", "F4"]}
        )()
    )
    live = ui._live_region_lines(width=80, height=h)
    assert len(live) <= h                          # live region never exceeds the viewport
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
