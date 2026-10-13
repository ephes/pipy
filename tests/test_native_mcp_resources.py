"""Tests for the three MCP resource tools (MCP parity plan S23).

Rows RES-03 to RES-09 at the level of the tool ports, against stand-in
servers. That the tools exist, with which exposure and for which servers
(RES-01, RES-02), is in ``test_native_mcp_manager_resources.py``; the session
level in ``test_native_mcp_session_resources.py``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_tools import _failing_saver, _saver

from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.codemode.declarations import render_tool_signature
from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.protocol import McpAbortError
from pipy_harness.native.mcp.resources import (
    LIST_MCP_RESOURCE_TEMPLATES_TOOL,
    LIST_MCP_RESOURCES_TOOL,
    McpResourceToolPort,
    create_mcp_resource_tools,
    is_mcp_app_resource,
    settle_all,
)
from pipy_harness.native.mcp.tools import MCP_OUTPUT_MAX_BYTES, READ_MCP_RESOURCE_TOOL
from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolContext,
    ToolExecutionResult,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.foreign_schema import validate_foreign_arguments


class _Resources:
    """A stand-in for a server connection that offers resources."""

    def __init__(
        self,
        name: str,
        *,
        pages: list[list[dict[str, Any]]] | None = None,
        templates: list[dict[str, Any]] | None = None,
        contents: dict[str, list[dict[str, Any]]] | None = None,
        error: Exception | None = None,
        timeout_ms: float = 5_000,
    ) -> None:
        self.name = name
        self.timeout_ms = timeout_ms
        self.pages = pages if pages is not None else [[]]
        self.templates = templates or []
        self.contents = contents or {}
        self.error = error
        self.calls: list[tuple[str, object, McpRequestOptions | None]] = []

    def _called(self, what: str, argument: object, options: Any) -> None:
        self.calls.append((what, argument, options))
        if self.error is not None:
            raise self.error

    def resources_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> dict[str, Any]:
        self._called("page", cursor, options)
        index = 0 if cursor is None else int(cursor)
        page: dict[str, Any] = {"resources": self.pages[index]}
        if index + 1 < len(self.pages):
            page["nextCursor"] = str(index + 1)
        return page

    def resource_templates_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> dict[str, Any]:
        self._called("templates-page", cursor, options)
        return {"resourceTemplates": self.templates}

    def all_resources(
        self, options: McpRequestOptions | None = None
    ) -> list[dict[str, Any]]:
        self._called("all", None, options)
        return [item for page in self.pages for item in page]

    def all_resource_templates(
        self, options: McpRequestOptions | None = None
    ) -> list[dict[str, Any]]:
        self._called("all-templates", None, options)
        return list(self.templates)

    def read_resource(
        self, uri: str, options: McpRequestOptions | None = None
    ) -> dict[str, Any]:
        self._called("read", uri, options)
        return {"contents": self.contents[uri]}


def _tools(
    *servers: _Resources, exposure: str = "direct", save_output: Any = None
) -> dict[str, McpResourceToolPort]:
    extra = {} if save_output is None else {"save_output": save_output}
    return {
        port.definition.name: port
        for port in create_mcp_resource_tools(
            exposure=exposure, servers=lambda: list(servers), **extra
        )
    }


def _call(
    port: McpResourceToolPort,
    arguments: dict[str, Any] | None = None,
    *,
    cancel_event: threading.Event | None = None,
) -> ToolExecutionResult:
    return port.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name=port.definition.name,
            arguments=arguments or {},
        ),
        ToolContext(workspace_root=Path.cwd(), cancel_event=cancel_event),
    )


def _intro() -> dict[str, Any]:
    return {
        "uri": "docs://intro",
        "name": "intro",
        "mimeType": "text/markdown",
        "_meta": {"x": 1},
        "icons": [{"src": "data:image/png;base64,AAAA"}],
    }


_VIEWER = {
    "uri": "ui://docs/viewer",
    "name": "viewer",
    "mimeType": "text/html;profile=mcp-app",
}


# -- RES-03: what the tools declare --------------------------------------------


def test_the_three_tools_have_pis_names_schemas_and_the_read_only_hint() -> None:
    tools = _tools()

    assert list(tools) == [
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    ]
    assert (LIST_MCP_RESOURCES_TOOL, LIST_MCP_RESOURCE_TEMPLATES_TOOL) == (
        "list_mcp_resources",
        "list_mcp_resource_templates",
    )
    for port in tools.values():
        definition = port.definition
        assert definition.annotations == ToolAnnotations(read_only_hint=True)
        assert definition.exposure == "direct"
        assert definition.namespace is None
        assert definition.input_schema["additionalProperties"] is False
    listing = tools["list_mcp_resources"].definition
    assert listing.description == (
        "Lists resources provided by MCP servers. Resources allow servers to "
        "share data that provides context to language models, such as files, "
        "database schemas, or application-specific information. Prefer "
        "resources over web search when possible."
    )
    assert listing.input_schema["properties"] == {
        "server": {
            "type": "string",
            "description": "MCP server name. Omit to list every server with resources.",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque cursor from a previous call with the same "
            "server; omit for the first page.",
        },
    }
    assert "required" not in listing.input_schema
    assert listing.output_schema is not None
    assert listing.output_schema["required"] == ["resources"]
    assert listing.output_schema["properties"]["resources"]["items"]["required"] == [
        "server",
        "uri",
        "name",
    ]
    templates = tools["list_mcp_resource_templates"].definition
    assert templates.description.startswith(
        "Lists resource templates provided by MCP servers. Parameterized "
        "resource templates allow servers"
    )
    assert templates.input_schema == listing.input_schema
    assert templates.output_schema is not None
    assert templates.output_schema["required"] == ["resourceTemplates"]
    item = templates.output_schema["properties"]["resourceTemplates"]["items"]
    assert item["properties"]["uriTemplate"] == {
        "type": "string",
        "description": "RFC 6570 URI template",
    }
    read = tools["read_mcp_resource"].definition
    assert read.description == (
        "Read a specific resource from an MCP server given the server name "
        "and resource URI."
    )
    assert read.input_schema["required"] == ["server", "uri"]
    assert read.input_schema["properties"]["server"]["description"] == (
        "MCP server name exactly as configured. Must match the 'server' field "
        "returned by list_mcp_resources."
    )
    assert read.input_schema["properties"]["uri"]["description"] == (
        "Resource URI to read. Must be one of the URIs returned by list_mcp_resources."
    )
    assert read.output_schema is not None
    assert read.output_schema["required"] == ["server", "uri", "contents"]
    blob = read.output_schema["properties"]["contents"]["items"]["anyOf"][1]
    assert blob["properties"]["blob"] == {"type": "string", "description": "base64"}


@pytest.mark.parametrize(
    ("exposure", "expected"),
    [
        ("direct", "direct"),
        ("codemode", "deferred"),
        ("deferred", "deferred"),
        ("hidden", "hidden"),
    ],
)
def test_the_tools_take_the_given_mcp_exposure(exposure: str, expected: str) -> None:
    for port in _tools(exposure=exposure).values():
        assert port.definition.exposure == expected


def test_the_schemas_check_arguments_and_render_for_scripts() -> None:
    tools = _tools()
    read = tools["read_mcp_resource"].definition

    with pytest.raises(ValueError):
        validate_foreign_arguments(
            tool_name=read.name, schema=read.input_schema, arguments={"server": "docs"}
        )
    with pytest.raises(ValueError):
        validate_foreign_arguments(
            tool_name=read.name,
            schema=read.input_schema,
            arguments={"server": "docs", "uri": "a", "extra": 1},
        )
    signature = render_tool_signature(read.name, read.input_schema, read.output_schema)
    assert "read_mcp_resource(" in signature
    assert '"contents"' in signature


# -- RES-04, RES-05: list_mcp_resources -----------------------------------------


def test_one_server_lists_one_page_and_the_cursor_continues() -> None:
    docs = _Resources(
        "docs",
        pages=[[_intro(), _VIEWER], [{"uri": "docs://faq", "name": "faq", "size": 12}]],
        timeout_ms=1_500,
    )
    port = _tools(docs)["list_mcp_resources"]

    first = _call(port, {"server": " docs "})

    assert not first.is_error
    payload = {
        "server": "docs",
        # No `_meta`, no icons, no MCP App resource; tagged with the server.
        "resources": [
            {
                "server": "docs",
                "uri": "docs://intro",
                "name": "intro",
                "mimeType": "text/markdown",
            }
        ],
        "nextCursor": "1",
    }
    assert first.structured_content == payload
    # RES-07: the model receives one compact JSON text.
    assert first.output_text == json.dumps(payload, separators=(",", ":"))
    assert first.details == {"server": "docs", "tool": "list_mcp_resources"}

    second = _call(port, {"server": "docs", "cursor": "1"})

    assert second.structured_content == {
        "server": "docs",
        "resources": [
            {"server": "docs", "uri": "docs://faq", "name": "faq", "size": 12}
        ],
    }
    assert [(what, argument) for what, argument, _ in docs.calls] == [
        ("page", None),
        ("page", "1"),
    ]
    options = docs.calls[0][2]
    assert options is not None and options.timeout_ms == 1_500


def test_without_a_server_every_page_of_every_server_is_listed_in_name_order() -> None:
    zeta = _Resources("zeta", pages=[[{"uri": "z://1", "name": "z1"}]])
    alpha = _Resources(
        "Alpha",
        pages=[[{"uri": "a://1", "name": "a1"}], [{"uri": "a://2", "name": "a2"}]],
    )
    broken = _Resources("broken", error=RuntimeError("no answer"))
    cancel = threading.Event()
    port = _tools(zeta, broken, alpha)["list_mcp_resources"]

    result = _call(port, {}, cancel_event=cancel)

    assert not result.is_error
    assert result.structured_content == {
        "resources": [
            {"server": "Alpha", "uri": "a://1", "name": "a1"},
            {"server": "Alpha", "uri": "a://2", "name": "a2"},
            {"server": "zeta", "uri": "z://1", "name": "z1"},
        ],
        "errors": [{"server": "broken", "error": "no answer"}],
    }
    assert json.loads(result.output_text) == result.structured_content
    assert result.details == {"server": "", "tool": "list_mcp_resources"}
    assert [what for what, _, _ in alpha.calls] == ["all"]
    # The call's cancel event and the server's timeout reach every request.
    for server in (zeta, alpha, broken):
        options = server.calls[0][2]
        assert options is not None and options.cancel_event is cancel
        assert options.timeout_ms == server.timeout_ms


def test_without_errors_the_listing_has_no_errors_key() -> None:
    port = _tools(_Resources("docs", pages=[[_VIEWER]]))["list_mcp_resources"]

    result = _call(port)

    assert result.structured_content == {"resources": []}
    assert result.output_text == '{"resources":[]}'


def test_a_cursor_needs_a_server() -> None:
    docs = _Resources("docs")
    port = _tools(docs)["list_mcp_resources"]

    for arguments in ({"cursor": "1"}, {"server": "  ", "cursor": "1"}):
        result = _call(port, arguments)
        assert result.is_error
        assert result.output_text == (
            "cursor can only be used when a server is specified"
        )
        assert result.structured_content is None
    # An empty cursor counts as none.
    assert not _call(port, {"cursor": " "}).is_error
    assert [what for what, _, _ in docs.calls] == ["all"]


def test_arguments_that_are_not_strings_are_refused() -> None:
    tools = _tools(_Resources("docs"))

    assert _call(tools["list_mcp_resources"], {"server": 3}).output_text == (
        "server must be a string"
    )
    assert _call(tools["list_mcp_resources"], {"cursor": ["a"]}).output_text == (
        "cursor must be a string"
    )
    assert _call(
        tools["read_mcp_resource"], {"server": "docs", "uri": 1}
    ).output_text == ("uri must be a string")


def test_a_failed_page_is_the_error_of_the_call() -> None:
    docs = _Resources("docs", error=RuntimeError("server went away"))

    result = _call(_tools(docs)["list_mcp_resources"], {"server": "docs"})

    assert (result.is_error, result.output_text) == (True, "server went away")
    assert result.structured_content is None


def test_a_cancelled_listing_is_an_error_result() -> None:
    docs = _Resources("docs", error=McpAbortError())

    result = _call(_tools(docs)["list_mcp_resources"], {"server": "docs"})

    assert result.is_error and result.output_text


# -- RES-06: list_mcp_resource_templates ---------------------------------------


def test_templates_are_listed_like_resources() -> None:
    page = {
        "uriTemplate": "docs://pages/{slug}",
        "name": "page",
        "icons": [{"src": "data:image/png;base64,AAAA"}],
    }
    app = {"uriTemplate": "ui://docs/{view}", "name": "view"}
    docs = _Resources("docs", templates=[page, app])
    other = _Resources("other")
    port = _tools(docs, other)["list_mcp_resource_templates"]

    one = _call(port, {"server": "docs"})
    every = _call(port)

    listed = [{"server": "docs", "uriTemplate": "docs://pages/{slug}", "name": "page"}]
    assert one.structured_content == {"server": "docs", "resourceTemplates": listed}
    assert one.details == {"server": "docs", "tool": "list_mcp_resource_templates"}
    assert every.structured_content == {"resourceTemplates": listed}
    assert [what for what, _, _ in docs.calls] == ["templates-page", "all-templates"]
    cursor = _call(port, {"cursor": "x"})
    assert cursor.output_text == "cursor can only be used when a server is specified"


# -- RES-07: the size rule ---------------------------------------------------------


def test_a_long_listing_is_cut_in_the_middle_for_the_model_only() -> None:
    saved: list[tuple[str | bytes, str]] = []
    items = [{"uri": f"docs://page/{n}", "name": "p" * 200} for n in range(200)]
    docs = _Resources("docs", pages=[items])
    port = _tools(docs, save_output=_saver(saved))["list_mcp_resources"]

    result = _call(port, {"server": "docs"})

    assert not result.is_error
    assert result.output_text.startswith("Warning: truncated output")
    assert "[Full output: /tmp/saved.txt (read it with offset/limit)]" in (
        result.output_text
    )
    assert result.details == {
        "server": "docs",
        "tool": "list_mcp_resources",
        "fullOutputPath": "/tmp/saved.txt",
    }
    full, extension = saved[0]
    assert extension == ".txt" and isinstance(full, str)
    assert len(full.encode()) > MCP_OUTPUT_MAX_BYTES
    # Scripts receive the whole object.
    assert json.loads(full) == result.structured_content
    assert isinstance(result.structured_content, dict)
    assert len(result.structured_content["resources"]) == 200


# -- RES-08, RES-09: read_mcp_resource -------------------------------------------


def test_read_needs_a_server_and_a_uri() -> None:
    port = _tools(_Resources("docs"))["read_mcp_resource"]

    assert _call(port, {"uri": "docs://x"}).output_text == "server must be provided"
    assert _call(port, {"server": " ", "uri": "docs://x"}).output_text == (
        "server must be provided"
    )
    assert _call(port, {"server": "docs"}).output_text == "uri must be provided"
    assert _call(port, {"server": "docs", "uri": ""}).output_text == (
        "uri must be provided"
    )
    assert _call(port, {"server": None, "uri": None}).is_error


def test_an_unknown_server_names_the_servers_with_resources() -> None:
    tools = _tools(_Resources("docs"), _Resources("wiki"))

    result = _call(tools["read_mcp_resource"], {"server": "nope", "uri": "docs://x"})

    assert result.is_error
    assert result.output_text == (
        'MCP server "nope" has no resources. Servers with resources: docs, wiki'
    )
    listing = _call(tools["list_mcp_resources"], {"server": "nope"})
    assert listing.output_text == result.output_text
    alone = _call(_tools()["read_mcp_resource"], {"server": "nope", "uri": "docs://x"})
    assert alone.output_text == 'MCP server "nope" has no resources'


def test_text_is_read_as_text_and_scripts_get_the_contents_without_meta() -> None:
    docs = _Resources(
        "docs",
        contents={
            "docs://pages/setup": [
                {
                    "uri": "docs://pages/setup",
                    "mimeType": "text/markdown",
                    "text": "# Setup",
                    "_meta": {"etag": "1"},
                }
            ]
        },
        timeout_ms=2_000,
    )
    port = _tools(docs)["read_mcp_resource"]

    result = _call(port, {"server": "docs", "uri": " docs://pages/setup "})

    assert (result.is_error, result.output_text) == (False, "# Setup")
    assert result.structured_content == {
        "server": "docs",
        "uri": "docs://pages/setup",
        "contents": [
            {
                "uri": "docs://pages/setup",
                "mimeType": "text/markdown",
                "text": "# Setup",
            }
        ],
    }
    assert result.details == {"server": "docs", "tool": READ_MCP_RESOURCE_TOOL}
    what, uri, options = docs.calls[0]
    assert (what, uri) == ("read", "docs://pages/setup")
    assert options is not None and options.timeout_ms == 2_000


def test_several_contents_are_each_preceded_by_their_uri() -> None:
    saved: list[tuple[str | bytes, str]] = []
    docs = _Resources(
        "docs",
        contents={
            "file:///dir": [
                {"uri": "file:///dir/a.txt", "text": "alpha"},
                # A blob with a text type is decoded.
                {
                    "uri": "file:///dir/b.json",
                    "mimeType": "application/json",
                    "blob": "eyJhIjoxfQ==",
                },
                # Other binary content goes to a temp file.
                {
                    "uri": "file:///dir/report.pdf",
                    "mimeType": "application/pdf",
                    "blob": "JVBERg==",
                },
                # An image is an image (RES-09).
                {
                    "uri": "file:///dir/logo.png",
                    "mimeType": "image/png",
                    "blob": "AAAA",
                },
            ]
        },
    )
    port = _tools(docs, save_output=_saver(saved))["read_mcp_resource"]

    result = _call(port, {"server": "docs", "uri": "file:///dir"})

    assert result.output_text.split("\n") == [
        "file:///dir/a.txt:",
        "alpha",
        "file:///dir/b.json:",
        '{"a":1}',
        "file:///dir/report.pdf:",
        "[Binary resource file:///dir/report.pdf (application/pdf, 4B) "
        "saved to /tmp/saved.pdf]",
        "file:///dir/logo.png:",
    ]
    assert result.blocks[-2:] == (
        TextContent("file:///dir/logo.png:"),
        ImageContent("AAAA", "image/png"),
    )
    assert [type(block) for block in result.blocks[:-1]] == [TextContent] * 7
    assert saved == [(b"%PDF", ".pdf")]
    assert isinstance(result.structured_content, dict)
    assert [entry["uri"] for entry in result.structured_content["contents"]] == [
        "file:///dir/a.txt",
        "file:///dir/b.json",
        "file:///dir/report.pdf",
        "file:///dir/logo.png",
    ]


def test_a_binary_resource_that_cannot_be_saved_says_why() -> None:
    docs = _Resources(
        "docs",
        contents={
            "file:///r.bin": [
                {"uri": "file:///r.bin", "blob": "AAAA"},
            ]
        },
    )
    port = _tools(docs, save_output=_failing_saver)["read_mcp_resource"]

    result = _call(port, {"server": "docs", "uri": "file:///r.bin"})

    assert not result.is_error
    assert result.output_text.startswith(
        "[Binary resource file:///r.bin (unknown type, 3B) could not be saved: "
    )


def test_an_empty_resource_says_so() -> None:
    docs = _Resources("docs", contents={"docs://none": []})

    result = _call(
        _tools(docs)["read_mcp_resource"], {"server": "docs", "uri": "docs://none"}
    )

    assert (result.is_error, result.output_text) == (
        False,
        "Resource docs://none is empty.",
    )
    assert result.structured_content == {
        "server": "docs",
        "uri": "docs://none",
        "contents": [],
    }


def test_a_long_resource_is_cut_in_the_middle_and_saved() -> None:
    saved: list[tuple[str | bytes, str]] = []
    text = "line\n" * 10_000
    docs = _Resources(
        "docs", contents={"docs://big": [{"uri": "docs://big", "text": text}]}
    )
    port = _tools(docs, save_output=_saver(saved))["read_mcp_resource"]

    result = _call(port, {"server": "docs", "uri": "docs://big"})

    assert result.output_text.startswith("Warning: truncated output")
    assert result.details == {
        "server": "docs",
        "tool": "read_mcp_resource",
        "fullOutputPath": "/tmp/saved.txt",
    }
    assert saved == [(text, ".txt")]
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["contents"][0]["text"] == text


def test_a_failed_read_is_the_error_of_the_call() -> None:
    docs = _Resources("docs", error=RuntimeError("Resource not found: docs://x"))

    result = _call(
        _tools(docs)["read_mcp_resource"], {"server": "docs", "uri": "docs://x"}
    )

    assert (result.is_error, result.output_text) == (
        True,
        "Resource not found: docs://x",
    )
    assert result.details is None


def test_the_servers_are_asked_for_at_every_call() -> None:
    current: list[_Resources] = []
    tools = {
        port.definition.name: port
        for port in create_mcp_resource_tools(
            exposure="codemode", servers=lambda: list(current)
        )
    }
    arguments = {"server": "docs", "uri": "docs://a"}

    assert _call(tools["read_mcp_resource"], arguments).is_error
    current.append(
        _Resources("docs", contents={"docs://a": [{"uri": "docs://a", "text": "a"}]})
    )

    assert _call(tools["read_mcp_resource"], arguments).output_text == "a"


# -- helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"uri": "ui://docs/viewer"}, True),
        ({"uriTemplate": "ui://docs/{view}"}, True),
        ({"uri": "docs://a", "mimeType": "text/html;profile=mcp-app"}, True),
        ({"uri": "docs://a", "mimeType": 'text/html; PROFILE = "mcp-app"'}, True),
        ({"uri": "docs://a", "mimeType": "text/html"}, False),
        ({"uri": "docs://ui://x"}, False),
        ({"uri": "docs://a", "uriTemplate": "ui://x"}, False),
        ({"mimeType": 3}, False),
        ({}, False),
    ],
)
def test_mcp_app_resources_are_recognized(item: dict[str, Any], expected: bool) -> None:
    assert is_mcp_app_resource(item) is expected


def test_settle_all_runs_the_calls_at_the_same_time_and_keeps_their_order() -> None:
    barrier = threading.Barrier(3, timeout=10)

    def meet(value: int) -> int:
        barrier.wait()
        return value

    def fail() -> int:
        barrier.wait()
        raise ValueError("no")

    results = settle_all([lambda: meet(1), fail, lambda: meet(3)])

    assert results[0] == 1 and results[2] == 3
    assert isinstance(results[1], ValueError)
    assert settle_all([]) == []


def test_settle_all_runs_one_after_another_when_no_thread_starts(monkeypatch) -> None:
    def refuse(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)

    assert settle_all([lambda: 1, lambda: 2]) == [1, 2]
