from pipy_harness.extensions import ToolRenderContext, lines_component
from pipy_harness.native.tool_renderers import render_tool_phase


def _ctx():
    return ToolRenderContext(
        tool_name="t", args={"a": 1}, is_result=True, is_error=False,
        content="raw", details={"k": "v"}, expanded=False, width=40,
        theme=None, state={},
    )


def test_good_renderer_returns_lines():
    out = render_tool_phase(lambda ctx: lines_component(["a", "b"]), _ctx())
    assert out == ["a", "b"]


def test_renderer_that_raises_falls_back_to_none():
    def boom(ctx):
        raise RuntimeError("nope")
    assert render_tool_phase(boom, _ctx()) is None


def test_render_method_that_raises_falls_back():
    class Bad:
        def render(self, width):
            raise ValueError("bad")
    assert render_tool_phase(lambda ctx: Bad(), _ctx()) is None


def test_non_component_return_falls_back():
    assert render_tool_phase(lambda ctx: 123, _ctx()) is None


def test_bad_render_output_type_falls_back():
    class Bad:
        def render(self, width):
            return 5
    assert render_tool_phase(lambda ctx: Bad(), _ctx()) is None


def test_bare_string_render_is_not_char_per_line():
    class S:
        def render(self, width):
            return "hello"
    assert render_tool_phase(lambda ctx: S(), _ctx()) == ["hello"]
