from pipy_harness.extensions import (
    MessageRenderComponent,
    MessageRenderContext,
    RenderedCustomEntry,
    ToolRenderComponent,
    lines_component,
)
from pipy_harness.native.extension_runtime import (
    RegisteredMessageRenderer,
    render_extension_message,
)


def _renderers(custom_type, fn):
    return {custom_type: RegisteredMessageRenderer(custom_type, fn, "ext")}


def test_one_arg_renderer_returns_plain_lines():
    r = _renderers("note", lambda data: [f"text:{data['t']}"])
    out = render_extension_message(r, "note", {"t": "hi"})
    assert out.lines == ("text:hi",)
    assert out.styled is False


def test_one_arg_renderer_returning_component_like_stays_plain():
    # Critical: a 1-arg (slice-16) renderer must NEVER hit the component path,
    # even if it returns an object exposing a render() attribute.
    class _Componentish:
        def render(self, width):
            return ["should-not-be-used"]

        def __repr__(self):
            return "PLAINREPR"

    out = render_extension_message(
        _renderers("note", lambda data: _Componentish()), "note", {},
    )
    assert out.styled is False
    assert "should-not-be-used" not in "".join(out.lines)


def test_two_arg_component_renderer_is_styled():
    # Component whose render(width) emits a themed line via ctx.theme.
    def renderer(data, ctx):
        text = ctx.theme.fg("accent", data["t"]) if ctx.theme else data["t"]
        return lines_component([text])

    class _Theme:
        def fg(self, color, text):
            return f"\x1b[1m{text}\x1b[0m"

        def bold(self, text):
            return text

        def dim(self, text):
            return text

    out = render_extension_message(
        _renderers("card", renderer), "card", {"t": "hi"},
        width=40, expanded=False, theme=_Theme(),
    )
    assert out.styled is True
    assert out.lines == ("\x1b[1mhi\x1b[0m",)


def test_two_arg_text_return_is_plain():
    out = render_extension_message(
        _renderers("note", lambda data, ctx: f"w={ctx.width}"),
        "note", {}, width=77,
    )
    assert out.lines == ("w=77",)
    assert out.styled is False


def test_unknown_type_renders_generic_plain():
    out = render_extension_message({}, "note", {"t": "x"})
    assert out.styled is False
    assert out.lines and "t" in out.lines[0]


def test_renderer_exception_is_fail_soft():
    def boom(data, ctx):
        raise RuntimeError("kaboom")

    out = render_extension_message(_renderers("card", boom), "card", {})
    assert out.styled is False
    assert out.lines[0].startswith("render error:")
    assert "kaboom" not in out.lines[0]


def test_component_render_exception_is_fail_soft():
    class _Bad:
        def render(self, width):
            raise RuntimeError("render-boom")

    out = render_extension_message(
        _renderers("card", lambda data, ctx: _Bad()), "card", {},
    )
    assert out.styled is False
    assert out.lines[0].startswith("render error:")


def test_expanded_threaded_to_renderer():
    out = render_extension_message(
        _renderers("note", lambda data, ctx: f"e={ctx.expanded}"),
        "note", {}, expanded=True,
    )
    assert out.lines == ("e=True",)


def test_capture_default_second_param_treated_as_one_arg():
    # The slice-16 capture-default idiom (lambda data, prefix=captured: ...) is
    # semantically 1-arg; the second param has a default, so it must stay on the
    # plain path and never be bound to the MessageRenderContext.
    out = render_extension_message(
        _renderers("note", lambda data, prefix="P:": [prefix + str(data)]),
        "note", "x", width=10, expanded=False, theme=object(),
    )
    assert out.styled is False
    assert out.lines == ("P:x",)   # default used; ctx did NOT clobber prefix


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
