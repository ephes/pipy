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


def test_clear_extension_chrome_resets_all():
    ui = _ui()
    ui.set_extension_widget("k", ["a"])
    ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["h"]})())
    ui.set_extension_title("t")
    ui.clear_extension_chrome()
    assert ui.extension_widgets_above == {}
    assert ui.extension_header is None
    assert ui.extension_title is None
