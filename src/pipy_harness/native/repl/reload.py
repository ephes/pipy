"""`/reload`: rebuild every derived projection without losing the conversation.

One command, and the most ordered thing in the product. It re-reads settings,
keybindings, resources and packages, activates a *candidate* extension
generation beside the live one, and only then swaps -- so a broken extension
set leaves the previous generation running instead of a half-reloaded session.

The publication gate is what makes that atomic. Between `publishing()` opening
and the accepted generation being installed, no turn may observe a partial
swap, and staged messages queued against the candidate are drained through a
reservation token rather than delivered as they arrive.

`ImplicitTrustState` is the one mutable thing here, and it is mutable on
purpose: Pi's no-resource-start trust exception fires at most once per run.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO, cast

import pipy_harness.native.agent.usage as _agent_usage
from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.chrome import print_startup_chrome
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandOutcome,
)
from pipy_harness.native.coding.state import CodingReloadHistoryValue
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_chrome_state import ExtensionChromeSink
from pipy_harness.native.extension_hooks import _activate_workspace_extensions
from pipy_harness.native.extensions.activation import (
    _ExtensionCandidate,
    _report_activation_cleanup,
)
from pipy_harness.native.extensions.flag_tokens import (
    parse_extension_flag_tokens,
)
from pipy_harness.native.extensions.tool_port import ToolRenderDetailsWriter
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.project_trust import (
    ProjectTrustError,
    ProjectTrustStore,
    has_trust_requiring_project_resources,
)
from pipy_harness.native.repl.command_menu import published_command_surface
from pipy_harness.native.repl.extension_attach import (
    AttachGenerationRefusal,
    ExtensionAttachInput,
    ReloadAttachPorts,
    ReloadGenerationAttachment,
    ReloadPresentation,
    attach_generation,
)
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.turn_leaves import pricing_for, raise_first
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
    UnavailableAfterReloadProvider,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import ReloadPreparationRefused
from pipy_harness.native.session_state_lock import SessionStateLock
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import NativeToolCapabilities
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.tui import (
    TerminalUi,
    _LiveExtensionUiDriver,
)
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer


@dataclass(slots=True)
class ImplicitTrustState:
    """Pi's narrowly guarded no-resource-start trust exception, one shot.

    A cwd that entered trusted state only because no protected resource existed
    at startup. If a later explicit ``/reload`` finds one has appeared, trust is
    persisted *once* and this clears. It is mutable and shared precisely because
    "once" is the whole contract: a fresh copy per reload would persist again.
    """

    cwd: Path | None = None


def maybe_save_implicit_trust_after_reload(
    implicit_trust: ImplicitTrustState,
    *,
    cwd: Path,
    settings: "SettingsManager",
    terminal_ui: TerminalUi | None,
    error_stream: TextIO,
) -> bool:
    """Persist Pi's narrowly guarded no-resource-start reload exception."""

    resolved = cwd.expanduser().resolve()
    if implicit_trust.cwd != resolved:
        return False
    if not settings.project_trusted or not has_trust_requiring_project_resources(
        resolved
    ):
        return False
    store = ProjectTrustStore()
    try:
        if store.get(resolved) is not None:
            implicit_trust.cwd = None
            return False
        store.set(resolved, True)
    except ProjectTrustError as exc:
        emit_diagnostic(
            terminal_ui.components.transcript if terminal_ui is not None else None,
            error_stream,
            f"pipy: could not save project trust after reload: {exc}",
        )
        return False
    implicit_trust.cwd = None
    return True


@dataclass(frozen=True, slots=True, kw_only=True)
class ReloadCommandEffects:
    """Execute ``/reload`` through explicit behavior-preserving phases."""

    implicit_trust: ImplicitTrustState
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    tool_registry: Mapping[str, ToolPort]
    verbose_startup: bool
    ctl: RunControlState
    settings: SettingsManager
    keybindings: KeybindingsManager
    terminal_ui: TerminalUi | None
    renderer: "_ToolLoopRenderer | TuiToolLoopRenderer"
    error_stream: TextIO
    emitter: _extension_hooks._ExtensionLifecycleAgentEventAdapter
    provider_mutation: ProviderMutationEffects
    cwd: Path
    resource_options: RuntimeResourceOptions
    tool_capabilities: NativeToolCapabilities
    diag: Callable[[str], None]
    redraw_custom_entries_for_active_branch: Callable[[], None]
    extension_send_message: Callable[
        [str, str, bool, "Mapping[str, object]", object | None], object
    ]
    extension_render_details: ToolRenderDetailsWriter
    extension_ui_driver: _LiveExtensionUiDriver | None = None

    def execute(self, command_outcome: CodingCommandOutcome) -> None:
        """Reload settings and publish every derived live projection in order."""

        if command_outcome.action is not CodingCommandAction.RELOAD:
            raise AssertionError("reload command executor received another action")
        # Open the publication gate before the first live selection, thinking,
        # or tool-visibility read. The optional holder adopts the composed
        # runtime immediately and disposes every rejected/exceptional candidate
        # only after the gate's session-mutex handoff has closed. Publication
        # empties it, so this cleanup can never dispose the live generation.
        candidate = _ExtensionCandidate()
        try:
            self._reload_configuration_and_resources()
            replacement_accepted = False
            try:
                replacement_accepted = self._reload_extensions(candidate)
            except ReloadPreparationRefused as error:
                self.diag(f"pipy: {error}")
                self.diag("pipy: keeping the previous extensions.")
            if not replacement_accepted:
                with self.ctl.generation_ref.publishing():
                    self.provider_mutation.refresh_provider_after_reload()
                    self._diagnose_unknown_tool_filters()
            saved_implicit_trust = self._refresh_presentation_and_persistence()
            self.diag(
                (
                    "pipy: reloaded settings, keybindings, and resources; "
                    "saved project trust."
                    if saved_implicit_trust
                    else "pipy: reloaded settings, keybindings, and resources."
                )
            )
        finally:
            _report_activation_cleanup(candidate.dispose(), self.diag)

    def _reload_configuration_and_resources(self) -> None:
        self.settings.reload()
        self.keybindings.reload()
        self.ctl.package_roots = compose_package_runtime(
            self.settings,
            self.cwd,
            include_package_themes=not self.resource_options.no_themes,
            explicit_theme_paths=self.resource_options.theme_paths,
        )
        self.ctl.workspace_resources = WorkspaceResources.discover(
            self.cwd,
            package_roots=self.ctl.package_roots,
            explicit_skill_paths=self.resource_options.skill_paths,
            explicit_prompt_template_paths=(
                self.resource_options.prompt_template_paths
            ),
            include_skills_defaults=not self.resource_options.no_skills,
            include_prompt_template_defaults=(
                not self.resource_options.no_prompt_templates
            ),
            include_workspace_defaults=self.settings.project_trusted,
        ).with_enablement(
            skills_patterns=self.settings.get_skills_patterns(),
            prompts_patterns=self.settings.get_prompts_patterns(),
            enable_skill_commands=self.settings.get_enable_skill_commands(),
        )

    def _reload_extensions(self, candidate: _ExtensionCandidate) -> bool:
        """Activate one candidate, then delegate its complete attachment."""

        chrome_candidate = (
            self.extension_ui_driver.new_candidate_sink()
            if self.extension_ui_driver is not None
            else None
        )
        try:
            runtime = _activate_workspace_extensions(
                self.cwd,
                self.ctl.workspace_resources,
                tuple(self.tool_registry.keys()),
                package_roots=(
                    ()
                    if self.resource_options.no_extensions
                    else self.ctl.package_roots.extensions
                ),
                extension_patterns=self.settings.get_extensions_patterns(),
                explicit_extension_paths=self.resource_options.extension_paths,
                include_default_extensions=not self.resource_options.no_extensions,
                include_workspace_defaults=self.settings.project_trusted,
                diagnostic=self.diag,
            )
            candidate.adopt(runtime, self.diag)
            flag_values, flag_error = parse_extension_flag_tokens(
                runtime.flags,
                tuple(self.resource_options.extension_flag_tokens),
            )
        except BaseException as error:  # noqa: BLE001 - chrome closes on interrupts
            activation_chrome_error: BaseException | None = None
            try:
                if chrome_candidate is not None:
                    chrome_candidate.close()
            except BaseException as cleanup_error:  # noqa: BLE001 - preserve primary
                activation_chrome_error = cleanup_error
            raise_first((error, activation_chrome_error))
        if flag_error is not None:
            diagnostic_error: BaseException | None = None
            chrome_error: BaseException | None = None
            try:
                self.diag(f"pipy: {flag_error}")
                self.diag("pipy: keeping the previous extensions.")
            except BaseException as error:  # noqa: BLE001 - chrome must still close
                diagnostic_error = error
            try:
                if chrome_candidate is not None:
                    chrome_candidate.close()
            except BaseException as error:  # noqa: BLE001 - preserve diagnostic failure
                chrome_error = error
            raise_first((diagnostic_error, chrome_error))
            return False
        provider_state = (
            self.provider_state
            if isinstance(self.provider_state, NativeReplProviderState)
            else None
        )
        coding = self.provider_mutation.coding_state
        predecessor = self.ctl.generation_ref
        result = attach_generation(
            ExtensionAttachInput(
                candidate=candidate,
                runtime=runtime,
                flag_values=flag_values,
                state_lock=cast(SessionStateLock, predecessor.lock),
                has_ui=self.terminal_ui is not None,
                notify_sink=self.provider_mutation.extension_notify,
                set_active_tools=self.provider_mutation.extension_set_active_tools,
                render_details=self.extension_render_details,
                project_trusted=self.settings.project_trusted,
                tool_capabilities=self.tool_capabilities,
                chrome_sink=chrome_candidate,
            ),
            predecessor,
            reload_ports=ReloadAttachPorts(
                provider_state=provider_state,
                coding_state=coding,
                unavailable_provider=lambda message: UnavailableAfterReloadProvider(
                    coding.provider_name,
                    coding.model_id,
                    message,
                ),
                usage_prototype=lambda item: _agent_usage.AgentUsageAccumulator(
                    pricing_for(item.provider_name, item.model_id)
                ),
                empty_history=CodingReloadHistoryValue(()),
                candidate_session_start=partial(
                    self._fire_candidate_session_start,
                    runtime.lifecycle_hooks,
                    flag_values,
                    chrome_candidate,
                ),
                chrome=self.extension_ui_driver,
                custom_sink=partial(
                    _extension_hooks.deliver_staged_custom,
                    self.extension_send_message,
                ),
                diagnostic=self.diag,
                report_presentation=self._report_reload_presentation,
            ),
        )
        if isinstance(result, AttachGenerationRefusal):
            return False
        assert isinstance(result, ReloadGenerationAttachment)
        return True

    def _fire_candidate_session_start(
        self,
        lifecycle_hooks: Mapping[str, tuple[Callable[..., object], ...]],
        flag_values: Mapping[str, object],
        chrome_candidate: ExtensionChromeSink | None,
    ) -> None:
        self.emitter.fire_candidate_session_start(
            lifecycle_hooks,
            flag_values,
            ui_driver=(
                self.extension_ui_driver.candidate_driver(chrome_candidate)
                if self.extension_ui_driver is not None and chrome_candidate is not None
                else None
            ),
        )

    def _report_reload_presentation(self, presentation: ReloadPresentation) -> None:
        """Report accepted reload changes after the publication gate closes."""

        if presentation.diagnostic is not None:
            self.diag(presentation.diagnostic)
        if presentation.persist_default and presentation.provider_state is not None:
            default_error = presentation.provider_state.flush_pending_default()
            if default_error is not None:
                self.diag(default_error)
        self._diagnose_unknown_tool_filters()

    def _diagnose_unknown_tool_filters(self) -> None:
        unknown_filter_names = self.tool_capabilities.unknown_filter_names
        if not unknown_filter_names:
            return
        known = ", ".join(sorted(self.tool_capabilities.registered_names)) or "<none>"
        unknown = ", ".join(unknown_filter_names)
        self.diag(f"pipy: unknown tool name(s): {unknown}. Known tools: {known}")

    def _refresh_presentation_and_persistence(self) -> bool:
        reloaded_theme = self.settings.get_theme()
        if reloaded_theme:
            os.environ["PIPY_THEME"] = reloaded_theme
        if self.terminal_ui is not None:
            snapshot = self.ctl.generation_ref.snapshot()
            projection = snapshot.generation.projection
            if projection is None:
                raise RuntimeError("published extension generation has no projection")
            commands = projection.commands
            self.terminal_ui.components.autocomplete.set_max_visible(
                self.settings.get_autocomplete_max_visible()
            )
            self.terminal_ui.components.autocomplete.replace_command_surface(
                published_command_surface(self.ctl.workspace_resources, commands)
            )
            self.redraw_custom_entries_for_active_branch()
        for scope, detail in self.settings.load_errors().items():
            self.diag(f"pipy: kept prior {scope} settings ({detail}).")
        if self.verbose_startup or not self.settings.get_quiet_startup():
            print_startup_chrome(
                self.error_stream,
                cwd=self.cwd,
                include_workspace_defaults=self.settings.project_trusted,
            )
        return maybe_save_implicit_trust_after_reload(
            self.implicit_trust,
            cwd=self.cwd,
            settings=self.settings,
            terminal_ui=self.terminal_ui,
            error_stream=self.error_stream,
        )
