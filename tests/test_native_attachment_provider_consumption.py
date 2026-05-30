"""Parity row D8: multimodal provider adapters consume image attachments.

Proves each supporting adapter turns a ``ProviderRequest.attachments`` entry
into its native image content block, attached to the current user message.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, cast

from pipy_harness.native.anthropic_provider import _messages_payload
from pipy_harness.native.google_provider import _gemini_contents
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.openai_provider import _responses_input
from pipy_harness.native.tools.messages import LoopMessage, UserMessage


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


def _request(*, messages: tuple[LoopMessage, ...] = ()) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="sys",
        user_prompt="describe this",
        provider_name="p",
        model_id="m",
        cwd=Path("/tmp"),
        messages=messages,
        attachments=(_attachment(),),
    )


def test_anthropic_attaches_image_block_to_last_user_message() -> None:
    payload = _messages_payload(_request())
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
    payload = _messages_payload(_request(messages=(UserMessage(content="hi"),)))
    user = payload[-1]
    image_blocks = [b for b in _content(user) if b.get("type") == "image"]
    assert len(image_blocks) == 1


def test_openai_responses_attaches_input_image() -> None:
    items = _responses_input(_request())
    assert isinstance(items, list)
    user = items[-1]
    image_parts = [c for c in _content(user) if c.get("type") == "input_image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"] == f"data:image/png;base64,{_B64}"


def test_openai_responses_attaches_with_messages() -> None:
    items = _responses_input(_request(messages=(UserMessage(content="hi"),)))
    assert isinstance(items, list)
    user = items[-1]
    image_parts = [c for c in _content(user) if c.get("type") == "input_image"]
    assert len(image_parts) == 1


def test_google_attaches_inline_data_part() -> None:
    contents = _gemini_contents(_request())
    user = cast(dict[str, Any], contents[-1])
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
    payload = _messages_payload(request)
    assert all(
        block.get("type") != "image"
        for message in payload
        for block in _content(message)
    )
