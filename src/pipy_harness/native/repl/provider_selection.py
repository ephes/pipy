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
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.native.agent import ProductContent
from pipy_harness.native.agent.history import (
    AgentHistoryCompaction,
    compact_agent_history,
    compact_agent_history_tool_cycles,
)
from pipy_harness.native.agent.provider_retry import ProviderManagedRetryPolicy
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnDeltaPolicy,
    ProviderTurnExecutor,
    _AbortCallbackSignal,
)
from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.compaction import (
    AutomaticCompactionContext,
    CodingCompactionOutcome,
    CodingCompactionResult,
    PrivateSummaryEvents,
    compaction_request,
    compound_compaction_cuts,
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
from pipy_harness.native.provider import PreparedProviderPort
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
from pipy_harness.native.scoped_models import filter_scoped_references, next_reference
from pipy_harness.native.session_generation import SessionGenerationSnapshot
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager, retry_policy_from_settings
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
class RpcConfigurationSnapshot:
    """Immutable configuration projection consumed by the RPC transport."""

    selection: NativeModelSelection
    thinking_level: str


@dataclass(frozen=True, slots=True)
class RpcConfigurationResult:
    """Bounded outcome of one RPC configuration operation."""

    success: bool
    snapshot: RpcConfigurationSnapshot | None = None
    thinking_changed: bool = False
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class RpcModelCycleResult:
    """A cycle projection, including Pi's enabled-model scope indication."""

    result: RpcConfigurationResult
    is_scoped: bool


class RpcProviderConfigurationPort:
    """Private, once-bound RPC view over :class:`ProviderMutationEffects`.

    It deliberately carries only immutable projections and callbacks.  The
    transport never receives provider state, settings, catalog rows, locks, or
    construction functions.
    """

    __slots__ = ("_commit_if_true_idle", "_effects")

    def __init__(
        self,
        effects: "ProviderMutationEffects",
        commit_if_true_idle: Callable[[Callable[[], None]], bool],
    ) -> None:
        self._effects = effects
        self._commit_if_true_idle = commit_if_true_idle

    def snapshot(self) -> RpcConfigurationSnapshot:
        return self._effects._rpc_snapshot()

    def available_models(self) -> tuple[NativeModelSelection, ...]:
        return self._effects._rpc_available_models()

    def set_model(self, selection: NativeModelSelection) -> RpcConfigurationResult:
        return self._effects._rpc_set_model(selection, self._commit_if_true_idle)

    def cycle_model(self) -> RpcModelCycleResult | None:
        return self._effects._rpc_cycle_model(self._commit_if_true_idle)

    def set_thinking_level(self, level: str) -> RpcConfigurationResult:
        return self._effects._rpc_set_thinking_level(level, self._commit_if_true_idle)

    def cycle_thinking_level(self) -> RpcConfigurationResult | None:
        return self._effects._rpc_cycle_thinking_level(self._commit_if_true_idle)


class RpcCompactionPort:
    """Private worker-only projection over the existing compaction owner."""

    __slots__ = ("_effects",)

    def __init__(self, effects: "ProviderMutationEffects") -> None:
        self._effects = effects

    def compact(
        self, custom_instructions: ProductContent | None
    ) -> CodingCompactionOutcome:
        return self._effects.compact_context(
            "manual",
            custom_instructions=custom_instructions,
            project_persistence_failure=True,
        )

    def auto_compaction_enabled(self) -> bool:
        return self._effects.settings.capture_compaction_budget_settings().enabled

    def is_compacting(self) -> bool:
        with self._effects.ctl.coding_effects.lock:
            return self._effects.ctl.compaction_active

    def set_auto_compaction_enabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise TypeError("enabled must be an exact bool")
        if self.auto_compaction_enabled() == enabled:
            return True
        # SettingsManager is the durable owner; it serializes file replacement
        # with effective-policy publication. A later preparation captures this
        # new immutable value while an already captured request keeps its old one.
        return self._effects.settings.set_auto_compaction_enabled(enabled)


@dataclass(frozen=True, slots=True)
class _CompactionWork:
    context: CodingCompactionSnapshot
    cut: AgentHistoryCompaction
    first_kept_entry_id: str | None
    retained_user_entry_id: str | None
    tree: NativeSessionTree
    tree_epoch: int
    pointer_epoch: int
    generation: SessionGenerationSnapshot
    publication_epoch: int
    retry_policy: ProviderManagedRetryPolicy | None


class _StaleCompactionRetry(RuntimeError):
    """Private control signal selecting the trigger-specific stale outcome."""


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

    def rpc_configuration_port(
        self, commit_if_true_idle: Callable[[Callable[[], None]], bool]
    ) -> RpcProviderConfigurationPort:
        """Return the private RPC port after composition has bound all owners."""

        return RpcProviderConfigurationPort(self, commit_if_true_idle)

    def rpc_compaction_port(self) -> RpcCompactionPort:
        return RpcCompactionPort(self)

    def _rpc_snapshot(self) -> RpcConfigurationSnapshot:
        """Capture model and thinking from one owner state transition."""

        state = self.provider_state
        if isinstance(state, NativeReplProviderState):
            with self.mutation_io_lock:
                with self.ctl.generation_ref.lock:
                    value = state.capture_model_mutation_state()
            return RpcConfigurationSnapshot(
                value.selection, value.thinking_level or "off"
            )
        binding = self.coding_state.provider_binding
        return RpcConfigurationSnapshot(
            NativeModelSelection(binding.provider_name, binding.model_id), "off"
        )

    def _rpc_available_models(self) -> tuple[NativeModelSelection, ...]:
        _active, models = self._rpc_selectable_models()
        return models

    def _rpc_selectable_models(
        self,
    ) -> tuple[NativeModelSelection, tuple[NativeModelSelection, ...]]:
        """Project selectable catalog models with one locked active snapshot.

        Catalog enumeration and detached provider construction intentionally run
        after releasing the session owners.  A selected custom reference that
        has no catalog row remains visible, while a known but unavailable or
        tool-incapable row remains excluded.
        """

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            selection = self._rpc_snapshot().selection
            return selection, (selection,)
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                expected = state.capture_model_mutation_state()
        active = expected.selection
        options = tuple(state.model_options())
        catalog_selections = {option.selection for option in options}
        models: list[NativeModelSelection] = []
        for option in options:
            if not option.available:
                continue
            prepared, _message = state.prepare_model_mutation(
                expected, option.selection.reference
            )
            if prepared is None:
                continue
            try:
                if prepared.provider.supports_tool_calls:
                    models.append(option.selection)
            except Exception:  # noqa: BLE001 - provider capability boundary
                continue
        models_tuple = tuple(models)
        if active not in catalog_selections and active not in models_tuple:
            models_tuple = (*models_tuple, active)
        return active, models_tuple

    def _rpc_commit_model_if_idle(
        self,
        state: NativeReplProviderState,
        prepared: _PreparedModelMutation,
        commit_if_true_idle: Callable[[Callable[[], None]], bool],
    ) -> tuple[bool, bool]:
        """Publish prepared model state under the outer admission gate."""

        committed = False

        def commit() -> None:
            nonlocal committed
            with self.mutation_io_lock:
                with self.ctl.generation_ref.lock:
                    if (
                        self.ctl.coding_effects.terminal
                        or self.provider_state is not state
                        or not state.model_mutation_matches_expected(prepared.selection)
                        or prepared.coding is None
                        or not self.coding_state.model_mutation_matches_expected(
                            prepared.coding
                        )
                    ):
                        return
                    state.publish_model_mutation(prepared.selection)
                    self.coding_state.publish_model_mutation(prepared.coding)
                    committed = True

        return commit_if_true_idle(commit), committed

    def _rpc_set_model(
        self,
        selection: NativeModelSelection,
        commit_if_true_idle: Callable[[Callable[[], None]], bool],
    ) -> RpcConfigurationResult:
        """Prepare a model detached, then publish under the queue's idle gate."""

        current = self._rpc_snapshot()
        if selection == current.selection:
            return RpcConfigurationResult(True, current)
        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return RpcConfigurationResult(False, diagnostic="unknown provider/model")
        if selection not in self._rpc_available_models():
            return RpcConfigurationResult(False, diagnostic="unknown provider/model")
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if self.ctl.coding_effects.terminal:
                    return RpcConfigurationResult(False, diagnostic="session is closed")
                expected = state.capture_model_mutation_state()
                expected_binding = self.coding_state.provider_binding
        prepared, message = self._prepare_model_mutation(
            state,
            expected,
            expected_binding,
            selection.reference,
            clamp_thinking=True,
        )
        if prepared is None or prepared.coding is None:
            return RpcConfigurationResult(False, diagnostic=message)

        idle, committed = self._rpc_commit_model_if_idle(
            state, prepared, commit_if_true_idle
        )
        if not idle:
            return RpcConfigurationResult(False, diagnostic="session is not idle")
        if not committed:
            return RpcConfigurationResult(
                False, diagnostic="configuration changed during preparation"
            )
        snapshot = self._rpc_snapshot()
        diagnostics: list[str] = []
        if snapshot.thinking_level != (expected.thinking_level or "off"):
            diagnostics.extend(self._rpc_append_thinking(snapshot.thinking_level))
        finish = self._finish_model_mutation(state, message)
        diagnostics.extend(
            line for line in finish.splitlines() if " is active but " in line
        )
        self._rpc_emit_diagnostics(diagnostics)
        return RpcConfigurationResult(
            True,
            snapshot,
            thinking_changed=snapshot.thinking_level
            != (expected.thinking_level or "off"),
            diagnostic="\n".join(diagnostics) or None,
        )

    def _rpc_cycle_model(
        self, commit_if_true_idle: Callable[[Callable[[], None]], bool]
    ) -> RpcModelCycleResult | None:
        current, all_models = self._rpc_selectable_models()
        if len(all_models) <= 1:
            return None
        all_references = [model.reference for model in all_models]
        patterns = self.settings.get_enabled_models()
        scoped_references = filter_scoped_references(all_references, patterns)
        scoped = bool(patterns) and bool(scoped_references)
        choices = (
            tuple(
                model
                for model in all_models
                if model.reference in set(scoped_references)
            )
            if scoped
            else all_models
        )
        if len(choices) <= 1:
            return None
        next_model = next_reference(
            [model.reference for model in choices], current.reference, forward=True
        )
        if next_model is None:
            return None
        result = self._rpc_set_model(
            next(model for model in choices if model.reference == next_model),
            commit_if_true_idle,
        )
        return RpcModelCycleResult(result, scoped)

    def _rpc_append_thinking(self, level: str) -> list[str]:
        """Persist a live thinking transition after releasing the queue gate."""

        try:
            with self.mutation_io_lock:
                self.ctl.session_tree.append_thinking_level_change(level)
        except Exception as exc:  # noqa: BLE001 - state-first durable outcome
            return [
                "pipy: thinking level is active but durable append failed with "
                f"{sanitize_text(type(exc).__name__)}."
            ]
        return []

    def _rpc_emit_diagnostics(self, diagnostics: Sequence[str]) -> None:
        """Surface bounded post-commit failures without changing live success."""

        for diagnostic in diagnostics:
            emit_diagnostic(
                self.terminal_ui.components.transcript if self.terminal_ui else None,
                self.error_stream,
                diagnostic,
            )

    def _rpc_set_thinking_level(
        self,
        level: str,
        commit_if_true_idle: Callable[[Callable[[], None]], bool],
    ) -> RpcConfigurationResult:
        """Refresh the same provider binding for a supported thinking level."""

        current = self._rpc_snapshot()
        normalized = level.strip().lower()
        if normalized == current.thinking_level:
            return RpcConfigurationResult(True, current)
        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return RpcConfigurationResult(
                False, diagnostic="unsupported thinking level"
            )
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if self.ctl.coding_effects.terminal:
                    return RpcConfigurationResult(False, diagnostic="session is closed")
                expected = state.capture_model_mutation_state()
                expected_binding = self.coding_state.provider_binding
        prepared = state.prepare_thinking_mutation(expected, normalized)
        if prepared is None:
            return RpcConfigurationResult(
                False, diagnostic="unsupported thinking level"
            )
        committed = False

        def commit() -> None:
            nonlocal committed
            with self.mutation_io_lock:
                with self.ctl.generation_ref.lock:
                    if (
                        self.ctl.coding_effects.terminal
                        or self.provider_state is not state
                        or not state.thinking_mutation_matches_expected(prepared)
                        or self.coding_state.provider_binding is not expected_binding
                    ):
                        return
                    state.publish_thinking_mutation(prepared)
                    self.coding_state.refresh_provider(prepared.provider)
                    committed = True

        if not commit_if_true_idle(commit):
            return RpcConfigurationResult(False, diagnostic="session is not idle")
        if not committed:
            return RpcConfigurationResult(
                False, diagnostic="configuration changed during preparation"
            )
        snapshot = self._rpc_snapshot()
        diagnostics = self._rpc_append_thinking(snapshot.thinking_level)
        try:
            self.refresh_footer_text()
        except Exception as exc:  # noqa: BLE001 - presentation is post-commit
            diagnostics.append(
                "pipy: thinking level is active but presentation refresh failed with "
                f"{sanitize_text(type(exc).__name__)}."
            )
        self._rpc_emit_diagnostics(diagnostics)
        return RpcConfigurationResult(
            True,
            snapshot,
            thinking_changed=True,
            diagnostic="\n".join(diagnostics) or None,
        )

    def _rpc_cycle_thinking_level(
        self, commit_if_true_idle: Callable[[Callable[[], None]], bool]
    ) -> RpcConfigurationResult | None:
        snapshot = self._rpc_snapshot()
        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return None
        levels = tuple(state.model_runtime.thinking_levels(snapshot.selection))
        if len(levels) <= 1:
            return None
        current = (
            snapshot.thinking_level if snapshot.thinking_level in levels else "off"
        )
        level = levels[(levels.index(current) + 1) % len(levels)]
        return self._rpc_set_thinking_level(level, commit_if_true_idle)

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
        *,
        clamp_thinking: bool = False,
    ) -> tuple[_PreparedModelMutation | None, str]:
        """Complete every fallible model/provider preparation while unlocked."""

        selection, message = state.prepare_model_mutation(
            expected, reference, clamp_thinking=clamp_thinking
        )
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

    def compact_context(  # noqa: C901 - ordered failure/stale settlement matrix
        self,
        trigger: str,
        budget: RequestBudget | None = None,
        keep_recent_groups: int = AGENT_HISTORY_KEEP_RECENT_GROUPS,
        automatic_context: AutomaticCompactionContext | None = None,
        lifecycle: Callable[[str, CodingCompactionOutcome | None], None] | None = None,
        custom_instructions: ProductContent | None = None,
        project_persistence_failure: bool = False,
    ) -> CodingCompactionOutcome:
        """Generate privately, then conditionally accept and persist one summary."""

        prepared = self._prepare_compaction_budget(
            trigger, budget, keep_recent_groups, automatic_context
        )
        if isinstance(prepared, CodingCompactionOutcome):
            return prepared
        work, budget = prepared
        completion = None
        started = False
        starting = False

        def finish(
            outcome: CodingCompactionOutcome | None,
        ) -> CodingCompactionOutcome | None:
            if started and lifecycle is not None:
                lifecycle("end", outcome)
            return outcome

        def stale_after_start() -> CodingCompactionOutcome:
            if trigger == "auto":
                finish(None)
                raise CodingContextChangedError()
            outcome = self._stale_compaction(trigger)
            assert outcome is not None
            return finish(outcome)  # type: ignore[return-value]

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
                dropped_messages=work.cut.removed_messages,
                prior_summary=work.context.summary_suffix.strip(),
                retained_user=work.cut.retained_user_anchor,
                custom_instructions=custom_instructions,
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

            if lifecycle is not None:
                starting = True
                lifecycle("start", None)
                starting = False
                started = True

            def _before_reissue() -> None:
                with self.mutation_io_lock:
                    with self.ctl.generation_ref.lock:
                        if not self._compaction_matches_locked(work):
                            raise _StaleCompactionRetry

            completion = self.provider_turn_executor.complete(
                provider,
                request,
                PrivateSummaryEvents(),
                turn_index=0,
                delta_policy=ProviderTurnDeltaPolicy(text=False, reasoning=False),
                waiter=waiter,
                retry_policy=work.retry_policy,
                before_reissue=(
                    _before_reissue if work.retry_policy is not None else None
                ),
            )
        except _StaleCompactionRetry:
            return stale_after_start()
        except CodingContextChangedError:
            finish(None)
            raise
        except Exception:  # noqa: BLE001 - auxiliary failures have content-free notices
            if starting:
                raise
            pass
        post_outcome: CodingCompactionOutcome | None = None
        stale_after_provider = False
        action: CodingProductSessionCompaction | None = None
        summary: str | None = None
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if not self._compaction_matches_locked(work):
                    stale_after_provider = True
                elif completion is None:
                    post_outcome = CodingCompactionOutcome(
                        "pipy: compaction failed; context unchanged."
                    )
                elif completion.cancellation_reason is not None:
                    post_outcome = CodingCompactionOutcome(
                        "pipy: compaction cancelled.", completion.cancellation_reason
                    )
                else:
                    result = completion.result
                    summary = (
                        summary_text(result)
                        if result is not None and not result.tool_calls
                        else None
                    )
                    if summary is None:
                        post_outcome = CodingCompactionOutcome(
                            "pipy: compaction failed; context unchanged."
                        )
                    else:
                        action = CodingProductSessionCompaction(
                            retained_messages=work.cut.messages,
                            summary_suffix=ProductContent("\n\n" + summary),
                            durable_summary=ProductContent(summary),
                            dropped_group_count=work.cut.dropped_group_count,
                            dropped_message_count=work.cut.dropped_message_count,
                            measure_before=work.cut.bytes_before,
                            first_kept_entry_id=work.first_kept_entry_id,
                            retained_user_entry_id=work.retained_user_entry_id,
                        )
                        self.product_session.accept_compaction(action)
        if stale_after_provider:
            return stale_after_start()
        if post_outcome is not None:
            return finish(post_outcome)  # type: ignore[return-value]
        assert action is not None and summary is not None
        # Accepted state intentionally survives a persistence exception. The
        # established persistence owner keeps its mutation serialization; only
        # the observer projection below runs after that scope has released.
        try:
            with self.mutation_io_lock:
                self.product_session.persist_compaction(action)
        except Exception as persistence_error:
            projected = CodingCompactionOutcome(
                self._compaction_notice(trigger, work.cut),
                result=CodingCompactionResult(
                    summary,
                    action.first_kept_entry_id if work.tree.persist else None,
                    action.measure_before,
                    action.dropped_group_count,
                    action.dropped_message_count,
                ),
                persistence_failed=True,
            )
            try:
                finish(projected)
            except BaseException:  # noqa: BLE001 - persistence remains primary
                # Persistence is the primary state-first failure.  Manual RPC
                # projection normally has no lifecycle observer; when a caller
                # explicitly supplies one in projected-return mode, retain the
                # accepted result rather than replacing it with observer I/O.
                persistence_error.add_note(
                    "compaction lifecycle publication also failed"
                )
            if not project_persistence_failure:
                raise persistence_error
            return projected
        return finish(
            CodingCompactionOutcome(
                self._compaction_notice(trigger, work.cut),
                result=CodingCompactionResult(
                    summary,
                    action.first_kept_entry_id if work.tree.persist else None,
                    action.measure_before,
                    action.dropped_group_count,
                    action.dropped_message_count,
                ),
            )
        )  # type: ignore[return-value]

    @staticmethod
    def _compaction_notice(trigger: str, cut: AgentHistoryCompaction) -> str:
        if cut.retained_user_anchor is not None:
            detail = (
                f"dropped {cut.dropped_message_count} message(s), including earlier "
                f"tool cycle(s) and {cut.dropped_group_count} whole exchange(s); "
                "kept the newest cycle"
            )
        elif cut.dropped_group_count:
            detail = (
                f"dropped {cut.dropped_group_count} earlier exchange(s), "
                f"kept {cut.retained_group_count}"
            )
        else:
            detail = (
                f"dropped {cut.dropped_message_count} message(s) from earlier "
                "tool cycle(s), kept the newest cycle"
            )
        return f"pipy: compacted conversation context ({trigger}; {detail})."

    def _prepare_compaction_budget(
        self,
        trigger: str,
        budget: RequestBudget | None,
        keep_recent_groups: int,
        automatic_context: AutomaticCompactionContext | None,
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
        retry_policy = None
        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                work = self._capture_compaction_locked(
                    trigger, keep_recent_groups, budget, automatic_context
                )
                if budget is None:
                    try:
                        settings = self.settings.capture_compaction_budget_settings()
                    except ValueError as exc:
                        invalid_policy = str(exc)
                if isinstance(work, _CompactionWork) and isinstance(
                    work.context.binding.provider, PreparedProviderPort
                ):
                    configured = retry_policy_from_settings(self.settings)
                    retry_policy = ProviderManagedRetryPolicy(
                        max_attempts=configured.max_attempts,
                        initial_delay_seconds=configured.initial_delay_seconds,
                        max_delay_seconds=configured.max_delay_seconds,
                        multiplier=configured.multiplier,
                        jitter_seconds=configured.jitter_seconds,
                    )
        if isinstance(work, CodingCompactionOutcome):
            return work
        work = replace(work, retry_policy=retry_policy)
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
        self,
        trigger: str,
        keep_recent_groups: int = AGENT_HISTORY_KEEP_RECENT_GROUPS,
        budget: RequestBudget | None = None,
        automatic_context: AutomaticCompactionContext | None = None,
    ) -> _CompactionWork | CodingCompactionOutcome:
        if (
            self.ctl.coding_effects.terminal
            or self.ctl.generation_ref.publication_pending
        ):
            return self._stale_compaction(trigger)
        context = self.coding_state.compaction_snapshot()
        if automatic_context is not None and not self._automatic_context_is_current(
            context, automatic_context
        ):
            return self._stale_compaction(trigger)
        cut = compact_agent_history(
            context.messages, keep_recent_groups=keep_recent_groups
        )
        selected = self._maybe_select_automatic_cut(
            trigger, context, cut, budget, automatic_context
        )
        if isinstance(selected, CodingCompactionOutcome):
            return selected
        cut = selected
        if not cut.changed:
            return CodingCompactionOutcome("pipy: nothing to compact yet.")
        tree = self.ctl.session_tree
        projection = tree.build_coding_context()
        durable_boundary = cut.retained_suffix_boundary or cut.messages[0]
        first_kept = self.product_session.resolve_entry_id(
            durable_boundary,
            CodingProductSessionContext(
                messages=projection.messages, entry_ids=projection.entry_ids
            ),
        )
        if first_kept is None and tree.persist:
            return CodingCompactionOutcome(
                "pipy: compact refused: retained history has no durable origin."
            )
        retained_user = None
        if cut.retained_user_anchor is not None:
            retained_user = self.product_session.resolve_entry_id(
                cut.retained_user_anchor,
                CodingProductSessionContext(
                    messages=projection.messages, entry_ids=projection.entry_ids
                ),
            )
            if retained_user is None or first_kept is None:
                return CodingCompactionOutcome(
                    "pipy: compact refused: retained cut has no durable origins."
                )
        if retained_user is not None and first_kept is not None:
            try:
                tree.validate_anchored_compaction_references(
                    retained_user_entry_id=retained_user,
                    first_kept_entry_id=first_kept,
                )
            except (TypeError, ValueError):
                return CodingCompactionOutcome(
                    "pipy: compact refused: retained cut has invalid durable origins."
                )
        return _CompactionWork(
            context,
            cut,
            first_kept,
            retained_user,
            tree,
            tree.mutation_epoch,
            self.ctl.tree_pointer_epoch,
            self.ctl.generation_ref.snapshot(),
            self.ctl.generation_ref.publication_epoch,
            None,
        )

    @staticmethod
    def _automatic_context_matches(
        context: CodingCompactionSnapshot,
        automatic: AutomaticCompactionContext,
    ) -> bool:
        expected = automatic.run_context
        return (
            context.summary_suffix == expected.summary_suffix
            and len(context.messages) == len(expected.messages)
            and all(
                current is captured
                for current, captured in zip(
                    context.messages, expected.messages, strict=True
                )
            )
        )

    def _automatic_context_is_current(
        self,
        context: CodingCompactionSnapshot,
        automatic: AutomaticCompactionContext,
    ) -> bool:
        try:
            self.coding_state.validate_run_context(automatic.run_context)
        except CodingContextChangedError:
            return False
        return self._automatic_context_matches(context, automatic)

    def _select_automatic_cut(
        self,
        trigger: str,
        context: CodingCompactionSnapshot,
        cut: AgentHistoryCompaction,
        budget: RequestBudget | None,
        automatic: AutomaticCompactionContext,
    ) -> AgentHistoryCompaction | CodingCompactionOutcome:
        if (
            trigger != "auto"
            or budget is None
            or (
                automatic.baseline.provider_name != context.binding.provider_name
                or automatic.baseline.model_id != context.binding.model_id
            )
        ):
            return self._stale_compaction(trigger)
        try:
            retained_request = freeze_provider_request(
                replace(
                    automatic.baseline,
                    messages=automatic.active_input.request_messages(cut.messages),
                )
            )
        except ValueError:
            return self._stale_compaction(trigger)
        retained_estimate = estimate_request(
            retained_request,
            image_count=len(retained_request.attachments),
            output_reserve=budget.output_reserve,
        )
        if budget.allows(retained_estimate) is not False:
            return cut
        if (
            not cut.messages
            or cut.messages[0] is not automatic.active_input.accepted_message
        ):
            return cut
        cycle_cut = compact_agent_history_tool_cycles(
            cut.messages, accepted_user=automatic.active_input.accepted_message
        )
        return compound_compaction_cuts(cut, cycle_cut) if cycle_cut.changed else cut

    def _maybe_select_automatic_cut(
        self,
        trigger: str,
        context: CodingCompactionSnapshot,
        cut: AgentHistoryCompaction,
        budget: RequestBudget | None,
        automatic: AutomaticCompactionContext | None,
    ) -> AgentHistoryCompaction | CodingCompactionOutcome:
        if automatic is None:
            return cut
        return self._select_automatic_cut(trigger, context, cut, budget, automatic)

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
        fields: dict[str, str] = {}
        if action.retained_user_entry_id is not None:
            fields["retained_user_entry_id"] = action.retained_user_entry_id
        self.ctl.session_tree.append_compaction(
            summary=action.durable_summary.value.strip(),
            first_kept_entry_id=action.first_kept_entry_id,
            tokens_before=action.measure_before,
            **fields,
        )
