from pathlib import Path

from pipy_harness.extensions import ExtensionTool, RegisteredTool, ToolResult
from pipy_harness.native.tool_loop_session import _ExtensionToolPort
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)


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
