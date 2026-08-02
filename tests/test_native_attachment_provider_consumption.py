"""Parity row D8: multimodal provider adapters consume image attachments.

Proves each supporting adapter turns a ``ProviderRequest.attachments`` entry
into its native image content block, attached to the current user message.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, cast

from pipy_harness.native.agent import AgentMessage, AgentUserMessage, ProductContent
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.providers.anthropic_messages import AnthropicResponseParseError
from pipy_harness.native.providers.anthropic_messages_wire import messages_payload
from pipy_harness.native.providers.google_generate_content_wire import gemini_contents
from pipy_harness.native.providers.google_generative_ai import GoogleResponseParseError
from pipy_harness.native.providers.openai_responses import OpenAIResponseParseError
from pipy_harness.native.providers.openai_responses_wire import responses_input


def _content(message: object) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], cast(dict[str, Any], message)["content"])


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_B64 = base64.b64encode(_PNG).decode("ascii")


def _attachment() -> ProviderImageAttachment:
    return ProviderImageAttachment(
        media_type="image/png",
        data_base64=_B64,
        byte_count=len(_PNG),
        sha256=hashlib.sha256(_PNG).hexdigest(),
        source_label="shot.png",
    )


def _request(*, messages: tuple[AgentMessage, ...] = ()) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="sys",
        user_prompt="describe this",
        provider_name="p",
        model_id="m",
        cwd=Path("/tmp"),
        messages=messages,
        attachments=(_attachment(),),
    )


def _anthropic_payload(request: ProviderRequest) -> list[dict[str, Any]]:
    # Invoke the shared translator with the Anthropic parameters (the adapter
    # enables image attachment and tool-result coalescing).
    return cast(
        list[dict[str, Any]],
        messages_payload(
            request,
            parse_error_class=AnthropicResponseParseError,
            attach_images=True,
            coalesce_tool_results=True,
        ),
    )


def test_anthropic_attaches_image_block_to_last_user_message() -> None:
    payload = _anthropic_payload(_request())
    user = payload[-1]
    assert user["role"] == "user"
    blocks = _content(user)
    image_blocks = [b for b in blocks if b.get("type") == "image"]
    assert len(image_blocks) == 1
    source = image_blocks[0]["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    assert source["data"] == _B64
    # The original text block is preserved alongside the image.
    assert any(b.get("type") == "text" for b in blocks)


def test_anthropic_attaches_to_messages_envelope() -> None:
    payload = _anthropic_payload(
        _request(messages=(AgentUserMessage(content=ProductContent("hi")),))
    )
    user = payload[-1]
    image_blocks = [b for b in _content(user) if b.get("type") == "image"]
    assert len(image_blocks) == 1


def test_openai_responses_attaches_input_image() -> None:
    items = responses_input(
        _request(),
        parse_error_class=OpenAIResponseParseError,
        attach_images=True,
    )
    assert isinstance(items, list)
    user = items[-1]
    image_parts = [c for c in _content(user) if c.get("type") == "input_image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"] == f"data:image/png;base64,{_B64}"


def test_openai_responses_attaches_with_messages() -> None:
    items = responses_input(
        _request(messages=(AgentUserMessage(content=ProductContent("hi")),)),
        parse_error_class=OpenAIResponseParseError,
        attach_images=True,
    )
    assert isinstance(items, list)
    user = items[-1]
    image_parts = [c for c in _content(user) if c.get("type") == "input_image"]
    assert len(image_parts) == 1


def test_google_attaches_inline_data_part() -> None:
    # Invoke the shared translator with the Google parameters (the Generative
    # AI adapter enables inlineData image attachment).
    contents = gemini_contents(
        _request(),
        parse_error_class=GoogleResponseParseError,
        attach_images=True,
    )
    user = contents[-1]
    assert user["role"] == "user"
    parts = cast(list[dict[str, Any]], user["parts"])
    inline = [p for p in parts if "inlineData" in p]
    assert len(inline) == 1
    assert inline[0]["inlineData"]["mimeType"] == "image/png"
    assert inline[0]["inlineData"]["data"] == _B64


def test_no_attachments_leaves_payload_text_only() -> None:
    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="hi",
        provider_name="p",
        model_id="m",
        cwd=Path("/tmp"),
    )
    payload = _anthropic_payload(request)
    assert all(
        block.get("type") != "image"
        for message in payload
        for block in _content(message)
    )
