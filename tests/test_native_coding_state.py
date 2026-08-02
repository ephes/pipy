"""Contracts for headless product coding-session state transitions."""

from __future__ import annotations

import ast
import threading
from pathlib import Path

from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentFailure,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentUsageAccumulator,
    AgentUsageRefreshValue,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.state import (
    CodingProviderBinding,
    CodingReloadBindingValue,
    CodingReloadRebindState,
    CodingSessionResultSnapshot,
    CodingSessionState,
    CodingSessionUsageSnapshot,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink


class _FakeProvider:
    def __init__(self, name: str, model_id: str) -> None:
        self._name = name
        self._model_id = model_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        raise AssertionError("state contracts never invoke the provider")


class _UserMessageSubclass(AgentUserMessage):
    pass


class _ToolPolicyStateSubclass(AgentToolPolicyState):
    pass


class _ProductContentSubclass(ProductContent):
    pass


class _ToolCallSubclass(AgentToolCall):
    pass


def _provider(name: str = "port-name", model_id: str = "port-model") -> ProviderPort:
    return _FakeProvider(name, model_id)


def _message(value: str = "hello") -> AgentUserMessage:
    return AgentUserMessage(ProductContent(value))


def _state(
    *,
    provider: ProviderPort | None = None,
    messages: tuple[AgentMessage, ...] = (),
    accumulator: AgentUsageAccumulator | None = None,
    state_lock: "threading.RLock | None" = None,
) -> CodingSessionState:
    return CodingSessionState(
        provider=provider or _provider(),
        provider_name="explicit-provider",
        model_id="explicit-model",
        usage_accumulator=accumulator or AgentUsageAccumulator(),
        messages=messages,
        state_lock=state_lock,
    )


def test_initial_state_uses_explicit_labels_and_detached_immutable_snapshot() -> None:
    message = _message()
    provider = _provider("different-port-name", "different-port-model")
    state = _state(provider=provider, messages=(message,))

    snapshot = state.result_snapshot()

    assert state.provider is provider
    assert state.provider_name == "explicit-provider"
    assert state.model_id == "explicit-model"
    assert state.messages == (message,)
    assert state.messages[0] is message
    assert state.provider_binding == CodingProviderBinding(
        provider,
        "explicit-provider",
        "explicit-model",
    )
    assert snapshot.provider_name == "explicit-provider"
    assert snapshot.model_id == "explicit-model"
    assert snapshot.messages == (message,)
    assert snapshot.messages[0] is message
    assert snapshot.usage == AgentUsage()
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "compaction_count", 1)


def test_reload_refresh_publishes_only_binding_and_preserves_later_state() -> None:
    message = _message("before")
    later = _message("live-later")
    failure = AgentFailure("ProviderFailure", ProductContent("safe failure"))
    state = _state(messages=(message,))
    replacement = _provider("replacement", "replacement-model")

    expected = state.provider_binding
    prepared = state.prepare_reload_refresh(replacement)
    assert type(prepared) is CodingReloadBindingValue
    assert [field.name for field in fields(prepared)] == ["expected", "replacement"]
    assert prepared.expected is expected
    assert state.reload_binding_matches_expected(prepared)

    state.append_message(later)
    state.apply_compaction(
        (message, later), summary_suffix="\n\nsummary", dropped_group_count=2
    )
    state.record_provider_failure(failure)
    state.publish_reload_refresh(prepared)

    assert state.provider is replacement
    assert state.messages == (message, later)
    assert state.compaction_suffix == "\n\nsummary"
    assert state.compaction_count == 1
    assert state.compaction_dropped_group_count == 2
    assert state.provider_failure is failure


def test_reload_refresh_matches_live_transition_for_owned_binding() -> None:
    original = _provider()
    live = _state(provider=original, messages=(_message("live"),))
    detached = _state(provider=original, messages=(_message("detached"),))
    replacement = _provider("replacement", "replacement-model")

    prepared = detached.prepare_reload_refresh(replacement)
    assert detached.reload_binding_matches_expected(prepared)
    live.refresh_provider(replacement)
    detached.publish_reload_refresh(prepared)

    assert detached.provider_binding == live.provider_binding


@pytest.mark.parametrize(
    ("method_name", "targets", "values"),
    (
        (
            "publish_reload_refresh",
            ["self._binding"],
            ["binding.replacement"],
        ),
        (
            "publish_reload_rebind",
            ["self._binding", "self._messages"],
            ["binding.replacement", "history.messages"],
        ),
    ),
)
def test_coding_reload_publishers_have_exact_assignments_under_sole_shared_lock(
    method_name: str,
    targets: list[str],
    values: list[str],
) -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/coding/state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    publisher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    assert not any(isinstance(node, ast.Call) for node in ast.walk(publisher))
    assert not any(
        isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
        for node in ast.walk(publisher)
    )
    assert [type(node) for node in publisher.body] == [ast.Expr, ast.With]
    guards = [
        node
        for node in ast.walk(publisher)
        if isinstance(node, (ast.With, ast.AsyncWith))
    ]
    assert len(guards) == 1
    guard = guards[0]
    assert isinstance(guard, ast.With)
    assert publisher.body[1] is guard
    assert len(guard.items) == 1
    assert ast.unparse(guard.items[0].context_expr) == "self._state_lock"
    assert all(isinstance(node, ast.Assign) for node in guard.body)
    assignments = cast(list[ast.Assign], guard.body)
    assert all(len(node.targets) == 1 for node in assignments)
    assert [ast.unparse(node.targets[0]) for node in assignments] == targets
    assert [ast.unparse(node.value) for node in assignments] == values


def test_model_mutation_uses_exact_binding_identity_and_assignment_only_publish() -> (
    None
):
    state = _state()
    expected = state.provider_binding
    replacement = _provider("candidate", "candidate-model")
    prepared = state.prepare_model_mutation(
        replacement,
        expected_binding=expected,
        provider_name="candidate",
        model_id="candidate-model",
        usage_accumulator=AgentUsageAccumulator(),
    )

    state.refresh_provider(expected.provider)
    assert state.provider_binding == expected
    assert state.provider_binding is not expected
    assert not state.model_mutation_matches_expected(prepared)
    if state.model_mutation_matches_expected(prepared):
        state.publish_model_mutation(prepared)
    assert state.provider is expected.provider

    source = Path(__file__).parents[1] / "src/pipy_harness/native/coding/state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    publisher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "publish_model_mutation"
    )
    assert not any(isinstance(node, ast.Call) for node in ast.walk(publisher))
    guard = publisher.body[1]
    assert isinstance(guard, ast.With)
    assignments = cast(list[ast.Assign], guard.body)
    assert [ast.unparse(node.targets[0]) for node in assignments] == [
        "self._binding",
        "self._messages",
        "self._usage_accumulator",
    ]
    assert [ast.unparse(node.value) for node in assignments] == [
        "prepared.replacement_binding",
        "()",
        "prepared.replacement_usage",
    ]


def test_reload_rebind_prepares_only_binding_and_immutable_empty_history() -> None:
    message = _message()
    state = _state(messages=(message,))
    replacement = _provider("fallback", "fallback-model")

    prepared = state.prepare_reload_rebind(
        replacement,
        provider_name="fallback",
        model_id="fallback-model",
    )

    assert type(prepared) is CodingReloadRebindState
    assert [field.name for field in fields(prepared)] == ["binding", "history"]
    assert prepared.binding.expected.provider is state.provider
    assert prepared.binding.replacement == CodingProviderBinding(
        replacement, "fallback", "fallback-model"
    )
    assert type(prepared.history.messages) is tuple
    assert prepared.history.messages == ()
    assert state.reload_binding_matches_expected(prepared.binding)
    assert state.messages == (message,)
    assert state.provider is not replacement


def test_reload_binding_expected_token_refuses_post_prepare_rebind() -> None:
    state = _state()
    prepared = state.prepare_reload_refresh(_provider("candidate", "candidate-model"))
    intervening = _provider("intervening", "intervening-model")

    state.rebind_provider(
        intervening,
        provider_name="intervening",
        model_id="intervening-model",
        usage_accumulator=AgentUsageAccumulator(),
    )

    assert not state.reload_binding_matches_expected(prepared)
    if state.reload_binding_matches_expected(prepared):
        state.publish_reload_refresh(prepared)
    assert state.provider_binding == CodingProviderBinding(
        intervening, "intervening", "intervening-model"
    )


def test_reload_binding_expected_check_is_read_only_under_shared_lock() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/coding/state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    checker = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "reload_binding_matches_expected"
    )
    guards = [node for node in ast.walk(checker) if isinstance(node, ast.With)]
    assert len(guards) == 1
    assert ast.unparse(guards[0].items[0].context_expr) == "self._state_lock"
    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
        for node in ast.walk(checker)
    )


def test_reload_rebind_matches_live_transition_and_preserves_later_retained_state() -> (
    None
):
    message = _message("prior history")
    failure = AgentFailure("ProviderFailure", ProductContent("safe failure"))
    original = _provider()
    live = _state(provider=original, messages=(message,))
    detached = _state(provider=original, messages=(message,))
    replacement = _provider("fallback", "fallback-model")
    prepared = detached.prepare_reload_rebind(
        replacement,
        provider_name="fallback",
        model_id="fallback-model",
    )
    assert detached.reload_binding_matches_expected(prepared.binding)

    for owner in (live, detached):
        owner.apply_compaction(
            (message,),
            summary_suffix="\n\nsummary",
            dropped_group_count=2,
        )
        owner.record_provider_failure(failure)
    live.rebind_provider(
        replacement,
        provider_name="fallback",
        model_id="fallback-model",
        usage_accumulator=AgentUsageAccumulator(),
    )
    detached.publish_reload_rebind(
        binding=prepared.binding,
        history=prepared.history,
    )

    assert (
        detached.provider_binding,
        detached.messages,
        detached.compaction_suffix,
        detached.compaction_count,
        detached.compaction_dropped_group_count,
        detached.provider_failure,
    ) == (
        live.provider_binding,
        live.messages,
        live.compaction_suffix,
        live.compaction_count,
        live.compaction_dropped_group_count,
        live.provider_failure,
    )


def test_reload_usage_refresh_retains_exact_usage_and_checks_owner_freshness() -> None:
    accumulator = AgentUsageAccumulator(AgentTokenPricing(1.0, 2.0, 3.0))
    state = _state(accumulator=accumulator)
    state.absorb_usage(
        AgentProviderUsageSample(
            input_tokens=1_000_000,
            output_tokens=500_000,
            total_tokens=1_500_000,
        )
    )
    before = state.usage_snapshot()

    prepared = state.prepare_reload_usage_refresh()

    assert type(prepared) is AgentUsageRefreshValue
    assert state.reload_usage_matches_expected(prepared)
    state.publish_reload_usage_refresh(prepared)
    assert state.usage_snapshot() == before
    state.absorb_usage(AgentProviderUsageSample(input_tokens=3, total_tokens=3))
    assert state.reload_usage_matches_expected(prepared)
    later = state.usage_snapshot()
    state.publish_reload_usage_refresh(prepared)
    assert state.usage_snapshot() == later


def test_reload_usage_fallback_clears_usage_without_clearing_provider_failure() -> None:
    accumulator = AgentUsageAccumulator(AgentTokenPricing(1.0, 2.0, 3.0))
    state = _state(accumulator=accumulator)
    state.absorb_usage(
        AgentProviderUsageSample(
            input_tokens=1_000_000,
            output_tokens=500_000,
            total_tokens=1_500_000,
        )
    )
    failure = AgentFailure("ProviderFailure", ProductContent("safe failure"))
    state.record_provider_failure(failure)
    replacement_pricing = AgentTokenPricing(4.0, 5.0, 6.0)
    supplied_replacement = AgentUsageAccumulator(replacement_pricing)
    prepared = state.prepare_reload_usage_fallback(supplied_replacement)

    assert state.reload_usage_matches_expected(prepared)
    assert prepared.replacement is not supplied_replacement
    assert state.usage_snapshot().usage.cost_usd == 2.0
    supplied_replacement.absorb(
        AgentProviderUsageSample(input_tokens=1_000_000, total_tokens=1_000_000)
    )
    state.absorb_usage(AgentProviderUsageSample(output_tokens=2, total_tokens=2))
    assert state.reload_usage_matches_expected(prepared)
    state.publish_reload_usage_fallback(prepared)

    assert state._usage_accumulator is prepared.replacement
    assert state._usage_accumulator is not accumulator
    assert state.usage_snapshot() == CodingSessionUsageSnapshot(
        usage=AgentUsage(), last_total_tokens=0, cache_hit_percent=None
    )
    assert state.provider_failure is failure
    assert accumulator.agent_usage().output_tokens == 500_002
    state.absorb_usage(
        AgentProviderUsageSample(input_tokens=1_000_000, total_tokens=1_000_000)
    )
    assert state.usage.cost_usd == 4.0


def test_reload_usage_fallback_matches_live_pointer_replacement_semantics() -> None:
    pricing = AgentTokenPricing(2.0, 3.0, 4.0)
    live_old = AgentUsageAccumulator()
    prepared_old = AgentUsageAccumulator()
    sample = AgentProviderUsageSample(
        input_tokens=7,
        output_tokens=5,
        total_tokens=12,
    )
    live_old.absorb(sample)
    prepared_old.absorb(sample)
    live_state = _state(accumulator=live_old)
    prepared_state = _state(accumulator=prepared_old)
    live_value = live_state.prepare_reload_usage_fallback(
        AgentUsageAccumulator(pricing)
    )
    prepared_value = prepared_state.prepare_reload_usage_fallback(
        AgentUsageAccumulator(pricing)
    )

    live_state.rebind_provider(
        live_state.provider,
        provider_name="replacement",
        model_id="replacement-model",
        usage_accumulator=live_value.replacement,
    )
    prepared_state.publish_reload_usage_fallback(prepared_value)

    assert live_state._usage_accumulator is live_value.replacement
    assert prepared_state._usage_accumulator is prepared_value.replacement
    assert live_state.usage_snapshot() == prepared_state.usage_snapshot()
    assert live_old.agent_usage() == AgentUsage(input_tokens=7, output_tokens=5)
    assert prepared_old.agent_usage() == AgentUsage(input_tokens=7, output_tokens=5)
    live_state.absorb_usage(
        AgentProviderUsageSample(input_tokens=1_000_000, total_tokens=1_000_000)
    )
    prepared_state.absorb_usage(
        AgentProviderUsageSample(input_tokens=1_000_000, total_tokens=1_000_000)
    )
    assert live_state.usage_snapshot() == prepared_state.usage_snapshot()
    assert live_state.usage.cost_usd == 2.0


def test_reload_usage_preparation_does_not_mutate_live_owner_or_usage() -> None:
    accumulator = AgentUsageAccumulator()
    state = _state(accumulator=accumulator)
    state.absorb_usage(AgentProviderUsageSample(input_tokens=5, total_tokens=5))
    before = state.usage_snapshot()

    owned = state._usage_accumulator
    prepared = state.prepare_reload_usage_fallback(AgentUsageAccumulator())
    assert state._usage_accumulator is owned
    assert state.usage_snapshot() == before

    state.absorb_usage(AgentProviderUsageSample(output_tokens=2, total_tokens=2))
    assert state.reload_usage_matches_expected(prepared)
    assert state.usage_snapshot() != before
    assert accumulator.agent_usage() == state.usage
    assert state.provider_failure is None


def test_reload_usage_fallback_refuses_an_equal_binding_owner_swap() -> None:
    state = _state(accumulator=AgentUsageAccumulator())
    binding = state.prepare_reload_refresh(state.provider)
    prepared = state.prepare_reload_usage_fallback(AgentUsageAccumulator())

    state.rebind_provider(
        state.provider,
        provider_name=state.provider_name,
        model_id=state.model_id,
        usage_accumulator=AgentUsageAccumulator(),
    )

    assert state.reload_binding_matches_expected(binding)
    assert not state.reload_usage_matches_expected(prepared)


def test_coding_usage_owner_adapters_use_only_public_accumulator_methods() -> None:
    source = Path(__file__).parents[1] / "src/pipy_harness/native/coding/state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method_names = {
        "prepare_reload_usage_refresh",
        "prepare_reload_usage_fallback",
        "reload_usage_matches_expected",
        "publish_reload_usage_refresh",
        "publish_reload_usage_fallback",
    }
    methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in method_names
    ]
    assert {node.name for node in methods} == method_names
    for method in methods:
        assert not any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_usage_accumulator"
            and node.attr.startswith("_")
            for node in ast.walk(method)
        )
        guards = [node for node in ast.walk(method) if isinstance(node, ast.With)]
        assert len(guards) == 1
        assert ast.unparse(guards[0].items[0].context_expr) == "self._state_lock"
    fallback_publisher = next(
        method for method in methods if method.name == "publish_reload_usage_fallback"
    )
    assert [type(node) for node in fallback_publisher.body] == [ast.Expr, ast.With]
    guarded_body = cast(ast.With, fallback_publisher.body[1]).body
    assert [type(node) for node in guarded_body] == [ast.Assign]
    assignment = cast(ast.Assign, guarded_body[0])
    assert ast.unparse(assignment.targets[0]) == "self._usage_accumulator"
    assert ast.unparse(assignment.value) == "value.replacement"
    assert not any(isinstance(node, ast.Call) for node in ast.walk(fallback_publisher))


def test_refresh_and_unavailable_provider_transitions_retain_context() -> None:
    message = _message()
    state = _state(messages=(message,))
    state.absorb_usage(AgentProviderUsageSample(input_tokens=4, total_tokens=4))
    state.apply_compaction(
        (message,),
        summary_suffix="\n\nsummary",
        dropped_group_count=2,
    )
    refreshed = _provider("refreshed-name", "refreshed-model")

    state.refresh_provider(refreshed)

    after_refresh = state.result_snapshot()
    assert state.provider_binding.provider is refreshed
    assert after_refresh.provider_name == "explicit-provider"
    assert after_refresh.model_id == "explicit-model"
    assert after_refresh.messages == (message,)
    assert after_refresh.usage.input_tokens == 4
    assert after_refresh.compaction_suffix == "\n\nsummary"

    unavailable = _provider("unavailable-name", "unavailable-model")
    state.mark_provider_unavailable(unavailable)

    after_unavailable = state.result_snapshot()
    assert state.provider_binding.provider is unavailable
    assert after_unavailable.provider_name == "explicit-provider"
    assert after_unavailable.model_id == "explicit-model"
    assert after_unavailable.messages == (message,)
    assert after_unavailable.usage.input_tokens == 4
    assert after_unavailable.compaction_suffix == "\n\nsummary"


def test_rebind_clears_history_and_usage_but_preserves_compaction_suffix() -> None:
    message = _message()
    old_accumulator = AgentUsageAccumulator()
    state = _state(messages=(message,), accumulator=old_accumulator)
    state.absorb_usage(AgentProviderUsageSample(input_tokens=7, total_tokens=7))
    state.apply_compaction(
        (message,),
        summary_suffix="\n\nold summary",
        dropped_group_count=1,
    )
    replacement = _provider("replacement-port", "replacement-port-model")
    new_accumulator = AgentUsageAccumulator(AgentTokenPricing(1.0, 2.0, 3.0))

    state.rebind_provider(
        replacement,
        provider_name="selected-provider",
        model_id="selected-model",
        usage_accumulator=new_accumulator,
    )

    snapshot = state.result_snapshot()
    assert state.provider_binding == CodingProviderBinding(
        replacement,
        "selected-provider",
        "selected-model",
    )
    assert snapshot.provider_name == "selected-provider"
    assert snapshot.model_id == "selected-model"
    assert snapshot.messages == ()
    assert snapshot.usage == AgentUsage()
    assert snapshot.compaction_suffix == "\n\nold summary"
    assert snapshot.compaction_count == 1
    assert snapshot.compaction_dropped_group_count == 1


def test_begin_run_resets_run_state_and_retains_the_state_owned_provider() -> None:
    provider = _provider()
    state = _state(provider=provider, messages=(_message(),))
    state.record_input_accepted()
    state.record_resource_invocation()
    state.record_file_references(
        reference_count=1,
        loaded_count=1,
        failed_count=0,
    )
    state.apply_compaction(
        state.messages,
        summary_suffix="\n\nold summary",
        dropped_group_count=1,
    )
    state.record_provider_failure(
        AgentFailure("ProviderFailure", ProductContent("safe failure"))
    )
    state.absorb_usage(AgentProviderUsageSample(input_tokens=4, total_tokens=4))

    state.begin_run(
        provider_name="next-provider",
        model_id="next-model",
        usage_accumulator=AgentUsageAccumulator(),
    )

    snapshot = state.result_snapshot()
    assert state.provider is provider
    assert snapshot.provider_name == "next-provider"
    assert snapshot.model_id == "next-model"
    assert snapshot.messages == ()
    assert snapshot.usage == AgentUsage()
    assert snapshot.user_turn_count == 0
    assert snapshot.resource_invocation_count == 0
    assert snapshot.file_reference_count == 0
    assert snapshot.compaction_suffix == ""
    assert snapshot.compaction_count == 0
    assert snapshot.compaction_dropped_group_count == 0
    assert snapshot.provider_failure is None


def test_history_transitions_preserve_identity_and_rebuild_clears_only_suffix() -> None:
    first = _message("first")
    second = _message("second")
    third = _message("third")
    state = _state()

    state.append_message(first)
    assert state.messages[0] is first

    state.mirror_history((second,))
    assert state.messages == (second,)
    assert state.messages[0] is second

    state.apply_compaction(
        (second,),
        summary_suffix="\n\nsummary",
        dropped_group_count=3,
    )
    state.clear_history()
    assert state.messages == ()
    assert state.compaction_suffix == "\n\nsummary"

    state.rebuild_history((third,))
    snapshot = state.result_snapshot()
    assert snapshot.messages == (third,)
    assert snapshot.messages[0] is third
    assert snapshot.compaction_suffix == ""
    assert snapshot.compaction_count == 1
    assert snapshot.compaction_dropped_group_count == 3


def test_tool_policy_and_product_counters_are_projected_exactly() -> None:
    state = _state()
    state.sync_tool_policy(
        AgentToolPolicyState(
            tool_budget=10,
            invocations_this_turn=2,
            tool_invocation_count=5,
            malformed_argument_count=4,
            consecutive_malformed_streak=2,
            budget_exhausted_count=1,
        )
    )
    state.record_input_accepted()
    state.record_input_accepted()
    state.record_resource_invocation()
    state.record_file_references(
        reference_count=3,
        loaded_count=2,
        failed_count=1,
    )
    state.record_file_references(
        reference_count=2,
        loaded_count=1,
        failed_count=0,
    )
    state.record_image_attachments(
        attachment_count=3,
        loaded_count=1,
        failed_count=1,
    )

    snapshot = state.result_snapshot()

    assert snapshot.user_turn_count == 2
    assert snapshot.tool_invocation_count == 5
    assert snapshot.resource_invocation_count == 1
    assert snapshot.malformed_argument_count == 4
    assert snapshot.consecutive_malformed_streak == 2
    assert snapshot.budget_exhausted_count == 1
    assert snapshot.file_reference_count == 5
    assert snapshot.file_reference_loaded_count == 3
    assert snapshot.file_reference_failed_count == 1
    assert snapshot.image_attachment_count == 3
    assert snapshot.image_attachment_loaded_count == 1
    assert snapshot.image_attachment_failed_count == 1


@pytest.mark.parametrize(
    ("field_name", "corrupt_value", "expected"),
    [
        ("tool_budget", True, TypeError),
        ("tool_budget", -1, ValueError),
        ("tool_budget", 0, ValueError),
        ("tool_budget", 201, ValueError),
        ("malformed_limit", True, TypeError),
        ("malformed_limit", -1, ValueError),
        ("malformed_limit", 4, ValueError),
        ("invocations_this_turn", True, TypeError),
        ("invocations_this_turn", -1, ValueError),
        ("tool_invocation_count", True, TypeError),
        ("tool_invocation_count", -1, ValueError),
        ("malformed_argument_count", True, TypeError),
        ("malformed_argument_count", -1, ValueError),
        ("consecutive_malformed_streak", True, TypeError),
        ("consecutive_malformed_streak", -1, ValueError),
        ("budget_exhausted_count", True, TypeError),
        ("budget_exhausted_count", -1, ValueError),
    ],
)
def test_sync_tool_policy_rejects_corrupted_fields_without_mutation(
    field_name: str,
    corrupt_value: object,
    expected: type[Exception],
) -> None:
    state = _state(messages=(_message("prior"),))
    state.sync_tool_policy(
        AgentToolPolicyState(
            tool_budget=10,
            invocations_this_turn=2,
            tool_invocation_count=5,
            malformed_argument_count=4,
            consecutive_malformed_streak=2,
            budget_exhausted_count=1,
        )
    )
    state.record_provider_failure(
        AgentFailure("ExistingFailure", ProductContent("prior failure"), True)
    )
    state.record_input_accepted()
    state.absorb_usage(AgentProviderUsageSample(input_tokens=2, total_tokens=2))
    before_result = state.result_snapshot()
    before_usage = state.usage_snapshot()
    before_binding = state.provider_binding
    corrupted = AgentToolPolicyState(
        tool_budget=10,
        invocations_this_turn=2,
        tool_invocation_count=8,
        malformed_argument_count=3,
        consecutive_malformed_streak=1,
        budget_exhausted_count=2,
    )
    object.__setattr__(corrupted, field_name, corrupt_value)

    with pytest.raises(expected):
        state.sync_tool_policy(corrupted)

    assert state.result_snapshot() == before_result
    assert state.usage_snapshot() == before_usage
    assert state.provider_binding is before_binding
    assert state.provider_failure is before_result.provider_failure


@pytest.mark.parametrize(
    ("corrupted", "expected_match"),
    [
        (
            AgentToolPolicyState(
                tool_budget=10,
                invocations_this_turn=2,
                malformed_argument_count=3,
                consecutive_malformed_streak=1,
            ),
            "invocations_this_turn must not exceed state.tool_budget",
        ),
        (
            AgentToolPolicyState(
                tool_budget=10,
                malformed_argument_count=3,
                consecutive_malformed_streak=1,
            ),
            "consecutive_malformed_streak must not exceed",
        ),
    ],
)
def test_sync_tool_policy_rejects_corrupted_invariants_without_mutation(
    corrupted: AgentToolPolicyState,
    expected_match: str,
) -> None:
    state = _state(messages=(_message("prior"),))
    state.sync_tool_policy(
        AgentToolPolicyState(
            tool_budget=10,
            tool_invocation_count=5,
            malformed_argument_count=2,
            consecutive_malformed_streak=1,
            budget_exhausted_count=1,
        )
    )
    state.record_provider_failure(
        AgentFailure("ExistingFailure", ProductContent("prior failure"), True)
    )
    before_result = state.result_snapshot()
    before_usage = state.usage_snapshot()
    before_binding = state.provider_binding
    if expected_match.startswith("invocations"):
        object.__setattr__(corrupted, "tool_budget", 1)
    else:
        object.__setattr__(corrupted, "malformed_argument_count", 0)

    with pytest.raises(ValueError, match=expected_match):
        state.sync_tool_policy(corrupted)

    assert state.result_snapshot() == before_result
    assert state.usage_snapshot() == before_usage
    assert state.provider_binding is before_binding
    assert state.provider_failure is before_result.provider_failure


def test_usage_and_unresolved_provider_failure_are_typed_state() -> None:
    accumulator = AgentUsageAccumulator(
        AgentTokenPricing(
            input_per_million=1.0,
            output_per_million=2.0,
            reasoning_per_million=3.0,
        )
    )
    state = _state(accumulator=accumulator)
    state.absorb_usage(
        AgentProviderUsageSample(
            input_tokens=1_000_000,
            output_tokens=500_000,
            reasoning_tokens=100_000,
            total_tokens=1_600_000,
        )
    )
    failure = AgentFailure("ProviderFailure", ProductContent("safe failure"))

    state.record_provider_failure(failure)

    failed = state.result_snapshot()
    usage_snapshot = state.usage_snapshot()
    assert failed.usage == AgentUsage(
        input_tokens=1_000_000,
        output_tokens=500_000,
        reasoning_tokens=100_000,
        cost_usd=2.3,
    )
    assert usage_snapshot == CodingSessionUsageSnapshot(
        usage=failed.usage,
        last_total_tokens=1_600_000,
        cache_hit_percent=0.0,
    )
    assert failed.provider_failure is failure

    state.clear_provider_failure()
    assert state.provider_failure is None
    assert state.result_snapshot().provider_failure is None


@pytest.mark.parametrize(
    ("field_name", "corrupt_value", "expected"),
    [
        ("error_type", cast(str, object()), TypeError),
        ("error_type", "", ValueError),
        ("message", cast(ProductContent, object()), TypeError),
        ("message", _ProductContentSubclass("subclass"), TypeError),
        ("retryable", cast(bool, 1), TypeError),
    ],
)
def test_record_provider_failure_rejects_corrupted_fields_without_mutation(
    field_name: str,
    corrupt_value: object,
    expected: type[Exception],
) -> None:
    state = _state(messages=(_message("prior"),))
    prior_failure = AgentFailure(
        "ExistingFailure",
        ProductContent("prior failure"),
        True,
    )
    state.record_provider_failure(prior_failure)
    state.record_input_accepted()
    state.absorb_usage(AgentProviderUsageSample(input_tokens=2, total_tokens=2))
    before_result = state.result_snapshot()
    before_usage = state.usage_snapshot()
    before_binding = state.provider_binding
    corrupted = AgentFailure("IncomingFailure", ProductContent("incoming"))
    object.__setattr__(corrupted, field_name, corrupt_value)

    with pytest.raises(expected):
        state.record_provider_failure(corrupted)

    assert state.result_snapshot() == before_result
    assert state.usage_snapshot() == before_usage
    assert state.provider_binding is before_binding
    assert state.provider_failure is prior_failure


def test_record_provider_failure_rejects_corrupted_content_without_mutation() -> None:
    state = _state(messages=(_message("prior"),))
    prior_failure = AgentFailure(
        "ExistingFailure",
        ProductContent("prior failure"),
        True,
    )
    state.record_provider_failure(prior_failure)
    before_result = state.result_snapshot()
    before_usage = state.usage_snapshot()
    before_binding = state.provider_binding
    content = ProductContent("incoming")
    object.__setattr__(content, "value", cast(str, ["mutable"]))
    corrupted = AgentFailure("IncomingFailure", content)

    with pytest.raises(
        TypeError, match="failure.message.value must be an exact string"
    ):
        state.record_provider_failure(corrupted)

    assert state.result_snapshot() == before_result
    assert state.usage_snapshot() == before_usage
    assert state.provider_binding is before_binding
    assert state.provider_failure is prior_failure


@pytest.mark.parametrize(
    "field_name",
    [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    ],
)
def test_negative_usage_samples_are_rejected_before_state_mutation(
    field_name: str,
) -> None:
    state = _state()
    before_result = state.result_snapshot()
    before_usage = state.usage_snapshot()
    sample = AgentProviderUsageSample(**{field_name: -1})

    with pytest.raises(ValueError, match=f"sample.{field_name} must not be negative"):
        state.absorb_usage(sample)

    assert state.result_snapshot() == before_result
    assert state.usage_snapshot() == before_usage


@pytest.mark.parametrize(
    ("transition", "expected"),
    [
        (
            lambda state: state.record_file_references(
                reference_count=True,
                loaded_count=1,
                failed_count=0,
            ),
            TypeError,
        ),
        (
            lambda state: state.record_file_references(
                reference_count=-1,
                loaded_count=0,
                failed_count=0,
            ),
            ValueError,
        ),
        (
            lambda state: state.record_file_references(
                reference_count=1,
                loaded_count=1,
                failed_count=1,
            ),
            ValueError,
        ),
        (
            lambda state: state.record_image_attachments(
                attachment_count=True,
                loaded_count=1,
                failed_count=0,
            ),
            TypeError,
        ),
        (
            lambda state: state.record_image_attachments(
                attachment_count=-1,
                loaded_count=0,
                failed_count=0,
            ),
            ValueError,
        ),
        (
            lambda state: state.apply_compaction(
                (),
                summary_suffix="",
                dropped_group_count=1,
            ),
            ValueError,
        ),
        (
            lambda state: state.apply_compaction(
                (),
                summary_suffix="summary",
                dropped_group_count=True,
            ),
            TypeError,
        ),
        (
            lambda state: state.apply_compaction(
                (),
                summary_suffix="summary",
                dropped_group_count=0,
            ),
            ValueError,
        ),
    ],
)
def test_counter_transitions_reject_bool_negative_and_inconsistent_inputs(
    transition: Callable[[CodingSessionState], None],
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        transition(_state())


def test_state_rejects_mutable_and_subclass_contract_substitutions() -> None:
    message = _message()
    with pytest.raises(TypeError, match="messages must be an exact tuple"):
        _state(messages=cast(tuple[AgentMessage, ...], [message]))
    with pytest.raises(TypeError, match="exact canonical AgentMessage"):
        _state(
            messages=(cast(AgentMessage, _UserMessageSubclass(ProductContent("x"))),)
        )
    with pytest.raises(TypeError, match="must be an AgentUsageAccumulator"):
        CodingSessionState(
            provider=_provider(),
            provider_name="provider",
            model_id="model",
            usage_accumulator=cast(AgentUsageAccumulator, object()),
        )
    state = _state()
    with pytest.raises(TypeError, match="messages must be an exact tuple"):
        state.mirror_history(cast(tuple[AgentMessage, ...], [message]))
    with pytest.raises(TypeError, match="exact canonical AgentMessage"):
        state.append_message(
            cast(AgentMessage, _UserMessageSubclass(ProductContent("x")))
        )
    with pytest.raises(TypeError, match="exact AgentToolPolicyState"):
        state.sync_tool_policy(_ToolPolicyStateSubclass(tool_budget=5))
    with pytest.raises(TypeError, match="exact AgentProviderUsageSample"):
        state.absorb_usage(cast(AgentProviderUsageSample, object()))
    with pytest.raises(TypeError, match="exact AgentFailure"):
        state.record_provider_failure(cast(AgentFailure, object()))
    with pytest.raises(TypeError, match="implement ProviderPort"):
        state.refresh_provider(cast(ProviderPort, object()))


def test_result_snapshot_rejects_mutable_and_invalid_substitutions() -> None:
    snapshot = _state().result_snapshot()
    with pytest.raises(TypeError, match="messages must be an exact tuple"):
        replace(snapshot, messages=cast(tuple[AgentMessage, ...], []))
    with pytest.raises(TypeError, match="usage must be an exact AgentUsage"):
        replace(snapshot, usage=cast(AgentUsage, object()))
    with pytest.raises(TypeError, match="exact integer"):
        replace(snapshot, user_turn_count=True)
    with pytest.raises(ValueError, match="must not be negative"):
        replace(snapshot, resource_invocation_count=-1)
    with pytest.raises(ValueError, match="must not exceed"):
        replace(
            snapshot,
            malformed_argument_count=0,
            consecutive_malformed_streak=1,
        )
    with pytest.raises(TypeError, match="exact string"):
        replace(snapshot, provider_name=cast(str, object()))
    with pytest.raises(TypeError, match="exact AgentFailure"):
        replace(snapshot, provider_failure=cast(AgentFailure, object()))


def test_result_snapshot_constructor_is_recursively_validated() -> None:
    snapshot = CodingSessionResultSnapshot(
        provider_name="provider",
        model_id="model",
        messages=(),
        usage=AgentUsage(),
    )

    assert snapshot.provider_name == "provider"
    assert snapshot.model_id == "model"
    assert snapshot.messages == ()
    assert snapshot.usage == AgentUsage()


@pytest.mark.parametrize(
    "message",
    [
        AgentUserMessage(_ProductContentSubclass("user")),
        AgentAssistantMessage(
            ProductContent("assistant"),
            (
                _ToolCallSubclass(
                    "provider-call",
                    "read",
                    ProductContent("{}"),
                ),
            ),
        ),
        AgentAssistantMessage(
            ProductContent("assistant"),
            (
                AgentToolCall(
                    "provider-call",
                    "read",
                    _ProductContentSubclass("{}"),
                ),
            ),
        ),
        AgentToolResultMessage(
            "pipy-tool-request-1",
            "read",
            _ProductContentSubclass("result"),
            "provider-call",
        ),
    ],
)
def test_result_snapshot_rejects_nested_message_substitutions(
    message: AgentMessage,
) -> None:
    with pytest.raises(TypeError):
        CodingSessionResultSnapshot(
            provider_name="provider",
            model_id="model",
            messages=(message,),
            usage=AgentUsage(),
        )


def test_result_snapshot_rejects_nested_failure_content_substitution() -> None:
    failure = AgentFailure(
        "ProviderFailure",
        _ProductContentSubclass("failure"),
    )

    with pytest.raises(TypeError, match="exact ProductContent"):
        CodingSessionResultSnapshot(
            provider_name="provider",
            model_id="model",
            messages=(),
            usage=AgentUsage(),
            provider_failure=failure,
        )


def test_message_validation_preserves_family_first_failure_order() -> None:
    assistant_content = ProductContent("assistant")
    assistant = AgentAssistantMessage(assistant_content)
    object.__setattr__(assistant_content, "value", cast(str, ["mutable"]))
    object.__setattr__(
        assistant,
        "tool_calls",
        cast(tuple[AgentToolCall, ...], []),
    )
    with pytest.raises(
        TypeError, match=r"messages\[0\]\.content\.value must be an exact string"
    ):
        _state(messages=(assistant,))

    result = AgentToolResultMessage(
        "pipy-tool-request-1",
        "read",
        ProductContent("result"),
        "provider-call",
    )
    object.__setattr__(result, "tool_request_id", "provider-owned")
    object.__setattr__(result, "tool_name", "")
    with pytest.raises(
        ValueError, match=r"messages\[0\]\.tool_request_id must be pipy-owned"
    ):
        _state(messages=(result,))


def test_result_snapshot_rejects_mutable_nested_message_substitutions() -> None:
    assistant = AgentAssistantMessage(ProductContent("assistant"))
    object.__setattr__(
        assistant,
        "tool_calls",
        cast(tuple[AgentToolCall, ...], []),
    )
    with pytest.raises(TypeError, match="tool_calls must be an exact tuple"):
        CodingSessionResultSnapshot(
            provider_name="provider",
            model_id="model",
            messages=(assistant,),
            usage=AgentUsage(),
        )

    result = AgentToolResultMessage(
        "pipy-tool-request-1",
        "read",
        ProductContent("result"),
        "provider-call",
    )
    object.__setattr__(
        result,
        "added_tool_names",
        cast(tuple[str, ...], ["mutable"]),
    )
    with pytest.raises(TypeError, match="added_tool_names must be an exact tuple"):
        CodingSessionResultSnapshot(
            provider_name="provider",
            model_id="model",
            messages=(result,),
            usage=AgentUsage(),
        )


def test_snapshots_reject_corrupted_nested_content_and_usage_fields() -> None:
    content = ProductContent("user")
    object.__setattr__(content, "value", cast(str, ["mutable"]))
    with pytest.raises(TypeError, match="content.value must be an exact string"):
        CodingSessionResultSnapshot(
            provider_name="provider",
            model_id="model",
            messages=(AgentUserMessage(content),),
            usage=AgentUsage(),
        )

    usage = AgentUsage()
    object.__setattr__(usage, "input_tokens", -1)
    with pytest.raises(ValueError, match="usage.input_tokens must not be negative"):
        CodingSessionUsageSnapshot(
            usage=usage,
            last_total_tokens=0,
            cache_hit_percent=None,
        )


def test_provider_binding_and_usage_snapshot_reject_invalid_substitutions() -> None:
    provider = _provider()
    with pytest.raises(TypeError, match="implement ProviderPort"):
        CodingProviderBinding(cast(ProviderPort, object()), "provider", "model")
    with pytest.raises(ValueError, match="provider_name must not be empty"):
        CodingProviderBinding(provider, "", "model")
    with pytest.raises(ValueError, match="model_id must not be empty"):
        CodingProviderBinding(provider, "provider", "")

    usage_snapshot = _state().usage_snapshot()
    with pytest.raises(TypeError, match="usage must be an exact AgentUsage"):
        replace(usage_snapshot, usage=cast(AgentUsage, object()))
    with pytest.raises(TypeError, match="last_total_tokens must be an exact integer"):
        replace(usage_snapshot, last_total_tokens=True)
    with pytest.raises(ValueError, match="must not be negative"):
        replace(usage_snapshot, last_total_tokens=-1)
    with pytest.raises(TypeError, match="exact float or None"):
        replace(usage_snapshot, cache_hit_percent=cast(float, 1))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        replace(usage_snapshot, cache_hit_percent=float("nan"))


def test_coding_state_shares_the_session_mutex_when_bound() -> None:
    """Two locks would not serialize a worker rebind against the session."""

    session_lock = threading.RLock()
    state = _state()
    private = state._state_lock
    assert private is not session_lock

    state.bind_state_lock(session_lock)
    assert state._state_lock is session_lock
    state.bind_state_lock(session_lock)
    assert state._state_lock is session_lock


def _blocks_while_lock_held(
    lock: "threading.RLock", operation: "Callable[[], object]"
) -> bool:
    """Whether ``operation`` waits for ``lock`` instead of running through it.

    Deterministic rather than probabilistic: rather than racing threads and
    hoping to observe a torn read, this holds the mutex and asserts the
    operation cannot make progress. An operation that skipped the lock would
    finish immediately and the helper returns False.
    """

    started = threading.Event()
    finished = threading.Event()

    def _run() -> None:
        started.set()
        operation()
        finished.set()

    worker = threading.Thread(target=_run, daemon=True)
    with lock:
        worker.start()
        assert started.wait(timeout=5)
        blocked = not finished.wait(timeout=0.2)
    worker.join(timeout=5)
    assert finished.wait(timeout=5), "operation never completed after release"
    return blocked


def test_provider_rebind_waits_for_the_shared_mutex() -> None:
    """The straggler-reachable write participates in the boundary."""

    lock = threading.RLock()
    state = _state(state_lock=lock)

    def _rebind() -> None:
        state.rebind_provider(
            _provider("late", "late-model"),
            provider_name="late",
            model_id="late-model",
            usage_accumulator=AgentUsageAccumulator(),
        )

    assert _blocks_while_lock_held(lock, _rebind) is True
    assert state.provider_name == "late"


def test_result_and_usage_snapshots_wait_for_the_shared_mutex() -> None:
    """Readers take the boundary too; a one-sided lock excludes nobody."""

    lock = threading.RLock()
    state = _state(state_lock=lock)

    assert _blocks_while_lock_held(lock, lambda: state.result_snapshot()) is True
    assert _blocks_while_lock_held(lock, lambda: state.usage_snapshot()) is True
    assert _blocks_while_lock_held(lock, lambda: state.messages) is True


def test_history_append_waits_for_the_shared_mutex() -> None:
    lock = threading.RLock()
    state = _state(state_lock=lock)

    assert (
        _blocks_while_lock_held(lock, lambda: state.append_message(_message("x")))
        is True
    )
    assert [message.content.value for message in state.messages] == ["x"]


def test_compaction_metadata_readers_wait_for_the_shared_mutex() -> None:
    """Compaction counters are part of the guarded group, so readers join it."""

    lock = threading.RLock()
    state = _state(state_lock=lock)

    assert _blocks_while_lock_held(lock, lambda: state.compaction_count) is True
    assert (
        _blocks_while_lock_held(lock, lambda: state.compaction_dropped_group_count)
        is True
    )
    assert _blocks_while_lock_held(lock, lambda: state.compaction_suffix) is True
