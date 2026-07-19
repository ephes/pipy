"""Sink contract for Pi-shaped automation events.

Canonical agent events are projected by ``AutomationAgentEventAdapter``. JSON
and RPC modes remain synchronous consumers of the resulting dictionaries.

The event vocabulary mirrors Pi's ``AgentEvent`` (`packages/agent/src/
types.ts`) plus the session-extension events of ``AgentSessionEvent``
(`packages/coding-agent/src/core/agent-session.ts`):

base lifecycle
    ``agent_start``/``agent_end``, ``turn_start``/``turn_end``,
    ``message_start``/``message_update``/``message_end``,
    ``tool_execution_start``/``tool_execution_update``/``tool_execution_end``
session extension
    ``queue_update``, ``compaction_start``/``compaction_end``,
    ``session_info_changed``, ``thinking_level_changed``,
    ``auto_retry_start``/``auto_retry_end``
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AutomationEventSink(Protocol):
    """Receives one Pi-shaped session event at a time.

    Implementations serialize to JSONL stdout (``--mode json``/``--mode rpc``)
    or collect events in tests. ``emit`` must be safe to call from the loop
    thread; a JSONL sink serializes writes through a single writer.
    """

    def emit(self, event: dict[str, Any]) -> None: ...
