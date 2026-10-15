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
from typing import Any, TextIO

from pipy_harness.native.agent import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolInterruptWaiter
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.extension_types import (
    ControlGetAllToolsFn,
    ExtensionTool,
    ExtensionUiDriver,
    RegisteredTool,
    ToolRendererResolver,
)
from pipy_harness.native.extensions.contracts import (
    HookHandler,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
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
from pipy_harness.native.tool_renderer_lookup import ToolRendererLookup
from pipy_harness.native.tools import ToolDefinition, ToolPort
from pipy_harness.native.tools.base import NestedToolCalls, ToolOutputSink


@dataclass(frozen=True, slots=True)
class ExecutionProjectionSnapshot:
    generation_id: int
    tools: NativeToolCapabilitySnapshot
    renderers: Mapping[str, ExtensionTool]
    tool_renderer_resolvers: Sequence[ToolRendererResolver]
    tool_call_hooks: tuple[HookHandler, ...]
    flags: Mapping[str, object]
    ui_driver: ExtensionUiDriver | None


@dataclass(frozen=True, slots=True)
class ExecutionCallableSnapshot:
    """One composite parent's callable tools, bound to one capability value."""

    generation_id: int
    tools: NativeToolCapabilitySnapshot

    def callable_names(self) -> frozenset[str]:
        return self.tools.callable_names()

    def callable_tools(self) -> tuple[ToolDefinition, ...]:
        return self.tools.callable_tools()

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: ToolOutputSink | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        nested_tools: NestedToolCalls | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        return self.tools.execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
            extension_generation_id=self.generation_id,
            nested_tools=nested_tools,
            arguments=arguments,
        )

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        return self.tools.validated_arguments(call)


class SessionExecutionProjections:
    """Bind one provider turn to one published tool/renderer view."""

    __slots__ = (
        "_active",
        "_generation_ref",
        "_tools",
        "_ui_driver",
    )

    def __init__(
        self,
        *,
        generation_ref: SessionGenerationRef,
        tool_capabilities: NativeToolCapabilities,
        ui_driver: ExtensionUiDriver | None,
    ) -> None:
        self._generation_ref = generation_ref
        self._tools = tool_capabilities
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
            # The terminal binds its chrome per generation; an RPC client
            # has no chrome of its own.
            bind = getattr(self._ui_driver, "generation_driver", None)
            if chrome is not None and callable(bind):
                ui_driver = bind(chrome.sink)
            active = ExecutionProjectionSnapshot(
                generation_id=snapshot.generation_id,
                tools=self._tools.snapshot_for_projection(
                    projection.tools.capability_state
                ),
                renderers=projection.renderers.tools,
                tool_renderer_resolvers=projection.renderers.tool_resolvers,
                tool_call_hooks=projection.hooks.tool_call,
                flags=projection.runtime_flags.values,
                ui_driver=ui_driver,
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

    def hidden_declarations(self) -> frozenset[str]:
        """Declared tools that this provider turn's request leaves out."""
        return self._require_active().tools.hidden_declarations()

    def callable_snapshot(self) -> ExecutionCallableSnapshot:
        """Bind one composite parent to the live capability value.

        Unlike a direct call, which runs on the generation its provider turn
        pinned, a parent's nested calls reach what is registered and active
        when the parent is admitted: tools no request declared, and a
        selection made earlier in the same turn. The provider turn's pinned
        view is left as it is.
        """
        self._require_active()
        with self._generation_ref.lock:
            snapshot = self._generation_ref.snapshot()
            projection = snapshot.generation.projection
            if projection is None:
                raise RuntimeError("published extension generation has no projection")
            return ExecutionCallableSnapshot(
                generation_id=snapshot.generation_id,
                tools=self._tools.snapshot_for_projection(
                    projection.tools.capability_state
                ),
            )

    def composite_enabled(self) -> bool:
        return self._require_active().tools.composite_enabled()

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: ToolOutputSink | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        active = self._require_active()
        return active.tools.execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
            extension_generation_id=active.generation_id,
            arguments=arguments,
        )

    def runs_as_parent(self, tool_name: str) -> bool:
        """Whether a call the model made to this tool may call other tools
        while it runs: the tool is an extension's (Pi gives every tool of an
        extension ``ctx.executeTool``)."""
        return self._require_active().tools.is_extension_tool(tool_name)

    def execute_parent(
        self,
        call: AgentToolCall,
        /,
        *,
        wait_for_interrupt: ToolInterruptWaiter | None,
        nested_tools: NestedToolCalls,
        arguments: dict[str, Any],
    ) -> ToolExecutionOutcome:
        """Run such a call on this provider turn's tools, like ``execute``,
        on the arguments the loop prepared, which are not prepared again."""
        active = self._require_active()
        return active.tools.execute(
            call,
            wait_for_interrupt=wait_for_interrupt,
            extension_generation_id=active.generation_id,
            nested_tools=nested_tools,
            arguments=arguments,
        )

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        return self._require_active().tools.error_result(call, output_text)

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        return self._require_active().tools.validated_arguments(call)

    def tool_renderers(
        self, advertised_names: Sequence[str]
    ) -> Mapping[str, ExtensionTool]:
        return renderers_of(self._require_active().renderers, advertised_names)

    def tool_renderer_lookup(
        self, advertised_names: Sequence[str]
    ) -> ToolRendererLookup:
        """What draws this provider turn's tool calls: the renderers of its
        advertised extension tools, the titles of its MCP server tools and
        the resolvers its extension generation registered."""

        active = self._require_active()
        return ToolRendererLookup(
            tools=self.tool_renderers(advertised_names),
            mcp_labels=active.tools.mcp_tool_labels(),
            resolvers=active.tool_renderer_resolvers,
        )

    def published_tool_renderer_lookup(self) -> ToolRendererLookup:
        """What draws stored tool calls outside a provider turn, as an export
        does: every tool of the published generation that has a renderer, its
        resolvers, and the titles of the MCP server tools registered now."""

        with self._generation_ref.lock:
            projection = self._generation_ref.snapshot().generation.projection
        if projection is None:
            raise RuntimeError("published extension generation has no projection")
        return ToolRendererLookup(
            tools=projection.renderers.tools,
            mcp_labels=self._tools.callable_snapshot().mcp_tool_labels(),
            resolvers=projection.renderers.tool_resolvers,
        )

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


def renderers_of(
    renderers: Mapping[str, ExtensionTool], names: Sequence[str]
) -> dict[str, ExtensionTool]:
    """The renderers of ``names`` that have some. One ``get`` per name: the
    renderers follow tools registered while the session runs, so a name's
    membership and its renderers are read in one step."""

    chosen: dict[str, ExtensionTool] = {}
    for name in names:
        tool = renderers.get(name)
        if tool is not None:
            chosen[name] = tool
    return chosen


def build_projected_extension_tool_port(
    registered: RegisteredTool,
    *,
    has_ui: bool,
    notify_sink: Callable[[str, str], None],
    set_active_tools: Callable[[int, Sequence[str]], bool],
    get_all_tools: ControlGetAllToolsFn,
    flags: Mapping[str, object],
    project_trusted: bool,
) -> ToolPort:
    """Construct one detached projection port without touching live state."""

    return _ExtensionToolPort(
        registered,
        has_ui=has_ui,
        notify_sink=notify_sink,
        set_active_tools_fn=set_active_tools,
        get_all_tools_fn=get_all_tools,
        flags=flags,
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
    get_all_tools: ControlGetAllToolsFn,
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
            get_all_tools=get_all_tools,
            flags=frozen_flags,
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
                        pricing_for(
                            provider_state,
                            fallback.provider_name,
                            fallback.model_id,
                        )
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
