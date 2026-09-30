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
