"""The state one ``run()`` shares: the mutable control holder and the frozen scope.

Two records and the two adapters that read them.

:class:`RunControlState` is the *mutable* half -- the handful of names a
built-in command reassigns (`/reload`, `/new`, `/resume`, `/fork`, `/clone`
rebind the session tree; the loop step rebinds `line`) and every other closure
must observe. It is one shared instance, never a copy: a cached tree is a
retired tree.

:class:`ReplLoopScope` is the *frozen* half -- the collaborators bound once per
run and never reassigned, travelling as one record instead of ~36 keyword
arguments. Keeping the two apart is what makes the mutability rule readable: if
it is on the scope it cannot change, and if it can change it is behind `ctl`.

The two status adapters bind the agent turn's status ports to those records
without letting the agent tier see either the concrete UI or the session.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent import AgentFailure, AgentMessage
from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    _AbortCallbackSignal,
)
from pipy_harness.native.agent_loop_policy import (
    NativeAgentProviderRequestPolicy,
    NativeAgentToolPolicy,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentQueuedInputPort,
    NativeAgentUsagePublisher,
)
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.session_controller import (
    CodingCommandEffects,
    CodingSessionController,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_runtime import ExtensionCodingSessionControl
from pipy_harness.native.package_runtime import PackageResourceRoots
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.repl.execution_projections import SessionExecutionProjections
from pipy_harness.native.repl.extension_operations import SessionExtensionOperations
from pipy_harness.native.repl_input import NativeReplInput
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer


@dataclass(slots=True)
class RunControlState:
    """Mutable holder for the control state a single ``run()`` invocation shares
    across its composition-root closures and the built-in command handler.

    Before this holder existed, these run-scope names were shared through a
    ~40-name ``nonlocal`` block reassigned by the built-in effect chain and read
    back by the REPL loop step and the extension/resource/persistence adapter
    closures. Routing them through one ``ctl`` instance removed those free-var
    captures so the built-in effects could be relocated into typed family owners
    routed by ``_BuiltinCommandInterpreter`` and the per-iteration loop step into
    ``_ReplLoopStep.step_once``. The effect owners and loop step receive ``ctl``
    explicitly and mutate it in place. It is deliberately a plain mutable record
    with no behavior: the handlers and closures reassign ``ctl.<attr>`` exactly
    where they previously rebound the ``nonlocal`` name, and a ``/reload``,
    ``/new``, ``/resume``, ``/fork``, or ``/clone`` rebind stays visible to every
    other closure through the shared instance.
    """

    coding_effects: CodingEffectCoordinator
    _session_tree: NativeSessionTree
    tree_filter_mode: str
    pending_prefill: str | None
    package_roots: PackageResourceRoots
    workspace_resources: WorkspaceResources
    generation_ref: SessionGenerationRef
    agent_settled_pending: bool
    extension_in_agent_turn: bool
    # ``line`` is (re)assigned by ``_ReplLoopStep.step_once`` before any read every
    # iteration;
    # the setup-scope changelog loop that reuses the name never seeds it here.
    line: str = ""

    def __post_init__(self) -> None:
        with self.coding_effects.lock:
            self._session_tree.bind_mutation_lock(self.coding_effects.lock)

    @property
    def session_tree(self) -> NativeSessionTree:
        with self.coding_effects.lock:
            return self._session_tree

    @session_tree.setter
    def session_tree(self, tree: NativeSessionTree) -> None:
        if not isinstance(tree, NativeSessionTree):
            raise TypeError("session_tree must be a NativeSessionTree")
        with self.coding_effects.lock:
            tree.bind_mutation_lock(self.coding_effects.lock)
            self._session_tree = tree

    @contextmanager
    def session_tree_section(self) -> Iterator[NativeSessionTree]:
        """Keep active-pointer selection and guarded tree work in one section."""

        with self.coding_effects.lock:
            yield self._session_tree

    @property
    def extension_generation(self) -> SessionExtensionGeneration:
        """The live extension generation, read under the session mutex.

        This is the per-access bridge retained for the pending R4c menu,
        lifecycle, and chrome consumers. R4a/R4b operation families instead use
        :meth:`SessionGenerationRef.snapshot` and never mix this bridge into a
        converted operation.
        """

        return self.generation_ref.current

    @extension_generation.setter
    def extension_generation(self, generation: SessionExtensionGeneration) -> None:
        """Publish a new generation under the session mutex.

        The value this replaces is deliberately kept alive until after the lock
        is released, so no finalizer runs inside the critical section.
        """

        retired = self.generation_ref.publish(generation)
        del retired


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTurnStatusStateAdapter:
    """Bind agent-turn status state ports at the product composition root."""

    ctl: RunControlState
    coding_state: CodingSessionState
    prompt_history_store: PromptHistoryStore
    prompt_for_recall: str | None

    def mark_run_entered(self) -> None:
        self.ctl.extension_in_agent_turn = True

    def record_input_accepted(self) -> None:
        self.coding_state.record_input_accepted()

    def record_prompt_recall(self, prompt: str, /) -> None:
        self.prompt_history_store.record(prompt)

    def sync_tool_policy(self, state: AgentToolPolicyState, /) -> None:
        self.coding_state.sync_tool_policy(state)

    def clear_provider_failure(self) -> None:
        self.coding_state.clear_provider_failure()

    def record_provider_failure(self, failure: AgentFailure, /) -> None:
        self.coding_state.record_provider_failure(failure)


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTurnStatusPresentationAdapter:
    """Bind status presentation ports without leaking the concrete UI inward."""

    terminal_ui: ToolLoopTerminalUi | None
    error_stream: TextIO
    refresh_legacy_footer_with_usage: Callable[[], None]

    def has_pending_input(self) -> bool:
        return (
            self.terminal_ui is not None
            and self.terminal_ui.pending_messages.has_pending_messages()
        )

    def promote_pending_input(self) -> None:
        if self.terminal_ui is not None:
            self.terminal_ui.pending_messages.promote_pending_to_drain()

    def restore_pending_input(self) -> None:
        if self.terminal_ui is not None:
            self.terminal_ui.pending_messages.restore_pending_to_editor()

    def emit_diagnostic(self, message: str, /) -> None:
        emit_diagnostic(
            self.terminal_ui,
            self.error_stream,
            message,
        )

    def refresh_usage_footer(self) -> None:
        self.refresh_legacy_footer_with_usage()


@dataclass(frozen=True, slots=True)
class ReplLoopScope:
    """The run-scope collaborators one REPL loop iteration reads.

    These values are bound once per ``NativeToolReplSession.run()`` and never
    reassigned for the life of that run, so they travel as one frozen record
    instead of ~36 separate keyword arguments threaded through
    ``functools.partial``. The run's *mutable* control state is deliberately
    not flattened into this record: it stays behind the ``ctl`` holder, so a
    ``/reload``, ``/new``, ``/resume``, ``/fork``, or ``/clone`` rebind is still
    observed by both the composition-root closures and :meth:`_ReplLoopStep.
    step_once` exactly as it was when the loop body was inline.

    The four scalars below (``abort_event``, ``file_reference_roots``,
    ``provider_state``, ``tool_budget``) are the last things the step reached
    through the session object. Each is set once when the adapter is configured,
    before the session is constructed, and never assigned afterwards, so
    carrying them by value here is what the step already observed -- and it is
    what lets this record leave a file the REPL tier may not import.
    """

    ctl: RunControlState
    loop_controller: CodingSessionController
    terminal_ui: ToolLoopTerminalUi | None
    error_stream: TextIO
    coding_state: CodingSessionState
    repl_input: "ToolLoopTerminalUi | NativeReplInput"
    renderer: "_ToolLoopRenderer | TuiToolLoopRenderer"
    emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter
    settings: SettingsManager
    cwd: Path
    started_at: datetime
    base_system_prompt: str
    # `image_reference_roots` is *derived* from `file_reference_roots` under a
    # different clipboard policy; both are consumed a dozen lines apart under
    # the same `reference_roots=` parameter name, so they keep distinct names.
    image_reference_roots: tuple[Path, ...]
    file_reference_roots: tuple[Path, ...]
    abort_event: "threading.Event | _AbortCallbackSignal | None"
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    tool_budget: int
    prompt_history_store: PromptHistoryStore
    execution_projections: SessionExecutionProjections
    agent_tool_policy: NativeAgentToolPolicy
    coding_input_queue: CodingInputQueue
    command_effects: CodingCommandEffects
    input_queued_input_port: NativeAgentQueuedInputPort | None
    provider_request_policy: NativeAgentProviderRequestPolicy
    provider_turn_executor: ProviderTurnExecutor
    usage_publisher: NativeAgentUsagePublisher
    extension_operations: SessionExtensionOperations
    diag: Callable[[str], None]
    coding_footer_text: Callable[[], str]
    refresh_legacy_footer_with_usage: Callable[[], None]
    apply_compaction: Callable[[str], str]
    cycle_thinking_level: Callable[[], str | None]
    append_agent_message: Callable[[AgentMessage], None]
    drain_extension_outboxes: Callable[[], None]
    active_provider_header_callback: Callable[
        [], Callable[[MutableMapping[str, str | None]], None] | None
    ]
    extension_custom_driver: Callable[..., object]
    extension_notify: Callable[[str, str], None]
    coding_session_control: Callable[[], ExtensionCodingSessionControl]
