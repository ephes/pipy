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
from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeCommitToken,
    ExtensionChromePrepareInput,
    ExtensionChromeRetirement,
    ExtensionChromeSink,
)
from pipy_harness.native.extension_hooks import _activate_workspace_extensions
from pipy_harness.native.extensions.activation import (
    _ExtensionCandidate,
    _report_activation_cleanup,
)
from pipy_harness.native.extensions.contracts import (
    _ExtensionRuntime,
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
from pipy_harness.native.repl.execution_projections import (
    build_candidate_extension_projection,
)
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl.turn_leaves import (
    finish_chrome_retirement,
    pricing_for,
    raise_first,
)
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
    UnavailableAfterReloadProvider,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    ExtensionProjection,
    FrozenStagedDeliveryBatch,
    OrderedDeliveryGate,
    PreparedReloadEffects,
    ReloadPreparationRefused,
    SessionExtensionGeneration,
    prepare_production_reload,
    with_tool_capability,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolCapabilityState,
)
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.tui import (
    ToolLoopTerminalUi,
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


@dataclass(slots=True)
class _ReloadAttempt:
    """What one reload attempt produced that its teardown has to dispose.

    The teardown runs whether the attempt published or refused, and it reads
    state created at three different depths of the attempt. Carrying that on
    one record keeps the phases from having to hand partial results back up
    through returns that only the ``finally`` clause would read.
    """

    projection: ExtensionProjection | None = None
    prepared: PreparedReloadEffects | None = None
    chrome_retirement: ExtensionChromeRetirement | None = None
    published: bool = False

    def require_projection(self) -> ExtensionProjection:
        if self.projection is None:
            raise RuntimeError("reload attempt has not staged a projection")
        return self.projection


def maybe_save_implicit_trust_after_reload(
    implicit_trust: ImplicitTrustState,
    *,
    cwd: Path,
    settings: "SettingsManager",
    terminal_ui: ToolLoopTerminalUi | None,
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
            terminal_ui,
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
    terminal_ui: ToolLoopTerminalUi | None
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
                replacement_accepted, _ = self._reload_extension_generation(candidate)
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

    def _finish_candidate_chrome(
        self,
        chrome_candidate: ExtensionChromeSink | None,
        *,
        replacement_accepted: bool,
        chrome_retirement: ExtensionChromeRetirement | None = None,
    ) -> str | None:
        if not replacement_accepted:
            return None
        if self.extension_ui_driver is None or chrome_candidate is None:
            return None
        owned = True
        try:
            acceptance = self.extension_ui_driver.accept_candidate(
                chrome_candidate,
                rollback_snapshot=(
                    chrome_retirement.snapshot
                    if chrome_retirement is not None
                    else None
                ),
            )
            if not acceptance.accepted:
                if not acceptance.candidate_closed:
                    chrome_candidate.close()
                owned = False
                return acceptance.diagnostic
            # Ownership transferred before retired cleanup. Interrupts from a
            # retired disposer propagate without closing the now-live candidate.
            owned = False
            cleanup_diagnostic = (
                self.extension_ui_driver.dispose_retired_sink(acceptance.retired_sink)
                if acceptance.retired_sink is not None
                else None
            )
            return cleanup_diagnostic or acceptance.diagnostic
        finally:
            if owned and not self.extension_ui_driver.owns_sink(chrome_candidate):
                chrome_candidate.close()

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

    def _reload_extension_generation(
        self, candidate: _ExtensionCandidate
    ) -> tuple[bool, ExtensionChromeSink | None]:
        """Publish one reloaded generation, or keep the previous one intact.

        Three phases behind one teardown that must run whatever the outcome.
        The teardown reads state produced at three different depths, so those
        four values sit on :class:`_ReloadAttempt` instead of being locals the
        phases would have to hand back to each other.
        """

        chrome_candidate = (
            self.extension_ui_driver.new_candidate_sink()
            if self.extension_ui_driver is not None
            else None
        )
        attempt = _ReloadAttempt()
        try:
            activated = self._activate_reload_candidate(
                candidate, chrome_candidate, attempt
            )
            if activated is None:
                return False, None
            reloaded_extension_bundle, gate = activated
            committed = self._commit_reload_generation(
                candidate, chrome_candidate, attempt, reloaded_extension_bundle, gate
            )
            if committed is None:
                return False, None
            prepared, provider_state = committed
            self._report_reload_presentation(prepared, provider_state)
            return True, chrome_candidate
        finally:
            self._retire_reload_attempt(chrome_candidate, attempt)

    def _activate_reload_candidate(
        self,
        candidate: _ExtensionCandidate,
        chrome_candidate: ExtensionChromeSink | None,
        attempt: "_ReloadAttempt",
    ) -> tuple[_ExtensionRuntime, OrderedDeliveryGate] | None:
        """Activate the workspace extensions and stage a candidate projection."""

        reloaded_extension_bundle = _activate_workspace_extensions(
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
        candidate.adopt(reloaded_extension_bundle, self.diag)
        reloaded_flag_values, reloaded_flag_error = parse_extension_flag_tokens(
            reloaded_extension_bundle.flags,
            tuple(self.resource_options.extension_flag_tokens),
        )
        if reloaded_flag_error is not None:
            self.diag(f"pipy: {reloaded_flag_error}")
            self.diag("pipy: keeping the previous extensions.")
            return None
        projection = build_candidate_extension_projection(
            reloaded_extension_bundle,
            reloaded_flag_values,
            queue_mutex=self.ctl.generation_ref.lock,
            reference_mutex=self.ctl.generation_ref.lock,
            has_ui=self.terminal_ui is not None,
            notify_sink=self.provider_mutation.extension_notify,
            set_active_tools=self.provider_mutation.extension_set_active_tools,
            render_details=self.extension_render_details,
            project_trusted=self.settings.project_trusted,
            prepare_capability=self.tool_capabilities.prepare_extensions,
            chrome=(
                ExtensionChromeHandle(chrome_candidate)
                if chrome_candidate is not None
                else None
            ),
        )
        attempt.projection = projection
        gate = OrderedDeliveryGate(self.ctl.generation_ref.lock)
        projection.queues.install_candidate_route(gate)
        self.emitter.fire_candidate_session_start(
            reloaded_extension_bundle.lifecycle_hooks,
            reloaded_flag_values,
            ui_driver=(
                self.extension_ui_driver.candidate_driver(chrome_candidate)
                if self.extension_ui_driver is not None and chrome_candidate is not None
                else None
            ),
        )
        return reloaded_extension_bundle, gate

    def _commit_reload_generation(
        self,
        candidate: _ExtensionCandidate,
        chrome_candidate: ExtensionChromeSink | None,
        attempt: "_ReloadAttempt",
        reloaded_extension_bundle: _ExtensionRuntime,
        gate: OrderedDeliveryGate,
    ) -> tuple[PreparedReloadEffects, NativeReplProviderState | None] | None:
        """Accept the staged generation under the publication gate, or refuse."""

        with self.ctl.generation_ref.publishing():
            with self.ctl.generation_ref.lock:
                expected_capability = self.tool_capabilities._state
            projection = attempt.require_projection()
            capability = self.tool_capabilities.prepare_extensions(
                projection.tools.ports
            )
            projection = with_tool_capability(projection, capability)
            attempt.projection = projection
            provider_state = self.provider_state
            if not isinstance(provider_state, NativeReplProviderState):
                provider_state = None
            coding = self.provider_mutation.coding_state
            chrome_sink = chrome_candidate or ExtensionChromeSink()
            prepared = prepare_production_reload(
                reloaded_extension_bundle,
                projection,
                ExtensionChromePrepareInput(chrome_sink),
                state=provider_state,
                coding=coding,
                lock=self.ctl.generation_ref.lock,
                unavailable_provider=lambda message: UnavailableAfterReloadProvider(
                    coding.provider_name,
                    coding.model_id,
                    message,
                ),
                usage_prototype=lambda item: _agent_usage.AgentUsageAccumulator(
                    pricing_for(item.provider_name, item.model_id)
                ),
                empty_history=CodingReloadHistoryValue(()),
                capability=capability,
            )
            attempt.prepared = prepared
            if chrome_candidate is None:
                chrome_sink.close()
            chrome_input = prepared.chrome_prepare_input.value
            if (driver := self.extension_ui_driver) is not None:
                chrome_token = driver.prepare_candidate(chrome_input)
            else:
                chrome_token = ExtensionChromeCommitToken(chrome_input)
            if chrome_token is None:
                self.diag("pipy: extension chrome candidate is unavailable")
                self.diag("pipy: keeping the previous extensions.")
                return None
            generation = SessionExtensionGeneration(
                reloaded_extension_bundle,
                projection,
                chrome_token,
            )
            if not self._accept_prepared_generation(
                candidate,
                attempt,
                generation,
                prepared,
                gate,
                provider_state=provider_state,
                expected_capability=expected_capability,
            ):
                return None
        return prepared, provider_state

    def _accept_prepared_generation(
        self,
        candidate: _ExtensionCandidate,
        attempt: "_ReloadAttempt",
        generation: SessionExtensionGeneration,
        prepared: PreparedReloadEffects,
        gate: OrderedDeliveryGate,
        *,
        provider_state: NativeReplProviderState | None,
        expected_capability: ToolCapabilityState,
    ) -> bool:
        """Reserve delivery, swap the live generation, and drain the staged batch."""

        projection = attempt.require_projection()
        with gate.reserve() as token:
            acceptance_failure, retired_chrome = (
                self.ctl.generation_ref.accept_prepared_reload(
                    generation,
                    prepared,
                    candidate=candidate,
                    provider_state=provider_state,
                    coding_state=self.provider_mutation.coding_state,
                    tool_capabilities=self.tool_capabilities,
                    expected_capability=expected_capability,
                )
            )
            if acceptance_failure is not None:
                self.diag(f"pipy: {acceptance_failure}")
                self.diag("pipy: keeping the previous extensions.")
                return False
            attempt.published = True
            attempt.chrome_retirement, chrome_close_error = (
                retired_chrome.close_nonraising() if retired_chrome else (None, None)
            )
            delivery_error: BaseException | None = None
            try:
                _extension_hooks.deliver_accepted_staged_batch(
                    cast(
                        FrozenStagedDeliveryBatch,
                        prepared.activation_inputs.value[0],
                    ),
                    gate=gate,
                    token=token,
                    user_sink=lambda _message: None,
                    custom_sink=partial(
                        _extension_hooks.deliver_staged_custom,
                        self.extension_send_message,
                    ),
                    release_route=projection.queues.release_pending_route,
                )
            except BaseException as error:  # noqa: BLE001 - collected; chrome retirement still runs
                delivery_error = error
            cleanup_error = finish_chrome_retirement(attempt.chrome_retirement)
            raise_first((delivery_error, cleanup_error, chrome_close_error))
        return True

    def _report_reload_presentation(
        self,
        prepared: PreparedReloadEffects,
        provider_state: NativeReplProviderState | None,
    ) -> None:
        """Report what the accepted reload changed, once the gate has closed."""

        diagnostic, persist_default = prepared.presentation_persistence.value
        if diagnostic is not None:
            self.diag(cast(str, diagnostic))
        if persist_default and provider_state is not None:
            default_error = provider_state.flush_pending_default()
            if default_error is not None:
                self.diag(default_error)
        self._diagnose_unknown_tool_filters()

    def _retire_reload_attempt(
        self,
        chrome_candidate: ExtensionChromeSink | None,
        attempt: "_ReloadAttempt",
    ) -> None:
        """Dispose the attempt's chrome and routes, whether or not it published."""

        try:
            if attempt.published:
                if (
                    chrome_diagnostic := self._finish_candidate_chrome(
                        chrome_candidate,
                        replacement_accepted=True,
                        chrome_retirement=attempt.chrome_retirement,
                    )
                ) is not None:
                    self.diag(chrome_diagnostic)
            else:
                if attempt.projection is not None:
                    attempt.projection.queues.retire_route()
                if chrome_candidate is not None:
                    chrome_candidate.close()
        finally:
            if attempt.prepared is not None:
                attempt.prepared.dispose()

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
            self.terminal_ui.autocomplete.set_max_visible(
                self.settings.get_autocomplete_max_visible()
            )
            self.terminal_ui.autocomplete.replace_command_surface(
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
