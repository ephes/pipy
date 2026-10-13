"""Data-classification marker for the canonical agent boundary.

Agent events carry product content: prompts, model output, reasoning, tool
arguments, and tool results. No summary-safe archive DTO or generic
event-to-archive serializer belongs here; a later workflow adapter must
explicitly allowlist metadata at that boundary.

The text and thinking blocks of Pi's ordered ``AssistantMessage.content``
live here too, so both the provider result (``models.py``) and the canonical
message hold them. Their ``signature`` is Pi's opaque provider replay data:
``thinkingSignature`` (the Anthropic signature or redacted payload, the OpenAI
Responses reasoning item as JSON, a Gemini ``thoughtSignature``) and
``textSignature`` (the Responses message id and phase, a Gemini
``thoughtSignature``). Only the model that produced it reads it back.

A tool result's ordered content (Pi ``ToolResultMessage.content``: text and
image blocks) is defined here as well, so the tool port and the canonical
message share one definition.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductContent:
    """Full-content product/automation data that must not enter the archive."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("ProductContent.value must be a string")

    def __repr__(self) -> str:
        """Keep full-content payloads out of diagnostics and assertion diffs."""

        return "ProductContent(<redacted>)"


REDACTED_THINKING_TEXT = "[Reasoning redacted]"
"""Pi's placeholder text for an Anthropic ``redacted_thinking`` block."""


def _require_optional_str(value: object, field_name: str) -> None:
    if value is not None and type(value) is not str:
        raise TypeError(f"{field_name} must be a string or None")


@dataclass(frozen=True, slots=True)
class TextContent:
    """Pi ``TextContent``: visible assistant text and its ``textSignature``."""

    text: str
    signature: str | None = None

    def __post_init__(self) -> None:
        if type(self.text) is not str:
            raise TypeError("TextContent.text must be a string")
        _require_optional_str(self.signature, "TextContent.signature")

    def __repr__(self) -> str:
        return "TextContent(<redacted>)"


@dataclass(frozen=True, slots=True)
class ThinkingContent:
    """Pi ``ThinkingContent``: reasoning text, ``thinkingSignature``, ``redacted``."""

    thinking: str
    signature: str | None = None
    redacted: bool = False

    def __post_init__(self) -> None:
        if type(self.thinking) is not str:
            raise TypeError("ThinkingContent.thinking must be a string")
        _require_optional_str(self.signature, "ThinkingContent.signature")
        if type(self.redacted) is not bool:
            raise TypeError("ThinkingContent.redacted must be a bool")

    def __repr__(self) -> str:
        return "ThinkingContent(<redacted>)"


@dataclass(frozen=True, slots=True)
class ImageContent:
    """Pi ``ImageContent``: base64 image data and its MIME type.

    Neither field is checked beyond its type: like Pi, pipy passes on what a
    tool returned. A producer that takes data from a script or a file checks
    it before it builds one.
    """

    data: str
    mime_type: str

    def __post_init__(self) -> None:
        if type(self.data) is not str or not self.data:
            raise TypeError("ImageContent.data must be a non-empty string")
        if type(self.mime_type) is not str or not self.mime_type:
            raise TypeError("ImageContent.mime_type must be a non-empty string")

    def __repr__(self) -> str:
        return "ImageContent(<redacted>)"


ToolResultBlock = TextContent | ImageContent
"""One entry of Pi's ordered ``ToolResultMessage.content``."""

ESTIMATED_IMAGE_CHARS = 4800
"""Pi ``ESTIMATED_IMAGE_CHARS``: what one image counts in a token estimate."""


def tool_result_text(blocks: Iterable[object]) -> str:
    """The text of a tool result's blocks, as Pi's adapters and rows join it."""

    return "\n".join(block.text for block in blocks if isinstance(block, TextContent))


def tool_result_images(blocks: Iterable[object]) -> tuple[ImageContent, ...]:
    """The image blocks of a tool result, in order."""

    return tuple(block for block in blocks if isinstance(block, ImageContent))


def normalized_tool_result_blocks(
    blocks: object, text: str, owner: str
) -> tuple[ToolResultBlock, ...]:
    """Check a tool result's ``blocks`` against its text.

    A result has blocks only when it has an image: its text blocks joined by
    newlines are then its text. Blocks without an image say no more than the
    text does and are dropped, so a result compares equal to one built from
    that text.
    """

    if type(blocks) is not tuple:
        raise TypeError(f"{owner}.blocks must be a tuple")
    if not blocks:
        return ()
    for block in blocks:
        # The fields are checked again: a frozen value can be changed behind
        # its constructor, and this is also what validates a stored result.
        if type(block) is ImageContent:
            ImageContent.__post_init__(block)
            continue
        if type(block) is not TextContent or block.signature is not None:
            raise TypeError(
                f"{owner}.blocks must hold unsigned TextContent or ImageContent values"
            )
        TextContent.__post_init__(block)
    if tool_result_text(blocks) != text:
        raise ValueError(f"{owner}.blocks text must equal its text")
    if not any(type(block) is ImageContent for block in blocks):
        return ()
    return blocks


def require_tool_result_blocks(blocks: object, text: str, owner: str) -> None:
    """Require the blocks a constructed result holds: none, or its text and
    at least one image."""

    if normalized_tool_result_blocks(blocks, text, owner) != blocks:
        raise ValueError(f"{owner}.blocks must hold an image")


def display_segments(blocks: Iterable[object]) -> list[tuple[bool, str]]:
    """What Pi's ``AssistantMessageComponent`` draws, in order.

    ``(True, text)`` is a run of consecutive thinking blocks (the non-blank
    ones trimmed and joined by a blank line); ``(False, text)`` a text block
    with visible text. Other blocks (tool calls) are drawn elsewhere.
    """

    segments: list[tuple[bool, str]] = []
    run: list[str] | None = None
    for block in blocks:
        if isinstance(block, ThinkingContent):
            if run is None:
                run = []
            if block.thinking.strip():
                run.append(block.thinking.strip())
            continue
        if run:
            segments.append((True, "\n\n".join(run)))
        run = None
        if isinstance(block, TextContent) and block.text.strip():
            segments.append((False, block.text))
    if run:
        segments.append((True, "\n\n".join(run)))
    return segments
