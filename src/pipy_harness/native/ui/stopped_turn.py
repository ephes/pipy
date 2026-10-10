"""Shared live/restored marker for a canonical stopped assistant message."""

from pipy_harness.native.agent import AgentAssistantMessage, AgentStopReason


def stopped_assistant_marker(message: AgentAssistantMessage) -> str | None:
    """Pi ``AssistantMessageComponent``'s line after an aborted/failed turn."""

    if message.stop_reason is AgentStopReason.ABORTED:
        if message.error_message and message.error_message != "Request was aborted":
            return message.error_message
        return "Operation aborted"
    if message.stop_reason is AgentStopReason.ERROR:
        return f"Error: {message.error_message or 'Unknown error'}"
    return None


def stopped_tool_call_text(message: AgentAssistantMessage) -> str:
    """The display-only failed result of a stopped message's partial call."""

    if message.stop_reason is AgentStopReason.ABORTED:
        return "Operation aborted"
    return message.error_message or "Error"
