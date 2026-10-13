"""Images from MCP servers in a session (MCP parity plan S39; TLS-11 and the
image part of RES-09).

A product session with a scripted provider and the fake stdio server as a
real child process: an image block of a tool result and an image resource
reach the provider request as images, in every adapter family's shape, and a
resumed session sends them again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_native_extension_tool_metadata import _result, _tool_results
from test_native_mcp_session import _Provider, _server, _write_mcp_json
from test_sdk_codemode_mcp import _config, _submit, _workspace

from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.providers.anthropic_messages_wire import messages_payload
from pipy_harness.native.providers.chat_completions_wire import chat_transcript
from pipy_harness.native.providers.google_generate_content_wire import (
    gemini_contents,
)
from pipy_harness.native.providers.openai_responses_wire import responses_input
from pipy_harness.native.session_tree import NativeSessionTree

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
JPEG = "/9j/4AAQSkZJRgABAQ=="
_SHOT = {
    "name": "shot",
    "description": "Take a screenshot.",
    "inputSchema": {"type": "object", "properties": {}},
}
_SCENARIO: dict[str, Any] = {
    "tools": [_SHOT],
    "toolResults": {
        "shot": {
            "content": [
                {"type": "text", "text": "captured"},
                {"type": "image", "data": PNG, "mimeType": "image/png"},
            ]
        }
    },
    "capabilities": {"tools": {}, "resources": {}},
    "resources": [{"uri": "docs://logo", "name": "logo", "mimeType": "image/jpeg"}],
    "resourceContents": {
        "docs://logo": [{"uri": "docs://logo", "mimeType": "image/jpeg", "blob": JPEG}]
    },
}


def _images_in_each_family(request: ProviderRequest) -> dict[str, str]:
    """The request as each adapter family serializes it."""

    return {
        "anthropic": repr(
            messages_payload(request, parse_error_class=ProviderHTTPError)
        ),
        "responses": repr(
            responses_input(request, parse_error_class=ProviderHTTPError)
        ),
        "google": repr(gemini_contents(request, parse_error_class=ProviderHTTPError)),
        "chat": repr(chat_transcript(request).messages),
    }


def test_mcp_images_reach_every_adapter_family_and_survive_a_resume(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    entry, _record = _server(tmp_path / "servers", **_SCENARIO)
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__docs__shot", "{}"),
                    ProviderToolCall(
                        "c2",
                        "read_mcp_resource",
                        '{"server": "docs", "uri": "docs://logo"}',
                    ),
                )
            ),
            _result(final_text="done"),
        ]
    )
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "saved")

    _submit(workspace, provider, mcp=McpOptions(), tree=tree)

    shot, logo = _tool_results(provider.requests[1])
    # TLS-11: text blocks as text, image blocks as images, in the server's order.
    assert (shot.is_error, shot.content.value) == (False, "captured")
    assert shot.blocks == (TextContent("captured"), ImageContent(PNG, "image/png"))
    # RES-09: an image resource is an image.
    assert (logo.is_error, logo.content.value) == (False, "")
    assert logo.blocks == (ImageContent(JPEG, "image/jpeg"),)

    sent = _images_in_each_family(provider.requests[1])
    assert "'media_type': 'image/png'" in sent["anthropic"]
    assert f"'image_url': 'data:image/png;base64,{PNG}'" in sent["responses"]
    assert (
        f"'inlineData': {{'mimeType': 'image/jpeg', 'data': '{JPEG}'}}"
        in (sent["google"])
    )
    assert f"'image_url': {{'url': 'data:image/jpeg;base64,{JPEG}'}}" in sent["chat"]
    for family, wire in sent.items():
        assert PNG in wire and JPEG in wire, family

    # A resumed session sends the stored results, images included.
    assert tree.path is not None
    resumed = _Provider([_result(final_text="again")])
    _submit(
        workspace,
        resumed,
        prompts=("and now?",),
        tree=NativeSessionTree.open(tree.path),
    )
    assert _tool_results(resumed.requests[0]) == [shot, logo]
    again = _images_in_each_family(resumed.requests[0])
    for family, wire in again.items():
        assert PNG in wire and JPEG in wire, family
