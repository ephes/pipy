import io
from pathlib import Path

from pipy_harness.extensions import (
    ExtensionTool,
    RegisteredTool,
    ToolResult,
    lines_component,
)
from pipy_harness.native.tool_loop_session import (
    _ExtensionToolPort,
    _TuiToolLoopRenderer,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


def _registered(handler, **kw):
    tool = ExtensionTool(
        name="kv", description="d",
        input_schema={"type": "object"}, handler=handler, **kw,
    )
    return RegisteredTool(tool=tool, extension="ext")


def test_port_writes_details_to_sink(tmp_path: Path):
    sink: dict[str, object] = {}
    port = _ExtensionToolPort(
        _registered(
            lambda ctx, inp: ToolResult(content="c", details={"k": "v"}),
            render_result=lambda ctx: None,
        ),
        has_ui=False, render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(), tool_name="kv",
        arguments={}, provider_correlation_id="corr-1",
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
        has_ui=False, render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(), tool_name="kv",
        arguments={}, provider_correlation_id="corr-2",
    )
    port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))
    assert sink["corr-2"] is None


def _tui(tmp_path):
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def test_tui_renderer_uses_render_result(tmp_path):
    from pipy_harness.native.models import ProviderToolCall

    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="ignored", details={"k": "v"}),
        render_result=lambda ctx: lines_component(
            [f"key={ctx.details['k']}", f"err={ctx.is_error}"]
        ),
    )
    ui = _tui(tmp_path)
    sink: dict[str, object] = {"corr-1": {"k": "v"}}
    renderer = _TuiToolLoopRenderer(
        ui=ui,
        tool_renderers={"kv": tool},
        render_details_sink=sink,
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="corr-1", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="ignored", is_error=False)
    blocks = [b for b in ui._history_blocks if b[0] == "tool_result_custom"]
    assert blocks, "expected a tool_result_custom block"
    text = "\n".join(blocks[-1][1])
    assert "key=v" in text and "err=False" in text


def test_tui_renderer_falls_back_when_renderer_crashes(tmp_path):
    from pipy_harness.native.models import ProviderToolCall

    def boom(ctx):
        raise RuntimeError("nope")

    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="real-output"),
        render_result=boom,
    )
    ui = _tui(tmp_path)
    renderer = _TuiToolLoopRenderer(
        ui=ui, tool_renderers={"kv": tool}, render_details_sink={},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="real-output", is_error=False)
    kinds = [b[0] for b in ui._history_blocks]
    assert "tool_result" in kinds and "tool_result_custom" not in kinds


def test_tui_renderer_falls_back_when_render_call_crashes(tmp_path):
    from pipy_harness.native.models import ProviderToolCall

    def boom(ctx):
        raise RuntimeError("nope")

    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=boom,
    )
    ui = _tui(tmp_path)
    renderer = _TuiToolLoopRenderer(
        ui=ui, tool_renderers={"kv": tool}, render_details_sink={},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    kinds = [b[0] for b in ui._history_blocks]
    assert "tool" in kinds and "tool_call_custom" not in kinds


def test_captured_renderer_emits_custom_lines(tmp_path):
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.tool_loop_session import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x", details={"k": "v"}),
        render_result=lambda ctx: lines_component([f"KV:{ctx.details['k']}"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out, error_stream=err,
        tool_renderers={"kv": tool},
        render_details_sink={"c": {"k": "v"}},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="x", is_error=False)
    assert "KV:v" in err.getvalue()


def test_captured_renderer_emits_custom_call_lines(tmp_path):
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.tool_loop_session import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x"),
        render_call=lambda ctx: lines_component(["CALL:kv"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out, error_stream=err,
        tool_renderers={"kv": tool}, render_details_sink={},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    assert "CALL:kv" in err.getvalue()
