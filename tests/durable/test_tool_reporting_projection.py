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


def test_existing_tool_result_domain_rejects_signed_text_before_fact_creation():
    with pytest.raises(TypeError, match="unsigned TextContent"):
        AgentToolResultMessage(
            AGENT_TOOL_REQUEST_ID_PREFIX + "signed",
            "tool",
            ProductContent("text"),
            "call:signed",
            blocks=(
                ImageContent("aW1hZ2U=", "image/png"),
                TextContent("text", "private-signature"),
            ),
        )


def test_saved_tool_result_cannot_introduce_a_signed_text_block():
    from pipy_harness.native.durable.canonical_codec import (
        CanonicalCodecError,
        decode_canonical,
    )
    from pipy_harness.native.tools.base import ToolExecutionResult

    blocks = (ImageContent("aW1hZ2U=", "image/png"), TextContent("text"))
    message = AgentToolResultMessage(
        AGENT_TOOL_REQUEST_ID_PREFIX + "stored",
        "tool",
        ProductContent("text"),
        "call:stored",
        blocks=blocks,
    )
    arena = encode_canonical(message)
    text_node = next(node for node in arena["nodes"] if node["tag"] == "TextContent")
    signature = text_node["children"][text_node["keys"].index("signature")]
    arena["nodes"][signature] = {
        "tag": "string",
        "value": "private-signature".encode().hex(),
    }
    with pytest.raises(CanonicalCodecError):
        decode_canonical(arena)
    with pytest.raises(TypeError, match="unsigned TextContent"):
        ToolExecutionResult(
            AGENT_TOOL_REQUEST_ID_PREFIX + "producer",
            "text",
            blocks=(blocks[0], TextContent("text", "private-signature")),
        )
