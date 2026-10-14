"""Tests for the resources of one MCP server connection (MCP parity plan S23).

Row RES-10 (what a connection knows of a server's resources and when it reads
them again), the resource requests the three resource tools make through a
connection, and the part of RES-06 that is the connection's: a server without
``resources/templates/list`` has no templates.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from test_native_mcp_connection import (
    _NO_ANSWER,
    _WAIT_SECONDS,
    _Harness,
    _RpcError,
    _Server,
    _tool,
    _wait_until,
)

from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.connection import ConnectionView
from pipy_harness.native.mcp.protocol import (
    JsonRpcMessage,
    McpAbortError,
    McpError,
)

_INTRO = {"uri": "docs://intro", "name": "intro", "mimeType": "text/markdown"}
_VIEWER = {
    "uri": "ui://docs/viewer",
    "name": "viewer",
    "mimeType": "text/html;profile=mcp-app",
}
_PAGE = {"uriTemplate": "docs://pages/{slug}", "name": "page"}
_APP_TEMPLATE = {"uriTemplate": "ui://docs/{view}", "name": "view"}


class _ResourceServer(_Server):
    """An in-memory server that also answers the resource methods.

    ``resources``, ``templates`` and ``contents`` may be changed while it
    runs. ``templates = None`` makes it answer "method not found".
    """

    def __init__(
        self,
        *,
        resources: list[dict[str, Any]] | None = None,
        templates: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        capabilities: dict[str, Any] | None = None,
        handlers: dict[str, Any] | None = None,
    ) -> None:
        self.resources: list[dict[str, Any]] = (
            [_INTRO, _VIEWER] if resources is None else resources
        )
        self.templates: list[dict[str, Any]] | None = (
            [_PAGE, _APP_TEMPLATE] if templates is None else templates
        )
        self.reads: list[str] = []
        super().__init__(
            capabilities={"tools": {}, "resources": {}}
            if capabilities is None
            else capabilities,
            tools=tools,
            handlers={
                "resources/list": self._list_resources,
                "resources/templates/list": self._list_templates,
                "resources/read": self._read,
                **(handlers or {}),
            },
        )

    def _list_resources(self, message: JsonRpcMessage) -> object:
        return {"resources": list(self.resources)}

    def _list_templates(self, message: JsonRpcMessage) -> object:
        if self.templates is None:
            raise _RpcError(-32601, "Method not found")
        return {"resourceTemplates": list(self.templates)}

    def _read(self, message: JsonRpcMessage) -> object:
        params = message["params"]
        assert isinstance(params, dict)
        uri = str(params["uri"])
        self.reads.append(uri)
        return {
            "contents": [{"uri": uri, "mimeType": "text/markdown", "text": f"# {uri}"}]
        }


def _refresh_threads() -> list[str]:
    return sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("mcp-refresh")
    )


def _uris(view: ConnectionView) -> list[str]:
    return [str(resource["uri"]) for resource in view.resources]


# -- RES-10: at connect -------------------------------------------------------


def test_a_connection_lists_the_resources_when_it_connects() -> None:
    server = _ResourceServer(tools=[_tool("echo")])
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    assert (connection.has_resources, connection.view().has_resources) == (False, False)

    connection.get_client()

    view = connection.view()
    assert view.has_resources and connection.has_resources
    # Without the MCP App resource and template.
    assert view.resources == (_INTRO,)
    assert view.resource_templates == (_PAGE,)
    assert sorted(server.methods) == [
        "initialize",
        "resources/list",
        "resources/templates/list",
        "tools/list",
    ]
    assert harness.tool_reports == [("echo",)]
    connection.close()


def test_a_server_without_the_capability_is_not_asked_for_resources() -> None:
    server = _ResourceServer(tools=[_tool("echo")], capabilities={"tools": {}})
    harness = _Harness([lambda: server.client_end])

    harness.connection.get_client()

    view = harness.connection.view()
    assert (view.has_resources, view.resources, view.resource_templates) == (
        False,
        (),
        (),
    )
    assert server.methods == ["initialize", "tools/list"]
    harness.connection.close()


def test_a_server_with_resources_only_connects_without_listing_tools() -> None:
    server = _ResourceServer(capabilities={"resources": {}})
    harness = _Harness([lambda: server.client_end])

    harness.connection.get_client()

    view = harness.connection.view()
    assert (view.state, view.tools, view.has_resources) == ("connected", (), True)
    assert "tools/list" not in server.methods
    harness.connection.close()


def test_a_server_whose_listings_fail_still_connects() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "not now")

    server = _ResourceServer(
        tools=[_tool("echo")],
        handlers={"resources/list": refuse, "resources/templates/list": refuse},
    )
    harness = _Harness([lambda: server.client_end])

    harness.connection.get_client()

    view = harness.connection.view()
    assert (view.state, view.error) == ("connected", None)
    # The capability counts, so the resource tools can still ask on demand.
    assert (view.has_resources, view.resources, view.resource_templates) == (
        True,
        (),
        (),
    )
    harness.connection.close()


def test_a_server_without_the_templates_method_has_no_templates() -> None:
    server = _ResourceServer()
    server.templates = None
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection

    connection.get_client()

    view = connection.view()
    assert (view.resources, view.resource_templates) == ((_INTRO,), ())
    # RES-06, for the requests of the resource tools.
    assert connection.all_resource_templates() == []
    assert connection.resource_templates_page() == {"resourceTemplates": []}
    connection.close()


def test_another_templates_error_is_the_error_of_the_request() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "templates are broken")

    server = _ResourceServer(handlers={"resources/templates/list": refuse})
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    with pytest.raises(McpError, match="templates are broken"):
        connection.all_resource_templates()
    with pytest.raises(McpError, match="templates are broken"):
        connection.resource_templates_page()
    connection.close()


def test_the_resources_are_listed_next_to_the_tools() -> None:
    # Pi lists both at the same time, so a slow tool listing does not come
    # on top of a slow resource listing. This server answers the first of
    # the two requests only when the second has arrived.
    held: list[tuple[JsonRpcMessage, object]] = []

    def together(result: object) -> Any:
        def handler(message: JsonRpcMessage) -> object:
            if not held:
                held.append((message, result))
                return _NO_ANSWER
            first, first_result = held.pop()
            server.end.send(
                {"jsonrpc": "2.0", "id": first["id"], "result": first_result}
            )
            return result

        return handler

    server = _ResourceServer(
        handlers={
            "tools/list": together({"tools": [_tool("echo")]}),
            "resources/list": together({"resources": [_INTRO]}),
        }
    )
    harness = _Harness(
        [lambda: server.client_end], config={"command": "unused", "timeout": 5}
    )

    harness.connection.get_client()

    view = harness.connection.view()
    assert (len(view.tools), view.resources) == (1, (_INTRO,))
    harness.connection.close()


def test_a_failed_tool_listing_fails_the_connect_of_a_resource_server() -> None:
    def refuse(message: JsonRpcMessage) -> object:
        raise _RpcError(-32000, "no tools today")

    server = _ResourceServer(handlers={"tools/list": refuse})
    harness = _Harness([lambda: server.client_end])

    with pytest.raises(Exception, match="no tools today"):
        harness.connection.get_client()

    view = harness.connection.view()
    assert (view.state, view.has_resources) == ("failed", False)
    harness.connection.close()


# -- RES-10: changes -----------------------------------------------------------


def test_a_resource_list_change_lists_the_resources_again() -> None:
    server = _ResourceServer(tools=[_tool("echo")])
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()
    tools_before = connection.view().tools

    server.resources = [_INTRO, {"uri": "docs://faq", "name": "faq"}]
    server.templates = []
    server.notify("notifications/resources/list_changed")

    _wait_until(lambda: _uris(connection.view()) == ["docs://intro", "docs://faq"])
    _wait_until(lambda: len(harness.tool_reports) == 2)
    view = connection.view()
    assert view.resource_templates == ()
    assert (view.state, view.error) == ("connected", None)
    # The tools were not listed again and are the same objects.
    assert view.tools is tools_before
    assert server.methods.count("tools/list") == 1
    connection.close()


def test_a_tool_change_and_a_resource_change_are_both_read() -> None:
    server = _ResourceServer(tools=[_tool("one")])
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    server.tools = [_tool("one"), _tool("two")]
    server.resources = []
    server.notify("notifications/resources/list_changed")
    server.notify("notifications/tools/list_changed")

    _wait_until(lambda: len(connection.view().tools) == 2)
    _wait_until(lambda: connection.view().resources == ())
    connection.close()


def test_a_slow_resource_listing_does_not_delay_a_tool_refresh() -> None:
    # Pi `connectOnce`: each notification starts `refreshTools` or
    # `refreshResources` on its own.
    stalled = False

    def list_resources(message: JsonRpcMessage) -> object:
        return _NO_ANSWER if stalled else {"resources": [_INTRO]}

    server = _ResourceServer(
        tools=[_tool("one")], handlers={"resources/list": list_resources}
    )
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    stalled = True
    server.notify("notifications/resources/list_changed")
    _wait_until(lambda: server.methods.count("resources/list") == 2)
    server.tools = [_tool("one"), _tool("two")]
    server.notify("notifications/tools/list_changed")

    # The resource listing is still unanswered.
    _wait_until(lambda: len(connection.view().tools) == 2)
    assert connection.view().resources == (_INTRO,)
    # The thread that listed the other kind ends on its own.
    _wait_until(lambda: _refresh_threads() == ["mcp-refresh-resources"])
    connection.close()
    _wait_until(lambda: not _refresh_threads())


def test_a_slow_tool_listing_does_not_delay_a_resource_refresh() -> None:
    stalled = False

    def list_tools(message: JsonRpcMessage) -> object:
        return _NO_ANSWER if stalled else {"tools": [_tool("one")]}

    server = _ResourceServer(handlers={"tools/list": list_tools})
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    stalled = True
    server.notify("notifications/tools/list_changed")
    _wait_until(lambda: server.methods.count("tools/list") == 2)
    server.resources = []
    server.notify("notifications/resources/list_changed")

    _wait_until(lambda: connection.view().resources == ())
    assert len(connection.view().tools) == 1
    # The thread that listed the other kind ends on its own.
    _wait_until(lambda: _refresh_threads() == ["mcp-refresh"])
    connection.close()
    _wait_until(lambda: not _refresh_threads())


def test_a_resource_change_during_a_resource_listing_is_listed_once_more() -> None:
    # One listing of a kind at a time; a change that arrives meanwhile is
    # read by one more.
    listings = 0

    def list_resources(message: JsonRpcMessage) -> object:
        nonlocal listings
        listings += 1
        if listings == 2:
            # The list changes after this answer was put together.
            server.notify("notifications/resources/list_changed")
        return {"resources": [_INTRO] * listings}

    server = _ResourceServer(handlers={"resources/list": list_resources})
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    server.notify("notifications/resources/list_changed")

    _wait_until(lambda: len(connection.view().resources) == 3)
    _wait_until(lambda: not _refresh_threads())
    assert listings == 3
    connection.close()


def test_changes_of_both_kinds_announced_while_connecting_are_both_read() -> None:
    listed = {"tools/list": 0, "resources/list": 0}

    def list_tools(message: JsonRpcMessage) -> object:
        listed["tools/list"] += 1
        if listed["tools/list"] == 1:
            server.notify("notifications/tools/list_changed")
            return {"tools": [_tool("old")]}
        return {"tools": [_tool("new")]}

    def list_resources(message: JsonRpcMessage) -> object:
        listed["resources/list"] += 1
        if listed["resources/list"] == 1:
            server.notify("notifications/resources/list_changed")
            return {"resources": []}
        return {"resources": [_INTRO]}

    server = _ResourceServer(
        handlers={"tools/list": list_tools, "resources/list": list_resources}
    )
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()

    _wait_until(lambda: connection.view().resources == (_INTRO,))
    _wait_until(lambda: [tool["name"] for tool in connection.view().tools] == ["new"])
    _wait_until(lambda: not _refresh_threads())
    assert listed == {"tools/list": 2, "resources/list": 2}
    connection.close()


def test_a_failed_resource_refresh_empties_the_lists_and_sets_no_error() -> None:
    # Pi `refreshResources`: a failed listing is an empty one.
    fail = False

    def list_resources(message: JsonRpcMessage) -> object:
        if fail:
            raise _RpcError(-32000, "gone")
        return {"resources": [_INTRO]}

    server = _ResourceServer(handlers={"resources/list": list_resources})
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection
    connection.get_client()
    assert connection.view().resources == (_INTRO,)

    fail = True
    server.notify("notifications/resources/list_changed")

    _wait_until(lambda: connection.view().resources == ())
    view = connection.view()
    assert (view.state, view.error, view.has_resources) == ("connected", None, True)
    connection.close()


def test_a_dropped_server_keeps_what_is_known_of_its_resources() -> None:
    # RES-01: the server keeps counting while it is disconnected.
    first = _ResourceServer()
    second = _ResourceServer(resources=[{"uri": "docs://new", "name": "new"}])
    harness = _Harness([lambda: first.client_end, lambda: second.client_end])
    connection = harness.connection
    connection.get_client()

    first.end.close()
    _wait_until(lambda: connection.state == "disconnected")

    view = connection.view()
    assert view.has_resources and view.resources == (_INTRO,)
    # CON-06: a resource request reconnects.
    assert connection.read_resource("docs://new") == {
        "contents": [
            {"uri": "docs://new", "mimeType": "text/markdown", "text": "# docs://new"}
        ]
    }
    assert connection.state == "connected"
    assert _uris(connection.view()) == ["docs://new"]
    assert (first.reads, second.reads) == ([], ["docs://new"])
    connection.close()


# -- the requests of the resource tools ---------------------------------------


def test_resource_requests_go_through_the_connection() -> None:
    cursors: list[object] = []

    def list_resources(message: JsonRpcMessage) -> object:
        params = message.get("params")
        cursor = params.get("cursor") if isinstance(params, dict) else None
        cursors.append(cursor)
        if cursor is None:
            return {"resources": [_INTRO], "nextCursor": "p2"}
        return {"resources": [{"uri": "docs://faq"}]}

    server = _ResourceServer(handlers={"resources/list": list_resources})
    harness = _Harness([lambda: server.client_end])
    connection = harness.connection

    # The first request connects.
    page = connection.resources_page()

    assert page == {"resources": [_INTRO], "nextCursor": "p2"}
    assert connection.resources_page("p2") == {
        # A resource without a name is named by its URI.
        "resources": [{"uri": "docs://faq", "name": "docs://faq"}]
    }
    assert [resource["uri"] for resource in connection.all_resources()] == [
        "docs://intro",
        "docs://faq",
    ]
    # The lists are the server's: MCP App entries are the tools' to leave out.
    assert connection.all_resource_templates() == [_PAGE, _APP_TEMPLATE]
    assert connection.resource_templates_page() == {
        "resourceTemplates": [_PAGE, _APP_TEMPLATE]
    }
    assert connection.read_resource("docs://intro") == {
        "contents": [
            {
                "uri": "docs://intro",
                "mimeType": "text/markdown",
                "text": "# docs://intro",
            }
        ]
    }
    connection.close()


def test_a_cancelled_resource_request_does_not_wait_for_the_connect() -> None:
    release = threading.Event()

    def slow_initialize(message: JsonRpcMessage) -> object:
        release.wait(_WAIT_SECONDS)
        return server._default(message)

    server = _ResourceServer(handlers={"initialize": slow_initialize})
    harness = _Harness([lambda: server.client_end])
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(McpAbortError):
        harness.connection.read_resource(
            "docs://intro", McpRequestOptions(cancel_event=cancel)
        )

    release.set()
    harness.connection.close()
