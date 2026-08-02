"""Product status effects for one accepted coding-agent turn.

The canonical agent loop invokes this collaborator at its synchronous status
seams.  It owns the product policy for run entry, accepted-input accounting and
prompt recall, result and cancellation handling, tool-policy counter sync,
provider settlement, the no-tool footer refresh, and malformed-fatal
diagnostics.

Only narrow state and presentation ports cross this boundary.  Provider
transport and construction, extension activation and packages, concrete
terminal UI types, accepted-input preparation, provider-request construction,
local input dispatch, and run coordination remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
)
from pipy_harness.native.models import ProviderResult


class CodingAgentStatusStatePort(Protocol):
    """Narrow mutable state used by agent-turn status effects."""

    @property
    def prompt_for_recall(self) -> str | None: ...

    def mark_run_entered(self) -> None: ...

    def record_input_accepted(self) -> None: ...

    def record_prompt_recall(self, prompt: str, /) -> None: ...

    def sync_tool_policy(self, state: AgentToolPolicyState, /) -> None: ...

    def clear_provider_failure(self) -> None: ...

    def record_provider_failure(self, failure: AgentFailure, /) -> None: ...


class CodingAgentStatusPresentationPort(Protocol):
    """Narrow presentation operations used by agent-turn status effects."""

    def has_pending_input(self) -> bool: ...

    def promote_pending_input(self) -> None: ...

    def restore_pending_input(self) -> None: ...

    def emit_diagnostic(self, message: str, /) -> None: ...

    def refresh_usage_footer(self) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CodingAgentTurnStatusEffects:
    """Apply one accepted turn's status policy through narrow product ports."""

    state: CodingAgentStatusStatePort
    presentation: CodingAgentStatusPresentationPort

    def run_entered(self) -> None:
        self.state.mark_run_entered()

    def input_accepted(self) -> None:
        self.state.record_input_accepted()
        prompt_for_recall = self.state.prompt_for_recall
        if prompt_for_recall is not None:
            self.state.record_prompt_recall(prompt_for_recall)

    def provider_result_observed(self, result: ProviderResult, /) -> None:
        del result
        if self.presentation.has_pending_input():
            self.presentation.promote_pending_input()

    def provider_cancellation_observed(
        self,
        reason: AgentCancellationReason,
        /,
    ) -> None:
        if reason is AgentCancellationReason.OPERATOR_ABORT:
            self.presentation.restore_pending_input()
        elif reason in (
            AgentCancellationReason.STEERING,
            AgentCancellationReason.LOCAL_COMMAND,
        ):
            self.presentation.promote_pending_input()

    def tool_policy_state_changed(
        self,
        state: AgentToolPolicyState,
        /,
    ) -> None:
        self.state.sync_tool_policy(state)

    def provider_succeeded(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        del status, tool_state
        self.state.clear_provider_failure()

    def provider_failed(
        self,
        status: AgentProviderStatusDecision,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        failure = status.failure
        assert failure is not None
        del tool_state
        self.state.record_provider_failure(failure)
        suffix = (
            f" (response_status={status.response_status})"
            if status.response_status is not None
            else ""
        )
        self.presentation.emit_diagnostic(
            "pipy: provider failure during turn: "
            f"{failure.error_type}: {failure.message.value}{suffix}"
        )
        self.presentation.refresh_usage_footer()

    def no_tool_assistant(self, tool_state: AgentToolPolicyState, /) -> None:
        del tool_state
        self.presentation.refresh_usage_footer()

    def malformed_fatal(
        self,
        failure: AgentFailure,
        tool_state: AgentToolPolicyState,
        /,
    ) -> None:
        del tool_state
        self.presentation.emit_diagnostic(
            f"pipy: tool-loop ended after {failure.message.value}"
        )
