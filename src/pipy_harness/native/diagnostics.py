"""Emitting one operator-facing diagnostic line, wherever the session is running.

A diagnostic has two possible destinations and the caller should not have to know
which: inside the TUI it becomes a notice in the frame, and headless it goes to
the error stream. Diagnostics routinely carry extension-supplied text, so an
unsanitized line could smuggle escape sequences into a terminal -- but the two
destinations sanitize in different places, and only one of them is here. The
stderr path sanitizes below; the notice path relies on the sink doing it, which
`TerminalUi.add_notice` does before the text reaches a frame. A sink that
did not sanitize would reintroduce that hole silently, so `NoticeSink` is a
deliberately tiny port with exactly one production implementation.

The sink is a one-method structural port rather than the terminal UI itself.
That is what lets this module sit below the UI, the REPL tier and the session at
once -- all three emit diagnostics, and a concrete `TerminalUi` here
would invert the dependency for two of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TextIO

from pipy_harness.native.agent.messages import AgentAssistantMessage, AgentMessage
from pipy_harness.native.session_tree_commands import sanitize_label_text


class NoticeSink(Protocol):
    """Anything that can show an operator notice in place of stderr."""

    def add_notice(self, message: str) -> None: ...


def emit_diagnostic(
    notice_sink: NoticeSink | None,
    error_stream: TextIO,
    message: str,
) -> None:
    """Show ``message`` as a notice, or print it sanitized to ``error_stream``."""

    if notice_sink is not None:
        notice_sink.add_notice(message)
        return
    safe_message = "\n".join(
        sanitize_label_text(line) for line in str(message).splitlines()
    )
    print(safe_message, file=error_stream)


def last_assistant_answer(messages: Sequence[AgentMessage]) -> str:
    """The most recent non-empty assistant answer, or an empty string."""

    for message in reversed(messages):
        if isinstance(message, AgentAssistantMessage):
            content = message.content.value.strip()
            if content:
                return message.content.value
    return ""
