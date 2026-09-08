"""Semantic summaries use canonical execution and guarded product publication."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from test_native_coding_effects import _provider_mutation_fixture

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.product_session import (
    CodingProductSessionCallbacks,
    CodingProductSessionCompaction,
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
)
from pipy_harness.native.coding.state import CodingContextChangedError
from pipy_harness.native.extension_types import (
    ExtensionModelRuntimeControl,
    SessionDecision,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.repl.extension_operations import SessionExtensionOperations
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager


def _result(
    text: str = "Keep goal: repair parser; verified test passed; next: docs.",
    **kwargs: Any,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=kwargs.pop("status", HarnessStatus.SUCCEEDED),
        provider_name="fake",
        model_id="summary-model",
        started_at=now,
        ended_at=now,
        final_text=text,
        **kwargs,
    )


class _SummaryProvider:
    name = "fake"
    model_id = "summary-model"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.probe_failures: list[str] = []
        self.on_complete: Callable[[ProviderRequest], ProviderResult] = (
            lambda _request: _result()
        )
        self.effects: ProviderMutationEffects | None = None

    def complete(self, request: ProviderRequest, **kwargs: Any) -> ProviderResult:
        assert self.effects is not None
        probes = {
            "text sink disabled": kwargs.get("stream_sink") is None,
            "reasoning sink disabled": kwargs.get("reasoning_sink") is None,
            "outer lock released": not cast(
                Any, self.effects.mutation_io_lock
            )._is_owned(),
            "session lock released": not cast(
                Any, self.effects.coding_state.state_lock
            )._is_owned(),
        }
        self.probe_failures.extend(
            name for name, passed in probes.items() if not passed
        )
        assert not self.probe_failures
        self.requests.append(request)
        return self.on_complete(request)


def _fixture(
    tmp_path: Path, *, previous_summary: str = ""
) -> tuple[ProviderMutationEffects, _SummaryProvider]:
    original, _state, _tools, ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(tmp_path, persist_tree=True)
    )
    provider = _SummaryProvider()
    state = original.coding_state
    state.rebind_provider(
        provider,
        provider_name=provider.name,
        model_id=provider.model_id,
        usage_accumulator=AgentUsageAccumulator(),
    )
    call = AgentToolCall(
        provider_correlation_id="check-parser",
        tool_name="read",
        arguments_json=ProductContent('{"path":"parser.py"}'),
    )
    messages: tuple[AgentMessage, ...] = (
        AgentUserMessage(ProductContent("Goal: repair parser; keep the public API.")),
        AgentAssistantMessage(ProductContent("Inspect parser.py"), tool_calls=(call,)),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-parser",
            provider_correlation_id="check-parser",
            tool_name="read",
            content=ProductContent("Verified parser fixture."),
            is_error=False,
        ),
        AgentUserMessage(ProductContent("Retained: update docs.")),
        AgentAssistantMessage(ProductContent("Pending documentation.")),
        AgentUserMessage(ProductContent("Accepted newest group.")),
    )
    for message in messages:
        tree.append_message(message)

    def load() -> CodingProductSessionContext:
        projection = tree.build_coding_context()
        return CodingProductSessionContext(
            projection.messages,
            prior_summary=ProductContent(previous_summary)
            if previous_summary
            else ProductContent(projection.prior_summary)
            if projection.prior_summary is not None
            else None,
            entry_ids=projection.entry_ids,
        )

    def append(message: AgentMessage) -> None:
        tree.append_message(message)

    def persist(action: CodingProductSessionCompaction) -> None:
        assert cast(Any, original.mutation_io_lock)._is_owned()
        assert not cast(Any, state.state_lock)._is_owned()
        assert action.first_kept_entry_id is not None
        tree.append_compaction(
            summary=action.durable_summary.value,
            first_kept_entry_id=action.first_kept_entry_id,
            tokens_before=action.measure_before,
        )

    product = CodingProductSessionCoordinator(
        state=state, port=CodingProductSessionCallbacks(load, append, persist)
    )
    product.rebuild_active_history()
    extension = SessionExtensionOperations(
        generation_ref=ref,
        cwd=str(tmp_path),
        has_ui=False,
        notify_sink=None,
        ui_driver=None,
        project_trusted=True,
        model_runtime_factory=lambda _generation, _allow: (
            ExtensionModelRuntimeControl()
        ),
    )
    effects = replace(
        original,
        settings=SettingsManager(
            global_path=tmp_path / "settings.json", state_lock=state.state_lock, env={}
        ),
        product_session=product,
        extension_operations=extension,
        provider_turn_executor=ProviderTurnExecutor(cancel_join_timeout_seconds=0.01),
    )
    provider.effects = effects
    return effects, provider


def _published(effects: ProviderMutationEffects) -> tuple[object, ...]:
    tree = effects.ctl.session_tree
    assert tree.path is not None
    return (
        effects.coding_state.result_snapshot(),
        tree.get_entries(),
        tree.path.read_bytes(),
    )


def test_summary_combines_prior_and_exact_dropped_prefix_then_reopens(
    tmp_path: Path,
) -> None:
    effects, provider = _fixture(
        tmp_path, previous_summary="Prior decision: preserve compatibility."
    )
    before = effects.coding_state.messages
    lease_available = threading.Event()

    def summarize(request: ProviderRequest) -> ProviderResult:
        def claim() -> None:
            with effects.ctl.coding_effects.effect() as admitted:
                assert admitted
                lease_available.set()

        worker = threading.Thread(target=claim)
        worker.start()
        worker.join(1)
        assert not worker.is_alive() and lease_available.is_set()
        assert (
            request.messages[0].content.value
            == "Previous context summary:\nPrior decision: preserve compatibility."
        )
        assert all(
            actual is expected
            for actual, expected in zip(request.messages[1:-1], before[:3], strict=True)
        )
        assert (
            request.messages[-1].content.value
            == "Provide the combined context summary now."
        )
        assert request.available_tools == () and request.attachments == ()
        return _result(
            "Preserve compatibility; repaired parser; verified fixture; finish docs."
        )

    provider.on_complete = summarize
    assert "compacted conversation" in effects.apply_compaction("manual")
    assert effects.coding_state.messages == before[3:]
    assert all(
        actual is expected
        for actual, expected in zip(
            effects.coding_state.messages, before[3:], strict=True
        )
    )
    assert effects.coding_state.compaction_count == 1
    tree = effects.ctl.session_tree
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path).build_coding_context()
    assert reopened.messages == effects.coding_state.messages
    assert reopened.prior_summary is not None
    assert effects.coding_state.compaction_suffix == "\n\n" + reopened.prior_summary


@pytest.mark.parametrize("failure", ["exception", "failed", "empty", "tool", "cancel"])
def test_failed_summary_publishes_nothing_and_diagnostics_are_content_free(
    tmp_path: Path, failure: str
) -> None:
    effects, provider = _fixture(tmp_path)
    before = _published(effects)
    secret = "PRIVATE_SUMMARY_AND_PROVIDER_ERROR"

    def complete(_request: ProviderRequest) -> ProviderResult:
        if failure == "exception":
            raise RuntimeError(secret)
        if failure == "cancel":
            raise ProviderCancelledError()
        if failure == "failed":
            return _result(
                secret,
                status=HarnessStatus.FAILED,
                error_type=secret,
                error_message=secret,
            )
        if failure == "tool":
            return _result(
                secret,
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="forbidden",
                        tool_name="read",
                        arguments_json="{}",
                    ),
                ),
            )
        return _result(" \n")

    provider.on_complete = complete
    outcome = effects.compact_context("manual")
    # An assertion inside complete() is caught by the auxiliary failure path.
    # Check recorded probes here so failure cases cannot hide an invariant breach.
    assert not provider.probe_failures
    assert secret not in outcome.notice
    assert _published(effects) == before
    assert len(provider.requests) == 1
    assert (outcome.cancellation_reason is not None) == (failure == "cancel")


def test_extension_veto_preserves_reason_and_invokes_no_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, provider = _fixture(tmp_path)
    before = _published(effects)
    monkeypatch.setattr(
        SessionExtensionOperations,
        "session_allows",
        lambda *_args, **_kwargs: SessionDecision(
            allow=False, reason="keep full context"
        ),
    )
    assert (
        effects.apply_compaction("manual")
        == "pipy: compact blocked by extension: keep full context"
    )
    assert provider.requests == [] and _published(effects) == before


def _mutate(effects: ProviderMutationEffects, name: str) -> None:
    state = effects.coding_state
    if name == "append":
        state.append_message(AgentAssistantMessage(ProductContent("concurrent")))
    elif name == "mirror":
        state.mirror_history(state.messages)
    elif name == "clear":
        state.clear_history()
    elif name == "rebuild":
        state.rebuild_history(state.messages, summary_suffix=state.compaction_suffix)
    elif name == "compaction":
        state.apply_compaction(
            state.messages[1:],
            summary_suffix="Concurrent summary",
            dropped_group_count=1,
            dropped_message_count=1,
        )
    else:
        _mutate_binding(effects, name)


def _mutate_binding(effects: ProviderMutationEffects, name: str) -> None:
    state = effects.coding_state
    if name == "refresh":
        state.refresh_provider(state.provider)
    elif name == "begin":
        state.begin_run(
            provider_name=state.provider_name,
            model_id=state.model_id,
            usage_accumulator=AgentUsageAccumulator(),
        )
    elif name == "unavailable":
        state.mark_provider_unavailable(state.provider)
    elif name == "rebind":
        state.rebind_provider(
            state.provider,
            provider_name=state.provider_name,
            model_id=state.model_id,
            usage_accumulator=AgentUsageAccumulator(),
        )
    elif name == "model":
        state.publish_model_mutation(
            state.prepare_model_mutation(
                state.provider,
                expected_binding=state.provider_binding,
                provider_name=state.provider_name,
                model_id=state.model_id,
                usage_accumulator=AgentUsageAccumulator(),
            )
        )
    elif name == "reload":
        prepared = state.prepare_reload_rebind(
            state.provider, provider_name=state.provider_name, model_id=state.model_id
        )
        state.publish_reload_rebind(binding=prepared.binding, history=prepared.history)
    elif name == "reload-refresh":
        state.publish_reload_refresh(state.prepare_reload_refresh(state.provider))
    else:
        _mutate_tree_or_generation(effects, name)


def _mutate_tree_or_generation(effects: ProviderMutationEffects, name: str) -> None:
    tree = effects.ctl.session_tree
    if name == "load":
        tree._load_entries(())
    elif name == "tree-append":
        tree.append_custom("concurrent", {"value": 1})
    elif name == "branch":
        assert tree.leaf_id is not None
        tree.branch(tree.leaf_id)
    elif name == "set-leaf":
        tree.set_leaf(tree.leaf_id)
    elif name == "reset-restore":
        leaf = tree.leaf_id
        tree.reset_leaf()
        tree.set_leaf(leaf)
    elif name == "branch-summary":
        tree.branch_with_summary(tree.leaf_id, "concurrent branch")
    elif name == "pointer-aba":
        effects.ctl.session_tree = NativeSessionTree.create(effects.cwd, persist=False)
        effects.ctl.session_tree = tree
    else:
        _mutate_generation(effects, name)


def _mutate_generation(effects: ProviderMutationEffects, name: str) -> None:
    ref = effects.ctl.generation_ref
    if name == "generation":
        ref.publish(ref.current)
    elif name == "refused-window":
        with ref.publishing():
            pass
    elif name == "terminal":
        with effects.ctl.coding_effects.terminal_section():
            pass
    else:
        raise AssertionError(name)


@pytest.mark.parametrize("trigger", ["manual", "auto"])
@pytest.mark.parametrize(
    "mutation",
    [
        "append",
        "mirror",
        "clear",
        "rebuild",
        "compaction",
        "refresh",
        "begin",
        "unavailable",
        "rebind",
        "model",
        "reload",
        "reload-refresh",
        "load",
        "tree-append",
        "branch",
        "set-leaf",
        "reset-restore",
        "branch-summary",
        "pointer-aba",
        "generation",
        "refused-window",
        "terminal",
    ],
)
def test_every_snapshot_writer_invalidates_semantic_work(
    tmp_path: Path, trigger: str, mutation: str
) -> None:
    effects, provider = _fixture(tmp_path)
    after_mutation: list[tuple[object, ...]] = []

    def complete(_request: ProviderRequest) -> ProviderResult:
        _mutate(effects, mutation)
        after_mutation.append(_published(effects))
        return _result()

    provider.on_complete = complete
    if trigger == "auto":
        with pytest.raises(CodingContextChangedError):
            effects.compact_context(trigger)
    else:
        assert (
            effects.compact_context(trigger).notice
            == "pipy: compact refused: context changed."
        )
    assert _published(effects) == after_mutation[0]


def test_header_capture_mutation_refuses_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, provider = _fixture(tmp_path)
    after_mutation: list[tuple[object, ...]] = []

    def headers(*_args: object) -> None:
        assert not cast(Any, effects.mutation_io_lock)._is_owned()
        assert not cast(Any, effects.coding_state.state_lock)._is_owned()
        effects.coding_state.mirror_history(effects.coding_state.messages)
        after_mutation.append(_published(effects))

    monkeypatch.setattr(SessionExtensionOperations, "provider_header_callback", headers)
    assert (
        effects.compact_context("manual").notice
        == "pipy: compact refused: context changed."
    )
    assert provider.requests == [] and _published(effects) == after_mutation[0]


@pytest.mark.parametrize("mutate", [False, True])
def test_late_cancelled_summary_cannot_publish_and_staleness_wins(
    tmp_path: Path, mutate: bool
) -> None:
    effects, provider = _fixture(tmp_path)
    abort = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    effects = replace(effects, abort_event=abort)
    provider.effects = effects
    expected: list[tuple[object, ...]] = []

    def complete(_request: ProviderRequest) -> ProviderResult:
        if mutate:
            _mutate(effects, "refused-window")
        expected.append(_published(effects))
        abort.set()
        assert release.wait(2)
        finished.set()
        return _result("LATE PRIVATE SUMMARY")

    provider.on_complete = complete
    try:
        if mutate:
            with pytest.raises(CodingContextChangedError):
                effects.compact_context("auto")
        else:
            outcome = effects.compact_context("manual")
            assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
        assert _published(effects) == expected[0]
    finally:
        release.set()
        assert finished.wait(2)
    assert _published(effects) == expected[0]


def _run_retained_control(operation: Callable[[], object]) -> None:
    failures: list[Exception] = []

    def invoke() -> None:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - propagate retained worker failures
            failures.append(exc)

    writer = threading.Thread(target=invoke)
    writer.start()
    writer.join(timeout=2)
    assert not writer.is_alive() and failures == []


@pytest.mark.parametrize(
    "outcome_kind", ["cancel", "stale-tree", "session-name", "entry-label"]
)
def test_automatic_summary_stops_before_ordinary_request_hooks_and_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome_kind: str
) -> None:
    import io

    from pipy_harness.native.agent.events import (
        AgentEvent,
        MessageCompleted,
        RunCancelled,
    )
    from pipy_harness.native.agent_loop_policy import NativeAgentProviderRequestPolicy
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
    from pipy_harness.native.repl import loop_step, wiring
    from pipy_harness.native.repl.collaborators import SessionCollaborators
    from pipy_harness.native.tool_renderers import _ToolLoopRenderer

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    effects, provider = _fixture(tmp_path)
    tree = effects.ctl.session_tree
    before = tree.get_entries()
    events: list[AgentEvent] = []
    collaborators: list[SessionCollaborators] = []
    mutation_entries: list[object] = []

    def compose(**kwargs: Any) -> SessionCollaborators:
        owner = SessionCollaborators(**kwargs)
        collaborators.append(owner)
        return owner

    monkeypatch.setattr(wiring, "SessionCollaborators", compose)

    class Sink:
        def emit(self, event: AgentEvent) -> None:
            events.append(event)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError(
            "ordinary request preparation ran after summary cancellation"
        )

    monkeypatch.setattr(
        loop_step, "should_compact_agent_history", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        loop_step._RequestPreparationEffects, "_provider_request", forbidden
    )
    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", forbidden)
    monkeypatch.setattr(_ToolLoopRenderer, "refresh_tool_renderers", forbidden)

    def complete(_request: ProviderRequest) -> ProviderResult:
        if outcome_kind == "stale-tree":
            tree.set_leaf(tree.leaf_id)
        elif outcome_kind in {"session-name", "entry-label"}:
            retained = collaborators[0]
            assert tree.leaf_id is not None
            operations = {
                "session-name": lambda: retained.extension_set_session_name(
                    "retained session name"
                ),
                "entry-label": lambda: retained.extension_set_label(
                    tree.leaf_id, "retained label"
                ),
            }
            _run_retained_control(operations[outcome_kind])
            mutation_entries.extend(tree.get_entries())
            assert len(mutation_entries) == len(before) + 1
            return _result()
        raise ProviderCancelledError()

    provider.on_complete = complete
    session = CodingSession(
        provider=provider, native_session=tree, agent_event_sink=Sink()
    )

    def run() -> Any:
        return session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("new accepted input\n/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    if outcome_kind != "cancel":
        with pytest.raises(CodingContextChangedError):
            run()
        assert not any(isinstance(event, RunCancelled) for event in events)
        assert tree.get_entries() == (mutation_entries or before)
        assert not any(isinstance(event, MessageCompleted) for event in events)
        retained = collaborators[0]
        assert retained.coding_effects.terminal
        with pytest.raises(ExtensionCapabilityError, match="closed"):
            retained.extension_set_session_name("must not publish after close")
        assert tree.get_entries() == (mutation_entries or before)
        witness = session._coding_state.begin_agent_run()
        session._coding_state.end_agent_run(witness)
    else:
        assert run().status is HarnessStatus.SUCCEEDED
        assert sum(isinstance(event, RunCancelled) for event in events) == 1
        accepted = [
            event.message
            for event in events
            if isinstance(event, MessageCompleted)
            and isinstance(event.message, AgentUserMessage)
        ]
        assert len(accepted) == 1 and accepted[0].content.value == "new accepted input"
        assert (
            sum(message is accepted[0] for message in session._coding_state.messages)
            == 1
        )
    assert len(provider.requests) == 1
    assert session._coding_state.compaction_count == 0
    assert session._coding_state.compaction_suffix == ""


def test_tree_pointer_and_window_epoch_readers_take_their_owner_guards(
    tmp_path: Path,
) -> None:
    from test_native_coding_state import _blocks_while_lock_held

    effects, _provider = _fixture(tmp_path)
    assert _blocks_while_lock_held(
        effects.mutation_io_lock, lambda: effects.ctl.session_tree.mutation_epoch
    )
    assert _blocks_while_lock_held(
        effects.mutation_io_lock, lambda: effects.ctl.tree_pointer_epoch
    )
    assert _blocks_while_lock_held(
        effects.coding_state.state_lock,
        lambda: effects.ctl.generation_ref.publication_epoch,
    )


@pytest.mark.parametrize("failure", ["load-iterator", "append-write", "branch-id"])
def test_tree_epoch_invalidates_before_fallible_mutation_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    effects, provider = _fixture(tmp_path)
    tree = effects.ctl.session_tree
    before_epoch = tree.mutation_epoch
    after: list[tuple[object, ...]] = []

    def fail(*_args: object) -> Any:
        raise RuntimeError("tree fixture failure")

    def complete(_request: ProviderRequest) -> ProviderResult:
        with pytest.raises(RuntimeError, match="tree fixture failure"):
            if failure == "load-iterator":

                def entries():
                    yield from ()
                    fail()

                tree._load_entries(entries())
            elif failure == "append-write":
                monkeypatch.setattr(tree, "_write_entry", fail)
                tree.append_custom("fixture", {})
            else:
                monkeypatch.setattr(tree, "_next_id", fail)
                tree.branch_with_summary(tree.leaf_id, "summary")
        assert tree.mutation_epoch > before_epoch
        after.append(_published(effects))
        return _result()

    provider.on_complete = complete
    assert (
        effects.compact_context("manual").notice
        == "pipy: compact refused: context changed."
    )
    assert _published(effects) == after[0]


def test_second_semantic_cut_combines_previous_summary_after_one_new_group(
    tmp_path: Path,
) -> None:
    effects, provider = _fixture(tmp_path)
    first_summary = "Goal and constraints preserved; parser verified."
    provider.on_complete = lambda _request: _result(first_summary)
    effects.apply_compaction("manual")
    retained = effects.coding_state.messages
    message = AgentUserMessage(ProductContent("One new group."))
    effects.product_session.append_message(message)
    final_summary = first_summary + " Documentation remains unfinished."
    provider.on_complete = lambda _request: _result(final_summary)
    effects.apply_compaction("manual")
    second_request = provider.requests[1]
    assert (
        second_request.messages[0].content.value
        == "Previous context summary:\n" + first_summary
    )
    assert second_request.messages[1:-1] == retained[:2]
    assert effects.coding_state.compaction_suffix == "\n\n" + final_summary
    assert effects.coding_state.compaction_count == 2
    assert effects.coding_state.messages == (retained[-1], message)
    path = effects.ctl.session_tree.path
    assert path is not None
    reopened = NativeSessionTree.open(path).build_coding_context()
    assert reopened.messages == effects.coding_state.messages
    assert reopened.prior_summary == final_summary


def test_branch_summary_keeps_captured_provider_and_labels_across_header_rebind(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from pipy_harness.native.repl.collaborators import SessionCollaborators

    effects, captured_provider = _fixture(tmp_path)
    binding = effects.coding_state.provider_binding
    replacement = _SummaryProvider()
    replacement.effects = effects
    messages: list[AgentMessage] = [
        AgentUserMessage(ProductContent("abandoned branch fact"))
    ]
    headers = []

    def acquire_header() -> None:
        effects.coding_state.rebind_provider(
            replacement,
            provider_name="replacement",
            model_id="new-model",
            usage_accumulator=AgentUsageAccumulator(),
        )
        headers.append("acquired")
        return None

    collaborators = cast(
        SessionCollaborators,
        SimpleNamespace(
            coding_state=effects.coding_state,
            cwd=tmp_path,
            active_provider_header_callback=acquire_header,
        ),
    )
    assert (
        SessionCollaborators.summarize_branch(
            collaborators, messages, "unfinished work"
        )
        == "Keep goal: repair parser; verified test passed; next: docs."
    )
    assert headers == ["acquired"]
    assert effects.coding_state.provider_binding.provider is replacement
    assert replacement.requests == []
    assert len(captured_provider.requests) == 1
    request = captured_provider.requests[0]
    assert (request.provider_name, request.model_id) == (
        binding.provider_name,
        binding.model_id,
    )
    assert request.messages == tuple(messages)
    assert request.system_prompt == (
        "Summarize the following abandoned conversation branch "
        "concisely so it can be referenced later. Focus on: unfinished work."
    )
    assert request.user_prompt == "Provide the branch summary now."
    assert request.available_tools == ()


@pytest.mark.parametrize(
    "completion",
    [
        "success",
        "failure",
        "stale",
        "provider-cancelled",
        "operator-abort",
        "steering",
        "local-command",
        "persistence-error",
    ],
)
def test_manual_compaction_wrapper_settles_returned_input_outside_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completion: str
) -> None:
    from types import SimpleNamespace

    from pipy_harness.native.coding.compaction import CodingCompactionOutcome
    from pipy_harness.native.tui import TerminalUi

    effects, _provider = _fixture(tmp_path)
    settlements: list[str] = []

    def settle(action: str) -> None:
        assert not cast(Any, effects.mutation_io_lock)._is_owned()
        assert not cast(Any, effects.coding_state.state_lock)._is_owned()
        settlements.append(action)

    pending = SimpleNamespace(
        restore_pending_to_editor=lambda: settle("restore"),
        promote_pending_to_drain=lambda: settle("promote"),
    )
    effects = replace(
        effects,
        terminal_ui=cast(
            TerminalUi,
            SimpleNamespace(
                components=SimpleNamespace(pending_messages=pending),
            ),
        ),
    )
    reason = {
        "provider-cancelled": AgentCancellationReason.PROVIDER_CANCELLED,
        "operator-abort": AgentCancellationReason.OPERATOR_ABORT,
        "steering": AgentCancellationReason.STEERING,
        "local-command": AgentCancellationReason.LOCAL_COMMAND,
    }.get(completion)
    outcome = CodingCompactionOutcome(completion, reason)

    def compact(
        _owner: ProviderMutationEffects, trigger: str
    ) -> CodingCompactionOutcome:
        assert trigger == "manual"
        if completion == "persistence-error":
            raise OSError("persistence failed after acceptance")
        return outcome

    monkeypatch.setattr(ProviderMutationEffects, "compact_context", compact)
    if completion == "persistence-error":
        with pytest.raises(OSError, match="persistence failed"):
            effects.apply_compaction("manual")
        assert settlements == []
    else:
        assert effects.apply_compaction("manual") == completion
        assert settlements == [
            "restore" if completion == "operator-abort" else "promote"
        ]
