"""F6b stopped arguments, interrupted tool settlement and private summaries."""

from __future__ import annotations

import io
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider
from test_native_request_budget_admission import _settings
from test_product_session_api import Sink

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.events import RunCancelled, TurnCompleted
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import (
    ProviderPartial,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.sdk import create_product_session, open_product_session


class PartialProvider:
    name = "partial-fixture"
    model_id = "fixture"
    supports_tool_calls = True

    def __init__(self, arguments: str, failure: bool) -> None:
        self.arguments, self.failure = arguments, failure
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        first = len(self.requests) == 1
        blocks = (
            ThinkingContent("private thought"),
            TextContent("partial text"),
            ProviderToolCall(
                "partial-id", "read", self.arguments, thought_signature="sig"
            ),
        )
        if first and not self.failure:
            exc = ProviderCancelledError("fixture")
            exc.partial = ProviderPartial(content_blocks=blocks)
            raise exc
        return ProviderResult(
            status=HarnessStatus.FAILED if first else HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            content_blocks=blocks if first else (),
            final_text=None if first else "continued",
            error_message="fixture failure" if first else None,
            error_type="FixtureFailure" if first else None,
            metadata={"retryable": False},
        )


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"path":"a', {"path": "a"}),
        ('{"pa', {}),
        ('{"path":"a","n":', {"path": "a"}),
        ('{"x":[1,{"y":tru', {"x": [1, {"y": True}]}),
        ('{"path":"C:\\q"}', {"path": "C:\\q"}),
        ('{"path":"line\nnext"}', {"path": "line\nnext"}),
        ('{"path":"a\\', {"path": "a"}),
        ("", {}),
        ("wrong", {}),
        ("[1,", [1]),
        ('"abc', "abc"),
        ("123", 123),
        ("true", True),
        ("null", None),
        ("nu", {}),
        ('{"path":"a","n":1e999}', {}),
        ('{"x":NaN}', {}),
        ('{"x":1e999}', {}),
    ],
)
def test_stopped_arguments_are_normalized_and_reopen_without_execution(
    tmp_path: Path, arguments: str, expected: object, failure: bool
) -> None:
    provider = PartialProvider(arguments, failure)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    settings = _settings(tmp_path)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tree=tree,
        settings=settings,
        load_context_files=False,
    ) as session:
        session.submit("first")
        stopped = session.snapshot().messages[-1]
        assert isinstance(stopped, AgentAssistantMessage)
        (call,) = stopped.tool_calls
        assert json.loads(call.arguments_json.value) == expected
        assert call.provider_correlation_id == "partial-id"
        assert call.thought_signature == "sig"
        assert serialize_message(stopped)["content"][-1]["arguments"] == expected
        assert stopped.ordered_content()[:2] == (
            ThinkingContent("private thought"),
            TextContent("partial text"),
        )
        session.submit("next")
        assert not any(
            isinstance(m, AgentAssistantMessage) and m.stop_reason
            for m in provider.requests[-1].messages
        )
    assert tree.path is not None
    saved = tree.path.read_bytes()
    reopened = NativeSessionTree.open(tree.path, strict=True)
    assert any(
        isinstance(e, MessageEntry) and e.message == stopped
        for e in reopened.get_entries()
    )
    with open_product_session(
        session_path=tree.path,
        workspace=tmp_path,
        provider=provider,
        settings=settings,
        load_context_files=False,
    ) as resumed:
        resumed.submit("resumed")
    assert tree.path.read_bytes().startswith(saved)
    assert len(provider.requests) == 3


class BlockingTool:
    definition = ToolDefinition(
        "fixture",
        "Wait for cancellation",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.cancelled = False

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        self.entered.set()
        assert context.cancel_event is not None
        assert context.cancel_event.wait(5)
        self.cancelled = True
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            provider_correlation_id=request.provider_correlation_id,
            output_text="interrupted",
            is_error=True,
        )


def test_interrupted_tools_append_empty_aborted_assistant_and_reopen(
    tmp_path: Path,
) -> None:
    provider = _RecordingToolProvider(
        call_script=(
            (
                ProviderToolCall("one", "fixture", "{}"),
                ProviderToolCall("two", "fixture", "{}"),
            ),
        )
    )
    tool, sink = BlockingTool(), Sink()
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={"fixture": tool},
        tree=tree,
        observer=sink,
        load_context_files=False,
    ) as session:

        def cancel() -> None:
            assert tool.entered.wait(5)
            session.cancel()

        thread = threading.Thread(target=cancel)
        thread.start()
        try:
            result = session.submit("work")
        finally:
            thread.join(6)
        assert not thread.is_alive()
        assert tool.cancelled
        stopped = result.messages[-1]
        assert isinstance(stopped, AgentAssistantMessage)
        assert stopped.stop_reason is AgentStopReason.ABORTED
        assert stopped.usage == AgentMessageUsage()
        assert (stopped.provider, stopped.model) == (provider.name, provider.model_id)
        assert stopped.content.value == "" and stopped.tool_calls == ()
        results = [m for m in result.messages if isinstance(m, AgentToolResultMessage)]
        assert [m.provider_correlation_id for m in results] == ["one", "two"]
        assert len(provider.requests) == 1
        assert len([e for e in sink.events if isinstance(e, RunCancelled)]) == 1
        assert [e.turn_index for e in sink.events if isinstance(e, TurnCompleted)] == [
            0,
            1,
        ]
        session.submit("next")
        replay = provider.requests[-1].messages
        assert stopped not in replay
        assert [
            m.provider_correlation_id
            for m in replay
            if isinstance(m, AgentToolResultMessage)
        ] == ["one", "two"]
    assert tree.path is not None
    saved = tree.path.read_bytes()
    reopened = NativeSessionTree.open(tree.path, strict=True)
    assert any(
        isinstance(e, MessageEntry) and e.message == stopped
        for e in reopened.get_entries()
    )
    with open_product_session(
        session_path=tree.path,
        workspace=tmp_path,
        provider=provider,
        tools={"fixture": tool},
        load_context_files=False,
    ) as resumed:
        resumed.submit("resumed")
    assert tree.path.read_bytes().startswith(saved)


@pytest.mark.parametrize("reason", [AgentStopReason.ABORTED, AgentStopReason.ERROR])
def test_manual_compaction_includes_stopped_text_as_private_data(
    tmp_path: Path, reason: AgentStopReason
) -> None:
    provider = _RecordingToolProvider()
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    for i in range(4):
        tree.append_message(AgentUserMessage(ProductContent(f"task {i}")))
        tree.append_message(
            AgentAssistantMessage.from_blocks(
                (
                    TextContent("unfinished evidence" if i == 0 else f"answer {i}"),
                    ThinkingContent("private thought"),
                ),
                stop_reason=reason if i == 0 else None,
                error_message="fixture"
                if i == 0 and reason is AgentStopReason.ERROR
                else None,
            )
        )
    assert tree.path is not None
    saved = tree.path.read_bytes()
    CodingSession(
        provider=provider, native_session=tree, settings_manager=_settings(tmp_path)
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/compact\nnext\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    summary = provider.requests[0]
    assert summary.system_prompt.startswith("Summarize conversation context")
    rows = [m.content.value for m in summary.messages]
    assert any(
        "[Assistant]" in text and "unfinished evidence" in text and reason.value in text
        for text in rows
    )
    assert "private thought" not in "\n".join(rows)
    assert all(
        not isinstance(m, AgentAssistantMessage) or m.stop_reason is None
        for m in summary.messages
    )
    assert summary.available_tools == () and summary.attachments == ()
    assert tree.path.read_bytes().startswith(saved)
    reopened = NativeSessionTree.open(tree.path, strict=True)
    assert any(
        "unfinished evidence" in m.content.value
        for m in reopened.build_coding_context().messages
    ) or "unfinished evidence" in (reopened.build_coding_context().prior_summary or "")


def test_branch_summary_includes_stopped_text_as_private_data(tmp_path: Path) -> None:
    provider = PartialProvider('{"path":"a', True)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    CodingSession(
        provider=provider, native_session=tree, settings_manager=_settings(tmp_path)
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("ROOT\nMAIN\n/tree select 1 summarize\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert len(provider.requests) == 3
    summary = provider.requests[-1]
    text = "\n".join(m.content.value for m in summary.messages)
    assert (
        "[Assistant]" in text and "partial text" in text and "stop_reason=error" in text
    )
    assert "private thought" not in text
    assert not any(
        isinstance(m, AgentAssistantMessage) and m.stop_reason for m in summary.messages
    )
    assert summary.available_tools == summary.attachments == ()
    assert tree.path is not None
    from pipy_harness.native.session_tree import BranchSummaryEntry

    assert any(
        isinstance(e, BranchSummaryEntry)
        for e in NativeSessionTree.open(tree.path, strict=True).get_entries()
    )


def test_local_command_tool_interruption_records_terminal_assistant() -> None:
    from test_native_agent_loop import (
        _make_loop,
        _provider_call,
        _provider_result,
        _run_input,
        _tool_result,
        _Tools,
    )

    from pipy_harness.native.agent import AgentCancellationReason, AgentToolCall
    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
    from pipy_harness.native.agent.tools import (
        ToolExecutionInterruption,
        ToolExecutionOutcome,
    )

    call = AgentToolCall("one", "fixture", ProductContent("{}"))
    tools = _Tools(
        [],
        [
            ToolExecutionOutcome(
                _tool_result(call), interruption=ToolExecutionInterruption.LOCAL_COMMAND
            )
        ],
    )
    loop, provider, events, _ = _make_loop(
        [],
        [ProviderTurnOutcome(result=_provider_result(calls=(_provider_call("one"),)))],
        tools=tools,
    )
    outcome = loop.run(_run_input())
    stopped = outcome.final_history[-1]
    assert (
        isinstance(stopped, AgentAssistantMessage)
        and stopped.stop_reason is AgentStopReason.ABORTED
    )
    assert stopped.usage == AgentMessageUsage()
    assert stopped.content.value == "" and not stopped.tool_calls
    assert outcome.result.cancellation_reason is AgentCancellationReason.LOCAL_COMMAND
    assert provider.calls == 1
    assert [e.turn_index for e in events.events if isinstance(e, TurnCompleted)] == [
        0,
        1,
    ]


@pytest.mark.parametrize("length", [24000, AgentAssistantMessage.CONTENT_MAX_LENGTH])
def test_stopped_text_under_automatic_summary_pressure_remains_budgeted(
    tmp_path: Path,
    length: int,
) -> None:
    from test_native_compaction_oversized_turn import _summary_requests
    from test_native_compaction_summary_recovery import _pressure_tree
    from test_native_request_budget_admission import _measure, _size

    from pipy_harness.native.coding.summary_recovery import RECOVERY_WARNING

    tree = _pressure_tree(tmp_path, "paste")
    tree.append_message(
        AgentAssistantMessage.from_blocks(
            (
                TextContent(
                    "UNFINISHED_SNAPSHOT "
                    + "Z" * (length - len("UNFINISHED_SNAPSHOT "))
                ),
                ThinkingContent("private thought"),
            ),
            stop_reason=AgentStopReason.ABORTED,
        )
    )
    ceiling = _size(_measure(tmp_path)) + 5000
    settings = _settings(tmp_path, contextWindow=ceiling, reserveTokens=300)
    provider = _RecordingToolProvider()
    assert tree.path is not None
    saved = tree.path.read_bytes()
    with create_product_session(
        workspace=tmp_path,
        tree=tree,
        provider=provider,
        settings=settings,
        load_context_files=False,
    ) as session:
        assert session.submit("continue").preparation_failure is None
        summaries = _summary_requests(provider)
        assert len(summaries) == 1
        request = summaries[0]
        assert _size(request, 300) <= ceiling
        text = "\n".join(m.content.value for m in request.messages)
        assert "UNFINISHED_SNAPSHOT" in text and "stop_reason=aborted" in text
        assert "private thought" not in text
        assert RECOVERY_WARNING in text
        assert request.available_tools == request.attachments == ()
        assert session.submit("again").preparation_failure is None
        assert len(_summary_requests(provider)) == 1
    assert tree.path.read_bytes().startswith(saved)
    context = NativeSessionTree.open(tree.path, strict=True).build_coding_context()
    assert RECOVERY_WARNING in (context.prior_summary or "")


@pytest.mark.parametrize("reason", [AgentStopReason.ABORTED, AgentStopReason.ERROR])
@pytest.mark.parametrize(
    "length",
    [
        AgentAssistantMessage.CONTENT_MAX_LENGTH - 200,
        AgentAssistantMessage.CONTENT_MAX_LENGTH - 80,
        AgentAssistantMessage.CONTENT_MAX_LENGTH,
    ],
)
def test_private_summary_preserves_stopped_text_at_canonical_cap(
    tmp_path: Path, reason: AgentStopReason, length: int
) -> None:
    from test_native_compaction_oversized_turn import _binding

    from pipy_harness.native.coding.compaction import build_summary_request

    text = "START " + "x" * (length - 10) + " END"
    stopped = AgentAssistantMessage.from_blocks(
        (TextContent(text), ThinkingContent("private thought")), stop_reason=reason
    )
    request = build_summary_request(
        binding=_binding(),
        cwd=tmp_path,
        messages=(
            AgentUserMessage(ProductContent("before")),
            stopped,
            AgentUserMessage(ProductContent("after")),
        ),
        instruction="Summarize",
        user_prompt="now",
        header_callback=None,
    )
    assert request.messages[0].content.value == "before"
    assert request.messages[-1].content.value == "after"
    excerpts = request.messages[1:-1]
    assert 1 <= len(excerpts) <= 2
    assert all(
        isinstance(m, AgentUserMessage)
        and len(m.content.value) <= AgentUserMessage.CONTENT_MAX_LENGTH
        for m in excerpts
    )
    assert "".join(m.content.value.split("\n", 2)[2] for m in excerpts) == text
    assert "private thought" not in "\n".join(m.content.value for m in request.messages)


def test_unexecuted_stopped_intent_keeps_truthful_attempted_file_metadata(
    tmp_path: Path,
) -> None:
    from test_native_compaction_oversized_turn import _binding

    from pipy_harness.native.agent import AgentToolCall
    from pipy_harness.native.agent.assistant_blocks import stopped_assistant
    from pipy_harness.native.coding.compaction import build_summary_request

    stopped = stopped_assistant(
        (AgentToolCall("partial", "edit", ProductContent('{"path":"src/ma')),),
        AgentStopReason.ABORTED,
        usage=AgentMessageUsage(),
        request=None,
    )
    request = build_summary_request(
        binding=_binding(),
        cwd=tmp_path,
        messages=(stopped,),
        instruction="summary",
        user_prompt="now",
        header_callback=None,
    )
    text = request.messages[0].content.value
    assert "never executed" in text and "stopped paths may be partial" in text
    assert '<modified-files>\n"src/ma"\n</modified-files>' in text
    assert all(isinstance(m, AgentUserMessage) for m in request.messages)
    assert all(not getattr(m, "tool_calls", ()) for m in request.messages)


def test_preexisting_raw_stopped_arguments_are_not_rewritten_on_resume(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.agent import AgentToolCall

    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    raw = '{"path":"legacy'
    stopped = AgentAssistantMessage.from_blocks(
        (AgentToolCall("legacy", "read", ProductContent(raw)),),
        stop_reason=AgentStopReason.ABORTED,
    )
    tree.append_message(AgentUserMessage(ProductContent("old")))
    tree.append_message(stopped)
    assert tree.path is not None
    saved = tree.path.read_bytes()
    provider = _RecordingToolProvider()
    with open_product_session(
        session_path=tree.path,
        workspace=tmp_path,
        provider=provider,
        load_context_files=False,
    ) as session:
        session.submit("next")
    reopened = NativeSessionTree.open(tree.path, strict=True)
    assert tree.path.read_bytes().startswith(saved)
    old = next(
        e.message
        for e in reopened.get_entries()
        if isinstance(e, MessageEntry)
        and isinstance(e.message, AgentAssistantMessage)
        and e.message.stop_reason
    )
    assert old.tool_calls[0].arguments_json.value == raw
    assert serialize_message(old)["content"][0]["arguments"] == {"_raw": raw}
    assert old not in provider.requests[0].messages
