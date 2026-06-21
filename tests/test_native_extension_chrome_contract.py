from pipy_harness.extensions import (
    ChromeComponent,
    FooterData,
    WidgetPlacement,
    lines_component,
)
from pipy_harness.native.tool_renderers import render_chrome_component


def test_footer_data_snapshot_is_readonly_mapping():
    fd = FooterData(git_branch="main", extension_statuses={"k": "v"})
    assert fd.git_branch == "main"
    assert fd.extension_statuses["k"] == "v"


def test_render_chrome_component_lines_source():
    # A bare list of lines renders verbatim, bounded by max lines.
    out = render_chrome_component(["a", "b"], width=40, max_lines=8)
    assert out == ["a", "b"]


def test_render_chrome_component_str_not_char_per_line():
    out = render_chrome_component("hello", width=40, max_lines=8)
    assert out == ["hello"]


def test_render_chrome_component_factory_receives_width():
    class _Comp:
        def render(self, width):
            return [f"w={width}"]

    out = render_chrome_component(lambda: _Comp(), width=37, max_lines=8)
    assert out == ["w=37"]


def test_render_chrome_component_failsoft_returns_none():
    def boom():
        raise RuntimeError("x")

    assert render_chrome_component(boom, width=40, max_lines=8) is None


def test_render_chrome_component_direct_component_object():
    # A bare ChromeComponent (what lines_component returns) renders, not clears.
    out = render_chrome_component(lines_component(["x", "y"]), width=40, max_lines=8)
    assert out == ["x", "y"]


def test_render_chrome_component_truncates_to_max_lines():
    out = render_chrome_component([f"l{i}" for i in range(20)], width=40, max_lines=3)
    assert len(out) == 4  # 3 lines + a truncation marker
    assert "truncated" in out[-1]


def test_lines_component_is_a_chrome_component():
    # lines_component output structurally satisfies ChromeComponent (render only).
    comp = lines_component(["x"])
    assert isinstance(comp, ChromeComponent)


def test_widget_placement_values():
    assert set(WidgetPlacement.__args__) == {"above_editor", "below_editor"}
