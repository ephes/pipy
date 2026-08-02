"""Focused contracts for the coding-agent turn status-effect owner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop import AgentLoopStatusPolicy
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusAction,
    AgentProviderStatusDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
)
from pipy_harness.native.coding.status_effects import CodingAgentTurnStatusEffects
from pipy_harness.native.models import ProviderResult
from pipy_harness.status import HarnessStatus

Trace = list[tuple[object, ...]]


@dataclass(slots=True)
class _StatePort:
    trace: Trace
    prompt_for_recall: str | None

    def mark_run_entered(self) -> None:
        self.trace.append(("run-entered",))

    def record_input_accepted(self) -> None:
        self.trace.append(("input-accepted",))

    def record_prompt_recall(self, prompt: str, /) -> None:
        self.trace.append(("prompt-recall", prompt))

    def sync_tool_policy(self, state: AgentToolPolicyState, /) -> None:
        self.trace.append(("tool-policy", state))

    def clear_provider_failure(self) -> None:
        self.trace.append(("provider-success",))

    def record_provider_failure(self, failure: AgentFailure, /) -> None:
        self.trace.append(("provider-failure", failure))


@dataclass(slots=True)
class _PresentationPort:
    trace: Trace
    pending_input: bool = True

    def has_pending_input(self) -> bool:
        self.trace.append(("pending-input-query",))
        return self.pending_input

    def promote_pending_input(self) -> None:
        self.trace.append(("pending-input-promote",))

    def restore_pending_input(self) -> None:
        self.trace.append(("pending-input-restore",))

    def emit_diagnostic(self, message: str, /) -> None:
        self.trace.append(("diagnostic", message))

    def refresh_usage_footer(self) -> None:
        self.trace.append(("footer",))


def _provider_result() -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="fake",
        model_id="fake-model",
        started_at=now,
        ended_at=now,
        final_text="answer",
        tool_calls=(),
    )


def test_status_effect_family_uses_only_ordered_state_and_presentation_ports() -> None:
    trace: Trace = []
    state = _StatePort(trace, "literal prompt")
    presentation = _PresentationPort(trace)
    effects = CodingAgentTurnStatusEffects(
        state=state,
        presentation=presentation,
    )
    tool_state = AgentToolPolicyState(tool_budget=5)
    success = AgentProviderStatusDecision(AgentProviderStatusAction.SUCCEEDED)
    provider_failure = AgentFailure(
        "ProviderFailed",
        ProductContent("rate limit"),
    )
    failure = AgentProviderStatusDecision(
        AgentProviderStatusAction.FAILED,
        provider_failure,
        response_status="429",
    )
    malformed = AgentFailure(
        "NativeToolLoopMalformedFatal",
        ProductContent("3 consecutive malformed tool calls"),
    )

    assert isinstance(effects, AgentLoopStatusPolicy)

    effects.run_entered()
    effects.input_accepted()
    effects.provider_result_observed(_provider_result())
    effects.tool_policy_state_changed(tool_state)
    effects.provider_succeeded(success, tool_state)
    effects.provider_failed(failure, tool_state)
    effects.no_tool_assistant(tool_state)
    effects.malformed_fatal(malformed, tool_state)

    assert trace == [
        ("run-entered",),
        ("input-accepted",),
        ("prompt-recall", "literal prompt"),
        ("pending-input-query",),
        ("pending-input-promote",),
        ("tool-policy", tool_state),
        ("provider-success",),
        ("provider-failure", provider_failure),
        (
            "diagnostic",
            "pipy: provider failure during turn: "
            "ProviderFailed: rate limit (response_status=429)",
        ),
        ("footer",),
        ("footer",),
        (
            "diagnostic",
            "pipy: tool-loop ended after 3 consecutive malformed tool calls",
        ),
    ]


def test_resource_input_and_absent_pending_input_add_no_recall_or_promotion() -> None:
    trace: Trace = []
    effects = CodingAgentTurnStatusEffects(
        state=_StatePort(trace, None),
        presentation=_PresentationPort(trace, pending_input=False),
    )

    effects.input_accepted()
    effects.provider_result_observed(_provider_result())

    assert trace == [
        ("input-accepted",),
        ("pending-input-query",),
    ]


@pytest.mark.parametrize(
    ("reason", "expected_effect"),
    (
        (AgentCancellationReason.OPERATOR_ABORT, "pending-input-restore"),
        (AgentCancellationReason.STEERING, "pending-input-promote"),
        (AgentCancellationReason.LOCAL_COMMAND, "pending-input-promote"),
    ),
)
def test_cancellation_reason_preserves_pending_input_transition(
    reason: AgentCancellationReason,
    expected_effect: str,
) -> None:
    trace: Trace = []
    effects = CodingAgentTurnStatusEffects(
        state=_StatePort(trace, None),
        presentation=_PresentationPort(trace),
    )

    effects.provider_cancellation_observed(reason)

    assert trace == [(expected_effect,)]
