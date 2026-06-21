from pipy_harness.extensions import (
    MessageRenderComponent,
    MessageRenderContext,
    RenderedCustomEntry,
    ToolRenderComponent,
    lines_component,
)


def test_message_render_context_fields():
    ctx = MessageRenderContext(
        custom_type="card",
        data={"title": "hi"},
        expanded=True,
        width=80,
        theme=None,
    )
    assert ctx.custom_type == "card"
    assert ctx.data == {"title": "hi"}
    assert ctx.expanded is True
    assert ctx.width == 80
    assert ctx.theme is None


def test_rendered_custom_entry_fields():
    entry = RenderedCustomEntry(lines=("a", "b"), styled=True)
    assert entry.lines == ("a", "b")
    assert entry.styled is True


def test_message_render_component_is_tool_render_component_alias():
    # The alias keeps one component contract across rich-UI slices.
    assert MessageRenderComponent is ToolRenderComponent
    component = lines_component(["x"])
    assert isinstance(component, MessageRenderComponent)
