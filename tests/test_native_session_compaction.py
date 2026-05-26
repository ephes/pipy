"""Session compaction contract tests (E2 parity).

The Streaming Output Parity Track closed C14; this module pins the
follow-up E2 row ("Session compaction"). `compact_loop_messages` is
a one-shot summarization pass over an oversized `LoopMessage` tuple
that returns a shorter tuple plus archive-safe metadata. It is
intentionally idempotent for already-small inputs and never leaks
raw turn text into the metadata dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.session_compaction import (
    COMPACTION_PROMPT_LABEL,
    COMPACTION_SYSTEM_PROMPT,
    CompactionError,
    CompactionResult,
    compact_loop_messages,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    UserMessage,
)


def _user_turn(index: int) -> UserMessage:
    return UserMessage(content=f"user message {index}")


def _assistant_turn(index: int) -> AssistantMessage:
    return AssistantMessage(content=f"assistant message {index}")


def _conversation(turn_count: int) -> tuple[LoopMessage, ...]:
    messages: list[LoopMessage] = []
    for i in range(turn_count):
        messages.append(_user_turn(i))
        messages.append(_assistant_turn(i))
    return tuple(messages)


def test_compact_loop_messages_is_identity_when_input_under_threshold(tmp_path: Path) -> None:
    provider = FakeNativeProvider(final_text="should not be called")
    messages = _conversation(2)

    result = compact_loop_messages(
        messages,
        provider=provider,
        keep_tail=2,
        threshold=8,
        workspace_root=tmp_path,
    )

    assert result.skipped is True
    assert result.messages == messages
    assert result.original_message_count == len(messages)
    assert result.summarized_message_count == 0
    assert result.kept_tail_count == len(messages)


def test_compact_loop_messages_replaces_old_turns_with_provider_summary(tmp_path: Path) -> None:
    provider = FakeNativeProvider(final_text="SAFE_COMPACTION_SUMMARY")
    messages = _conversation(8)  # 16 messages

    result = compact_loop_messages(
        messages,
        provider=provider,
        keep_tail=4,
        threshold=8,
        workspace_root=tmp_path,
    )

    assert result.skipped is False
    assert len(result.messages) == 5  # one summary + 4 tail
    head = result.messages[0]
    assert isinstance(head, AssistantMessage)
    assert head.content == "SAFE_COMPACTION_SUMMARY"
    assert result.messages[1:] == messages[-4:]
    assert result.original_message_count == 16
    assert result.summarized_message_count == 12
    assert result.kept_tail_count == 4


def test_compact_loop_messages_archive_metadata_excludes_summary_text(tmp_path: Path) -> None:
    provider = FakeNativeProvider(final_text="SAFE_COMPACTION_SUMMARY")
    messages = _conversation(8)

    result = compact_loop_messages(
        messages,
        provider=provider,
        keep_tail=4,
        threshold=8,
        workspace_root=tmp_path,
    )

    metadata = result.archive_metadata()

    assert metadata == {
        "original_message_count": 16,
        "summarized_message_count": 12,
        "kept_tail_count": 4,
        "skipped": False,
    }
    assert "SAFE_COMPACTION_SUMMARY" not in str(metadata)


def test_compact_loop_messages_raises_when_provider_returns_no_text(tmp_path: Path) -> None:
    provider = FakeNativeProvider(status=HarnessStatus.FAILED)
    messages = _conversation(8)

    with pytest.raises(CompactionError):
        compact_loop_messages(
            messages,
            provider=provider,
            keep_tail=2,
            threshold=8,
            workspace_root=tmp_path,
        )


def test_compact_loop_messages_passes_compaction_label_to_provider(tmp_path: Path) -> None:
    captured: list[str] = []

    class CapturingProvider:
        name = "fake-capture"
        model_id = "fake-native-bootstrap"
        supports_tool_calls = False

        def complete(self, request, **_kwargs):
            captured.append(request.provider_turn_label)
            captured.append(request.system_prompt)
            now_text = "compaction summary"
            from datetime import UTC, datetime

            from pipy_harness.native.models import ProviderResult

            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                final_text=now_text,
                usage={},
                metadata=None,
            )

    provider = CapturingProvider()
    messages = _conversation(8)

    result = compact_loop_messages(
        messages,
        provider=provider,
        keep_tail=2,
        threshold=8,
        workspace_root=tmp_path,
    )

    assert captured[0] == COMPACTION_PROMPT_LABEL
    assert captured[1] == COMPACTION_SYSTEM_PROMPT
    head = result.messages[0]
    assert isinstance(head, AssistantMessage)
    assert head.content == "compaction summary"


def test_compact_loop_messages_validates_keep_tail_bounds(tmp_path: Path) -> None:
    provider = FakeNativeProvider()
    messages = _conversation(8)

    with pytest.raises(ValueError):
        compact_loop_messages(
            messages,
            provider=provider,
            keep_tail=0,
            threshold=4,
            workspace_root=tmp_path,
        )
    with pytest.raises(ValueError):
        compact_loop_messages(
            messages,
            provider=provider,
            keep_tail=33,
            threshold=40,
            workspace_root=tmp_path,
        )


def test_compact_loop_messages_requires_threshold_greater_than_keep_tail(tmp_path: Path) -> None:
    provider = FakeNativeProvider()
    messages = _conversation(8)

    with pytest.raises(ValueError):
        compact_loop_messages(
            messages,
            provider=provider,
            keep_tail=4,
            threshold=4,
            workspace_root=tmp_path,
        )


def test_compaction_result_archive_metadata_keys_are_stable() -> None:
    result = CompactionResult(
        messages=(),
        original_message_count=0,
        summarized_message_count=0,
        kept_tail_count=0,
        skipped=True,
    )

    assert set(result.archive_metadata().keys()) == {
        "original_message_count",
        "summarized_message_count",
        "kept_tail_count",
        "skipped",
    }
