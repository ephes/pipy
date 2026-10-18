"""Pi ``transformMessages`` for an assistant message's ordered content.

``packages/ai/src/api/transform-messages.ts``: before an adapter serializes
history, every assistant message is compared with the target model. Only the
model that produced a thinking block can read its signature, so another
model receives no thinking, text without its provider signature, and tool
calls without a Gemini ``thoughtSignature``. Tool-call ids stay as stored:
each wire applies its own id projection (``tool_call_ids.py``, D8a).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.content import (
    ImageContent,
    TextContent,
    ThinkingContent,
    tool_result_images,
)
from pipy_harness.native.agent.messages import AgentContentBlock
from pipy_harness.native.models import ProviderRequest

TOOL_IMAGE_ONLY_TEXT = "(see attached image)"
"""Pi: the text of a tool result that is images only, in every adapter."""


def tool_result_parts(
    message: AgentToolResultMessage,
) -> tuple[str, tuple[ImageContent, ...]]:
    """A tool result as the adapters that cannot interleave send it: its
    text (Pi's placeholder when it is images only) and its images."""

    images = tool_result_images(message.blocks)
    text = message.content.value
    return (text or TOOL_IMAGE_ONLY_TEXT if images else text), images


def image_data_url(image: ImageContent) -> str:
    return f"data:{image.mime_type};base64,{image.data}"


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    """The model a request goes to (Pi ``model.provider/api/id``)."""

    provider: str
    api: str
    model: str

    @classmethod
    def of(cls, request: ProviderRequest, api: str) -> ReplayTarget:
        return cls(request.provider_name, api, request.model_id)

    def produced(self, message: AgentAssistantMessage) -> bool:
        """Pi ``isSameModel``: provider, API and model id all match."""

        return (
            message.provider == self.provider
            and message.api == self.api
            and message.model == self.model
        )


def transform_assistant_blocks(
    message: AgentAssistantMessage, target: ReplayTarget | None
) -> tuple[AgentContentBlock, ...]:
    """The blocks ``target`` may see of ``message`` (Pi's first pass).

    ``None`` is a target that produced nothing (every message is foreign).
    """

    same = target is not None and target.produced(message)
    transformed: list[AgentContentBlock] = []
    for block in message.ordered_content():
        if isinstance(block, ThinkingContent):
            if not same:
                continue
            if block.redacted or block.signature:
                transformed.append(block)
                continue
            if not block.thinking.strip():
                continue
            transformed.append(block)
        elif isinstance(block, TextContent):
            transformed.append(block if same else TextContent(block.text))
        elif isinstance(block, AgentToolCall):
            if not same and block.thought_signature is not None:
                block = replace(block, thought_signature=None)
            transformed.append(block)
    return tuple(transformed)
