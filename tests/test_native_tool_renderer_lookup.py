"""Which renderers draw the calls to a tool (MCP parity plan S35).

Rows RND-01 and RND-03: a call to an MCP server tool is titled
``<server>/<tool>``, with the exact names while the tool is registered and
with the names its pipy name tells otherwise, as in a resumed session before
its server connected. Pi ``extensions/mcp/index.ts`` (the resolver it
registers) and regression test ``10285-mcp-tool-renderers.test.ts``. The rows
themselves are in ``test_native_tool_rows.py``.
"""

from __future__ import annotations

import io

import pytest

from pipy_harness.extensions import ExtensionTool, lines_component
from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.tool_renderer_lookup import (
    McpToolRows,
    ToolRendererLookup,
    as_tool_renderer_lookup,
)
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tools.name_patterns import mcp_tool_label_from_name


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("mcp__my_docs__search", "my_docs/search"),
        # Pi `/^mcp__(.+?)__(.+)$/`: the server ends at the first `__`.
        ("mcp__a__b__c", "a/b__c"),
        ("mcp__a___b", "a/_b"),
        ("mcp__docs__search_1a2b3c4d", "docs/search_1a2b3c4d"),
        ("mcp__docs", None),
        ("mcp__docs__", None),
        ("mcp____search", None),
        ("xmcp__docs__search", None),
        ("mcp__docs__search\n", None),
        ("read_mcp_resource", None),
        ("read", None),
        ("", None),
    ],
)
def test_the_title_a_tool_name_tells(name: str, label: str | None) -> None:
    assert mcp_tool_label_from_name(name) == label


def _tool(name: str) -> ExtensionTool:
    return ExtensionTool(
        name=name,
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, params: "",
        render_call=lambda ctx: lines_component([f"CALL:{name}"]),
    )


def test_a_registered_tool_comes_first_then_the_mcp_titles() -> None:
    own = _tool("mcp__docs__own")
    lookup = ToolRendererLookup(
        tools={"kv": _tool("kv"), "mcp__docs__own": own},
        mcp_labels={"mcp__my_docs__search": "my-docs/search"},
    )

    assert lookup.resolve("mcp__docs__own") is own
    assert lookup.resolve("mcp__my_docs__search") == McpToolRows("my-docs/search")
    assert lookup.resolve("mcp__my_docs__other") == McpToolRows("my_docs/other")
    # Pi: `expect(resolve("not_mcp")).toBeUndefined()`.
    assert lookup.resolve("not_mcp") is None
    assert ToolRendererLookup().resolve("mcp__docs__search") == McpToolRows(
        "docs/search"
    )


def _header(renderer: _ToolLoopRenderer, stream: io.StringIO, name: str, args: str):
    stream.seek(0)
    stream.truncate()
    renderer.render_tool_call(AgentToolCall("c", name, ProductContent(args)))
    return [line.strip() for line in stream.getvalue().splitlines() if line.strip()]


def test_a_title_has_no_characters_that_steer_the_terminal() -> None:
    # A server names its tools: this one would set the clipboard (OSC 52).
    hostile = "search\x1b]52;c;aGlqYWNrZWQ=\x07\x9b2J\u202e\n!"
    lookup = ToolRendererLookup(mcp_labels={"mcp__docs__search": f"docs/{hostile}"})

    resolved = lookup.resolve("mcp__docs__search")

    assert resolved == McpToolRows("docs/search ]52;c;aGlqYWNrZWQ=  2J  !")

    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    renderer.refresh_tool_renderers(lookup)
    assert _header(renderer, stream, "mcp__docs__search", "{}") == [
        "docs/search ]52;c;aGlqYWNrZWQ=  2J  !"
    ]
    assert "\x1b" not in stream.getvalue() and "\x07" not in stream.getvalue()


def test_a_mapping_of_extension_tools_becomes_a_lookup() -> None:
    tools = {"kv": _tool("kv")}
    lookup = as_tool_renderer_lookup(tools)
    tools.clear()

    assert lookup.resolve("kv") is not None
    assert as_tool_renderer_lookup(lookup) is lookup
    assert as_tool_renderer_lookup(None) == ToolRendererLookup()


def test_the_line_renderer_titles_mcp_calls_too() -> None:
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)

    assert _header(
        renderer, stream, "mcp__docs__search", '{"query": "pi", "n": 2}'
    ) == ['docs/search query="pi" n=2']
    assert _header(renderer, stream, "mcp__docs__search", "{}") == ["docs/search"]
    assert _header(renderer, stream, "not_mcp", '{"query": "pi"}') == [
        'not_mcp(query="pi")'
    ]

    renderer.refresh_tool_renderers(
        ToolRendererLookup(
            tools={"mcp__docs__own": _tool("mcp__docs__own")},
            mcp_labels={"mcp__docs__search": "my docs/search"},
        )
    )
    assert _header(renderer, stream, "mcp__docs__search", "{}") == ["my docs/search"]
    assert _header(renderer, stream, "mcp__docs__own", "{}") == ["CALL:mcp__docs__own"]
