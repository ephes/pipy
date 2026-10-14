"""Normalize the images of a tool result as they enter history, ported from Pi.

Mirrors ``normalizeToolResultImages``
(``packages/coding-agent/src/utils/tool-result-images.ts``) and the call in
``AgentSession._afterToolCall`` (``core/agent-session.ts``) of pi-mono
``a447ba11``. Tools that make images themselves (MCP servers, codemode
scripts, extensions) return any base64 payload; an oversized image makes
the provider reject the whole conversation, not only the turn, so each image
goes through :func:`~pipy_harness.native.image_process.process_image` once,
after the ``tool_result`` hooks, for every call the model or a tool made.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from pipy_harness.native.agent.content import (
    ImageContent,
    ProductContent,
    TextContent,
    ToolResultBlock,
    tool_result_text,
)
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.image_process import (
    ImageResizeOptions,
    decode_base64,
    process_image,
)
from pipy_harness.native.settings import SettingsManager


def normalize_tool_result_images(
    blocks: tuple[ToolResultBlock, ...],
    *,
    auto_resize: bool = True,
    options: ImageResizeOptions | None = None,
) -> tuple[ToolResultBlock, ...]:
    """Pi ``normalizeToolResultImages``: each image as a provider takes it.

    An image that was converted or resized is replaced, and a text block
    with its hints (conversion, original size and coordinate scale) follows
    it. An image that cannot be processed is kept as the tool returned it.
    The same tuple comes back when nothing changed.
    """

    if not any(isinstance(block, ImageContent) for block in blocks):
        return blocks
    normalized: list[ToolResultBlock] = []
    changed = False
    for block in blocks:
        if not isinstance(block, ImageContent):
            normalized.append(block)
            continue
        processed = process_image(
            decode_base64(block.data),
            block.mime_type,
            auto_resize=auto_resize,
            options=options,
        )
        # Data that decodes to nothing has nothing to send in its place.
        if (
            processed is None
            or not processed.data
            or (
                processed.data == block.data
                and processed.mime_type == block.mime_type
                and not processed.hints
            )
        ):
            normalized.append(block)
            continue
        normalized.append(ImageContent(processed.data, processed.mime_type))
        if processed.hints:
            normalized.append(TextContent("\n".join(processed.hints)))
        changed = True
    return tuple(normalized) if changed else blocks


def normalize_tool_result(
    result: AgentToolResultMessage, *, auto_resize: bool = True
) -> AgentToolResultMessage:
    """The result with its images normalized; the same object if unchanged.

    A result's text is its text blocks joined, so hint lines become part of
    it. Hints that would take the text past
    :attr:`AgentToolResultMessage.CONTENT_MAX_LENGTH` are left out, and the
    processed images are kept.
    """

    if not result.blocks:
        return result
    blocks = normalize_tool_result_images(result.blocks, auto_resize=auto_resize)
    if blocks is result.blocks:
        return result
    text = tool_result_text(blocks)
    if len(text) > result.CONTENT_MAX_LENGTH:
        # A hint is the only text block the result did not have.
        kept = frozenset(id(block) for block in result.blocks)
        blocks = tuple(
            block
            for block in blocks
            if not isinstance(block, TextContent) or id(block) in kept
        )
        text = tool_result_text(blocks)
    return replace(result, content=ProductContent(text), blocks=blocks)


def tool_result_normalizer(
    settings: SettingsManager,
) -> Callable[[AgentToolResultMessage], AgentToolResultMessage]:
    """The agent loop's normalizer; ``images.autoResize`` is read per result,
    so a change takes effect at once (Pi ``getImageAutoResize``)."""

    def normalize(result: AgentToolResultMessage) -> AgentToolResultMessage:
        if not result.blocks:
            return result
        return normalize_tool_result(
            result, auto_resize=settings.get_image_auto_resize()
        )

    return normalize
