"""JSON-RPC 2.0 message predicates, MCP error types, protocol versions and
the conversion of tool result content.

Messages are plain ``dict`` objects as decoded from JSON. The predicates follow
JSON's types rather than Python's: ``True`` and ``False`` are not numbers, so
they are neither a request id nor an error code.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Final, TypeGuard

JsonObject = dict[str, object]
JsonRpcId = str | int | float
JsonRpcMessage = dict[str, object]
# Tool result content in the shape a model takes: ``{"type": "text", "text"}``
# or ``{"type": "image", "data", "mimeType"}`` with base64 data.
LlmContent = dict[str, str]

LATEST_PROTOCOL_VERSION: Final = "2025-11-25"
# Versions the client accepts from a server. A server that does not support
# the requested version answers with its own latest one, so older versions
# stay accepted for servers built on older SDKs.
SUPPORTED_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = (
    LATEST_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603


class McpError(Exception):
    """A JSON-RPC error: one a server answered with, or an invalid message."""

    def __init__(self, code: int | float, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class McpProtocolError(Exception):
    """A server broke the MCP protocol in a way that has no JSON-RPC error code."""


class McpConnectionClosedError(Exception):
    """The connection is closed, or was never opened."""

    def __init__(self, message: str = "MCP connection closed") -> None:
        super().__init__(message)


class McpTimeoutError(Exception):
    """A request got no response within its timeout."""

    def __init__(self, timeout_ms: int | float) -> None:
        super().__init__(f"MCP request timed out after {format_number(timeout_ms)}ms")
        self.timeout_ms = timeout_ms


class McpAbortError(Exception):
    """The caller cancelled a request."""

    def __init__(self, message: str = "MCP request aborted") -> None:
        super().__init__(message)


def format_number(value: int | float) -> str:
    """Render a JSON number as JSON would: ``5.0`` reads ``5``."""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_id(request_id: JsonRpcId) -> str:
    """Render a request id for a message."""

    return request_id if isinstance(request_id, str) else format_number(request_id)


def is_object(value: object) -> TypeGuard[JsonObject]:
    return isinstance(value, dict)


def is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_json_rpc_id(value: object) -> TypeGuard[JsonRpcId]:
    if isinstance(value, float):
        return math.isfinite(value)
    # An int is finite at any size; `math.isfinite` would overflow on a huge one.
    return isinstance(value, str) or is_number(value)


def is_json_rpc_request(message: object) -> TypeGuard[JsonRpcMessage]:
    return (
        is_object(message)
        and message.get("jsonrpc") == "2.0"
        and is_json_rpc_id(message.get("id"))
        and isinstance(message.get("method"), str)
    )


def is_json_rpc_notification(message: object) -> TypeGuard[JsonRpcMessage]:
    return (
        is_object(message)
        and message.get("jsonrpc") == "2.0"
        and "id" not in message
        and isinstance(message.get("method"), str)
    )


def is_json_rpc_response(message: object) -> TypeGuard[JsonRpcMessage]:
    if (
        not is_object(message)
        or message.get("jsonrpc") != "2.0"
        or not is_json_rpc_id(message.get("id"))
    ):
        return False
    if "result" in message:
        return "error" not in message
    error = message.get("error")
    return (
        is_object(error)
        and is_number(error.get("code"))
        and isinstance(error.get("message"), str)
    )


def parse_json_rpc_message(value: object) -> JsonRpcMessage:
    """Return ``value`` when it is a request, notification or response."""

    if (
        is_json_rpc_request(value)
        or is_json_rpc_notification(value)
        or is_json_rpc_response(value)
    ):
        return value
    raise McpError(INVALID_REQUEST, "Invalid JSON-RPC message")


def _text(text: str) -> LlmContent:
    return {"type": "text", "text": text}


def _image(data: object, mime_type: object) -> LlmContent | None:
    if isinstance(data, str) and isinstance(mime_type, str):
        return {"type": "image", "data": data, "mimeType": mime_type}
    return None


def _text_block(block: JsonObject) -> LlmContent | None:
    text = block.get("text")
    return _text(text) if isinstance(text, str) else None


def _image_block(block: JsonObject) -> LlmContent | None:
    return _image(block.get("data"), block.get("mimeType"))


def _audio_block(block: JsonObject) -> LlmContent | None:
    mime_type = block.get("mimeType")
    if isinstance(mime_type, str):
        return _text(f"[audio {mime_type} omitted]")
    return None


def _resource_link_block(block: JsonObject) -> LlmContent | None:
    name = block.get("name")
    uri = block.get("uri")
    if isinstance(name, str) and isinstance(uri, str):
        return _text(f"{name}: {uri}")
    return None


def _resource_block(block: JsonObject) -> LlmContent | None:
    resource = block.get("resource")
    if not is_object(resource):
        return None
    text = resource.get("text")
    if isinstance(text, str):
        return _text(text)
    blob = resource.get("blob")
    uri = resource.get("uri")
    mime_type = resource.get("mimeType")
    if not isinstance(blob, str):
        return None
    if isinstance(mime_type, str) and mime_type.startswith("image/"):
        return _image(blob, mime_type)
    if not isinstance(uri, str):
        return None
    shown = mime_type if isinstance(mime_type, str) else "unknown type"
    return _text(f"[binary resource {uri} ({shown}) omitted]")


_BLOCK_CONVERTERS: Final[dict[str, Callable[[JsonObject], LlmContent | None]]] = {
    "text": _text_block,
    "image": _image_block,
    "audio": _audio_block,
    "resource_link": _resource_link_block,
    "resource": _resource_block,
}


def _block_to_llm_content(block: object) -> LlmContent:
    kind = block.get("type") if is_object(block) else None
    if is_object(block) and isinstance(kind, str):
        convert = _BLOCK_CONVERTERS.get(kind)
        content = convert(block) if convert is not None else None
        if content is not None:
            return content
    shown = kind if isinstance(kind, str) else "undefined"
    return _text(f"[unsupported MCP content {shown}]")


def to_llm_content(result: Mapping[str, object]) -> list[LlmContent]:
    """Convert a ``tools/call`` result to text and image content for a model.

    Text and images pass through, embedded text resources become text,
    embedded image resources become images, and other blocks (audio, resource
    links, binary resources) become a short text placeholder. A block of an
    unknown type, or one without the fields its type requires, becomes a
    placeholder naming the type. A result without content blocks but with
    ``structuredContent`` becomes its JSON, since servers should, but do not
    always, mirror structured results as text.
    """

    blocks = result.get("content")
    content = [
        _block_to_llm_content(block)
        for block in (blocks if isinstance(blocks, list) else [])
    ]
    structured = result.get("structuredContent")
    if not content and structured is not None:
        content.append(_text(json.dumps(structured, indent=2, ensure_ascii=False)))
    return content
