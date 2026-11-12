"""P15: signed tool-result text (Pi ``TextContent.textSignature``).

Pi keeps a tool result's text signature wherever it copies the content and
writes it to its session file, and its provider adapters send none for tool
results. pipy accepts signed text blocks, keeps them in the session tree and
the durable fact, and sends no signature on any wire.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_tool_result_image_wires import IMAGE, _request
from test_native_tool_result_images import _path, _stored_results, _tree

from pipy_harness.native.agent import AgentToolResultMessage, ProductContent
from pipy_harness.native.agent.content import TextContent
from pipy_harness.native.assistant_blocks_json import (
    tool_result_blocks_from_json,
    tool_result_blocks_to_json,
)
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.providers.anthropic_messages_wire import messages_payload
from pipy_harness.native.providers.chat_completions_wire import (
    ChatTranscriptOptions,
    chat_transcript,
)
from pipy_harness.native.providers.google_generate_content_wire import (
    gemini_contents,
)
from pipy_harness.native.providers.openai_responses_wire import responses_input
from pipy_harness.native.session_tree import NativeSessionTree

SIGNATURE = "msg_signed_tool_text"
SIGNED = TextContent("signed text", SIGNATURE)


def _signed(*blocks: Any, correlation: str = "call-1") -> AgentToolResultMessage:
    joined = "\n".join(b.text for b in blocks if isinstance(b, TextContent))
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{correlation}",
        tool_name="shot",
        content=ProductContent(joined),
        provider_correlation_id=correlation,
        blocks=blocks,
    )


def test_jsonl_blocks_carry_text_signature_in_pis_spelling():
    blocks = (SIGNED, IMAGE, TextContent("plain"))
    raw = tool_result_blocks_to_json(blocks)
    assert raw[0] == {"type": "text", "text": "signed text", "textSignature": SIGNATURE}
    assert raw[2] == {"type": "text", "text": "plain"}  # unsigned writes none
    assert tool_result_blocks_from_json(raw) == blocks
    with pytest.raises(ValueError):
        tool_result_blocks_from_json(
            [{"type": "text", "text": "x", "textSignature": 3}]
        )


@pytest.mark.parametrize("blocks", [(SIGNED,), (SIGNED, IMAGE)], ids=["text", "image"])
def test_the_session_file_saves_and_reloads_the_signature(tmp_path: Path, blocks):
    result = _signed(*blocks)
    tree = _tree(tmp_path, result)
    raw = [json.loads(line) for line in _path(tree).read_text().splitlines()]
    stored = [row["message"] for row in raw if row.get("type") == "message"][-1]
    assert stored["blocks"][0]["textSignature"] == SIGNATURE
    reopened = NativeSessionTree.open(_path(tree), strict=True)
    assert _stored_results(reopened) == [result]
    # History equality distinguishes signed from unsigned.
    assert result != _signed(
        *(replace(b, signature=None) if b is SIGNED else b for b in blocks)
    )


def _payloads(result: AgentToolResultMessage) -> list[Any]:
    request = _request(result)
    return [
        messages_payload(request, parse_error_class=ProviderHTTPError),
        responses_input(request, parse_error_class=ProviderHTTPError),
        chat_transcript(request, ChatTranscriptOptions()),
        gemini_contents(request, parse_error_class=ProviderHTTPError),
    ]


@pytest.mark.parametrize("blocks", [(SIGNED,), (SIGNED, IMAGE)], ids=["text", "image"])
def test_no_adapter_family_sends_a_tool_result_text_signature(blocks):
    for payload in _payloads(_signed(*blocks)):
        assert SIGNATURE not in json.dumps(payload, default=str)


def test_anthropic_sends_a_signed_text_only_result_as_its_text():
    """Pi converts content blocks only when the result has an image."""
    payload: Any = messages_payload(
        _request(_signed(SIGNED)), parse_error_class=ProviderHTTPError
    )
    assert payload[-1]["content"][0]["content"] == "signed text"


def test_hooks_and_extension_tools_keep_signed_text():
    """Review R1: a hook that returns the event's signed blocks, or an
    extension tool that returns a signed block, keeps the signature."""
    from pipy_harness.native.extension_hooks import (
        _replaced_content,
        bounded_result_blocks,
    )
    from pipy_harness.native.extension_types import ToolResultTransform

    assert bounded_result_blocks((SIGNED,), 1000, "[cut]") == (SIGNED,)
    cut = bounded_result_blocks((TextContent("x" * 50, SIGNATURE),), 20, "[cut]")
    assert cut is not None and cut[0].signature == SIGNATURE
    replaced = _replaced_content(ToolResultTransform(blocks=(SIGNED,)))
    assert replaced == ("signed text", (SIGNED,))
    unsigned = _replaced_content(ToolResultTransform(blocks=(TextContent("a"),)))
    assert unsigned == ("a", ())


def test_image_rewrites_leave_a_signed_text_only_result_alone():
    """Review R1: only results with images are rewritten for a model without
    image input or with images blocked; the caller's tuple is kept."""
    from pipy_harness.native.agent.messages import (
        without_blocked_images,
        without_tool_result_images,
    )

    history = (_signed(SIGNED),)
    assert without_tool_result_images(history) is history
    assert without_blocked_images(history) is history
