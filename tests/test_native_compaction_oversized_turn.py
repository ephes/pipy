"""DF1-F5: one oversized tool result must not wedge the session.

Pi cuts every tool result to 2000 characters in the summary input
(``serializeConversation``/``truncateForSummary``), so the next prompt can
summarize an oversized turn away instead of being refused forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider
from test_native_request_budget_admission import _measure, _settings, _size

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.coding.compaction import (
    SUMMARY_TOOL_RESULT_MAX_CHARS,
    build_summary_request,
    compaction_request,
    truncate_for_summary,
)
from pipy_harness.native.coding.state import CodingProviderBinding
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.session_tree import CompactionEntry, NativeSessionTree
from pipy_harness.sdk import create_product_session

_BIG_FILE = ("x" * 99 + "\n") * 240  # 24 000 characters
_SUMMARY_PREFIX = "Summarize conversation context"


def _binding() -> CodingProviderBinding:
    return CodingProviderBinding(
        provider=_RecordingToolProvider(),
        provider_name="fake",
        model_id="fake-native-bootstrap",
    )


def _result(content: str, *, is_error: bool = False) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="read",
        content=ProductContent(content),
        provider_correlation_id="call-1",
        is_error=is_error,
    )


def _summary_requests(provider: _RecordingToolProvider) -> list[ProviderRequest]:
    return [
        request
        for request in provider.requests
        if request.system_prompt.startswith(_SUMMARY_PREFIX)
    ]


def _tool_results(request: ProviderRequest) -> list[AgentToolResultMessage]:
    return [
        message
        for message in request.messages
        if isinstance(message, AgentToolResultMessage)
    ]


@pytest.mark.parametrize(
    "length,truncated",
    [
        (SUMMARY_TOOL_RESULT_MAX_CHARS - 1, False),
        (SUMMARY_TOOL_RESULT_MAX_CHARS, False),
        (SUMMARY_TOOL_RESULT_MAX_CHARS + 1, True),
        (24_000, True),
    ],
)
def test_truncate_for_summary_matches_pi_marker(length, truncated):
    text = "".join(str(i % 10) for i in range(length))
    out = truncate_for_summary(text, SUMMARY_TOOL_RESULT_MAX_CHARS)
    if not truncated:
        assert out == text
        return
    assert out == (
        text[:SUMMARY_TOOL_RESULT_MAX_CHARS]
        + f"\n\n[... {length - SUMMARY_TOOL_RESULT_MAX_CHARS} more characters truncated]"
    )


def test_summary_request_truncates_only_tool_results(tmp_path: Path):
    long_user = "u" * 5000
    long_answer = "a" * 5000
    small = _result("short output")
    big = _result("y" * 3000, is_error=True)
    call = AgentToolCall(
        tool_name="read",
        arguments_json=ProductContent('{"path": "' + "p" * 3000 + '"}'),
        provider_correlation_id="call-1",
    )
    messages = (
        AgentUserMessage(ProductContent(long_user)),
        AgentAssistantMessage(ProductContent(long_answer), tool_calls=(call,)),
        big,
        small,
    )
    for request, sent_count in (
        (
            build_summary_request(
                binding=_binding(),
                cwd=tmp_path,
                messages=messages,
                instruction="Summarize the following abandoned conversation branch.",
                user_prompt="Provide the branch summary now.",
                header_callback=None,
            ),
            4,
        ),
        (
            # The compaction request appends its final instruction message.
            compaction_request(
                binding=_binding(),
                cwd=tmp_path,
                dropped_messages=messages,
                prior_summary="",
                header_callback=None,
            ),
            5,
        ),
    ):
        assert len(request.messages) == sent_count
        sent = request.messages[:4]
        assert sent[0] is messages[0] and sent[1] is messages[1]
        assert sent[3] is small
        truncated = sent[2]
        assert isinstance(truncated, AgentToolResultMessage)
        assert truncated.content.value == (
            "y" * 2000 + "\n\n[... 1000 more characters truncated]"
        )
        assert (
            truncated.tool_request_id,
            truncated.tool_name,
            truncated.provider_correlation_id,
            truncated.is_error,
        ) == ("pipy-tool-1", "read", "call-1", True)
    assert big.content.value == "y" * 3000


def _oversized_session(tmp_path: Path, extra: int = 5000):
    (tmp_path / "big.txt").write_text(_BIG_FILE)
    base = _size(_measure(tmp_path))
    settings = _settings(tmp_path, contextWindow=base + extra)
    provider = _RecordingToolProvider(
        call_script=(
            (),
            (),
            (ProviderToolCall("r1", "read", '{"path":"big.txt"}'),),
        )
    )
    return settings, provider


@pytest.mark.parametrize("persist", [False, True])
def test_oversized_tool_result_turn_recovers_on_next_prompt(
    tmp_path: Path, persist: bool
):
    settings, provider = _oversized_session(tmp_path)
    tree = NativeSessionTree.create(
        tmp_path, session_dir=tmp_path / "sessions", persist=persist
    )
    diagnostics: list[str] = []
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        settings=settings,
        tree=tree,
        load_context_files=False,
        diagnostic_sink=diagnostics.append,
    ) as session:
        for prompt in ("one", "two"):
            assert session.submit(prompt).preparation_failure is None
        # The read result alone is over the window: that turn is refused
        # (Pi also fails it after one compact-and-retry attempt).
        refused = session.submit("cat big")
        assert refused.preparation_failure is not None
        assert refused.preparation_failure.error_type == "request_budget"
        assert _tool_results(provider.requests[-1]) == []
        # Before the fix, every later prompt was refused here because the
        # summary request carried the full 24 KB result.
        before = len(provider.requests)
        recovered = session.submit("next")
        assert recovered.preparation_failure is None
        assert recovered.provider_failure is None
        last = recovered.messages[-1]
        assert isinstance(last, AgentAssistantMessage)
        assert last.content.value == "answer"
        summaries = _summary_requests(provider)
        assert summaries
        (summarized,) = _tool_results(summaries[-1])
        assert summarized.content.value.endswith("more characters truncated]")
        assert len(summarized.content.value) < 2100
        ordinary = provider.requests[-1]
        assert not ordinary.system_prompt.startswith(_SUMMARY_PREFIX)
        assert _tool_results(ordinary) == []
        assert len(provider.requests) == before + 2
        again = session.submit("again")
        assert again.preparation_failure is None
    joined = "\n".join(diagnostics)
    assert "estimated summary request exceeds" not in joined
    # A persistent tree used to refuse here: the cut keeps only the new
    # prompt, which has no entry yet (Pi: firstKeptEntryId ?? own id).
    assert "no durable origin" not in joined
    if not persist:
        return
    compactions = [e for e in tree.get_entries() if isinstance(e, CompactionEntry)]
    assert compactions[-1].first_kept_entry_id == compactions[-1].id
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path).build_coding_context()
    assert reopened.messages == again.messages
    forked = NativeSessionTree.fork_from(
        tree.path, tmp_path, session_dir=tmp_path / "forks"
    ).build_coding_context()
    assert forked.messages == again.messages


def test_oversized_user_message_is_still_refused_like_pi(tmp_path: Path):
    """Refuse the paste honestly, then recover older history for fitting input."""

    settings, provider = _oversized_session(tmp_path)
    diagnostics: list[str] = []
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        settings=settings,
        load_context_files=False,
        diagnostic_sink=diagnostics.append,
    ) as session:
        assert session.submit("one").preparation_failure is None
        refused = session.submit("paste: " + _BIG_FILE)
        assert refused.preparation_failure is not None
        before = len(provider.requests)
        still = session.submit("next")
        assert still.preparation_failure is None
        assert len(provider.requests) == before + 2
    assert "incomplete excerpts" in "\n".join(diagnostics)
