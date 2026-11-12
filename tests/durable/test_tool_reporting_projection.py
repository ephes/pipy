"""The full fact stays exact while model text is bounded and image ordered."""

from datetime import datetime, timezone

import pytest

from pipy_harness.native.agent.content import ImageContent, ProductContent, TextContent
from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.tool_output_values import project_tool_output
from pipy_harness.native.durable.tool_values import (
    native_tool_output,
    read_native_tool_output,
)
from pipy_harness.native.tools.truncate import truncate_tail


@pytest.mark.parametrize(
    "texts",
    [
        (),
        ("",),
        ("", "tail"),
        ("head", ""),
        ("", ""),
        ("before", "after"),
        ("x" * 52000, "after"),
        ("😀" * 14000,),
        ("\n" * 2001, "tail"),
    ],
)
@pytest.mark.parametrize("failed", [False, True])
def test_fullfact_and_projected_image_text_remain_consistent(texts, failed):
    image = ImageContent("aW1hZ2U=", "image/png")
    blocks = (image,) + tuple(TextContent(text) for text in texts)
    message = AgentToolResultMessage(
        AGENT_TOOL_REQUEST_ID_PREFIX + "projection",
        "tool",
        ProductContent("\n".join(texts)),
        "call:projection",
        is_error=failed,
        blocks=blocks,
    )
    outcome = ToolExecutionOutcome(message, execution_seconds=0.25)
    raw = encode_canonical(outcome)
    notice = {"severity": "warning", "message": "bounded 😀 notice", "code": "fixture"}
    fact = native_tool_output(
        outcome,
        datetime.now(timezone.utc),
        diagnostics=[notice],
        failure="invocation_exception" if failed else None,
    )
    assert fact["outcome"] == raw
    _, decoded = read_native_tool_output(fact)
    projected = project_tool_output(fact, decoded)
    assert encode_canonical(decoded) == raw
    assert [b for b in projected.blocks if type(b) is ImageContent] == [image]
    assert (
        "\n".join(b.text for b in projected.blocks if type(b) is TextContent)
        == projected.content.value
    )
    tail = truncate_tail(message.content.value, max_bytes=50 * 1024, max_lines=2000)
    assert projected.content.value.startswith(tail.content)
    assert "[warning (fixture)] bounded 😀 notice" in projected.content.value
    assert len(projected.content.value) <= AgentToolResultMessage.CONTENT_MAX_LENGTH
    assert projected.content.value.encode("utf-8")
    assert all(b.signature is None for b in projected.blocks if type(b) is TextContent)


def _signed(signature="private-signature", image=True, text="text"):
    blocks = (TextContent(text, signature),)
    if image:
        blocks = (ImageContent("aW1hZ2U=", "image/png"), *blocks)
    return AgentToolResultMessage(
        AGENT_TOOL_REQUEST_ID_PREFIX + "signed",
        "tool",
        ProductContent(text),
        "call:signed",
        blocks=blocks,
    )


@pytest.mark.parametrize("image", [True, False], ids=["with-image", "text-only"])
def test_signed_tool_result_text_is_kept_like_pi(image):
    """P15: a signed text block is accepted and kept, also without an image."""
    message = _signed(image=image)
    assert message.blocks[-1] == TextContent("text", "private-signature")
    # Unsigned text-only blocks still say no more than the text and are dropped.
    plain = AgentToolResultMessage(
        AGENT_TOOL_REQUEST_ID_PREFIX + "plain",
        "tool",
        ProductContent("text"),
        "call:plain",
        blocks=(TextContent("text"),),
    )
    assert plain.blocks == ()
    # History equality includes the signature, as Pi's structural equality does.
    assert message != _signed(signature=None, image=True) if image else True
    assert message != _signed(signature="other", image=image)


def test_tool_result_blocks_still_refuse_foreign_blocks():
    from pipy_harness.native.agent.content import ThinkingContent

    with pytest.raises(TypeError, match="TextContent or ImageContent"):
        AgentToolResultMessage(
            AGENT_TOOL_REQUEST_ID_PREFIX + "foreign",
            "tool",
            ProductContent(""),
            "call:foreign",
            blocks=(ThinkingContent("x"),),
        )


@pytest.mark.parametrize("image", [True, False], ids=["with-image", "text-only"])
def test_signed_tool_result_round_trips_through_the_canonical_codec(image):
    """The codec's blocks schema has carried signatures since version 2; a signed
    result reads back byte-identical at the current version, and the producer
    DTO accepts it too."""
    from pipy_harness.native.durable.canonical_codec import decode_canonical
    from pipy_harness.native.tools.base import ToolExecutionResult

    message = _signed(image=image)
    for version in (None, 3):
        arena = encode_canonical(message, codec_version=version)
        decoded = decode_canonical(arena)
        assert decoded == message
        assert encode_canonical(decoded, codec_version=arena["codec_version"]) == arena
    result = ToolExecutionResult(
        AGENT_TOOL_REQUEST_ID_PREFIX + "producer", "text", blocks=message.blocks
    )
    assert result.blocks == message.blocks


def test_truncation_keeps_the_signature_on_the_kept_slice_never_on_the_suffix():
    """Pi's boundContent spreads each item, so a bounded signed block keeps its
    signature; the harness suffix block is unsigned."""
    long = "x" * 60000
    message = _signed(text=long, image=False)
    outcome = ToolExecutionOutcome(message, execution_seconds=0.25)
    notice = {"severity": "warning", "message": "note", "code": "fixture"}
    fact = native_tool_output(outcome, datetime.now(timezone.utc), diagnostics=[notice])
    _, decoded = read_native_tool_output(fact)
    projected = project_tool_output(fact, decoded)
    kept, suffix = projected.blocks
    assert kept.signature == "private-signature" and len(kept.text) < len(long)
    assert suffix.signature is None and suffix.text.startswith("<harness>")
    assert (
        "\n".join(b.text for b in projected.blocks if type(b) is TextContent)
        == projected.content.value
    )
