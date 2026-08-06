import io
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
from pipy_harness.native.tool_renderers import _extension_render_details_sinks
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer


def test_render_details_sinks_select_renderer_and_preserve_writer_identity():
    tui = _extension_render_details_sinks(has_terminal_ui=True)
    assert tui.writer is tui.tui
    assert tui.captured is None

    captured = _extension_render_details_sinks(has_terminal_ui=False)
    assert captured.writer is captured.captured
    assert captured.tui is None


def _registered(handler, **kw):
    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=handler,
        **kw,
    )
    return RegisteredTool(tool=tool, extension="ext")


def test_port_writes_details_to_sink(tmp_path: Path):
    sink: dict[str, object] = {}
    port = _ExtensionToolPort(
        _registered(
            lambda ctx, inp: ToolResult(content="c", details={"k": "v"}),
            render_result=lambda ctx: None,
        ),
        has_ui=False,
        render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="kv",
        arguments={},
        provider_correlation_id="corr-1",
    )
    port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))
    assert sink["corr-1"] == {"k": "v"}


def test_port_writes_none_details_when_absent(tmp_path: Path):
    sink: dict[str, object] = {}
    port = _ExtensionToolPort(
        _registered(
            lambda ctx, inp: ToolResult(content="c"),
            render_result=lambda ctx: None,
        ),
        has_ui=False,
        render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="kv",
        arguments={},
        provider_correlation_id="corr-2",
    )
    port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))
    assert sink["corr-2"] is None


def _tui(tmp_path):
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def test_tui_renderer_uses_render_result(tmp_path):
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
    sink: dict[str, object] = {"corr-1": {"k": "v"}}
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": tool},
        render_details_sink=sink,
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="corr-1",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    renderer.render_tool_result(output_text="ignored", is_error=False)
    blocks = [
        b
        for b in ui.components.transcript.history_blocks
        if b[0] == "tool_result_custom"
    ]
    assert blocks, "expected a tool_result_custom block"
    text = "\n".join(blocks[-1][1])
    assert "key=v" in text and "err=False" in text


def test_tui_renderer_forwards_manually_injected_non_mapping_details(
    tmp_path: Path,
) -> None:
    seen: list[object | None] = []

    def render_result(ctx: ToolRenderContext) -> object:
        seen.append(ctx.details)
        return lines_component(["custom"])

    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="ignored"),
        render_result=render_result,
    )
    ui = _tui(tmp_path)
    sink: dict[str, object | None] = {"corr-1": "manually-injected"}
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": tool},
        render_details_sink=sink,
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="corr-1",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    renderer.render_tool_result(output_text="ignored", is_error=False)

    assert seen == ["manually-injected"]


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
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": tool},
        render_details_sink={},
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="c",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    renderer.render_tool_result(output_text="real-output", is_error=False)
    kinds = [b[0] for b in ui.components.transcript.history_blocks]
    assert "tool_result" in kinds and "tool_result_custom" not in kinds


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
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": tool},
        render_details_sink={},
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="c",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    kinds = [b[0] for b in ui.components.transcript.history_blocks]
    assert "tool" in kinds and "tool_call_custom" not in kinds


def test_captured_renderer_emits_custom_lines(tmp_path):
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
        render_details_sink={"c": {"k": "v"}},
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="c",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    renderer.render_tool_result(output_text="x", is_error=False)
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
        render_details_sink={},
    )
    renderer.render_tool_call(
        AgentToolCall(
            provider_correlation_id="c",
            tool_name="kv",
            arguments_json=ProductContent("{}"),
        )
    )
    assert "CALL:kv" in err.getvalue()


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
        render_details_sink={},
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
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": first},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": second})
    renderer.render_tool_call(AgentToolCall("c2", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_call(AgentToolCall("c3", "kv", ProductContent("{}")))

    custom_blocks = [
        b for b in ui.components.transcript.history_blocks if b[0] == "tool_call_custom"
    ]
    assert [tuple(b[1]) for b in custom_blocks] == [
        ("CALL:first",),
        ("CALL:second",),
    ]
    assert ui.components.transcript.history_blocks[-1][0] == "tool"


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
        render_details_sink={},
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
        render_details_sink={},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_result(output_text="x", is_error=False)

    assert "RESULT:first" in err.getvalue()


def test_tui_renderer_pins_result_renderer_to_its_call(tmp_path):
    ui = _tui(tmp_path)
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": _renderer_pair("first")},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({"kv": _renderer_pair("second")})
    renderer.render_tool_result(output_text="x", is_error=False)

    result_blocks = [
        b
        for b in ui.components.transcript.history_blocks
        if b[0] == "tool_result_custom"
    ]
    assert [tuple(b[1]) for b in result_blocks] == [("RESULT:first",)]


def test_tui_renderer_pins_result_renderer_when_tool_is_removed(tmp_path):
    ui = _tui(tmp_path)
    renderer = TuiToolLoopRenderer(
        transcript=ui.components.transcript,
        chrome=ui.components.chrome.record,
        render_inputs=ui.components.screen.render_inputs,
        tool_renderers={"kv": _renderer_pair("first")},
    )
    renderer.render_tool_call(AgentToolCall("c1", "kv", ProductContent("{}")))
    renderer.refresh_tool_renderers({})
    renderer.render_tool_result(output_text="x", is_error=False)

    result_blocks = [
        b
        for b in ui.components.transcript.history_blocks
        if b[0] == "tool_result_custom"
    ]
    assert [tuple(b[1]) for b in result_blocks] == [("RESULT:first",)]
