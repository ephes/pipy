"""Assemble Pi's ordered assistant content from a provider turn.

Pi's stream functions build ``AssistantMessage.content`` block by block and
keep whatever was streamed when a turn is aborted or fails
(``agent-loop.ts``). pipy adapters report their ordered output on
``ProviderResult.content_blocks`` (or ``ProviderPartial`` on a cancellation);
a streaming provider that reports no blocks is covered by the recorded text
and reasoning deltas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pipy_harness.native.agent.content import (
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.events import (
    AgentEvent,
    AssistantReasoningDelta,
    AssistantTextDelta,
    RetryScheduled,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentContentBlock,
    AgentMessageUsage,
    AgentStopReason,
    AgentToolCall,
)
from pipy_harness.native.agent.partial_arguments import normalize_partial_arguments
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.models import (
    ProviderContentBlock,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)


class PartialAssistantContent:
    """Forward provider-turn events and record the content streamed so far.

    Consecutive text or reasoning deltas of this turn form one text or
    thinking block, in the order they streamed: Pi's partial assistant
    message. A scheduled retry starts a new attempt, so it discards the failed
    attempt's blocks.
    """

    __slots__ = ("_segments", "_sink", "_turn_index")

    def __init__(self, sink: AgentEventSink, turn_index: int) -> None:
        self._sink = sink
        self._turn_index = turn_index
        self._segments: list[tuple[bool, list[str]]] = []

    def emit(self, event: AgentEvent) -> None:
        self._sink.emit(event)
        if isinstance(event, RetryScheduled):
            self._segments.clear()
        elif (
            isinstance(event, (AssistantTextDelta, AssistantReasoningDelta))
            and event.turn_index == self._turn_index
        ):
            thinking = isinstance(event, AssistantReasoningDelta)
            if not self._segments or self._segments[-1][0] is not thinking:
                self._segments.append((thinking, []))
            self._segments[-1][1].append(event.delta.value)

    def blocks(self) -> tuple[TextContent | ThinkingContent, ...]:
        """The streamed blocks, text and thinking, in order."""

        return tuple(
            ThinkingContent("".join(chunks))
            if thinking
            else TextContent("".join(chunks))
            for thinking, chunks in self._segments
        )

    @property
    def text(self) -> str:
        return "".join(
            block.text for block in self.blocks() if isinstance(block, TextContent)
        )


def agent_blocks(
    blocks: Sequence[ProviderContentBlock],
) -> tuple[AgentContentBlock, ...]:
    """Map a provider's ordered output onto canonical message blocks."""

    return tuple(
        _agent_tool_call(block) if isinstance(block, ProviderToolCall) else block
        for block in blocks
    )


def _agent_tool_call(call: ProviderToolCall) -> AgentToolCall:
    return AgentToolCall(
        call.provider_correlation_id,
        call.tool_name,
        ProductContent(call.arguments_json),
        thought_signature=call.thought_signature,
    )


def assistant_message(
    result: ProviderResult,
    usage: AgentMessageUsage,
    request: ProviderRequest,
    streamed: Sequence[TextContent | ThinkingContent] = (),
) -> AgentAssistantMessage:
    """The completed assistant message of a successful provider result.

    The adapter's ordered blocks when it reported them; otherwise any
    streamed reasoning, then the final text and the tool calls.
    """

    blocks: tuple[AgentContentBlock, ...]
    if result.content_blocks:
        blocks = agent_blocks(result.content_blocks)
    else:
        thinking = tuple(b for b in streamed if isinstance(b, ThinkingContent))
        text = result.final_text or ""
        blocks = (
            *thinking,
            *((TextContent(text),) if text else ()),
            *agent_blocks(result.tool_calls),
        )
    return AgentAssistantMessage.from_blocks(
        _bounded(blocks),
        usage=usage,
        provider=request.provider_name or None,
        api=result.api or None,
        model=request.model_id or None,
        provider_thinking_level=result.provider_thinking_level,
    )


def stopped_assistant(
    blocks: Sequence[AgentContentBlock],
    stop_reason: AgentStopReason,
    error_message: str | None = None,
    *,
    usage: AgentMessageUsage,
    request: ProviderRequest | None,
    provider_thinking_level: str | None = None,
) -> AgentAssistantMessage:
    """Pi's stopped text/thinking and best-effort streamed argument values."""

    normalized = tuple(
        replace(
            block,
            arguments_json=ProductContent(
                normalize_partial_arguments(block.arguments_json.value)
            ),
        )
        if isinstance(block, AgentToolCall)
        else block
        for block in blocks
    )
    return AgentAssistantMessage.from_blocks(
        _bounded(normalized),
        stop_reason=stop_reason,
        error_message=error_message,
        usage=usage,
        provider=request.provider_name or None if request is not None else None,
        model=request.model_id or None if request is not None else None,
        provider_thinking_level=provider_thinking_level,
    )


def _bounded(blocks: Sequence[AgentContentBlock]) -> tuple[AgentContentBlock, ...]:
    """Keep the joined text within ``AgentAssistantMessage.CONTENT_MAX_LENGTH``."""

    room = AgentAssistantMessage.CONTENT_MAX_LENGTH
    bounded: list[AgentContentBlock] = []
    for block in blocks:
        if isinstance(block, TextContent):
            if len(block.text) > room:
                block = TextContent(block.text[:room], block.signature)
            room -= len(block.text)
        bounded.append(block)
    return tuple(bounded)
