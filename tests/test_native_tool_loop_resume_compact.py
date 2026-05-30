"""Tool-loop product tests: resume seeding, /compact, tool-message validity."""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import NativeToolReplSession, ProviderToolCall
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.session_resume import ResumeContext
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


class _RecordingToolProvider:
    """Tool-capable provider that records requests and replays a call script."""

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(
        self,
        *,
        call_script: tuple[tuple[ProviderToolCall, ...], ...] = (),
    ) -> None:
        self.requests: list[ProviderRequest] = []
        self.call_script = call_script
        self._call_index = 0

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        calls: tuple[ProviderToolCall, ...] = ()
        if self._call_index < len(self.call_script):
            calls = self.call_script[self._call_index]
        self._call_index += 1
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="" if calls else "answer",
            tool_calls=calls,
        )


class _RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, object] | None]] = []

    def emit(self, event_type, *, summary, payload=None):  # noqa: ANN001
        self.events.append((event_type, payload))


def _resume_context() -> ResumeContext:
    return ResumeContext(
        prior_session_id="2026-04-30T133000Z-studio-pipy-native-parent",
        prior_provider_name="fake",
        prior_model_id="fake-native-bootstrap",
        prior_turn_count=3,
        prior_workspace_hash="HASH",
        prior_started_at="2026-04-30T13:30:00+00:00",
        prior_ended_at="2026-04-30T14:00:00+00:00",
        prior_summary="PRIOR_SUMMARY_SECRET_BODY",
    )


def test_tool_loop_resume_seeds_system_prompt_and_banner(tmp_path: Path) -> None:
    provider = _RecordingToolProvider()
    error_stream = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("hi\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
        tool_budget=3,
        resume_context=_resume_context(),
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="resumed-tl",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )
    adapter.run(prepared, event_sink=_RecordingEventSink(), capture_policy=CapturePolicy())

    assert provider.requests
    system_prompt = provider.requests[0].system_prompt
    assert "Resumed from session" in system_prompt
    assert "PRIOR_SUMMARY_SECRET_BODY" not in system_prompt

    stderr = error_stream.getvalue()
    assert "Resumed (resume) from session" in stderr
    assert "PRIOR_SUMMARY_SECRET_BODY" not in stderr


def test_tool_loop_manual_compact_reduces_history_and_keeps_protocol(
    tmp_path: Path,
) -> None:
    provider = _RecordingToolProvider()
    session = NativeToolReplSession(provider=provider, tool_budget=3)
    # Four plain turns build four user-turn groups, then /compact, then a fifth
    # turn whose request we inspect.
    input_stream = io.StringIO("a\nb\nc\nd\n/compact\ne\n/exit\n")
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.compaction_count == 1
    assert "compacted conversation context" in error_stream.getvalue()

    # The fifth turn's request is the last one captured.
    final_request = provider.requests[-1]
    assert "[Context compacted" in final_request.system_prompt
    # Protocol validity: retained history starts at a user message and never
    # orphans a tool result.
    messages = final_request.messages
    assert messages
    assert isinstance(messages[0], UserMessage)
    seen: set[str] = set()
    for message in messages:
        if isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                seen.add(call.provider_correlation_id)
        if isinstance(message, ToolResultMessage):
            assert message.provider_correlation_id in seen


def test_tool_loop_compaction_with_tool_calls_no_orphans(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    read_call = ProviderToolCall(
        provider_correlation_id="call_read_1",
        tool_name="read",
        arguments_json='{"path": "notes.txt"}',
    )
    # Turn 1 emits a tool call then a final answer (2 provider calls). Turns
    # 2-4 are plain. Then /compact, then a fifth plain turn.
    provider = _RecordingToolProvider(call_script=((read_call,), ()))
    session = NativeToolReplSession(provider=provider, tool_budget=3)
    input_stream = io.StringIO("read it\nb\nc\nd\n/compact\ne\n/exit\n")
    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.compaction_count == 1
    final_messages = provider.requests[-1].messages
    assert isinstance(final_messages[0], UserMessage)
    seen: set[str] = set()
    for message in final_messages:
        if isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                seen.add(call.provider_correlation_id)
        if isinstance(message, ToolResultMessage):
            assert message.provider_correlation_id in seen


def test_tool_loop_adapter_emits_compaction_event(tmp_path: Path) -> None:
    provider = _RecordingToolProvider()
    sink = _RecordingEventSink()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("a\nb\nc\nd\n/compact\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        tool_budget=3,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="compact-tl",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )
    adapter.run(prepared, event_sink=sink, capture_policy=CapturePolicy())

    compacted = [e for e in sink.events if e[0] == "native.session.compacted"]
    assert len(compacted) == 1
    payload = compacted[0][1]
    assert payload is not None
    assert payload["compaction_count"] == 1
