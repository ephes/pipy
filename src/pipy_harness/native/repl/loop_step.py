"""One behavior-preserving REPL iteration and its lifecycle bookends.

The headless :class:`CodingSessionController` owns the outer loop. This module
owns the A--F phases of one accepted iteration and receives the run's frozen
collaborators through :class:`ReplLoopScope`. Mutable control always stays on the
single shared ``scope.ctl`` instance; no phase snapshots or copies it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from pipy_harness.models import HarnessStatus
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent import (
    AgentEventSink,
    AgentMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.history import should_compact_agent_history
from pipy_harness.native.agent.loop import AgentLoopRequestPreparation
from pipy_harness.native.agent.loop_policy import AgentProviderRequestPolicyInput
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnOutcome,
    ProviderTurnWaiter,
    _AbortCallbackSignal,
    _StartGatedProvider,
    _wait_for_external_abort,
)
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.runtime_ports import AgentQueuedInput
from pipy_harness.native.agent_loop_policy import materialize_provider_request
from pipy_harness.native.chrome import print_input_separator
from pipy_harness.native.coding.accepted_input import (
    CodingAcceptedInputPreparer,
    CodingAcceptedTurn,
    CodingSessionAcceptedInputRecorder,
)
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    CodingAgentRunCoordinator,
)
from pipy_harness.native.coding.commands import (
    CommandDispatchResolution,
    CommandDispatchResolutionKind,
)
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.result import (
    NativeToolReplResult,
    build_repl_result,
)
from pipy_harness.native.coding.session_controller import (
    CodingLoopStepKind,
    LoopStepSignal,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.coding.status_effects import CodingAgentTurnStatusEffects
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_runtime import (
    EVENT_SESSION_SHUTDOWN,
    EVENT_SESSION_START,
)
from pipy_harness.native.extensions.message_routing import GenerationMessageRetirement
from pipy_harness.native.file_references import (
    FileReferenceResolution,
    resolve_file_references,
)
from pipy_harness.native.image_attachment import (
    ImageAttachmentResolution,
    resolve_image_attachments,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.local_shell import run_local_shell_shortcut
from pipy_harness.native.repl.loop_scope import (
    AgentTurnStatusPresentationAdapter,
    AgentTurnStatusStateAdapter,
    ReplLoopScope,
    RunControlState,
)
from pipy_harness.native.repl.turn_leaves import (
    AGENT_HISTORY_KEEP_RECENT_GROUPS,
    AGENT_HISTORY_MAX_BYTES,
    AGENT_HISTORY_MAX_MESSAGES,
    finish_chrome_retirement,
    pricing_for,
    raise_first,
    wait_for_provider_interrupt,
    wait_for_tool_interrupt,
)
from pipy_harness.native.repl.view_actions import (
    cycle_thinking_level_action,
    toggle_view_fold,
)
from pipy_harness.native.repl_input import NativeReplInput
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.tools import ToolDefinition
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_CYCLE_NEXT,
    HOTKEY_MODEL_CYCLE_PREV,
    HOTKEY_MODEL_SELECT,
    HOTKEY_THINKING_CYCLE,
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
)


@dataclass(frozen=True, slots=True)
class _TurnScope:
    scope: ReplLoopScope
    footer_text: str


@dataclass(frozen=True, slots=True)
class _TurnInput:
    turn: _TurnScope
    selected_provider_content: ProductContent | None
    queued_input: AgentQueuedInput | None
    user_input: str
    stripped: str
    command_text: str
    from_hotkey: bool = False


@dataclass(frozen=True, slots=True)
class _AcceptedRun:
    turn_input: _TurnInput
    accepted_turn: CodingAcceptedTurn
    resource_provider_text: str | None


@dataclass(frozen=True, slots=True)
class _AcceptedInputEffects:
    turn: _TurnScope

    def transform_input(self, prompt: str) -> str:
        return self.turn.scope.extension_operations.dispatch_input(prompt)

    def resolve_file_references(self, prompt: str) -> FileReferenceResolution:
        scope = self.turn.scope
        return resolve_file_references(
            prompt,
            workspace_root=scope.cwd,
            reference_roots=scope.file_reference_roots,
        )

    def resolve_image_attachments(self, prompt: str) -> ImageAttachmentResolution:
        scope = self.turn.scope
        return resolve_image_attachments(
            prompt,
            workspace_root=scope.cwd,
            reference_roots=scope.image_reference_roots,
        )

    def system_prompt_suffix(self, base_prompt: str) -> str | None:
        result = self.turn.scope.extension_operations.dispatch_before_agent_start(
            base_prompt
        )
        return result.append_system_prompt

    def emit_diagnostic(self, message: str) -> None:
        scope = self.turn.scope
        emit_diagnostic(scope.terminal_ui, scope.error_stream, message)


@dataclass(frozen=True, slots=True)
class _RequestPreparationEffects:
    accepted: _AcceptedRun

    def prepare(
        self,
        history: tuple[AgentMessage, ...],
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
    ) -> AgentLoopRequestPreparation:
        scope = self.accepted.turn_input.turn.scope
        scope.coding_state.mirror_history(history)
        self._compact_if_needed()
        snapshot = scope.provider_request_policy.prepare(
            AgentProviderRequestPolicyInput(
                baseline=self._provider_request(
                    active_input, turn_index, available_tools
                ),
                active_input=active_input,
            )
        )
        scope.renderer.refresh_tool_renderers(
            scope.execution_projections.tool_renderers(snapshot.advertised_tool_names)
        )
        return AgentLoopRequestPreparation(scope.coding_state.messages, snapshot)

    def _compact_if_needed(self) -> None:
        scope = self.accepted.turn_input.turn.scope
        if not scope.settings.get_compaction_enabled():
            return
        if not should_compact_agent_history(
            scope.coding_state.messages,
            max_messages=AGENT_HISTORY_MAX_MESSAGES,
            max_bytes=AGENT_HISTORY_MAX_BYTES,
            keep_recent_groups=AGENT_HISTORY_KEEP_RECENT_GROUPS,
        ):
            return
        notice = scope.apply_compaction("auto")
        emit_diagnostic(scope.terminal_ui, scope.error_stream, notice)

    def _provider_request(
        self,
        active_input: AgentActiveInput,
        turn_index: int,
        available_tools: tuple[ToolDefinition, ...],
    ) -> ProviderRequest:
        scope = self.accepted.turn_input.turn.scope
        accepted_turn = self.accepted.accepted_turn
        coding_state = scope.coding_state
        return ProviderRequest(
            system_prompt=(
                accepted_turn.agent_system_prompt + coding_state.compaction_suffix
            ),
            user_prompt=accepted_turn.provider_user_input,
            provider_name=coding_state.provider_name,
            model_id=coding_state.model_id,
            cwd=scope.cwd,
            messages=active_input.request_messages(coding_state.messages),
            available_tools=available_tools,
            attachments=(accepted_turn.turn_attachments if turn_index == 0 else ()),
            provider_header_callback=scope.active_provider_header_callback(),
        )


@dataclass(frozen=True, slots=True)
class _ProviderTurnCompletion:
    turn: _TurnScope

    def complete(
        self,
        snapshot: AgentProviderRequestSnapshot,
        event_sink: AgentEventSink,
        turn_index: int,
    ) -> ProviderTurnOutcome:
        scope = self.turn.scope
        provider_request = materialize_provider_request(snapshot)
        provider_for_turn = scope.execution_projections.provider
        waiter: ProviderTurnWaiter | None = None
        if scope.terminal_ui is not None:
            waiter = partial(wait_for_provider_interrupt, scope.terminal_ui)
        elif scope.abort_event is not None:
            provider_for_turn, waiter = self._external_abort_turn(provider_for_turn)
        return scope.provider_turn_executor.complete(
            provider_for_turn,
            provider_request,
            event_sink,
            turn_index=turn_index,
            waiter=waiter,
        )

    def _external_abort_turn(
        self, provider_for_turn: ProviderPort
    ) -> tuple[ProviderPort, ProviderTurnWaiter]:
        scope = self.turn.scope
        abort_event = scope.abort_event
        assert abort_event is not None
        provider_start_event = None
        if isinstance(abort_event, _AbortCallbackSignal):
            provider_start_event = threading.Event()
            provider_for_turn = _StartGatedProvider(
                scope.coding_state.provider, provider_start_event
            )
        waiter = partial(
            _wait_for_external_abort,
            abort_event,
            provider_start_event,
        )
        return provider_for_turn, waiter


def _phase_a_unpack_and_prefill(scope: ReplLoopScope) -> _TurnScope:
    if scope.terminal_ui is None:
        print_input_separator(scope.error_stream)
    turn = _TurnScope(scope=scope, footer_text=scope.coding_footer_text())
    prefill = scope.ctl.pending_prefill
    if prefill is None:
        return turn
    if scope.terminal_ui is not None:
        scope.terminal_ui.input_editor.set_input_text(prefill)
    else:
        scope.diag(
            "pipy: editor rehydrated with selected message; "
            "type your (edited) message to branch from here, or "
            "submit as-is.\n"
            f"  > {prefill}"
        )
    scope.ctl.pending_prefill = None
    return turn


def _phase_b_intake(turn: _TurnScope) -> _TurnInput | LoopStepSignal:
    scope = turn.scope
    step = scope.loop_controller.select_next_step(
        settle_pending=scope.ctl.agent_settled_pending,
        drain_outbox=scope.drain_extension_outboxes,
        read_fresh_line=partial(
            scope.repl_input.read_line, "", footer=turn.footer_text
        ),
        input_queued_input_port=scope.input_queued_input_port,
    )
    scope.ctl.agent_settled_pending = step.settle_pending
    if step.kind is CodingLoopStepKind.EOF:
        if step.keyboard_interrupt:
            print(file=scope.error_stream)
        return LoopStepSignal.break_loop()
    scope.ctl.line = step.line
    selected = step.selected_provider_content
    user_input = selected.value if selected is not None else scope.ctl.line.rstrip("\n")
    stripped = user_input.strip()
    return _TurnInput(
        turn=turn,
        selected_provider_content=selected,
        queued_input=step.queued_input,
        user_input=user_input,
        stripped=stripped,
        command_text="" if selected is not None else stripped,
    )


def _phase_c1_hotkeys(turn_input: _TurnInput) -> _TurnInput | LoopStepSignal:
    command_text = turn_input.command_text
    if command_text in {HOTKEY_TOGGLE_TOOLS, HOTKEY_TOGGLE_THINKING}:
        _toggle_view_hotkey(turn_input)
        return LoopStepSignal.continue_loop()
    if command_text == HOTKEY_THINKING_CYCLE:
        _cycle_thinking_hotkey(turn_input)
        return LoopStepSignal.continue_loop()
    if command_text.startswith(HOTKEY_EXTENSION_SHORTCUT_PREFIX):
        _dispatch_extension_hotkey(turn_input)
        return LoopStepSignal.continue_loop()
    if command_text not in {
        HOTKEY_MODEL_CYCLE_NEXT,
        HOTKEY_MODEL_CYCLE_PREV,
        HOTKEY_MODEL_SELECT,
    }:
        return turn_input
    translated = _translate_model_hotkey(command_text)
    return _TurnInput(
        turn=turn_input.turn,
        selected_provider_content=turn_input.selected_provider_content,
        queued_input=turn_input.queued_input,
        user_input=translated,
        stripped=translated,
        command_text=translated,
        from_hotkey=True,
    )


def _toggle_view_hotkey(turn_input: _TurnInput) -> None:
    scope = turn_input.turn.scope
    toggle_view_fold(
        turn_input.stripped,
        terminal_ui=scope.terminal_ui,
        error_stream=scope.error_stream,
        settings=scope.settings,
    )


def _cycle_thinking_hotkey(turn_input: _TurnInput) -> None:
    scope = turn_input.turn.scope
    cycle_thinking_level_action(
        scope.provider_state,
        terminal_ui=scope.terminal_ui,
        error_stream=scope.error_stream,
        cycle_thinking_level=scope.cycle_thinking_level,
    )
    scope.refresh_legacy_footer_with_usage()


def _dispatch_extension_hotkey(turn_input: _TurnInput) -> None:
    scope = turn_input.turn.scope
    shortcut_key = turn_input.command_text[len(HOTKEY_EXTENSION_SHORTCUT_PREFIX) :]
    dispatch = scope.extension_operations.dispatch_shortcut(
        shortcut_key,
        coding_session=scope.coding_session_control(),
        ui_custom_driver=scope.extension_custom_driver,
    )
    if dispatch is None or dispatch.ran or not dispatch.error:
        return
    emit_diagnostic(
        scope.terminal_ui,
        scope.error_stream,
        f"pipy: extension shortcut {shortcut_key!r} failed ({dispatch.error})",
    )


def _translate_model_hotkey(command_text: str) -> str:
    if command_text == HOTKEY_MODEL_SELECT:
        return "/model"
    if command_text == HOTKEY_MODEL_CYCLE_NEXT:
        return "/scoped-models next"
    return "/scoped-models prev"


def _phase_c2_shell(turn_input: _TurnInput) -> LoopStepSignal | None:
    if not turn_input.command_text.startswith("!"):
        return None
    scope = turn_input.turn.scope
    hooks, flags, ui_driver, model_runtime = (
        scope.extension_operations.user_bash_inputs()
    )
    shell_context_text = run_local_shell_shortcut(
        turn_input.stripped,
        terminal_ui=scope.terminal_ui,
        error_stream=scope.error_stream,
        cwd=scope.cwd,
        user_bash_hooks=hooks,
        model_runtime=model_runtime,
        ui_driver=ui_driver,
        flags=flags,
        project_trusted=scope.settings.project_trusted,
    )
    if shell_context_text is not None:
        scope.append_agent_message(
            AgentUserMessage(content=ProductContent(shell_context_text))
        )
    scope.refresh_legacy_footer_with_usage()
    return LoopStepSignal.continue_loop()


def _phase_d_dispatch(
    turn_input: _TurnInput,
) -> CommandDispatchResolution | LoopStepSignal:
    scope = turn_input.turn.scope
    if turn_input.stripped and not turn_input.from_hotkey:
        scope.renderer.render_user_message(turn_input.user_input)
    resolution = scope.loop_controller.dispatch_command(
        command_text=turn_input.command_text,
        stripped=turn_input.stripped,
        user_input=turn_input.user_input,
        selected_provider_content=turn_input.selected_provider_content,
        effects=scope.command_effects,
    )
    if resolution.kind is CommandDispatchResolutionKind.EXIT_LOOP:
        return LoopStepSignal.break_loop()
    if resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP:
        return LoopStepSignal.continue_loop()
    return resolution


def _phase_e_accepted_input(
    turn_input: _TurnInput,
    resolution: CommandDispatchResolution,
) -> _AcceptedRun:
    scope = turn_input.turn.scope
    effects = _AcceptedInputEffects(turn_input.turn)
    accepted_turn = CodingAcceptedInputPreparer(
        transform_input=effects.transform_input,
        resolve_file_references=effects.resolve_file_references,
        resolve_image_attachments=effects.resolve_image_attachments,
        system_prompt_suffix=effects.system_prompt_suffix,
        next_turn_context=scope.coding_input_queue.take_next_turn_context,
        emit_diagnostic=effects.emit_diagnostic,
        state_recorder=CodingSessionAcceptedInputRecorder(
            scope.coding_state, tool_budget=scope.tool_budget
        ),
    ).prepare(
        user_input=resolution.user_input,
        resource_provider_text=resolution.resource_provider_text,
        selected_provider_content=resolution.selected_provider_content,
        base_system_prompt=scope.base_system_prompt,
    )
    return _AcceptedRun(
        turn_input=turn_input,
        accepted_turn=accepted_turn,
        resource_provider_text=resolution.resource_provider_text,
    )


def _phase_f1_assemble(accepted: _AcceptedRun) -> CodingAgentRunCoordinator:
    scope = accepted.turn_input.turn.scope
    request_effects = _RequestPreparationEffects(accepted)
    provider_effects = _ProviderTurnCompletion(accepted.turn_input.turn)
    status_effects = CodingAgentTurnStatusEffects(
        state=AgentTurnStatusStateAdapter(
            ctl=scope.ctl,
            coding_state=scope.coding_state,
            prompt_history_store=scope.prompt_history_store,
            prompt_for_recall=(
                accepted.turn_input.user_input
                if accepted.resource_provider_text is None
                else None
            ),
        ),
        presentation=AgentTurnStatusPresentationAdapter(
            terminal_ui=scope.terminal_ui,
            error_stream=scope.error_stream,
            refresh_legacy_footer_with_usage=(scope.refresh_legacy_footer_with_usage),
        ),
    )
    tool_waiter = (
        None
        if scope.terminal_ui is None
        else partial(wait_for_tool_interrupt, scope.terminal_ui)
    )
    return CodingAgentRunCoordinator(
        request_source=AgentLoopRequestSourceAdapter(request_effects.prepare),
        provider_turn=AgentLoopProviderTurnAdapter(provider_effects.complete),
        status_policy=status_effects,
        tool_capabilities=scope.execution_projections,
        tool_policy=scope.agent_tool_policy,
        event_sink=scope.emitter,
        usage_publisher=scope.usage_publisher,
        queued_input_port=scope.coding_input_queue.agent_loop_port,
        coding_state=scope.coding_state,
        retain_next_input=scope.coding_input_queue.retain_agent_input,
        tool_waiter=tool_waiter,
    )


def _phase_f2_run_and_settle(
    accepted: _AcceptedRun,
    coordinator: CodingAgentRunCoordinator,
) -> LoopStepSignal:
    scope = accepted.turn_input.turn.scope
    scope.ctl.agent_settled_pending = True
    outcome = coordinator.run_turn(
        accepted.accepted_turn.active_input,
        accepted.accepted_turn.initial_tool_state,
        pricing=pricing_for(
            scope.coding_state.provider_name,
            scope.coding_state.model_id,
        ),
        accepted_queued_input=accepted.turn_input.queued_input,
    )
    scope.ctl.extension_in_agent_turn = False
    if not outcome.terminate_session:
        return LoopStepSignal.continue_loop()
    failure = outcome.result.failure
    assert failure is not None
    result_snapshot = scope.coding_state.result_snapshot()
    ended_at = datetime.now(UTC)
    try:
        scope.repl_input.close()
    except Exception:  # noqa: BLE001 - the run failure retains precedence
        pass
    return LoopStepSignal.return_result(
        build_repl_result(
            result_snapshot,
            status=HarnessStatus.FAILED,
            exit_code=1,
            started_at=scope.started_at,
            ended_at=ended_at,
            error_type=failure.error_type,
            error_message=failure.message.value,
        )
    )


class _ReplLoopStep:
    """Stateless owner of one REPL iteration and its lifecycle bookends."""

    __slots__ = ()

    def step_once(self, *, scope: ReplLoopScope) -> LoopStepSignal:
        turn = _phase_a_unpack_and_prefill(scope)
        intake = _phase_b_intake(turn)
        if isinstance(intake, LoopStepSignal):
            return intake
        hotkey = _phase_c1_hotkeys(intake)
        if isinstance(hotkey, LoopStepSignal):
            return hotkey
        shell_signal = _phase_c2_shell(hotkey)
        if shell_signal is not None:
            return shell_signal
        dispatch = _phase_d_dispatch(hotkey)
        if isinstance(dispatch, LoopStepSignal):
            return dispatch
        accepted = _phase_e_accepted_input(hotkey, dispatch)
        coordinator = _phase_f1_assemble(accepted)
        return _phase_f2_run_and_settle(accepted, coordinator)

    def finalize(
        self,
        *,
        coding_state: CodingSessionState,
        repl_input: "ToolLoopTerminalUi | NativeReplInput",
        started_at: datetime,
    ) -> NativeToolReplResult:
        try:
            repl_input.close()
        except Exception:  # noqa: BLE001 - teardown close is best-effort
            pass
        ended_at = datetime.now(UTC)
        result_snapshot = coding_state.result_snapshot()
        return build_repl_result(
            result_snapshot,
            status=HarnessStatus.SUCCEEDED,
            exit_code=0,
            started_at=started_at,
            ended_at=ended_at,
        )

    def fire_session_start(
        self, *, emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter
    ) -> None:
        emitter.fire_lifecycle(EVENT_SESSION_START, reason="startup")

    def fire_session_shutdown(
        self, *, emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter
    ) -> None:
        emitter.fire_lifecycle(EVENT_SESSION_SHUTDOWN)

    def consume_settle_pending(self, *, ctl: RunControlState) -> bool:
        if ctl.agent_settled_pending:
            ctl.agent_settled_pending = False
            return True
        return False

    def close_extension_session(
        self,
        *,
        coding_effects: CodingEffectCoordinator,
        generation_ref: SessionGenerationRef,
    ) -> None:
        """Close effect admission and detach terminal generation sidecars."""

        queue_retirement = GenerationMessageRetirement()
        generation: SessionExtensionGeneration | None = None
        chrome: ExtensionChromeHandle | None = None
        with coding_effects.terminal_section() as first_close:
            if first_close:
                with generation_ref.lock:
                    generation, chrome = generation_ref.detach_terminal_locked(
                        queue_retirement
                    )
        if not first_close:
            return

        retained: tuple[object, ...] = ()
        queue_error: BaseException | None = None
        try:
            retained = queue_retirement.finalize_retirement()
        except BaseException as error:  # noqa: BLE001 - aggregate teardown errors
            queue_error = error
        chrome_retirement, chrome_close_error = (
            chrome.close_nonraising() if chrome is not None else (None, None)
        )
        chrome_finalize_error = finish_chrome_retirement(chrome_retirement)
        del retained, generation
        raise_first((queue_error, chrome_close_error, chrome_finalize_error))

    def clear_extension_chrome(self, *, terminal_ui: ToolLoopTerminalUi | None) -> None:
        if terminal_ui is not None:
            terminal_ui.clear_extension_chrome()
