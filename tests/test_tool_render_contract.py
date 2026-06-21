from collections.abc import Mapping

from pipy_harness.extensions import (
    ExtensionTool,
    ToolRenderContext,
    coerce_tool_render_lines,
    lines_component,
)


def test_lines_component_str_is_one_logical_value_not_char_per_line():
    comp = lines_component("ok\ndone")
    assert comp.render(80) == ["ok", "done"]


def test_lines_component_rejects_char_per_line_for_single_line_str():
    comp = lines_component("hello")
    assert comp.render(80) == ["hello"]


def test_coerce_sequence_elementwise():
    assert coerce_tool_render_lines(["a", 1]) == ("a", "1")


def test_coerce_str_splits_on_newlines():
    assert coerce_tool_render_lines("a\nb") == ("a", "b")


def test_coerce_rejects_bytes():
    assert coerce_tool_render_lines(b"nope") is None


def test_coerce_rejects_unknown_type():
    assert coerce_tool_render_lines(object()) is None


def test_coerce_bounds_huge_output():
    out = coerce_tool_render_lines("x" * 20000)
    assert "tool render truncated" in "\n".join(out)


def test_extension_tool_accepts_renderers():
    tool = ExtensionTool(
        name="t",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: None,
        render_call=lambda ctx: lines_component("call"),
        render_result=lambda ctx: lines_component("result"),
    )
    assert callable(tool.render_call)
    assert callable(tool.render_result)


def test_extension_tool_renderers_default_none():
    tool = ExtensionTool(
        name="t", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: None,
    )
    assert tool.render_call is None and tool.render_result is None


def test_render_context_is_frozen_with_state_mapping():
    ctx = ToolRenderContext(
        tool_name="t", args={}, is_result=False, is_error=False,
        content=None, details=None, expanded=False, width=80,
        theme=None, state={},
    )
    assert isinstance(ctx.args, Mapping)
    ctx.state["x"] = 1  # state mapping is mutable even though the dataclass is frozen
    assert ctx.state["x"] == 1
