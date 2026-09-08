"""Provider, model and auth mutation: one ordered commit path for a live turn.

Pi's model runtime. Everything that swaps what the session is talking to --
`/model`, `/scoped-models`, an auth change, a thinking-level cycle, a compaction,
an extension's `setActiveTools` -- goes through this one owner, and each of
those does the same three things in the same order: decide under the state lock,
commit, then run the follow-on I/O outside it.

That ordering is the reason this is a class and not a pile of functions. The
`mutation_io_lock` serializes a mutation's decision against its own persistence
and rendering, while the session state lock guards the decision itself. They are
always taken in that order -- `mutation_io_lock` first, never the reverse -- so a
worker thread setting active tools and a `/reload` publishing a generation cannot
deadlock against each other.

`provider_state` arrives as a value. It is read eight times and assigned zero,
here and everywhere else, so this owner reads what the composition root bound
rather than reaching through the session for it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.native.agent import ProductContent
from pipy_harness.native.agent.history import (
    AgentHistoryCompaction,
    compact_agent_history,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnDeltaPolicy,
    ProviderTurnExecutor,
    _AbortCallbackSignal,
)
from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.compaction import (
    CodingCompactionOutcome,
    PrivateSummaryEvents,
    compaction_request,
    summary_text,
)
from pipy_harness.native.coding.product_session import (
    CodingProductSessionCompaction,
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
)
from pipy_harness.native.coding.request_budget import (
    RequestBudget,
    estimate_request,
    resolve_request_budget,
)
from pipy_harness.native.coding.state import (
    CodingCompactionSnapshot,
    CodingContextChangedError,
    CodingModelMutation,
    CodingProviderBinding,
    CodingSessionState,
)
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_types import ExtensionModelRuntimeControl
from pipy_harness.native.repl.extension_operations import SessionExtensionOperations
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.turn_leaves import (
    AGENT_HISTORY_KEEP_RECENT_GROUPS,
    pricing_for,
    provider_turn_inputs,
)
from pipy_harness.native.repl_state import (
    NativeModelMutationState,
    NativeModelSelection,
    NativeReplProviderState,
    PreparedNativeModelMutation,
    StaticNativeReplProviderState,
    UnavailableAfterReloadProvider,
    normalize_repl_fake_selection,
)
from pipy_harness.native.session_generation import SessionGenerationSnapshot
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import NativeToolCapabilities
from pipy_harness.native.tui import TerminalUi


def _report_default_persistence(
    state: "NativeReplProviderState",
) -> str | None:
    """Drain a queued default after its selection is live.

    Returns the diagnostic so a caller can surface it. Persistence is
    irreversible, so it deliberately runs only once the semantic rebind has
    completed; a failure leaves the live selection untouched and says so.
    """

    return state.flush_pending_default()


def _deny_model_mutation(_generation_id: int, _reference: str) -> bool:
    """Refuse a generation-bound mid-turn model switch."""

    return False


@dataclass(frozen=True, slots=True)
class _PreparedModelMutation:
    provider_state: NativeReplProviderState
    selection: PreparedNativeModelMutation
    coding: CodingModelMutation | None


@dataclass(frozen=True, slots=True)
class _CompactionWork:
    context: CodingCompactionSnapshot
    cut: AgentHistoryCompaction
    first_kept_entry_id: str | None
    tree: NativeSessionTree
    tree_epoch: int
    pointer_epoch: int
    generation: SessionGenerationSnapshot
    publication_epoch: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderMutationEffects:
    """Composition-root handler owning the provider/model/auth/compaction
    mutation effects.

    Symmetric with :class:`CustomEntryRenderer`, :class:`_ReplLoopStep`, and
    :class:`_BuiltinCommandInterpreter`, these bodies formerly lived as the
    ``apply_model_selection``/``apply_auth_change``/``apply_compaction``/
    ``_append_durable_compaction``/``extension_set_active_tools``/
    ``extension_set_model``/``extension_set_thinking_level`` closures nested in
    ``CodingSession.run()``. They call one another densely (the compaction
    hook path and ``extension_set_model`` re-enter the peer effects), so the
    handler is a frozen, slotted, keyword-only dataclass that holds the run's
    mutable control-state holder ``ctl`` (its ``session_tree`` and canonical
    ``extension_generation`` are read fresh on every call so a
    ``/reload``/``/new``/``/resume``/``/fork``/``/clone`` rebind is reflected
    exactly as it was inline) plus the stable
    run-scope collaborators — the owning session (for its live
    ``provider_state``), the coding state, the product session, the terminal UI,
    the tool-capability facade, settings, cwd, the input/error streams, the
    footer-refresh port, and the extension notify sink / UI driver — passed as
    keyword-only construction arguments; its methods call each other through
    ``self``. The provider/model/auth rebinds clear only the live provider
    history and reset usage while preserving the in-memory compaction suffix and
    leaving the durable session tree intact. The ``run()`` composition root passes
    each bound method exactly where the deleted closures were consumed: the
    built-in interpreter's ``apply_compaction``/``apply_model_selection``/
    ``apply_auth_change``/``extension_set_active_tools`` ports, the loop-step
    handler's ``extension_set_*`` ports, and ``compact_context`` to its
    automatic-compaction port. Only the built-in ``/compact`` path uses the
    settling ``apply_compaction`` wrapper. Other consumers include the
    extension-dispatch and provider-request/tool-policy hook seams, and the
    product-session ``_persist_compaction`` durable-append callback.
    """

    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    ctl: RunControlState
    extension_operations: SessionExtensionOperations
    coding_state: CodingSessionState
    product_session: CodingProductSessionCoordinator
    terminal_ui: TerminalUi | None
    tool_capabilities: NativeToolCapabilities
    settings: SettingsManager
    cwd: Path
    input_stream: TextIO
    error_stream: TextIO
    refresh_footer_text: Callable[[], None]
    extension_notify: Callable[[str, str], None]
    # Orders a mutation's decision against its own follow-on I/O. Two callers
    # that assign under the session mutex and then append to the session tree
    # outside it could otherwise persist their changes in the opposite order,
    # leaving the durable record disagreeing with live state. Held *outside*
    # the session mutex, never inside it, so file I/O still never runs under
    # the session boundary.
    mutation_io_lock: "threading.RLock"
    provider_turn_executor: ProviderTurnExecutor
    abort_event: threading.Event | _AbortCallbackSignal | None

    def extension_set_active_tools(
        self, generation_id: int, tool_names: Sequence[str]
    ) -> bool:
        """Restrict future tools only for the context's live generation."""

        with self.ctl.generation_ref.lock:
            if not self._generation_admitted_locked(generation_id):
                return False
            return self.tool_capabilities.set_active_tools(tool_names)

    def extension_set_model(self, generation_id: int, reference: str) -> bool:
        """Prepare unlocked, atomically commit, then present one model switch."""

        with self.ctl.coding_effects.effect() as effect_admitted:
            if not effect_admitted:
                return False
            with self.mutation_io_lock:
                with self.ctl.generation_ref.lock:
                    if (
                        self.ctl.coding_effects.terminal
                        or not self._generation_admitted_locked(generation_id)
                    ):
                        return False
                    state = self.provider_state
                    if not isinstance(state, NativeReplProviderState):
                        return False
                    expected = state.capture_model_mutation_state()
                    expected_binding = self.coding_state.provider_binding
            prepared, _message = self._prepare_model_mutation(
                state, expected, expected_binding, reference
            )
            if prepared is None or not self._commit_model_mutation(
                prepared, generation_id=generation_id
            ):
                return False
            if prepared.coding is None:
                return False
            self._finish_model_mutation(prepared.provider_state, _message)
            return True

    def extension_set_thinking_level(self, generation_id: int, level: str) -> bool:
        """Commit, durably append, then paint one generation-bound level."""

        normalized: str | None = None
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._generation_admitted_locked(generation_id):
                    return False
                state = self.provider_state
                if isinstance(state, NativeReplProviderState):
                    normalized = state.set_supported_thinking_level(level)
                if normalized is None:
                    return False
            # The session mutex is released before durable filesystem I/O. The
            # outer coordinator remains held so concurrent commits and JSONL
            # appends have one order.
            self.ctl.session_tree.append_thinking_level_change(normalized)
        self.refresh_footer_text()
        return True

    def cycle_thinking_level(self) -> str | None:
        """Apply the session-thread cycle through the same ordered commit path."""

        next_level: str | None = None
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                state = self.provider_state
                if isinstance(state, NativeReplProviderState):
                    next_level = state.cycle_thinking_level()
                if next_level is None:
                    return None
            self.ctl.session_tree.append_thinking_level_change(next_level)
        return next_level

    def _generation_admitted_locked(self, generation_id: int) -> bool:
        """Check terminal, generation identity, and gate under the caller's mutex."""

        try:
            snapshot = self.ctl.generation_ref.snapshot()
        except RuntimeError:
            return False
        return (
            snapshot.generation_id == generation_id
            and not self.ctl.generation_ref.publication_pending
        )

    def model_runtime_control(
        self, generation_id: int, *, allow_model: bool = True
    ) -> ExtensionModelRuntimeControl:
        """Bundle the three model-runtime control callables for a context.

        The single adapter every command/hook/tool seam threads instead of
        passing the three bare callables. When ``allow_model`` is ``False``
        (the mid-turn tool_call / tool_result / before_provider_request hook
        paths, where a live model switch is not permitted), ``set_model``
        fails closed by returning ``False``.
        """

        return ExtensionModelRuntimeControl(
            set_active_tools_fn=partial(self.extension_set_active_tools, generation_id),
            set_model_fn=partial(
                self.extension_set_model if allow_model else _deny_model_mutation,
                generation_id,
            ),
            set_thinking_level_fn=partial(
                self.extension_set_thinking_level, generation_id
            ),
        )

    def _prepare_model_mutation(
        self,
        state: NativeReplProviderState,
        expected: NativeModelMutationState,
        expected_binding: CodingProviderBinding,
        reference: str,
    ) -> tuple[_PreparedModelMutation | None, str]:
        """Complete every fallible model/provider preparation while unlocked."""

        selection, message = state.prepare_model_mutation(expected, reference)
        if selection is None:
            return None, message
        try:
            supports_tools = bool(selection.provider.supports_tool_calls)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 - extension provider boundary
            return None, (
                "pipy: model provider capability preparation failed with "
                f"{sanitize_text(type(exc).__name__)}; selection unchanged."
            )
        if not supports_tools:
            return _PreparedModelMutation(state, selection, None), (
                "pipy: selected model does not support tool calls in tool-loop "
                "mode; selection unchanged."
            )
        replacement = selection.replacement.selection
        coding = self.coding_state.prepare_model_mutation(
            selection.provider,
            expected_binding=expected_binding,
            provider_name=replacement.provider_name,
            model_id=replacement.model_id,
            usage_accumulator=AgentUsageAccumulator(
                pricing_for(replacement.provider_name, replacement.model_id)
            ),
        )
        return _PreparedModelMutation(state, selection, coding), message

    def _commit_model_mutation(
        self,
        prepared: _PreparedModelMutation,
        *,
        generation_id: int | None,
    ) -> bool:
        """Check all owners, then publish only prepared in-memory values."""

        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if generation_id is not None and (
                    self.ctl.coding_effects.terminal
                    or not self._generation_admitted_locked(generation_id)
                ):
                    return False
                if self.provider_state is not prepared.provider_state:
                    return False
                if not prepared.provider_state.model_mutation_matches_expected(
                    prepared.selection
                ):
                    return False
                if prepared.coding is None:
                    prepared.provider_state.publish_model_capability_refusal(
                        prepared.selection
                    )
                    return True
                if not self.coding_state.model_mutation_matches_expected(
                    prepared.coding
                ):
                    return False
                prepared.provider_state.publish_model_mutation(prepared.selection)
                self.coding_state.publish_model_mutation(prepared.coding)
                return True

    def _finish_model_mutation(
        self, state: NativeReplProviderState, message: str
    ) -> str:
        """Run fail-soft presentation and default persistence after unlock."""

        diagnostics: list[str] = []
        try:
            self.refresh_footer_text()
        except Exception as exc:  # noqa: BLE001 - presentation is post-commit
            diagnostics.append(
                "pipy: selected model is active but presentation refresh failed "
                f"with {sanitize_text(type(exc).__name__)}."
            )
        try:
            persistence_error = state.flush_pending_default()
        except Exception as exc:  # noqa: BLE001 - persistence is post-commit
            persistence_error = (
                "pipy: selected model is active but could not be saved as the "
                f"default ({sanitize_text(type(exc).__name__)}); this session "
                "is unaffected."
            )
        if persistence_error is not None:
            diagnostics.append(persistence_error)
        return "\n".join((message, *diagnostics)) if diagnostics else message

    def apply_model_selection(self, reference: str) -> tuple[bool, str]:
        """Prepare, atomically commit, and present one product model switch."""

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return False, "pipy: /model is unavailable for this REPL provider state."
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                expected = state.capture_model_mutation_state()
                expected_binding = self.coding_state.provider_binding
        prepared, message = self._prepare_model_mutation(
            state, expected, expected_binding, reference
        )
        if prepared is None:
            return False, message
        if not self._commit_model_mutation(prepared, generation_id=None):
            return False, (
                "pipy: model selection changed while the provider was prepared; "
                "try again."
            )
        if prepared.coding is None:
            return False, message
        return True, self._finish_model_mutation(state, message)

    def apply_auth_change(self, action: str, argument: str) -> str:
        """Run ``/login`` or ``/logout`` through the auth boundary.

        Mirrors the no-tool auth path through the same
        ``NativeReplProviderState``: it performs no provider turn and no
        tool call, clears the in-memory conversation, then rebinds the live
        provider/usage/footer so refreshed model-option availability and the
        (possibly reset) selection take effect on the next turn. Interactive
        login output (the OAuth URL/prompt) renders only on the live
        terminal — never in the session archive — and the TUI live region is
        suspended around it so the inline frame repaints coherently
        afterward.
        """

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return f"pipy: /{action} is unavailable for this REPL provider state."
        provider_name = argument or "openai-codex"
        if action == "login":
            try:
                if self.terminal_ui is None:
                    _ok, message = state.login(
                        provider_name,
                        input_stream=self.input_stream,
                        output_stream=self.error_stream,
                    )
                else:
                    with self.terminal_ui.components.screen.external_io_suspension():
                        _ok, message = state.login(
                            provider_name,
                            input_stream=self.input_stream,
                            output_stream=self.error_stream,
                        )
            except Exception as exc:  # noqa: BLE001 - report, never crash REPL
                message = (
                    "pipy: openai-codex login failed with "
                    f"{type(exc).__name__}: {sanitize_text(str(exc))}"
                )
        else:
            try:
                _ok, message = state.logout(provider_name)
            except Exception as exc:  # noqa: BLE001 - report, never crash REPL
                message = (
                    "pipy: openai-codex logout failed with "
                    f"{type(exc).__name__}: {sanitize_text(str(exc))}"
                )
        # Clear context and rebind the live provider regardless of outcome,
        # so a credential change never leaks prior context or leaves a stale
        # provider bound (logout resets the selection to the local default).
        # The persisted default stays the inert ``fake-native-bootstrap``;
        # the product REPL upgrades the *live* fake selection to the
        # tool-capable ``fake-tools`` here so the next turn has tool support.
        state.replace_selection(
            normalize_repl_fake_selection(state.current_selection())
        )
        rebound_provider = state.current_provider()
        selection = state.current_selection()
        self.coding_state.rebind_provider(
            rebound_provider,
            provider_name=selection.provider_name,
            model_id=selection.model_id,
            usage_accumulator=AgentUsageAccumulator(
                pricing_for(selection.provider_name, selection.model_id)
            ),
        )
        self.refresh_footer_text()
        # Post-commit: the logout-reset selection is already live.
        persistence_error = state.flush_pending_default()
        if persistence_error is not None:
            message = f"{message}\n{persistence_error}"
        return message

    def refresh_provider_after_reload(self) -> None:
        """Refresh or rebind the live provider after catalog recomposition."""

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return
        snapshot = self.ctl.generation_ref.snapshot()
        projection = snapshot.generation.projection
        if projection is None:
            raise RuntimeError("published extension generation has no projection")
        runtime = state.model_runtime
        catalog_state = runtime.catalog
        if catalog_state.auth_store is None:
            return
        was_extension_selection = state.current_selection_uses_extension_provider()
        catalog_state.refresh()
        providers = projection.providers
        catalog_state.set_extension_provider_contributions(
            providers.providers,
            providers.unregistered,
        )
        selection_disappeared = not state.current_selection_supported() or (
            was_extension_selection
            and not state.current_selection_uses_extension_provider()
        )
        if selection_disappeared:
            self._rebind_after_reload(
                state,
                selected_message="pipy: active model disappeared on reload; selected",
                unavailable_message=(
                    "active model disappeared on reload and no available "
                    "tool-capable fallback was found"
                ),
            )
            return
        if not state.current_selection_uses_extension_provider():
            return
        refreshed_provider = state.current_provider()
        if getattr(refreshed_provider, "supports_tool_calls", False):
            self.coding_state.refresh_provider(refreshed_provider)
            return
        self._rebind_after_reload(
            state,
            selected_message=(
                "pipy: active model no longer supports tool calls after reload; "
                "selected"
            ),
            unavailable_message=(
                "active model no longer supports tool calls after reload and no "
                "available tool-capable fallback was found"
            ),
        )

    def _rebind_after_reload(
        self,
        state: NativeReplProviderState,
        *,
        selected_message: str,
        unavailable_message: str,
    ) -> None:
        fallback = state.reset_to_first_available_model(require_tool_calls=True)
        if fallback is None:
            self._bind_unavailable_after_reload(unavailable_message)
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                f"pipy: {unavailable_message}.",
            )
            return
        fallback_provider = state.current_provider()
        self.coding_state.rebind_provider(
            fallback_provider,
            provider_name=fallback.provider_name,
            model_id=fallback.model_id,
            usage_accumulator=AgentUsageAccumulator(
                pricing_for(fallback.provider_name, fallback.model_id)
            ),
        )
        emit_diagnostic(
            self.terminal_ui.components.transcript
            if self.terminal_ui is not None
            else None,
            self.error_stream,
            f"{selected_message} {fallback.reference}.",
        )
        persistence_error = _report_default_persistence(state)
        if persistence_error is not None:
            emit_diagnostic(
                self.terminal_ui.components.transcript
                if self.terminal_ui is not None
                else None,
                self.error_stream,
                persistence_error,
            )

    def _bind_unavailable_after_reload(self, message: str) -> None:
        unavailable_provider = UnavailableAfterReloadProvider(
            name=self.coding_state.provider_name,
            model_id=self.coding_state.model_id,
            error_message=message,
        )
        self.coding_state.mark_provider_unavailable(unavailable_provider)

    def declared_context_window(self, binding: CodingProviderBinding) -> int | None:
        """Resolve metadata for captured labels; injected/assumed rows stay unknown."""

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return None
        spec = state.model_runtime.resolve_spec(
            NativeModelSelection(binding.provider_name, binding.model_id)
        )
        return spec.declared_context_window if spec is not None else None

    def apply_compaction(self, trigger: str) -> str:
        """Settle manual input: this command has no later canonical settlement.

        Restore operator-aborted input for editing; otherwise release queued
        input to the ordinary drain after the command returns. Persistence
        exceptions still escape before settlement. Automatic work calls
        ``compact_context`` directly and retains canonical settlement.
        """

        outcome = self.compact_context(trigger)
        if self.terminal_ui is not None:
            pending = self.terminal_ui.components.pending_messages
            if outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT:
                pending.restore_pending_to_editor()
            else:
                pending.promote_pending_to_drain()
        return outcome.notice

    def compact_context(
        self,
        trigger: str,
        budget: RequestBudget | None = None,
        keep_recent_groups: int = AGENT_HISTORY_KEEP_RECENT_GROUPS,
    ) -> CodingCompactionOutcome:
        """Generate privately, then conditionally accept and persist one summary."""

        prepared = self._prepare_compaction_budget(trigger, budget, keep_recent_groups)
        if isinstance(prepared, CodingCompactionOutcome):
            return prepared
        work, budget = prepared
        completion = None
        try:
            # Capturing request headers can reach extension callbacks. It must
            # neither hold the locks nor authorize a stale provider request.
            header = self.extension_operations.provider_header_callback(work.tree)
            with self.mutation_io_lock:
                with self.ctl.generation_ref.lock:
                    if not self._compaction_matches_locked(work):
                        return self._stale_compaction(trigger)
            request = compaction_request(
                binding=work.context.binding,
                cwd=self.cwd,
                dropped_messages=work.context.messages[
                    : work.cut.dropped_message_count
                ],
                prior_summary=work.context.summary_suffix.strip(),
                header_callback=header,
            )
            request = freeze_provider_request(request)
            estimate = estimate_request(
                request, image_count=0, output_reserve=budget.output_reserve
            )
            if budget.allows(estimate) is False:
                return self._refuse_compaction_budget(
                    work,
                    trigger,
                    "estimated summary request exceeds the context window; context unchanged",
                )
            provider, waiter = provider_turn_inputs(
                work.context.binding.provider, self.terminal_ui, self.abort_event
            )
            completion = self.provider_turn_executor.complete(
                provider,
                request,
                PrivateSummaryEvents(),
                turn_index=0,
                delta_policy=ProviderTurnDeltaPolicy(text=False, reasoning=False),
                waiter=waiter,
            )
        except CodingContextChangedError:
            raise
        except Exception:  # noqa: BLE001 - auxiliary failures have content-free notices
            pass
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._compaction_matches_locked(work):
                    return self._stale_compaction(trigger)
                if completion is None:
                    return CodingCompactionOutcome(
                        "pipy: compaction failed; context unchanged."
                    )
                if completion.cancellation_reason is not None:
                    return CodingCompactionOutcome(
                        "pipy: compaction cancelled.", completion.cancellation_reason
                    )
                result = completion.result
                summary = (
                    summary_text(result)
                    if result is not None and not result.tool_calls
                    else None
                )
                if summary is None:
                    return CodingCompactionOutcome(
                        "pipy: compaction failed; context unchanged."
                    )
                action = CodingProductSessionCompaction(
                    retained_messages=work.cut.messages,
                    summary_suffix=ProductContent("\n\n" + summary),
                    durable_summary=ProductContent(summary),
                    dropped_group_count=work.cut.dropped_group_count,
                    measure_before=work.cut.bytes_before,
                    first_kept_entry_id=work.first_kept_entry_id,
                )
                self.product_session.accept_compaction(action)
            # Accepted state intentionally survives a persistence exception.
            # Keep this callback outside the generation-failure handler above.
            self.product_session.persist_compaction(action)
        return CodingCompactionOutcome(
            f"pipy: compacted conversation context ({trigger}; dropped "
            f"{work.cut.dropped_group_count} earlier exchange(s), kept {work.cut.retained_group_count})."
        )

    def _prepare_compaction_budget(
        self, trigger: str, budget: RequestBudget | None, keep_recent_groups: int
    ) -> tuple[_CompactionWork, RequestBudget] | CodingCompactionOutcome:
        decision = self.extension_operations.session_allows(
            "compact", operation="compact", trigger=trigger
        )
        if not decision.allow:
            return CodingCompactionOutcome(
                f"pipy: compact blocked by extension: {decision.reason or 'blocked by extension'}"
            )
        invalid_policy = None
        settings = None
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                work = self._capture_compaction_locked(trigger, keep_recent_groups)
                if budget is None:
                    try:
                        settings = self.settings.capture_compaction_budget_settings()
                    except ValueError as exc:
                        invalid_policy = str(exc)
        if isinstance(work, CodingCompactionOutcome):
            return work
        if invalid_policy is not None:
            return self._refuse_compaction_budget(work, trigger, invalid_policy)
        if budget is None:
            assert settings is not None
            declared = self.declared_context_window(work.context.binding)
            try:
                budget = resolve_request_budget(
                    declared_context_window=declared,
                    explicit_ceiling=settings.context_window,
                    output_reserve=settings.reserve_tokens,
                )
            except (TypeError, ValueError):
                return self._refuse_compaction_budget(
                    work,
                    trigger,
                    "invalid context budget: use positive context limits and an output reserve smaller than the context window",
                )
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._compaction_matches_locked(work):
                    return self._stale_compaction(trigger)
        return work, budget

    def _refuse_compaction_budget(
        self, work: _CompactionWork, trigger: str, notice: str
    ) -> CodingCompactionOutcome:
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._compaction_matches_locked(work):
                    return self._stale_compaction(trigger)
        cancelled = self.abort_event is not None and self.abort_event.is_set()
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._compaction_matches_locked(work):
                    return self._stale_compaction(trigger)
        if cancelled:
            return CodingCompactionOutcome(
                "pipy: compaction cancelled.", AgentCancellationReason.OPERATOR_ABORT
            )
        return CodingCompactionOutcome(f"pipy: compact refused: {notice}.")

    def _capture_compaction_locked(
        self, trigger: str, keep_recent_groups: int = AGENT_HISTORY_KEEP_RECENT_GROUPS
    ) -> _CompactionWork | CodingCompactionOutcome:
        if (
            self.ctl.coding_effects.terminal
            or self.ctl.generation_ref.publication_pending
        ):
            return self._stale_compaction(trigger)
        context = self.coding_state.compaction_snapshot()
        cut = compact_agent_history(
            context.messages, keep_recent_groups=keep_recent_groups
        )
        if not cut.changed:
            return CodingCompactionOutcome("pipy: nothing to compact yet.")
        tree = self.ctl.session_tree
        projection = tree.build_coding_context()
        first_kept = self.product_session.resolve_entry_id(
            cut.messages[0],
            CodingProductSessionContext(
                messages=projection.messages, entry_ids=projection.entry_ids
            ),
        )
        if first_kept is None and tree.persist:
            return CodingCompactionOutcome(
                "pipy: compact refused: retained history has no durable origin."
            )
        return _CompactionWork(
            context,
            cut,
            first_kept,
            tree,
            tree.mutation_epoch,
            self.ctl.tree_pointer_epoch,
            self.ctl.generation_ref.snapshot(),
            self.ctl.generation_ref.publication_epoch,
        )

    def _compaction_matches_locked(self, work: _CompactionWork) -> bool:
        if (
            self.ctl.coding_effects.terminal
            or self.ctl.generation_ref.publication_pending
        ):
            return False
        generation = self.ctl.generation_ref.snapshot()
        return (
            self.coding_state.compaction_matches(work.context)
            and self.ctl.session_tree is work.tree
            and self.ctl.tree_pointer_epoch == work.pointer_epoch
            and work.tree.mutation_epoch == work.tree_epoch
            and generation.generation is work.generation.generation
            and generation.generation_id == work.generation.generation_id
            and self.ctl.generation_ref.publication_epoch == work.publication_epoch
        )

    @staticmethod
    def _stale_compaction(trigger: str) -> CodingCompactionOutcome:
        if trigger == "auto":
            raise CodingContextChangedError()
        return CodingCompactionOutcome("pipy: compact refused: context changed.")

    def append_durable_compaction(self, action: CodingProductSessionCompaction) -> None:
        """Persist the exact boundary resolved before live state acceptance."""

        if action.first_kept_entry_id is None:
            return
        self.ctl.session_tree.append_compaction(
            summary=action.durable_summary.value.strip(),
            first_kept_entry_id=action.first_kept_entry_id,
            tokens_before=action.measure_before,
        )
