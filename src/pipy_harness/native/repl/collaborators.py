"""One run's collaborators, assembled once for the command families.

`SessionCollaborators` is the factory the composition root hands to a run: it
holds the narrow ports each command family needs and builds those families on
demand, so the families themselves never see the session object -- they see the
handful of values they actually use. Closed built-in fan-out lives in
``native.repl.command_router``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from pipy_harness.models import HarnessStatus
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent import (
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    ProductContent,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderRequestPolicyInput,
    AgentToolPolicyDecision,
)
from pipy_harness.native.agent.provider_turn import _AbortCallbackSignal
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.commands import (
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
)
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.product_session import CodingProductSessionCoordinator
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_hooks import dispatch_tool_call_hooks
from pipy_harness.native.extension_types import ExtensionCodingSessionControl
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.tool_port import ToolRenderDetailsWriter
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.repl.execution_projections import SessionExecutionProjections
from pipy_harness.native.repl.extension_operations import (
    SessionExtensionOperations,
    SessionHookFamily,
)
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_config_commands import (
    ProviderConfigurationCommandEffects,
)
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.reload import ImplicitTrustState, ReloadCommandEffects
from pipy_harness.native.repl.session_commands import SessionCommandEffects
from pipy_harness.native.repl.session_transfer import TransferCommandEffects
from pipy_harness.native.repl_input import NativeReplInput
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import DISPATCH_LIST, dispatch_resource_command
from pipy_harness.native.session_tree import default_native_session_dir
from pipy_harness.native.session_tree_commands import resolve_session_target
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import NativeToolCapabilities
from pipy_harness.native.tool_renderers import _parse_tool_input, _ToolLoopRenderer
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.tui import (
    TerminalUi,
    _LiveExtensionUiDriver,
)
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryRenderer,
)
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer

_EXTENSION_COMPLETE_MAX_CHARS = 100 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionCollaborators:
    """Composition-root handler owning the residual run-loop collaborators.

    Symmetric with :class:`ProviderMutationEffects`/:class:`CustomEntryRenderer`/
    :class:`_ReplLoopStep`/:class:`_BuiltinCommandInterpreter`, these bodies
    formerly lived as the ``diag``/``extension_session_allows``/
    ``rebuild_messages_from_tree``/``summarize_branch``/``current_session_dir``/
    ``resolve_session_file``/session-name-setter/``_extension_complete``/
    ``_extension_custom_driver``/provider-request/tool-policy-hook/
    ``_dispatch_resource_effect``/``_dispatch_extension_effect`` closures nested in
    ``CodingSession.run()``. They reach one another densely (extension
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

    abort_event: threading.Event | _AbortCallbackSignal | None
    clipboard_copy: Callable[..., ClipboardResult]
    implicit_trust: ImplicitTrustState
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    tool_registry: Mapping[str, ToolPort]
    verbose_startup: bool
    ctl: RunControlState
    extension_operations: SessionExtensionOperations
    execution_projections: SessionExecutionProjections
    coding_state: CodingSessionState
    product_session: CodingProductSessionCoordinator
    coding_input_queue: CodingInputQueue
    coding_effects: CodingEffectCoordinator
    terminal_ui: TerminalUi | None
    settings: SettingsManager
    cwd: Path
    error_stream: TextIO
    provider_mutation: ProviderMutationEffects
    custom_renderer: CustomEntryRenderer
    extension_ui_driver: _LiveExtensionUiDriver | None
    extension_notify: Callable[[str, str], None]

    def diag(self, message: str) -> None:
        emit_diagnostic(self.terminal_ui, self.error_stream, message)

    def extension_set_session_name(self, name: str | None) -> object:
        with self.coding_effects.effect() as admitted:
            if not admitted:
                raise ExtensionCapabilityError("coding session is closed")
            with self.ctl.session_tree_section() as tree:
                return tree.append_session_info(name)

    def extension_get_session_name(self) -> str | None:
        with self.ctl.session_tree_section() as tree:
            return tree.name

    def extension_set_label(self, entry_id: str, label: str | None) -> object:
        with self.coding_effects.effect() as admitted:
            if not admitted:
                raise ExtensionCapabilityError("coding session is closed")
            with self.ctl.session_tree_section() as tree:
                return tree.append_label_change(entry_id, label)

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

    def session_command_effects(
        self, repl_input: "TerminalUi | NativeReplInput"
    ) -> SessionCommandEffects:
        """Assemble the session-command executor from this run's narrow ports."""

        return SessionCommandEffects(
            ctl=self.ctl,
            cwd=self.cwd,
            terminal_ui=self.terminal_ui,
            error_stream=self.error_stream,
            repl_input=repl_input,
            diag=self.diag,
            apply_compaction=self.provider_mutation.apply_compaction,
            extension_session_allows=self.extension_session_allows,
            rebuild_messages_from_tree=self.rebuild_messages_from_tree,
            redraw_custom_entries_for_active_branch=self.custom_renderer.redraw_custom_entries_for_active_branch,
            current_session_dir=self.current_session_dir,
            resolve_session_file=self.resolve_session_file,
            summarize_branch=self.summarize_branch,
        )

    def provider_configuration_command_effects(
        self,
        *,
        keybindings: KeybindingsManager,
        prompt_history_store: PromptHistoryStore,
    ) -> ProviderConfigurationCommandEffects:
        """Assemble the provider/configuration executor from narrow owners."""

        return ProviderConfigurationCommandEffects(
            provider_state=self.provider_state,
            clipboard_copy=self.clipboard_copy,
            ctl=self.ctl,
            coding_state=self.coding_state,
            terminal_ui=self.terminal_ui,
            error_stream=self.error_stream,
            keybindings=keybindings,
            settings=self.settings,
            cwd=self.cwd,
            prompt_history_store=prompt_history_store,
            provider_mutation=self.provider_mutation,
        )

    def transfer_command_effects(
        self,
        *,
        system_prompt: str,
        input_stream: TextIO,
    ) -> TransferCommandEffects:
        """Assemble native session transfer effects from narrow live ports."""

        return TransferCommandEffects(
            abort_event=self.abort_event,
            ctl=self.ctl,
            cwd=self.cwd,
            system_prompt=system_prompt,
            input_stream=input_stream,
            error_stream=self.error_stream,
            terminal_ui=self.terminal_ui,
            diag=self.diag,
            current_session_dir=self.current_session_dir,
            session_switch_allows=self.transfer_session_switch_allows,
            rebuild_messages_from_tree=self.rebuild_messages_from_tree,
        )

    def transfer_session_switch_allows(self, target: str) -> bool:
        """Apply the extension session-switch gate for an import source."""

        return self.extension_session_allows(
            "switch", operation="switch", target=target
        )

    def reload_command_effects(
        self,
        *,
        keybindings: KeybindingsManager,
        renderer: "_ToolLoopRenderer | TuiToolLoopRenderer",
        emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter,
        resource_options: RuntimeResourceOptions,
        tool_capabilities: NativeToolCapabilities,
        extension_render_details: ToolRenderDetailsWriter,
    ) -> ReloadCommandEffects:
        """Assemble the phased reload executor from authoritative owners."""

        return ReloadCommandEffects(
            implicit_trust=self.implicit_trust,
            provider_state=self.provider_state,
            tool_registry=self.tool_registry,
            verbose_startup=self.verbose_startup,
            ctl=self.ctl,
            settings=self.settings,
            keybindings=keybindings,
            terminal_ui=self.terminal_ui,
            extension_ui_driver=self.extension_ui_driver,
            renderer=renderer,
            error_stream=self.error_stream,
            emitter=emitter,
            provider_mutation=self.provider_mutation,
            cwd=self.cwd,
            resource_options=resource_options,
            tool_capabilities=tool_capabilities,
            diag=self.diag,
            redraw_custom_entries_for_active_branch=self.custom_renderer.redraw_custom_entries_for_active_branch,
            extension_send_message=self.custom_renderer.extension_send_message,
            extension_render_details=extension_render_details,
        )

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
        family: SessionHookFamily,
        *,
        operation: str,
        target: str | None = None,
        trigger: str | None = None,
    ) -> bool:
        decision = self.extension_operations.session_allows(
            family,
            operation=operation,
            target=target,
            trigger=trigger,
        )
        if decision.allow:
            return True
        reason = decision.reason or "blocked by extension"
        self.diag(f"pipy: {operation} blocked by extension: {reason}")
        return False

    def extension_complete(self, system_prompt: str, user_text: str) -> str:
        with self.coding_effects.effect() as admitted:
            if not admitted:
                raise ExtensionCapabilityError("coding session is closed")
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

    def active_provider_header_callback(
        self,
    ) -> Callable[[MutableMapping[str, str | None]], None] | None:
        return self.extension_operations.provider_header_callback(self.ctl.session_tree)

    def prepare_agent_provider_request(
        self, policy_input: AgentProviderRequestPolicyInput
    ) -> AgentProviderRequestSnapshot:
        return self.extension_operations.prepare_provider_request(policy_input)

    def apply_extension_tool_policy(
        self, call: AgentToolCall
    ) -> AgentToolPolicyDecision:
        generation_id, hooks, flags, ui_driver = (
            self.execution_projections.tool_call_policy_inputs()
        )
        tool_block = dispatch_tool_call_hooks(
            hooks,
            tool_name=call.tool_name,
            tool_input=_parse_tool_input(call.arguments_json.value),
            cwd=str(self.cwd),
            has_ui=self.terminal_ui is not None,
            notify_sink=self.extension_notify,
            ui_driver=ui_driver,
            model_runtime=self.provider_mutation.model_runtime_control(
                generation_id, allow_model=False
            ),
            flags=flags,
            project_trusted=self.settings.project_trusted,
        )
        if tool_block is None:
            return AgentToolPolicyDecision()
        return AgentToolPolicyDecision(ProductContent(tool_block.reason))

    def transform_extension_tool_result(
        self, call: AgentToolCall, result: AgentToolResultMessage
    ) -> ProductContent:
        return self.extension_operations.transform_tool_result(
            tool_name=call.tool_name,
            content=result.content,
            is_error=result.is_error,
        )

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
        extension_dispatch = self.extension_operations.dispatch_command(
            command_text,
            coding_session=self.coding_session_control(),
            ui_custom_driver=self.extension_custom_driver,
        )
        if extension_dispatch is None:
            return None
        return ExtensionDispatchResolution(
            name=extension_dispatch.name,
            ran=extension_dispatch.ran,
            error=extension_dispatch.error,
        )
