"""Production composition phases for one native REPL session.

The concrete session facade passes only explicit values and its two established
factory seams.  Each phase returns an immutable record; live run mutation remains
owned by ``RunControlState`` and the collaborators assembled here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TextIO

import pipy_harness.native.repl.loop_step as _repl_loop_step
import pipy_harness.native.tool_renderers as _tool_renderers
from pipy_harness.models import HarnessStatus
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent import (
    AgentEventSink,
    AgentMessage,
    ProductContent,
)
from pipy_harness.native.agent.provider_turn import (
    ProviderTurnExecutor,
    _AbortCallbackSignal,
)
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
    _ChromeFooterEffects,
    print_startup_chrome,
)
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.product_session import (
    CodingProductSessionCallbacks,
    CodingProductSessionCompaction,
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
)
from pipy_harness.native.coding.result import CodingSessionResult
from pipy_harness.native.coding.session_controller import (
    CodingCommandEffects,
    CodingSessionController,
    LoopStepSignal,
    _CallableCodingCommandEffects,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_hooks import (
    _activate_workspace_extensions,
)
from pipy_harness.native.extension_hooks import (
    dispatch_session_before_hooks as dispatch_session_before_hooks,
)
from pipy_harness.native.extension_types import (
    QueuedCustomMessage,
    QueuedUserMessage,
    normalize_shortcut_key,
)
from pipy_harness.native.extensions.activation import _ExtensionCandidate
from pipy_harness.native.extensions.contracts import (
    ExtensionActivationBatch,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.flag_tokens import parse_extension_flag_tokens
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.package_resources import PackageResourceRoots
from pipy_harness.native.package_runtime import (
    compose_package_runtime,
)
from pipy_harness.native.project_trust import (
    has_trust_requiring_project_resources,
)
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.repl.collaborators import SessionCollaborators
from pipy_harness.native.repl.command_menu import (
    published_command_surface,
    tool_loop_command_descriptions,
    tool_loop_command_names,
)
from pipy_harness.native.repl.command_router import BuiltinCommandInterpreter
from pipy_harness.native.repl.execution_projections import (
    SessionExecutionProjections,
    apply_startup_provider_projection,
)
from pipy_harness.native.repl.extension_attach import (
    AttachGenerationRefusal,
    ExtensionAttachInput,
    StartupAttachPorts,
    StartupGenerationAttachment,
    attach_generation,
)
from pipy_harness.native.repl.extension_operations import (
    SessionExtensionOperations,
)
from pipy_harness.native.repl.loop_scope import (
    ReplLoopScope,
    RunControlState,
)
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.reload import (
    ImplicitTrustState,
)
from pipy_harness.native.repl.turn_leaves import (
    CANCEL_JOIN_TIMEOUT_SECONDS,
    pricing_for,
    raise_first,
)
from pipy_harness.native.repl_input import (
    NativeReplInput,
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
    ExtensionCommandProjection,
    ExtensionProjection,
    SessionGenerationRef,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_status_line,
)
from pipy_harness.native.session_state_lock import SessionStateLock
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
    _ExtensionRenderDetailsSinks,
)
from pipy_harness.native.tool_renderers import (
    _ToolLoopRenderer as _ToolLoopRenderer,
)
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.tui import ToolLoopTerminalUi, _LiveExtensionUiDriver
from pipy_harness.native.ui import RenderingAgentEventAdapter
from pipy_harness.native.ui.components.custom_entry_renderer import CustomEntryRenderer
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.version_check import pipy_version


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionWiringInput:
    candidate: _ExtensionCandidate
    cwd: Path
    input_stream: TextIO
    output_stream: TextIO
    error_stream: TextIO
    system_prompt: str
    provider_name: str | None
    model_id: str | None
    build_terminal_ui: Callable[..., ToolLoopTerminalUi | None]
    build_repl_input: Callable[..., NativeReplInput]
    coding_state: CodingSessionState
    abort_event: threading.Event | _AbortCallbackSignal | None
    agent_event_sink: AgentEventSink | None
    automation_observer: AutomationEventSink | None
    clipboard_copy: Callable[..., ClipboardResult]
    implicit_trust: ImplicitTrustState
    initial_extension_batch: ExtensionActivationBatch | None
    initial_messages: tuple[str, ...]
    keybindings_manager: KeybindingsManager | None
    native_session: NativeSessionTree | None
    prompt_history_store: PromptHistoryStore | None
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    reference_roots: tuple[Path, ...]
    resource_options: RuntimeResourceOptions
    resume_branch_label: str | None
    resume_context: ResumeContext | None
    settings_manager: SettingsManager | None
    tool_budget: int
    tool_filter_options: ToolFilterOptions
    tool_registry: dict[str, ToolPort]
    verbose_startup: bool


@dataclass(frozen=True, slots=True)
class _LoopDelegation:
    loop_controller: CodingSessionController
    step_once: Callable[[], LoopStepSignal]
    finalize: Callable[[], CodingSessionResult]
    fire_session_start: Callable[[], None]
    fire_session_shutdown: Callable[[], None]
    consume_settle_pending: Callable[[], bool]
    close_extension_session: Callable[[], None]
    clear_extension_chrome: Callable[[], None]


@dataclass(frozen=True, slots=True)
class SessionWiring:
    startup_failure: CodingSessionResult | None
    delegation: _LoopDelegation | None


@dataclass(frozen=True, slots=True)
class _StartupPhase:
    cwd: Path
    stderr_sink: Callable[[str], None]
    coding_state: CodingSessionState
    session_state_lock: SessionStateLock
    coding_effects: CodingEffectCoordinator
    keybindings: KeybindingsManager
    settings: SettingsManager
    resource_options: RuntimeResourceOptions
    package_roots: PackageResourceRoots
    workspace_resources: WorkspaceResources
    extension_bundle: _ExtensionRuntime
    extension_flag_values: Mapping[str, object]
    extension_in_agent_turn: bool


@dataclass(frozen=True, slots=True)
class _ExtensionPhase:
    terminal_ui: ToolLoopTerminalUi | None
    extension_notify: Callable[[str, str], None]
    extension_ui_driver: _LiveExtensionUiDriver | None
    render_details: _ExtensionRenderDetailsSinks
    tool_capabilities: NativeToolCapabilities
    startup_projection: ExtensionProjection
    attachment: StartupGenerationAttachment
    generation_ref: SessionGenerationRef
    execution_projections: SessionExecutionProjections
    provider_turn_executor: ProviderTurnExecutor
    startup_commands: ExtensionCommandProjection
    agent_settled_pending: bool


@dataclass(frozen=True, slots=True)
class _ProductPhase:
    image_reference_roots: tuple[Path, ...]
    prompt_history_store: PromptHistoryStore
    renderer: _ToolLoopRenderer | TuiToolLoopRenderer
    started_at: datetime
    base_system_prompt: str
    ctl: RunControlState
    product_session: CodingProductSessionCoordinator
    append_agent_message: Callable[[AgentMessage], None]
    emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter


@dataclass(frozen=True, slots=True)
class _RuntimePhase:
    emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter
    usage_publisher: NativeAgentUsagePublisher
    input_queued_input_port: NativeAgentQueuedInputPort | None
    coding_input_queue: CodingInputQueue
    loop_controller: CodingSessionController
    custom_renderer: CustomEntryRenderer


@dataclass(frozen=True, slots=True)
class _ChromePhase:
    repl_input: NativeReplInput
    footer: _ChromeFooterEffects


@dataclass(frozen=True, slots=True)
class _CollaboratorPhase:
    extension_operations: SessionExtensionOperations
    provider_mutation: ProviderMutationEffects
    collaborators: SessionCollaborators
    provider_request_policy: NativeAgentProviderRequestPolicy
    agent_tool_policy: NativeAgentToolPolicy


@dataclass(frozen=True, slots=True)
class _CommandPhase:
    command_effects: CodingCommandEffects


class _ProviderMutationBinding:
    """One-shot late binding for callbacks created before mutation effects."""

    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value: ProviderMutationEffects | None = None

    def bind(self, value: ProviderMutationEffects) -> None:
        if self._value is not None:
            raise RuntimeError("provider mutation effects are already bound")
        self._value = value

    def _bound(self) -> ProviderMutationEffects:
        if self._value is None:
            raise RuntimeError("provider mutation effects are not bound")
        return self._value

    def set_active_tools(self, generation_id: int, names: Sequence[str]) -> bool:
        return self._bound().extension_set_active_tools(generation_id, names)

    def append_durable_compaction(self, summary: str, measure_before: int) -> None:
        self._bound().append_durable_compaction(summary, measure_before)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ExtensionCustomEntryRunState:
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


def _fail_startup(
    coding_state: CodingSessionState, error_type: str, message: str
) -> CodingSessionResult:
    return CodingSessionResult(
        status=HarnessStatus.FAILED,
        exit_code=2,
        started_at=(now := datetime.now(UTC)),
        ended_at=now,
        provider_name=coding_state.provider_name,
        model_id=coding_state.model_id,
        error_type=error_type,
        error_message=message,
    )


def _abort_startup_attachment_nonraising(
    attachment: StartupGenerationAttachment,
) -> BaseException | None:
    try:
        attachment.abort()
    except BaseException as error:  # noqa: BLE001 - preserve startup primary
        return error
    return None


def _prepare_startup(
    inputs: SessionWiringInput,
) -> _StartupPhase | CodingSessionResult:
    cwd = inputs.cwd
    error_stream = inputs.error_stream
    candidate = inputs.candidate

    def _stderr_sink(text: str) -> None:
        error_stream.write(text)

    coding_state = inputs.coding_state
    seed_provider = coding_state.provider
    initial_provider_name = inputs.provider_name or seed_provider.name
    initial_model_id = inputs.model_id or seed_provider.model_id
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
    session_state_lock = SessionStateLock(threading.RLock())
    coding_effects = CodingEffectCoordinator()
    keybindings = inputs.keybindings_manager or KeybindingsManager.create(
        state_lock=session_state_lock
    )
    keybindings.bind_state_lock(session_state_lock)
    settings = inputs.settings_manager or SettingsManager.for_workspace(cwd)
    settings.bind_state_lock(session_state_lock)
    # The coding state is built in `__post_init__`, before a session
    # exists, so it starts on a private lock and adopts the shared one
    # here. An extension handler reaching `set_model` from a worker thread
    # rebinds its provider and clears its history; that must serialize
    # against the session thread's own reads and writes.
    coding_state.bind_state_lock(session_state_lock)
    if isinstance(inputs.provider_state, NativeReplProviderState):
        inputs.provider_state.bind_state_lock(session_state_lock)
    resource_options = inputs.resource_options
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
    extension_bundle = _activate_workspace_extensions(
        cwd,
        workspace_resources,
        tuple(inputs.tool_registry.keys()),
        package_roots=()
        if resource_options.no_extensions
        else package_roots.extensions,
        extension_patterns=settings.get_extensions_patterns(),
        explicit_extension_paths=resource_options.extension_paths,
        include_default_extensions=not resource_options.no_extensions,
        include_workspace_defaults=settings.project_trusted,
        activation_batch=inputs.initial_extension_batch,
        diagnostic=lambda message: emit_diagnostic(None, error_stream, message),
    )
    candidate.adopt(
        extension_bundle,
        partial(emit_diagnostic, None, error_stream),
    )
    extension_flag_values, extension_flag_error = parse_extension_flag_tokens(
        extension_bundle.flags,
        tuple(resource_options.extension_flag_tokens),
    )
    if extension_flag_error is not None:
        print(f"pipy: {extension_flag_error}", file=error_stream)
        return _fail_startup(coding_state, "ExtensionFlagError", extension_flag_error)
    extension_in_agent_turn = False
    return _StartupPhase(
        cwd=cwd,
        stderr_sink=_stderr_sink,
        coding_state=coding_state,
        session_state_lock=session_state_lock,
        coding_effects=coding_effects,
        keybindings=keybindings,
        settings=settings,
        resource_options=resource_options,
        package_roots=package_roots,
        workspace_resources=workspace_resources,
        extension_bundle=extension_bundle,
        extension_flag_values=extension_flag_values,
        extension_in_agent_turn=extension_in_agent_turn,
    )


def _compose_extension_phase(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    provider_binding: _ProviderMutationBinding,
) -> _ExtensionPhase | CodingSessionResult:
    cwd = startup.cwd
    coding_state = startup.coding_state
    session_state_lock = startup.session_state_lock
    keybindings = startup.keybindings
    settings = startup.settings
    workspace_resources = startup.workspace_resources
    extension_bundle = startup.extension_bundle
    error_stream = inputs.error_stream
    terminal_ui = inputs.build_terminal_ui(
        input_stream=inputs.input_stream,
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

    def _extension_notify(_kind: str, message: str) -> None:
        safe_message = "\n".join(
            sanitize_label_text(line) for line in str(message).splitlines()
        )
        emit_diagnostic(terminal_ui, error_stream, safe_message)

    extension_ui_driver = (
        _LiveExtensionUiDriver(terminal_ui, cwd) if terminal_ui is not None else None
    )
    render_details = _tool_renderers._extension_render_details_sinks(
        terminal_ui is not None
    )
    tool_capabilities = NativeToolCapabilities(
        inputs.tool_registry,
        {},
        workspace_root=cwd,
        reference_roots=inputs.reference_roots,
        stderr_sink=startup.stderr_sink,
        filter_options=inputs.tool_filter_options,
        cancel_join_timeout_seconds=CANCEL_JOIN_TIMEOUT_SECONDS,
        state_lock=session_state_lock,
    )

    def _prepare_before_publish(
        generation_ref: SessionGenerationRef, projection: ExtensionProjection
    ) -> None:
        _prepare_startup_extension_consumers(
            terminal_ui=terminal_ui,
            settings=settings,
            workspace_resources=workspace_resources,
            startup_commands=projection.commands,
            keybindings=keybindings,
            error_stream=error_stream,
        )
        apply_startup_provider_projection(
            generation_ref=generation_ref,
            provider_state=inputs.provider_state,
            coding_state=coding_state,
            error_stream=error_stream,
        )

    attached = attach_generation(
        ExtensionAttachInput(
            candidate=inputs.candidate,
            runtime=extension_bundle,
            flag_values=startup.extension_flag_values,
            state_lock=session_state_lock,
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            set_active_tools=lambda generation_id, names: (
                provider_binding.set_active_tools(generation_id, names)
            ),
            render_details=render_details.writer,
            project_trusted=settings.project_trusted,
            tool_capabilities=tool_capabilities,
            chrome_sink=(
                extension_ui_driver.startup_chrome_sink()
                if extension_ui_driver is not None
                else None
            ),
        ),
        startup_ports=StartupAttachPorts(before_publish=_prepare_before_publish),
    )
    if isinstance(attached, AttachGenerationRefusal):
        print(f"pipy: {attached.reason}", file=error_stream)
        return _fail_startup(coding_state, "ExtensionActivationError", attached.reason)
    assert isinstance(attached, StartupGenerationAttachment)
    startup_projection = attached.projection
    generation_ref = attached.generation_ref
    startup_commands = startup_projection.commands
    try:
        execution_projections = SessionExecutionProjections(
            generation_ref=generation_ref,
            tool_capabilities=tool_capabilities,
            coding_state=coding_state,
            ui_driver=extension_ui_driver,
        )
        provider_turn_executor = ProviderTurnExecutor(
            cancel_join_timeout_seconds=CANCEL_JOIN_TIMEOUT_SECONDS,
        )
        _raise_unknown_startup_tool_filters(tool_capabilities)
    except BaseException as error:  # noqa: BLE001 - preserve startup primary
        raise_first((error, _abort_startup_attachment_nonraising(attached)))
        raise AssertionError("startup failure did not propagate")
    return _ExtensionPhase(
        terminal_ui=terminal_ui,
        extension_notify=_extension_notify,
        extension_ui_driver=extension_ui_driver,
        render_details=render_details,
        tool_capabilities=tool_capabilities,
        startup_projection=startup_projection,
        attachment=attached,
        generation_ref=generation_ref,
        execution_projections=execution_projections,
        provider_turn_executor=provider_turn_executor,
        startup_commands=startup_commands,
        agent_settled_pending=False,
    )


def _prepare_startup_extension_consumers(
    *,
    terminal_ui: ToolLoopTerminalUi | None,
    settings: SettingsManager,
    workspace_resources: WorkspaceResources,
    startup_commands: ExtensionCommandProjection,
    keybindings: KeybindingsManager,
    error_stream: TextIO,
) -> None:
    if terminal_ui is None:
        return
    terminal_ui.autocomplete.set_max_visible(settings.get_autocomplete_max_visible())
    terminal_ui.autocomplete.replace_command_surface(
        published_command_surface(workspace_resources, startup_commands)
    )
    if not keybindings.has_user_binding("app.editor.external"):
        return
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


def _raise_unknown_startup_tool_filters(
    tool_capabilities: NativeToolCapabilities,
) -> None:
    unknown_filter_names = tool_capabilities.unknown_filter_names
    if not unknown_filter_names:
        return
    known = ", ".join(sorted(tool_capabilities.registered_names)) or "<none>"
    unknown = ", ".join(unknown_filter_names)
    raise ValueError(f"unknown tool name(s): {unknown}. Known tools: {known}")


def _compose_emitter(
    inputs: SessionWiringInput,
    *,
    renderer: _ToolLoopRenderer | TuiToolLoopRenderer,
    append_agent_message: Callable[[AgentMessage], None],
    generation_ref: SessionGenerationRef,
    cwd: Path,
    terminal_ui: ToolLoopTerminalUi | None,
    extension_notify: Callable[[str, str], None],
    extension_ui_driver: _LiveExtensionUiDriver | None,
    project_trusted: bool,
) -> _extension_hooks._ExtensionLifecycleAgentEventAdapter:
    product_action_sink = NativeProductSessionActionSink(append_agent_message)
    immediate_sinks: list[AgentEventSink] = [RenderingAgentEventAdapter(renderer)]
    if inputs.automation_observer is not None:
        immediate_sinks.append(AutomationAgentEventAdapter(inputs.automation_observer))
    immediate_sinks.extend(
        (
            ProductSessionEventProjection(product_action_sink),
            WorkflowArchiveAgentEventAdapter(),
        )
    )
    if inputs.agent_event_sink is not None:
        immediate_sinks.append(inputs.agent_event_sink)
    immediate_sink = SynchronousAgentEventComposite(tuple(immediate_sinks))
    return _extension_hooks._ExtensionLifecycleAgentEventAdapter(
        immediate_sink,
        generation_snapshot=generation_ref.snapshot,
        cwd=str(cwd),
        has_ui=terminal_ui is not None,
        notify_sink=extension_notify,
        ui_driver=extension_ui_driver,
        project_trusted=project_trusted,
    )


def _compose_product_session(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    provider_binding: _ProviderMutationBinding,
) -> _ProductPhase:
    cwd = startup.cwd
    coding_state = startup.coding_state
    coding_effects = startup.coding_effects
    settings = startup.settings
    package_roots = startup.package_roots
    workspace_resources = startup.workspace_resources
    terminal_ui = extension.terminal_ui
    render_details = extension.render_details
    generation_ref = extension.generation_ref
    extension_notify = extension.extension_notify
    extension_ui_driver = extension.extension_ui_driver
    agent_settled_pending = extension.agent_settled_pending
    extension_in_agent_turn = startup.extension_in_agent_turn
    output_stream = inputs.output_stream
    error_stream = inputs.error_stream
    system_prompt = inputs.system_prompt
    image_reference_roots = inputs.reference_roots
    if terminal_ui is not None:
        terminal_ui.set_thinking_hidden(settings.get_hide_thinking_block())
        clipboard_config = terminal_ui.clipboard_images.config
        if clipboard_config is not None:
            image_reference_roots = (
                *inputs.reference_roots,
                clipboard_config.temp_dir,
            )
    # Local-only persistent prompt-history store (independent of the
    # metadata-first session archive). Built once per session; the
    # ``/settings`` dialog toggles/clears it. When enabled, a fresh TUI
    # session seeds its in-memory recall buffer from the saved prompts.
    prompt_history_store = inputs.prompt_history_store or PromptHistoryStore()
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
    session_tree = inputs.native_session or NativeSessionTree.create(cwd, persist=False)
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
        provider_binding.append_durable_compaction(
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

    # Preserve the fixed renderer -> automation -> product -> archive -> caller
    # order before wrapping the completed product sink with extension lifecycle.
    emitter = _compose_emitter(
        inputs,
        renderer=renderer,
        append_agent_message=append_agent_message,
        generation_ref=generation_ref,
        cwd=cwd,
        terminal_ui=terminal_ui,
        extension_notify=extension_notify,
        extension_ui_driver=extension_ui_driver,
        project_trusted=settings.project_trusted,
    )
    return _ProductPhase(
        image_reference_roots=image_reference_roots,
        prompt_history_store=prompt_history_store,
        renderer=renderer,
        started_at=started_at,
        base_system_prompt=base_system_prompt,
        ctl=ctl,
        product_session=product_session,
        append_agent_message=append_agent_message,
        emitter=emitter,
    )


def _compose_runtime_adapters(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    product: _ProductPhase,
) -> _RuntimePhase:
    coding_state = startup.coding_state
    coding_effects = startup.coding_effects
    terminal_ui = extension.terminal_ui
    attachment = extension.attachment
    ctl = product.ctl
    emitter = product.emitter
    input_stream = inputs.input_stream
    error_stream = inputs.error_stream

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

    terminal_queued_input_port = NativeAgentQueuedInputPort(take_terminal_queued_input)

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
            ProductContent(message) for message in inputs.initial_messages if message
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
    attachment.deliver_staged(
        partial(
            _extension_hooks.deliver_staged_custom,
            custom_renderer.extension_send_message,
        )
    )
    return _RuntimePhase(
        emitter=emitter,
        usage_publisher=usage_publisher,
        input_queued_input_port=input_queued_input_port,
        coding_input_queue=coding_input_queue,
        loop_controller=loop_controller,
        custom_renderer=custom_renderer,
    )


def _start_chrome(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    product: _ProductPhase,
    runtime: _RuntimePhase,
) -> _ChromePhase:
    cwd = startup.cwd
    coding_state = startup.coding_state
    settings = startup.settings
    workspace_resources = startup.workspace_resources
    terminal_ui = extension.terminal_ui
    startup_commands = extension.startup_commands
    custom_renderer = runtime.custom_renderer
    input_stream = inputs.input_stream
    error_stream = inputs.error_stream
    repl_input = (
        terminal_ui
        if terminal_ui is not None
        else inputs.build_repl_input(
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
    # Terminal chrome owns footer composition and receives the live provider
    # state so runtime thinking-level changes remain visible.
    footer = _ChromeFooterEffects(
        cwd=cwd,
        coding_state=coding_state,
        provider_state=inputs.provider_state,
        error_stream=error_stream,
        terminal_ui=terminal_ui,
        repl_runtime=repl_input,
    )
    if terminal_ui is None:
        print_startup_chrome(
            error_stream,
            cwd=cwd,
            quiet=settings.get_quiet_startup() and not inputs.verbose_startup,
            include_workspace_defaults=settings.project_trusted,
        )
        if inputs.resume_context is not None:
            print(
                "pipy: "
                + compose_resume_status_line(
                    inputs.resume_context,
                    branch_label=inputs.resume_branch_label,
                ),
                file=error_stream,
            )
    else:
        terminal_ui.set_footer_text(footer.coding_footer_text())
        terminal_ui.start()
        if inputs.resume_context is not None:
            # Safe resumed-state notice committed to scrollback at startup:
            # prior session id, provider, model, turn count, finalized time
            # (and branch label) only — never prompts, output, or summary.
            terminal_ui.add_notice(
                compose_resume_status_line(
                    inputs.resume_context,
                    branch_label=inputs.resume_branch_label,
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
        is_fresh=inputs.resume_context is None,
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
    return _ChromePhase(repl_input=repl_input, footer=footer)


def _compose_collaborators(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    product: _ProductPhase,
    runtime: _RuntimePhase,
    chrome: _ChromePhase,
    provider_binding: _ProviderMutationBinding,
) -> _CollaboratorPhase:
    cwd = startup.cwd
    coding_state = startup.coding_state
    coding_effects = startup.coding_effects
    settings = startup.settings
    terminal_ui = extension.terminal_ui
    extension_ui_driver = extension.extension_ui_driver
    tool_capabilities = extension.tool_capabilities
    execution_projections = extension.execution_projections
    _extension_notify = extension.extension_notify
    ctl = product.ctl
    product_session = product.product_session
    coding_input_queue = runtime.coding_input_queue
    custom_renderer = runtime.custom_renderer
    footer = chrome.footer
    input_stream = inputs.input_stream
    error_stream = inputs.error_stream
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
        provider_state=inputs.provider_state,
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
        abort_event=inputs.abort_event,
        clipboard_copy=inputs.clipboard_copy,
        implicit_trust=inputs.implicit_trust,
        provider_state=inputs.provider_state,
        tool_registry=inputs.tool_registry,
        verbose_startup=inputs.verbose_startup,
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
    provider_binding.bind(provider_mutation)
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
    # EOF. Chrome re-emits it after each submission.
    if footer.legacy_footer_enabled():
        footer._print_footer(
            error_stream,
            cwd=cwd,
            provider_name=coding_state.provider_name,
            model_id=coding_state.model_id,
            user_turn_count=coding_state.user_turn_count,
            tool_invocation_count=coding_state.tool_invocation_count,
            usage_snapshot=coding_state.usage_snapshot(),
        )

    return _CollaboratorPhase(
        extension_operations=extension_operations,
        provider_mutation=provider_mutation,
        collaborators=collaborators,
        provider_request_policy=provider_request_policy,
        agent_tool_policy=agent_tool_policy,
    )


def _compose_commands(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    product: _ProductPhase,
    runtime: _RuntimePhase,
    chrome: _ChromePhase,
    collaborators_phase: _CollaboratorPhase,
) -> _CommandPhase:
    coding_state = startup.coding_state
    keybindings = startup.keybindings
    resource_options = startup.resource_options
    tool_capabilities = extension.tool_capabilities
    render_details = extension.render_details
    renderer = product.renderer
    prompt_history_store = product.prompt_history_store
    emitter = runtime.emitter
    repl_input = chrome.repl_input
    footer = chrome.footer
    collaborators = collaborators_phase.collaborators
    system_prompt = inputs.system_prompt
    input_stream = inputs.input_stream
    # Preserve built-in > resource > extension precedence while supplying the
    # four closed effect families to the interpreter.
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
    return _CommandPhase(command_effects=command_effects)


def _assemble_session_wiring(
    inputs: SessionWiringInput,
    startup: _StartupPhase,
    extension: _ExtensionPhase,
    product: _ProductPhase,
    runtime: _RuntimePhase,
    chrome: _ChromePhase,
    collaborators_phase: _CollaboratorPhase,
    commands: _CommandPhase,
) -> SessionWiring:
    cwd = startup.cwd
    coding_state = startup.coding_state
    coding_effects = startup.coding_effects
    settings = startup.settings
    terminal_ui = extension.terminal_ui
    execution_projections = extension.execution_projections
    provider_turn_executor = extension.provider_turn_executor
    extension_operations = collaborators_phase.extension_operations
    provider_mutation = collaborators_phase.provider_mutation
    collaborators = collaborators_phase.collaborators
    provider_request_policy = collaborators_phase.provider_request_policy
    agent_tool_policy = collaborators_phase.agent_tool_policy
    ctl = product.ctl
    renderer = product.renderer
    started_at = product.started_at
    image_reference_roots = product.image_reference_roots
    prompt_history_store = product.prompt_history_store
    append_agent_message = product.append_agent_message
    base_system_prompt = product.base_system_prompt
    emitter = runtime.emitter
    usage_publisher = runtime.usage_publisher
    input_queued_input_port = runtime.input_queued_input_port
    coding_input_queue = runtime.coding_input_queue
    loop_controller = runtime.loop_controller
    custom_renderer = runtime.custom_renderer
    repl_input = chrome.repl_input
    footer = chrome.footer
    command_effects = commands.command_effects
    error_stream = inputs.error_stream
    _extension_notify = extension.extension_notify
    repl_loop_step = _repl_loop_step._ReplLoopStep()
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
        file_reference_roots=inputs.reference_roots,
        abort_event=inputs.abort_event,
        provider_state=inputs.provider_state,
        tool_budget=inputs.tool_budget,
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
    delegation = _LoopDelegation(
        loop_controller=loop_controller,
        step_once=partial(repl_loop_step.step_once, scope=scope),
        finalize=partial(
            repl_loop_step.finalize,
            coding_state=coding_state,
            repl_input=repl_input,
            started_at=started_at,
        ),
        fire_session_start=partial(repl_loop_step.fire_session_start, emitter=emitter),
        fire_session_shutdown=partial(
            repl_loop_step.fire_session_shutdown, emitter=emitter
        ),
        consume_settle_pending=partial(repl_loop_step.consume_settle_pending, ctl=ctl),
        close_extension_session=partial(
            repl_loop_step.close_extension_session,
            coding_effects=coding_effects,
            generation_ref=ctl.generation_ref,
        ),
        clear_extension_chrome=partial(
            repl_loop_step.clear_extension_chrome, terminal_ui=terminal_ui
        ),
    )
    return SessionWiring(startup_failure=None, delegation=delegation)


def wire_session(inputs: SessionWiringInput) -> SessionWiring:
    """Compose one session in ordered immutable phases."""

    provider_binding = _ProviderMutationBinding()
    startup = _prepare_startup(inputs)
    if isinstance(startup, CodingSessionResult):
        return SessionWiring(startup_failure=startup, delegation=None)
    extension = _compose_extension_phase(inputs, startup, provider_binding)
    if isinstance(extension, CodingSessionResult):
        return SessionWiring(startup_failure=extension, delegation=None)
    try:
        product = _compose_product_session(inputs, startup, extension, provider_binding)
        runtime = _compose_runtime_adapters(inputs, startup, extension, product)
        chrome = _start_chrome(inputs, startup, extension, product, runtime)
        collaborators = _compose_collaborators(
            inputs, startup, extension, product, runtime, chrome, provider_binding
        )
        commands = _compose_commands(
            inputs, startup, extension, product, runtime, chrome, collaborators
        )
        return _assemble_session_wiring(
            inputs,
            startup,
            extension,
            product,
            runtime,
            chrome,
            collaborators,
            commands,
        )
    except BaseException as error:  # noqa: BLE001 - preserve startup primary
        raise_first((error, _abort_startup_attachment_nonraising(extension.attachment)))
        raise AssertionError("startup failure did not propagate")
