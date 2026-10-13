"""MCP resources, through the tools Codex and OpenCode use.

Pi ``extensions/mcp/resources.ts``: ``list_mcp_resources``,
``list_mcp_resource_templates`` and ``read_mcp_resource``. They take a
``server`` argument and cover every server with resources, so models trained
on those tools use them unchanged.

Listings are JSON, as in Codex: ``{server?, resources: [{server,
...resource}], nextCursor?}``. With a ``server`` one page is listed and
``cursor`` continues it; without, every page of every server. MCP App
resources (``ui://`` URIs and ``profile=mcp-app`` HTML) are left out, since
they are user interfaces for hosts that render them, and so are icons. Read
resources become text for the model; binary resources are saved to temp
files. Scripts get the JSON payloads.

A pipy tool result carries text only, so an image resource is shown to the
model as ``[image <mime> omitted]`` for now.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any, Final, Protocol, TypeVar, cast

from pipy_harness.native.fuzzy import JS_WHITESPACE, js_trim
from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.protocol import JsonObject, LlmContent
from pipy_harness.native.mcp.tools import (
    MCP_OUTPUT_MAX_BYTES,
    READ_MCP_RESOURCE_TOOL,
    McpOutputSaver,
    limit_mcp_content,
    render_model_text,
    save_to_temp_file,
    to_model_content,
    to_tool_exposure,
)
from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.collation import locale_compare_key
from pipy_harness.native.tools.fs_errors import node_encodable_text
from pipy_harness.native.tools.truncate import truncate_middle

T = TypeVar("T")

LIST_MCP_RESOURCES_TOOL: Final = "list_mcp_resources"
LIST_MCP_RESOURCE_TEMPLATES_TOOL: Final = "list_mcp_resource_templates"

_JS_SPACE: Final = "[" + "".join(sorted(re.escape(ch) for ch in JS_WHITESPACE)) + "]*"
_MCP_APP_PROFILE: Final = re.compile(
    f';{_JS_SPACE}profile{_JS_SPACE}={_JS_SPACE}"?mcp-app"?', re.IGNORECASE
)


class McpResourceServer(Protocol):
    """A server that offers resources: a connection, or a stand-in for one."""

    @property
    def name(self) -> str: ...

    @property
    def timeout_ms(self) -> float: ...

    def resources_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject: ...

    def resource_templates_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject: ...

    def all_resources(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]: ...

    def all_resource_templates(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]: ...

    def read_resource(
        self, uri: str, options: McpRequestOptions | None = None
    ) -> JsonObject: ...


def is_mcp_app_resource(item: Mapping[str, object]) -> bool:
    """MCP App user interfaces, which only hosts that render them can use."""

    uri = item.get("uri")
    if uri is None:
        uri = item.get("uriTemplate")
    mime_type = item.get("mimeType")
    return (isinstance(uri, str) and uri.startswith("ui://")) or (
        isinstance(mime_type, str) and _MCP_APP_PROFILE.search(mime_type) is not None
    )


def settle_all(calls: Sequence[Callable[[], T]]) -> list[T | Exception]:
    """Run ``calls`` at the same time and wait for all of them.

    Returns, in the order of the calls, what each returned or the exception
    it raised (``Promise.allSettled``). The first call runs on the caller's
    thread, each other one on a daemon thread of its own; a call for which
    no thread can be started runs on the caller's thread as well.
    """

    results: list[T | Exception] = [RuntimeError("not run")] * len(calls)

    def run(index: int) -> None:
        try:
            results[index] = calls[index]()
        except Exception as error:  # noqa: BLE001 - the exception is the call's result
            results[index] = error

    threads: list[threading.Thread] = []
    for index in range(1, len(calls)):
        thread = threading.Thread(
            target=run, args=(index,), name="mcp-request", daemon=True
        )
        try:
            thread.start()
        except RuntimeError:
            run(index)
        else:
            threads.append(thread)
    if calls:
        run(0)
    for thread in threads:
        thread.join()
    return results


def _listed(server: str, item: Mapping[str, object]) -> dict[str, object]:
    """A listed resource or template without ``_meta`` and icons, tagged with
    its server."""

    rest = {key: value for key, value in item.items() if key not in ("_meta", "icons")}
    return {"server": server, **rest}


def _string_property(description: str) -> dict[str, object]:
    return {"type": "string", "description": description}


_LIST_PARAMETERS: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "server": _string_property(
            "MCP server name. Omit to list every server with resources."
        ),
        "cursor": _string_property(
            "Opaque cursor from a previous call with the same server; "
            "omit for the first page."
        ),
    },
    "additionalProperties": False,
}
_READ_PARAMETERS: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "server": _string_property(
            "MCP server name exactly as configured. Must match the 'server' "
            "field returned by list_mcp_resources."
        ),
        "uri": _string_property(
            "Resource URI to read. Must be one of the URIs returned by "
            "list_mcp_resources."
        ),
    },
    "required": ["server", "uri"],
    "additionalProperties": False,
}

_STRING: Final[Mapping[str, object]] = {"type": "string"}
_LISTING_ERRORS: Final[Mapping[str, object]] = {
    "type": "array",
    "description": "Servers that could not be listed",
    "items": {
        "type": "object",
        "properties": {"server": _STRING, "error": _STRING},
        "required": ["server", "error"],
    },
}
_LIST_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "server": _STRING,
        "resources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "server": _STRING,
                    "uri": _STRING,
                    "name": _STRING,
                    "title": _STRING,
                    "description": _STRING,
                    "mimeType": _STRING,
                    "size": {"type": "number"},
                },
                "required": ["server", "uri", "name"],
            },
        },
        "nextCursor": _STRING,
        "errors": _LISTING_ERRORS,
    },
    "required": ["resources"],
}
_LIST_TEMPLATES_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "server": _STRING,
        "resourceTemplates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "server": _STRING,
                    "uriTemplate": _string_property("RFC 6570 URI template"),
                    "name": _STRING,
                    "title": _STRING,
                    "description": _STRING,
                    "mimeType": _STRING,
                },
                "required": ["server", "uriTemplate", "name"],
            },
        },
        "nextCursor": _STRING,
        "errors": _LISTING_ERRORS,
    },
    "required": ["resourceTemplates"],
}
_READ_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "server": _STRING,
        "uri": _STRING,
        "contents": {
            "type": "array",
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "uri": _STRING,
                            "mimeType": _STRING,
                            "text": _STRING,
                        },
                        "required": ["uri", "text"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "uri": _STRING,
                            "mimeType": _STRING,
                            "blob": _string_property("base64"),
                        },
                        "required": ["uri", "blob"],
                    },
                ]
            },
        },
    },
    "required": ["server", "uri", "contents"],
}

_LIST_DESCRIPTION: Final = (
    "Lists resources provided by MCP servers. Resources allow servers to share "
    "data that provides context to language models, such as files, database "
    "schemas, or application-specific information. Prefer resources over web "
    "search when possible."
)
_LIST_TEMPLATES_DESCRIPTION: Final = (
    "Lists resource templates provided by MCP servers. Parameterized resource "
    "templates allow servers to share data that takes parameters and provides "
    "context to language models, such as files, database schemas, or "
    "application-specific information. Prefer resource templates over web "
    "search when possible."
)
_READ_DESCRIPTION: Final = (
    "Read a specific resource from an MCP server given the server name and "
    "resource URI."
)


class McpResourceError(Exception):
    """A resource tool cannot do what was asked; the message is the result."""


def _string_argument(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpResourceError(f"{key} must be a string")
    return js_trim(value) or None


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _name_order(server: McpResourceServer) -> tuple[object, ...]:
    # As the servers section sorts names (Pi ``localeCompare``).
    name = server.name
    return (locale_compare_key(name.lower()), tuple(ch.isupper() for ch in name))


# What a resource tool worked out: the model-facing content, the details and
# the payload for scripts.
_Outcome = tuple[list[LlmContent], dict[str, str], dict[str, object]]
_Run = Callable[[Mapping[str, object], threading.Event | None], _Outcome]
# One page of a server: its items and the cursor of the next page.
_Page = Callable[
    [McpResourceServer, str | None, McpRequestOptions],
    tuple[list[JsonObject], object],
]
_All = Callable[[McpResourceServer, McpRequestOptions], list[JsonObject]]


class McpResourceToolPort:
    """One of the three resource tools as a pipy tool.

    Whatever a call raises is its error result; a script that calls the tool
    then gets the message as an exception.
    """

    def __init__(
        self,
        definition: ToolDefinition,
        run: _Run,
    ) -> None:
        self._definition = definition
        self._run = run

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            content, details, payload = self._run(
                request.arguments, context.cancel_event
            )
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=render_model_text(content),
                provider_correlation_id=request.provider_correlation_id,
                details=details,
                structured_content=payload,
            )
        except Exception as error:  # noqa: BLE001 - whatever a call raises is the tool's error result
            # A server's error message has no size limit of its own.
            text = truncate_middle(
                node_encodable_text(_error_text(error)), MCP_OUTPUT_MAX_BYTES
            ).content
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=text,
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )


class _ResourceTools:
    """What the three tools share: the servers they reach and how they answer."""

    def __init__(
        self,
        servers: Callable[[], Sequence[McpResourceServer]],
        save_output: McpOutputSaver,
    ) -> None:
        self._servers = servers
        self._save_output = save_output

    def _find_server(self, name: str) -> McpResourceServer:
        current = self._servers()
        for candidate in current:
            if candidate.name == name:
                return candidate
        available = ", ".join(candidate.name for candidate in current)
        raise McpResourceError(
            f'MCP server "{name}" has no resources'
            + (f". Servers with resources: {available}" if available else "")
        )

    def _json_result(
        self, tool: str, server: str | None, payload: dict[str, object]
    ) -> _Outcome:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        content, full_output_path = limit_mcp_content([_text(text)], self._save_output)
        return content, _details(server or "", tool, full_output_path), payload

    def listing(self, tool: str, key: str, page: _Page, every: _All) -> _Run:
        """One page of one server, or every page of every server."""

        def run(
            arguments: Mapping[str, object], cancel_event: threading.Event | None
        ) -> _Outcome:
            server_name = _string_argument(arguments, "server")
            cursor = _string_argument(arguments, "cursor")
            if server_name:
                server = self._find_server(server_name)
                items, next_cursor = page(
                    server, cursor, _options_for(server, cancel_event)
                )
                payload: dict[str, object] = {
                    "server": server.name,
                    key: _visible(server.name, items),
                }
                if next_cursor is not None:
                    payload["nextCursor"] = next_cursor
                return self._json_result(tool, server_name, payload)
            if cursor:
                raise McpResourceError(
                    "cursor can only be used when a server is specified"
                )
            return self._json_result(
                tool, None, self._list_every_server(key, every, cancel_event)
            )

        return run

    def _list_every_server(
        self, key: str, every: _All, cancel_event: threading.Event | None
    ) -> dict[str, object]:
        ordered = sorted(self._servers(), key=_name_order)
        results = settle_all(
            [
                partial(every, server, _options_for(server, cancel_event))
                for server in ordered
            ]
        )
        listed: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        for server, result in zip(ordered, results, strict=True):
            if isinstance(result, Exception):
                errors.append({"server": server.name, "error": _error_text(result)})
            else:
                listed.extend(_visible(server.name, result))
        payload: dict[str, object] = {key: listed}
        if errors:
            payload["errors"] = errors
        return payload

    def read(
        self, arguments: Mapping[str, object], cancel_event: threading.Event | None
    ) -> _Outcome:
        server_name = _string_argument(arguments, "server")
        uri = _string_argument(arguments, "uri")
        if not server_name:
            raise McpResourceError("server must be provided")
        if not uri:
            raise McpResourceError("uri must be provided")
        server = self._find_server(server_name)
        result = server.read_resource(uri, _options_for(server, cancel_event))
        contents = result.get("contents")
        entries: list[Any] = contents if isinstance(contents, list) else []
        # Several contents (for example a directory) are labeled with their URIs.
        blocks: list[object] = []
        for entry in entries:
            if len(entries) > 1:
                blocks.append({"type": "text", "text": f"{entry.get('uri')}:"})
            blocks.append({"type": "resource", "resource": entry})
        converted = to_model_content(server.name, blocks, save_output=self._save_output)
        content, full_output_path = limit_mcp_content(
            converted or [_text(f"Resource {uri} is empty.")], self._save_output
        )
        payload: dict[str, object] = {
            "server": server.name,
            "uri": uri,
            "contents": [
                {key: value for key, value in entry.items() if key != "_meta"}
                for entry in entries
            ],
        }
        details = _details(server.name, READ_MCP_RESOURCE_TOOL, full_output_path)
        return content, details, payload


def _text(text: str) -> LlmContent:
    return {"type": "text", "text": node_encodable_text(text)}


def _details(server: str, tool: str, full_output_path: str | None) -> dict[str, str]:
    details = {"server": server, "tool": tool}
    if full_output_path:
        details["fullOutputPath"] = full_output_path
    return details


def _options_for(
    server: McpResourceServer, cancel_event: threading.Event | None
) -> McpRequestOptions:
    return McpRequestOptions(cancel_event=cancel_event, timeout_ms=server.timeout_ms)


def _visible(server: str, items: Sequence[JsonObject]) -> list[dict[str, object]]:
    return [_listed(server, item) for item in items if not is_mcp_app_resource(item)]


def _resources_page(
    server: McpResourceServer, cursor: str | None, options: McpRequestOptions
) -> tuple[list[JsonObject], object]:
    result = server.resources_page(cursor, options)
    return cast("list[JsonObject]", result["resources"]), result.get("nextCursor")


def _templates_page(
    server: McpResourceServer, cursor: str | None, options: McpRequestOptions
) -> tuple[list[JsonObject], object]:
    result = server.resource_templates_page(cursor, options)
    return (
        cast("list[JsonObject]", result["resourceTemplates"]),
        result.get("nextCursor"),
    )


def create_mcp_resource_tools(
    *,
    exposure: str,
    servers: Callable[[], Sequence[McpResourceServer]],
    save_output: McpOutputSaver = save_to_temp_file,
) -> list[McpResourceToolPort]:
    """The three resource tools, with the MCP exposure ``exposure``.

    ``servers`` returns the servers whose resources they reach, at call time.
    """

    tool_exposure = to_tool_exposure(exposure)
    tools = _ResourceTools(servers, save_output)

    def definition(
        name: str,
        description: str,
        parameters: Mapping[str, object],
        output_schema: Mapping[str, object],
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=parameters,
            output_schema=output_schema,
            exposure=tool_exposure,
            annotations=ToolAnnotations(read_only_hint=True),
            schema_mode="foreign",
        )

    return [
        McpResourceToolPort(
            definition(
                LIST_MCP_RESOURCES_TOOL,
                _LIST_DESCRIPTION,
                _LIST_PARAMETERS,
                _LIST_OUTPUT_SCHEMA,
            ),
            tools.listing(
                LIST_MCP_RESOURCES_TOOL,
                "resources",
                _resources_page,
                lambda server, options: server.all_resources(options),
            ),
        ),
        McpResourceToolPort(
            definition(
                LIST_MCP_RESOURCE_TEMPLATES_TOOL,
                _LIST_TEMPLATES_DESCRIPTION,
                _LIST_PARAMETERS,
                _LIST_TEMPLATES_OUTPUT_SCHEMA,
            ),
            tools.listing(
                LIST_MCP_RESOURCE_TEMPLATES_TOOL,
                "resourceTemplates",
                _templates_page,
                lambda server, options: server.all_resource_templates(options),
            ),
        ),
        McpResourceToolPort(
            definition(
                READ_MCP_RESOURCE_TOOL,
                _READ_DESCRIPTION,
                _READ_PARAMETERS,
                _READ_OUTPUT_SCHEMA,
            ),
            tools.read,
        ),
    ]
