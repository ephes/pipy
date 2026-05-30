"""Parity row E2 behavior check: live in-session compaction.

Seeds a temporary session by running the no-tool REPL product path with a
deterministic fake provider and an explicit ``/compact`` command, then proves
that the finalized record carries a ``native.session.compacted`` event whose
safe counters show the provider-visible context was actually reduced. It also
proves the pure tool-loop compactor reduces a message history at a user-turn
boundary without orphaning a tool result (provider message-protocol validity).

Exits 0 when both behaviors hold, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_compaction import compact_tool_loop_messages
from pipy_harness.native.tools.base import SUPPORTED_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.runner import HarnessRunner


def _seeded_no_tool_compaction_record() -> list[dict]:
    root = Path(tempfile.mkdtemp())
    cwd = Path(tempfile.mkdtemp())
    adapter = PipyNativeReplAdapter(
        provider=FakeNativeProvider(),
        input_stream=io.StringIO("a\nb\nc\n/compact\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="parity-compaction",
            command=[],
            cwd=cwd,
            goal="parity compaction",
            root=root,
            capture_policy=CapturePolicy(),
        )
    )
    return [
        json.loads(line)
        for line in result.record.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]


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
    events = _seeded_no_tool_compaction_record()
    compacted = [e for e in events if e.get("type") == "native.session.compacted"]
    if not compacted:
        return 1
    payload = compacted[0].get("payload", {})
    if payload.get("compaction_dropped_exchange_count", 0) <= 0:
        return 1
    if payload.get("compaction_bytes_after", 1) >= payload.get(
        "compaction_bytes_before", 0
    ):
        return 1
    if not _tool_loop_protocol_preserved():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
