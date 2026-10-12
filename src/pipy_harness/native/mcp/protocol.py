"""JSON-RPC 2.0 message predicates, MCP error types and protocol versions.

Messages are plain ``dict`` objects as decoded from JSON. The predicates follow
JSON's types rather than Python's: ``True`` and ``False`` are not numbers, so
they are neither a request id nor an error code.
"""

from __future__ import annotations

import math
from typing import Final, TypeGuard

JsonObject = dict[str, object]
JsonRpcId = str | int | float
JsonRpcMessage = dict[str, object]

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
