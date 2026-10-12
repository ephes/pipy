"""Tests for the MCP JSON-RPC predicates and error types."""

from __future__ import annotations

import json
import math

import pytest

from pipy_harness.native.mcp.protocol import (
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpTimeoutError,
    format_id,
    is_json_rpc_id,
    is_json_rpc_notification,
    is_json_rpc_request,
    is_json_rpc_response,
    parse_json_rpc_message,
)


def test_supported_versions_start_with_the_latest() -> None:
    assert LATEST_PROTOCOL_VERSION == "2025-11-25"
    assert SUPPORTED_PROTOCOL_VERSIONS == (
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    )


@pytest.mark.parametrize("value", ["a", "", 1, 0, -3, 1.5, 10**400])
def test_strings_and_finite_numbers_are_ids(value: object) -> None:
    assert is_json_rpc_id(value)


@pytest.mark.parametrize(
    "value", [None, True, False, math.nan, math.inf, [1], {"id": 1}]
)
def test_other_values_are_not_ids(value: object) -> None:
    assert not is_json_rpc_id(value)


def test_request_needs_version_id_and_method() -> None:
    request = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert is_json_rpc_request(request)
    assert not is_json_rpc_notification(request)
    assert not is_json_rpc_response(request)
    assert not is_json_rpc_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})
    assert not is_json_rpc_request({"jsonrpc": "2.0", "id": None, "method": "ping"})
    assert not is_json_rpc_request({"jsonrpc": "2.0", "id": True, "method": "ping"})
    assert not is_json_rpc_request({"jsonrpc": "2.0", "id": 1, "method": 7})
    assert not is_json_rpc_request([request])


def test_a_huge_integer_id_from_json_is_validated_without_overflow() -> None:
    message = json.loads(
        '{"jsonrpc": "2.0", "id": 1' + "0" * 400 + ', "method": "ping"}'
    )
    assert parse_json_rpc_message(message) is message
    assert is_json_rpc_response({"jsonrpc": "2.0", "id": message["id"], "result": {}})


def test_notification_has_a_method_and_no_id_key() -> None:
    assert is_json_rpc_notification({"jsonrpc": "2.0", "method": "notifications/x"})
    # An id key of any value, also an invalid one, makes it not a notification.
    assert not is_json_rpc_notification(
        {"jsonrpc": "2.0", "id": None, "method": "notifications/x"}
    )
    assert not is_json_rpc_notification({"jsonrpc": "2.0"})
    assert not is_json_rpc_notification({"method": "notifications/x"})


def test_response_has_a_result_or_a_well_formed_error() -> None:
    assert is_json_rpc_response({"jsonrpc": "2.0", "id": 1, "result": None})
    assert is_json_rpc_response(
        {"jsonrpc": "2.0", "id": "a", "error": {"code": -32000, "message": "no"}}
    )
    assert not is_json_rpc_response({"jsonrpc": "2.0", "id": 1})
    assert not is_json_rpc_response(
        {"jsonrpc": "2.0", "id": 1, "result": 1, "error": {"code": 1, "message": "no"}}
    )
    assert not is_json_rpc_response({"jsonrpc": "2.0", "id": 1, "error": "no"})
    assert not is_json_rpc_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": "1", "message": "no"}}
    )
    assert not is_json_rpc_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": True, "message": "no"}}
    )
    assert not is_json_rpc_response({"jsonrpc": "2.0", "id": 1, "error": {"code": 1}})
    assert not is_json_rpc_response({"jsonrpc": "2.0", "result": 1})


def test_parse_returns_valid_messages_and_rejects_the_rest() -> None:
    message = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert parse_json_rpc_message(message) is message
    with pytest.raises(McpError) as raised:
        parse_json_rpc_message({"jsonrpc": "2.0", "id": 1})
    assert raised.value.code == INVALID_REQUEST
    assert str(raised.value) == "Invalid JSON-RPC message"


def test_error_types_carry_their_details() -> None:
    error = McpError(1234, "tool failed", {"retryable": False})
    assert (error.code, error.message, error.data) == (
        1234,
        "tool failed",
        {"retryable": False},
    )
    assert str(error) == "tool failed"
    assert McpError(1, "x").data is None
    assert str(McpConnectionClosedError()) == "MCP connection closed"
    assert str(McpConnectionClosedError("MCP client is idle")) == "MCP client is idle"
    assert str(McpAbortError()) == "MCP request aborted"
    timeout = McpTimeoutError(30_000)
    assert timeout.timeout_ms == 30_000
    assert str(timeout) == "MCP request timed out after 30000ms"
    assert str(McpTimeoutError(5.0)) == "MCP request timed out after 5ms"
    assert str(McpTimeoutError(2.5)) == "MCP request timed out after 2.5ms"


def test_ids_are_rendered_as_json_would() -> None:
    assert format_id("roots") == "roots"
    assert format_id(7) == "7"
    assert format_id(7.0) == "7"
