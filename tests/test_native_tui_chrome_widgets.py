import io

from pipy_harness.native.tui import ToolLoopTerminalUi, _ChromeRegion
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
    ui.clear_extension_chrome()
    assert ui.extension_widgets_above == {}
    assert ui.extension_header is None
    assert ui.extension_title is None


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

    # Widths must stay at/above the _MIN_WIDTH=60 floor that _dimensions clamps
    # to (anything narrower renders at 60), so use 65/70 to exercise re-render.
    ui.set_extension_widget("k", lambda theme: _Comp())
    _frame_text(ui, width=65)
    assert any("W65" in line for line in _frame_text(ui, width=65))
    assert any("W70" in line for line in _frame_text(ui, width=70))


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
