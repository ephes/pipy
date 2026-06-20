"""Parity row E2 behavior check: live in-session compaction.

Drives the product tool-loop REPL through the real ``PipyNativeToolReplAdapter``
with several plain user turns and an explicit ``/compact`` command, then proves
the adapter emits a ``native.session.compacted`` event whose safe counters show
context was actually compacted (a positive ``compaction_count`` and at least one
dropped user-turn group). It also proves the pure tool-loop compactor reduces a
message history at a user-turn boundary without orphaning a tool result
(provider message-protocol validity), and never leaks tool-result bodies into
the summary block.

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
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.session_compaction import compact_tool_loop_messages
from pipy_harness.native.tools.base import SUPPORTED_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


class _PlainToolProvider:
    """Tool-capable provider that always answers (no tool calls)."""

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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


def _adapter_compaction_event() -> Mapping[str, object] | None:
    sink = _RecordingEventSink()
    adapter = PipyNativeToolReplAdapter(
        provider=_PlainToolProvider(),
        input_stream=io.StringIO("a\nb\nc\nd\n/compact\n/exit\n"),
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
    return compacted[0] if compacted else None


def _adapter_compaction_holds() -> bool:
    payload = _adapter_compaction_event()
    if payload is None:
        return False
    if payload.get("compaction_count", 0) <= 0:
        return False
    if payload.get("compaction_dropped_group_count", 0) <= 0:
        return False
    return True


def _tool_loop_protocol_preserved() -> bool:
    correlation = "corr-old"
    messages = [
        UserMessage(content="old prompt 1"),
        AssistantMessage(content="", tool_calls=(
            ProviderToolCall(
                provider_correlation_id=correlation,
                tool_name="read",
                arguments_json='{"path": "x"}',
            ),
        )),
        ToolResultMessage(
            tool_request_id=f"{SUPPORTED_TOOL_REQUEST_ID_PREFIX}0001",
            output_text="SENSITIVE_TOOL_BODY",
            provider_correlation_id=correlation,
        ),
        AssistantMessage(content="done 1"),
        UserMessage(content="old prompt 2"),
        AssistantMessage(content="done 2"),
        UserMessage(content="recent prompt"),
        AssistantMessage(content="done 3"),
    ]
    result = compact_tool_loop_messages(messages, keep_recent_groups=1)
    if not result.changed:
        return False
    if not isinstance(result.messages[0], UserMessage):
        return False
    if "SENSITIVE_TOOL_BODY" in result.summary_block:
        return False
    seen: set[str] = set()
    for message in result.messages:
        if isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                seen.add(call.provider_correlation_id)
        if isinstance(message, ToolResultMessage):
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
