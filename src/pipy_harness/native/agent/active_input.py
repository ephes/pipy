"""Identity-safe request and result views for one active agent input."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentMessage,
    AgentUserMessage,
)


@dataclass(frozen=True, slots=True)
class AgentActiveInput:
    """Bind one accepted user message to its transient request overlay.

    The accepted message is located by object identity, so history compaction
    may shift its index without changing either the request overlay position or
    the messages reported for the active run.
    """

    accepted_message: AgentUserMessage
    request_overlay: tuple[AgentUserMessage, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted_message, AgentUserMessage):
            raise TypeError("accepted_message must be AgentUserMessage")
        if not isinstance(self.request_overlay, tuple) or any(
            not isinstance(message, AgentUserMessage)
            for message in self.request_overlay
        ):
            raise TypeError(
                "request_overlay must be a tuple of AgentUserMessage values"
            )

    def request_messages(
        self, history: Sequence[AgentMessage]
    ) -> tuple[AgentMessage, ...]:
        """Overlay transient context immediately after the accepted message."""

        messages, accepted_index = self._anchored_history(history)
        if not self.request_overlay:
            return messages
        insertion_index = accepted_index + 1
        return (
            messages[:insertion_index]
            + self.request_overlay
            + messages[insertion_index:]
        )

    def result_messages(
        self, history: Sequence[AgentMessage]
    ) -> tuple[AgentMessage, ...]:
        """Return durable messages added by this run, excluding the overlay."""

        messages, accepted_index = self._anchored_history(history)
        return messages[accepted_index:]

    def transformed_request_messages(
        self,
        request_messages: Sequence[AgentMessage],
        provider_prompt: str,
    ) -> tuple[AgentMessage, ...]:
        """Rewrite only the accepted user message in a request-local view."""

        messages, accepted_index = self._anchored_history(request_messages)
        if provider_prompt == self.accepted_message.content.value:
            return messages
        transformed = list(messages)
        transformed[accepted_index] = replace(
            self.accepted_message,
            content=ProductContent(provider_prompt),
        )
        return tuple(transformed)

    def _anchored_history(
        self, history: Sequence[AgentMessage]
    ) -> tuple[tuple[AgentMessage, ...], int]:
        messages = tuple(history)
        if any(not isinstance(message, AgentMessage) for message in messages):
            raise TypeError("history must contain only AgentMessage values")
        matches = tuple(
            index
            for index, message in enumerate(messages)
            if message is self.accepted_message
        )
        if len(matches) != 1:
            raise ValueError("accepted_message must occur exactly once in history")
        return messages, matches[0]
