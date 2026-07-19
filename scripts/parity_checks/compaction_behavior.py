"""Parity row E2 behavior check: live in-session compaction.

Drives the product tool-loop REPL through the real ``PipyNativeToolReplAdapter``
with several plain user turns and an explicit ``/compact`` command, then proves
the adapter emits a ``native.session.compacted`` event whose safe counters show
context was actually compacted (a positive ``compaction_count`` and at least one
dropped user-turn group). It also proves the pure canonical agent-history
compactor reduces a message history at a user-turn boundary without orphaning
a tool result (provider message-protocol validity). Product-owned summary
construction is checked on the subsequent provider request and never leaks
dropped content.

Exits 0 when both behaviors hold, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.history import compact_agent_history
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
)


class _PlainToolProvider:
    """Tool-capable provider that always answers (no tool calls)."""

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="answer",
            tool_calls=(),
        )


class _RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, object] | None]] = []

    def emit(self, event_type, *, summary, payload=None):  # noqa: ANN001
        self.events.append((event_type, payload))


def _adapter_compaction_observation() -> tuple[
    Mapping[str, object] | None,
    tuple[ProviderRequest, ...],
]:
    sink = _RecordingEventSink()
    provider = _PlainToolProvider()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO(
            "SENSITIVE_OLD_A\nSENSITIVE_OLD_B\nrecent-c\nrecent-d\n"
            "/compact\nafter\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        tool_budget=3,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="parity-compaction",
            command=[],
            cwd=Path(mkdtemp()),
            goal="parity compaction",
            capture_policy=CapturePolicy(),
        )
    )
    adapter.run(prepared, event_sink=sink, capture_policy=CapturePolicy())
    compacted = [p for (t, p) in sink.events if t == "native.session.compacted"]
    return compacted[0] if compacted else None, tuple(provider.requests)


def _adapter_compaction_holds() -> bool:
    payload, requests = _adapter_compaction_observation()
    if payload is None:
        return False
    if not _positive_int(payload.get("compaction_count")):
        return False
    if not _positive_int(payload.get("compaction_dropped_group_count")):
        return False
    if not requests:
        return False
    final_request = requests[-1]
    if "[Context compacted to save space:" not in final_request.system_prompt:
        return False
    for forbidden in ("SENSITIVE_OLD_A", "SENSITIVE_OLD_B"):
        if forbidden in final_request.system_prompt:
            return False
        if any(
            forbidden in message.content.value for message in final_request.messages
        ):
            return False
    return True


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _tool_loop_protocol_preserved() -> bool:
    correlation = "corr-old"
    messages = [
        AgentUserMessage(content=ProductContent("old prompt 1")),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall(
                    provider_correlation_id=correlation,
                    tool_name="read",
                    arguments_json=ProductContent('{"path": "x"}'),
                ),
            ),
        ),
        AgentToolResultMessage(
            tool_request_id=f"{AGENT_TOOL_REQUEST_ID_PREFIX}0001",
            tool_name="read",
            content=ProductContent("SENSITIVE_TOOL_BODY"),
            provider_correlation_id=correlation,
        ),
        AgentAssistantMessage(content=ProductContent("done 1")),
        AgentUserMessage(content=ProductContent("old prompt 2")),
        AgentAssistantMessage(content=ProductContent("done 2")),
        AgentUserMessage(content=ProductContent("recent prompt")),
        AgentAssistantMessage(content=ProductContent("done 3")),
    ]
    result = compact_agent_history(messages, keep_recent_groups=1)
    if not result.changed:
        return False
    if not isinstance(result.messages[0], AgentUserMessage):
        return False
    if any(
        "SENSITIVE_TOOL_BODY" in message.content.value for message in result.messages
    ):
        return False
    seen: set[str] = set()
    for message in result.messages:
        if isinstance(message, AgentAssistantMessage):
            for call in message.tool_calls:
                seen.add(call.provider_correlation_id)
        if isinstance(message, AgentToolResultMessage):
            if message.provider_correlation_id not in seen:
                return False
    return True


def main() -> int:
    if not _adapter_compaction_holds():
        return 1
    if not _tool_loop_protocol_preserved():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
