"""Recoverable preparation refusal through real product and transport owners."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_automation_rpc import _RpcClient
from test_native_coding_session_resume_compact import _RecordingToolProvider

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.agent import (
    AgentEvent,
    AgentFailure,
    AgentMessage,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentToolResultMessage,
    AgentUserMessage,
    MessageCompleted,
    ProductContent,
    ProviderFailed,
    RunCancelled,
    TurnStarted,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop import AgentLoopRequestPreparation
from pipy_harness.native.automation.run_modes import run_json_mode, run_print_mode
from pipy_harness.native.coding.state import (
    CodingContextChangedError,
    CodingSessionState,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.repl import loop_step
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools import ToolDefinition
from pipy_harness.native.ui.state import UiState, reduce
from pipy_harness.runner import HarnessRunner
from pipy_harness.sdk import create_product_session

_PRIVATE_TYPE = "PRIVATE_PREPARATION_TYPE"
_PRIVATE_TEXT = "PRIVATE_PREPARATION_TEXT"
_FAILURE = AgentFailure(_PRIVATE_TYPE, ProductContent(_PRIVATE_TEXT))


class _Sink:
    def __init__(self, callback: Callable[[AgentEvent], None] | None = None) -> None:
        self.events: list[AgentEvent] = []
        self.callback = callback

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.callback is not None:
            self.callback(event)


def _inject_refusal(
    monkeypatch: pytest.MonkeyPatch, *, iteration: int = 0, prompt: str = "refuse"
) -> list[CodingSessionState]:
    original = loop_step._RequestPreparationEffects.prepare
    owners: list[CodingSessionState] = []

    def prepare(
        self: loop_step._RequestPreparationEffects,
        history: tuple[AgentMessage, ...],
        active: AgentActiveInput,
        index: int,
        tools: tuple[ToolDefinition, ...],
    ) -> AgentLoopRequestPreparation:
        if index == iteration and active.accepted_message.content.value == prompt:
            state = self.accepted.turn_input.turn.scope.coding_state
            owners.append(state)
            state.mirror_history(history)
            context = state.capture_run_context()
            return AgentLoopRequestPreparation(
                context.messages, preparation_failure=_FAILURE
            )
        return original(self, history, active, index, tools)

    monkeypatch.setattr(loop_step._RequestPreparationEffects, "prepare", prepare)
    return owners


class _PriorToolProvider(_RecordingToolProvider):
    def __init__(self) -> None:
        super().__init__(
            call_script=(
                (
                    ProviderToolCall(
                        "write", "write", '{"path":"effect.txt","content":"kept"}'
                    ),
                ),
            )
        )

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        result = super().complete(request, **kwargs)
        return replace(
            result,
            final_text="interim tool answer" if result.tool_calls else "answer",
            usage={"input_tokens": 7},
        )


@pytest.mark.parametrize("iteration", [0, 1])
def test_sdk_refusal_retains_history_usage_and_lifetime_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    owners = _inject_refusal(monkeypatch, iteration=iteration)
    provider = _PriorToolProvider() if iteration else _RecordingToolProvider()
    events = _Sink()
    diagnostics: list[str] = []
    probes: list[bool] = []

    def diagnostic(fragment: str) -> None:
        diagnostics.append(fragment)
        if "request preparation refused" not in fragment:
            return
        # A foreign reader must enter while this synchronous callback is active:
        # publication cannot retain the state mutex across diagnostics.
        read = threading.Event()

        def probe() -> None:
            assert owners[-1].preparation_failure is _FAILURE
            read.set()

        worker = threading.Thread(target=probe)
        worker.start()
        try:
            assert read.wait(2)
        finally:
            worker.join(5)
            assert not worker.is_alive()
        probes.append(True)

    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        observer=events,
        diagnostic_sink=diagnostic,
    ) as session:
        refused = session.submit("refuse")
        assert refused.preparation_failure is _FAILURE
        assert refused.provider_failure is None and refused.user_turn_count == 1
        assert refused.tool_invocation_count == iteration
        assert refused.usage.input_tokens == iteration * 7
        assert len(provider.requests) == iteration
        assert sum(isinstance(m, AgentUserMessage) for m in refused.messages) == 1
        assert (
            sum(isinstance(m, AgentToolResultMessage) for m in refused.messages)
            == iteration
        )
        if iteration:
            assert (tmp_path / "effect.txt").read_text() == "kept"
        assert isinstance(events.events[-1], AgentRunCompleted)
        assert events.events[-1].result.outcome is AgentRunOutcome.FAILED
        assert events.events[-1].result.failure is _FAILURE
        assert not any(
            isinstance(e, (ProviderFailed, RunCancelled)) for e in events.events
        )
        # The unchanged UI reducer produces no assistant block for this request.
        ui = UiState()
        after_refusal_start = False
        refused_decisions: list[object] = []
        for event in events.events:
            if isinstance(event, TurnStarted) and event.turn_index == iteration:
                after_refusal_start = True
            ui, decisions = reduce(ui, event)
            if after_refusal_start:
                refused_decisions.extend(decisions)
        assert not refused_decisions and not ui.assistant_active
        recovered = session.submit("next")
        assert recovered.preparation_failure is None
        assert recovered.messages[-1].content.value == "answer"
        assert recovered.messages[: len(refused.messages)] == refused.messages
        assert recovered.user_turn_count == 2
        assert refused.preparation_failure is _FAILURE
    assert probes == [True]
    assert "request preparation refused" in "".join(diagnostics)


def test_refusal_publication_cannot_hide_context_change_in_turn_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owners = _inject_refusal(monkeypatch)

    def mutate(event: AgentEvent) -> None:
        if isinstance(event, TurnStarted):
            owners[-1].rebuild_history(
                (AgentUserMessage(ProductContent("replacement")),)
            )

    diagnostics: list[str] = []
    provider = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        observer=_Sink(mutate),
        diagnostic_sink=diagnostics.append,
    ) as session:
        with pytest.raises(CodingContextChangedError):
            session.submit("refuse")
        assert session.snapshot().preparation_failure is None
        assert not provider.requests
        assert "request preparation refused" not in "".join(diagnostics)
        with pytest.raises(RuntimeError, match="closed"):
            session.submit("next")


@pytest.mark.parametrize("iteration", [0, 1])
def test_json_refusal_keeps_envelopes_without_assistant_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    _inject_refusal(monkeypatch, iteration=iteration)
    provider = _PriorToolProvider() if iteration else _RecordingToolProvider()
    output = io.BytesIO()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    exit_code = run_json_mode(
        adapter=CodingSessionAdapter(provider=provider),
        prompt="refuse",
        cwd=tmp_path,
        native_session=tree,
        stdout_buffer=output,
        error_stream=io.StringIO(),
    )
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert exit_code == 0  # Existing aggregate lifetime exit semantics.
    assert records[0]["type"] == "session"
    start = max(i for i, r in enumerate(records) if r["type"] == "turn_start")
    final = records[start:]
    assert [r["type"] for r in final] == (
        [
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
            "agent_settled",
        ]
        if iteration == 0
        else ["turn_start", "turn_end", "agent_end", "agent_settled"]
    )
    assert not any(
        r["type"] in ("message_start", "message_end")
        and r["message"]["role"] == "assistant"
        for r in final
    )
    turn_end = next(r for r in final if r["type"] == "turn_end")
    assert turn_end["message"]["role"] == "assistant"
    assert turn_end["message"]["content"] == []
    assert set(turn_end) == {"type", "message", "toolResults"}
    assert set(final[-2]) == {"type", "messages", "willRetry"}
    assert sum(m["role"] == "user" for m in final[-2]["messages"]) == 1


def test_print_refusal_suppresses_prior_assistant_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_refusal(monkeypatch, iteration=1)
    output, errors = io.StringIO(), io.StringIO()
    code = run_print_mode(
        adapter=CodingSessionAdapter(provider=_PriorToolProvider()),
        prompt="refuse",
        cwd=tmp_path,
        stdout=output,
        error_stream=errors,
    )
    assert code == 1 and output.getvalue() == ""
    assert (tmp_path / "effect.txt").read_text() == "kept"
    assert "request_preparation_refused" in errors.getvalue()


def _continuation_resources(tmp_path: Path, content: str) -> RuntimeResourceOptions:
    extension = tmp_path / "continuation.py"
    extension.write_text(f"""
def activate(api):
    pending = True
    @api.on("agent_settled")
    def continue_once(event, ctx):
        nonlocal pending
        if pending:
            pending = False
            api.send_user_message({content!r})
""")
    return RuntimeResourceOptions(extension_paths=(extension,))


def test_print_current_refusal_takes_precedence_over_retained_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProvider(_RecordingToolProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            return replace(
                super().complete(request, **kwargs),
                status=HarnessStatus.FAILED,
                error_type="OlderProviderFailure",
                error_message="older reason",
            )

    provider = FailedProvider()
    owners = _inject_refusal(monkeypatch)
    errors = io.StringIO()
    code = run_print_mode(
        adapter=CodingSessionAdapter(
            provider=provider,
            resource_options=_continuation_resources(tmp_path, "refuse"),
        ),
        prompt="fail provider",
        cwd=tmp_path,
        stdout=io.StringIO(),
        error_stream=errors,
    )
    assert code == 1 and len(provider.requests) == 1
    assert owners[-1].provider_failure is not None
    assert owners[-1].preparation_failure is _FAILURE
    assert (
        errors.getvalue().splitlines()[-1].strip()
        == "pipy: run failed with request_preparation_refused"
    )


def test_refusal_classification_survives_real_archive_without_private_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_refusal(monkeypatch)
    sink = _Sink()
    adapter = CodingSessionAdapter(
        provider=_RecordingToolProvider(),
        input_stream=io.StringIO("refuse\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        agent_event_sink=sink,
    )
    request = RunRequest(
        agent="pipy-native",
        slug="refusal-privacy",
        command=[],
        cwd=tmp_path,
        root=tmp_path / "archive",
        goal="bounded refusal fixture",
    )
    result = HarnessRunner(adapter=adapter).run(request)
    assert result.metadata is not None
    assert result.metadata["preparation_failure_type"] == "request_preparation_refused"
    assert result.record.markdown_path is not None
    persisted = (
        repr(result),
        result.record.jsonl_path.read_text(),
        result.record.markdown_path.read_text(),
    )
    if any(
        marker in text
        for marker in (_PRIVATE_TYPE, _PRIVATE_TEXT)
        for text in persisted
    ):
        raise AssertionError(
            "private refusal content reached bounded archive projection"
        )
    assert any(
        isinstance(e, AgentRunCompleted) and e.result.failure is _FAILURE
        for e in sink.events
    )


def test_rpc_refusal_settles_then_next_prompt_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_refusal(monkeypatch)
    provider = _RecordingToolProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        client.send({"id": "one", "type": "prompt", "message": "refuse"})
        records = client.collect_until(lambda r: r.get("type") == "agent_settled")
        assert not provider.requests
        assert not any(
            r.get("type") in ("message_start", "message_end")
            and r.get("message", {}).get("role") == "assistant"
            for r in records
        )
        assert sum(r.get("type") == "agent_end" for r in records) == 1
        assert sum(r.get("type") == "agent_settled" for r in records) == 1
        client.send({"id": "two", "type": "prompt", "message": "next"})
        records = client.collect_until(lambda r: r.get("type") == "agent_settled")
        assert len(provider.requests) == 1
        assert [m.content.value for m in provider.requests[0].messages] == [
            "refuse",
            "next",
        ]
        assert any(
            r.get("type") == "message_end"
            and r.get("message", {}).get("role") == "assistant"
            for r in records
        )
    finally:
        client.close()


def test_settled_continuation_after_refusal_clears_failure_before_accepted_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owners = _inject_refusal(monkeypatch)
    failures_at_acceptance: list[AgentFailure | None] = []

    def observe(event: AgentEvent) -> None:
        if isinstance(event, MessageCompleted) and isinstance(
            event.message, AgentUserMessage
        ):
            failures_at_acceptance.append(owners[-1].preparation_failure)

    sink = _Sink(observe)
    provider = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        resources=_continuation_resources(tmp_path, "next"),
        observer=sink,
    ) as session:
        result = session.submit("refuse")
        assert result.preparation_failure is None
        assert result.user_turn_count == 2
        assert result.messages[-1].content.value == "answer"
        assert [m.content.value for m in provider.requests[0].messages] == [
            "refuse",
            "next",
        ]
    assert failures_at_acceptance == [None, None]
    completed = [e for e in sink.events if isinstance(e, AgentRunCompleted)]
    assert [e.result.outcome for e in completed] == [
        AgentRunOutcome.FAILED,
        AgentRunOutcome.SUCCEEDED,
    ]
