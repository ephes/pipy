"""Adapts MCP tools to pipy tools.

Pi ``extensions/mcp/tools.ts``. A result maps onto model-facing content (text
and images). Text over 20 KB keeps its start and end with the middle cut out,
as Codex does, and the full text is saved to a temp file the model can read.
Binary resources other than images are saved to temp files too, and resource
links name the ``read_mcp_resource`` tool. Codemode scripts receive the whole
``CallToolResult`` without ``_meta`` (``content`` blocks as the server sent
them, ``structuredContent``, ``isError``), never truncated: it is the tool's
structured content, and every MCP tool declares a ``CallToolResult`` output
schema. An MCP error (``isError``) is an error result for the model, but a
script still resolves to the result.

A pipy tool result carries text only, so an image is shown to the model as
``[image <mime> omitted]`` for now.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
import re
import secrets
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from pipy_harness.native.fuzzy import js_trim
from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.config import mcp_namespace
from pipy_harness.native.mcp.protocol import (
    JsonObject,
    LlmContent,
    format_number,
    is_number,
    is_object,
    to_llm_content,
)
from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolExposure,
    ToolNamespace,
    ToolRequest,
)
from pipy_harness.native.tools.fs_errors import node_encodable_text
from pipy_harness.native.tools.truncate import format_size, truncate_middle

# Provider tool names are limited to 64 characters of ``[A-Za-z0-9_-]``.
MAX_TOOL_NAME_LENGTH: Final = 64
# Model-facing text of an MCP result beyond this is cut in the middle.
MCP_OUTPUT_MAX_BYTES: Final = 20 * 1024
# Tool that reads the resources named by resource links.
READ_MCP_RESOURCE_TOOL: Final = "read_mcp_resource"

_HASH_LENGTH: Final = 8
_OUTPUT_FILE_PREFIX: Final = "pipy-mcp"
# Output can carry private data, so only the user may read the files.
_OUTPUT_FILE_MODE: Final = 0o600
_NAME_CHARACTERS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)
_URL_SCHEME: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_FILE_EXTENSION: Final = re.compile(r"\.[A-Za-z0-9]{1,8}\Z")
_NOT_BASE64: Final = re.compile(r"[^A-Za-z0-9+/]")
_ANNOTATION_HINTS: Final = (
    ("readOnlyHint", "read_only_hint"),
    ("destructiveHint", "destructive_hint"),
    ("idempotentHint", "idempotent_hint"),
    ("openWorldHint", "open_world_hint"),
)

# Saves the full text of a truncated result, or a binary resource, and returns
# the file path. The extension includes the dot, for example ``.txt``.
McpOutputSaver = Callable[[str | bytes, str], str]


class McpToolCaller(Protocol):
    """What a tool port calls: a server connection, or a stand-in for one."""

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """One converted ``tools/call`` result.

    ``content`` is what the model is shown, ``structured_content`` what a
    script receives, and ``details`` is ``{server, tool, fullOutputPath?}``.
    """

    content: list[LlmContent]
    details: dict[str, str]
    structured_content: JsonObject
    is_error: bool


def to_tool_exposure(exposure: str) -> ToolExposure:
    """Tool exposure of an MCP exposure.

    ``codemode`` and ``deferred`` both leave tools out of the codemode
    description; they differ only in which tool is activated to reach them.
    """

    if exposure in ("codemode", "deferred"):
        return "deferred"
    if exposure == "direct":
        return "direct"
    if exposure == "hidden":
        return "hidden"
    raise ValueError(f"Unknown MCP exposure: {exposure!r}")


def _sanitized(name: str) -> str:
    # Pi replaces UTF-16 units, so a character outside the BMP is two `_`.
    return "".join(
        char if char in _NAME_CHARACTERS else "_" * (2 if ord(char) > 0xFFFF else 1)
        for char in name
    )


def create_mcp_tool_name(
    server: str, tool: str, is_taken: Callable[[str], bool] | None = None
) -> str:
    """``mcp__<server>__<tool>``, sanitized, with a hash suffix when needed.

    Everything but ``[A-Za-z0-9_]`` becomes ``_``, so the name is also the
    identifier codemode scripts call the tool by. A name longer than 64
    characters is cut and ends in ``_`` and the first 8 hex digits of SHA-256
    over ``<server>\\0<tool>``. ``is_taken`` reports names used by a different
    MCP tool: sanitizing can map two tools to one name (``a-b`` and ``a_b``),
    which then get the hash suffix too.
    """

    name = _sanitized(f"mcp__{server}__{tool}")
    if len(name) <= MAX_TOOL_NAME_LENGTH and not (is_taken and is_taken(name)):
        return name
    return _suffixed(name, f"{server}\0{tool}")


def _suffixed(name: str, identity: str) -> str:
    digest = hashlib.sha256(node_encodable_text(identity).encode("utf-8")).hexdigest()
    return f"{name[: MAX_TOOL_NAME_LENGTH - _HASH_LENGTH - 1]}_{digest[:_HASH_LENGTH]}"


class McpToolNames:
    """The names given to MCP tools in one session (Pi ``toolOwners``).

    A name that was ever assigned to one tool is never given to another tool,
    of any server; collision suffixes are computed again from every list. The
    names of one list are all different.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, tuple[str, str]] = {}

    def assign(self, server: str, tools: Sequence[str]) -> list[str]:
        """The names for ``server``'s current tool list, in list order.

        All tools of the list whose names sanitize to the same name get the
        hash suffix, so which one would keep the plain name does not depend on
        the order of the list.
        """

        plain = Counter(create_mcp_tool_name(server, tool) for tool in set(tools))
        current: set[str] = set()
        names: list[str] = []
        with self._lock:
            for tool in tools:
                owner = (server, tool)

                def is_taken(candidate: str, owner: tuple[str, str] = owner) -> bool:
                    return (
                        self._owners.get(candidate, owner) != owner
                        or candidate in current
                        or plain[candidate] > 1
                    )

                name = create_mcp_tool_name(server, tool, is_taken)
                # The suffixed name can be in use too: another tool may be
                # called just that, or the server lists a tool twice. Pi
                # would hand the name over; pipy looks for a free one.
                attempt = 1
                while self._owners.get(name, owner) != owner or name in current:
                    attempt += 1
                    name = _suffixed(
                        _sanitized(f"mcp__{server}__{tool}"),
                        f"{server}\0{tool}\0{attempt}",
                    )
                self._owners[name] = owner
                current.add(name)
                names.append(name)
        return names

    def owner(self, name: str) -> tuple[str, str] | None:
        """The ``(server, tool)`` a name was assigned to, if any."""

        with self._lock:
            return self._owners.get(name)


def mcp_tool_namespace(
    server: str, *, description: object = None, instructions: str | None = None
) -> ToolNamespace:
    """The namespace of a server's tools.

    ``describe_namespace("docs")`` finds ``mcp__docs`` through the alias.
    ``description`` is the configured one, ``instructions`` the server's.
    """

    summary = js_trim(description) if isinstance(description, str) else ""
    return ToolNamespace(
        name=mcp_namespace(server),
        aliases=(server,),
        description=summary or None,
        instructions=instructions or None,
    )


# MCP content blocks as one compact type with the fields of text and image
# blocks, which scripts read most. Rendered into every MCP tool's declaration.
_CONTENT_BLOCK_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "type": {"enum": ["text", "image", "audio", "resource_link", "resource"]},
        "text": {"type": "string"},
        "data": {"type": "string"},
        "mimeType": {"type": "string"},
    },
    "required": ["type"],
    "additionalProperties": True,
}
# Keywords whose values are data, so a `$ref` key in them is not a reference.
_SCHEMA_DATA_KEYWORDS: Final = frozenset({"const", "enum", "default", "examples"})
# Keywords whose values map names to schemas, so their keys are names.
_SCHEMA_MAP_KEYWORDS: Final = frozenset(
    {
        "properties",
        "patternProperties",
        "dependentSchemas",
        "dependencies",
        "$defs",
        "definitions",
    }
)
_STRUCTURED_CONTENT_POINTER: Final = "#/properties/structuredContent"


def _relocate_schema(schema: object, pointer: str) -> object:
    """``schema`` as a subschema at the JSON pointer ``pointer`` of another.

    Its local references (``#``, ``#/$defs/Item``) are rewritten to point
    below ``pointer``, so they resolve to the same subschemas as before.
    """

    if isinstance(schema, list):
        return [_relocate_schema(item, pointer) for item in schema]
    if not isinstance(schema, dict):
        return schema
    relocated: dict[str, object] = {}
    for key, value in schema.items():
        if (
            key == "$ref"
            and isinstance(value, str)
            and (value == "#" or value.startswith("#/"))
        ):
            relocated[key] = f"{pointer}{value[1:]}"
        elif key in _SCHEMA_DATA_KEYWORDS:
            relocated[key] = value
        elif key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            relocated[key] = {
                name: _relocate_schema(subschema, pointer)
                for name, subschema in value.items()
            }
        else:
            relocated[key] = _relocate_schema(value, pointer)
    return relocated


def create_mcp_result_schema(
    structured_content_schema: Mapping[str, object] | None,
) -> dict[str, object]:
    """Output schema of every MCP tool: the ``CallToolResult`` scripts receive.

    It has no ``_meta``, which ``convert_mcp_result`` drops, and the tool's
    own output schema as ``structuredContent``.
    """

    structured: object = (
        # Servers may send structured content without declaring a schema.
        {"type": "object"}
        if structured_content_schema is None
        else _relocate_schema(
            dict(structured_content_schema), _STRUCTURED_CONTENT_POINTER
        )
    )
    return {
        "type": "object",
        "properties": {
            "content": {"type": "array", "items": dict(_CONTENT_BLOCK_SCHEMA)},
            "structuredContent": structured,
            "isError": {"type": "boolean"},
        },
        "required": ["content"],
        # Fields newer protocol versions add.
        "additionalProperties": True,
    }


def save_to_temp_file(data: str | bytes, extension: str) -> str:
    """Write ``data`` to a new file in the temp directory and return its path.

    The file is ``pipy-mcp-<random hex><extension>``, readable by the user
    only. It is created exclusively, which never follows a link someone else
    placed at the path.
    """

    name = f"{_OUTPUT_FILE_PREFIX}-{secrets.token_hex(8)}{extension}"
    path = os.path.join(tempfile.gettempdir(), name)
    payload = (
        node_encodable_text(data).encode("utf-8") if isinstance(data, str) else data
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OUTPUT_FILE_MODE)
    with os.fdopen(fd, "wb") as file:
        file.write(payload)
    return path


def _text(text: str) -> LlmContent:
    # A lone surrogate from a JSON escape cannot be encoded; like Node, pipy
    # writes U+FFFD for it.
    return {"type": "text", "text": node_encodable_text(text)}


def _text_of(content: Sequence[LlmContent]) -> str:
    return "\n".join(block["text"] for block in content if block["type"] == "text")


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__


def limit_mcp_content(
    content: list[LlmContent], save_output: McpOutputSaver = save_to_temp_file
) -> tuple[list[LlmContent], str | None]:
    """Keep model-facing text within ``MCP_OUTPUT_MAX_BYTES``.

    Longer text becomes one text block in Codex's truncation format, followed
    by the path of the file with the full text; images follow it. Returns the
    content and that path, ``None`` when nothing was saved.
    """

    combined = _text_of(content)
    truncation = truncate_middle(combined, MCP_OUTPUT_MAX_BYTES)
    if not truncation.truncated:
        return content, None
    full_output_path: str | None = None
    try:
        full_output_path = save_output(combined, ".txt")
        where = f"[Full output: {full_output_path} (read it with offset/limit)]"
    except Exception as error:  # noqa: BLE001 - a saver may fail in any way; the result is still shown
        where = f"[Could not save the full output: {_error_text(error)}]"
    tokens = -(-truncation.total_bytes // 4)
    text = (
        f"Warning: truncated output (original token count: {tokens})\n"
        f"Total output lines: {truncation.total_lines}\n\n"
        f"{truncation.content}\n\n{where}"
    )
    images = [block for block in content if block["type"] == "image"]
    return [_text(text), *images], full_output_path


def _extension_of(uri: str) -> str:
    """Extension for a saved binary resource: its URI's, else ``.bin``."""

    path = urlsplit(uri).path if _URL_SCHEME.match(uri) else uri
    match = _FILE_EXTENSION.search(path)
    return match.group(0) if match else ".bin"


def _is_text_mime_type(mime_type: str | None) -> bool:
    """Blobs of these types are shown as text."""

    if not mime_type:
        return False
    kind = js_trim(mime_type.split(";", 1)[0]).lower()
    return (
        kind.startswith("text/")
        or kind == "application/json"
        or kind.endswith(("+json", "+xml"))
    )


def _decode_base64(blob: str) -> bytes:
    """Decode as Node does: either alphabet, other characters skipped."""

    text = blob.split("=", 1)[0].replace("-", "+").replace("_", "/")
    text = _NOT_BASE64.sub("", text)
    if len(text) % 4 == 1:
        text = text[:-1]
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return b""


def _resource_link_content(
    server: str, block: JsonObject, readable_resources: bool
) -> list[LlmContent] | None:
    uri = block.get("uri")
    name = block.get("name")
    if not isinstance(uri, str) or not isinstance(name, str):
        return None
    title = block.get("title")
    mime_type = block.get("mimeType")
    size = block.get("size")
    description = block.get("description")
    details = [
        *([mime_type] if isinstance(mime_type, str) and mime_type else []),
        *([_format_size(size)] if is_number(size) and math.isfinite(size) else []),
    ]
    shown = f" ({', '.join(details)})" if details else ""
    about = f": {description}" if isinstance(description, str) and description else ""
    read = (
        f'. Read it with {READ_MCP_RESOURCE_TOOL} (server "{server}")'
        if readable_resources
        else ""
    )
    label = title if isinstance(title, str) else name
    return [_text(f'[Resource {uri} "{label}"{shown}{about}{read}]')]


def _format_size(size: int | float) -> str:
    # JSON has one number type: 2048.0 is 2048.
    return format_size(int(size) if float(size).is_integer() else size)


def _blob_content(
    resource: JsonObject, save_output: McpOutputSaver
) -> list[LlmContent] | None:
    """An embedded blob that is not an image: its text, or the file it went to."""

    uri = resource.get("uri")
    blob = resource.get("blob")
    mime = resource.get("mimeType")
    mime_type = mime if isinstance(mime, str) else None
    if not isinstance(uri, str) or not isinstance(blob, str):
        return None
    if mime_type is not None and mime_type.startswith("image/"):
        return None
    data = _decode_base64(blob)
    if _is_text_mime_type(mime_type):
        return [_text(data.decode("utf-8", "replace"))]
    shown = "unknown type" if mime_type is None else mime_type
    kind = f"{shown}, {format_size(len(data))}"
    try:
        path = save_output(data, _extension_of(uri))
    except Exception as error:  # noqa: BLE001 - a saver may fail in any way; the result is still shown
        reason = _error_text(error)
        return [_text(f"[Binary resource {uri} ({kind}) could not be saved: {reason}]")]
    return [_text(f"[Binary resource {uri} ({kind}) saved to {path}]")]


def _block_to_content(
    server: str,
    block: object,
    save_output: McpOutputSaver,
    readable_resources: bool,
) -> list[LlmContent]:
    """Model-facing content of one block of ``server``'s result."""

    content: list[LlmContent] | None = None
    if is_object(block):
        resource = block.get("resource")
        if block.get("type") == "resource_link":
            content = _resource_link_content(server, block, readable_resources)
        elif block.get("type") == "resource" and is_object(resource):
            content = _blob_content(resource, save_output)
    if content is None:
        content = to_llm_content({"content": [block]})
    return [_text(item["text"]) if item["type"] == "text" else item for item in content]


def to_model_content(
    server: str,
    blocks: Sequence[object],
    *,
    save_output: McpOutputSaver = save_to_temp_file,
    readable_resources: bool = False,
) -> list[LlmContent]:
    """Model-facing content of ``server``'s content blocks, before the limit.

    ``readable_resources`` says whether ``read_mcp_resource`` can read the
    server's resources, which resource links then name.
    """

    return [
        item
        for block in blocks
        for item in _block_to_content(server, block, save_output, readable_resources)
    ]


def _is_truthy(value: object) -> bool:
    """JavaScript truthiness of a JSON value: ``{}`` and ``[]`` count."""

    if value is None or value is False or value == "":
        return False
    return not (is_number(value) and value == 0)


def convert_mcp_result(
    server: str,
    tool: str,
    result: Mapping[str, object],
    *,
    save_output: McpOutputSaver = save_to_temp_file,
    readable_resources: bool = False,
) -> McpToolResult:
    """Convert a ``tools/call`` result.

    An ``isError`` result is an error result that keeps the structured result.
    """

    blocks = result.get("content")
    if isinstance(blocks, list) and blocks:
        converted = to_model_content(
            server,
            blocks,
            save_output=save_output,
            readable_resources=readable_resources,
        )
    else:
        # Without content blocks the structured content is shown as JSON.
        converted = [_text(item["text"]) for item in to_llm_content(result)]
    is_error = _is_truthy(result.get("isError"))
    if is_error and _text_of(converted) == "":
        converted.append(_text(f"MCP tool {server}/{tool} returned an error"))
    content, full_output_path = limit_mcp_content(converted, save_output)
    details = {"server": server, "tool": tool}
    if full_output_path:
        details["fullOutputPath"] = full_output_path
    return McpToolResult(
        content=content,
        details=details,
        structured_content={
            key: value for key, value in result.items() if key != "_meta"
        },
        is_error=is_error,
    )


def _to_parameters(schema: object) -> dict[str, object]:
    """The declared input schema.

    Tool input schemas must be objects. MCP servers may omit ``type``, and
    some providers reject object schemas without ``properties``.
    """

    parameters: dict[str, object] = dict(schema) if isinstance(schema, dict) else {}
    if parameters.get("type") is None:
        parameters["type"] = "object"
    parameters.setdefault("properties", {})
    return parameters


def _to_tool_annotations(tool: Mapping[str, object]) -> ToolAnnotations | None:
    """The boolean hints of an MCP tool's annotations, or None without any."""

    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        return None
    hints = {
        field: annotations[key]
        for key, field in _ANNOTATION_HINTS
        if isinstance(annotations.get(key), bool)
    }
    return ToolAnnotations(**hints) if hints else None


def _tool_description(server: str, tool: Mapping[str, object]) -> str:
    description = tool.get("description")
    trimmed = js_trim(description) if isinstance(description, str) else ""
    if trimmed:
        return trimmed
    title = tool.get("title")
    if not isinstance(title, str):
        annotations = tool.get("annotations")
        title = annotations.get("title") if isinstance(annotations, dict) else None
    if isinstance(title, str) and title:
        return title
    return f"MCP tool {tool['name']} from server {server}"


def create_mcp_tool_definition(
    *,
    server: str,
    tool: Mapping[str, object],
    name: str,
    exposure: str,
    namespace: ToolNamespace,
) -> ToolDefinition:
    """The pipy definition of one tool as its server lists it.

    ``name`` is the tool's pipy name (see ``McpToolNames``) and ``exposure``
    its MCP exposure. Raises ``ValueError`` for a tool pipy cannot declare:
    one whose input schema says it is not an object, or is not JSON.
    """

    output_schema = tool.get("outputSchema")
    return ToolDefinition(
        name=name,
        description=_tool_description(server, tool),
        input_schema=_to_parameters(tool.get("inputSchema")),
        output_schema=create_mcp_result_schema(
            output_schema if isinstance(output_schema, dict) else None
        ),
        exposure=to_tool_exposure(exposure),
        namespace=namespace,
        annotations=_to_tool_annotations(tool),
        schema_mode="foreign",
    )


def _progress_text(progress: Mapping[str, object]) -> str:
    message = progress.get("message")
    if isinstance(message, str):
        return message
    value = progress.get("progress")
    total = progress.get("total")
    shown = format_number(value) if is_number(value) else ""
    of = f"/{format_number(total)}" if is_number(total) else ""
    return f"Progress {shown}{of}"


def render_model_text(content: Sequence[LlmContent]) -> str:
    """The text of a tool result: text blocks, and a placeholder per image."""

    text = "\n".join(
        block["text"]
        if block["type"] == "text"
        else f"[image {block.get('mimeType', 'unknown type')} omitted]"
        for block in content
    )
    return _bounded(node_encodable_text(text))


def _bounded(text: str) -> str:
    cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
    if len(text) <= cap:
        return text
    return text[: cap - 64] + "\n[pipy: MCP tool output truncated]"


class McpToolPort:
    """One MCP tool as a pipy tool.

    The port holds the server and tool name, not a connection: ``get_caller``
    is asked for the server's current connection on every call, and what it
    raises is the result of the call. ``readable_resources`` says, at the
    time of a call, whether ``read_mcp_resource`` can read the server's
    resources. Raises ``ValueError`` like ``create_mcp_tool_definition``.
    """

    def __init__(
        self,
        *,
        server: str,
        tool: Mapping[str, object],
        name: str,
        exposure: str,
        namespace: ToolNamespace,
        timeout_ms: float,
        get_caller: Callable[[], McpToolCaller],
        readable_resources: Callable[[], bool] | None = None,
        save_output: McpOutputSaver = save_to_temp_file,
    ) -> None:
        self._definition = create_mcp_tool_definition(
            server=server, tool=tool, name=name, exposure=exposure, namespace=namespace
        )
        self._server = server
        self._tool = str(tool["name"])
        self._timeout_ms = timeout_ms
        self._get_caller = get_caller
        self._readable_resources = readable_resources
        self._save_output = save_output

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def server(self) -> str:
        return self._server

    @property
    def tool(self) -> str:
        """The tool's name on its server."""

        return self._tool

    @property
    def label(self) -> str:
        """The title of the tool's calls in the transcript (Pi ``label``)."""

        return f"{self._server}/{self._tool}"

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        try:
            converted = self._call(dict(request.arguments), context)
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=render_model_text(converted.content),
                is_error=converted.is_error,
                provider_correlation_id=request.provider_correlation_id,
                details=converted.details,
                structured_content=converted.structured_content,
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
                details={"server": self._server, "tool": self._tool},
            )

    def _call(self, arguments: dict[str, Any], context: ToolContext) -> McpToolResult:
        sink = context.output_sink

        def on_progress(progress: JsonObject) -> None:
            if sink is not None:
                sink(f"{node_encodable_text(_progress_text(progress))}\n")

        result = self._get_caller().call_tool(
            self._tool,
            arguments,
            McpRequestOptions(
                cancel_event=context.cancel_event,
                timeout_ms=self._timeout_ms,
                on_progress=on_progress,
            ),
        )
        readable = self._readable_resources
        return convert_mcp_result(
            self._server,
            self._tool,
            result,
            save_output=self._save_output,
            readable_resources=readable() if readable is not None else False,
        )
