"""Product-session integration contracts for canonical agent runtime ports."""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest

from pipy_harness.status import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentEventSink,
    AgentMessage,
    AgentRunStarted,
    AgentToolResultMessage,
    AgentUsage,
    AgentUserMessage,
    FollowUpConsumed,
    MessageCompleted,
    ProductContent,
    ProviderFailed,
    RunCancelled,
    SteeringConsumed,
    ToolCallCompleted,
    TurnStarted,
    UsageUpdated,
)
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentRunEffect,
    AgentUsagePublication,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


@dataclass(slots=True)
class _CollectingSink:
    events: list[AgentEvent] = field(default_factory=list)
    trace: list[tuple[str, object]] | None = None

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.trace is not None:
            self.trace.append(("event", event))


@dataclass(frozen=True, slots=True)
class _EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Echo one string.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            request.tool_request_id,
            str(request.arguments["text"]),
            provider_correlation_id=request.provider_correlation_id,
        )


@dataclass(slots=True)
class _ScriptProvider:
    script: tuple[ProviderResult, ...]
    supports_tool_calls: bool = True
    name: str = "runtime-ports-fixture"
    model_id: str = "fixture-model"
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink, cancel_token
        self.requests.append(request)
        return self.script[len(self.requests) - 1]


@dataclass(slots=True)
class _CancelledProvider:
    supports_tool_calls: bool = True
    name: str = "runtime-ports-cancelled"
    model_id: str = "fixture-model"
    calls: int = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        self.calls += 1
        raise ProviderCancelledError("fixture cancellation")


def _result(
    text: str | None,
    *,
    usage: Mapping[str, int] | None = None,
    tool_calls: tuple[ProviderToolCall, ...] = (),
    status: HarnessStatus = HarnessStatus.SUCCEEDED,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=status,
        provider_name="runtime-ports-fixture",
        model_id="fixture-model",
        started_at=now,
        ended_at=now,
        final_text=text,
        usage=usage,
        tool_calls=tool_calls,
        error_type="FixtureFailure" if status is HarnessStatus.FAILED else None,
        error_message="fixture failed" if status is HarnessStatus.FAILED else None,
    )


def _run(
    session: NativeToolReplSession,
    tmp_path: Path,
    prompts: str,
) -> None:
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )


def _message_event_index(trace: list[tuple[str, object]], message: AgentMessage) -> int:
    for index, (kind, item) in enumerate(trace):
        if kind != "event":
            continue
        if isinstance(item, MessageCompleted) and item.message is message:
            return index
        if isinstance(item, ToolCallCompleted) and item.result is message:
            return index
    raise AssertionError("canonical completion event did not carry effect identity")


def _effect_index(trace: list[tuple[str, object]], message: AgentMessage) -> int:
    return next(
        index
        for index, (kind, item) in enumerate(trace)
        if kind == "effect" and item is message
    )


def _assert_usage_publications(
    publications: list[AgentUsagePublication],
) -> None:
    assert [publication.sample for publication in publications] == [
        AgentProviderUsageSample(input_tokens=2, output_tokens=1, total_tokens=3),
        AgentProviderUsageSample(input_tokens=3, output_tokens=1),
        AgentProviderUsageSample(input_tokens=7, output_tokens=2, total_tokens=9),
    ]
    assert [publication.cumulative_usage for publication in publications] == [
        AgentUsage(input_tokens=2, output_tokens=1),
        AgentUsage(input_tokens=5, output_tokens=2),
        AgentUsage(input_tokens=7, output_tokens=2),
    ]
    assert [publication.context_tokens for publication in publications] == [3, 4, 9]


def test_product_session_traverses_run_effect_port_with_exact_message_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    trace: list[tuple[str, object]] = []
    original = loop_module.NativeAgentRunEffectSink

    class RecordingRunEffectSink:
        def __init__(self, append_message: Callable[[AgentMessage], object]) -> None:
            self._delegate = original(append_message)

        def emit(self, effect: AgentRunEffect) -> None:
            trace.append(("effect", effect.message))
            self._delegate.emit(effect)

    monkeypatch.setattr(loop_module, "NativeAgentRunEffectSink", RecordingRunEffectSink)
    call = ProviderToolCall("provider-call", "echo", '{"text":"tool-result"}')
    provider = _ScriptProvider(
        (_result("using tool", tool_calls=(call,)), _result("done"))
    )
    canonical = _CollectingSink(trace=trace)
    tree = NativeSessionTree.create(tmp_path, persist=False)

    _run(
        NativeToolReplSession(
            provider=provider,
            tool_registry={"echo": _EchoTool()},
            native_session=tree,
            agent_event_sink=canonical,
        ),
        tmp_path,
        "prompt\n",
    )

    effects = [item for kind, item in trace if kind == "effect"]
    persisted = list(tree.build_context().messages)
    assert effects == persisted
    assert len({id(message) for message in effects}) == len(effects) == 4
    event_messages = [
        event.message
        for event in canonical.events
        if isinstance(event, MessageCompleted)
    ] + [
        event.result
        for event in canonical.events
        if isinstance(event, ToolCallCompleted)
    ]
    assert all(
        any(message is event_message for event_message in event_messages)
        for message in effects
    )

    user, assistant_with_call, tool_result, final_assistant = effects
    assert isinstance(user, AgentUserMessage)
    assert isinstance(assistant_with_call, AgentAssistantMessage)
    assert isinstance(tool_result, AgentToolResultMessage)
    assert isinstance(final_assistant, AgentAssistantMessage)
    assert _effect_index(trace, user) < next(
        index for index, (_, item) in enumerate(trace) if isinstance(item, TurnStarted)
    )
    for message in (assistant_with_call, tool_result, final_assistant):
        assert _message_event_index(trace, message) < _effect_index(trace, message)


@pytest.mark.parametrize("cancelled", [False, True])
def test_run_effect_port_excludes_synthetic_failure_and_cancel_assistant(
    cancelled: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    effects: list[object] = []
    original = loop_module.NativeAgentRunEffectSink

    class RecordingRunEffectSink:
        def __init__(self, append_message: Callable[[AgentMessage], object]) -> None:
            self._delegate = original(append_message)

        def emit(self, effect: AgentRunEffect) -> None:
            effects.append(effect.message)
            self._delegate.emit(effect)

    monkeypatch.setattr(loop_module, "NativeAgentRunEffectSink", RecordingRunEffectSink)
    provider = (
        _CancelledProvider()
        if cancelled
        else _ScriptProvider((_result(None, status=HarnessStatus.FAILED),))
    )
    canonical = _CollectingSink()
    _run(
        NativeToolReplSession(provider=provider, agent_event_sink=canonical),
        tmp_path,
        "prompt\n",
    )

    assert len(effects) == 1
    assert isinstance(effects[0], AgentUserMessage)
    assert any(
        isinstance(event, MessageCompleted)
        and isinstance(event.message, AgentAssistantMessage)
        and event.message.content == ProductContent("")
        for event in canonical.events
    )
    terminal_type = RunCancelled if cancelled else ProviderFailed
    assert any(isinstance(event, terminal_type) for event in canonical.events)


def test_product_session_usage_port_preserves_run_and_session_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    publications: list[AgentUsagePublication] = []
    trace: list[tuple[str, object]] = []
    footers: list[tuple[AgentUsage, int]] = []
    original = loop_module.NativeAgentUsagePublisher

    class RecordingUsagePublisher:
        def __init__(
            self,
            absorb_usage: Callable[[AgentProviderUsageSample], None],
            event_sink: AgentEventSink,
        ) -> None:
            self._delegate = original(absorb_usage, event_sink)

        def publish(self, publication: AgentUsagePublication) -> None:
            publications.append(publication)
            trace.append(("publish", publication))
            self._delegate.publish(publication)

    def record_footer(
        _self: NativeToolReplSession,
        _stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_accumulator: AgentUsageAccumulator | None = None,
    ) -> None:
        del cwd, provider_name, model_id, user_turn_count, tool_invocation_count
        assert usage_accumulator is not None
        footers.append(
            (usage_accumulator.agent_usage(), usage_accumulator.last_total_tokens)
        )

    monkeypatch.setattr(
        loop_module, "NativeAgentUsagePublisher", RecordingUsagePublisher
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    call = ProviderToolCall("usage-call", "echo", '{"text":"ok"}')
    provider = _ScriptProvider(
        (
            _result(
                "tool",
                usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                tool_calls=(call,),
            ),
            _result("done", usage={"input_tokens": 3, "output_tokens": 1}),
            _result(
                None,
                usage={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
                status=HarnessStatus.FAILED,
            ),
        )
    )
    canonical = _CollectingSink(trace=trace)
    _run(
        NativeToolReplSession(
            provider=provider,
            tool_registry={"echo": _EchoTool()},
            agent_event_sink=canonical,
        ),
        tmp_path,
        "first\nsecond\n",
    )

    _assert_usage_publications(publications)
    assert footers[-2:] == [
        (AgentUsage(input_tokens=5, output_tokens=2), 4),
        (AgentUsage(input_tokens=12, output_tokens=4), 9),
    ]
    for publication in publications:
        publish_index = trace.index(("publish", publication))
        update_index = next(
            index
            for index, item in enumerate(trace[publish_index + 1 :], publish_index + 1)
            if isinstance(item[1], UsageUpdated)
        )
        later_terminal = next(
            index
            for index, item in enumerate(trace[update_index + 1 :], update_index + 1)
            if isinstance(item[1], (MessageCompleted, ProviderFailed))
            and not (
                isinstance(item[1], MessageCompleted)
                and isinstance(item[1].message, AgentUserMessage)
            )
        )
        assert publish_index < update_index < later_terminal


def _write_queue_extension(tmp_path: Path) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "runtime_queue.py").write_text(
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def tag(event, ctx):\n"
        "        return InputTransform(text='[TAGGED] ' + event.text)\n"
        "    def queue(ctx, args):\n"
        "        ctx.send_message(\n"
        "            {'customType': 'note', 'content': '/not-a-command'},\n"
        "            {'deliverAs': 'followUp'},\n"
        "        )\n"
        "        ctx.send_message(\n"
        "            {'customType': 'note', 'content': '!not-a-shell'},\n"
        "            {'deliverAs': 'steer'},\n"
        "        )\n"
        "    api.register_command('queue-runtime', 'queue runtime input', queue)\n",
        encoding="utf-8",
    )


def test_product_queue_port_preserves_priority_kind_and_original_hooked_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_queue_extension(tmp_path)
    taken: list[AgentQueuedInput] = []
    original = loop_module.NativeAgentQueuedInputPort

    class RecordingQueuedInputPort:
        def __init__(self, take_next: Callable[[], AgentQueuedInput | None]) -> None:
            self._delegate = original(take_next)

        def take_next(self) -> AgentQueuedInput | None:
            queued_input = self._delegate.take_next()
            if queued_input is not None:
                taken.append(queued_input)
            return queued_input

    monkeypatch.setattr(
        loop_module, "NativeAgentQueuedInputPort", RecordingQueuedInputPort
    )
    provider = _ScriptProvider((_result("seed"), _result("steer"), _result("follow")))
    canonical = _CollectingSink()
    _run(
        NativeToolReplSession(
            provider=provider,
            initial_messages=("seed-first",),
            agent_event_sink=canonical,
        ),
        tmp_path,
        "/queue-runtime\n",
    )

    assert [request.user_prompt for request in provider.requests] == [
        "[TAGGED] seed-first",
        "[TAGGED] !not-a-shell",
        "[TAGGED] /not-a-command",
    ]
    assert taken == [
        AgentQueuedInput(ProductContent("!not-a-shell"), AgentQueuedInputKind.STEERING),
        AgentQueuedInput(
            ProductContent("/not-a-command"), AgentQueuedInputKind.FOLLOW_UP
        ),
    ]
    consumed = [
        event
        for event in canonical.events
        if isinstance(event, (SteeringConsumed, FollowUpConsumed))
    ]
    assert consumed == [
        SteeringConsumed(ProductContent("!not-a-shell")),
        FollowUpConsumed(ProductContent("/not-a-command")),
    ]


def test_run_effect_failure_prevents_turn_start_and_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    class FailingRunEffectSink:
        def __init__(self, _append_message: Callable[[AgentMessage], object]) -> None:
            pass

        def emit(self, effect: AgentRunEffect) -> None:
            assert isinstance(effect.message, AgentUserMessage)
            raise RuntimeError("effect refused append")

    monkeypatch.setattr(loop_module, "NativeAgentRunEffectSink", FailingRunEffectSink)
    provider = _ScriptProvider((_result("unused"),))
    canonical = _CollectingSink()
    with pytest.raises(RuntimeError, match="effect refused append"):
        _run(
            NativeToolReplSession(provider=provider, agent_event_sink=canonical),
            tmp_path,
            "prompt\n",
        )

    assert provider.requests == []
    assert [type(event) for event in canonical.events] == [AgentRunStarted]


@pytest.mark.parametrize("status", [HarnessStatus.SUCCEEDED, HarnessStatus.FAILED])
def test_usage_publisher_failure_stops_before_completion_failure_or_next_request(
    status: HarnessStatus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    publications: list[AgentUsagePublication] = []

    class FailingUsagePublisher:
        def __init__(
            self,
            _absorb_usage: Callable[[AgentProviderUsageSample], None],
            _sink: AgentEventSink,
        ) -> None:
            pass

        def publish(self, publication: AgentUsagePublication) -> None:
            publications.append(publication)
            raise RuntimeError("usage publication failed")

    monkeypatch.setattr(loop_module, "NativeAgentUsagePublisher", FailingUsagePublisher)
    call = ProviderToolCall("unused-tool-call", "echo", '{"text":"unused"}')
    provider = _ScriptProvider(
        (
            _result(
                "provider completed" if status is HarnessStatus.SUCCEEDED else None,
                usage={"input_tokens": 4, "output_tokens": 2},
                tool_calls=(call,) if status is HarnessStatus.SUCCEEDED else (),
                status=status,
            ),
            _result("must not run"),
        )
    )
    canonical = _CollectingSink()
    with pytest.raises(RuntimeError, match="usage publication failed"):
        _run(
            NativeToolReplSession(
                provider=provider,
                tool_registry={"echo": _EchoTool()},
                agent_event_sink=canonical,
            ),
            tmp_path,
            "prompt\n",
        )

    assert len(provider.requests) == 1
    assert len(publications) == 1
    assert publications[0].cumulative_usage == AgentUsage(
        input_tokens=4, output_tokens=2
    )
    assert not any(isinstance(event, UsageUpdated) for event in canonical.events)
    assert not any(isinstance(event, ProviderFailed) for event in canonical.events)
    assert not any(
        isinstance(event, MessageCompleted)
        and isinstance(event.message, AgentAssistantMessage)
        for event in canonical.events
    )
