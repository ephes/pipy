"""Bounded model-driven REPL session skeleton.

Slice 4 of the Tool-Loop Parity Track introduces a small `NativeToolReplSession`
class that wires the slice 2 contracts (`ToolDefinition`, `ToolRequest`,
`ToolExecutionResult`, `ToolPort`, `ToolContext`, `validate_arguments`) and the
slice 3 provider extension (`ProviderPort.supports_tool_calls`,
`ProviderToolCall`, `ProviderResult.tool_calls`) into a real turn loop.

The session is the product REPL behind `pipy repl --agent pipy-native`. It runs
the exact production tool registry (`read`, `ls`, `grep`, `find`, `write`,
`edit`, `bash`); tests may inject a `_FixtureTool` through the registry
argument to verify loop behavior in isolation.

Invariants pinned by the focused tests:

- The session refuses providers that do not advertise
  `supports_tool_calls=True`.
- `--tool-budget` is bounded to `[1, 200]`; the constructor validates the
  value.
- Each user turn allows at most `tool_budget` tool invocations; subsequent
  model-emitted calls receive a deterministic "tool budget exhausted"
  observation.
- Malformed tool calls are authorized calls whose arguments fail JSON decoding
  or schema validation. They are returned as canonical error tool-result
  observations and increment a streak counter; three consecutive malformed
  calls end the loop with a deterministic stderr diagnostic.
- Calls to unknown or out-of-snapshot tools are returned as budget-consuming
  policy errors and neither increment nor reset the malformed-call streak.
- One valid tool invocation resets the malformed streak, even if the tool
  returns an execution error such as a failed read or timed-out shell command.
- The session does not write prompts, model text, tool payloads, file
  contents, or diffs to the archive; only safe counters and labels.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import ClassVar, TextIO

import pipy_harness.native.chrome as _chrome
import pipy_harness.native.tool_renderers as _tool_renderers
from pipy_harness.models import HarnessStatus
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent import (
    AgentEventSink,
    AgentMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.history import (
    should_compact_agent_history,
)
from pipy_harness.native.agent.loop import (
    AgentLoopRequestPreparation,
)
from pipy_harness.native.agent.loop_policy import (
    MAX_AGENT_TOOL_BUDGET,
    AgentProviderRequestPolicyInput,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    ProviderTurnOutcome,
    _AbortCallbackSignal,
    _StartGatedProvider,
    _wait_for_external_abort,
)
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.agent_adapters import (
    NativeProductSessionActionSink as NativeProductSessionActionSink,
)
from pipy_harness.native.agent_adapters import (
    ProductSessionEventProjection,
    SynchronousAgentEventComposite,
    WorkflowArchiveAgentEventAdapter,
)
from pipy_harness.native.agent_loop_policy import (
    NativeAgentProviderRequestPolicy,
    NativeAgentToolPolicy,
    materialize_provider_request,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentQueuedInputPort as NativeAgentQueuedInputPort,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentUsagePublisher as NativeAgentUsagePublisher,
)
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.automation.events import (
    AutomationEventSink,
)
from pipy_harness.native.changelog import (
    changelog_startup,
    read_changelog_entries,
)
from pipy_harness.native.chrome import (
    BottomStatusFields,
    _ChromeFooterEffects,
    chrome_width,
    format_bottom_status_line,
    print_bottom_status_block,
    print_input_separator,
    print_startup_chrome,
)
from pipy_harness.native.clipboard import (
    ClipboardResult,
    ImageClipboardResult,
    copy_to_clipboard,
    read_clipboard_image,
)
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.accepted_input import (
    CodingAcceptedInputPreparer,
    CodingSessionAcceptedInputRecorder,
)
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    CodingAgentRunCoordinator,
)
from pipy_harness.native.coding.commands import (
    CommandDispatchResolutionKind,
)
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.product_session import (
    CodingProductSessionCallbacks,
    CodingProductSessionCompaction,
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
)
from pipy_harness.native.coding.result import (
    NativeToolReplResult,
    build_repl_result,
)
from pipy_harness.native.coding.session_controller import (
    CodingCommandEffects,
    CodingLoopStepKind,
    CodingSessionController,
    LoopStepSignal,
    _CallableCodingCommandEffects,
)
from pipy_harness.native.coding.state import (
    CodingSessionState,
    CodingSessionUsageSnapshot,
)
from pipy_harness.native.coding.status_effects import CodingAgentTurnStatusEffects
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_hooks import (
    _activate_workspace_extensions,
)
from pipy_harness.native.extension_hooks import (
    dispatch_session_before_hooks as dispatch_session_before_hooks,
)
from pipy_harness.native.extension_runtime import (
    EVENT_SESSION_SHUTDOWN,
    EVENT_SESSION_START,
    QueuedCustomMessage,
    QueuedUserMessage,
    _ExtensionCandidate,
    normalize_shortcut_key,
)
from pipy_harness.native.extensions.contracts import ExtensionActivationBatch
from pipy_harness.native.extensions.flag_tokens import parse_extension_flag_tokens
from pipy_harness.native.extensions.message_routing import GenerationMessageRetirement
from pipy_harness.native.file_references import (
    FileReferenceResolution,
    resolve_file_references,
)
from pipy_harness.native.image_attachment import (
    ImageAttachmentResolution,
    resolve_image_attachments,
)
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.package_runtime import (
    compose_package_runtime,
)
from pipy_harness.native.project_trust import (
    has_trust_requiring_project_resources,
)
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.collaborators import (
    BuiltinCommandInterpreter,
    SessionCollaborators,
)
from pipy_harness.native.repl.command_menu import (
    published_command_surface,
    tool_loop_command_descriptions,
    tool_loop_command_names,
)
from pipy_harness.native.repl.execution_projections import (
    SessionExecutionProjections,
    apply_startup_provider_projection,
    build_candidate_extension_projection,
)
from pipy_harness.native.repl.extension_operations import (
    SessionExtensionOperations,
)
from pipy_harness.native.repl.local_shell import run_local_shell_shortcut
from pipy_harness.native.repl.loop_scope import (
    AgentTurnStatusPresentationAdapter,
    AgentTurnStatusStateAdapter,
    ReplLoopScope,
    RunControlState,
)
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.reload import (
    ImplicitTrustState,
)
from pipy_harness.native.repl.turn_leaves import (
    AGENT_HISTORY_KEEP_RECENT_GROUPS,
    AGENT_HISTORY_MAX_BYTES,
    AGENT_HISTORY_MAX_MESSAGES,
    CANCEL_JOIN_TIMEOUT_SECONDS,
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
from pipy_harness.native.repl_input import (
    REPL_INPUT_RUNTIME_AUTO,
    NativeReplInput,
    native_repl_input_for,
)
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import (
    WorkspaceResources,
)
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    FrozenStagedDeliveryBatch,
    OrderedDeliveryGate,
    PreparedReloadEffects,
    ReloadEffectPreparationPorts,
    ReloadPreparationObserver,
    SessionExtensionGeneration,
    SessionGenerationRef,
    balance_startup_candidate,
    build_prepared_reload_effects,
    publish_candidate_ownership,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_status_line,
)
from pipy_harness.native.session_tree import (
    NativeSessionTree,
)
from pipy_harness.native.session_tree_commands import (
    sanitize_label_text,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tool_renderers import (
    _ToolLoopRenderer as _ToolLoopRenderer,
)
from pipy_harness.native.tools import (
    ToolDefinition,
    ToolPort,
)
from pipy_harness.native.tools.registry import production_tool_registry
from pipy_harness.native.tui import (
    TURN_ABORTED as TURN_ABORTED,
)
from pipy_harness.native.tui import ToolLoopTerminalUi, _LiveExtensionUiDriver
from pipy_harness.native.ui import RenderingAgentEventAdapter
from pipy_harness.native.ui.clipboard_images import create_clipboard_config
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_CYCLE_NEXT,
    HOTKEY_MODEL_CYCLE_PREV,
    HOTKEY_MODEL_SELECT,
    HOTKEY_THINKING_CYCLE,
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
)
from pipy_harness.native.ui.components.custom_entry_renderer import CustomEntryRenderer
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.version_check import pipy_version


@dataclass(frozen=True, slots=True, kw_only=True)
class _ExtensionCustomEntryRunState:
    """Narrow TUI adapter over the canonical extension generation.

    ``CustomEntryRenderer`` intentionally accepts a structural live-state
    protocol. Keep that UI boundary cycle-free while forwarding every
    extension-owned value to the generation rather than mirroring it on run
    control.
    """

    ctl: RunControlState

    @property
    def session_tree(self) -> NativeSessionTree:
        return self.ctl.session_tree

    @property
    def extension_message_outbox(self) -> list[QueuedUserMessage]:
        return self.ctl.extension_generation.runtime.outbox

    @property
    def extension_custom_message_outbox(self) -> list[QueuedCustomMessage]:
        return self.ctl.extension_generation.runtime.custom_outbox

    @property
    def extension_in_agent_turn(self) -> bool:
        return self.ctl.extension_in_agent_turn


def _build_detached_reload_effects(
    ports: ReloadEffectPreparationPorts,
    *,
    step_observer: ReloadPreparationObserver | None = None,
) -> PreparedReloadEffects:
    """Pure R3b adapter; production startup/reload intentionally never calls it."""

    return build_prepared_reload_effects(ports, step_observer=step_observer)


class _ReplLoopStep:
    """Composition-root handler that owns one REPL loop iteration and the
    loop's lifecycle bookends.

    Symmetric with :class:`_BuiltinCommandInterpreter`: the headless controller
    (:meth:`CodingSessionController.run_loop`) owns the ``while True`` skeleton
    and reaches this handler through the injected ``step_once``/``finalize``/
    ``fire_session_start``/``fire_session_shutdown``/``consume_settle_pending``/
    ``clear_extension_chrome`` ports. :meth:`step_once` performs exactly one
    iteration and returns only the routing :class:`LoopStepSignal`; the bookend
    methods build the terminal projections and fire the lifecycle effects. The
    handler holds no state of its own (``__slots__ = ()``); it receives the
    stable run-scope collaborators as one frozen :class:`ReplLoopScope` record,
    reaches the run's mutable control-state holder through ``scope.ctl``, and
    mutates that holder in place, so the composition-root closures read the
    reassigned loop control flags back byte-identically. Only genuinely
    per-run-constant values belong in the scope; anything a command may rebind
    stays behind ``ctl``. The bodies formerly lived as the ``_repl_step`` closure
    (with its nested ``_prepare_loop_request``) and the ``_finalize_repl_loop``/
    ``_fire_session_start``/``_fire_session_shutdown``/
    ``_consume_agent_settled_pending``/``_clear_extension_chrome_after_run``
    bookends nested in ``NativeToolReplSession.run()``.
    """

    __slots__ = ()

    def step_once(self, *, scope: ReplLoopScope) -> LoopStepSignal:
        # Unpacked once into locals so the 460-line body below reads the
        # run-scope collaborators by their own names, exactly as it did when
        # they arrived as keyword arguments.
        ctl = scope.ctl
        loop_controller = scope.loop_controller
        terminal_ui = scope.terminal_ui
        error_stream = scope.error_stream
        coding_state = scope.coding_state
        repl_input = scope.repl_input
        renderer = scope.renderer
        emitter = scope.emitter
        settings = scope.settings
        cwd = scope.cwd
        started_at = scope.started_at
        base_system_prompt = scope.base_system_prompt
        image_reference_roots = scope.image_reference_roots
        file_reference_roots = scope.file_reference_roots
        abort_event = scope.abort_event
        provider_state = scope.provider_state
        tool_budget = scope.tool_budget
        prompt_history_store = scope.prompt_history_store
        execution_projections = scope.execution_projections
        agent_tool_policy = scope.agent_tool_policy
        coding_input_queue = scope.coding_input_queue
        command_effects = scope.command_effects
        input_queued_input_port = scope.input_queued_input_port
        provider_request_policy = scope.provider_request_policy
        provider_turn_executor = scope.provider_turn_executor
        usage_publisher = scope.usage_publisher
        extension_operations = scope.extension_operations
        diag = scope.diag
        coding_footer_text = scope.coding_footer_text
        refresh_legacy_footer_with_usage = scope.refresh_legacy_footer_with_usage
        apply_compaction = scope.apply_compaction
        cycle_thinking_level = scope.cycle_thinking_level
        append_agent_message = scope.append_agent_message
        drain_extension_outboxes = scope.drain_extension_outboxes
        _active_provider_header_callback = scope.active_provider_header_callback
        _extension_custom_driver = scope.extension_custom_driver
        _extension_notify = scope.extension_notify
        coding_session_control = scope.coding_session_control
        # Per-action built-in control-state reassignments (session tree, tree
        # filter mode, prefill, and the whole `/reload` extension-runtime
        # bundle) now live in typed effect-family owners routed through
        # `_BuiltinCommandInterpreter`; this step only reassigns its own loop
        # control flags plus input/agent-turn bookkeeping, all through ``ctl``.
        if terminal_ui is None:
            print_input_separator(error_stream)
        footer_text = coding_footer_text()
        if ctl.pending_prefill is not None:
            # A ``/tree`` user-message selection puts the chosen text back
            # into the editor. The live TUI rehydrates the editor directly;
            # captured-stream callers see a hint and type the (edited) text
            # as the next line, which branches from the selected parent.
            if terminal_ui is not None:
                terminal_ui.input_editor.set_input_text(ctl.pending_prefill)
            elif terminal_ui is None:
                diag(
                    "pipy: editor rehydrated with selected message; "
                    "type your (edited) message to branch from here, or "
                    "submit as-is.\n"
                    f"  > {ctl.pending_prefill}"
                )
            ctl.pending_prefill = None
        # Input selection and the true-idle (`agent_settled`) boundary are
        # owned by the headless controller. It drains any messages an
        # extension enqueued via send_user_message at the top of every
        # iteration (so they are scheduled as deterministic prompts
        # regardless of which callback queued them), takes one queued input
        # using the product priority, fires the once-only `agent_settled`
        # notification and re-polls when nothing is pending, and otherwise
        # reads one fresh line — with Pi's cursor-only prompt (the separator
        # pair frames the input area) — and applies the external-wake
        # overlay. A local command (`/…`/`!…`) submitted mid-turn still
        # dispatches through the NORMAL path below; queued provider content
        # bypasses local dispatch. The returned step carries the exact line
        # the loop consumes and the post-boundary settled flag.
        step = loop_controller.select_next_step(
            settle_pending=ctl.agent_settled_pending,
            drain_outbox=drain_extension_outboxes,
            read_fresh_line=lambda: repl_input.read_line("", footer=footer_text),
            input_queued_input_port=input_queued_input_port,
        )
        ctl.agent_settled_pending = step.settle_pending
        selected_provider_content: ProductContent | None = (
            step.selected_provider_content
        )
        queued_input: AgentQueuedInput | None = step.queued_input
        if step.kind is CodingLoopStepKind.EOF:
            if step.keyboard_interrupt:
                print(file=error_stream)
            return LoopStepSignal.break_loop()
        ctl.line = step.line
        user_input = (
            selected_provider_content.value
            if selected_provider_content is not None
            else ctl.line.rstrip("\n")
        )
        stripped = user_input.strip()
        # Queued steering/follow-up messages (Pi) are provider-visible prompt
        # text, never local commands: a follow-up enqueued mid-turn that
        # happens to begin with `/` (slash command) or `!` (bash shortcut)
        # must reach the model verbatim, not be intercepted and silently
        # dropped from the conversation. ``command_text`` is the dispatch key
        # for every local command/hotkey below; it is blank for a drained
        # line or for RPC input carrying a closed delivery classification,
        # so neither can match and both fall through to the provider-message
        # path (which still resolves any @file/@image references). Ordinary
        # typed input keeps ``command_text == stripped`` and is unaffected.
        command_text = "" if selected_provider_content is not None else stripped
        # In-editor hotkeys arrive as private sentinel "commands" from the
        # TUI so they dispatch without rendering a user-message bubble.
        # Shift+Tab cycles the thinking level; Ctrl+P / Shift+Ctrl+P cycle
        # the model (translated to the existing /scoped-models dispatch).
        if command_text in {HOTKEY_TOGGLE_TOOLS, HOTKEY_TOGGLE_THINKING}:
            toggle_view_fold(
                stripped,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                settings=settings,
            )
            return LoopStepSignal.continue_loop()
        if command_text == HOTKEY_THINKING_CYCLE:
            cycle_thinking_level_action(
                provider_state,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                cycle_thinking_level=cycle_thinking_level,
            )
            refresh_legacy_footer_with_usage()
            return LoopStepSignal.continue_loop()
        if command_text.startswith(HOTKEY_EXTENSION_SHORTCUT_PREFIX):
            # An activated extension's registered keyboard shortcut
            # fired; dispatch its handler with the same mode-aware
            # context as its command. Like the command path, a handler
            # that calls api.send_user_message enqueues to the shared
            # outbox, which is drained into a deterministic provider
            # prompt at the top of the next iteration (see the
            # drain_user_messages call above) — so the turn fires; this
            # branch only needs to surface a handler failure and
            # continue. Covered by
            # test_shortcut_send_user_message_triggers_a_turn.
            shortcut_key = command_text[len(HOTKEY_EXTENSION_SHORTCUT_PREFIX) :]
            shortcut_dispatch = extension_operations.dispatch_shortcut(
                shortcut_key,
                coding_session=coding_session_control(),
                ui_custom_driver=_extension_custom_driver,
            )
            if (
                shortcut_dispatch is not None
                and not shortcut_dispatch.ran
                and shortcut_dispatch.error
            ):
                emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    (
                        f"pipy: extension shortcut {shortcut_key!r} "
                        f"failed ({shortcut_dispatch.error})"
                    ),
                )
            return LoopStepSignal.continue_loop()
        from_hotkey = command_text in {
            HOTKEY_MODEL_CYCLE_NEXT,
            HOTKEY_MODEL_CYCLE_PREV,
            HOTKEY_MODEL_SELECT,
        }
        if from_hotkey:
            stripped = (
                "/model"
                if command_text == HOTKEY_MODEL_SELECT
                else (
                    "/scoped-models next"
                    if command_text == HOTKEY_MODEL_CYCLE_NEXT
                    else "/scoped-models prev"
                )
            )
            user_input = stripped
            # Keep the dispatch key in sync with the translated command so
            # the /scoped-models handler below matches (a hotkey is never a
            # drained line, so this only rewrites typed-hotkey input).
            command_text = stripped
        # Local shell shortcut: a submitted line whose first non-space
        # character is ``!`` runs a bash command from the editor with no
        # provider turn (Pi's ``handleBashCommand``). ``!cmd`` records the
        # command/output into the conversation context and native session
        # tree so the next turn and resume see it; ``!!cmd`` runs identically
        # but is excluded from context (a live-only diagnostic). Escape
        # cancels a running command. Intercepted before the user-message
        # panel so it renders as a shell block, not a chat bubble.
        if command_text.startswith("!"):
            (
                user_bash_hooks,
                user_bash_flags,
                user_bash_ui,
                user_bash_model_runtime,
            ) = extension_operations.user_bash_inputs()
            shell_context_text = run_local_shell_shortcut(
                stripped,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                cwd=cwd,
                user_bash_hooks=user_bash_hooks,
                model_runtime=user_bash_model_runtime,
                ui_driver=user_bash_ui,
                flags=user_bash_flags,
                project_trusted=settings.project_trusted,
            )
            if shell_context_text is not None:
                shell_message = AgentUserMessage(
                    content=ProductContent(shell_context_text)
                )
                append_agent_message(shell_message)
            refresh_legacy_footer_with_usage()
            return LoopStepSignal.continue_loop()
        # Pi paints the submitted user message back on a muted
        # `userMessageBg` panel — distinct from the green tool
        # panel — so the prompt reads as a chat bubble. Overwrite
        # the readline echo line with the styled panel row when
        # the renderer can drive ANSI cursor controls.
        if stripped and not from_hotkey:
            renderer.render_user_message(user_input)
        # The built-in>resource>extension command-dispatch precedence is
        # owned by the headless controller: it classifies built-ins first
        # (`/exit`/`/quit` -> EXIT_LOOP breaks the loop; every other
        # continuing built-in is interpreted through the injected
        # `command_effects.interpret_builtin` port — closed family routing in
        # `_BuiltinCommandInterpreter.interpret` — and resolves to
        # CONTINUE_LOOP), then resource dispatch (list/reject consumed
        # locally; run records the invocation counter and carries the bounded
        # provider text), then extension dispatch (never shadowing a built-in
        # or resource), then the unhandled `/…` fallback — each effect
        # performed through the injected `command_effects` port.
        # Queued/provider content has a blank `command_text` and falls
        # straight through to PROCEED_TO_RUN.
        resolution = loop_controller.dispatch_command(
            command_text=command_text,
            stripped=stripped,
            user_input=user_input,
            selected_provider_content=selected_provider_content,
            effects=command_effects,
        )
        if resolution.kind is CommandDispatchResolutionKind.EXIT_LOOP:
            return LoopStepSignal.break_loop()
        if resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP:
            return LoopStepSignal.continue_loop()
        resource_provider_text: str | None = resolution.resource_provider_text

        # User-directed file context: a genuine prompt may name workspace
        # files with ``@path``. Resolve them through the shared bounded
        # reader (reusing this loop's ``read`` policy and reference roots),
        # append the bounded excerpts to the provider-visible user message,
        # and keep the literal prompt for the rendered panel, prompt
        # history, and native product session tree. None of that content
        # enters the metadata-only workflow archive.
        # Accepted-input preparation owns the resource-vs-literal
        # branch, the transformed-vs-original prompt split, the hook
        # ordering (input hook, @file resolution, image attachments,
        # then before_agent_start augmentation), diagnostic text, and
        # safe-counter recording. The controller supplies only thin
        # adapters over its effectful helpers; the provider-visible
        # excerpts, image bytes, transformed text, and injected
        # system-prompt context ride the returned turn and never enter
        # the metadata-only workflow archive.
        def _transform_accepted_input(prompt: str) -> str:
            return extension_operations.dispatch_input(prompt)

        def _resolve_accepted_file_references(
            prompt: str,
        ) -> FileReferenceResolution:
            return resolve_file_references(
                prompt,
                workspace_root=cwd,
                reference_roots=file_reference_roots,
            )

        def _resolve_accepted_image_attachments(
            prompt: str,
        ) -> ImageAttachmentResolution:
            # User-directed image attachments (@image:<path>): bounded,
            # fail-closed image loading that becomes provider-visible
            # image blocks on the current user message. Raw bytes never
            # reach prompt history, the native product session tree, the
            # metadata-only workflow archive, or the result.
            return resolve_image_attachments(
                prompt,
                workspace_root=cwd,
                reference_roots=image_reference_roots,
            )

        def _accepted_system_prompt_suffix(base_prompt: str) -> str | None:
            before_agent_result = extension_operations.dispatch_before_agent_start(
                base_prompt
            )
            return before_agent_result.append_system_prompt

        def _emit_accepted_input_diagnostic(message: str) -> None:
            emit_diagnostic(terminal_ui, error_stream, message)

        accepted_turn = CodingAcceptedInputPreparer(
            transform_input=_transform_accepted_input,
            resolve_file_references=_resolve_accepted_file_references,
            resolve_image_attachments=_resolve_accepted_image_attachments,
            system_prompt_suffix=_accepted_system_prompt_suffix,
            next_turn_context=coding_input_queue.take_next_turn_context,
            emit_diagnostic=_emit_accepted_input_diagnostic,
            state_recorder=CodingSessionAcceptedInputRecorder(
                coding_state, tool_budget=tool_budget
            ),
        ).prepare(
            user_input=resolution.user_input,
            resource_provider_text=resource_provider_text,
            selected_provider_content=resolution.selected_provider_content,
            base_system_prompt=base_system_prompt,
        )
        active_input = accepted_turn.active_input
        initial_tool_state = accepted_turn.initial_tool_state
        provider_user_input = accepted_turn.provider_user_input
        turn_attachments = accepted_turn.turn_attachments
        agent_system_prompt = accepted_turn.agent_system_prompt

        def _prepare_loop_request(
            history: tuple[AgentMessage, ...],
            loop_active_input: AgentActiveInput,
            turn_index: int,
            available_tools: tuple[ToolDefinition, ...],
        ) -> AgentLoopRequestPreparation:
            coding_state.mirror_history(history)
            # Automatic compaction: when the provider-visible history grows
            # past the threshold, drop the oldest user-turn groups before
            # building the next request. The cut is at a user-message
            # boundary so no tool result is orphaned, and the safe summary
            # rides in the system prompt suffix below.
            if settings.get_compaction_enabled() and should_compact_agent_history(
                coding_state.messages,
                max_messages=AGENT_HISTORY_MAX_MESSAGES,
                max_bytes=AGENT_HISTORY_MAX_BYTES,
                keep_recent_groups=AGENT_HISTORY_KEEP_RECENT_GROUPS,
            ):
                notice = apply_compaction("auto")
                emit_diagnostic(terminal_ui, error_stream, notice)
            snapshot = provider_request_policy.prepare(
                AgentProviderRequestPolicyInput(
                    baseline=ProviderRequest(
                        system_prompt=(
                            agent_system_prompt + coding_state.compaction_suffix
                        ),
                        user_prompt=provider_user_input,
                        provider_name=coding_state.provider_name,
                        model_id=coding_state.model_id,
                        cwd=cwd,
                        messages=loop_active_input.request_messages(
                            coding_state.messages
                        ),
                        available_tools=available_tools,
                        # Image attachments belong to the current user
                        # message, so they ride only the first provider
                        # call of this turn; later tool-loop iterations
                        # append tool results (also user-role), and
                        # re-sending would mis-attach the image.
                        attachments=(turn_attachments if turn_index == 0 else ()),
                        provider_header_callback=(_active_provider_header_callback()),
                    ),
                    active_input=loop_active_input,
                ),
            )
            renderer.refresh_tool_renderers(
                execution_projections.tool_renderers(snapshot.advertised_tool_names)
            )
            return AgentLoopRequestPreparation(coding_state.messages, snapshot)

        def _complete_loop_provider_turn(
            snapshot: AgentProviderRequestSnapshot,
            event_sink: AgentEventSink,
            turn_index: int,
        ) -> ProviderTurnOutcome:
            provider_request = materialize_provider_request(snapshot)
            provider_waiter = None
            provider_for_turn = execution_projections.provider
            if terminal_ui is not None:
                provider_waiter = partial(wait_for_provider_interrupt, terminal_ui)
            elif abort_event is not None:
                provider_start_event = None
                if isinstance(abort_event, _AbortCallbackSignal):
                    provider_start_event = threading.Event()
                    provider_for_turn = _StartGatedProvider(
                        coding_state.provider, provider_start_event
                    )
                provider_waiter = partial(
                    _wait_for_external_abort,
                    abort_event,
                    provider_start_event,
                )
            return provider_turn_executor.complete(
                provider_for_turn,
                provider_request,
                event_sink,
                turn_index=turn_index,
                waiter=provider_waiter,
            )

        status_effects = CodingAgentTurnStatusEffects(
            state=AgentTurnStatusStateAdapter(
                ctl=ctl,
                coding_state=coding_state,
                prompt_history_store=prompt_history_store,
                # Only genuine literal prompts enter the local recall store.
                prompt_for_recall=(
                    user_input if resource_provider_text is None else None
                ),
            ),
            presentation=AgentTurnStatusPresentationAdapter(
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                refresh_legacy_footer_with_usage=(refresh_legacy_footer_with_usage),
            ),
        )
        tool_waiter = (
            None
            if terminal_ui is None
            else partial(wait_for_tool_interrupt, terminal_ui)
        )
        run_coordinator = CodingAgentRunCoordinator(
            request_source=AgentLoopRequestSourceAdapter(_prepare_loop_request),
            provider_turn=AgentLoopProviderTurnAdapter(_complete_loop_provider_turn),
            status_policy=status_effects,
            tool_capabilities=execution_projections,
            tool_policy=agent_tool_policy,
            event_sink=emitter,
            usage_publisher=usage_publisher,
            queued_input_port=coding_input_queue.agent_loop_port,
            coding_state=coding_state,
            retain_next_input=coding_input_queue.retain_agent_input,
            tool_waiter=tool_waiter,
        )
        ctl.agent_settled_pending = True
        loop_outcome = run_coordinator.run_turn(
            active_input,
            initial_tool_state,
            pricing=pricing_for(
                coding_state.provider_name,
                coding_state.model_id,
            ),
            accepted_queued_input=queued_input,
        )
        ctl.extension_in_agent_turn = False

        if loop_outcome.terminate_session:
            run_failure = loop_outcome.result.failure
            assert run_failure is not None
            result_snapshot = coding_state.result_snapshot()
            ended_at = datetime.now(UTC)
            try:
                repl_input.close()
            except Exception:  # noqa: BLE001 - the run already failed; close must not mask it
                pass
            return LoopStepSignal.return_result(
                build_repl_result(
                    result_snapshot,
                    status=HarnessStatus.FAILED,
                    exit_code=1,
                    started_at=started_at,
                    ended_at=ended_at,
                    error_type=run_failure.error_type,
                    error_message=run_failure.message.value,
                )
            )
        return LoopStepSignal.continue_loop()

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
        except BaseException as error:  # noqa: BLE001 - collected; chrome close still runs
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


# A bounded one-shot completion handed to extension command handlers as
# ``ctx.complete(system_prompt, user_text)`` caps its inputs so a buggy handler
# cannot create unbounded provider input.


@dataclass
class NativeToolReplSession:
    """Bounded model-driven tool loop, slice 4 skeleton.

    `tool_registry` defaults to the empty production registry; tests pass a
    mapping populated with a `_FixtureTool` (or later real tools) to exercise
    the loop. `tool_budget` is per-user-turn and capped at
    `MAX_TOOL_BUDGET`. The session reads one user turn per `readline()`
    call from `input_stream` and stops when the stream returns an empty
    string (EOF) or the malformed-tool-call streak reaches three.
    """

    provider: InitVar[ProviderPort]
    tool_registry: dict[str, ToolPort] = field(default_factory=production_tool_registry)
    tool_budget: int = 50
    workspace_root: Path | None = None
    input_runtime: str = REPL_INPUT_RUNTIME_AUTO
    reference_roots: tuple[Path, ...] = field(default_factory=tuple)
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None = (
        None
    )
    clipboard_copy: Callable[..., ClipboardResult] = copy_to_clipboard
    # OS clipboard-image reader for the editor's Ctrl+V image paste (Pi parity);
    # tests inject a deterministic fake. Returns image bytes + media type.
    clipboard_image_read: Callable[[], ImageClipboardResult] = read_clipboard_image
    prompt_history_store: PromptHistoryStore | None = None
    # Resolved keybindings for /hotkeys (and future bound surfaces). When not
    # injected the session loads <config>/keybindings.json via the shared config
    # home; tests inject a manager directly.
    keybindings_manager: "KeybindingsManager | None" = None
    # Resolved layered settings. When not injected the session loads the
    # global+project settings for the workspace, surfaced read-only by /settings.
    settings_manager: "SettingsManager | None" = None
    resume_context: ResumeContext | None = None
    resume_branch_label: str | None = None
    # Native product session tree (the product session source of truth). When
    # not injected the loop runs on an ephemeral in-memory tree that writes no
    # file; the CLI/adapter injects a persistent tree under the native-session
    # store. ``pipy-session`` remains a separate metadata-only archive.
    native_session: "NativeSessionTree | None" = None
    # Optional Pi-shaped session-event sink for the headless automation
    # transports (``--mode json``/``--mode rpc``). When ``None`` (the CLI/TUI
    # default) every emit is a no-op and behavior is unchanged; the events are
    # derived from this real loop, never a parallel session model.
    automation_observer: "AutomationEventSink | None" = None
    # Optional canonical event projection used by architecture adapters and
    # tests. It participates in the same synchronous, ordered mode composite.
    agent_event_sink: "AgentEventSink | None" = None
    # Optional external abort signal for the headless automation RPC mode. When
    # set, a non-TUI provider turn runs on a worker thread with a cancel token
    # wired to this event, so an RPC ``abort`` cancels the in-flight turn at the
    # provider boundary. ``None`` (CLI/TUI/one-shot) keeps the simple blocking
    # provider call.
    abort_event: "threading.Event | _AbortCallbackSignal | None" = None
    resource_options: RuntimeResourceOptions = field(
        default_factory=RuntimeResourceOptions.empty
    )
    # Pi-shape ``pipy "<prompt>"``: positional prompts that seed the interactive
    # session's first user turn(s). They are delivered as provider-visible prompt
    # text (like a typed message, resolving @file/@image references) before the
    # loop blocks on fresh input. Empty for the bare ``pipy`` / piped-stdin case.
    initial_messages: tuple[str, ...] = field(default_factory=tuple)
    tool_filter_options: ToolFilterOptions = field(
        default_factory=ToolFilterOptions.empty
    )
    # Pi `--verbose`: force startup/resource chrome even when quietStartup is
    # enabled in settings, without mutating the persisted setting.
    verbose_startup: bool = False
    # Exact final cwd that entered trusted state only because no protected
    # resource existed at startup. A later explicit /reload may persist trust
    # once if a protected resource has appeared (Pi's narrow safety exception).
    # Construction input only: the live one-shot is `implicit_trust`, which the
    # reload owner clears once it fires.
    auto_trust_on_reload_cwd: InitVar[Path | None] = None
    # Finalized startup activation shared with catalog construction. Only the
    # initial run consumes it; explicit /reload performs a fresh activation.
    initial_extension_batch: ExtensionActivationBatch | None = None
    _coding_state: CodingSessionState = field(init=False, repr=False)
    implicit_trust: ImplicitTrustState = field(init=False, repr=False)

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 50
    MAX_TOOL_BUDGET: ClassVar[int] = MAX_AGENT_TOOL_BUDGET

    def __post_init__(
        self, provider: ProviderPort, auto_trust_on_reload_cwd: Path | None
    ) -> None:
        self.implicit_trust = ImplicitTrustState(
            cwd=(
                auto_trust_on_reload_cwd.expanduser().resolve()
                if auto_trust_on_reload_cwd is not None
                else None
            )
        )
        if not provider.supports_tool_calls:
            raise ValueError(
                f"provider {provider.name!r} does not advertise "
                "supports_tool_calls=True; the pipy repl requires a "
                "tool-capable provider"
            )
        self._coding_state = CodingSessionState(
            provider=provider,
            provider_name=provider.name,
            model_id=provider.model_id,
        )
        if isinstance(self.tool_budget, bool) or not isinstance(self.tool_budget, int):
            raise TypeError("tool_budget must be an int")
        if self.tool_budget < 1 or self.tool_budget > self.MAX_TOOL_BUDGET:
            raise ValueError(
                "tool_budget must be in "
                f"[1, {self.MAX_TOOL_BUDGET}]; got {self.tool_budget}"
            )

    @property
    def provider_port(self) -> ProviderPort:
        """Return the state-owned provider port outside an active run."""

        return self._coding_state.provider

    def _fail_startup(
        self, coding_state: CodingSessionState, error_type: str, message: str
    ) -> NativeToolReplResult:
        return NativeToolReplResult(
            status=HarnessStatus.FAILED,
            exit_code=2,
            started_at=(now := datetime.now(UTC)),
            ended_at=now,
            provider_name=coding_state.provider_name,
            model_id=coding_state.model_id,
            error_type=error_type,
            error_message=message,
        )

    @balance_startup_candidate
    def run(
        self,
        candidate: _ExtensionCandidate,
        *,
        workspace_root: Path | None = None,
        input_stream: TextIO,
        output_stream: TextIO,
        error_stream: TextIO,
        system_prompt: str = "",
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> NativeToolReplResult:
        cwd = workspace_root or self.workspace_root
        if cwd is None:
            raise ValueError("NativeToolReplSession.run requires a workspace_root")
        cwd = cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"workspace_root is not a directory: {cwd}")

        def _stderr_sink(text: str) -> None:
            error_stream.write(text)

        coding_state = self._coding_state
        seed_provider = coding_state.provider
        initial_provider_name = provider_name or seed_provider.name
        initial_model_id = model_id or seed_provider.model_id
        coding_state.begin_run(
            provider_name=initial_provider_name,
            model_id=initial_model_id,
            usage_accumulator=AgentUsageAccumulator(
                pricing_for(initial_provider_name, initial_model_id)
            ),
        )

        # The session's single synchronization boundary, created before the
        # first owner of guarded state so every one of them shares this exact
        # object. Two locks would not serialize a reload against a worker's
        # mutation; see
        # `docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`.
        # A caller-supplied manager keeps its identity but adopts this lock:
        # leaving it on a private one would give the run two boundaries, which
        # serialize nothing against each other.
        session_state_lock = threading.RLock()
        coding_effects = CodingEffectCoordinator()
        keybindings = self.keybindings_manager or KeybindingsManager.create(
            state_lock=session_state_lock
        )
        keybindings.bind_state_lock(session_state_lock)
        settings = self.settings_manager or SettingsManager.for_workspace(cwd)
        settings.bind_state_lock(session_state_lock)
        # The coding state is built in `__post_init__`, before a session
        # exists, so it starts on a private lock and adopts the shared one
        # here. An extension handler reaching `set_model` from a worker thread
        # rebinds its provider and clears its history; that must serialize
        # against the session thread's own reads and writes.
        coding_state.bind_state_lock(session_state_lock)
        if isinstance(self.provider_state, NativeReplProviderState):
            self.provider_state.bind_state_lock(session_state_lock)
        resource_options = self.resource_options
        # Compose installed package resources: resolve local paths and managed
        # git caches, then install the package theme registry so package
        # skills/prompts/extensions/themes flow through discovery at lowest
        # precedence with the Pi-shaped enablement filters applied.
        package_roots = compose_package_runtime(
            settings,
            cwd,
            include_package_themes=not resource_options.no_themes,
            explicit_theme_paths=resource_options.theme_paths,
        )
        # Apply the settings resource enable/disable directives (Pi pi config):
        # disabled skills/prompts are dropped from what is registered.
        workspace_resources = WorkspaceResources.discover(
            cwd,
            package_roots=package_roots,
            explicit_skill_paths=resource_options.skill_paths,
            explicit_prompt_template_paths=resource_options.prompt_template_paths,
            include_skills_defaults=not resource_options.no_skills,
            include_prompt_template_defaults=not resource_options.no_prompt_templates,
            include_workspace_defaults=settings.project_trusted,
        ).with_enablement(
            skills_patterns=settings.get_skills_patterns(),
            prompts_patterns=settings.get_prompts_patterns(),
            enable_skill_commands=settings.get_enable_skill_commands(),
        )
        # Activate extensions fail-closed; built-in tool names stay reserved
        # so an extension cannot shadow them.
        extension_runtime = _activate_workspace_extensions(
            cwd,
            workspace_resources,
            tuple(self.tool_registry.keys()),
            package_roots=()
            if resource_options.no_extensions
            else package_roots.extensions,
            extension_patterns=settings.get_extensions_patterns(),
            explicit_extension_paths=resource_options.extension_paths,
            include_default_extensions=not resource_options.no_extensions,
            include_workspace_defaults=settings.project_trusted,
            activation_batch=self.initial_extension_batch,
            diagnostic=lambda message: emit_diagnostic(None, error_stream, message),
        )
        candidate.adopt(
            extension_runtime,
            partial(emit_diagnostic, None, error_stream),
        )
        extension_flag_values, extension_flag_error = parse_extension_flag_tokens(
            extension_runtime.flags,
            tuple(resource_options.extension_flag_tokens),
        )
        if extension_flag_error is not None:
            print(f"pipy: {extension_flag_error}", file=error_stream)
            return self._fail_startup(
                coding_state, "ExtensionFlagError", extension_flag_error
            )
        extension_in_agent_turn = False
        # Set immediately before an accepted agent run starts and cleared only
        # when its extension-surface true-idle notification has fired. Keeping
        # this independent of `agent_end` lets the session finally settle a run
        # that exits through an unexpected provider/tool/lifecycle exception.
        agent_settled_pending = False
        terminal_ui = self._build_terminal_ui(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=cwd,
            keybindings_manager=keybindings,
            include_workspace_defaults=settings.project_trusted,
        )
        if (
            terminal_ui is not None
            and not settings.project_trusted
            and has_trust_requiring_project_resources(cwd)
        ):
            terminal_ui.add_notice(
                "This project is not trusted. Project .pipy resources and "
                "packages are ignored. Use /trust to save a trust decision, "
                "then restart pipy."
            )

        # Live UI sink for extension `ctx.ui.notify` from hooks and tools:
        # notifications are emitted as local diagnostics (interactive) and
        # degrade deterministically in non-interactive mode.
        def _extension_notify(_kind: str, message: str) -> None:
            safe_message = "\n".join(
                sanitize_label_text(line) for line in str(message).splitlines()
            )
            emit_diagnostic(terminal_ui, error_stream, safe_message)

        extension_ui_driver = (
            _LiveExtensionUiDriver(terminal_ui, cwd)
            if terminal_ui is not None
            else None
        )

        # Adapt activated extension tools at the product composition seam. The
        # shared built-in registry is never mutated; the capability facade owns
        # the run-local merged registry, visibility, and executor context.
        render_details = _tool_renderers._extension_render_details_sinks(
            terminal_ui is not None
        )
        tool_capabilities = NativeToolCapabilities(
            self.tool_registry,
            {},
            workspace_root=cwd,
            reference_roots=self.reference_roots,
            stderr_sink=_stderr_sink,
            filter_options=self.tool_filter_options,
            cancel_join_timeout_seconds=CANCEL_JOIN_TIMEOUT_SECONDS,
            # Share the session mutex rather than letting the capability owner
            # create a private one: an extension tool handler reaching
            # `set_active_tools` from a worker thread and a reload publishing a
            # new generation must serialize against each other, which two
            # separate locks would not do.
            state_lock=session_state_lock,
        )
        startup_projection = build_candidate_extension_projection(
            extension_runtime,
            extension_flag_values,
            queue_mutex=session_state_lock,
            reference_mutex=session_state_lock,
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            set_active_tools=lambda generation_id, names: (
                provider_mutation.extension_set_active_tools(generation_id, names)
            ),
            render_details=render_details.writer,
            project_trusted=settings.project_trusted,
            prepare_capability=tool_capabilities.prepare_extensions,
            chrome=(
                ExtensionChromeHandle(extension_ui_driver.startup_chrome_sink())
                if extension_ui_driver is not None
                else None
            ),
        )
        extension_generation = SessionExtensionGeneration(
            extension_runtime, startup_projection
        )
        if (published_projection := extension_generation.projection) is None:
            message = "extension generation projection is unavailable"
            print(f"pipy: {message}", file=error_stream)
            return self._fail_startup(coding_state, "ExtensionActivationError", message)
        startup_projection = published_projection
        startup_gate = OrderedDeliveryGate(session_state_lock)
        startup_projection.queues.install_candidate_route(startup_gate)
        staged = FrozenStagedDeliveryBatch.freeze((), extension_runtime.custom_messages)
        generation_ref = SessionGenerationRef(
            extension_generation, lock=session_state_lock
        )
        startup_snapshot = generation_ref.snapshot()
        startup_generation_projection = startup_snapshot.generation.projection
        if startup_generation_projection is None:
            raise RuntimeError("published extension generation has no projection")
        startup_commands = startup_generation_projection.commands
        if terminal_ui is not None:
            terminal_ui.autocomplete.set_max_visible(
                settings.get_autocomplete_max_visible()
            )
            terminal_ui.autocomplete.replace_command_surface(
                published_command_surface(workspace_resources, startup_commands)
            )
            if keybindings.has_user_binding("app.editor.external"):
                editor_keys = {
                    normalized
                    for key in keybindings.keys_for("app.editor.external")
                    if (normalized := normalize_shortcut_key(key))
                }
                for key in sorted(editor_keys.intersection(startup_commands.shortcuts)):
                    print(
                        "pipy: extension shortcut "
                        f"{key!r} is shadowed by app.editor.external; rebind the "
                        "editor action or extension shortcut.",
                        file=error_stream,
                    )
        apply_startup_provider_projection(
            generation_ref=generation_ref,
            provider_state=self.provider_state,
            coding_state=coding_state,
            error_stream=error_stream,
        )
        if not publish_candidate_ownership(candidate):
            startup_projection.queues.retire_route()
            message = "extension candidate ownership is unavailable"
            print(f"pipy: {message}", file=error_stream)
            return self._fail_startup(coding_state, "ExtensionActivationError", message)
        tool_capabilities.publish(startup_projection.tools.capability_state)
        execution_projections = SessionExecutionProjections(
            generation_ref=generation_ref,
            tool_capabilities=tool_capabilities,
            coding_state=coding_state,
            ui_driver=extension_ui_driver,
        )
        provider_turn_executor = ProviderTurnExecutor(
            cancel_join_timeout_seconds=CANCEL_JOIN_TIMEOUT_SECONDS,
        )
        unknown_filter_names = tool_capabilities.unknown_filter_names
        if unknown_filter_names:
            known = ", ".join(sorted(tool_capabilities.registered_names)) or "<none>"
            unknown = ", ".join(unknown_filter_names)
            raise ValueError(f"unknown tool name(s): {unknown}. Known tools: {known}")
        # Image attachments may reference the owner-only clipboard temp dir
        # injected at TUI wiring. The session consumes the exact same frozen
        # config record for image-root policy; file references do not use it.
        image_reference_roots = self.reference_roots
        if terminal_ui is not None:
            terminal_ui.set_thinking_hidden(settings.get_hide_thinking_block())
            clipboard_config = terminal_ui.clipboard_images.config
            if clipboard_config is not None:
                image_reference_roots = (
                    *self.reference_roots,
                    clipboard_config.temp_dir,
                )
        # Local-only persistent prompt-history store (independent of the
        # metadata-first session archive). Built once per session; the
        # ``/settings`` dialog toggles/clears it. When enabled, a fresh TUI
        # session seeds its in-memory recall buffer from the saved prompts.
        prompt_history_store = self.prompt_history_store or PromptHistoryStore()
        # Settings is the source of truth for the prompt-history toggle: when it
        # sets promptHistory.enabled, surface that into the store (which remains
        # the on-disk recall cache) so a fresh session honors the setting.
        if settings.get_prompt_history_enabled() and not prompt_history_store.enabled:
            prompt_history_store.set_enabled(True)
        if terminal_ui is not None and prompt_history_store.enabled:
            terminal_ui.input_editor.load_history(prompt_history_store.entries())
        renderer: _ToolLoopRenderer | TuiToolLoopRenderer
        if terminal_ui is not None:
            renderer = terminal_ui.create_tool_loop_renderer(
                render_details_sink=render_details.tui,
            )
        else:
            renderer = _ToolLoopRenderer(
                output_stream=output_stream,
                error_stream=error_stream,
                render_details_sink=render_details.captured,
            )
        # `session_start` fires once the session is set up (reason "startup");
        # `session_shutdown` fires when the run ends.
        started_at = datetime.now(UTC)
        # Native product session tree: the durable source of truth. When not
        # injected we run on an ephemeral in-memory tree (no file). The live
        # Coding state mirrors the tree's active branch as immutable snapshots
        # while retaining exact canonical message identities. Every append is
        # applied to live state before the existing synchronous tree write so
        # /tree navigation, resume, fork, clone, and durable compaction observe
        # the established ordering.
        session_tree = self.native_session or NativeSessionTree.create(
            cwd, persist=False
        )
        # Single mutable holder for this run's shared control state. It is
        # constructed as soon as ``session_tree`` is available (the first
        # setup-time closure call — ``product_session.rebuild_active_history()``
        # below — reads ``ctl.session_tree``) and seeded from the setup locals.
        # ``pending_prefill``/``tree_filter_mode`` carry their former literal
        # initializers here; ``line`` uses the dataclass default. The
        # composition-root closures reassign ``ctl.<attr>`` where they previously
        # rebound the run-scope ``nonlocal`` names.
        ctl = RunControlState(
            coding_effects=coding_effects,
            _session_tree=session_tree,
            tree_filter_mode="default",
            pending_prefill=None,
            package_roots=package_roots,
            workspace_resources=workspace_resources,
            generation_ref=generation_ref,
            agent_settled_pending=agent_settled_pending,
            extension_in_agent_turn=extension_in_agent_turn,
        )

        def _load_product_session_history() -> CodingProductSessionContext:
            return CodingProductSessionContext(
                messages=tuple(ctl.session_tree.build_context().messages)
            )

        def _persist_agent_message(message: AgentMessage) -> None:
            ctl.session_tree.append_message(message)

        def _persist_compaction(action: CodingProductSessionCompaction) -> None:
            # `provider_mutation` is assigned later in this run scope; this
            # callback only fires at runtime (via `product_session.apply_compaction`
            # inside the handler's `apply_compaction`), by which point the handler
            # is bound, so the late name reference is safe.
            provider_mutation.append_durable_compaction(
                action.durable_summary.value,
                action.measure_before,
            )

        product_session = CodingProductSessionCoordinator(
            state=coding_state,
            port=CodingProductSessionCallbacks(
                load_active_history_callback=_load_product_session_history,
                append_message_callback=_persist_agent_message,
                apply_compaction_callback=_persist_compaction,
            ),
        )
        product_session.rebuild_active_history()

        # Native session-tree command state. ``ctl.pending_prefill`` carries text
        # from a ``/tree`` user-message selection back into the next prompt
        # (rehydrated editor in the live TUI). ``ctl.tree_filter_mode`` is the
        # active ``/tree`` filter; both are seeded in the ``ctl`` constructor.
        # Mutable safe summary suffix appended to the system prompt after a
        # /compact or auto-compaction; the base system prompt itself is never
        # mutated. base_system_prompt already carries any resume seed block.
        base_system_prompt = system_prompt

        append_agent_message = product_session.append_message

        # Slice 3.3: durable product-session persistence is a live projection in
        # the mode's fixed immediate composite. The composition root owns the
        # renderer -> automation (optional) -> product session -> metadata-only
        # workflow archive -> caller (optional) order. Extension lifecycle
        # mapping wraps that completed product composition as one sink.
        product_action_sink = NativeProductSessionActionSink(append_agent_message)
        immediate_sinks: list[AgentEventSink] = [RenderingAgentEventAdapter(renderer)]
        if self.automation_observer is not None:
            immediate_sinks.append(
                AutomationAgentEventAdapter(self.automation_observer)
            )
        immediate_sinks.extend(
            (
                ProductSessionEventProjection(product_action_sink),
                WorkflowArchiveAgentEventAdapter(),
            )
        )
        if self.agent_event_sink is not None:
            immediate_sinks.append(self.agent_event_sink)
        immediate_sink = SynchronousAgentEventComposite(tuple(immediate_sinks))
        emitter = _extension_hooks._ExtensionLifecycleAgentEventAdapter(
            immediate_sink,
            generation_snapshot=generation_ref.snapshot,
            cwd=str(cwd),
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            ui_driver=extension_ui_driver,
            project_trusted=settings.project_trusted,
        )

        def absorb_session_usage(sample: AgentProviderUsageSample) -> None:
            coding_state.absorb_usage(sample)

        usage_publisher = NativeAgentUsagePublisher(absorb_session_usage, emitter)

        input_queued_input_source = (
            input_stream.take_next
            if isinstance(input_stream, AgentQueuedInputPort)
            else None
        )
        input_queued_input_port = (
            NativeAgentQueuedInputPort(input_queued_input_source)
            if input_queued_input_source is not None
            else None
        )

        def take_terminal_queued_input() -> AgentQueuedInput | None:
            if terminal_ui is not None:
                drained_content = terminal_ui.pending_messages.take_next_drain()
                if drained_content is not None:
                    raw_kind = terminal_ui.pending_messages.take_last_drain_kind()
                    if raw_kind not in {
                        AgentQueuedInputKind.STEERING.value,
                        AgentQueuedInputKind.FOLLOW_UP.value,
                    }:
                        raise ValueError(
                            "terminal queued input must have a closed delivery kind"
                        )
                    return AgentQueuedInput(
                        ProductContent(drained_content),
                        AgentQueuedInputKind(raw_kind),
                    )
            return None

        terminal_queued_input_port = NativeAgentQueuedInputPort(
            take_terminal_queued_input
        )

        def take_pending_local_command() -> ProductContent | None:
            if terminal_ui is None:
                return None
            command = terminal_ui.input_editor.take_pending_command()
            return None if command is None else ProductContent(command)

        coding_input_queue = CodingInputQueue(
            external_inputs=tuple(
                port
                for port in (input_queued_input_port, terminal_queued_input_port)
                if port is not None
            ),
            mutation_lock=coding_effects.lock,
            pending_local_command_source=take_pending_local_command,
            seeds=(
                ProductContent(message) for message in self.initial_messages if message
            ),
        )
        loop_controller = CodingSessionController(
            input_queue=coding_input_queue,
            coding_state=coding_state,
            emitter=emitter,
        )

        # Custom-entry / custom-message rendering and the extension outbox drain
        # live in the component-owned `CustomEntryRenderer` handler (symmetric
        # with `_ReplLoopStep`/`_BuiltinCommandInterpreter`). Rendering uses one
        # published projection snapshot; the narrow adapter retains live outboxes
        # only for R4a's legacy/harness direct-drain fallback, while the session
        # tree and `extension_in_agent_turn` remain live run control. Its
        # bound methods are passed wherever the deleted closures were consumed.
        custom_renderer = CustomEntryRenderer(
            ctl=_ExtensionCustomEntryRunState(ctl=ctl),
            terminal=terminal_ui.custom_entry_render_target() if terminal_ui else None,
            coding_input_queue=coding_input_queue,
            coding_effects=coding_effects,
            error_stream=error_stream,
            generation_snapshot=ctl.generation_ref.snapshot,
        )
        with startup_gate.reserve() as startup_token:
            _extension_hooks.deliver_accepted_staged_batch(
                staged,
                gate=startup_gate,
                token=startup_token,
                user_sink=lambda _message: None,
                custom_sink=partial(
                    _extension_hooks.deliver_staged_custom,
                    custom_renderer.extension_send_message,
                ),
                release_route=startup_projection.queues.release_pending_route,
            )

        repl_input = (
            terminal_ui
            if terminal_ui is not None
            else self._build_repl_input(
                input_stream=input_stream,
                error_stream=error_stream,
                workspace=cwd,
                command_names=tool_loop_command_names(
                    workspace_resources, startup_commands.menu_names
                ),
                command_descriptions=tool_loop_command_descriptions(
                    workspace_resources, dict(startup_commands.descriptions)
                ),
            )
        )
        # Terminal chrome owns the footer effect adapter. Inject the session's
        # current bound methods so its formatting seams remain monkeypatchable
        # without chrome importing this composition root or the concrete TUI.
        footer = _ChromeFooterEffects(
            footer_text=self._footer_text,
            print_footer=self._print_footer,
            cwd=cwd,
            coding_state=coding_state,
            error_stream=error_stream,
            terminal_ui=terminal_ui,
            repl_runtime=repl_input,
        )
        if terminal_ui is None:
            print_startup_chrome(
                error_stream,
                cwd=cwd,
                quiet=settings.get_quiet_startup() and not self.verbose_startup,
                include_workspace_defaults=settings.project_trusted,
            )
            if self.resume_context is not None:
                print(
                    "pipy: "
                    + compose_resume_status_line(
                        self.resume_context,
                        branch_label=self.resume_branch_label,
                    ),
                    file=error_stream,
                )
        else:
            terminal_ui.set_footer_text(footer.coding_footer_text())
            terminal_ui.start()
            if self.resume_context is not None:
                # Safe resumed-state notice committed to scrollback at startup:
                # prior session id, provider, model, turn count, finalized time
                # (and branch label) only — never prompts, output, or summary.
                terminal_ui.add_notice(
                    compose_resume_status_line(
                        self.resume_context,
                        branch_label=self.resume_branch_label,
                    )
                )
            custom_renderer.replay_custom_entries_to_terminal()

        # Startup changelog: on a fresh session, show the entries new since the
        # stored lastChangelogVersion (or a condensed line under collapseChangelog)
        # and record the current version. First run / resumed sessions show
        # nothing. Runs no provider turn.
        changelog_lines, store_version = changelog_startup(
            read_changelog_entries(),
            last_version=settings.get_last_changelog_version(),
            current_version=pipy_version(),
            collapse=settings.get_collapse_changelog(),
            is_fresh=self.resume_context is None,
        )
        for line in changelog_lines:
            if terminal_ui is not None:
                terminal_ui.add_notice(line)
            else:
                print(line, file=error_stream)
        if store_version is not None:
            try:
                settings.set_last_changelog_version(store_version)
            except RuntimeError:
                pass

        extension_operations = SessionExtensionOperations(
            generation_ref=ctl.generation_ref,
            cwd=str(cwd),
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            ui_driver=extension_ui_driver,
            project_trusted=settings.project_trusted,
            model_runtime_factory=lambda generation_id, allow_model: (
                provider_mutation.model_runtime_control(
                    generation_id, allow_model=allow_model
                )
            ),
        )

        # The provider/model/auth/compaction mutation effects live in the
        # `ProviderMutationEffects` handler, built after `product_session`/`footer`
        # exist; it reaches the run's mutable control state through the shared `ctl`
        # holder so a `/reload` rebind is reflected exactly as it was inline.
        provider_mutation = ProviderMutationEffects(
            provider_state=self.provider_state,
            ctl=ctl,
            extension_operations=extension_operations,
            coding_state=coding_state,
            product_session=product_session,
            terminal_ui=terminal_ui,
            tool_capabilities=tool_capabilities,
            settings=settings,
            cwd=cwd,
            input_stream=input_stream,
            error_stream=error_stream,
            refresh_footer_text=footer.refresh_footer_text,
            extension_notify=_extension_notify,
            mutation_io_lock=coding_effects.lock,
        )

        # The residual run-loop collaborators (diagnostics, session-name setters,
        # session-dir/resolution, tree rebuild, branch summarization, the extension
        # completion/custom-UI/session-gate/provider-request/tool-policy hooks, and
        # the resource/extension command-dispatch effects) live in the
        # `_SessionCollaborators` handler, built once `provider_mutation`/
        # `custom_renderer` exist; it reads the run's mutable control state through
        # the shared `ctl` holder so a `/reload` rebind is reflected on next dispatch.
        collaborators = SessionCollaborators(
            abort_event=self.abort_event,
            clipboard_copy=self.clipboard_copy,
            implicit_trust=self.implicit_trust,
            provider_state=self.provider_state,
            tool_registry=self.tool_registry,
            verbose_startup=self.verbose_startup,
            ctl=ctl,
            extension_operations=extension_operations,
            execution_projections=execution_projections,
            coding_state=coding_state,
            product_session=product_session,
            coding_input_queue=coding_input_queue,
            coding_effects=coding_effects,
            terminal_ui=terminal_ui,
            settings=settings,
            cwd=cwd,
            error_stream=error_stream,
            provider_mutation=provider_mutation,
            custom_renderer=custom_renderer,
            extension_ui_driver=extension_ui_driver,
            extension_notify=_extension_notify,
        )
        provider_request_policy = NativeAgentProviderRequestPolicy(
            collaborators.prepare_agent_provider_request
        )
        agent_tool_policy = NativeAgentToolPolicy(
            collaborators.apply_extension_tool_policy,
            collaborators.transform_extension_tool_result,
        )

        # Pi-parity: the slash-menu input adapter draws the bottom status
        # block (cwd + status line) live below the input area, so we only
        # emit a pre-loop frame for non-slash-menu runtimes. This avoids a
        # duplicate cwd/status row above the prompt area in TTY sessions,
        # while keeping the captured-stream/plain case visible on immediate
        # EOF. `_print_footer` re-emits it after each submission.
        if footer.legacy_footer_enabled():
            self._print_footer(
                error_stream,
                cwd=cwd,
                provider_name=coding_state.provider_name,
                model_id=coding_state.model_id,
                user_turn_count=coding_state.user_turn_count,
                tool_invocation_count=coding_state.tool_invocation_count,
                usage_snapshot=coding_state.usage_snapshot(),
            )

        # Continuing built-in interpretation runs through four closed typed
        # effect families. The controller retains built-in > resource >
        # extension precedence; this composition seam only supplies effects.
        session_command_effects = collaborators.session_command_effects(repl_input)
        provider_configuration_effects = (
            collaborators.provider_configuration_command_effects(
                keybindings=keybindings,
                prompt_history_store=prompt_history_store,
            )
        )
        transfer_command_effects = collaborators.transfer_command_effects(
            system_prompt=system_prompt,
            input_stream=input_stream,
        )
        reload_command_effects = collaborators.reload_command_effects(
            keybindings=keybindings,
            renderer=renderer,
            emitter=emitter,
            resource_options=resource_options,
            tool_capabilities=tool_capabilities,
            extension_render_details=render_details.writer,
        )
        builtin_interpreter = BuiltinCommandInterpreter(
            session_effects=session_command_effects,
            provider_configuration_effects=provider_configuration_effects,
            transfer_effects=transfer_command_effects,
            reload_effects=reload_command_effects,
            refresh_legacy_footer=footer.refresh_legacy_footer,
            refresh_legacy_footer_with_usage=footer.refresh_legacy_footer_with_usage,
        )

        command_effects: CodingCommandEffects = _CallableCodingCommandEffects(
            emit=collaborators.diag,
            footer=footer.refresh_legacy_footer,
            interpret=builtin_interpreter.interpret,
            record_resource=coding_state.record_resource_invocation,
            resolve_resource=collaborators.dispatch_resource_effect,
            resolve_extension=collaborators.dispatch_extension_effect,
        )

        # The `while True` skeleton and the start/shutdown lifecycle are owned by
        # the headless controller (`CodingSessionController.run_loop`). It fires
        # `session_start`, runs the `while True` itself, and on each iteration
        # calls the injected `step_once` port and routes the `LoopStepSignal` it
        # returns — `CONTINUE` re-enters the loop, `BREAK` finalizes through the
        # `finalize` port (the post-loop `SUCCEEDED` projection), and
        # `RETURN_RESULT` returns the terminate `FAILED` projection the step
        # already built — guaranteeing the once-only true-idle settle, the
        # `session_shutdown` fire, terminal generation/outbox close, and the
        # extension-chrome clear on EVERY exit path (normal return, fatal return,
        # or a propagated exception). One
        # iteration's body, the run transition, and every UI/provider/persistence
        # effect live in `_ReplLoopStep.step_once` (a module-level composition-root
        # handler, symmetric with `_BuiltinCommandInterpreter`); it performs one
        # iteration and returns only the routing signal, and shares the run's
        # mutable control state with the composition-root closures through the
        # `ctl` `RunControlState` holder so a `/reload`, `/new`, `/resume`,
        # `/fork`, or `/clone` rebind is reflected in those closures exactly as it
        # was inline. `run()` reaches the handler by passing its bound methods
        # (each `functools.partial`-bound to the run-scope collaborators) through
        # the same `run_loop` ports.
        repl_loop_step = _ReplLoopStep()
        scope = ReplLoopScope(
            ctl=ctl,
            loop_controller=loop_controller,
            terminal_ui=terminal_ui,
            error_stream=error_stream,
            coding_state=coding_state,
            repl_input=repl_input,
            renderer=renderer,
            emitter=emitter,
            settings=settings,
            cwd=cwd,
            started_at=started_at,
            base_system_prompt=base_system_prompt,
            image_reference_roots=image_reference_roots,
            file_reference_roots=self.reference_roots,
            abort_event=self.abort_event,
            provider_state=self.provider_state,
            tool_budget=self.tool_budget,
            prompt_history_store=prompt_history_store,
            execution_projections=execution_projections,
            agent_tool_policy=agent_tool_policy,
            coding_input_queue=coding_input_queue,
            command_effects=command_effects,
            input_queued_input_port=input_queued_input_port,
            provider_request_policy=provider_request_policy,
            provider_turn_executor=provider_turn_executor,
            usage_publisher=usage_publisher,
            extension_operations=extension_operations,
            diag=collaborators.diag,
            coding_footer_text=footer.coding_footer_text,
            refresh_legacy_footer_with_usage=footer.refresh_legacy_footer_with_usage,
            apply_compaction=provider_mutation.apply_compaction,
            cycle_thinking_level=provider_mutation.cycle_thinking_level,
            append_agent_message=append_agent_message,
            drain_extension_outboxes=custom_renderer.drain_extension_outboxes,
            active_provider_header_callback=collaborators.active_provider_header_callback,
            extension_custom_driver=collaborators.extension_custom_driver,
            extension_notify=_extension_notify,
            coding_session_control=collaborators.coding_session_control,
        )
        return loop_controller.run_loop(
            step_once=partial(repl_loop_step.step_once, scope=scope),
            finalize=partial(
                repl_loop_step.finalize,
                coding_state=coding_state,
                repl_input=repl_input,
                started_at=started_at,
            ),
            fire_session_start=partial(
                repl_loop_step.fire_session_start, emitter=emitter
            ),
            fire_session_shutdown=partial(
                repl_loop_step.fire_session_shutdown, emitter=emitter
            ),
            consume_settle_pending=partial(
                repl_loop_step.consume_settle_pending, ctl=ctl
            ),
            close_extension_session=partial(
                repl_loop_step.close_extension_session,
                coding_effects=coding_effects,
                generation_ref=ctl.generation_ref,
            ),
            clear_extension_chrome=partial(
                repl_loop_step.clear_extension_chrome, terminal_ui=terminal_ui
            ),
        )

    def _build_repl_input(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        command_names: tuple[str, ...],
        command_descriptions: Mapping[str, str],
    ) -> NativeReplInput:
        return native_repl_input_for(
            input_stream=input_stream,
            error_stream=error_stream,
            input_runtime=self.input_runtime,
            workspace=workspace,
            command_names=command_names,
            command_descriptions=command_descriptions,
        )

    def _build_terminal_ui(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        keybindings_manager: KeybindingsManager | None = None,
        include_workspace_defaults: bool = False,
    ) -> ToolLoopTerminalUi | None:
        if self.input_runtime not in {REPL_INPUT_RUNTIME_AUTO, "tool-loop-tui"}:
            return None
        if not ToolLoopTerminalUi.is_supported(input_stream, error_stream):
            return None
        clipboard_config = create_clipboard_config(self.clipboard_image_read)
        return ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=error_stream,
            cwd=workspace,
            keybindings_manager=keybindings_manager,
            include_workspace_defaults=include_workspace_defaults,
            clipboard_config=clipboard_config,
        )

    def _effort_label(self, provider_name: str, model_id: str) -> str:
        """Reasoning-effort label, preferring the live runtime thinking level.

        When the user has cycled the thinking level with Shift+Tab (or selected
        a ``model:level`` reference), the provider state carries the runtime
        level and the footer reflects it; otherwise it falls back to the
        model's default effort label.
        """

        state = self.provider_state
        level = (
            state.current_thinking_level()
            if isinstance(state, NativeReplProviderState)
            else None
        )
        if isinstance(level, str) and level:
            return level
        return _chrome._effort_label_for(provider_name, model_id)

    def _footer_text(
        self,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        error_stream: TextIO | None = None,
        usage_snapshot: CodingSessionUsageSnapshot | None = None,
    ) -> str:
        plan_label = "sub" if provider_name == "openai-codex" else "api"
        budget = _chrome._context_budget_for(provider_name, model_id)
        used_pct = 0.0
        if budget.token_budget > 0:
            if usage_snapshot is not None and usage_snapshot.last_total_tokens > 0:
                used_pct = (
                    100.0
                    * usage_snapshot.last_total_tokens
                    / float(budget.token_budget)
                )
            else:
                estimated_tokens = self._estimated_context_tokens(
                    tool_invocation_count=tool_invocation_count,
                    user_turn_count=user_turn_count,
                )
                used_pct = 100.0 * estimated_tokens / float(budget.token_budget)
            used_pct = min(used_pct, 999.9)
        cost_label = (
            f"${usage_snapshot.usage.cost_usd:.3f}"
            if usage_snapshot is not None
            else "$0.000"
        )
        cache_hit_percent = (
            usage_snapshot.cache_hit_percent if usage_snapshot is not None else None
        )
        usage = usage_snapshot.usage if usage_snapshot is not None else None
        fields = BottomStatusFields(
            cwd_label="",
            cost_label=cost_label,
            plan_label=plan_label,
            context_used_pct=used_pct,
            context_budget_label=budget.budget_label,
            context_budget_suffix="auto",
            provider_name=provider_name,
            model_id=model_id,
            effort_label=self._effort_label(provider_name, model_id),
            tokens_in=(usage.input_tokens if usage else 0),
            tokens_out=(usage.output_tokens if usage else 0),
            tokens_reasoning=(usage.reasoning_tokens if usage else 0),
            tokens_cache_read=(usage.cache_read_tokens if usage else 0),
            tokens_cache_write=(usage.cache_write_tokens if usage else 0),
            cache_hit_percent=cache_hit_percent,
        )
        status_width = max(20, chrome_width(error_stream))
        status_line = format_bottom_status_line(status_width, fields)
        cwd_label = _chrome._friendly_cwd_label(cwd)
        return f"{cwd_label}\n{status_line}"

    def _estimated_context_tokens(
        self, *, tool_invocation_count: int, user_turn_count: int
    ) -> float:
        """Cheap upper-bound estimate for the prompt's context-window draw.

        We do not parse provider usage telemetry yet; until that lands the
        bottom-status meter shows a deterministic rough estimate that
        grows with tool invocations and user turns. This matches Pi's
        ``used%/budget`` shape without inventing fake exact counts.
        """

        per_turn_tokens = 2_000.0
        per_tool_tokens = 1_500.0
        return (
            user_turn_count * per_turn_tokens + tool_invocation_count * per_tool_tokens
        )

    def _print_footer(
        self,
        error_stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_snapshot: CodingSessionUsageSnapshot | None = None,
    ) -> None:
        print_input_separator(error_stream)
        footer = self._footer_text(
            cwd=cwd,
            provider_name=provider_name,
            model_id=model_id,
            user_turn_count=user_turn_count,
            tool_invocation_count=tool_invocation_count,
            error_stream=error_stream,
            usage_snapshot=usage_snapshot,
        )
        cwd_label, _, status_line = footer.partition("\n")
        print_bottom_status_block(
            error_stream, cwd_label=cwd_label, status_line=status_line
        )


__all__ = [
    "NativeToolReplSession",
    "production_tool_registry",
]
