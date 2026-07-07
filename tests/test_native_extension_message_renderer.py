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


def test_custom_message_renderer_payload_fields():
    from datetime import UTC, datetime

    from pipy_harness.native.session_tree import CustomMessageEntry
    from pipy_harness.native.tool_loop_session import _custom_message_renderer_payload

    entry = CustomMessageEntry(
        "1",
        None,
        datetime.now(UTC),
        "card",
        "BODY",
        True,
        {"answer": 42},
    )

    assert _custom_message_renderer_payload(entry) == {
        "customType": "card",
        "content": "BODY",
        "display": True,
        "details": {"answer": 42},
    }


def test_custom_entry_redraw_rows_renders_custom_messages():
    from datetime import UTC, datetime

    from pipy_harness.native.extension_runtime import RenderedCustomEntry
    from pipy_harness.native.session_tree import CustomEntry, CustomMessageEntry
    from pipy_harness.native.tool_loop_session import _custom_entry_redraw_rows

    now = datetime.now(UTC)
    rows = _custom_entry_redraw_rows(
        (
            CustomEntry("1", None, now, "note", {"x": 1}),
            CustomMessageEntry("2", None, now, "hidden", "HIDDEN", False, None),
            CustomMessageEntry("3", None, now, "card", "BODY", True, {"k": "v"}),
        ),
        lambda custom_type, data: RenderedCustomEntry(("ENTRY",), False),
        lambda entry: RenderedCustomEntry(
            (f"MSG:{entry.content}:{entry.details['k']}",), True
        ),
    )

    assert rows == [
        ("plain", "note", ("ENTRY",)),
        ("styled", "card", ("MSG:BODY:v",)),
    ]


def test_custom_entry_redraw_rows_custom_message_falls_back_to_content():
    from datetime import UTC, datetime

    from pipy_harness.native.extension_runtime import RenderedCustomEntry
    from pipy_harness.native.session_tree import CustomMessageEntry
    from pipy_harness.native.tool_loop_session import _custom_entry_redraw_rows

    now = datetime.now(UTC)
    rows = _custom_entry_redraw_rows(
        (CustomMessageEntry("1", None, now, "card", "A\nB", True, None),),
        lambda custom_type, data: RenderedCustomEntry(("unused",), False),
    )

    assert rows == [("plain", "card", ("A", "B"))]


def test_render_extension_message_custom_message_payload_rich_component():
    def renderer(message, ctx):
        return lines_component([
            f"{message['customType']}:{message['content']}:{message['details']['n']}:w={ctx.width}"
        ])

    out = render_extension_message(
        _renderers("card", renderer),
        "card",
        {"customType": "card", "content": "BODY", "display": True, "details": {"n": 7}},
        width=55,
    )

    assert out.styled is True
    assert out.lines == ("card:BODY:7:w=55",)

class _NoopTty:
    def write(self, text):
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return True


def test_tui_redraw_custom_entries_replaces_previous_branch(tmp_path):
    from io import StringIO
    from typing import TextIO, cast

    from pipy_harness.native.tui import ToolLoopTerminalUi

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, StringIO()),
        terminal_stream=cast(TextIO, _NoopTty()),
        cwd=tmp_path,
    )
    ui.add_custom_entry("old", ["OLD-BODY"])
    ui.add_notice("ordinary history remains")

    ui.redraw_custom_entries((
        ("styled", "card", ("\x1b[1mNEW-STYLED\x1b[0m",)),
        ("plain", "note", ("NEW-PLAIN",)),
    ))

    blocks = ui.custom_entry_blocks()
    assert blocks == (
        ("custom_message_custom", ("\x1b[1mNEW-STYLED\x1b[0m",)),
        ("custom", ("[note]", "NEW-PLAIN")),
    )
    frame = "\n".join(ui.render_lines(width=80, height=20))
    assert "NEW-STYLED" in frame
    assert "NEW-PLAIN" in frame
    assert "ordinary history remains" in frame
    assert "OLD-BODY" not in frame


def test_custom_entry_redraw_rows_dispatches_branch_entries():
    from datetime import UTC, datetime

    from pipy_harness.native.extension_runtime import RenderedCustomEntry
    from pipy_harness.native.session_tree import CustomEntry, CustomMessageEntry
    from pipy_harness.native.tool_loop_session import _custom_entry_redraw_rows

    called = []

    def render(custom_type, data):
        called.append((custom_type, data))
        if custom_type == "card":
            return RenderedCustomEntry(lines=("STYLED",), styled=True)
        return RenderedCustomEntry(lines=("PLAIN",), styled=False)

    now = datetime.now(UTC)
    rows = _custom_entry_redraw_rows(
        (
            CustomEntry("1", None, now, "card", {"x": 1}),
            CustomMessageEntry("2", None, now, "hidden", "HIDDEN", False, None),
            CustomMessageEntry("3", None, now, "shown", "LINE1\nLINE2", True, None),
            CustomEntry("4", None, now, "note", {"y": 2}),
        ),
        render,
    )

    assert called == [("card", {"x": 1}), ("note", {"y": 2})]
    assert rows == [
        ("styled", "card", ("STYLED",)),
        ("plain", "shown", ("LINE1", "LINE2")),
        ("plain", "note", ("PLAIN",)),
    ]


def test_redraw_rows_with_metadata_keep_resume_rerender_state(tmp_path):
    from datetime import UTC, datetime
    from io import StringIO
    from typing import TextIO, cast

    from pipy_harness.native.session_tree import CustomMessageEntry
    from pipy_harness.native.tool_loop_session import (
        _custom_entry_redraw_rows,
        _custom_message_renderer_payload,
    )
    from pipy_harness.native.tui import ToolLoopTerminalUi

    def render(data, ctx):
        return lines_component([f"expanded={ctx.expanded}:{data['content']}"])

    renderers = _renderers("card", render)
    entry = CustomMessageEntry(
        "1",
        None,
        datetime.now(UTC),
        "card",
        "BODY",
        True,
        None,
    )
    rows = _custom_entry_redraw_rows(
        (entry,),
        lambda custom_type, data: render_extension_message(renderers, custom_type, data),
        lambda message: render_extension_message(
            renderers,
            message.custom_type,
            _custom_message_renderer_payload(message),
            expanded=False,
        ),
        render_metadata=renderers,
    )

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, StringIO()),
        terminal_stream=cast(TextIO, _NoopTty()),
        cwd=tmp_path,
    )
    ui.redraw_custom_entries(rows)
    assert ui.custom_entry_blocks() == (("custom_message_custom", ("expanded=False:BODY",)),)

    ui.tools_expanded = True
    ui.rerender_custom_messages()

    assert ui.custom_entry_blocks() == (("custom_message_custom", ("expanded=True:BODY",)),)


def test_tui_rerender_custom_messages_uses_current_expanded_flag(tmp_path):
    from io import StringIO
    from typing import TextIO, cast

    from pipy_harness.native.tui import ToolLoopTerminalUi

    def render(data, ctx):
        return lines_component([f"expanded={ctx.expanded}:{data['title']}"])

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, StringIO()),
        terminal_stream=cast(TextIO, _NoopTty()),
        cwd=tmp_path,
    )
    renderers = _renderers("card", render)
    ui.add_custom_entry_styled(
        ("expanded=False:alpha",),
        custom_type="card",
        data={"title": "alpha"},
        renderers=renderers,
    )

    assert ui.custom_entry_blocks() == (("custom_message_custom", ("expanded=False:alpha",)),)
    ui.tools_expanded = True
    ui.rerender_custom_messages()

    assert ui.custom_entry_blocks() == (("custom_message_custom", ("expanded=True:alpha",)),)


def test_tui_rerender_custom_messages_fail_soft_without_body_or_exception(tmp_path):
    from io import StringIO
    from typing import TextIO, cast

    from pipy_harness.native.tui import ToolLoopTerminalUi

    def render(data, ctx):
        raise RuntimeError(f"secret {data['secret']}")

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, StringIO()),
        terminal_stream=cast(TextIO, _NoopTty()),
        cwd=tmp_path,
    )
    renderers = _renderers("card", render)
    ui.add_custom_entry_styled(
        ("initial",),
        custom_type="card",
        data={"secret": "TOPSECRET"},
        renderers=renderers,
    )

    ui.tools_expanded = True
    ui.rerender_custom_messages()

    blocks = ui.custom_entry_blocks()
    assert blocks[0][0] == "custom"
    body = "\n".join(blocks[0][1])
    assert "render error:" in body
    assert "TOPSECRET" not in body
    assert "secret" not in body
