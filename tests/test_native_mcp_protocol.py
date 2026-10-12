"""Tests for the MCP JSON-RPC predicates, error types and content conversion.

Tests that mirror a case of Pi's ``packages/mcp/test/content.test.ts`` name it
in a ``Pi:`` comment.
"""

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
    to_llm_content,
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


def test_passes_text_and_images_through_and_replaces_other_blocks_with_text() -> None:
    # Pi: "passes text and images through and replaces other blocks with text"
    blocks = [
        {"type": "text", "text": "hello", "annotations": {"priority": 1}},
        {"type": "image", "data": "aW1n", "mimeType": "image/png", "_meta": {"x": 1}},
        {"type": "audio", "data": "YXVk", "mimeType": "audio/wav"},
        {"type": "resource_link", "uri": "file:///a.txt", "name": "a.txt"},
        {"type": "resource", "resource": {"uri": "file:///b.txt", "text": "inline"}},
        {
            "type": "resource",
            "resource": {
                "uri": "file:///c.png",
                "mimeType": "image/png",
                "blob": "Yw==",
            },
        },
        {"type": "resource", "resource": {"uri": "file:///d.bin", "blob": "ZA=="}},
    ]

    assert to_llm_content({"content": blocks}) == [
        {"type": "text", "text": "hello"},
        {"type": "image", "data": "aW1n", "mimeType": "image/png"},
        {"type": "text", "text": "[audio audio/wav omitted]"},
        {"type": "text", "text": "a.txt: file:///a.txt"},
        {"type": "text", "text": "inline"},
        {"type": "image", "data": "Yw==", "mimeType": "image/png"},
        {
            "type": "text",
            "text": "[binary resource file:///d.bin (unknown type) omitted]",
        },
    ]


def test_falls_back_to_the_structured_content_as_json_when_there_are_no_blocks() -> (
    None
):
    # Pi: "falls back to the structured content as JSON when there are no blocks"
    assert to_llm_content({"content": [], "structuredContent": {"n": 1}}) == [
        {"type": "text", "text": '{\n  "n": 1\n}'}
    ]
    assert to_llm_content(
        {"content": [{"type": "text", "text": "n=1"}], "structuredContent": {"n": 1}}
    ) == [{"type": "text", "text": "n=1"}]


def test_structured_content_json_is_indented_and_keeps_non_ascii_text() -> None:
    assert to_llm_content(
        {"structuredContent": {"name": "Grüße", "items": [1, {"ok": True}], "none": {}}}
    ) == [
        {
            "type": "text",
            "text": (
                "{\n"
                '  "name": "Grüße",\n'
                '  "items": [\n'
                "    1,\n"
                "    {\n"
                '      "ok": true\n'
                "    }\n"
                "  ],\n"
                '  "none": {}\n'
                "}"
            ),
        }
    ]
    assert to_llm_content({"content": [], "structuredContent": {}}) == [
        {"type": "text", "text": "{}"}
    ]


def test_a_result_without_content_or_structured_content_converts_to_nothing() -> None:
    assert to_llm_content({}) == []
    assert to_llm_content({"content": []}) == []
    assert to_llm_content({"content": None, "structuredContent": None}) == []


def test_a_binary_resource_names_its_mime_type() -> None:
    assert to_llm_content(
        {
            "content": [
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///a.pdf",
                        "mimeType": "application/pdf",
                        "blob": "JVBERg==",
                    },
                }
            ]
        }
    ) == [
        {
            "type": "text",
            "text": "[binary resource file:///a.pdf (application/pdf) omitted]",
        }
    ]


@pytest.mark.parametrize(
    ("block", "shown"),
    [
        ({"type": "video", "data": "x"}, "video"),
        ({"data": "x"}, "undefined"),
        ({"type": 7}, "undefined"),
        ("text", "undefined"),
        (None, "undefined"),
        # A known type without the fields it requires is not passed on.
        ({"type": "text"}, "text"),
        ({"type": "text", "text": 1}, "text"),
        ({"type": "image", "data": "aW1n"}, "image"),
        ({"type": "image", "mimeType": "image/png"}, "image"),
        ({"type": "audio", "data": "YXVk"}, "audio"),
        ({"type": "resource_link", "uri": "file:///a"}, "resource_link"),
        ({"type": "resource_link", "name": "a"}, "resource_link"),
        ({"type": "resource"}, "resource"),
        ({"type": "resource", "resource": "file:///a"}, "resource"),
        ({"type": "resource", "resource": {"uri": "file:///a"}}, "resource"),
        ({"type": "resource", "resource": {"blob": "ZA=="}}, "resource"),
    ],
)
def test_unknown_and_malformed_blocks_become_a_placeholder(
    block: object, shown: str
) -> None:
    assert to_llm_content({"content": [block, {"type": "text", "text": "kept"}]}) == [
        {"type": "text", "text": f"[unsupported MCP content {shown}]"},
        {"type": "text", "text": "kept"},
    ]


def test_an_embedded_image_blob_needs_no_uri() -> None:
    assert to_llm_content(
        {
            "content": [
                {
                    "type": "resource",
                    "resource": {"mimeType": "image/jpeg", "blob": "/9g="},
                }
            ]
        }
    ) == [{"type": "image", "data": "/9g=", "mimeType": "image/jpeg"}]
