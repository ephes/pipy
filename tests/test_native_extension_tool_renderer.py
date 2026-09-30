import io
import re
from pathlib import Path

from pipy_harness.extensions import (
    ExtensionTool,
    RegisteredTool,
    ToolRenderContext,
    ToolResult,
    lines_component,
)
from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.frame_renderer import decode_tool_box_line
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _registered(handler, **kw):
    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=handler,
        **kw,
    )
    return RegisteredTool(tool=tool, extension="ext")


def _invoke(port: _ExtensionToolPort, tmp_path: Path):
    req = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="kv",
        arguments={},
        provider_correlation_id="corr-1",
    )
    return port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))


def test_port_puts_details_on_the_result(tmp_path: Path):
    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c", details={"k": "v"})),
        has_ui=False,
    )
    assert _invoke(port, tmp_path).details == {"k": "v"}


def test_port_result_has_no_details_when_absent(tmp_path: Path):
    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c")),
        has_ui=False,
    )
    assert _invoke(port, tmp_path).details is None


def test_port_drops_details_that_are_not_json(tmp_path: Path):
    """The session stores the JSON copy; live rows render the same value."""

    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c", details={"k": object()})),
        has_ui=False,
    )
    assert _invoke(port, tmp_path).details is None


def test_port_details_are_a_detached_json_copy(tmp_path: Path):
    source = {"k": [1, 2]}
    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c", details=source)),
        has_ui=False,
    )
    details = _invoke(port, tmp_path).details
    source["k"].append(3)
    assert details == {"k": [1, 2]}


def _tui(tmp_path):
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def _renderer(ui, tools, tmp_path) -> TuiToolLoopRenderer:
    return TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers=tools,
        cwd=tmp_path,
    )


def _row_texts(lines) -> list[str]:
    return [_SGR.sub("", decode_tool_box_line(line)[2]) for line in lines]


def _tool_rows(ui) -> list[list[str]]:
    """Plain text of every committed tool row, then the running one."""

    transcript = ui.components.transcript
    rows = [
        [text for text in _row_texts(lines) if text]
        for kind, lines in transcript.history_blocks
        if kind == "tool_box"
    ]
    if transcript.pending_tool_lines:
        rows.append([t for t in _row_texts(transcript.pending_tool_lines) if t])
    return rows


def test_tui_renderer_uses_render_result_with_result_details(tmp_path):
    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="ignored", details={"k": "v"}),
        render_result=lambda ctx: lines_component(
            [f"key={ctx.details['k']}", f"err={ctx.is_error}"]
        ),
    )
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": tool}, tmp_path)
    renderer.render_tool_call(AgentToolCall("corr-1", "kv", ProductContent("{}")))
    renderer.render_tool_result(
        output_text="ignored", is_error=False, details={"k": "v"}
    )
    # No render_call: Pi's generic header, then the extension's result lines.
    assert _tool_rows(ui) == [["kv", "key=v", "err=False"]]


def test_tui_renderer_rerenders_extension_rows_on_ctrl_o(tmp_path):
    """DF1-F2b: extension rows follow the expansion flag (Pi setExpanded)."""

    seen: list[tuple[bool, bool, object]] = []

    def render_call(ctx: ToolRenderContext) -> object:
        seen.append((ctx.is_result, ctx.expanded, dict(ctx.state)))
        calls = ctx.state.get("calls", 0)
        ctx.state["calls"] = (calls if isinstance(calls, int) else 0) + 1
        return lines_component([f"CALL expanded={ctx.expanded}"])

    def render_result(ctx: ToolRenderContext) -> object:
        seen.append((ctx.is_result, ctx.expanded, ctx.details))
        return lines_component([f"RESULT expanded={ctx.expanded} {ctx.content}"])

    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=render_call,
        render_result=render_result,
    )
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": tool}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent('{"a": 1}')))
    renderer.render_tool_result(output_text="out", is_error=False, details={"d": 1})
    assert _tool_rows(ui) == [["CALL expanded=False", "RESULT expanded=False out"]]

    ui.components.transcript.set_tools_expanded(True)

    assert _tool_rows(ui) == [["CALL expanded=True", "RESULT expanded=True out"]]
    # The retained per-call state and details reach every redraw.
    assert (True, True, {"d": 1}) in seen
    assert any(
        not is_result and expanded and state.get("calls")
        for is_result, expanded, state in seen
        if isinstance(state, dict)
    )


def test_tui_renderer_falls_back_when_renderer_crashes(tmp_path):
    def boom(ctx):
        raise RuntimeError("nope")

    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="real-output"),
        render_result=boom,
    )
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": tool}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c", "kv", ProductContent("{}")))
    renderer.render_tool_result(output_text="real-output", is_error=False)
    # Pi createResultFallback: the output under the generic header.
    assert _tool_rows(ui) == [["kv", "real-output"]]


def test_tui_renderer_falls_back_when_render_call_crashes(tmp_path):
    def boom(ctx):
        raise RuntimeError("nope")

    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=boom,
    )
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": tool}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c", "kv", ProductContent('{"a": 1}')))
    # Pi formatToolCallWithArgs.
    assert _tool_rows(ui) == [["kv a=1"]]


def test_captured_renderer_emits_custom_lines_with_result_details(tmp_path):
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x", details={"k": "v"}),
        render_result=lambda ctx: lines_component([f"KV:{ctx.details['k']}"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out,
        error_stream=err,
        tool_renderers={"kv": tool},
    )
    renderer.render_tool_call(AgentToolCall("c", "kv", ProductContent("{}")))
    renderer.render_tool_result(output_text="x", is_error=False, details={"k": "v"})
    assert "KV:v" in err.getvalue()


def test_captured_renderer_emits_custom_call_lines(tmp_path):
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:kv"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out,
        error_stream=err,
        tool_renderers={"kv": tool},
    )
    renderer.render_tool_call(AgentToolCall("c", "kv", ProductContent("{}")))
    assert "CALL:kv" in err.getvalue()


def test_captured_renderer_prints_edit_diff_and_write_content(tmp_path):
    """The tools no longer print side output; the captured renderer does."""

    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=out, error_stream=err)
    renderer.render_tool_call(
        AgentToolCall("c1", "edit", ProductContent('{"path": "a.py", "edits": []}'))
    )
    renderer.render_tool_result(
        output_text="Successfully replaced 1 block(s) in a.py.",
        is_error=False,
        details={"diff": "-1 old\n+1 new", "firstChangedLine": 1},
    )
    renderer.render_tool_call(
        AgentToolCall(
            "c2", "write", ProductContent('{"path": "b.txt", "content": "x\\ty\\n\\n"}')
        )
    )
    renderer.render_tool_result(
        output_text="Successfully wrote to b.txt", is_error=False
    )
    rendered = err.getvalue()
    assert "-1 old\n+1 new" in rendered
    assert "x   y" in rendered


def test_captured_renderer_refreshes_tool_renderers_after_reload():
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    first = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:first"]),
    )
    second = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:second"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out,
        error_stream=err,
        tool_renderers={"kv": first},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": second})
    renderer.render_tool_call(AgentToolCall("c2", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_call(AgentToolCall("c3", "kv", ProductContent("{}")))

    rendered = err.getvalue()
    assert "CALL:first" in rendered
    assert "CALL:second" in rendered
    assert rendered.count("CALL:second") == 1
    assert "CALL:kv" not in rendered


def test_tui_renderer_refreshes_tool_renderers_after_reload(tmp_path):
    first = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:first"]),
    )
    second = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:second"]),
    )
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": first}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": second})
    renderer.render_tool_call(AgentToolCall("c2", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_call(AgentToolCall("c3", "kv", ProductContent("{}")))

    # The unregistered tool falls back to Pi's generic header.
    assert _tool_rows(ui) == [["CALL:first"], ["CALL:second"], ["kv"]]


def _renderer_pair(marker: str) -> ExtensionTool:
    return ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component([f"CALL:{marker}"]),
        render_result=lambda ctx: lines_component([f"RESULT:{marker}"]),
    )


def test_captured_renderer_pins_result_renderer_to_its_call():
    """A reload mid-call must not re-target the in-flight result renderer."""

    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    renderer = _ToolLoopRenderer(
        output_stream=out,
        error_stream=err,
        tool_renderers={"kv": _renderer_pair("first")},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": _renderer_pair("second")})
    renderer.render_tool_result(output_text="x", is_error=False)

    rendered = err.getvalue()
    assert "CALL:first" in rendered
    assert "RESULT:first" in rendered
    assert "RESULT:second" not in rendered


def test_captured_renderer_pins_result_renderer_when_tool_is_removed():
    """Losing the tool on reload must not silently drop the pending result."""

    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    renderer = _ToolLoopRenderer(
        output_stream=out,
        error_stream=err,
        tool_renderers={"kv": _renderer_pair("first")},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_result(output_text="x", is_error=False)

    assert "RESULT:first" in err.getvalue()


def test_tui_renderer_pins_result_renderer_to_its_call(tmp_path):
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": _renderer_pair("first")}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": _renderer_pair("second")})
    renderer.render_tool_result(output_text="x", is_error=False)

    assert _tool_rows(ui) == [["CALL:first", "RESULT:first"]]


def test_tui_renderer_pins_result_renderer_when_tool_is_removed(tmp_path):
    ui = _tui(tmp_path)
    renderer = _renderer(ui, {"kv": _renderer_pair("first")}, tmp_path)
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_result(output_text="x", is_error=False)

    assert _tool_rows(ui) == [["CALL:first", "RESULT:first"]]
