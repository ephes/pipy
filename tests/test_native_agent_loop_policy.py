"""Direct contracts for the pure canonical agent-loop policy layer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent import loop_policy as loop_policy_module
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import (
    MAX_AGENT_TOOL_BUDGET,
    AgentProviderRequestPolicy,
    AgentProviderRequestPolicyInput,
    AgentProviderStatusAction,
    AgentProviderStatusDecision,
    AgentToolPolicy,
    AgentToolPolicyAction,
    AgentToolPolicyDecision,
    AgentToolPolicyState,
    AgentToolPolicyTransition,
    apply_tool_policy_decision,
    decide_tool_admission,
    normalize_provider_status,
    settle_tool_execution,
)
from pipy_harness.native.agent.messages import (
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import AgentFailure
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tools.base import ToolDefinition
from pipy_harness.status import HarnessStatus


class _IntSubclass(int):
    pass


class _StringSubclass(str):
    pass


def _active_input() -> AgentActiveInput:
    return AgentActiveInput(AgentUserMessage(ProductContent("hello")))


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="hello",
        provider_name="fixture",
        model_id="fixture-model",
        cwd=tmp_path,
    )


def _call() -> AgentToolCall:
    return AgentToolCall("provider-1", "read", ProductContent("{}"))


def _snapshot(*, authorized: bool) -> AgentProviderRequestSnapshot:
    tools: tuple[ToolDefinition, ...] = ()
    if authorized:
        tools = (
            ToolDefinition(
                name="read",
                description="Fixture read tool.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
        )
    request = AgentProviderRequestPolicyInput(
        ProviderRequest(
            system_prompt="system",
            user_prompt="hello",
            provider_name="fixture",
            model_id="fixture-model",
            cwd=Path("/fixture"),
            available_tools=tools,
        ),
        _active_input(),
    ).baseline
    return snapshot_provider_request(request)


def _tool_result(*, is_error: bool = False) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-fixture",
        tool_name="read",
        content=ProductContent("result"),
        provider_correlation_id="provider-1",
        is_error=is_error,
    )


def _provider_result(
    *,
    status: HarnessStatus = HarnessStatus.SUCCEEDED,
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=status,
        provider_name="fixture",
        model_id="fixture-model",
        started_at=now,
        ended_at=now,
        final_text="done" if status is HarnessStatus.SUCCEEDED else None,
        error_type=error_type,
        error_message=error_message,
        metadata=metadata,
    )


class _RequestPolicy:
    def prepare(
        self,
        policy_input: AgentProviderRequestPolicyInput,
        /,
    ) -> AgentProviderRequestSnapshot:
        return snapshot_provider_request(policy_input.baseline)


class _ToolPolicy:
    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        del call
        return AgentToolPolicyDecision()

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent:
        del call, result
        return ProductContent("transformed")


class _CountingToolPolicy(_ToolPolicy):
    def __init__(self) -> None:
        self.before_execute_count = 0

    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        self.before_execute_count += 1
        return super().before_execute(call)


def _admit_with_policy(
    state: AgentToolPolicyState,
    *,
    authorized: bool,
    policy: AgentToolPolicy,
) -> AgentToolPolicyTransition:
    admission = decide_tool_admission(state, _snapshot(authorized=authorized), _call())
    if admission.action is not AgentToolPolicyAction.EXECUTE:
        return admission
    return apply_tool_policy_decision(
        admission.state,
        policy.before_execute(_call()),
    )


def test_request_and_tool_policy_protocols_are_runtime_checkable(
    tmp_path: Path,
) -> None:
    request_policy = _RequestPolicy()
    tool_policy = _ToolPolicy()
    policy_input = AgentProviderRequestPolicyInput(_request(tmp_path), _active_input())

    assert isinstance(request_policy, AgentProviderRequestPolicy)
    assert request_policy.prepare(policy_input).request == policy_input.baseline
    assert isinstance(tool_policy, AgentToolPolicy)
    assert tool_policy.before_execute(_call()) == AgentToolPolicyDecision()
    assert tool_policy.transform_result(_call(), _tool_result()) == ProductContent(
        "transformed"
    )
    assert not isinstance(object(), AgentProviderRequestPolicy)
    assert not isinstance(object(), AgentToolPolicy)


def test_core_admission_skips_product_policy_for_exhausted_and_unauthorized() -> None:
    policy = _CountingToolPolicy()

    exhausted = _admit_with_policy(
        AgentToolPolicyState(tool_budget=1, invocations_this_turn=1),
        authorized=True,
        policy=policy,
    )
    unauthorized = _admit_with_policy(
        AgentToolPolicyState(tool_budget=2),
        authorized=False,
        policy=policy,
    )

    assert exhausted.action is AgentToolPolicyAction.BUDGET_EXHAUSTED
    assert unauthorized.action is AgentToolPolicyAction.UNAUTHORIZED
    assert policy.before_execute_count == 0


def test_core_admission_invokes_product_policy_only_after_execute() -> None:
    policy = _CountingToolPolicy()

    transition = _admit_with_policy(
        AgentToolPolicyState(tool_budget=2),
        authorized=True,
        policy=policy,
    )

    assert transition.action is AgentToolPolicyAction.EXECUTE
    assert policy.before_execute_count == 1


def test_tool_policy_can_transform_content_but_not_result_identity() -> None:
    policy = _ToolPolicy()
    call = _call()
    result = _tool_result(is_error=True)

    transformed = policy.transform_result(call, result)

    assert transformed == ProductContent("transformed")
    assert result.tool_request_id == "pipy-tool-fixture"
    assert result.tool_name == "read"
    assert result.provider_correlation_id == "provider-1"
    assert result.is_error is True


@pytest.mark.parametrize("authorized", [True, False])
def test_budget_exhaustion_precedes_authorization(authorized: bool) -> None:
    state = AgentToolPolicyState(
        tool_budget=2,
        invocations_this_turn=2,
        tool_invocation_count=7,
        malformed_argument_count=4,
        consecutive_malformed_streak=1,
        budget_exhausted_count=5,
    )

    transition = decide_tool_admission(state, _snapshot(authorized=authorized), _call())

    assert transition.action is AgentToolPolicyAction.BUDGET_EXHAUSTED
    assert transition.state == AgentToolPolicyState(
        tool_budget=2,
        invocations_this_turn=2,
        tool_invocation_count=7,
        malformed_argument_count=4,
        consecutive_malformed_streak=1,
        budget_exhausted_count=6,
    )
    assert transition.failure is None


def test_budget_exhaustion_does_not_consult_request_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(authorized=True)

    def fail_authorization(
        _snapshot: AgentProviderRequestSnapshot,
        _tool_name: str,
    ) -> bool:
        raise AssertionError("authorization must follow the budget gate")

    monkeypatch.setattr(AgentProviderRequestSnapshot, "authorizes", fail_authorization)

    transition = decide_tool_admission(
        AgentToolPolicyState(tool_budget=1, invocations_this_turn=1),
        snapshot,
        _call(),
    )

    assert transition.action is AgentToolPolicyAction.BUDGET_EXHAUSTED


def test_tool_admission_does_not_repeat_deep_snapshot_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(authorized=True)

    def fail_deep_validation(_snapshot: AgentProviderRequestSnapshot) -> None:
        raise AssertionError("validated immutable snapshots must not be rescanned")

    monkeypatch.setattr(
        loop_policy_module,
        "validate_provider_request_snapshot",
        fail_deep_validation,
        raising=False,
    )

    transition = decide_tool_admission(
        AgentToolPolicyState(tool_budget=1),
        snapshot,
        _call(),
    )

    assert transition.action is AgentToolPolicyAction.EXECUTE


def test_tool_policy_budget_uses_the_canonical_maximum() -> None:
    assert MAX_AGENT_TOOL_BUDGET == 200
    assert AgentToolPolicyState(tool_budget=MAX_AGENT_TOOL_BUDGET).tool_budget == 200
    with pytest.raises(ValueError, match="between 1 and 200"):
        AgentToolPolicyState(tool_budget=MAX_AGENT_TOOL_BUDGET + 1)


def test_unauthorized_admission_consumes_only_a_per_turn_slot() -> None:
    state = AgentToolPolicyState(
        tool_budget=5,
        invocations_this_turn=1,
        tool_invocation_count=8,
        malformed_argument_count=3,
        consecutive_malformed_streak=2,
        budget_exhausted_count=4,
    )

    transition = decide_tool_admission(state, _snapshot(authorized=False), _call())

    assert transition.action is AgentToolPolicyAction.UNAUTHORIZED
    assert transition.state == AgentToolPolicyState(
        tool_budget=5,
        invocations_this_turn=2,
        tool_invocation_count=8,
        malformed_argument_count=3,
        consecutive_malformed_streak=2,
        budget_exhausted_count=4,
    )


def test_blocked_policy_decision_consumes_only_a_per_turn_slot() -> None:
    state = AgentToolPolicyState(
        tool_budget=5,
        invocations_this_turn=1,
        tool_invocation_count=8,
        malformed_argument_count=3,
        consecutive_malformed_streak=2,
        budget_exhausted_count=4,
    )

    transition = apply_tool_policy_decision(
        state,
        AgentToolPolicyDecision(ProductContent("blocked")),
    )

    assert transition.action is AgentToolPolicyAction.BLOCKED
    assert transition.state == AgentToolPolicyState(
        tool_budget=5,
        invocations_this_turn=2,
        tool_invocation_count=8,
        malformed_argument_count=3,
        consecutive_malformed_streak=2,
        budget_exhausted_count=4,
    )


def test_authorized_unblocked_admission_defers_counters_until_settlement() -> None:
    state = AgentToolPolicyState(tool_budget=3, consecutive_malformed_streak=0)

    admission = decide_tool_admission(state, _snapshot(authorized=True), _call())
    transition = apply_tool_policy_decision(state, AgentToolPolicyDecision())

    assert admission == AgentToolPolicyTransition(
        AgentToolPolicyAction.EXECUTE,
        state,
    )
    assert transition == AgentToolPolicyTransition(
        AgentToolPolicyAction.EXECUTE,
        state,
    )


def test_apply_tool_policy_decision_rejects_invalid_values() -> None:
    state = AgentToolPolicyState(tool_budget=3)

    with pytest.raises(TypeError, match="state"):
        apply_tool_policy_decision(
            cast(AgentToolPolicyState, object()),
            AgentToolPolicyDecision(),
        )
    with pytest.raises(TypeError, match="policy_decision"):
        apply_tool_policy_decision(
            state,
            cast(AgentToolPolicyDecision, object()),
        )


@pytest.mark.parametrize("is_error", [False, True])
def test_settled_execution_consumes_slot_and_resets_malformed_streak(
    is_error: bool,
) -> None:
    state = AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=1,
        tool_invocation_count=9,
        malformed_argument_count=5,
        consecutive_malformed_streak=2,
    )

    transition = settle_tool_execution(
        state,
        ToolExecutionOutcome(_tool_result(is_error=is_error)),
    )

    assert transition.action is AgentToolPolicyAction.SETTLED
    assert transition.state == AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=2,
        tool_invocation_count=10,
        malformed_argument_count=5,
        consecutive_malformed_streak=0,
    )


def test_malformed_settlement_does_not_consume_tool_slot() -> None:
    state = AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=2,
        tool_invocation_count=6,
        malformed_argument_count=8,
        consecutive_malformed_streak=1,
    )

    transition = settle_tool_execution(
        state,
        ToolExecutionOutcome(_tool_result(is_error=True), malformed_arguments=True),
    )

    assert transition.action is AgentToolPolicyAction.MALFORMED
    assert transition.state == AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=2,
        tool_invocation_count=6,
        malformed_argument_count=9,
        consecutive_malformed_streak=2,
    )
    assert transition.failure is None


def test_third_consecutive_malformed_call_returns_exact_fatal_failure() -> None:
    state = AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=2,
        tool_invocation_count=6,
        malformed_argument_count=9,
        consecutive_malformed_streak=2,
    )

    transition = settle_tool_execution(
        state,
        ToolExecutionOutcome(_tool_result(is_error=True), malformed_arguments=True),
    )

    assert transition.state.malformed_argument_count == 10
    assert transition.state.consecutive_malformed_streak == 3
    assert transition.state.invocations_this_turn == 2
    assert transition.state.tool_invocation_count == 6
    assert transition.failure == AgentFailure(
        "NativeToolLoopMalformedFatal",
        ProductContent("3 consecutive malformed tool calls"),
    )


@pytest.mark.parametrize(
    "interruption",
    [
        ToolExecutionInterruption.OPERATOR_ABORT,
        ToolExecutionInterruption.LOCAL_COMMAND,
    ],
)
def test_interruption_precedes_malformed_and_changes_no_counters(
    interruption: ToolExecutionInterruption,
) -> None:
    state = AgentToolPolicyState(
        tool_budget=4,
        invocations_this_turn=2,
        tool_invocation_count=6,
        malformed_argument_count=9,
        consecutive_malformed_streak=2,
        budget_exhausted_count=1,
    )

    transition = settle_tool_execution(
        state,
        ToolExecutionOutcome(
            _tool_result(is_error=True),
            malformed_arguments=True,
            interruption=interruption,
        ),
    )

    assert transition.action is AgentToolPolicyAction.INTERRUPTED
    assert transition.state is state
    assert transition.interruption is interruption
    assert transition.failure is None


def test_settlement_cannot_bypass_the_tool_budget() -> None:
    state = AgentToolPolicyState(tool_budget=1, invocations_this_turn=1)

    with pytest.raises(ValueError, match="cannot exceed tool_budget"):
        settle_tool_execution(state, ToolExecutionOutcome(_tool_result()))


def test_provider_success_has_no_failure_or_retry() -> None:
    decision = normalize_provider_status(_provider_result(), provider_name="fixture")

    assert decision == AgentProviderStatusDecision(AgentProviderStatusAction.SUCCEEDED)
    assert decision.failure is None
    assert decision.response_status is None
    assert decision.will_retry is False


def test_provider_failure_preserves_explicit_fields_and_response_status() -> None:
    decision = normalize_provider_status(
        _provider_result(
            status=HarnessStatus.FAILED,
            error_type="ExplicitFailure",
            error_message="explicit message",
            metadata={"response_status": "rate_limited"},
        ),
        provider_name="fixture",
    )

    assert decision.action is AgentProviderStatusAction.FAILED
    assert decision.failure == AgentFailure(
        "ExplicitFailure", ProductContent("explicit message")
    )
    assert decision.response_status == "rate_limited"
    assert decision.will_retry is False


@pytest.mark.parametrize("status", [HarnessStatus.FAILED, HarnessStatus.ABORTED])
def test_provider_failure_uses_exact_fallback(status: HarnessStatus) -> None:
    decision = normalize_provider_status(
        _provider_result(status=status),
        provider_name="selected-provider",
    )

    assert decision.failure == AgentFailure(
        "ProviderFailed",
        ProductContent(
            "provider 'selected-provider' returned status "
            f"'{status.value}' without a final response"
        ),
    )


@pytest.mark.parametrize(
    "metadata",
    [{}, {"response_status": ""}, {"response_status": 42}],
)
def test_provider_response_status_is_optional_string_diagnostic(
    metadata: dict[str, object],
) -> None:
    decision = normalize_provider_status(
        _provider_result(status=HarnessStatus.FAILED, metadata=metadata),
        provider_name="fixture",
    )

    assert decision.response_status is None


def test_policy_values_are_frozen_and_have_no_mutable_fields(tmp_path: Path) -> None:
    policy_input = AgentProviderRequestPolicyInput(_request(tmp_path), _active_input())
    state = AgentToolPolicyState(tool_budget=3)
    transition = AgentToolPolicyTransition(AgentToolPolicyAction.EXECUTE, state)
    provider_decision = AgentProviderStatusDecision(AgentProviderStatusAction.SUCCEEDED)

    for value, field_name, replacement in (
        (policy_input, "baseline", _request(tmp_path)),
        (state, "tool_budget", 4),
        (transition, "action", AgentToolPolicyAction.BLOCKED),
        (provider_decision, "will_retry", True),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, replacement)
    assert not hasattr(state, "token_budget")
    assert all(
        not isinstance(value, (list, dict, set))
        for value in (
            policy_input.baseline,
            policy_input.active_input,
            state.tool_budget,
            transition.action,
            provider_decision.response_status,
        )
    )


def test_request_policy_input_deep_freezes_detached_tool_schemas(
    tmp_path: Path,
) -> None:
    required = ["path"]
    path_schema: dict[str, object] = {
        "type": "string",
        "enum": ["one", "two"],
    }
    properties: dict[str, object] = {"path": path_schema}
    source_schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    definition = ToolDefinition("read", "Read fixture.", source_schema)
    policy_input = AgentProviderRequestPolicyInput(
        ProviderRequest(
            system_prompt="system",
            user_prompt="hello",
            provider_name="fixture",
            model_id="fixture-model",
            cwd=tmp_path,
            available_tools=(definition,),
        ),
        _active_input(),
    )

    required.append("later")
    path_schema["enum"] = ["changed"]
    properties["later"] = {"type": "boolean"}
    source_schema["additionalProperties"] = True

    stored = policy_input.baseline.available_tools[0].input_schema
    stored_properties = cast(dict[str, object], stored["properties"])
    stored_path = cast(dict[str, object], stored_properties["path"])
    assert stored["required"] == ("path",)
    assert stored_path["enum"] == ("one", "two")
    assert tuple(stored_properties) == ("path",)
    assert stored["additionalProperties"] is False
    assert not isinstance(stored, dict)
    with pytest.raises(TypeError):
        cast(dict[str, object], stored)["new"] = "value"
    with pytest.raises(TypeError):
        dict.__setitem__(cast(dict[str, object], stored), "new", "value")
    with pytest.raises(AttributeError):
        stored_properties.update({"new": {"type": "boolean"}})
    with pytest.raises(AttributeError):
        cast(list[str], stored["required"]).append("new")


@pytest.mark.parametrize(
    "request_update, error_match",
    [
        ({"provider_turn_index": True}, "provider_turn_index"),
        ({"messages": []}, "messages"),
        ({"available_tools": []}, "available_tools"),
        ({"attachments": []}, "attachments"),
        ({"provider_header_callback": "not-callable"}, "provider_header_callback"),
    ],
)
def test_request_policy_input_rejects_wrong_nested_request_values(
    tmp_path: Path,
    request_update: dict[str, object],
    error_match: str,
) -> None:
    request = _request(tmp_path)
    for field_name, value in request_update.items():
        object.__setattr__(request, field_name, value)

    with pytest.raises(TypeError, match=error_match):
        AgentProviderRequestPolicyInput(
            request,
            _active_input(),
        )


@pytest.mark.parametrize(
    "bad_schema, error_match",
    [
        (
            {
                "type": "object",
                "properties": {},
                "required": [],
                "description": object(),
            },
            "immutable JSON",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": True},
                },
                "required": [],
            },
            "minimum",
        ),
    ],
)
def test_request_policy_input_rejects_wrong_nested_schema_values(
    tmp_path: Path,
    bad_schema: dict[str, object],
    error_match: str,
) -> None:
    definition = ToolDefinition("bad", "Bad schema fixture.", bad_schema)

    with pytest.raises(TypeError, match=error_match):
        AgentProviderRequestPolicyInput(
            ProviderRequest(
                system_prompt="system",
                user_prompt="hello",
                provider_name="fixture",
                model_id="fixture-model",
                cwd=tmp_path,
                available_tools=(definition,),
            ),
            _active_input(),
        )


@pytest.mark.parametrize("budget", [True, False, 0, 201, -1])
def test_tool_policy_state_rejects_bool_and_out_of_range_budgets(
    budget: object,
) -> None:
    error = TypeError if isinstance(budget, bool) else ValueError
    with pytest.raises(error):
        AgentToolPolicyState(tool_budget=cast(int, budget))


@pytest.mark.parametrize(
    "field_name",
    [
        "tool_budget",
        "malformed_limit",
        "invocations_this_turn",
        "tool_invocation_count",
        "malformed_argument_count",
        "consecutive_malformed_streak",
        "budget_exhausted_count",
    ],
)
def test_tool_policy_state_rejects_int_subclasses(field_name: str) -> None:
    values = {
        "tool_budget": 3,
        "malformed_limit": 3,
        "invocations_this_turn": 0,
        "tool_invocation_count": 0,
        "malformed_argument_count": 0,
        "consecutive_malformed_streak": 0,
        "budget_exhausted_count": 0,
    }
    values[field_name] = _IntSubclass(values[field_name])

    with pytest.raises(TypeError, match=field_name):
        AgentToolPolicyState(**values)


def test_provider_status_rejects_string_subclass_diagnostic() -> None:
    with pytest.raises(TypeError, match="response_status"):
        AgentProviderStatusDecision(
            AgentProviderStatusAction.FAILED,
            AgentFailure("ProviderFailed", ProductContent("failed")),
            response_status=_StringSubclass("rate_limited"),
        )


def test_policy_values_reject_mutable_and_invalid_substitutions(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="baseline"):
        AgentProviderRequestPolicyInput(
            cast(ProviderRequest, []),
            _active_input(),
        )
    with pytest.raises(TypeError, match="active_input"):
        AgentProviderRequestPolicyInput(
            _request(tmp_path),
            cast(AgentActiveInput, []),
        )
    with pytest.raises(TypeError, match="blocked_reason"):
        AgentToolPolicyDecision(cast(ProductContent, []))
    with pytest.raises(ValueError, match="malformed_limit must be 3"):
        AgentToolPolicyState(tool_budget=3, malformed_limit=4)
    with pytest.raises(ValueError, match="streak"):
        AgentToolPolicyState(
            tool_budget=3,
            malformed_argument_count=0,
            consecutive_malformed_streak=1,
        )
    with pytest.raises(TypeError, match="action"):
        AgentToolPolicyTransition(
            cast(AgentToolPolicyAction, []),
            AgentToolPolicyState(tool_budget=3),
        )
    with pytest.raises(TypeError, match="response_status"):
        AgentProviderStatusDecision(
            AgentProviderStatusAction.SUCCEEDED,
            response_status=cast(str, []),
        )
    with pytest.raises(ValueError, match="does not schedule retries"):
        AgentProviderStatusDecision(
            AgentProviderStatusAction.SUCCEEDED,
            will_retry=True,
        )
    with pytest.raises(TypeError, match="snapshot"):
        decide_tool_admission(
            AgentToolPolicyState(tool_budget=1),
            cast(AgentProviderRequestSnapshot, object()),
            _call(),
        )
    with pytest.raises(TypeError, match="call"):
        decide_tool_admission(
            AgentToolPolicyState(tool_budget=1),
            _snapshot(authorized=True),
            cast(AgentToolCall, object()),
        )
    with pytest.raises(TypeError, match="provider_name"):
        normalize_provider_status(_provider_result(), provider_name="")
    with pytest.raises(TypeError, match="provider_name"):
        normalize_provider_status(
            _provider_result(),
            provider_name=cast(str, 3),
        )


def test_request_policy_input_rejects_canonical_value_subclasses(
    tmp_path: Path,
) -> None:
    class RequestSubclass(ProviderRequest):
        pass

    class DefinitionSubclass(ToolDefinition):
        pass

    class UserMessageSubclass(AgentUserMessage):
        pass

    class AttachmentSubclass(ProviderImageAttachment):
        pass

    exact_attachment = ProviderImageAttachment(
        "image/png", "encoded", 3, "sha", "fixture.png"
    )
    exact_input = AgentProviderRequestPolicyInput(
        ProviderRequest(
            "system",
            "hello",
            "fixture",
            "fixture-model",
            tmp_path,
            attachments=(exact_attachment,),
        ),
        _active_input(),
    )
    assert exact_input.baseline.attachments == (exact_attachment,)

    with pytest.raises(TypeError, match="baseline"):
        AgentProviderRequestPolicyInput(
            RequestSubclass("system", "hello", "fixture", "fixture-model", tmp_path),
            _active_input(),
        )
    with pytest.raises(TypeError, match="ToolDefinition"):
        AgentProviderRequestPolicyInput(
            ProviderRequest(
                "system",
                "hello",
                "fixture",
                "fixture-model",
                tmp_path,
                available_tools=(
                    DefinitionSubclass(
                        "read",
                        "Read fixture.",
                        {"type": "object", "properties": {}, "required": []},
                    ),
                ),
            ),
            _active_input(),
        )
    with pytest.raises(TypeError, match="non-canonical message"):
        AgentProviderRequestPolicyInput(
            ProviderRequest(
                "system",
                "hello",
                "fixture",
                "fixture-model",
                tmp_path,
                messages=(UserMessageSubclass(ProductContent("hello")),),
            ),
            _active_input(),
        )
    with pytest.raises(TypeError, match="ProviderImageAttachment"):
        AgentProviderRequestPolicyInput(
            ProviderRequest(
                "system",
                "hello",
                "fixture",
                "fixture-model",
                tmp_path,
                attachments=(
                    AttachmentSubclass("image/png", "encoded", 3, "sha", "fixture.png"),
                ),
            ),
            _active_input(),
        )
    with pytest.raises(TypeError, match="accepted_message"):
        AgentProviderRequestPolicyInput(
            _request(tmp_path),
            AgentActiveInput(UserMessageSubclass(ProductContent("hello"))),
        )


def test_policy_transitions_reject_canonical_value_subclasses() -> None:
    class StateSubclass(AgentToolPolicyState):
        pass

    class SnapshotSubclass(AgentProviderRequestSnapshot):
        pass

    class CallSubclass(AgentToolCall):
        pass

    class DecisionSubclass(AgentToolPolicyDecision):
        pass

    class OutcomeSubclass(ToolExecutionOutcome):
        pass

    class ResultSubclass(AgentToolResultMessage):
        pass

    class FailureSubclass(AgentFailure):
        pass

    state = AgentToolPolicyState(tool_budget=3)
    snapshot = _snapshot(authorized=True)
    call = _call()
    decision_subclass = object.__new__(DecisionSubclass)
    object.__setattr__(decision_subclass, "blocked_reason", None)

    with pytest.raises(TypeError, match="state"):
        AgentToolPolicyTransition(
            AgentToolPolicyAction.EXECUTE,
            StateSubclass(tool_budget=3),
        )
    with pytest.raises(TypeError, match="state"):
        decide_tool_admission(StateSubclass(tool_budget=3), snapshot, call)
    with pytest.raises(TypeError, match="snapshot"):
        decide_tool_admission(
            state,
            SnapshotSubclass(snapshot.request, snapshot.advertised_tool_names),
            call,
        )
    with pytest.raises(TypeError, match="call"):
        decide_tool_admission(
            state,
            snapshot,
            CallSubclass("provider-1", "read", ProductContent("{}")),
        )
    with pytest.raises(TypeError, match="policy_decision"):
        apply_tool_policy_decision(state, decision_subclass)
    with pytest.raises(TypeError, match="outcome"):
        settle_tool_execution(state, OutcomeSubclass(_tool_result()))
    with pytest.raises(TypeError, match="exact AgentToolResultMessage"):
        settle_tool_execution(
            state,
            ToolExecutionOutcome(
                ResultSubclass(
                    "pipy-tool-fixture",
                    "read",
                    ProductContent("result"),
                    "provider-1",
                )
            ),
        )
    failure = FailureSubclass("Failure", ProductContent("failed"))
    with pytest.raises(TypeError, match="exact AgentFailure"):
        AgentToolPolicyTransition(
            AgentToolPolicyAction.MALFORMED,
            AgentToolPolicyState(
                tool_budget=3,
                malformed_argument_count=3,
                consecutive_malformed_streak=3,
            ),
            failure=failure,
        )
    with pytest.raises(TypeError, match="exact AgentFailure"):
        AgentProviderStatusDecision(
            AgentProviderStatusAction.FAILED,
            failure=failure,
        )


def test_transition_validation_rejects_incoherent_payloads() -> None:
    state = AgentToolPolicyState(tool_budget=3)
    failure = AgentFailure("Failure", ProductContent("failed"))

    with pytest.raises(ValueError, match="requires an interruption"):
        AgentToolPolicyTransition(AgentToolPolicyAction.INTERRUPTED, state)
    with pytest.raises(ValueError, match="only interrupted"):
        AgentToolPolicyTransition(
            AgentToolPolicyAction.SETTLED,
            state,
            interruption=ToolExecutionInterruption.LOCAL_COMMAND,
        )
    with pytest.raises(ValueError, match="only malformed"):
        AgentToolPolicyTransition(
            AgentToolPolicyAction.SETTLED,
            state,
            failure=failure,
        )
    with pytest.raises(ValueError, match="requires a malformed streak"):
        AgentToolPolicyTransition(AgentToolPolicyAction.MALFORMED, state)
    fatal_state = AgentToolPolicyState(
        tool_budget=3,
        malformed_argument_count=3,
        consecutive_malformed_streak=3,
    )
    with pytest.raises(ValueError, match="must match the malformed limit"):
        AgentToolPolicyTransition(AgentToolPolicyAction.MALFORMED, fatal_state)
    with pytest.raises(ValueError, match="requires a failure"):
        AgentProviderStatusDecision(AgentProviderStatusAction.FAILED)
