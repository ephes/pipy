"""Bounded model-driven REPL session skeleton.

Slice 4 of the Tool-Loop Parity Track introduces a small `NativeToolReplSession`
class that wires the slice 2 contracts (`ToolDefinition`, `ToolRequest`,
`ToolExecutionResult`, `ToolPort`, `ToolContext`, `validate_arguments`) and the
slice 3 provider extension (`ProviderPort.supports_tool_calls`,
`ProviderToolCall`, `ProviderResult.tool_calls`) into a real turn loop.

The session is the product REPL behind `pipy repl --agent pipy-native`. It runs
the production tool registry (`read`, `ls`, `grep`, `find`, `write`, `edit`,
`bash`, ...); tests may inject a `_FixtureTool` through the registry argument to
verify loop behavior in isolation.

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

import os
import tempfile
import threading
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any, ClassVar, TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
import pipy_harness.native.agent.history as _agent_history
import pipy_harness.native.chrome as _chrome
import pipy_harness.native.tool_renderers as _tool_renderers
from pipy_harness.native.clipboard import (
    ClipboardResult,
    ImageClipboardResult,
    copy_to_clipboard,
    read_clipboard_image,
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
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.automation.events import (
    AutomationEventSink,
)
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentEventSink,
    AgentFailure,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.history import (
    compact_agent_history,
    should_compact_agent_history,
)
from pipy_harness.native.agent.loop import (
    AgentLoopRequestPreparation,
)
from pipy_harness.native.agent.loop_policy import (
    MAX_AGENT_TOOL_BUDGET,
    AgentProviderRequestPolicyInput,
    AgentProviderStatusDecision,
    AgentToolPolicyDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    ProviderTurnInterruption,
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
    AgentTokenPricing,
    AgentUsageAccumulator,
)
from pipy_harness.native.agent_adapters import (
    NativeProductSessionActionSink as NativeProductSessionActionSink,
    ProductSessionEventProjection,
    SynchronousAgentEventComposite,
    WorkflowArchiveAgentEventAdapter,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentQueuedInputPort as NativeAgentQueuedInputPort,
    NativeAgentUsagePublisher as NativeAgentUsagePublisher,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.accepted_input import (
    CodingAcceptedInputPreparer,
    CodingSessionAcceptedInputRecorder,
)
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    AgentLoopStatusPolicyAdapter,
    CodingAgentRunCoordinator,
)
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    CommandDispatchResolutionKind,
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
)
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
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
    REPL_INPUT_RUNTIME_AUTO,
    NativeReplInput,
    native_repl_input_for,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    UnavailableAfterReloadProvider,
    NativeReplProviderState,
    StaticNativeReplProviderState,
    normalize_repl_fake_selection,
    settings_overlay_lines,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.changelog import (
    changelog_startup,
    read_changelog_entries,
    render_changelog,
)
from pipy_harness.native.keybindings import KeybindingsManager, render_hotkeys
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.project_trust import (
    DefaultProjectTrust,
    ProjectTrustError,
    ProjectTrustStore,
    get_project_trust_options,
    has_trust_requiring_project_resources,
)
from pipy_harness.native.scoped_models import filter_scoped_references, next_reference
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.catalog import THINKING_LEVELS
from pipy_harness.native.version_check import pipy_version
from pipy_harness.native.export_distribution import (
    NativeExportError,
    ShareCancelled,
    ShareResult,
    default_html_export_path,
    export_native_branch_to_jsonl,
    export_native_session_to_html,
    import_native_session_jsonl as import_native_session_jsonl,
    parse_command_path_argument,
    resolve_github_token,
    share_native_session,
)
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_status_line,
)
from pipy_harness.native.session_tree import (
    CompactionEntry as _CompactionEntry,
)
from pipy_harness.native.session_tree import (
    MessageEntry as _MessageEntry,
)
from pipy_harness.native.session_tree import (
    NativeSessionTree,
    default_native_session_dir,
)
from pipy_harness.native.session_tree_commands import (
    FILTER_MODES,
    TreeCommandOutcome,
    apply_tree_selection,
    delete_native_session,
    entry_preview,
    format_session_status,
    handle_tree_command,
    list_all_native_sessions,
    list_native_sessions,
    resolve_entry_ref,
    resolve_session_target,
    sanitize_label_text,
    visible_tree_entries,
)
from pipy_harness.native.extension_runtime import (
    EVENT_SESSION_SHUTDOWN,
    EVENT_SESSION_START,
    ExtensionCapabilityError,
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    ExtensionUiDriver,
    HookHandler,
    ExtensionActivationBatch,
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    ToolRenderDetailsWriter,
    _ExtensionToolPort,
    dispatch_extension_command,
    dispatch_extension_shortcut,
    normalize_shortcut_key,
    parse_extension_flag_tokens,
)
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.extension_hooks import (
    _activate_workspace_extensions,
    dispatch_before_agent_start_hooks,
    dispatch_before_provider_headers_hooks,
    dispatch_input_hooks,
    dispatch_session_before_hooks as dispatch_session_before_hooks,
    dispatch_tool_call_hooks,
    dispatch_tool_result_hooks,
    dispatch_user_bash_hooks,
)
from pipy_harness.native.package_runtime import (
    PackageResourceRoots,
    compose_package_runtime,
)
from pipy_harness.native.resources import (
    DISPATCH_LIST,
    WorkspaceResources,
    dispatch_resource_command,
)
from pipy_harness.native.themes import (
    NativeThemeStore,
    available_theme_names,
    resolve_active_theme_name,
    select_theme,
)
from pipy_harness.native.tui import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_CYCLE_NEXT,
    HOTKEY_MODEL_CYCLE_PREV,
    HOTKEY_MODEL_SELECT,
    HOTKEY_THINKING_CYCLE,
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
    TURN_ABORTED as TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    TURN_STEERED,
    ModelSelectorOption,
    ScopedModelRow,
    SettingsRow,
    TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
    ToolLoopTerminalUi,
    _CustomEntryRenderer,
    _LiveExtensionUiDriver,
    _TuiToolLoopRenderer,
    run_project_trust_selector,
)
from pipy_harness.native.ui import RenderingAgentEventAdapter
from pipy_harness.native.tools.bash import LocalShellResult, run_local_command
from pipy_harness.native.file_references import (
    FileReferenceResolution,
    resolve_file_references,
)
from pipy_harness.native.image_attachment import (
    ImageAttachmentResolution,
    resolve_image_attachments,
)
from pipy_harness.native.tools import (
    ToolDefinition,
    ToolPort,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tool_renderers import (
    _ToolLoopRenderer as _ToolLoopRenderer,
    _extension_tool_renderer_map,
    _parse_tool_input,
)
from pipy_harness.native.agent_request import (
    NativeProviderRequestHookContext,
    prepare_provider_request,
)
from pipy_harness.native.agent_loop_policy import (
    NativeAgentProviderRequestPolicy,
    NativeAgentToolPolicy,
    materialize_provider_request,
)


def _wait_for_tool_interrupt(
    terminal_ui: ToolLoopTerminalUi,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ToolExecutionInterruption:
    """Translate the terminal driver's string outcome at the composition seam."""

    outcome = terminal_ui.wait_for_active_turn_interrupt(
        done_event,
        cancel_event,
        accept_commands=True,
    )
    if outcome == TURN_SETTLED:
        return ToolExecutionInterruption.SETTLED
    if outcome == TURN_ABORTED:
        return ToolExecutionInterruption.OPERATOR_ABORT
    if outcome == TURN_LOCAL_COMMAND:
        return ToolExecutionInterruption.LOCAL_COMMAND
    raise RuntimeError(f"unexpected tool interrupt outcome: {outcome!r}")


def _wait_for_provider_interrupt(
    terminal_ui: ToolLoopTerminalUi,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ProviderTurnInterruption:
    """Translate terminal-driver strings into the provider-loop contract."""

    try:
        outcome = terminal_ui.wait_for_active_turn_interrupt(
            done_event, cancel_event, accept_queue=True
        )
    except KeyboardInterrupt:
        cancel_event.set()
        return ProviderTurnInterruption.OPERATOR_ABORT
    if outcome == TURN_SETTLED:
        return ProviderTurnInterruption.SETTLED
    if outcome == TURN_ABORTED:
        return ProviderTurnInterruption.OPERATOR_ABORT
    if outcome == TURN_STEERED:
        return ProviderTurnInterruption.STEERING
    if outcome == TURN_LOCAL_COMMAND:
        return ProviderTurnInterruption.LOCAL_COMMAND
    raise RuntimeError(f"unexpected provider interrupt outcome: {outcome!r}")


_PRICING_TABLE: dict[tuple[str, str], AgentTokenPricing] = {
    # OpenAI Codex subscription (GPT-5.x family) — approximate.
    ("openai-codex", "gpt-5"): AgentTokenPricing(
        input_per_million=1.25, output_per_million=10.00, reasoning_per_million=10.00
    ),
}


def _pricing_for(provider_name: str, model_id: str) -> AgentTokenPricing | None:
    """Return per-million-token pricing for (provider, model), or None.

    Falls back to a model-family prefix lookup so e.g. ``gpt-5.5`` reuses
    the ``gpt-5`` entry. ``None`` disables cost rendering for that
    selection; the bottom status keeps showing ``$0.000``.
    """

    direct = _PRICING_TABLE.get((provider_name, model_id))
    if direct is not None:
        return direct
    for (entry_provider, entry_model), price in _PRICING_TABLE.items():
        if entry_provider != provider_name:
            continue
        if model_id.startswith(entry_model):
            return price
    return None


_AGENT_HISTORY_KEEP_RECENT_GROUPS = 2
_AGENT_HISTORY_MAX_MESSAGES = 40
_AGENT_HISTORY_MAX_BYTES = 48 * 1024


def production_tool_registry() -> dict[str, ToolPort]:
    """Return the current production tool registry.

    `bash` is a real shell, matching Pi: it runs an arbitrary command in the
    workspace and returns combined, bounded stdout/stderr to the model. See
    `pipy_harness.native.tools.bash.BashTool`.
    """

    from pipy_harness.native.tools.bash import BashTool
    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.edit_diff import EditDiffTool
    from pipy_harness.native.tools.find import FindTool
    from pipy_harness.native.tools.grep import GrepTool
    from pipy_harness.native.tools.ls import LsTool
    from pipy_harness.native.tools.read import ReadTool
    from pipy_harness.native.tools.truncate import TruncateTool
    from pipy_harness.native.tools.write import WriteTool

    return {
        "read": ReadTool(),
        "ls": LsTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "write": WriteTool(),
        "edit": EditTool(),
        "edit_diff": EditDiffTool(),
        "truncate": TruncateTool(),
        "bash": BashTool(),
    }


def _tool_loop_command_names(
    resources: WorkspaceResources,
    extension_command_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Tool-loop slash-menu command set, honest to what can execute.

    The static built-in set is augmented with the ``/skill`` resource
    entry point (which always at least lists), every discovered prompt
    template registered as its own ``/<name>`` command (Pi shape), every
    discovered, non-reserved custom ``/<name>`` command, and any activated
    extension ``/<name>`` commands (appended last, never shadowing a
    built-in or custom command).
    """

    names = list(TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS)
    insert_at = (names.index("/model") + 1) if "/model" in names else len(names)
    names[insert_at:insert_at] = ["/skill"]
    for slash_name in resources.template_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in resources.custom_command_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in extension_command_names:
        if slash_name not in names:
            names.append(slash_name)
    return tuple(names)


def _tool_loop_command_descriptions(
    resources: WorkspaceResources,
    extension_descriptions: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the slash-menu descriptions with dispatch-honest precedence.

    The menu description for a name must describe what dispatching that name
    actually runs. ``dispatch_resource_command`` resolves a colliding name in
    the order built-in > prompt template > custom command, and extension
    commands dispatch last (lowest precedence). Descriptions are layered in
    the reverse order (lowest precedence first) so a later ``update`` for a
    higher-precedence source wins a collision — i.e. for a name shared by a
    template and a custom command, the menu shows the *template's*
    description, matching what runs.
    """

    descriptions: dict[str, str] = {}
    if extension_descriptions:
        descriptions.update(extension_descriptions)
    descriptions.update(resources.custom_command_descriptions())
    descriptions.update(resources.template_descriptions())
    descriptions.update(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    return descriptions


@dataclass(slots=True)
class _RunControlState:
    """Mutable holder for the control state a single ``run()`` invocation shares
    across its composition-root closures and the built-in command handler.

    Before this holder existed, these run-scope names were shared through a
    ~40-name ``nonlocal`` block reassigned by the built-in effect chain and read
    back by the REPL loop step and the extension/resource/persistence adapter
    closures. Routing them through one ``ctl`` instance removed those free-var
    captures so the built-in effect chain could be relocated into
    ``_BuiltinCommandInterpreter.interpret`` and the per-iteration loop step into
    ``_ReplLoopStep.step_once`` — both receive ``ctl`` explicitly as a keyword-only
    argument and mutate it in place. It is deliberately a plain mutable record with
    no behavior: the handlers and closures reassign ``ctl.<attr>`` exactly where they
    previously rebound the ``nonlocal`` name, and a ``/reload``, ``/new``,
    ``/resume``, ``/fork``, or ``/clone`` rebind stays visible to every other
    closure through the shared instance.
    """

    session_tree: NativeSessionTree
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

    @property
    def extension_generation(self) -> SessionExtensionGeneration:
        """The live extension generation, read under the session mutex.

        Every consumer keeps reading ``ctl.extension_generation`` exactly where
        it did before; the difference is that the read now goes through the
        session's one synchronization boundary instead of touching a bare
        attribute a reload could be rewriting.

        This is a per-access read, **not** the per-operation snapshot the
        concurrency contract ultimately requires: an operation that reads it
        twice would see two generations if a publication landed in between.
        Today it cannot, because the only publisher is ``/reload`` on the
        session thread. Converting consumers to
        :meth:`SessionGenerationRef.snapshot` is a precondition of the slice
        that lets a detached worker publish.
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
class _ExtensionCustomEntryRunState:
    """Narrow TUI adapter over the canonical extension generation.

    ``_CustomEntryRenderer`` intentionally accepts a structural live-state
    protocol. Keep that UI boundary cycle-free while forwarding every
    extension-owned value to the generation rather than mirroring it on run
    control.
    """

    ctl: _RunControlState

    @property
    def session_tree(self) -> NativeSessionTree:
        return self.ctl.session_tree

    @property
    def extension_renderer_map(self) -> Mapping[str, RegisteredMessageRenderer]:
        return self.ctl.extension_generation.runtime.message_renderers

    @property
    def extension_entry_renderer_map(
        self,
    ) -> Mapping[str, RegisteredEntryRenderer]:
        return self.ctl.extension_generation.runtime.entry_renderers

    @property
    def extension_message_outbox(self) -> list[QueuedUserMessage]:
        return self.ctl.extension_generation.runtime.outbox

    @property
    def extension_custom_message_outbox(self) -> list[QueuedCustomMessage]:
        return self.ctl.extension_generation.runtime.custom_outbox

    @property
    def extension_in_agent_turn(self) -> bool:
        return self.ctl.extension_in_agent_turn


class _BuiltinCommandInterpreter:
    """Composition-root handler that owns the built-in command effect chain.

    The controller classifies the built-in>resource>extension precedence and, for
    a continuing built-in, invokes this handler through the already-wired
    :meth:`CodingCommandEffects.interpret_builtin` port (symmetric with the
    resource and extension dispatch ports). :meth:`interpret` receives the run's
    mutable control-state holder ``ctl`` plus the run-loop collaborators
    (terminal UI, renderer, session-tree callbacks, extension runtime) explicitly
    and mutates ``ctl`` in place, so ``run()`` reads the reassigned session tree,
    tree filter mode, pending prefill, and ``/reload`` extension-runtime bundle
    back byte-identically after dispatch. The handler holds no state of its own;
    the per-action effect chain formerly lived as the ``_interpret_builtin_effect``
    closure nested in ``NativeToolReplSession.run()``.
    """

    __slots__ = ()

    def interpret(
        self,
        command_outcome: CodingCommandOutcome,
        *,
        session: "NativeToolReplSession",
        ctl: _RunControlState,
        coding_state: CodingSessionState,
        terminal_ui: ToolLoopTerminalUi | None,
        renderer: "_ToolLoopRenderer | _TuiToolLoopRenderer",
        error_stream: TextIO,
        emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter,
        keybindings: KeybindingsManager,
        settings: SettingsManager,
        cwd: Path,
        system_prompt: str,
        input_stream: TextIO,
        prompt_history_store: PromptHistoryStore,
        resource_options: RuntimeResourceOptions,
        tool_capabilities: NativeToolCapabilities,
        repl_input: "ToolLoopTerminalUi | NativeReplInput",
        diag: Callable[[str], None],
        apply_compaction: Callable[[str], str],
        apply_model_selection: Callable[[str], tuple[bool, str]],
        apply_auth_change: Callable[[str, str], str],
        rebuild_messages_from_tree: Callable[[], None],
        redraw_custom_entries_for_active_branch: Callable[[], None],
        refresh_legacy_footer: Callable[[], None],
        refresh_legacy_footer_with_usage: Callable[[], None],
        current_session_dir: Callable[[], Path],
        resolve_session_file: Callable[[str], Path | None],
        summarize_branch: Callable[[list[AgentMessage], str | None], str | None],
        # ``extension_session_allows`` takes keyword-only gate arguments
        # (operation/target/trigger), so ``Callable[..., bool]`` is the accurate
        # callback shape here rather than a positional parameter list.
        extension_session_allows: Callable[..., bool],
        extension_send_message: Callable[
            [str, str, bool, "Mapping[str, object]", object | None], object
        ],
        extension_render_details: ToolRenderDetailsWriter,
        extension_set_active_tools: Callable[[Sequence[str]], bool],
        _extension_notify: Callable[[str, str], None],
        _bind_unavailable_after_reload: Callable[[str], None],
    ) -> None:
        # The run's shared control state is reassigned through ``ctl.<attr>``;
        # the transient names this effect recomputes locally on every
        # invocation before reading (``fallback``/``fallback_provider``/
        # ``catalog_state``/``was_extension_selection`` in the reload provider
        # refresh, ``unknown_filter_names``/``known``/``unknown`` in the
        # reload tool-filter check, and the ``_registered_tool``/``_port``/
        # ``custom_message`` loop variables) stay function-local.
        if command_outcome.kind is CodingCommandOutcomeKind.CONTINUE:
            if command_outcome.action is CodingCommandAction.SHOW_HOTKEYS:
                # Render from the resolved keybinding manager so user
                # keybindings.json overrides remain reflected.
                hotkeys_text = render_hotkeys(keybindings)
                if terminal_ui is not None:
                    terminal_ui.add_notice(hotkeys_text)
                else:
                    print(hotkeys_text, file=error_stream)
            elif command_outcome.action is CodingCommandAction.SHOW_CHANGELOG:
                changelog_text = render_changelog(read_changelog_entries())
                if terminal_ui is not None:
                    terminal_ui.add_notice(changelog_text)
                else:
                    print(changelog_text, file=error_stream)
            elif command_outcome.action is CodingCommandAction.COPY_LAST_ANSWER:
                session._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    session._copy_last_answer(
                        coding_state.messages,
                        error_stream=error_stream,
                    ),
                )
            elif command_outcome.action is CodingCommandAction.SHOW_SESSION_STATUS:
                diag(format_session_status(ctl.session_tree))
            elif command_outcome.action is CodingCommandAction.COMPACT:
                # Local-only: reduce provider-visible history while
                # preserving the shared manual/automatic compaction
                # policy, extension gate, and durable write ordering.
                diag(apply_compaction("manual"))
            elif command_outcome.action is CodingCommandAction.SESSION_NAME:
                session_name_argument = command_outcome.argument
                if type(session_name_argument) is not ProductContent:
                    raise TypeError(
                        "SESSION_NAME requires an exact ProductContent argument"
                    )
                if not session_name_argument.value:
                    diag(
                        "pipy: current session name: "
                        + (
                            sanitize_label_text(ctl.session_tree.name)
                            if ctl.session_tree.name
                            else "(unnamed)"
                        )
                    )
                else:
                    ctl.session_tree.append_session_info(session_name_argument.value)
                    diag(f"pipy: session named {session_name_argument.value!r}.")
            elif command_outcome.action is CodingCommandAction.NEW_SESSION:
                # Start a fresh native product session in the same store.
                if extension_session_allows(
                    ctl.extension_generation.runtime.session_before_switch_hooks,
                    operation="switch",
                    target="new",
                ):
                    session_dir = (
                        ctl.session_tree.path.parent
                        if ctl.session_tree.path is not None
                        else None
                    )
                    ctl.session_tree = NativeSessionTree.create(
                        cwd,
                        session_dir=session_dir,
                        persist=ctl.session_tree.persist,
                    )
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: started a new native session "
                        f"({sanitize_label_text(ctl.session_tree.session_id[:8])})."
                    )
            elif command_outcome.action is CodingCommandAction.SESSION_TREE:
                tree_argument = command_outcome.argument
                if type(tree_argument) is not ProductContent:
                    raise TypeError(
                        "SESSION_TREE requires an exact ProductContent argument"
                    )
                argument = tree_argument.value
                tree_sub = argument.split(maxsplit=1)[0].lower() if argument else ""
                tree_may_change = (
                    not argument and terminal_ui is not None
                ) or tree_sub in {"select", "label", "filter"}
                tree_allowed = not tree_may_change or extension_session_allows(
                    ctl.extension_generation.runtime.session_before_tree_hooks,
                    operation="tree",
                    target=argument or None,
                )
                if tree_allowed:
                    tree_outcome = session._handle_tree_command(
                        argument,
                        session_tree=ctl.session_tree,
                        terminal_ui=terminal_ui,
                        error_stream=error_stream,
                        repl_input=repl_input,
                        filter_mode=ctl.tree_filter_mode,
                        rebuild_messages=rebuild_messages_from_tree,
                        summarizer=summarize_branch,
                    )
                    if tree_outcome.filter_mode is not None:
                        ctl.tree_filter_mode = tree_outcome.filter_mode
                    if tree_outcome.prefill is not None:
                        ctl.pending_prefill = tree_outcome.prefill
            elif command_outcome.action is CodingCommandAction.SESSION_RESUME:
                resume_argument = command_outcome.argument
                if type(resume_argument) is not ProductContent:
                    raise TypeError(
                        "SESSION_RESUME requires an exact ProductContent argument"
                    )
                argument = resume_argument.value
                resume_tokens = argument.split()
                resume_sub = resume_tokens[0].lower() if resume_tokens else ""

                def _list_sessions(named_only: bool = False) -> None:
                    sessions = list_native_sessions(current_session_dir())
                    sessions = (
                        [session for session in sessions if session.name]
                        if named_only
                        else sessions
                    )
                    if not sessions:
                        diag("pipy: no native sessions found for this workspace.")
                        return
                    scope = "named " if named_only else ""
                    diag(f"pipy: {scope}native sessions (newest first):")
                    for index, entry in enumerate(sessions, start=1):
                        label = (
                            sanitize_label_text(entry.name)
                            if entry.name
                            else "(unnamed)"
                        )
                        diag(
                            f"  {index}. "
                            f"{sanitize_label_text(entry.session_id[:8])} "
                            f"{label} "
                            f"messages={entry.message_count} "
                            f"file={sanitize_label_text(entry.path.name)}"
                        )
                    diag("pipy: use '/resume <number|id>' to open a session.")

                if (
                    not argument
                    and terminal_ui is not None
                    and hasattr(terminal_ui, "run_session_picker")
                ):
                    picked_session = session._run_interactive_session_picker(
                        session_tree=ctl.session_tree,
                        terminal_ui=terminal_ui,
                    )
                    if picked_session is None:
                        diag("pipy: /resume cancelled.")
                    elif (
                        ctl.session_tree.path is not None
                        and picked_session == ctl.session_tree.path
                    ):
                        diag("pipy: already on the selected native session.")
                    elif extension_session_allows(
                        ctl.extension_generation.runtime.session_before_switch_hooks,
                        operation="switch",
                        target=str(picked_session),
                    ):
                        ctl.session_tree = NativeSessionTree.open(picked_session)
                        rebuild_messages_from_tree()
                        redraw_custom_entries_for_active_branch()
                        diag(
                            "pipy: resumed native session "
                            f"{sanitize_label_text(ctl.session_tree.session_id[:8])} "
                            f"({sanitize_label_text(ctl.session_tree.name) if ctl.session_tree.name else 'unnamed'})."
                        )
                elif not argument:
                    _list_sessions()
                elif resume_sub == "named":
                    _list_sessions(named_only=True)
                elif resume_sub == "rename":
                    if len(resume_tokens) < 3:
                        diag("pipy: usage: /resume rename <number|id> <name>")
                    else:
                        target = resolve_session_file(resume_tokens[1])
                        if target is None:
                            diag(
                                f"pipy: no native session matched {resume_tokens[1]!r}."
                            )
                        else:
                            renamed = NativeSessionTree.open(target)
                            new_name = " ".join(resume_tokens[2:])
                            renamed.append_session_info(new_name)
                            diag(
                                "pipy: renamed session "
                                f"{sanitize_label_text(renamed.session_id[:8])} "
                                f"to {new_name!r}."
                            )
                elif resume_sub == "delete":
                    confirm = "--yes" in resume_tokens[1:]
                    refs = [token for token in resume_tokens[1:] if token != "--yes"]
                    if not refs:
                        diag("pipy: usage: /resume delete <number|id> --yes")
                    else:
                        target = resolve_session_file(refs[0])
                        if target is None:
                            diag(f"pipy: no native session matched {refs[0]!r}.")
                        elif (
                            ctl.session_tree.path is not None
                            and target == ctl.session_tree.path
                        ):
                            diag("pipy: cannot delete the active native session.")
                        elif not confirm:
                            diag(
                                "pipy: deletion needs confirmation; "
                                "re-run "
                                f"'/resume delete {refs[0]} --yes'. This "
                                "removes only the native session file, "
                                "never pipy-session archive records."
                            )
                        else:
                            _ok, detail = delete_native_session(target)
                            diag(f"pipy: {detail}")
                else:
                    target = resolve_session_file(argument)
                    if target is None:
                        diag(f"pipy: no native session matched {argument!r}.")
                    elif extension_session_allows(
                        ctl.extension_generation.runtime.session_before_switch_hooks,
                        operation="switch",
                        target=str(target),
                    ):
                        ctl.session_tree = NativeSessionTree.open(target)
                        rebuild_messages_from_tree()
                        redraw_custom_entries_for_active_branch()
                        diag(
                            "pipy: resumed native session "
                            f"{sanitize_label_text(ctl.session_tree.session_id[:8])} "
                            f"({sanitize_label_text(ctl.session_tree.name) if ctl.session_tree.name else 'unnamed'})."
                        )
            elif command_outcome.action in {
                CodingCommandAction.SESSION_FORK,
                CodingCommandAction.SESSION_CLONE,
            }:
                if command_outcome.action is CodingCommandAction.SESSION_FORK:
                    fork_argument = command_outcome.argument
                    if type(fork_argument) is not ProductContent:
                        raise TypeError(
                            "SESSION_FORK requires an exact ProductContent argument"
                        )
                    argument = fork_argument.value
                else:
                    argument = ""
                if ctl.session_tree.path is None:
                    command_name = {
                        CodingCommandAction.SESSION_FORK: "/fork",
                        CodingCommandAction.SESSION_CLONE: "/clone",
                    }[command_outcome.action]
                    diag(f"pipy: {command_name} requires a persistent native session.")
                else:
                    fork_leaf: str | None = None
                    fork_target_resolved = True
                    if argument:
                        target_entry = resolve_entry_ref(
                            ctl.session_tree,
                            argument,
                            filter_mode=ctl.tree_filter_mode,
                        )
                        if target_entry is None:
                            diag(f"pipy: no tree entry matched {argument!r}.")
                            fork_target_resolved = False
                        else:
                            fork_leaf = target_entry.id
                    else:
                        fork_leaf = ctl.session_tree.get_leaf_id()
                    if fork_target_resolved and extension_session_allows(
                        ctl.extension_generation.runtime.session_before_fork_hooks,
                        operation="fork",
                        target=fork_leaf,
                    ):
                        forked_tree = NativeSessionTree.fork_from(
                            ctl.session_tree.path,
                            cwd,
                            leaf_id=fork_leaf,
                            session_dir=ctl.session_tree.path.parent,
                        )
                        ctl.session_tree = forked_tree
                        rebuild_messages_from_tree()
                        success_text = {
                            CodingCommandAction.SESSION_FORK: (
                                "forked into new native session "
                            ),
                            CodingCommandAction.SESSION_CLONE: (
                                "cloned active branch into new native session "
                            ),
                        }[command_outcome.action]
                        diag(
                            f"pipy: {success_text}"
                            f"{sanitize_label_text(ctl.session_tree.session_id[:8])}."
                        )
            elif command_outcome.action is CodingCommandAction.SESSION_EXPORT:
                session._export_session(
                    command_outcome.argument,
                    session_tree=ctl.session_tree,
                    cwd=cwd,
                    system_prompt=system_prompt,
                    diagnostic=diag,
                )
            elif command_outcome.action is CodingCommandAction.SESSION_IMPORT:
                imported_tree = session._import_session(
                    command_outcome.argument,
                    cwd=cwd,
                    input_stream=input_stream,
                    error_stream=error_stream,
                    current_session_dir=current_session_dir,
                    session_switch_allows=lambda target: extension_session_allows(
                        ctl.extension_generation.runtime.session_before_switch_hooks,
                        operation="switch",
                        target=target,
                    ),
                    diagnostic=diag,
                )
                if imported_tree is not None:
                    ctl.session_tree = imported_tree
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: imported native session "
                        f"{sanitize_label_text(ctl.session_tree.session_id[:8])}."
                    )
            elif command_outcome.action is CodingCommandAction.SESSION_SHARE:
                token = resolve_github_token()
                if not token:
                    diag(
                        "pipy: No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`."
                    )
                else:
                    try:
                        result = session._share_native_session_command(
                            session_tree=ctl.session_tree,
                            token=token,
                            terminal_ui=terminal_ui,
                            error_stream=error_stream,
                        )
                    except NativeExportError as exc:
                        diag(f"pipy: {exc}")
                    else:
                        if result is not None:
                            if result.viewer_url:
                                diag(
                                    f"pipy: share URL: {result.viewer_url}\npipy: gist URL: {result.gist_url}"
                                )
                            else:
                                diag(f"pipy: gist URL: {result.gist_url}")
            elif command_outcome.action is CodingCommandAction.SETTINGS:
                if terminal_ui is not None:
                    session._drive_settings_dialog(
                        terminal_ui,
                        prompt_history_store,
                        provider=coding_state.provider,
                        apply_model_selection=apply_model_selection,
                        apply_auth_change=apply_auth_change,
                        settings=settings,
                        session_tree=ctl.session_tree,
                        error_stream=error_stream,
                    )
                else:
                    for overlay_line in session._settings_overlay_lines(
                        settings,
                        provider=coding_state.provider,
                    ):
                        print(overlay_line, file=error_stream)
            elif command_outcome.action is CodingCommandAction.TRUST_PROJECT:
                session._handle_trust_command(
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    cwd=cwd,
                    settings=settings,
                )
            elif command_outcome.action in {
                CodingCommandAction.MODEL,
                CodingCommandAction.SCOPED_MODELS,
                CodingCommandAction.LOGIN,
                CodingCommandAction.LOGOUT,
            }:
                command_argument = command_outcome.argument
                if type(command_argument) is not ProductContent:
                    raise TypeError(
                        f"{command_outcome.action.name} requires an exact "
                        "ProductContent argument"
                    )
                argument = command_argument.value
                if command_outcome.action is CodingCommandAction.MODEL:
                    state = session.provider_state
                    if not isinstance(state, NativeReplProviderState):
                        session._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            "pipy: /model is unavailable for this REPL provider state.",
                        )
                    elif argument:
                        _ok, message = apply_model_selection(argument)
                        session._emit_diagnostic(terminal_ui, error_stream, message)
                    elif terminal_ui is not None:
                        ui_options, selections = session._model_selector_rows(state)
                        current = state.current_selection()
                        current_index = next(
                            (
                                index
                                for index, selection in enumerate(selections)
                                if selection.provider_name == current.provider_name
                                and selection.model_id == current.model_id
                            ),
                            0,
                        )
                        chosen = terminal_ui.run_model_selector(
                            ui_options, current_index=current_index
                        )
                        if chosen is not None:
                            _ok, message = apply_model_selection(
                                selections[chosen].reference
                            )
                            terminal_ui.add_notice(message)
                    else:
                        for overlay_line in session._settings_overlay_lines(
                            settings,
                            provider=coding_state.provider,
                        ):
                            print(overlay_line, file=error_stream)
                elif command_outcome.action is CodingCommandAction.SCOPED_MODELS:
                    # Local-only: view/set/clear the enabledModels
                    # patterns constraining model cycling, or cycle over
                    # the scoped set without a provider/tool turn.
                    state = session.provider_state
                    available_refs = (
                        [
                            option.selection.reference
                            for option in state.model_options()
                            if option.available
                        ]
                        if isinstance(state, NativeReplProviderState)
                        else []
                    )
                    patterns = settings.get_enabled_models()
                    scoped = filter_scoped_references(available_refs, patterns)
                    if (
                        not argument
                        and terminal_ui is not None
                        and isinstance(state, NativeReplProviderState)
                        and available_refs
                    ):
                        session._open_scoped_models_overlay(
                            terminal_ui, state=state, settings=settings
                        )
                    elif not argument:
                        pattern_text = (
                            ", ".join(patterns) if patterns else "(none — full catalog)"
                        )
                        cycle_text = ", ".join(scoped) if scoped else "(none available)"
                        for ctl.line in (
                            "pipy: scoped models:",
                            f"  patterns: {pattern_text}",
                            f"  cycle set: {cycle_text}",
                        ):
                            session._emit_diagnostic(
                                terminal_ui, error_stream, ctl.line
                            )
                    elif argument == "clear":
                        try:
                            settings.set_enabled_models([])
                            message = (
                                "pipy: scoped models cleared (cycle uses "
                                "the full catalog)."
                            )
                        except RuntimeError as exc:
                            message = f"pipy: could not update scoped models: {exc}"
                        session._emit_diagnostic(terminal_ui, error_stream, message)
                    elif argument in {"next", "prev"}:
                        current_ref = (
                            state.current_selection().reference
                            if isinstance(state, NativeReplProviderState)
                            else ""
                        )
                        cycle_target = next_reference(
                            scoped,
                            current_ref,
                            forward=argument == "next",
                        )
                        if cycle_target is None:
                            session._emit_diagnostic(
                                terminal_ui,
                                error_stream,
                                "pipy: no models available to cycle.",
                            )
                        else:
                            _ok, message = apply_model_selection(cycle_target)
                            session._emit_diagnostic(terminal_ui, error_stream, message)
                    else:
                        new_patterns = argument.split()
                        try:
                            settings.set_enabled_models(new_patterns)
                            message = "pipy: scoped models set: " + ", ".join(
                                new_patterns
                            )
                        except RuntimeError as exc:
                            message = f"pipy: could not update scoped models: {exc}"
                        session._emit_diagnostic(terminal_ui, error_stream, message)
                else:
                    auth_action = (
                        "login"
                        if command_outcome.action is CodingCommandAction.LOGIN
                        else "logout"
                    )
                    message = apply_auth_change(auth_action, argument)
                    session._emit_diagnostic(terminal_ui, error_stream, message)
            elif command_outcome.action is CodingCommandAction.RELOAD:
                # One publication: the gate is open from the moment this
                # reload starts reading live provider selection, thinking
                # level, and tool visibility until every derived projection is
                # published. Extension mutation ports fail closed for that
                # window, so a change accepted mid-reload cannot be silently
                # overwritten by values derived from what was read earlier.
                # The context manager closes the gate even if preparation
                # raises, so a failed reload never leaves mutations refused.
                with ctl.generation_ref.publishing():
                    # Local-only: re-read settings (both scopes), keybindings, and
                    # workspace resources, then re-apply derived UI settings. Runs
                    # between turns at the prompt, so no provider turn or compaction
                    # is in flight. A settings/theme load error keeps the prior good
                    # state for that scope; a malformed keybindings.json falls back
                    # to the built-in defaults. No provider turn, no tool call.
                    settings.reload()
                    keybindings.reload()
                    # Re-resolve package roots + re-install the theme
                    # registry so a package added/removed since startup is
                    # reflected after /reload.
                    ctl.package_roots = compose_package_runtime(
                        settings,
                        cwd,
                        include_package_themes=not resource_options.no_themes,
                        explicit_theme_paths=resource_options.theme_paths,
                    )
                    ctl.workspace_resources = WorkspaceResources.discover(
                        cwd,
                        package_roots=ctl.package_roots,
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
                    # Re-discover + re-activate extensions on reload (Pi
                    # /reload also reloads extensions). A failing extension is
                    # disabled without affecting the session. Clear any chrome
                    # set by the prior generation first so a removed/disabled
                    # extension cannot leave stale widgets/header/footer/title.
                    if terminal_ui is not None:
                        terminal_ui.clear_extension_chrome()
                    reloaded_extension_runtime = _activate_workspace_extensions(
                        cwd,
                        ctl.workspace_resources,
                        tuple(session.tool_registry.keys()),
                        package_roots=()
                        if resource_options.no_extensions
                        else ctl.package_roots.extensions,
                        extension_patterns=settings.get_extensions_patterns(),
                        explicit_extension_paths=resource_options.extension_paths,
                        include_default_extensions=not resource_options.no_extensions,
                        include_workspace_defaults=settings.project_trusted,
                    )
                    # Preserve the established reload ordering: the newly activated
                    # contributions become live before extension custom messages and
                    # flag parsing. A malformed flag therefore still leaves the new
                    # runtime paired with the prior parsed values; Slice 3 owns making
                    # this replacement transactional.
                    ctl.extension_generation = SessionExtensionGeneration(
                        runtime=reloaded_extension_runtime,
                        flag_values=ctl.extension_generation.flag_values,
                    )
                    for custom_message in reloaded_extension_runtime.custom_messages:
                        extension_send_message(
                            custom_message.custom_type,
                            custom_message.content,
                            custom_message.display,
                            custom_message.options,
                            custom_message.details,
                        )
                    reloaded_flag_values, reloaded_flag_error = (
                        parse_extension_flag_tokens(
                            reloaded_extension_runtime.flags,
                            tuple(resource_options.extension_flag_tokens),
                        )
                    )
                    if reloaded_flag_error is not None:
                        session._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            f"pipy: {reloaded_flag_error}",
                        )
                    else:
                        ctl.extension_generation = SessionExtensionGeneration(
                            runtime=reloaded_extension_runtime,
                            flag_values=reloaded_flag_values,
                        )
                        emitter.set_flags(ctl.extension_generation.flag_values)
                    state = session.provider_state
                    if isinstance(state, NativeReplProviderState):
                        runtime = state.model_runtime
                        if runtime is not None:
                            catalog_state = runtime.catalog
                            was_extension_selection = (
                                state.current_selection_uses_extension_provider()
                            )
                            catalog_state.refresh()
                            catalog_state.set_extension_provider_contributions(
                                ctl.extension_generation.runtime.providers,
                                ctl.extension_generation.runtime.unregistered_providers,
                            )
                            selection_disappeared = (
                                not state.current_selection_supported()
                                or (
                                    was_extension_selection
                                    and not state.current_selection_uses_extension_provider()
                                )
                            )
                            if not selection_disappeared:
                                if state.current_selection_uses_extension_provider():
                                    refreshed_provider = state.current_provider()
                                    if getattr(
                                        refreshed_provider,
                                        "supports_tool_calls",
                                        False,
                                    ):
                                        coding_state.refresh_provider(
                                            refreshed_provider
                                        )
                                    else:
                                        fallback = state.reset_to_first_available_model(
                                            require_tool_calls=True
                                        )
                                        if fallback is not None:
                                            fallback_provider = state.current_provider()
                                            coding_state.rebind_provider(
                                                fallback_provider,
                                                provider_name=fallback.provider_name,
                                                model_id=fallback.model_id,
                                                usage_accumulator=(
                                                    AgentUsageAccumulator(
                                                        _pricing_for(
                                                            fallback.provider_name,
                                                            fallback.model_id,
                                                        )
                                                    )
                                                ),
                                            )
                                            session._emit_diagnostic(
                                                terminal_ui,
                                                error_stream,
                                                "pipy: active model no longer "
                                                "supports tool calls after reload; "
                                                f"selected {fallback.reference}.",
                                            )
                                            # Post-commit: the fallback is now
                                            # bound, so persisting it is safe
                                            # and its failure is reported.
                                            persistence_error = (
                                                _report_default_persistence(state)
                                            )
                                            if persistence_error is not None:
                                                session._emit_diagnostic(
                                                    terminal_ui,
                                                    error_stream,
                                                    persistence_error,
                                                )
                                        else:
                                            message = (
                                                "active model no longer supports "
                                                "tool calls after reload and no "
                                                "available tool-capable fallback "
                                                "was found"
                                            )
                                            _bind_unavailable_after_reload(message)
                                            session._emit_diagnostic(
                                                terminal_ui,
                                                error_stream,
                                                f"pipy: {message}.",
                                            )
                            else:
                                fallback = state.reset_to_first_available_model(
                                    require_tool_calls=True
                                )
                                if fallback is not None:
                                    fallback_provider = state.current_provider()
                                    coding_state.rebind_provider(
                                        fallback_provider,
                                        provider_name=fallback.provider_name,
                                        model_id=fallback.model_id,
                                        usage_accumulator=AgentUsageAccumulator(
                                            _pricing_for(
                                                fallback.provider_name,
                                                fallback.model_id,
                                            )
                                        ),
                                    )
                                    session._emit_diagnostic(
                                        terminal_ui,
                                        error_stream,
                                        "pipy: active model disappeared on "
                                        "reload; selected "
                                        f"{fallback.reference}.",
                                    )
                                    # Post-commit: the fallback is bound.
                                    persistence_error = _report_default_persistence(
                                        state
                                    )
                                    if persistence_error is not None:
                                        session._emit_diagnostic(
                                            terminal_ui,
                                            error_stream,
                                            persistence_error,
                                        )
                                else:
                                    message = (
                                        "active model disappeared on reload and "
                                        "no available tool-capable fallback was "
                                        "found"
                                    )
                                    _bind_unavailable_after_reload(message)
                                    session._emit_diagnostic(
                                        terminal_ui,
                                        error_stream,
                                        f"pipy: {message}.",
                                    )
                    # Replace the run's extension capability registry and
                    # custom renderer map with the reloaded generation.
                    reloaded_tool_renderers = _extension_tool_renderer_map(
                        ctl.extension_generation.runtime.tools
                    )
                    renderer.refresh_tool_renderers(reloaded_tool_renderers)
                    reloaded_tool_registry: dict[str, ToolPort] = {}
                    for _registered_tool in ctl.extension_generation.runtime.tools:
                        _port = _ExtensionToolPort(
                            _registered_tool,
                            has_ui=terminal_ui is not None,
                            notify_sink=_extension_notify,
                            set_active_tools_fn=lambda names: (
                                extension_set_active_tools(names)
                            ),
                            flags=ctl.extension_generation.flag_values,
                            render_details_sink=extension_render_details,
                            project_trusted=settings.project_trusted,
                        )
                        reloaded_tool_registry[_port.definition.name] = _port
                    # Candidate-only build, then one non-fallible publication. The
                    # split is what Slice 3.9 needs to move the publication into
                    # the atomic commit; today the two steps still run back to back
                    # so reload behavior is unchanged.
                    tool_capabilities.publish(
                        tool_capabilities.prepare_extensions(reloaded_tool_registry)
                    )
                    unknown_filter_names = tool_capabilities.unknown_filter_names
                    if unknown_filter_names:
                        known = (
                            ", ".join(sorted(tool_capabilities.registered_names))
                            or "<none>"
                        )
                        unknown = ", ".join(unknown_filter_names)
                        session._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            f"pipy: unknown tool name(s): {unknown}. Known tools: {known}",
                        )
                    # Refresh the emitter's lifecycle hooks so reloaded
                    # extensions observe subsequent agent/turn events.
                    emitter.set_lifecycle_hooks(
                        ctl.extension_generation.runtime.lifecycle_hooks
                    )
                    emitter.set_flags(ctl.extension_generation.flag_values)
                    # Re-apply the edited theme (settings is source of truth over the
                    # persisted store) and the derived UI settings.
                    reloaded_theme = settings.get_theme()
                    if reloaded_theme:
                        os.environ["PIPY_THEME"] = reloaded_theme
                    if terminal_ui is not None:
                        terminal_ui.autocomplete_max_visible = (
                            settings.get_autocomplete_max_visible()
                        )
                        terminal_ui.command_names = _tool_loop_command_names(
                            ctl.workspace_resources,
                            ctl.extension_generation.runtime.menu_names,
                        )
                        terminal_ui.command_descriptions = (
                            _tool_loop_command_descriptions(
                                ctl.workspace_resources,
                                ctl.extension_generation.runtime.descriptions,
                            )
                        )
                        terminal_ui.extension_shortcut_keys = frozenset(
                            ctl.extension_generation.runtime.shortcuts
                        )
                        redraw_custom_entries_for_active_branch()
                    load_errors = settings.load_errors()
                    if load_errors:
                        for scope, detail in load_errors.items():
                            session._emit_diagnostic(
                                terminal_ui,
                                error_stream,
                                f"pipy: kept prior {scope} settings ({detail}).",
                            )
                    if session.verbose_startup or not settings.get_quiet_startup():
                        print_startup_chrome(
                            error_stream,
                            cwd=cwd,
                            include_workspace_defaults=settings.project_trusted,
                        )
                    saved_implicit_trust = (
                        session._maybe_save_implicit_trust_after_reload(
                            cwd=cwd,
                            settings=settings,
                            terminal_ui=terminal_ui,
                            error_stream=error_stream,
                        )
                    )
                # Everything the reload publishes is now live, so close the
                # gate before running post-reload extension hooks. A
                # `session_start` hook from the freshly activated generation
                # may legitimately call setModel / setThinkingLevel /
                # setActiveTools, and refusing those would be a behavior
                # change rather than a protection.
                emitter.fire_lifecycle(EVENT_SESSION_START, reason="reload")
                session._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    (
                        "pipy: reloaded settings, keybindings, and resources; "
                        "saved project trust."
                        if saved_implicit_trust
                        else "pipy: reloaded settings, keybindings, and resources."
                    ),
                )
            if command_outcome.footer_policy is CodingCommandFooterPolicy.STANDARD:
                refresh_legacy_footer()
            elif command_outcome.footer_policy is CodingCommandFooterPolicy.USAGE_AWARE:
                refresh_legacy_footer_with_usage()
            else:
                raise AssertionError("handled command requires a closed footer policy")


def _report_default_persistence(
    state: "NativeReplProviderState",
) -> str | None:
    """Drain a queued default after its selection is live.

    Returns the diagnostic so a caller can surface it. Persistence is
    irreversible, so it deliberately runs only once the semantic rebind has
    completed; a failure leaves the live selection untouched and says so.
    """

    return state.flush_pending_default()


def _deny_model_mutation(_reference: str) -> bool:
    """Refuse a mid-turn model switch (hook contexts that forbid it)."""

    return False


@dataclass(frozen=True, slots=True, kw_only=True)
class _ProviderMutationEffects:
    """Composition-root handler owning the provider/model/auth/compaction
    mutation effects.

    Symmetric with :class:`_CustomEntryRenderer`, :class:`_ReplLoopStep`, and
    :class:`_BuiltinCommandInterpreter`, these bodies formerly lived as the
    ``apply_model_selection``/``apply_auth_change``/``apply_compaction``/
    ``_append_durable_compaction``/``extension_set_active_tools``/
    ``extension_set_model``/``extension_set_thinking_level`` closures nested in
    ``NativeToolReplSession.run()``. They call one another densely (the compaction
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
    handler's ``apply_compaction``/``extension_set_*`` ports, the
    extension-dispatch and provider-request/tool-policy hook seams, and the
    product-session ``_persist_compaction`` durable-append callback.
    """

    session: NativeToolReplSession
    ctl: _RunControlState
    coding_state: CodingSessionState
    product_session: CodingProductSessionCoordinator
    terminal_ui: ToolLoopTerminalUi | None
    tool_capabilities: NativeToolCapabilities
    settings: SettingsManager
    cwd: Path
    input_stream: TextIO
    error_stream: TextIO
    refresh_footer_text: Callable[[], None]
    extension_notify: Callable[[str, str], None]
    extension_ui_driver: _LiveExtensionUiDriver | None
    # Orders a mutation's decision against its own follow-on I/O. Two callers
    # that assign under the session mutex and then append to the session tree
    # outside it could otherwise persist their changes in the opposite order,
    # leaving the durable record disagreeing with live state. Held *outside*
    # the session mutex, never inside it, so file I/O still never runs under
    # the session boundary.
    mutation_io_lock: "threading.RLock"

    def _mutation_refused_during_publication(self) -> bool:
        """Whether a reload is mid-publication, so mutations must fail closed.

        These three ports are reachable from an extension handler on a detached
        worker thread. A reload reads the live provider selection, thinking
        level, and tool visibility, then republishes values derived from them;
        a mutation accepted in that window would be silently overwritten at the
        swap. Refusing is the fail-closed direction and matches the ports'
        existing contract of returning ``False`` when a change is not applied.

        **Admission is atomic only where the effect is.** Reading this flag and
        then applying a mutation are two critical sections, so a worker can
        pass the check and land its effect after a reload opens the gate.
        ``extension_set_active_tools`` and ``extension_set_thinking_level``
        close that window by holding the session mutex across the check and the
        assignment, with their session-tree and rendering effects moved after
        the critical section. ``extension_set_model`` cannot yet: its effect
        persists a default part-way through, and holding the session mutex
        across file I/O is exactly what the concurrency contract forbids.
        Closing its window needs provider construction and persistence split
        out of the mutation, which is Slice 3.7c and 3.8 work; until then the
        gate narrows that one port's window rather than eliminating it, and
        this is recorded as a residual in the rebuild plan.
        """

        return self.ctl.generation_ref.publication_pending

    def extension_set_active_tools(self, tool_names: Sequence[str]) -> bool:
        """Restrict model-visible tools for future provider requests.

        The check and the mutation share one critical section — the tool
        capability owner takes the same session mutex — so a publication
        cannot open between admitting this call and applying it.
        """

        with self.ctl.generation_ref.lock:
            if self._mutation_refused_during_publication():
                return False
            return self.tool_capabilities.set_active_tools(tool_names)

    def extension_set_model(self, reference: str) -> bool:
        if self._mutation_refused_during_publication():
            return False
        ok, _message = self.apply_model_selection(reference)
        return ok

    def extension_set_thinking_level(self, level: str) -> bool:
        """Set the active reasoning level through the provider state.

        The gate check, the eligibility checks, and the assignment share one
        critical section, so a publication cannot open between admitting this
        call and applying it. Everything after the assignment — the session-tree
        append and the footer refresh — is a post-mutation effect and runs
        outside that lock, keeping file I/O and rendering off the session mutex.

        ``mutation_io_lock`` spans the decision *and* those effects so two
        concurrent callers cannot assign in one order and append in the other,
        which would leave the durable thinking-level record disagreeing with
        live state. Lock order is always this lock first, then the session
        mutex.
        """

        with self.mutation_io_lock:
            with self.ctl.generation_ref.lock:
                if self._mutation_refused_during_publication():
                    return False
                # Normalize exactly once: persisting a separately re-derived
                # value could disagree with what was assigned.
                normalized = self._assign_thinking_level_locked(level)
                if normalized is None:
                    return False
            self.ctl.session_tree.append_thinking_level_change(normalized)
            self.refresh_footer_text()
        return True

    def _assign_thinking_level_locked(self, level: str) -> str | None:
        """Validate and assign the level, returning the value actually set.

        Caller holds the session mutex. Returns ``None`` when the level is
        refused, so the caller persists exactly the string that was assigned
        rather than re-deriving it.
        """

        state = self.session.provider_state
        if not isinstance(state, NativeReplProviderState):
            return None
        normalized = str(level).strip().lower()
        if normalized not in THINKING_LEVELS:
            return None
        current = state.current_selection()
        supports_thinking = any(
            option.selection.provider_name == current.provider_name
            and option.selection.model_id == current.model_id
            and bool(option.reasoning)
            for option in state.model_options()
        )
        if normalized != "off" and not supports_thinking:
            return None
        state.thinking_level = normalized
        return normalized

    def model_runtime_control(
        self, *, allow_model: bool = True
    ) -> ExtensionModelRuntimeControl:
        """Bundle the three model-runtime control callables for a context.

        The single adapter every command/hook/tool seam threads instead of
        passing the three bare callables. When ``allow_model`` is ``False``
        (the mid-turn tool_call / tool_result / before_provider_request hook
        paths, where a live model switch is not permitted), ``set_model``
        fails closed by returning ``False``.
        """

        return ExtensionModelRuntimeControl(
            set_active_tools_fn=self.extension_set_active_tools,
            set_model_fn=(
                self.extension_set_model if allow_model else _deny_model_mutation
            ),
            set_thinking_level_fn=self.extension_set_thinking_level,
        )

    def apply_model_selection(self, reference: str) -> tuple[bool, str]:
        """Select ``reference`` through the provider-state boundary.

        Mirrors the no-tool ``/model`` path: on success it rebinds the live
        provider, clears the in-memory conversation context, rebinds the
        usage meter, and refreshes the footer/status model label so the next
        provider turn is constructed with the new provider/model. The switch
        is refused (and the previous selection restored) when the chosen
        provider does not advertise tool-call support, which the product
        REPL requires. No provider turn happens here.
        """

        state = self.session.provider_state
        if not isinstance(state, NativeReplProviderState):
            return False, ("pipy: /model is unavailable for this REPL provider state.")
        previous_selection = state.current_selection()
        ok, message = state.select_model(reference)
        if not ok:
            return False, message
        new_provider = state.current_provider()
        if not getattr(new_provider, "supports_tool_calls", False):
            # Restore the prior selection directly rather than via
            # select_model(): the previous selection may be an explicit,
            # tool-capable provider that is not "available" under the
            # env-credential probe (e.g. an injected provider), in which
            # case re-selecting it would fail and silently leave the
            # rejected selection (and persisted default) in place.
            state.selection = previous_selection
            state._save_default(previous_selection)
            return False, (
                f"pipy: {reference} does not support tool calls in "
                "tool-loop mode; selection unchanged."
            )
        selection = state.current_selection()
        self.coding_state.rebind_provider(
            new_provider,
            provider_name=selection.provider_name,
            model_id=selection.model_id,
            usage_accumulator=AgentUsageAccumulator(
                _pricing_for(selection.provider_name, selection.model_id)
            ),
        )
        self.refresh_footer_text()
        # Post-commit: the selection is already live. Persisting it is
        # irreversible file I/O, so it runs after publication and reports
        # failure without claiming the selection rolled back.
        persistence_error = state.flush_pending_default()
        if persistence_error is not None:
            message = f"{message}\n{persistence_error}"
        return True, message

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

        state = self.session.provider_state
        if not isinstance(state, NativeReplProviderState):
            return f"pipy: /{action} is unavailable for this REPL provider state."
        provider_name = argument or "openai-codex"
        if action == "login":
            if self.terminal_ui is not None:
                self.terminal_ui.suspend_for_external_io()
            try:
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
        state.selection = normalize_repl_fake_selection(state.current_selection())
        rebound_provider = state.current_provider()
        selection = state.current_selection()
        self.coding_state.rebind_provider(
            rebound_provider,
            provider_name=selection.provider_name,
            model_id=selection.model_id,
            usage_accumulator=AgentUsageAccumulator(
                _pricing_for(selection.provider_name, selection.model_id)
            ),
        )
        self.refresh_footer_text()
        # Post-commit: the logout-reset selection is already live.
        persistence_error = state.flush_pending_default()
        if persistence_error is not None:
            message = f"{message}\n{persistence_error}"
        return message

    def apply_compaction(self, trigger: str) -> str:
        """Compact the in-memory provider history at a user-turn boundary.

        Returns a safe diagnostic string. The cut keeps the most recent
        turns and replaces the dropped prefix with a metadata-only summary
        appended to the system prompt; provider/model, usage counters,
        prompt history, and the TUI frame are all left intact. No tool
        result is orphaned because the cut is at a user-message boundary.
        """

        decision = dispatch_session_before_hooks(
            self.ctl.extension_generation.runtime.session_before_compact_hooks,
            operation="compact",
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            trigger=trigger,
            notify_sink=self.extension_notify,
            ui_driver=self.extension_ui_driver,
            model_runtime=self.model_runtime_control(),
            flags=self.ctl.extension_generation.flag_values,
            project_trusted=self.settings.project_trusted,
        )
        if not decision.allow:
            reason = decision.reason or "blocked by extension"
            return f"pipy: compact blocked by extension: {reason}"
        result = compact_agent_history(
            self.coding_state.messages,
            keep_recent_groups=_AGENT_HISTORY_KEEP_RECENT_GROUPS,
        )
        if not result.changed:
            return "pipy: nothing to compact yet."
        summary_block = _agent_history._agent_history_summary(result)
        self.product_session.apply_compaction(
            CodingProductSessionCompaction(
                retained_messages=result.messages,
                summary_suffix=ProductContent(f"\n\n{summary_block}"),
                durable_summary=ProductContent(summary_block),
                dropped_group_count=result.dropped_group_count,
                measure_before=result.bytes_before,
            )
        )
        return (
            f"pipy: compacted conversation context ({trigger}; dropped "
            f"{result.dropped_group_count} earlier exchange(s), kept "
            f"{result.retained_group_count})."
        )

    def append_durable_compaction(self, summary_block: str, bytes_before: int) -> None:
        branch = self.ctl.session_tree.get_branch()
        last_compaction = -1
        for i, entry in enumerate(branch):
            if isinstance(entry, _CompactionEntry):
                last_compaction = i
        segment = branch[last_compaction + 1 :]
        user_entries = [
            entry
            for entry in segment
            if isinstance(entry, _MessageEntry)
            and isinstance(entry.message, AgentUserMessage)
        ]
        if len(user_entries) <= _AGENT_HISTORY_KEEP_RECENT_GROUPS:
            return
        first_kept = user_entries[len(user_entries) - _AGENT_HISTORY_KEEP_RECENT_GROUPS]
        self.ctl.session_tree.append_compaction(
            summary=summary_block.strip(),
            first_kept_entry_id=first_kept.id,
            tokens_before=bytes_before,
        )


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
    handler holds no state of its own (``__slots__ = ()``); it receives the run's
    mutable control-state holder ``ctl`` plus the stable run-scope collaborators
    explicitly as keyword-only arguments and mutates ``ctl`` in place, so the
    composition-root closures read the reassigned loop control flags back
    byte-identically. The bodies formerly lived as the ``_repl_step`` closure
    (with its nested ``_prepare_loop_request``) and the ``_finalize_repl_loop``/
    ``_fire_session_start``/``_fire_session_shutdown``/
    ``_consume_agent_settled_pending``/``_clear_extension_chrome_after_run``
    bookends nested in ``NativeToolReplSession.run()``.
    """

    __slots__ = ()

    def step_once(
        self,
        *,
        session: "NativeToolReplSession",
        ctl: _RunControlState,
        loop_controller: CodingSessionController,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        coding_state: CodingSessionState,
        repl_input: "ToolLoopTerminalUi | NativeReplInput",
        renderer: "_ToolLoopRenderer | _TuiToolLoopRenderer",
        emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter,
        settings: SettingsManager,
        cwd: Path,
        started_at: datetime,
        base_system_prompt: str,
        image_reference_roots: tuple[Path, ...],
        prompt_history_store: PromptHistoryStore,
        tool_capabilities: NativeToolCapabilities,
        agent_tool_policy: NativeAgentToolPolicy,
        coding_input_queue: CodingInputQueue,
        command_effects: CodingCommandEffects,
        input_queued_input_port: NativeAgentQueuedInputPort | None,
        provider_request_policy: NativeAgentProviderRequestPolicy,
        provider_turn_executor: ProviderTurnExecutor,
        usage_publisher: NativeAgentUsagePublisher,
        extension_ui_driver: _LiveExtensionUiDriver | None,
        diag: Callable[[str], None],
        coding_footer_text: Callable[[], str],
        refresh_legacy_footer_with_usage: Callable[[], None],
        apply_compaction: Callable[[str], str],
        append_agent_message: Callable[[AgentMessage], None],
        drain_extension_outboxes: Callable[[], None],
        _active_provider_header_callback: Callable[
            [], Callable[[MutableMapping[str, str | None]], None] | None
        ],
        _extension_custom_driver: Callable[..., object],
        _extension_notify: Callable[[str, str], None],
        _sync_tool_policy_counters: Callable[[AgentToolPolicyState], None],
        coding_session_control: Callable[[], ExtensionCodingSessionControl],
        model_runtime: ExtensionModelRuntimeControl,
    ) -> LoopStepSignal:
        # The per-action built-in control-state reassignments (session tree,
        # tree filter mode, prefill, and the whole `/reload` extension-runtime
        # bundle) now live in `_BuiltinCommandInterpreter.interpret`, invoked through the
        # command-dispatch port; this step only reassigns its own loop control
        # flags plus the input/agent-turn bookkeeping, all through ``ctl``.
        if terminal_ui is None:
            print_input_separator(error_stream)
        footer_text = coding_footer_text()
        if ctl.pending_prefill is not None:
            # A ``/tree`` user-message selection puts the chosen text back
            # into the editor. The live TUI rehydrates the editor directly;
            # captured-stream callers see a hint and type the (edited) text
            # as the next line, which branches from the selected parent.
            if terminal_ui is not None and hasattr(terminal_ui, "set_input_text"):
                terminal_ui.set_input_text(ctl.pending_prefill)
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
            session._toggle_view_fold(
                stripped,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                settings=settings,
            )
            return LoopStepSignal.continue_loop()
        if command_text == HOTKEY_THINKING_CYCLE:
            session._cycle_thinking_level(
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                session_tree=ctl.session_tree,
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
            shortcut_dispatch = dispatch_extension_shortcut(
                shortcut_key,
                ctl.extension_generation.runtime.shortcuts,
                cwd=str(cwd),
                has_ui=terminal_ui is not None,
                coding_session=coding_session_control(),
                notify_sink=_extension_notify,
                ui_custom_driver=_extension_custom_driver,
                ui_driver=extension_ui_driver,
                model_runtime=model_runtime,
                flags=ctl.extension_generation.flag_values,
                project_trusted=settings.project_trusted,
            )
            if (
                shortcut_dispatch is not None
                and not shortcut_dispatch.ran
                and shortcut_dispatch.error
            ):
                session._emit_diagnostic(
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
            shell_context_text = session._run_local_shell_shortcut(
                stripped,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                cwd=cwd,
                user_bash_hooks=ctl.extension_generation.runtime.user_bash_hooks,
                model_runtime=model_runtime,
                ui_driver=extension_ui_driver,
                flags=ctl.extension_generation.flag_values,
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
        # `command_effects.interpret_builtin` port — the per-action effect
        # chain in `_BuiltinCommandInterpreter.interpret` — and resolves to
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
            return dispatch_input_hooks(
                ctl.extension_generation.runtime.input_hooks,
                prompt,
                cwd=str(cwd),
                has_ui=terminal_ui is not None,
                notify_sink=_extension_notify,
                ui_driver=extension_ui_driver,
                model_runtime=model_runtime,
                project_trusted=settings.project_trusted,
            )

        def _resolve_accepted_file_references(
            prompt: str,
        ) -> FileReferenceResolution:
            return resolve_file_references(
                prompt,
                workspace_root=cwd,
                reference_roots=session.reference_roots,
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
            before_agent_result = dispatch_before_agent_start_hooks(
                ctl.extension_generation.runtime.before_agent_start_hooks,
                cwd=str(cwd),
                has_ui=terminal_ui is not None,
                system_prompt=base_prompt,
                notify_sink=_extension_notify,
                ui_driver=extension_ui_driver,
                model_runtime=model_runtime,
                flags=ctl.extension_generation.flag_values,
                project_trusted=settings.project_trusted,
            )
            return before_agent_result.append_system_prompt

        def _emit_accepted_input_diagnostic(message: str) -> None:
            session._emit_diagnostic(terminal_ui, error_stream, message)

        accepted_turn = CodingAcceptedInputPreparer(
            transform_input=_transform_accepted_input,
            resolve_file_references=_resolve_accepted_file_references,
            resolve_image_attachments=_resolve_accepted_image_attachments,
            system_prompt_suffix=_accepted_system_prompt_suffix,
            next_turn_context=coding_input_queue.take_next_turn_context,
            emit_diagnostic=_emit_accepted_input_diagnostic,
            state_recorder=CodingSessionAcceptedInputRecorder(
                coding_state, tool_budget=session.tool_budget
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
                max_messages=_AGENT_HISTORY_MAX_MESSAGES,
                max_bytes=_AGENT_HISTORY_MAX_BYTES,
                keep_recent_groups=_AGENT_HISTORY_KEEP_RECENT_GROUPS,
            ):
                notice = apply_compaction("auto")
                session._emit_diagnostic(terminal_ui, error_stream, notice)
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
            live_tool_renderers = _extension_tool_renderer_map(
                ctl.extension_generation.runtime.tools
            )
            renderer.refresh_tool_renderers(
                {
                    name: live_tool_renderers[name]
                    for name in snapshot.advertised_tool_names
                    if name in live_tool_renderers
                }
            )
            return AgentLoopRequestPreparation(coding_state.messages, snapshot)

        def _complete_loop_provider_turn(
            snapshot: AgentProviderRequestSnapshot,
            event_sink: AgentEventSink,
            turn_index: int,
        ) -> ProviderTurnOutcome:
            provider_request = materialize_provider_request(snapshot)
            provider_waiter = None
            provider_for_turn: ProviderPort = coding_state.provider
            if terminal_ui is not None:
                provider_waiter = partial(_wait_for_provider_interrupt, terminal_ui)
            elif session.abort_event is not None:
                provider_start_event = None
                if isinstance(session.abort_event, _AbortCallbackSignal):
                    provider_start_event = threading.Event()
                    provider_for_turn = _StartGatedProvider(
                        coding_state.provider, provider_start_event
                    )
                provider_waiter = partial(
                    _wait_for_external_abort,
                    session.abort_event,
                    provider_start_event,
                )
            return provider_turn_executor.complete(
                provider_for_turn,
                provider_request,
                event_sink,
                turn_index=turn_index,
                waiter=provider_waiter,
            )

        def _agent_loop_entered() -> None:
            ctl.extension_in_agent_turn = True

        def _agent_input_accepted() -> None:
            coding_state.record_input_accepted()
            # Only genuine literal prompts enter the local recall store.
            if resource_provider_text is None:
                prompt_history_store.record(user_input)

        def _provider_result_observed(result: ProviderResult) -> None:
            del result
            if terminal_ui is not None and terminal_ui.has_pending_messages():
                terminal_ui.promote_pending_to_drain()

        def _agent_provider_succeeded(
            status: AgentProviderStatusDecision,
            tool_state: AgentToolPolicyState,
        ) -> None:
            del status
            del tool_state
            coding_state.clear_provider_failure()

        def _agent_cancellation_observed(
            reason: AgentCancellationReason,
        ) -> None:
            if terminal_ui is None:
                return
            if reason is AgentCancellationReason.OPERATOR_ABORT:
                terminal_ui.restore_pending_to_editor()
            elif reason in (
                AgentCancellationReason.STEERING,
                AgentCancellationReason.LOCAL_COMMAND,
            ):
                terminal_ui.promote_pending_to_drain()

        def _agent_provider_failed(
            status: AgentProviderStatusDecision,
            tool_state: AgentToolPolicyState,
        ) -> None:
            failure = status.failure
            assert failure is not None
            del tool_state
            coding_state.record_provider_failure(failure)
            suffix = (
                f" (response_status={status.response_status})"
                if status.response_status is not None
                else ""
            )
            session._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: provider failure during turn: "
                f"{failure.error_type}: {failure.message.value}{suffix}",
            )
            refresh_legacy_footer_with_usage()

        def _agent_no_tool_assistant(
            tool_state: AgentToolPolicyState,
        ) -> None:
            del tool_state
            refresh_legacy_footer_with_usage()

        def _agent_malformed_fatal(
            failure: AgentFailure,
            tool_state: AgentToolPolicyState,
        ) -> None:
            del tool_state
            session._emit_diagnostic(
                terminal_ui,
                error_stream,
                f"pipy: tool-loop ended after {failure.message.value}",
            )

        status_policy = AgentLoopStatusPolicyAdapter(
            run_entered=_agent_loop_entered,
            input_accepted=_agent_input_accepted,
            provider_result_observed=_provider_result_observed,
            provider_cancellation_observed=(_agent_cancellation_observed),
            tool_policy_state_changed=_sync_tool_policy_counters,
            provider_succeeded=_agent_provider_succeeded,
            provider_failed=_agent_provider_failed,
            no_tool_assistant=_agent_no_tool_assistant,
            malformed_fatal=_agent_malformed_fatal,
        )
        tool_waiter = (
            None
            if terminal_ui is None
            else partial(_wait_for_tool_interrupt, terminal_ui)
        )
        run_coordinator = CodingAgentRunCoordinator(
            request_source=AgentLoopRequestSourceAdapter(_prepare_loop_request),
            provider_turn=AgentLoopProviderTurnAdapter(_complete_loop_provider_turn),
            status_policy=status_policy,
            tool_capabilities=tool_capabilities,
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
            pricing=_pricing_for(
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
            except Exception:
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
        except Exception:
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

    def consume_settle_pending(self, *, ctl: _RunControlState) -> bool:
        if ctl.agent_settled_pending:
            ctl.agent_settled_pending = False
            return True
        return False

    def clear_extension_chrome(self, *, terminal_ui: ToolLoopTerminalUi | None) -> None:
        if terminal_ui is not None:
            terminal_ui.clear_extension_chrome()


# A bounded one-shot completion handed to extension command handlers as
# ``ctx.complete(system_prompt, user_text)`` caps its inputs so a buggy handler
# cannot create unbounded provider input.
_EXTENSION_COMPLETE_MAX_CHARS = 100 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class _SessionCollaborators:
    """Composition-root handler owning the residual run-loop collaborators.

    Symmetric with :class:`_ProviderMutationEffects`/:class:`_CustomEntryRenderer`/
    :class:`_ReplLoopStep`/:class:`_BuiltinCommandInterpreter`, these bodies
    formerly lived as the ``diag``/``extension_session_allows``/
    ``rebuild_messages_from_tree``/``summarize_branch``/``current_session_dir``/
    ``resolve_session_file``/session-name-setter/``_extension_complete``/
    ``_extension_custom_driver``/provider-request/tool-policy-hook/
    ``_dispatch_resource_effect``/``_dispatch_extension_effect`` closures nested in
    ``NativeToolReplSession.run()``. They reach one another densely (extension
    dispatch calls the completion, custom driver, and session-name setters;
    ``extension_session_allows``/``summarize_branch`` call ``diag``/
    ``active_provider_header_callback``), so the handler is a frozen, slotted,
    keyword-only dataclass holding the run's mutable control-state holder ``ctl``
    (its ``session_tree`` and canonical extension generation are read fresh on
    every call so a ``/reload``/``/new``/``/resume``/``/fork``/``/clone`` rebind is
    reflected exactly as it was inline) plus the stable run-scope collaborators —
    the owning session, the coding state, the product session, the coding input
    queue, the terminal UI, settings, cwd, the error stream, the provider-mutation
    and custom-entry handlers, the extension UI driver, and the extension notify
    sink; its methods call each other through ``self``. The ``run()`` composition
    root passes each bound method exactly where the deleted closures were consumed.
    """

    session: NativeToolReplSession
    ctl: _RunControlState
    coding_state: CodingSessionState
    product_session: CodingProductSessionCoordinator
    coding_input_queue: CodingInputQueue
    terminal_ui: ToolLoopTerminalUi | None
    settings: SettingsManager
    cwd: Path
    error_stream: TextIO
    provider_mutation: _ProviderMutationEffects
    custom_renderer: _CustomEntryRenderer
    extension_ui_driver: _LiveExtensionUiDriver | None
    extension_notify: Callable[[str, str], None]

    def diag(self, message: str) -> None:
        self.session._emit_diagnostic(self.terminal_ui, self.error_stream, message)

    def extension_set_session_name(self, name: str | None) -> object:
        return self.ctl.session_tree.append_session_info(name)

    def extension_get_session_name(self) -> str | None:
        return self.ctl.session_tree.name

    def extension_set_label(self, entry_id: str, label: str | None) -> object:
        return self.ctl.session_tree.append_label_change(entry_id, label)

    def coding_session_control(self) -> ExtensionCodingSessionControl:
        """Bundle the coding-session host collaborators + live snapshot.

        Built fresh at each dispatch so the ``messages`` conversation snapshot
        and the live ``session_tree`` (rebindable by ``/new`` / ``/resume`` /
        ``/fork`` / ``/clone``) reflect the current session, exactly as the
        prior loose-callable fan-out read them per call.
        """

        return ExtensionCodingSessionControl(
            complete_fn=self.extension_complete,
            append_entry_fn=self.custom_renderer.extension_append_entry,
            set_session_name_fn=self.extension_set_session_name,
            get_session_name_fn=self.extension_get_session_name,
            set_label_fn=self.extension_set_label,
            send_message_fn=self.custom_renderer.extension_send_message,
            session_tree=self.ctl.session_tree,
            messages=self.coding_state.messages,
        )

    def current_session_dir(self) -> Path:
        if self.ctl.session_tree.path is not None:
            return self.ctl.session_tree.path.parent
        return default_native_session_dir(self.cwd)

    def resolve_session_file(self, ref: str) -> Path | None:
        return resolve_session_target(self.current_session_dir(), ref)

    def rebuild_messages_from_tree(self) -> None:
        """Rebuild the live provider-visible list from the active branch.

        Used after ``/tree`` navigation, ``/new``, ``/resume``, ``/fork``,
        ``/clone``, and ``/import``: the native tree is the source of truth,
        so the provider list and the system-prompt compaction suffix are
        reset to match the (possibly compacted) active branch.
        """

        self.product_session.rebuild_active_history()
        # Extension delivery is bound to the active native session/branch
        # and must not leak into its replacement. Positional seeds, a local
        # command, a retained loop handoff, and externally owned RPC
        # reservations preserve their existing independent lifetimes.
        self.coding_input_queue.clear_extension_inputs()

    def extension_session_allows(
        self,
        hooks: Sequence[HookHandler],
        *,
        operation: str,
        target: str | None = None,
        trigger: str | None = None,
    ) -> bool:
        decision = dispatch_session_before_hooks(
            hooks,
            operation=operation,
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            target=target,
            trigger=trigger,
            notify_sink=self.extension_notify,
            ui_driver=self.extension_ui_driver,
            model_runtime=self.provider_mutation.model_runtime_control(),
            flags=self.ctl.extension_generation.flag_values,
            project_trusted=self.settings.project_trusted,
        )
        if decision.allow:
            return True
        reason = decision.reason or "blocked by extension"
        self.diag(f"pipy: {operation} blocked by extension: {reason}")
        return False

    def extension_complete(self, system_prompt: str, user_text: str) -> str:
        request = ProviderRequest(
            system_prompt=str(system_prompt)[:_EXTENSION_COMPLETE_MAX_CHARS],
            user_prompt=str(user_text)[:_EXTENSION_COMPLETE_MAX_CHARS],
            provider_name=self.coding_state.provider_name,
            model_id=self.coding_state.model_id,
            cwd=self.cwd,
            available_tools=(),
            provider_header_callback=self.active_provider_header_callback(),
        )
        result = self.coding_state.provider.complete(request)
        if result.status != HarnessStatus.SUCCEEDED:
            raise ExtensionCapabilityError(
                f"completion failed ({result.error_type or result.status})"
            )
        return result.final_text or ""

    def extension_custom_driver(self, factory: Any, options: Any = None) -> object:
        # Only an interactive terminal can take over the screen; a
        # captured-stream run degrades to a deterministic no-op (also
        # enforced by ExtensionUi.custom when has_ui is False).
        if self.terminal_ui is None:
            return None
        return self.terminal_ui.run_custom_component(factory, options)

    def summarize_branch(
        self, branch_messages: list[AgentMessage], focus: str | None
    ) -> str | None:
        """Summarize an abandoned branch through the active provider.

        Runs one bounded provider turn (no tools) and returns the summary
        text, or ``None`` when the provider fails so the caller can leave
        the tree and leaf unchanged.
        """

        if not branch_messages:
            return None
        instruction = (
            "Summarize the following abandoned conversation branch "
            "concisely so it can be referenced later."
        )
        if focus:
            instruction += f" Focus on: {focus}."
        request = ProviderRequest(
            system_prompt=instruction,
            user_prompt="Provide the branch summary now.",
            provider_name=self.coding_state.provider_name,
            model_id=self.coding_state.model_id,
            cwd=self.cwd,
            messages=tuple(branch_messages),
            available_tools=(),
            provider_header_callback=self.active_provider_header_callback(),
        )
        try:
            result = self.coding_state.provider.complete(request)
        except Exception:  # noqa: BLE001 - never crash the REPL
            return None
        if result.status != HarnessStatus.SUCCEEDED:
            return None
        return (result.final_text or "").strip() or None

    def dispatch_extension_provider_headers(
        self, headers: MutableMapping[str, str | None]
    ) -> None:
        dispatch_before_provider_headers_hooks(
            self.ctl.extension_generation.runtime.before_provider_headers_hooks,
            headers,
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            notify_sink=self.extension_notify,
            ui_driver=self.extension_ui_driver,
            flags=self.ctl.extension_generation.flag_values,
            session_tree=self.ctl.session_tree,
            project_trusted=self.settings.project_trusted,
        )

    def active_provider_header_callback(
        self,
    ) -> Callable[[MutableMapping[str, str | None]], None] | None:
        if not self.ctl.extension_generation.runtime.before_provider_headers_hooks:
            return None
        return self.dispatch_extension_provider_headers

    def prepare_agent_provider_request(
        self, policy_input: AgentProviderRequestPolicyInput
    ) -> AgentProviderRequestSnapshot:
        return prepare_provider_request(
            policy_input,
            self.ctl.extension_generation.runtime.before_provider_request_hooks,
            NativeProviderRequestHookContext(
                cwd=str(self.cwd),
                has_ui=self.terminal_ui is not None,
                notify_sink=self.extension_notify,
                ui_driver=self.extension_ui_driver,
                model_runtime=self.provider_mutation.model_runtime_control(
                    allow_model=False
                ),
                flags=self.ctl.extension_generation.flag_values,
                project_trusted=self.settings.project_trusted,
            ),
        )

    def apply_extension_tool_policy(
        self, call: AgentToolCall
    ) -> AgentToolPolicyDecision:
        tool_block = dispatch_tool_call_hooks(
            self.ctl.extension_generation.runtime.tool_call_hooks,
            tool_name=call.tool_name,
            tool_input=_parse_tool_input(call.arguments_json.value),
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            notify_sink=self.extension_notify,
            ui_driver=self.extension_ui_driver,
            model_runtime=self.provider_mutation.model_runtime_control(
                allow_model=False
            ),
            flags=self.ctl.extension_generation.flag_values,
            project_trusted=self.settings.project_trusted,
        )
        if tool_block is None:
            return AgentToolPolicyDecision()
        return AgentToolPolicyDecision(ProductContent(tool_block.reason))

    def transform_extension_tool_result(
        self, call: AgentToolCall, result: AgentToolResultMessage
    ) -> ProductContent:
        if not self.ctl.extension_generation.runtime.tool_result_hooks:
            return result.content
        transformed = dispatch_tool_result_hooks(
            self.ctl.extension_generation.runtime.tool_result_hooks,
            tool_name=call.tool_name,
            content=result.content.value,
            is_error=result.is_error,
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            notify_sink=self.extension_notify,
            ui_driver=self.extension_ui_driver,
            model_runtime=self.provider_mutation.model_runtime_control(
                allow_model=False
            ),
            flags=self.ctl.extension_generation.flag_values,
            project_trusted=self.settings.project_trusted,
        )
        return ProductContent(transformed)

    def dispatch_resource_effect(
        self, command_text: str
    ) -> ResourceDispatchResolution | None:
        resource_dispatch = dispatch_resource_command(
            command_text, self.ctl.workspace_resources
        )
        if resource_dispatch is None:
            return None
        if resource_dispatch.kind == DISPATCH_LIST:
            return ResourceDispatchResolution(
                ResourceDispatchKind.LIST, resource_dispatch.message
            )
        if resource_dispatch.is_reject:
            return ResourceDispatchResolution(
                ResourceDispatchKind.REJECT, resource_dispatch.message
            )
        if resource_dispatch.is_run:
            return ResourceDispatchResolution(
                ResourceDispatchKind.RUN,
                resource_dispatch.message,
                resource_dispatch.provider_text,
            )
        return None

    def dispatch_extension_effect(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None:
        extension_dispatch = dispatch_extension_command(
            command_text,
            self.ctl.extension_generation.runtime.commands,
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            coding_session=self.coding_session_control(),
            notify_sink=self.extension_notify,
            ui_custom_driver=self.extension_custom_driver,
            ui_driver=self.extension_ui_driver,
            model_runtime=self.provider_mutation.model_runtime_control(),
            flags=self.ctl.extension_generation.flag_values,
            project_trusted=self.settings.project_trusted,
        )
        if extension_dispatch is None:
            return None
        return ExtensionDispatchResolution(
            name=extension_dispatch.name,
            ran=extension_dispatch.ran,
            error=extension_dispatch.error,
        )


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
    auto_trust_on_reload_cwd: Path | None = None
    # Finalized startup activation shared with catalog construction. Only the
    # initial run consumes it; explicit /reload performs a fresh activation.
    initial_extension_batch: ExtensionActivationBatch | None = None
    _coding_state: CodingSessionState = field(init=False, repr=False)

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 50
    MAX_TOOL_BUDGET: ClassVar[int] = MAX_AGENT_TOOL_BUDGET

    def __post_init__(self, provider: ProviderPort) -> None:
        if self.auto_trust_on_reload_cwd is not None:
            self.auto_trust_on_reload_cwd = (
                self.auto_trust_on_reload_cwd.expanduser().resolve()
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

    @staticmethod
    def _export_session(
        argument: ProductContent | None,
        *,
        session_tree: NativeSessionTree,
        cwd: Path,
        system_prompt: str,
        diagnostic: Callable[[str], None],
    ) -> None:
        """Export the active product session through the typed command effect."""

        if type(argument) is not ProductContent:
            raise TypeError("SESSION_EXPORT requires an exact ProductContent argument")
        path_arg = parse_command_path_argument(argument.value)
        try:
            if path_arg and Path(path_arg).suffix.lower() == ".jsonl":
                output_path = Path(path_arg).expanduser()
                if not output_path.is_absolute():
                    output_path = cwd / output_path
                exported = export_native_branch_to_jsonl(session_tree, output_path)
                diagnostic(f"pipy: exported native session JSONL to {exported}.")
            else:
                output_path = (
                    Path(path_arg).expanduser()
                    if path_arg
                    else default_html_export_path(session_tree, cwd=cwd)
                )
                if not output_path.is_absolute():
                    output_path = cwd / output_path
                exported = export_native_session_to_html(
                    session_tree,
                    output_path,
                    system_prompt=system_prompt,
                )
                diagnostic(f"pipy: exported native session HTML to {exported}.")
        except NativeExportError as exc:
            diagnostic(f"pipy: {exc}")

    @staticmethod
    def _confirm_import_prompt(
        prompt: str,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
    ) -> bool:
        """Read one direct import confirmation without changing failure policy."""

        print(prompt, end="", file=error_stream, flush=True)
        try:
            return input_stream.readline().strip().lower() in ("y", "yes")
        except (OSError, ValueError):
            return False

    @staticmethod
    def _resolve_import_source_path(argument: str, *, cwd: Path) -> Path | None:
        """Parse and expand the first import path without resolving symlinks."""

        path_arg = parse_command_path_argument(argument)
        if not path_arg:
            return None
        source_path = Path(path_arg).expanduser()
        if source_path.is_absolute():
            return source_path
        return cwd / source_path

    @classmethod
    def _import_session(
        cls,
        argument: ProductContent | None,
        *,
        cwd: Path,
        input_stream: TextIO,
        error_stream: TextIO,
        current_session_dir: Callable[[], Path],
        session_switch_allows: Callable[[str], bool],
        diagnostic: Callable[[str], None],
    ) -> NativeSessionTree | None:
        """Import a product session through the typed command effect."""

        if type(argument) is not ProductContent:
            raise TypeError("SESSION_IMPORT requires an exact ProductContent argument")
        source_path = cls._resolve_import_source_path(argument.value, cwd=cwd)
        if source_path is None:
            diagnostic("pipy: Usage: /import <path.jsonl>")
            return None
        confirm = "--yes" in argument.value.split()
        if not confirm:
            confirm = cls._confirm_import_prompt(
                f"Replace current session with {source_path}? [y/N] ",
                input_stream=input_stream,
                error_stream=error_stream,
            )
        if not confirm:
            diagnostic("pipy: /import cancelled.")
            return None
        if not session_switch_allows(str(source_path)):
            return None
        try:
            return import_native_session_jsonl(
                source_path,
                session_dir=current_session_dir(),
            )
        except NativeExportError as exc:
            if "imported session cwd does not exist:" not in str(exc):
                diagnostic(f"pipy: {exc}")
                return None
            use_current = cls._confirm_import_prompt(
                f"{exc} Use current workspace {cwd}? [y/N] ",
                input_stream=input_stream,
                error_stream=error_stream,
            )
            if not use_current:
                diagnostic("pipy: /import cancelled.")
                return None
            try:
                return import_native_session_jsonl(
                    source_path,
                    session_dir=current_session_dir(),
                    missing_cwd=cwd,
                )
            except NativeExportError as second_exc:
                diagnostic(f"pipy: {second_exc}")
                return None

    def run(
        self,
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
                _pricing_for(initial_provider_name, initial_model_id)
            ),
        )

        def _bind_unavailable_after_reload(message: str) -> None:
            unavailable_provider = UnavailableAfterReloadProvider(
                name=coding_state.provider_name,
                model_id=coding_state.model_id,
                error_message=message,
            )
            coding_state.mark_provider_unavailable(unavailable_provider)

        # The session's single synchronization boundary, created before the
        # first owner of guarded state so every one of them shares this exact
        # object. Two locks would not serialize a reload against a worker's
        # mutation; see
        # `docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`.
        # A caller-supplied manager keeps its identity but adopts this lock:
        # leaving it on a private one would give the run two boundaries, which
        # serialize nothing against each other.
        session_state_lock = threading.RLock()
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
        # Discover + activate Python extensions and project their slash
        # commands. Activation runs extension code; a failing extension is
        # disabled without affecting the session.
        # Built-in tool names are reserved so an extension tool can never
        # shadow a built-in tool.
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
        )
        extension_flag_values, extension_flag_error = parse_extension_flag_tokens(
            extension_runtime.flags,
            tuple(resource_options.extension_flag_tokens),
        )
        if extension_flag_error is not None:
            print(f"pipy: {extension_flag_error}", file=error_stream)
            now = datetime.now(UTC)
            return NativeToolReplResult(
                status=HarnessStatus.FAILED,
                exit_code=2,
                started_at=now,
                ended_at=now,
                provider_name=coding_state.provider_name,
                model_id=coding_state.model_id,
                error_type="ExtensionFlagError",
                error_message=extension_flag_error,
            )
        generation_ref = SessionGenerationRef(
            SessionExtensionGeneration(
                runtime=extension_runtime,
                flag_values=extension_flag_values,
            ),
            lock=session_state_lock,
        )
        extension_generation = generation_ref.current
        if isinstance(self.provider_state, NativeReplProviderState):
            runtime = self.provider_state.model_runtime
            if runtime is not None:
                catalog_state = runtime.catalog
                was_extension_selection = (
                    self.provider_state.current_selection_uses_extension_provider()
                )
                catalog_state.set_extension_provider_contributions(
                    extension_generation.runtime.providers,
                    extension_generation.runtime.unregistered_providers,
                )
                if not self.provider_state.current_selection_supported() or (
                    was_extension_selection
                    and not self.provider_state.current_selection_uses_extension_provider()
                ):
                    fallback = self.provider_state.reset_to_first_available_model(
                        require_tool_calls=True
                    )
                    if fallback is None:
                        raise ValueError(
                            "selected provider is unavailable after extension "
                            "activation, and no available tool-capable fallback "
                            "was found"
                        )
                    fallback_provider = self.provider_state.current_provider()
                    coding_state.rebind_provider(
                        fallback_provider,
                        provider_name=fallback.provider_name,
                        model_id=fallback.model_id,
                        usage_accumulator=AgentUsageAccumulator(
                            _pricing_for(fallback.provider_name, fallback.model_id)
                        ),
                    )
                    print(
                        "pipy: active model disappeared on startup; selected "
                        f"{fallback.reference}.",
                        file=error_stream,
                    )
                    # Post-commit: the fallback is bound, so persist it and
                    # report a failure without claiming the binding reverted.
                    startup_persistence_error = (
                        self.provider_state.flush_pending_default()
                    )
                    if startup_persistence_error is not None:
                        print(startup_persistence_error, file=error_stream)
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
            resources=workspace_resources,
            autocomplete_max_visible=settings.get_autocomplete_max_visible(),
            keybindings_manager=keybindings,
            extension_menu_names=extension_generation.runtime.menu_names,
            extension_descriptions=extension_generation.runtime.descriptions,
            extension_shortcut_keys=frozenset(extension_generation.runtime.shortcuts),
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
        if terminal_ui is not None and keybindings.has_user_binding(
            "app.editor.external"
        ):
            editor_keys = {
                normalized
                for key in keybindings.keys_for("app.editor.external")
                if (normalized := normalize_shortcut_key(key))
            }
            shadowed_keys = sorted(
                editor_keys.intersection(extension_generation.runtime.shortcuts)
            )
            for key in shadowed_keys:
                print(
                    "pipy: extension shortcut "
                    f"{key!r} is shadowed by app.editor.external; rebind the "
                    "editor action or extension shortcut.",
                    file=error_stream,
                )

        # Live UI sink for extension `ctx.ui.notify` from hooks and tools:
        # notifications are emitted as local diagnostics (interactive) and
        # degrade deterministically in non-interactive mode.
        def _extension_notify(_kind: str, message: str) -> None:
            safe_message = "\n".join(
                sanitize_label_text(line) for line in str(message).splitlines()
            )
            self._emit_diagnostic(terminal_ui, error_stream, safe_message)

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
        extension_tool_renderers = _extension_tool_renderer_map(
            extension_generation.runtime.tools
        )
        extension_tool_registry: dict[str, ToolPort] = {}
        for _registered_tool in extension_generation.runtime.tools:
            _port = _ExtensionToolPort(
                _registered_tool,
                has_ui=terminal_ui is not None,
                notify_sink=_extension_notify,
                set_active_tools_fn=lambda names: (
                    provider_mutation.extension_set_active_tools(names)
                ),
                flags=extension_generation.flag_values,
                render_details_sink=render_details.writer,
                project_trusted=settings.project_trusted,
            )
            extension_tool_registry[_port.definition.name] = _port
        tool_capabilities = NativeToolCapabilities(
            self.tool_registry,
            extension_tool_registry,
            workspace_root=cwd,
            reference_roots=self.reference_roots,
            stderr_sink=_stderr_sink,
            filter_options=self.tool_filter_options,
            cancel_join_timeout_seconds=self._CANCEL_JOIN_TIMEOUT_SECONDS,
            # Share the session mutex rather than letting the capability owner
            # create a private one: an extension tool handler reaching
            # `set_active_tools` from a worker thread and a reload publishing a
            # new generation must serialize against each other, which two
            # separate locks would not do.
            state_lock=session_state_lock,
        )
        provider_turn_executor = ProviderTurnExecutor(
            cancel_join_timeout_seconds=self._CANCEL_JOIN_TIMEOUT_SECONDS,
        )
        unknown_filter_names = tool_capabilities.unknown_filter_names
        if unknown_filter_names:
            known = ", ".join(sorted(tool_capabilities.registered_names)) or "<none>"
            unknown = ", ".join(unknown_filter_names)
            raise ValueError(f"unknown tool name(s): {unknown}. Known tools: {known}")
        # Image attachments may reference an owner-only clipboard temp dir
        # (Ctrl+V paste); that dir is added to the image reference roots so a
        # pasted ``@image:<temp>`` resolves while the workspace path policy is
        # otherwise unchanged. File-reference (@path) reads do not use it.
        image_reference_roots = self.reference_roots
        if terminal_ui is not None:
            # Seed the thinking-block fold (Ctrl+T) from the persisted setting.
            terminal_ui.thinking_hidden = settings.get_hide_thinking_block()
            clipboard_dir = Path(tempfile.mkdtemp(prefix="pipy-clipboard-"))
            try:
                clipboard_dir.chmod(0o700)
            except OSError:
                pass
            terminal_ui.clipboard_temp_dir = clipboard_dir
            terminal_ui.clipboard_image_read = self.clipboard_image_read
            image_reference_roots = (*self.reference_roots, clipboard_dir)
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
            terminal_ui.input_history = list(prompt_history_store.entries())
        renderer: _ToolLoopRenderer | _TuiToolLoopRenderer
        if terminal_ui is not None:
            renderer = _TuiToolLoopRenderer(
                ui=terminal_ui,
                tool_renderers=extension_tool_renderers,
                render_details_sink=render_details.tui,
            )
        else:
            renderer = _ToolLoopRenderer(
                output_stream=output_stream,
                error_stream=error_stream,
                tool_renderers=extension_tool_renderers,
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
        ctl = _RunControlState(
            session_tree=session_tree,
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

        def _sync_tool_policy_counters(state: AgentToolPolicyState) -> None:
            coding_state.sync_tool_policy(state)

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
            lifecycle_hooks=extension_generation.runtime.lifecycle_hooks,
            cwd=str(cwd),
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            ui_driver=extension_ui_driver,
            flags=extension_generation.flag_values,
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
                drained_content = terminal_ui.take_next_drain()
                if drained_content is not None:
                    raw_kind = terminal_ui.take_last_drain_kind()
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
            command = terminal_ui.take_pending_command()
            return None if command is None else ProductContent(command)

        coding_input_queue = CodingInputQueue(
            external_inputs=tuple(
                port
                for port in (input_queued_input_port, terminal_queued_input_port)
                if port is not None
            ),
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
        # live in the module-level `_CustomEntryRenderer` handler (symmetric with
        # `_ReplLoopStep`/`_BuiltinCommandInterpreter`). A narrow adapter reads
        # renderer maps and outboxes through `ctl.extension_generation`, while the
        # session tree and `extension_in_agent_turn` remain live run control. Its
        # bound methods are passed wherever the deleted closures were consumed.
        custom_renderer = _CustomEntryRenderer(
            session=self,
            ctl=_ExtensionCustomEntryRunState(ctl=ctl),
            terminal_ui=terminal_ui,
            coding_input_queue=coding_input_queue,
            error_stream=error_stream,
        )

        for custom_message in ctl.extension_generation.runtime.custom_messages:
            custom_renderer.extension_send_message(
                custom_message.custom_type,
                custom_message.content,
                custom_message.display,
                custom_message.options,
                custom_message.details,
            )

        repl_input = (
            terminal_ui
            if terminal_ui is not None
            else self._build_repl_input(
                input_stream=input_stream,
                error_stream=error_stream,
                workspace=cwd,
                resources=workspace_resources,
                extension_menu_names=extension_generation.runtime.menu_names,
                extension_descriptions=extension_generation.runtime.descriptions,
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

        # The provider/model/auth/compaction mutation effects live in the
        # `_ProviderMutationEffects` handler, built after `product_session`/`footer`
        # exist; it reaches the run's mutable control state through the shared `ctl`
        # holder so a `/reload` rebind is reflected exactly as it was inline.
        provider_mutation = _ProviderMutationEffects(
            session=self,
            ctl=ctl,
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
            extension_ui_driver=extension_ui_driver,
            mutation_io_lock=threading.RLock(),
        )

        # The residual run-loop collaborators (diagnostics, session-name setters,
        # session-dir/resolution, tree rebuild, branch summarization, the extension
        # completion/custom-UI/session-gate/provider-request/tool-policy hooks, and
        # the resource/extension command-dispatch effects) live in the
        # `_SessionCollaborators` handler, built once `provider_mutation`/
        # `custom_renderer` exist; it reads the run's mutable control state through
        # the shared `ctl` holder so a `/reload` rebind is reflected on next dispatch.
        collaborators = _SessionCollaborators(
            session=self,
            ctl=ctl,
            coding_state=coding_state,
            product_session=product_session,
            coding_input_queue=coding_input_queue,
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

        # Continuing built-in interpretation runs through the command-dispatch
        # effect port (symmetric with resource/extension dispatch): the
        # controller classifies and invokes this per-action effect chain, which
        # reassigns the run's control state (the live session tree, tree filter
        # mode, pending prefill, and the whole `/reload` extension-runtime
        # bundle) exactly as the superseded inline INTERPRET_BUILTIN branch did.
        builtin_interpreter = _BuiltinCommandInterpreter()

        command_effects: CodingCommandEffects = _CallableCodingCommandEffects(
            emit=collaborators.diag,
            footer=footer.refresh_legacy_footer,
            interpret=lambda outcome: builtin_interpreter.interpret(
                outcome,
                session=self,
                ctl=ctl,
                coding_state=coding_state,
                terminal_ui=terminal_ui,
                renderer=renderer,
                error_stream=error_stream,
                emitter=emitter,
                keybindings=keybindings,
                settings=settings,
                cwd=cwd,
                system_prompt=system_prompt,
                input_stream=input_stream,
                prompt_history_store=prompt_history_store,
                resource_options=resource_options,
                tool_capabilities=tool_capabilities,
                repl_input=repl_input,
                diag=collaborators.diag,
                apply_compaction=provider_mutation.apply_compaction,
                apply_model_selection=provider_mutation.apply_model_selection,
                apply_auth_change=provider_mutation.apply_auth_change,
                rebuild_messages_from_tree=collaborators.rebuild_messages_from_tree,
                redraw_custom_entries_for_active_branch=custom_renderer.redraw_custom_entries_for_active_branch,
                refresh_legacy_footer=footer.refresh_legacy_footer,
                refresh_legacy_footer_with_usage=footer.refresh_legacy_footer_with_usage,
                current_session_dir=collaborators.current_session_dir,
                resolve_session_file=collaborators.resolve_session_file,
                summarize_branch=collaborators.summarize_branch,
                extension_session_allows=collaborators.extension_session_allows,
                extension_send_message=custom_renderer.extension_send_message,
                extension_render_details=render_details.writer,
                extension_set_active_tools=provider_mutation.extension_set_active_tools,
                _extension_notify=_extension_notify,
                _bind_unavailable_after_reload=_bind_unavailable_after_reload,
            ),
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
        # `session_shutdown` fire, and the extension-chrome clear on EVERY exit
        # path (normal return, fatal return, or a propagated exception). One
        # iteration's body, the run transition, and every UI/provider/persistence
        # effect live in `_ReplLoopStep.step_once` (a module-level composition-root
        # handler, symmetric with `_BuiltinCommandInterpreter`); it performs one
        # iteration and returns only the routing signal, and shares the run's
        # mutable control state with the composition-root closures through the
        # `ctl` `_RunControlState` holder so a `/reload`, `/new`, `/resume`,
        # `/fork`, or `/clone` rebind is reflected in those closures exactly as it
        # was inline. `run()` reaches the handler by passing its bound methods
        # (each `functools.partial`-bound to the run-scope collaborators) through
        # the same `run_loop` ports.
        repl_loop_step = _ReplLoopStep()
        return loop_controller.run_loop(
            step_once=partial(
                repl_loop_step.step_once,
                session=self,
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
                prompt_history_store=prompt_history_store,
                tool_capabilities=tool_capabilities,
                agent_tool_policy=agent_tool_policy,
                coding_input_queue=coding_input_queue,
                command_effects=command_effects,
                input_queued_input_port=input_queued_input_port,
                provider_request_policy=provider_request_policy,
                provider_turn_executor=provider_turn_executor,
                usage_publisher=usage_publisher,
                extension_ui_driver=extension_ui_driver,
                diag=collaborators.diag,
                coding_footer_text=footer.coding_footer_text,
                refresh_legacy_footer_with_usage=footer.refresh_legacy_footer_with_usage,
                apply_compaction=provider_mutation.apply_compaction,
                append_agent_message=append_agent_message,
                drain_extension_outboxes=custom_renderer.drain_extension_outboxes,
                _active_provider_header_callback=collaborators.active_provider_header_callback,
                _extension_custom_driver=collaborators.extension_custom_driver,
                _extension_notify=_extension_notify,
                _sync_tool_policy_counters=_sync_tool_policy_counters,
                coding_session_control=collaborators.coding_session_control,
                model_runtime=provider_mutation.model_runtime_control(),
            ),
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
        resources: WorkspaceResources,
        extension_menu_names: tuple[str, ...] = (),
        extension_descriptions: dict[str, str] | None = None,
    ) -> NativeReplInput:
        return native_repl_input_for(
            input_stream=input_stream,
            error_stream=error_stream,
            input_runtime=self.input_runtime,
            workspace=workspace,
            command_names=_tool_loop_command_names(resources, extension_menu_names),
            command_descriptions=_tool_loop_command_descriptions(
                resources, extension_descriptions
            ),
        )

    def _build_terminal_ui(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        resources: WorkspaceResources,
        autocomplete_max_visible: int = 5,
        extension_menu_names: tuple[str, ...] = (),
        extension_descriptions: dict[str, str] | None = None,
        extension_shortcut_keys: frozenset[str] = frozenset(),
        keybindings_manager: KeybindingsManager | None = None,
        include_workspace_defaults: bool = False,
    ) -> ToolLoopTerminalUi | None:
        if self.input_runtime not in {REPL_INPUT_RUNTIME_AUTO, "tool-loop-tui"}:
            return None
        if not ToolLoopTerminalUi.is_supported(input_stream, error_stream):
            return None
        return ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=error_stream,
            cwd=workspace,
            command_names=_tool_loop_command_names(resources, extension_menu_names),
            command_descriptions=_tool_loop_command_descriptions(
                resources, extension_descriptions
            ),
            autocomplete_max_visible=autocomplete_max_visible,
            keybindings_manager=keybindings_manager,
            extension_shortcut_keys=extension_shortcut_keys,
            include_workspace_defaults=include_workspace_defaults,
        )

    def _share_native_session_command(
        self,
        *,
        session_tree: NativeSessionTree,
        token: str,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
    ) -> ShareResult | None:
        """Run ``/share`` with product cancellation when the TUI is active."""

        if terminal_ui is None:
            return share_native_session(
                session_tree,
                token=token,
                cancelled=(
                    self.abort_event.is_set if self.abort_event is not None else None
                ),
            )

        cancel_token = CancelToken()
        done_event = threading.Event()
        result_holder: list[ShareResult] = []
        error_holder: list[BaseException] = []

        def _run_share() -> None:
            try:
                result_holder.append(
                    share_native_session(
                        session_tree,
                        token=token,
                        cancelled=cancel_token.event.is_set,
                        cancel_token=cancel_token,
                    )
                )
            except BaseException as exc:  # pragma: no cover - re-raised below
                error_holder.append(exc)
            finally:
                done_event.set()

        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            "pipy: sharing native session... press Escape to cancel.",
        )
        worker = threading.Thread(
            target=_run_share, name="pipy-share-gist", daemon=True
        )
        worker.start()
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, cancel_token.event, accept_queue=False
            )
        except KeyboardInterrupt:
            cancel_token.cancel()
            worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
            self._emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
            return None
        if outcome == TURN_ABORTED:
            cancel_token.cancel()
            worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
            self._emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
            return None
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
        if error_holder:
            error = error_holder[0]
            if isinstance(error, ShareCancelled):
                self._emit_diagnostic(
                    terminal_ui, error_stream, "pipy: Share cancelled."
                )
                return None
            if isinstance(error, NativeExportError):
                raise error
            raise error
        return result_holder[0] if result_holder else None

    # Bound on how long the main thread waits for a cancelled provider worker to
    # unwind after its connection is closed. The worker is a daemon thread, so
    # if the join times out the process can still exit and—because the turn
    # returns ``None``—the worker can no longer mutate provider/tool/context
    # state regardless.
    _CANCEL_JOIN_TIMEOUT_SECONDS: ClassVar[float] = 2.0

    # Bound on a ``!``/``!!`` editor shell command so it cannot hang the session
    # indefinitely (Escape cancels earlier in a live TTY; a non-TTY script has no
    # cancel key, so the deadline is the only bound there). Generous so ordinary
    # builds/tests finish well within it.
    _LOCAL_SHELL_TIMEOUT_SECONDS: ClassVar[int] = 600

    def _run_local_shell_shortcut(
        self,
        command_line: str,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        cwd: Path,
        user_bash_hooks: Sequence[HookHandler] = (),
        model_runtime: ExtensionModelRuntimeControl | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> str | None:
        """Run a ``!``/``!!`` editor shell shortcut; return context text or None.

        ``!!`` excludes the command from provider context (returns ``None``);
        ``!`` returns the command/output text to record into the conversation
        and native session tree. Output streams live into a shaded shell block,
        and Escape cancels a running command (terminating its process group)
        without tearing down the session. Runs no provider turn.
        """

        exclude_from_context = command_line.startswith("!!")
        command = (
            command_line[2:] if exclude_from_context else command_line[1:]
        ).strip()
        if not command:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: ! needs a command, e.g. !ls (use !! to skip recording).",
            )
            return None

        decision = dispatch_user_bash_hooks(
            user_bash_hooks,
            command=command,
            exclude_from_context=exclude_from_context,
            cwd=str(cwd),
            has_ui=terminal_ui is not None,
            notify_sink=lambda kind, message: self._emit_diagnostic(
                terminal_ui, error_stream, message
            ),
            ui_driver=ui_driver,
            model_runtime=model_runtime,
            flags=flags,
            project_trusted=project_trusted,
        )
        if not decision.allowed:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                f"pipy: shell command blocked by extension: {decision.reason}",
            )
            return None
        command = decision.command
        exclude_from_context = decision.exclude_from_context

        if terminal_ui is not None:
            terminal_ui.add_tool_call(f"$ {command}")
            sink: Callable[[str], None] = terminal_ui.append_tool_output
        else:
            print(f"$ {command}", file=error_stream)

            def sink(chunk: str) -> None:
                print(chunk, end="", file=error_stream, flush=True)

        if decision.result is not None:
            result = LocalShellResult(
                output=decision.result,
                exit_code=decision.exit_code,
                truncated=False,
                timed_out=False,
                cancelled=False,
                started=True,
            )
            sink(decision.result)
        else:
            result = self._execute_local_shell(
                command, sink=sink, terminal_ui=terminal_ui, cwd=cwd
            )

        output_text = result.output or "(no output)"
        # Status line mirrors the bash tool's _shape: a timeout, the exit code,
        # or cancellation. A non-zero exit (e.g. !false) is an error the model
        # should see, matching the real bash execution boundary.
        if result.cancelled:
            reason = result.cancel_reason or "escape"
            status_line = f"(cancelled by {reason})"
        elif result.timed_out:
            status_line = "(timed out)"
        else:
            status_line = f"exit code: {result.exit_code}"
        is_error = (
            result.timed_out
            or not result.started
            or (
                not result.cancelled
                and result.exit_code is not None
                and result.exit_code != 0
            )
        )
        if terminal_ui is not None:
            rendered = [status_line, *(output_text.splitlines() or [""])]
            terminal_ui.add_tool_result(lines=rendered, is_error=is_error)
        else:
            # Captured-stream path: the body already streamed through the sink,
            # so print only the status line (never re-print the output — that
            # duplicated every command's output).
            print(status_line, file=error_stream)

        if exclude_from_context or not result.started:
            return None
        return (
            "I ran a shell command in the workspace (not a tool call):\n\n"
            f"$ {command}\n{status_line}\n\n{output_text}"
        )

    # Ordinary-tier fallback cycle (Pi's base reasoning levels). The live cycle
    # is model-aware via ``state.current_thinking_levels()`` — it appends
    # ``xhigh``/``max`` when the active row maps them (Sol cycles all seven).
    # This constant is used only when no per-model level list is available.
    _THINKING_CYCLE_LEVELS: ClassVar[tuple[str, ...]] = (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
    )

    def _toggle_view_fold(
        self,
        hotkey: str,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        settings: "SettingsManager",
    ) -> None:
        """Toggle a renderer view fold (Pi Ctrl+O tool output / Ctrl+T thinking).

        Ctrl+O flips tool-output expansion (a pure live-render view flag); Ctrl+T
        flips thinking-block visibility and persists it to the non-secret
        settings store. Both run no provider turn and only mutate renderer view
        state (plus, for thinking, the settings file). A status is shown.
        """

        if hotkey == HOTKEY_TOGGLE_TOOLS:
            new_value = not (terminal_ui.tools_expanded if terminal_ui else False)
            if terminal_ui is not None:
                terminal_ui.tools_expanded = new_value
                terminal_ui.rerender_custom_messages()
            label = "expanded" if new_value else "collapsed"
            self._emit_diagnostic(
                terminal_ui, error_stream, f"pipy: tool output: {label}"
            )
            return
        # HOTKEY_TOGGLE_THINKING
        current = (
            terminal_ui.thinking_hidden
            if terminal_ui is not None
            else settings.get_hide_thinking_block()
        )
        new_hidden = not current
        if terminal_ui is not None:
            # Route through set_thinking_hidden so unfolding reveals any
            # reasoning that settled while folded (deferred, not dropped).
            terminal_ui.set_thinking_hidden(new_hidden)
        try:
            settings.set_value("hideThinkingBlock", new_hidden)
        except RuntimeError:
            # A read-only/locked settings file must not break the live toggle.
            pass
        label = "hidden" if new_hidden else "visible"
        self._emit_diagnostic(
            terminal_ui, error_stream, f"pipy: thinking blocks: {label}"
        )

    def _cycle_thinking_level(
        self,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        session_tree: NativeSessionTree,
    ) -> None:
        """Cycle the reasoning level (Pi's Shift+Tab ``cycleThinkingLevel``).

        Cycles off→minimal→low→medium→high (wrapping), clamped to whether the
        active model advertises reasoning support, sets the runtime level on the
        provider state (so the footer effort label reflects it), appends a
        ``thinking_level_change`` native-tree entry, and shows a status. Runs no
        provider turn; the new level applies to the next turn.
        """

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: thinking-level cycling is unavailable for this REPL state.",
            )
            return
        current = state.current_selection()
        supports_thinking = any(
            option.selection.provider_name == current.provider_name
            and option.selection.model_id == current.model_id
            and bool(option.reasoning)
            for option in state.model_options()
        )
        if not supports_thinking:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: current model does not support thinking.",
            )
            return
        # Model-aware cycle (Pi's Shift+Tab over getSupportedThinkingLevels):
        # the ordinary tier for every reasoning model, plus xhigh/max only when
        # the active row maps them.
        levels = tuple(state.current_thinking_levels()) or self._THINKING_CYCLE_LEVELS
        current_level = (
            state.thinking_level if state.thinking_level in levels else "off"
        )
        next_level = levels[(levels.index(current_level) + 1) % len(levels)]
        state.thinking_level = next_level
        session_tree.append_thinking_level_change(next_level)
        self._emit_diagnostic(
            terminal_ui, error_stream, f"pipy: thinking level: {next_level}"
        )

    def _execute_local_shell(
        self,
        command: str,
        *,
        sink: Callable[[str], None],
        terminal_ui: ToolLoopTerminalUi | None,
        cwd: Path,
    ) -> LocalShellResult:
        """Execute ``command`` locally, watching stdin for Escape cancellation.

        With no live TUI (captured streams), runs synchronously. With a live
        TUI, runs the command on a worker thread while the same active-turn
        interrupt watcher used for provider turns reads stdin; Escape/Ctrl-C set
        the cancel event so the runner kills the child process group, then the
        worker is best-effort joined.
        """

        if terminal_ui is None:
            return run_local_command(
                command,
                workspace_root=cwd,
                output_sink=sink,
                timeout=self._LOCAL_SHELL_TIMEOUT_SECONDS,
            )

        cancel_event = threading.Event()
        done_event = threading.Event()
        holder: list[LocalShellResult] = []

        def _worker() -> None:
            try:
                holder.append(
                    run_local_command(
                        command,
                        workspace_root=cwd,
                        output_sink=sink,
                        cancel_event=cancel_event,
                        timeout=self._LOCAL_SHELL_TIMEOUT_SECONDS,
                    )
                )
            finally:
                done_event.set()

        worker = threading.Thread(target=_worker, name="pipy-local-shell", daemon=True)
        worker.start()
        outcome = TURN_SETTLED
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, cancel_event, accept_commands=True
            )
        except KeyboardInterrupt:
            cancel_event.set()
            outcome = TURN_ABORTED
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
        cancel_reason = "local command" if outcome == TURN_LOCAL_COMMAND else "escape"
        if holder:
            result = holder[0]
            if result.cancelled:
                result.cancel_reason = cancel_reason
            return result
        return LocalShellResult(
            output="",
            exit_code=None,
            truncated=False,
            timed_out=False,
            cancelled=True,
            started=True,
            cancel_reason=cancel_reason,
        )

    def _effort_label(self, provider_name: str, model_id: str) -> str:
        """Reasoning-effort label, preferring the live runtime thinking level.

        When the user has cycled the thinking level with Shift+Tab (or selected
        a ``model:level`` reference), the provider state carries the runtime
        level and the footer reflects it; otherwise it falls back to the
        model's default effort label.
        """

        level = getattr(self.provider_state, "thinking_level", None)
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

    def _handle_trust_command(
        self,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        cwd: Path,
        settings: "SettingsManager",
    ) -> None:
        """Show and persist a next-start trust decision without hot loading."""

        if terminal_ui is None:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: /trust requires the interactive product TUI; use "
                "--approve for this run.",
            )
            return
        store = ProjectTrustStore()
        try:
            saved = store.get_entry(cwd)
        except ProjectTrustError as exc:
            terminal_ui.add_notice(f"pipy: could not read project trust: {exc}")
            return
        selected = run_project_trust_selector(
            terminal_ui,
            cwd=cwd,
            options=get_project_trust_options(cwd),
            saved_decision=saved,
            current_trusted=settings.project_trusted,
        )
        if selected is None:
            return
        try:
            store.set_many(selected.updates)
        except ProjectTrustError as exc:
            terminal_ui.add_notice(f"pipy: could not save project trust: {exc}")
            return
        terminal_ui.add_notice(
            "pipy: saved trust decision: "
            f"{'trusted' if selected.trusted else 'untrusted'}. "
            "Restart pipy for this to take effect."
        )

    def _maybe_save_implicit_trust_after_reload(
        self,
        *,
        cwd: Path,
        settings: "SettingsManager",
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
    ) -> bool:
        """Persist Pi's narrowly guarded no-resource-start reload exception."""

        resolved = cwd.expanduser().resolve()
        if self.auto_trust_on_reload_cwd != resolved:
            return False
        if not settings.project_trusted or not has_trust_requiring_project_resources(
            resolved
        ):
            return False
        store = ProjectTrustStore()
        try:
            if store.get(resolved) is not None:
                self.auto_trust_on_reload_cwd = None
                return False
            store.set(resolved, True)
        except ProjectTrustError as exc:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                f"pipy: could not save project trust after reload: {exc}",
            )
            return False
        self.auto_trust_on_reload_cwd = None
        return True

    def _settings_overlay_lines(
        self,
        settings_manager: "SettingsManager | None" = None,
        *,
        provider: ProviderPort,
    ) -> list[str]:
        """Build the read-only settings/status overlay content.

        Reuses the shared no-tool ``/settings`` builder so the tool-loop TUI
        shows the same safe provider/model/status information and availability
        reasons, then appends a footer honest for the tool-loop surface (where
        ``/model``, ``/login``, and ``/logout`` are all executable). When no
        provider state is wired, a single-provider static view is shown and the
        footer says those commands are unavailable for that state.
        """

        state = self.provider_state or StaticNativeReplProviderState(provider)
        lines = settings_overlay_lines(state, settings_manager)
        if isinstance(state, NativeReplProviderState):
            lines.append(
                "  read-only view; use /model to switch provider/model and "
                "/login or /logout to manage openai-codex OAuth."
            )
        else:
            lines.append(
                "  read-only view; /model, /login, and /logout are not "
                "available for this REPL provider state."
            )
        return lines

    def _drive_settings_dialog(
        self,
        terminal_ui: ToolLoopTerminalUi,
        prompt_history_store: PromptHistoryStore,
        *,
        provider: ProviderPort,
        apply_model_selection: Callable[[str], tuple[bool, str]],
        apply_auth_change: Callable[[str, str], str],
        settings: "SettingsManager",
        session_tree: NativeSessionTree,
        error_stream: TextIO,
    ) -> None:
        """Open the live ``/settings`` dialog and act on the user's choices.

        Local toggles (persistent prompt-history on/off, clear persisted
        history) are handled in place by the dialog without leaving it.
        Provider/model and auth actions reuse the existing
        ``NativeReplProviderState`` boundaries (``apply_model_selection`` /
        ``apply_auth_change``) and run **no** provider or tool turn; afterward
        the dialog re-opens so the user can keep adjusting settings. The dialog
        closes on Esc/Ctrl-C/Ctrl-D.
        """

        state = self.provider_state or StaticNativeReplProviderState(provider)
        is_native = isinstance(state, NativeReplProviderState)
        # Actions that need the terminal themselves (an interactive selector or
        # auth flow) close the dialog and are returned for the caller's
        # post-return branch to drive; everything else is handled locally by
        # ``on_local_action`` while the dialog stays open. The theme picker is
        # available for any provider state with a live TUI, so it is always an
        # exit action; the provider/model, auth, and scoped-models flows are
        # native-only (scoped models builds model patterns from the native
        # provider state, and its row is shown only for that state).
        exit_actions = frozenset({"theme", "project_trust_default"}) | (
            frozenset({"model", "login", "logout", "scoped_models"})
            if is_native
            else frozenset()
        )

        def _rows() -> list[SettingsRow]:
            return self._settings_dialog_rows(
                state,
                prompt_history_store,
                in_memory_depth=len(terminal_ui.input_history),
                terminal_ui=terminal_ui,
                settings=settings,
            )

        def _local_action(action: str) -> list[SettingsRow]:
            if action == "toggle_history":
                prompt_history_store.set_enabled(not prompt_history_store.enabled)
            elif action == "clear_history":
                # Wipe only the persisted store; the current session's in-memory
                # Up/Down recall keeps working (the goal only requires that a
                # *fresh* session not recall cleared prompts, and record() never
                # re-persists the existing recall buffer — only new prompts).
                prompt_history_store.clear()
            elif action == "toggle_tools":
                self._toggle_view_fold(
                    HOTKEY_TOGGLE_TOOLS,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    settings=settings,
                )
            elif action == "toggle_thinking":
                self._toggle_view_fold(
                    HOTKEY_TOGGLE_THINKING,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    settings=settings,
                )
            elif action == "cycle_thinking":
                self._cycle_thinking_level(
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    session_tree=session_tree,
                )
            return _rows()

        while True:
            action = terminal_ui.run_settings_dialog(
                _rows(),
                on_local_action=_local_action,
                exit_actions=exit_actions,
            )
            if action is None:
                return
            if action == "model" and isinstance(state, NativeReplProviderState):
                ui_options, selections = self._model_selector_rows(state)
                current = state.current_selection()
                current_index = next(
                    (
                        index
                        for index, selection in enumerate(selections)
                        if selection.provider_name == current.provider_name
                        and selection.model_id == current.model_id
                    ),
                    0,
                )
                chosen = terminal_ui.run_model_selector(
                    ui_options, current_index=current_index
                )
                if chosen is not None:
                    _ok, message = apply_model_selection(selections[chosen].reference)
                    terminal_ui.add_notice(message)
                continue
            if action in {"login", "logout"}:
                message = apply_auth_change(action, "")
                terminal_ui.add_notice(message)
                continue
            if action == "scoped_models" and isinstance(state, NativeReplProviderState):
                self._open_scoped_models_overlay(
                    terminal_ui, state=state, settings=settings
                )
                continue
            if action == "theme":
                self._open_theme_selector(terminal_ui, settings=settings)
                continue
            if action == "project_trust_default":
                self._open_default_project_trust_selector(
                    terminal_ui, settings=settings
                )
                continue

    def _open_scoped_models_overlay(
        self,
        terminal_ui: ToolLoopTerminalUi,
        *,
        state: NativeReplProviderState,
        settings: "SettingsManager",
    ) -> None:
        """Open the multi-select scope overlay and persist the chosen scope.

        Builds a checklist of available models, pre-checks those matching the
        current ``enabledModels`` patterns, and on save writes the chosen
        ``provider/model`` references back as the patterns the Ctrl+P cycle uses.
        Runs no provider turn.
        """

        available_refs = [
            option.selection.reference
            for option in state.model_options()
            if option.available
        ]
        if not available_refs:
            terminal_ui.add_notice("pipy: no available models to scope.")
            return
        scoped = filter_scoped_references(available_refs, settings.get_enabled_models())
        rows = [ScopedModelRow(reference=ref, available=True) for ref in available_refs]
        pre_checked = [
            index for index, ref in enumerate(available_refs) if ref in scoped
        ]
        chosen = terminal_ui.run_scoped_models_selector(rows, checked=pre_checked)
        if chosen is None:
            return
        try:
            settings.set_enabled_models(sorted(chosen))
            message = (
                "pipy: scoped models set: " + ", ".join(sorted(chosen))
                if chosen
                else "pipy: scoped models cleared (cycle uses the full catalog)."
            )
        except RuntimeError as exc:
            message = f"pipy: could not update scoped models: {exc}"
        terminal_ui.add_notice(message)

    def _open_theme_selector(
        self,
        terminal_ui: ToolLoopTerminalUi,
        *,
        settings: "SettingsManager",
    ) -> None:
        """Open the theme picker and apply + persist the chosen chrome theme.

        Mirrors the ``action == "model"`` path: it builds one selectable row per
        registered theme (the active theme starts highlighted), opens the shared
        label/selectable selector with a theme-specific heading, and on a choice
        applies the theme via ``select_theme`` (which sets ``PIPY_THEME`` so the
        next rendered frame repaints and persists the non-secret name to the
        chrome store) and persists it through ``settings`` — the source of truth
        a later ``/reload`` re-reads. Runs no provider turn, tool call, or
        archive write; ``Esc`` leaves the theme unchanged.
        """

        names = available_theme_names()
        if not names:
            terminal_ui.add_notice("pipy: no themes available to select.")
            return
        active = resolve_active_theme_name(env=os.environ, store=NativeThemeStore())
        options = [
            ModelSelectorOption(
                label=f"{name} (active)" if name == active else name,
                selectable=True,
            )
            for name in names
        ]
        current_index = next(
            (index for index, name in enumerate(names) if name == active), 0
        )
        chosen = terminal_ui.run_model_selector(
            options, current_index=current_index, title="Select theme"
        )
        if chosen is None:
            return
        name = names[chosen]
        ok, message = select_theme(name, environ=os.environ, store=NativeThemeStore())
        if ok:
            # Settings is the source of truth (a later /reload re-applies
            # settings.get_theme() over the chrome store), so persist the choice
            # there too. A write failure keeps the live selection.
            try:
                settings.set_theme(name)
            except (OSError, RuntimeError):
                pass
        terminal_ui.add_notice(message)

    def _open_default_project_trust_selector(
        self,
        terminal_ui: ToolLoopTerminalUi,
        *,
        settings: "SettingsManager",
    ) -> None:
        """Select Pi's global-only trust fallback for future startups."""

        values: tuple[DefaultProjectTrust, ...] = (
            "ask",
            "always",
            "never",
        )
        labels = {
            "ask": "Ask",
            "always": "Trust",
            "never": "Do not trust",
        }
        current = settings.get_default_project_trust()
        options = [
            ModelSelectorOption(
                label=(
                    f"{labels[value]} (current)" if value == current else labels[value]
                ),
                selectable=True,
            )
            for value in values
        ]
        chosen = terminal_ui.run_model_selector(
            options,
            current_index=values.index(current),
            title="Default project trust",
        )
        if chosen is None:
            return
        value = values[chosen]
        try:
            settings.set_default_project_trust(value)
        except (OSError, RuntimeError, ValueError) as exc:
            terminal_ui.add_notice(
                f"pipy: could not update default project trust: {exc}"
            )
            return
        terminal_ui.add_notice(
            f"pipy: default project trust set to {labels[value]}; "
            "the current session is unchanged."
        )

    def _settings_dialog_rows(
        self,
        state: "NativeReplProviderState | StaticNativeReplProviderState",
        prompt_history_store: PromptHistoryStore,
        *,
        in_memory_depth: int,
        terminal_ui: ToolLoopTerminalUi | None = None,
        settings: "SettingsManager | None" = None,
    ) -> list[SettingsRow]:
        """Build the interactive ``/settings`` dialog rows.

        Strictly local/read-only construction: it probes the current
        selection, openai-codex auth availability, and prompt-history state but
        runs no provider turn, tool call, or auth/model mutation. Actionable
        rows carry an identifier the dialog hands back when activated; headers
        and read-only status rows stay visible for context but are not
        choosable.
        """

        current = state.current_selection()
        rows: list[SettingsRow] = [
            SettingsRow(label="Provider / model", kind="header"),
            SettingsRow(
                label=f"active: {sanitize_text(current.reference)}", kind="status"
            ),
        ]
        if isinstance(state, NativeReplProviderState):
            rows.append(
                SettingsRow(
                    label="change provider/model…", kind="action", action="model"
                )
            )
            rows.append(SettingsRow(label="Authentication", kind="header"))
            if state.provider_available("openai-codex"):
                rows.append(
                    SettingsRow(
                        label="openai-codex: logged in — log out",
                        kind="action",
                        action="logout",
                    )
                )
            else:
                rows.append(
                    SettingsRow(
                        label="openai-codex: logged out — log in",
                        kind="action",
                        action="login",
                    )
                )
        rows.append(SettingsRow(label="Prompt history", kind="header"))
        enabled = prompt_history_store.enabled
        rows.append(
            SettingsRow(
                label=(
                    f"persistent prompt history: {'on' if enabled else 'off'} — toggle"
                ),
                kind="action",
                action="toggle_history",
            )
        )
        rows.append(
            SettingsRow(
                label=(
                    "clear persisted history "
                    f"({len(prompt_history_store.entries())} saved)"
                ),
                kind="action",
                action="clear_history",
            )
        )
        rows.append(
            SettingsRow(
                label=f"in-memory recall this session: {in_memory_depth} prompts",
                kind="status",
            )
        )
        # Display / folding view flags and the thinking-level cycle (Ctrl+O /
        # Ctrl+T / Shift+Tab also drive these). Only meaningful with a live TUI.
        if terminal_ui is not None:
            rows.append(SettingsRow(label="Display", kind="header"))
            rows.append(
                SettingsRow(
                    label=(
                        "tool output: "
                        f"{'expanded' if terminal_ui.tools_expanded else 'collapsed'}"
                        " — toggle (ctrl+o)"
                    ),
                    kind="action",
                    action="toggle_tools",
                )
            )
            rows.append(
                SettingsRow(
                    label=(
                        "thinking blocks: "
                        f"{'hidden' if terminal_ui.thinking_hidden else 'visible'}"
                        " — toggle (ctrl+t)"
                    ),
                    kind="action",
                    action="toggle_thinking",
                )
            )
            level = getattr(state, "thinking_level", None) or "off"
            rows.append(
                SettingsRow(
                    label=f"thinking level: {level} — cycle (shift+tab)",
                    kind="action",
                    action="cycle_thinking",
                )
            )
            active_theme = resolve_active_theme_name(
                env=os.environ, store=NativeThemeStore()
            )
            rows.append(
                SettingsRow(
                    label=f"theme: {active_theme} — change…",
                    kind="action",
                    action="theme",
                )
            )
        if isinstance(state, NativeReplProviderState):
            rows.append(SettingsRow(label="Model cycle", kind="header"))
            rows.append(
                SettingsRow(
                    label="scoped models (Ctrl+P cycle set)…",
                    kind="action",
                    action="scoped_models",
                )
            )
        rows.append(SettingsRow(label="Project trust", kind="header"))
        trust_labels = {
            "ask": "Ask",
            "always": "Trust",
            "never": "Do not trust",
        }
        trust_default = (
            settings.get_default_project_trust() if settings is not None else "ask"
        )
        rows.append(
            SettingsRow(
                label=(
                    f"default project trust: {trust_labels[trust_default]} — change…"
                ),
                kind="action",
                action="project_trust_default",
            )
        )
        rows.append(SettingsRow(label="Providers (read-only)", kind="header"))
        for option in state.model_options():
            availability = (
                "available"
                if option.available
                else f"unavailable ({option.reason or 'unknown'})"
            )
            rows.append(
                SettingsRow(
                    label=(
                        f"{sanitize_text(option.selection.reference)} [{availability}]"
                    ),
                    kind="status",
                )
            )
        return rows

    def _model_selector_rows(
        self, state: NativeReplProviderState
    ) -> tuple[list[ModelSelectorOption], list[NativeModelSelection]]:
        """Build the interactive selector rows from the provider-state options.

        Returns the display rows (parallel to ``selections``) and the matching
        ``NativeModelSelection`` list so the caller can map a chosen index back
        to a provider/model reference. A row is selectable only when the
        provider is locally available *and* the built provider advertises
        tool-call support, which tool-loop mode requires. Unavailable or
        non-tool-capable rows stay visible with a reason but are not choosable,
        so the selector never lets a user pick a provider as if it were usable.
        """

        current = state.current_selection()

        def _matches_current(selection: NativeModelSelection) -> bool:
            return (
                selection.provider_name == current.provider_name
                and selection.model_id == current.model_id
            )

        ui_options: list[ModelSelectorOption] = []
        selections: list[NativeModelSelection] = []
        # The active selection may use a non-default model (explicit
        # --native-model or a prior /model <provider>/<custom-model>), which is
        # not present in model_options(). Surface it as the first row so the
        # selector can mark it "(current)" and start the highlight on it. The
        # active provider is tool-capable by the tool-loop invariant, so the row
        # is selectable.
        if not any(
            _matches_current(option.selection) for option in state.model_options()
        ):
            selections.append(current)
            ui_options.append(
                ModelSelectorOption(
                    label=f"{current.reference}  [available] (current)",
                    selectable=True,
                )
            )
        for option in state.model_options():
            selection = option.selection
            selectable = option.available
            reason = option.reason
            if selectable and not self._selection_supports_tool_calls(state, selection):
                selectable = False
                reason = "no tool-call support"
            if selectable:
                status = "available"
            else:
                status = f"unavailable: {reason or 'unknown'}"
            label = f"{selection.reference}  [{status}]"
            if _matches_current(selection):
                label = f"{label} (current)"
            ui_options.append(ModelSelectorOption(label=label, selectable=selectable))
            selections.append(selection)
        return ui_options, selections

    @staticmethod
    def _selection_supports_tool_calls(
        state: NativeReplProviderState, selection: NativeModelSelection
    ) -> bool:
        """Return whether the provider for ``selection`` advertises tool calls.

        Builds the provider through the state's catalog-aware construction
        boundary (cheap, side-effect-free construction) only to read
        ``supports_tool_calls`` — so a models.json custom provider/model (api:
        openai-completions) is probed the way it will be used. Any construction
        failure is treated as "not tool-capable" so a broken selection is never
        offered as choosable.
        """

        try:
            provider = state.provider_for(selection)
        except Exception:
            return False
        return bool(getattr(provider, "supports_tool_calls", False))

    @staticmethod
    def _emit_diagnostic(
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        message: str,
    ) -> None:
        if terminal_ui is not None:
            terminal_ui.add_notice(message)
            return
        safe_message = "\n".join(
            sanitize_label_text(line) for line in str(message).splitlines()
        )
        print(safe_message, file=error_stream)

    def _copy_last_answer(
        self, messages: Sequence[AgentMessage], *, error_stream: TextIO
    ) -> str:
        """Copy the most recent assistant answer; return a local status line.

        This is a purely local operation: it reads the in-memory conversation,
        copies through the injected clipboard path, and reports what happened.
        It never invokes the provider, tools, login/logout, or model switching.
        """

        answer = self._last_assistant_answer(messages)
        if not answer:
            return "pipy: nothing to copy yet (no assistant answer in this session)."
        result = self.clipboard_copy(answer, terminal_stream=error_stream)
        if result.copied:
            return f"pipy: copied last answer to clipboard ({result.detail})."
        return f"pipy: could not copy last answer — {result.detail}."

    def _run_interactive_session_picker(
        self,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi",
    ) -> Path | None:
        """Drive the live-TTY ``/resume`` picker over native product sessions.

        Lists the current project's sessions (Tab toggles to all projects),
        offers in-overlay rename/delete (the active session cannot be deleted),
        and returns the chosen native session file or ``None`` on cancel. Runs
        no provider turn and no model-visible tool call.
        """

        session_dir = (
            session_tree.path.parent
            if session_tree.path is not None
            else default_native_session_dir(Path(session_tree.get_header().cwd))
        )
        sessions_root = session_dir.parent
        project_sessions = list_native_sessions(session_dir)
        all_sessions = list_all_native_sessions(sessions_root)

        def on_rename(path: Path, name: str) -> None:
            # Renaming the currently active session must update the live tree so
            # `/session` and the footer reflect the new name immediately; other
            # sessions are renamed through a separately opened tree.
            if session_tree.path is not None and path == session_tree.path:
                session_tree.append_session_info(name)
            else:
                NativeSessionTree.open(path).append_session_info(name)

        def on_delete(path: Path) -> tuple[bool, str]:
            return delete_native_session(path)

        return terminal_ui.run_session_picker(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=session_tree.path,
            on_rename=on_rename,
            on_delete=on_delete,
        )

    def _run_interactive_tree_selector(
        self,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi",
        error_stream: TextIO,
        filter_mode: str,
        rebuild_messages: Callable[[], None],
    ) -> TreeCommandOutcome:
        """Drive the live-TTY ``/tree`` selector and apply the chosen entry.

        Builds filtered rows for the selector, toggles labels on demand, and on
        Enter applies Pi selection semantics: a user message rehydrates the
        editor for a new branch; any other entry sets the leaf with an empty
        editor. Escape cancels with the tree and leaf unchanged.
        """

        from pipy_harness.native.tui import TreeSelectorRow

        def build_rows(mode: str) -> list[TreeSelectorRow]:
            active_ids = {e.id for e in session_tree.get_branch()}
            rows: list[TreeSelectorRow] = []
            for entry in visible_tree_entries(session_tree, filter_mode=mode):
                rows.append(
                    TreeSelectorRow(
                        entry_id=entry.id,
                        label=entry_preview(session_tree, entry),
                        active=entry.id in active_ids,
                        labeled=session_tree.get_label(entry.id) is not None,
                    )
                )
            return rows

        def on_label_toggle(entry_id: str) -> None:
            existing = session_tree.get_label(entry_id)
            session_tree.append_label_change(entry_id, None if existing else "marked")

        chosen = terminal_ui.run_tree_selector(
            build_rows=build_rows,
            filter_modes=FILTER_MODES,
            initial_filter=filter_mode if filter_mode in FILTER_MODES else "default",
            on_label_toggle=on_label_toggle,
        )
        new_filter = terminal_ui.tree_selector_filter
        if chosen is None:
            self._emit_diagnostic(terminal_ui, error_stream, "pipy: /tree cancelled.")
            return TreeCommandOutcome(filter_mode=new_filter)
        selection = apply_tree_selection(session_tree, chosen)
        rebuild_messages()
        if selection.is_noop:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: already at the selected point (no change).",
            )
            return TreeCommandOutcome(filter_mode=new_filter)
        if selection.is_user_selection:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: selected user message; rehydrating editor for a new branch.",
            )
            return TreeCommandOutcome(
                prefill=selection.editor_text, filter_mode=new_filter
            )
        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            f"pipy: continuing from entry {sanitize_label_text(chosen[:8])}.",
        )
        return TreeCommandOutcome(filter_mode=new_filter)

    def _handle_tree_command(
        self,
        argument: str,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi | None",
        error_stream: TextIO,
        repl_input: object,
        filter_mode: str,
        rebuild_messages: Callable[[], None],
        summarizer: Callable[[list[AgentMessage], str | None], str | None]
        | None = None,
    ) -> TreeCommandOutcome:
        """Adapt the owner command handler to optional terminal interaction."""

        del repl_input
        interactive_selector: Callable[[], TreeCommandOutcome] | None = None
        if terminal_ui is not None and hasattr(terminal_ui, "run_tree_selector"):
            interactive_selector = partial(
                self._run_interactive_tree_selector,
                session_tree=session_tree,
                terminal_ui=terminal_ui,
                error_stream=error_stream,
                filter_mode=filter_mode,
                rebuild_messages=rebuild_messages,
            )
        return handle_tree_command(
            argument,
            session_tree=session_tree,
            filter_mode=filter_mode,
            rebuild_messages=rebuild_messages,
            diagnostic=lambda message: self._emit_diagnostic(
                terminal_ui, error_stream, message
            ),
            summarizer=summarizer,
            interactive_selector=interactive_selector,
        )

    @staticmethod
    def _last_assistant_answer(messages: Sequence[AgentMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AgentAssistantMessage):
                content = message.content.value.strip()
                if content:
                    return message.content.value
        return ""


__all__ = [
    "NativeToolReplSession",
    "production_tool_registry",
]
