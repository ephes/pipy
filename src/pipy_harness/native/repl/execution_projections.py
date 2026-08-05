"""Turning a live extension generation into the frozen views one turn reads.

A turn must not observe an extension reload happening underneath it. Everything
here takes the current generation and produces an immutable projection -- tool
ports, the candidate's contributions, the startup provider set -- so the turn
runs against one coherent snapshot rather than whatever the runtime holds at each
individual read.

Pure builders: they read a generation and return values. Nothing here mutates
the session or the runtime.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from pipy_harness.native.agent import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolInterruptWaiter
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.extension_types import (
    ExtensionTool,
    ExtensionUiDriver,
    RegisteredTool,
)
from pipy_harness.native.extensions.contracts import (
    HookHandler,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.tool_port import (
    ToolRenderDetailsWriter,
    _ExtensionToolPort,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.turn_leaves import pricing_for
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    ExtensionProjection,
    SessionGenerationRef,
    build_extension_projection,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    NativeToolCapabilitySnapshot,
    ToolCapabilityState,
)
from pipy_harness.native.tools import ToolDefinition, ToolPort
from pipy_harness.native.tui import _LiveExtensionUiDriver


@dataclass(frozen=True, slots=True)
class ExecutionProjectionSnapshot:
    generation_id: int
    tools: NativeToolCapabilitySnapshot
    renderers: Mapping[str, ExtensionTool]
    tool_call_hooks: tuple[HookHandler, ...]
    flags: Mapping[str, object]
    ui_driver: ExtensionUiDriver | None
    provider: ProviderPort


class SessionExecutionProjections:
    """Bind one provider turn to one published tool/renderer/provider view."""

    __slots__ = (
        "_active",
        "_coding_state",
        "_generation_ref",
        "_tools",
        "_ui_driver",
    )

    def __init__(
        self,
        *,
        generation_ref: SessionGenerationRef,
        tool_capabilities: NativeToolCapabilities,
        coding_state: CodingSessionState,
        ui_driver: _LiveExtensionUiDriver | None,
    ) -> None:
        self._generation_ref = generation_ref
        self._tools = tool_capabilities
        self._coding_state = coding_state
        self._ui_driver = ui_driver
        self._active: ExecutionProjectionSnapshot | None = None

    def _begin_provider_turn(self) -> ExecutionProjectionSnapshot:
        with self._generation_ref.lock:
            snapshot = self._generation_ref.snapshot()
            projection = snapshot.generation.projection
            if projection is None:
                raise RuntimeError("published extension generation has no projection")
            chrome = projection.chrome
            ui_driver: ExtensionUiDriver | None = self._ui_driver
            if chrome is not None and self._ui_driver is not None:
                ui_driver = self._ui_driver.generation_driver(chrome.sink)
            active = ExecutionProjectionSnapshot(
                generation_id=snapshot.generation_id,
                tools=self._tools.snapshot_for_projection(
                    projection.tools.capability_state
                ),
                renderers=projection.renderers.tools,
                tool_call_hooks=projection.hooks.tool_call,
                flags=projection.runtime_flags.values,
                ui_driver=ui_driver,
                provider=self._coding_state.provider,
            )
            self._active = active
            return active

    def _require_active(self) -> ExecutionProjectionSnapshot:
        if self._active is None:
            raise RuntimeError("tool advertisement has not started a provider turn")
        return self._active

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        return self._begin_provider_turn().tools.definitions(allowed_names)

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        active = self._require_active()
        return active.tools.execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
            extension_generation_id=active.generation_id,
        )

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        return self._require_active().tools.error_result(call, output_text)

    def tool_renderers(
        self, advertised_names: Sequence[str]
    ) -> Mapping[str, ExtensionTool]:
        renderers = self._require_active().renderers
        return {name: renderers[name] for name in advertised_names if name in renderers}

    @property
    def provider(self) -> ProviderPort:
        return self._require_active().provider

    def tool_call_policy_inputs(
        self,
    ) -> tuple[
        int,
        tuple[HookHandler, ...],
        Mapping[str, object],
        ExtensionUiDriver | None,
    ]:
        active = self._require_active()
        return (
            active.generation_id,
            active.tool_call_hooks,
            active.flags,
            active.ui_driver,
        )


def build_projected_extension_tool_port(
    registered: RegisteredTool,
    *,
    has_ui: bool,
    notify_sink: Callable[[str, str], None],
    set_active_tools: Callable[[int, Sequence[str]], bool],
    flags: Mapping[str, object],
    render_details: ToolRenderDetailsWriter,
    project_trusted: bool,
) -> ToolPort:
    """Construct one detached projection port without touching live state."""

    return _ExtensionToolPort(
        registered,
        has_ui=has_ui,
        notify_sink=notify_sink,
        set_active_tools_fn=set_active_tools,
        flags=flags,
        render_details_sink=render_details,
        project_trusted=project_trusted,
    )


def build_candidate_extension_projection(
    runtime: _ExtensionRuntime,
    flag_values: Mapping[str, object],
    *,
    queue_mutex: threading.RLock,
    reference_mutex: threading.RLock,
    has_ui: bool,
    notify_sink: Callable[[str, str], None],
    set_active_tools: Callable[[int, Sequence[str]], bool],
    render_details: ToolRenderDetailsWriter,
    project_trusted: bool,
    prepare_capability: Callable[[Mapping[str, ToolPort]], ToolCapabilityState],
    chrome: ExtensionChromeHandle | None,
) -> ExtensionProjection:
    """Purely compose one detached candidate projection for publication."""

    def build_tool_port(
        registered: RegisteredTool, frozen_flags: Mapping[str, object]
    ) -> ToolPort:
        return build_projected_extension_tool_port(
            registered,
            has_ui=has_ui,
            notify_sink=notify_sink,
            set_active_tools=set_active_tools,
            flags=frozen_flags,
            render_details=render_details,
            project_trusted=project_trusted,
        )

    return build_extension_projection(
        runtime,
        flag_values,
        queue_mutex=queue_mutex,
        reference_mutex=reference_mutex,
        build_tool_port=build_tool_port,
        build_tool_capability=prepare_capability,
        chrome=chrome,
    )


def apply_startup_provider_projection(
    *,
    generation_ref: SessionGenerationRef,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    coding_state: CodingSessionState,
    error_stream: TextIO,
) -> None:
    """Apply one published startup provider projection and any required fallback."""

    snapshot = generation_ref.snapshot()
    projection = snapshot.generation.projection
    if projection is None:
        raise RuntimeError("published extension generation has no projection")
    match provider_state:
        case NativeReplProviderState():
            catalog_state = provider_state.model_runtime.catalog
            was_extension_selection = (
                provider_state.current_selection_uses_extension_provider()
            )
            providers = projection.providers
            catalog_state.set_extension_provider_contributions(
                providers.providers,
                providers.unregistered,
            )
            if not provider_state.current_selection_supported() or (
                was_extension_selection
                and not provider_state.current_selection_uses_extension_provider()
            ):
                fallback = provider_state.reset_to_first_available_model(
                    require_tool_calls=True
                )
                if fallback is None:
                    raise ValueError(
                        "selected provider is unavailable after extension activation, "
                        "and no available tool-capable fallback was found"
                    )
                fallback_provider = provider_state.current_provider()
                coding_state.rebind_provider(
                    fallback_provider,
                    provider_name=fallback.provider_name,
                    model_id=fallback.model_id,
                    usage_accumulator=AgentUsageAccumulator(
                        pricing_for(fallback.provider_name, fallback.model_id)
                    ),
                )
                print(
                    "pipy: active model disappeared on startup; selected "
                    f"{fallback.reference}.",
                    file=error_stream,
                )
                # Post-commit: the fallback is bound, so persist it and report a
                # failure without claiming the binding reverted.
                startup_persistence_error = provider_state.flush_pending_default()
                if startup_persistence_error is not None:
                    print(startup_persistence_error, file=error_stream)
