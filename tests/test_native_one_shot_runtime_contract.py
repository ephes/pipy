"""Slice 10 contracts for the intentionally distinct one-shot runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentEvent,
    AgentEventSink,
    AgentFailure,
    AgentRunOutcome,
    AgentUserMessage,
    AssistantTextDelta,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop import (
    AgentLoop,
    AgentLoopOutcome,
    AgentLoopRequestPreparation,
    AgentLoopRunInput,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusDecision,
    AgentToolPolicyDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.messages import (
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnDeltaPolicy,
    ProviderTurnExecutor,
    ProviderTurnOutcome,
)
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.runtime_ports import AgentUsagePublication
from pipy_harness.native.agent.tools import (
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.models import (
    NativeRunInput,
    NativeToolRequest,
    NativeToolResult,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.session import (
    NativeHarnessCompatibilityRuntime,
    SYSTEM_PROMPT_ID,
    SYSTEM_PROMPT_VERSION,
    _CompatibilityRuntimeInvariantError,
    _HarnessCompatibilityProvider,
)
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(slots=True)
class _WorkflowSink:
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        del summary
        self.events.append((event_type, dict(payload or {})))


@dataclass(slots=True)
class _AgentEvents:
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class _Provider:
    result: ProviderResult
    requests: list[ProviderRequest] = field(default_factory=list)
    sink_presence: list[tuple[bool, bool, bool]] = field(default_factory=list)
    name: str = "fixture"
    model_id: str = "fixture-model"
    supports_tool_calls: bool = False

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        self.requests.append(request)
        self.sink_presence.append(
            (
                stream_sink is not None,
                reasoning_sink is not None,
                cancel_token is not None,
            )
        )
        if stream_sink is not None:
            stream_sink("shared-delta")
        return self.result


@dataclass(slots=True)
class _ExceptionalProvider:
    failure: Exception
    name: str = "fixture"
    model_id: str = "fixture-model"
    supports_tool_calls: bool = False
    complete_calls: int = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        self.complete_calls += 1
        raise self.failure


class _RequestSource:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
        /,
    ) -> AgentLoopRequestPreparation:
        request = ProviderRequest(
            system_prompt="system",
            user_prompt=active_input.accepted_message.content.value,
            provider_name="fixture",
            model_id="fixture-model",
            cwd=self._cwd,
            provider_turn_index=turn_index,
            messages=active_input.request_messages(history),
            available_tools=available_tools,
        )
        return AgentLoopRequestPreparation(
            history,
            snapshot_provider_request(request),
        )


class _CanonicalProviderTurn:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider
        self._executor = ProviderTurnExecutor()

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
        /,
    ) -> ProviderTurnOutcome:
        return self._executor.complete(
            self._provider,
            snapshot.request,
            event_sink,
            turn_index=turn_index,
        )


@dataclass(slots=True)
class _CancellingProvider:
    name: str = "fixture"
    model_id: str = "fixture-model"
    supports_tool_calls: bool = False
    complete_calls: int = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        self.complete_calls += 1
        raise ProviderCancelledError("PRIVATE_PROVIDER_CANCELLATION_DETAIL")


class _NoCanonicalTools:
    def __init__(self) -> None:
        self.execute_count = 0

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        del allowed_names
        return ()

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        del call, output_sink, wait_for_interrupt
        self.execute_count += 1
        raise AssertionError("metadata fixtures are not canonical provider tool calls")

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        del call, output_text
        raise AssertionError("no canonical tool result is expected")


class _AllowToolPolicy:
    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        del call
        return AgentToolPolicyDecision()

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent:
        del call
        return result.content


class _UsagePublisher:
    def __init__(self) -> None:
        self.publications: list[AgentUsagePublication] = []

    def publish(self, publication: AgentUsagePublication) -> None:
        self.publications.append(publication)


class _NoQueuedInput:
    def take_next(self) -> None:
        return None


class _NoopStatusPolicy:
    def run_entered(self) -> None:
        return None

    def input_accepted(self) -> None:
        return None

    def tool_policy_state_changed(self, state: AgentToolPolicyState, /) -> None:
        del state

    def provider_result_observed(self, result: ProviderResult, /) -> None:
        del result

    def provider_cancellation_observed(
        self, reason: AgentCancellationReason, /
    ) -> None:
        del reason

    def provider_succeeded(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        del status, tool_state

    def provider_failed(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        del status, tool_state

    def no_tool_assistant(self, tool_state: AgentToolPolicyState, /) -> None:
        del tool_state

    def malformed_fatal(
        self,
        failure: AgentFailure,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        del failure, tool_state


@dataclass(slots=True)
class _CountingCompatibilityTool:
    delegate: FakeNoOpNativeTool = field(default_factory=FakeNoOpNativeTool)
    invocation_count: int = 0

    @property
    def name(self) -> str:
        return self.delegate.name

    def invoke(self, request: NativeToolRequest) -> NativeToolResult:
        self.invocation_count += 1
        return self.delegate.invoke(request)


def _result(*, metadata: dict[str, object] | None = None) -> ProviderResult:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="fixture",
        model_id="fixture-model",
        started_at=now,
        ended_at=now,
        final_text="shared-final",
        usage={
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 9,
            "cached_tokens": 1,
            "cache_write_tokens": 2,
            "reasoning_tokens": 1,
        },
        metadata=metadata,
    )


def _compatibility_request(cwd: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="goal",
        provider_name="fixture",
        model_id="fixture-model",
        cwd=cwd,
    )


def _safe_compatibility_intent() -> dict[str, object]:
    return {
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "turn_index": 0,
        "intent_source": "provider_metadata",
        "approval_policy": "not-required",
        "approval_required": False,
        "sandbox_policy": "no-workspace-access",
        "filesystem_mutation_allowed": False,
        "shell_execution_allowed": False,
        "network_access_allowed": False,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "metadata": {"fixture": "contract"},
    }


def _run_canonical(
    tmp_path: Path,
    provider: _Provider,
    tools: _NoCanonicalTools,
) -> tuple[AgentLoopOutcome, _AgentEvents, _UsagePublisher]:
    events = _AgentEvents()
    usage = _UsagePublisher()
    loop = AgentLoop(
        request_source=_RequestSource(tmp_path),
        provider_turn=_CanonicalProviderTurn(provider),
        tool_capabilities=tools,
        tool_policy=_AllowToolPolicy(),
        event_sink=events,
        usage_publisher=usage,
        queued_input_port=_NoQueuedInput(),
        status_policy=_NoopStatusPolicy(),
    )
    user = AgentUserMessage(ProductContent("goal"))
    outcome = loop.run(
        AgentLoopRunInput(
            history=(),
            active_input=AgentActiveInput(user),
            tool_policy_state=AgentToolPolicyState(tool_budget=1),
        )
    )
    return outcome, events, usage


def test_simple_provider_result_is_equivalent_at_the_shared_completion_boundary(
    tmp_path: Path,
) -> None:
    compatibility_provider = _Provider(_result())
    canonical_provider = _Provider(_result())
    compatibility_deltas: list[str] = []
    compatibility = NativeHarnessCompatibilityRuntime(
        provider=compatibility_provider,
        stream_sink=compatibility_deltas.append,
    ).run(
        NativeRunInput(
            goal="goal",
            cwd=tmp_path,
            provider_name=compatibility_provider.name,
            model_id=compatibility_provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        _WorkflowSink(),
    )
    canonical, events, usage = _run_canonical(
        tmp_path, canonical_provider, _NoCanonicalTools()
    )
    canonical_deltas = [
        event.delta.value
        for event in events.events
        if isinstance(event, AssistantTextDelta)
    ]
    canonical_usage_sample = usage.publications[0].sample
    canonical_normalized_usage = {
        "input_tokens": canonical_usage_sample.input_tokens,
        "output_tokens": canonical_usage_sample.output_tokens,
        "total_tokens": canonical_usage_sample.total_tokens,
        "cached_tokens": canonical_usage_sample.cache_read_tokens,
        "cache_write_tokens": canonical_usage_sample.cache_write_tokens,
        "reasoning_tokens": canonical_usage_sample.reasoning_tokens,
    }

    assert len(compatibility_provider.requests) == 1
    assert len(canonical_provider.requests) == 1
    assert compatibility_provider.sink_presence == [(True, False, False)]
    assert canonical_provider.sink_presence == [(True, True, False)]
    assert compatibility_deltas == canonical_deltas == ["shared-delta"]
    assert compatibility.final_text == canonical.result.messages[-1].content.value
    assert compatibility.final_text == "shared-final"
    assert (
        compatibility.usage
        == canonical_normalized_usage
        == {
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 9,
            "cached_tokens": 1,
            "cache_write_tokens": 2,
            "reasoning_tokens": 1,
        }
    )


def test_compatibility_provider_rejects_reasoning_and_cancellation_channels(
    tmp_path: Path,
) -> None:
    provider = _Provider(_result())
    adapter = _HarnessCompatibilityProvider(provider)
    request = _compatibility_request(tmp_path)

    with pytest.raises(
        _CompatibilityRuntimeInvariantError,
        match="requires reasoning_sink=None",
    ):
        adapter.complete(request, reasoning_sink=lambda _chunk: None)
    with pytest.raises(
        _CompatibilityRuntimeInvariantError,
        match="requires cancel_token=None",
    ):
        adapter.complete(request, cancel_token=CancelToken())

    chunks: list[str] = []
    assert adapter.complete(request).final_text == "shared-final"
    assert adapter.complete(request, stream_sink=chunks.append).final_text == (
        "shared-final"
    )
    assert chunks == ["shared-delta"]
    assert provider.sink_presence == [
        (False, False, False),
        (True, False, False),
    ]


def test_unsupported_executor_channel_escapes_runtime_without_failed_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(_result())
    workflow = _WorkflowSink()

    def miswired_delta_policy(
        *, text: bool, reasoning: bool
    ) -> ProviderTurnDeltaPolicy:
        del reasoning
        return ProviderTurnDeltaPolicy(text=text, reasoning=True)

    monkeypatch.setattr(
        "pipy_harness.native.session.ProviderTurnDeltaPolicy",
        miswired_delta_policy,
    )

    with pytest.raises(
        _CompatibilityRuntimeInvariantError,
        match="requires reasoning_sink=None",
    ):
        NativeHarnessCompatibilityRuntime(provider=provider).run(
            NativeRunInput(
                goal="goal",
                cwd=tmp_path,
                provider_name=provider.name,
                model_id=provider.model_id,
                system_prompt_id=SYSTEM_PROMPT_ID,
                system_prompt_version=SYSTEM_PROMPT_VERSION,
            ),
            workflow,
        )

    assert provider.requests == []
    assert [event_type for event_type, _payload in workflow.events] == [
        "native.session.started",
        "native.provider.started",
    ]


def test_invalid_executor_provider_wiring_escapes_runtime_without_failed_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(_result())
    workflow = _WorkflowSink()

    def invalid_provider_adapter(_provider: object) -> object:
        return object()

    monkeypatch.setattr(
        "pipy_harness.native.session._HarnessCompatibilityProvider",
        invalid_provider_adapter,
    )

    with pytest.raises(
        _CompatibilityRuntimeInvariantError,
        match="executor invariant failed",
    ) as captured:
        NativeHarnessCompatibilityRuntime(provider=provider).run(
            NativeRunInput(
                goal="goal",
                cwd=tmp_path,
                provider_name=provider.name,
                model_id=provider.model_id,
                system_prompt_id=SYSTEM_PROMPT_ID,
                system_prompt_version=SYSTEM_PROMPT_VERSION,
            ),
            workflow,
        )

    assert isinstance(captured.value.__cause__, TypeError)
    assert provider.requests == []
    assert [event_type for event_type, _payload in workflow.events] == [
        "native.session.started",
        "native.provider.started",
    ]


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (ValueError("PRIVATE_VALUE_PROVIDER_DETAIL"), "ValueError"),
        (TypeError("PRIVATE_TYPE_PROVIDER_DETAIL"), "TypeError"),
    ],
)
def test_genuine_provider_value_and_type_errors_keep_failed_projection(
    tmp_path: Path,
    failure: Exception,
    error_type: str,
) -> None:
    provider = _ExceptionalProvider(failure)
    workflow = _WorkflowSink()

    output = NativeHarnessCompatibilityRuntime(provider=provider).run(
        NativeRunInput(
            goal="goal",
            cwd=tmp_path,
            provider_name=provider.name,
            model_id=provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        workflow,
    )

    provider_failure = next(
        payload
        for event_type, payload in workflow.events
        if event_type == "native.provider.failed"
    )
    assert provider.complete_calls == 1
    assert [event_type for event_type, _payload in workflow.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.failed",
        "native.tool.skipped",
        "native.session.completed",
    ]
    assert output.status is HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_type == error_type
    assert output.error_message == error_type
    assert provider_failure["status"] == HarnessStatus.FAILED.value
    assert provider_failure["error_type"] == error_type
    assert provider_failure["error_message"] == error_type
    assert str(failure) not in repr(workflow.events)


def test_metadata_fixture_tools_are_intentionally_not_agent_loop_tool_calls(
    tmp_path: Path,
) -> None:
    compatibility_provider = _Provider(
        _result(
            metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: _safe_compatibility_intent()}
        )
    )
    canonical_provider = _Provider(
        _result(
            metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: _safe_compatibility_intent()}
        )
    )
    compatibility_tool = _CountingCompatibilityTool()
    workflow = _WorkflowSink()

    compatibility = NativeHarnessCompatibilityRuntime(
        provider=compatibility_provider,
        tool=compatibility_tool,
    ).run(
        NativeRunInput(
            goal="goal",
            cwd=tmp_path,
            provider_name=compatibility_provider.name,
            model_id=compatibility_provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        workflow,
    )
    canonical_tools = _NoCanonicalTools()
    canonical, _events, _usage = _run_canonical(
        tmp_path, canonical_provider, canonical_tools
    )

    assert len(compatibility_provider.requests) == 1
    assert len(canonical_provider.requests) == 1
    assert compatibility_provider.sink_presence == [(False, False, False)]
    assert compatibility.status is HarnessStatus.SUCCEEDED
    assert compatibility_tool.invocation_count == 1
    assert [event_type for event_type, _payload in workflow.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.session.completed",
    ]
    assert canonical.result.outcome is AgentRunOutcome.SUCCEEDED
    assert canonical_tools.execute_count == 0
    assert canonical.final_history[-1].content.value == "shared-final"


def test_typed_provider_cancellation_preserves_compatibility_failure_contract(
    tmp_path: Path,
) -> None:
    provider = _CancellingProvider()
    workflow = _WorkflowSink()

    output = NativeHarnessCompatibilityRuntime(provider=provider).run(
        NativeRunInput(
            goal="goal",
            cwd=tmp_path,
            provider_name=provider.name,
            model_id=provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        workflow,
    )

    provider_failure = next(
        payload
        for event_type, payload in workflow.events
        if event_type == "native.provider.failed"
    )
    assert provider.complete_calls == 1
    assert [event_type for event_type, _payload in workflow.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.failed",
        "native.tool.skipped",
        "native.session.completed",
    ]
    assert output.status is HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_type == "ProviderCancelledError"
    assert output.error_message == "ProviderCancelledError"
    assert provider_failure["status"] == HarnessStatus.FAILED.value
    assert provider_failure["error_type"] == "ProviderCancelledError"
    assert provider_failure["error_message"] == "ProviderCancelledError"
    assert "PRIVATE_PROVIDER_CANCELLATION_DETAIL" not in repr(workflow.events)
