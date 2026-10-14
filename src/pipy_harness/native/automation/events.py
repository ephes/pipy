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
    ``auto_retry_start``/``auto_retry_end``,
    ``summarization_retry_scheduled``/``summarization_retry_attempt_start``/
    ``summarization_retry_finished`` (bounded private-summary statuses)
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AutomationEventSink(Protocol):
    """Receives one Pi-shaped session event at a time.

    Implementations serialize to JSONL stdout (``--mode json``/``--mode rpc``)
    or collect events in tests. ``emit`` must be safe to call from the loop
    thread; a JSONL sink serializes writes through a single writer.
    """

    def emit(self, event: dict[str, Any]) -> None: ...


@runtime_checkable
class AutomationUi(Protocol):
    """The UI a session has through its automation transport.

    RPC mode has one (``rpc_ui.RpcUi``): the requests go to the client as
    ``extension_ui_request`` lines. Print and JSON mode have none.
    """

    def notify(self, message: str, kind: str = "info") -> None: ...

    def select(
        self,
        title: str,
        options: Sequence[str],
        cancel_event: threading.Event | None = None,
    ) -> str | None: ...

    def input(
        self,
        title: str,
        placeholder: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str | None: ...

    def extension_error(self, extension_path: str, event: str, error: str) -> None: ...


def automation_ui(observer: object) -> AutomationUi | None:
    """The UI of the transport behind ``observer``: its ``ui``, if it has one."""

    ui = getattr(observer, "ui", None)
    return ui if isinstance(ui, AutomationUi) else None
