"""Per-operation extension effects for one session.

Every extension-visible operation -- a provider request, a tool call, a hook --
is prepared here into an immutable snapshot before anything runs. That ordering
is the point: an extension may mutate session state while an operation is in
flight, and a snapshot taken first means the operation completes against the
state it was admitted with rather than whatever the extension left behind.

None of this reaches the session object. It receives the projections it needs,
which is what lets it live outside the composition root.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Literal

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.agent.loop_policy import AgentProviderRequestPolicyInput
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent_request import (
    NativeProviderRequestHookContext,
    prepare_provider_request,
)
from pipy_harness.native.extension_hooks import (
    dispatch_before_agent_start_hooks,
    dispatch_before_provider_headers_hooks,
    dispatch_input_hooks,
    dispatch_tool_result_hooks,
)
from pipy_harness.native.extension_hooks import (
    dispatch_session_before_hooks as dispatch_session_before_hooks,
)
from pipy_harness.native.extension_runtime import (
    BeforeAgentStartResult,
    ExtensionCodingSessionControl,
    ExtensionCommandDispatch,
    ExtensionModelRuntimeControl,
    ExtensionUiDriver,
    HookHandler,
    SessionDecision,
    dispatch_extension_command,
    dispatch_extension_shortcut,
)
from pipy_harness.native.extension_types import CustomComponentDriver
from pipy_harness.native.session_generation import (
    ExtensionProjection,
    SessionGenerationRef,
)
from pipy_harness.native.session_tree import NativeSessionTree

SessionHookFamily = Literal["switch", "fork", "compact", "tree"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderHeaderRequestSnapshot:
    """Detached provider-worker callback inputs for one request."""

    hooks: tuple[HookHandler, ...]
    flags: Mapping[str, object]
    cwd: str
    has_ui: bool
    notify_sink: Callable[[str, str], None] | None
    ui_driver: ExtensionUiDriver | None
    session_tree: NativeSessionTree
    project_trusted: bool

    def __call__(self, headers: MutableMapping[str, str | None]) -> None:
        dispatch_before_provider_headers_hooks(
            self.hooks,
            headers,
            cwd=self.cwd,
            has_ui=self.has_ui,
            notify_sink=self.notify_sink,
            ui_driver=self.ui_driver,
            flags=self.flags,
            session_tree=self.session_tree,
            project_trusted=self.project_trusted,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionExtensionOperations:
    """Take one published projection snapshot for each R4a operation."""

    generation_ref: SessionGenerationRef
    cwd: str
    has_ui: bool
    notify_sink: Callable[[str, str], None] | None
    ui_driver: ExtensionUiDriver | None
    project_trusted: bool
    model_runtime_factory: Callable[[int, bool], ExtensionModelRuntimeControl]

    def _projection(
        self,
    ) -> tuple[ExtensionProjection, ExtensionUiDriver | None, int]:
        snapshot = self.generation_ref.snapshot()
        projection = snapshot.generation.projection
        if projection is None:
            raise RuntimeError("published extension generation has no projection")
        ui_driver = self.ui_driver
        chrome = projection.chrome
        bind = getattr(ui_driver, "generation_driver", None)
        if chrome is not None and callable(bind):
            ui_driver = bind(chrome.sink)
        return projection, ui_driver, snapshot.generation_id

    def dispatch_command(
        self,
        command_text: str,
        *,
        coding_session: ExtensionCodingSessionControl,
        ui_custom_driver: CustomComponentDriver | None,
    ) -> ExtensionCommandDispatch | None:
        projection, ui_driver, generation_id = self._projection()
        return dispatch_extension_command(
            command_text,
            projection.commands.commands,
            cwd=self.cwd,
            has_ui=self.has_ui,
            coding_session=coding_session,
            notify_sink=self.notify_sink,
            ui_custom_driver=ui_custom_driver,
            ui_driver=ui_driver,
            model_runtime=self.model_runtime_factory(generation_id, True),
            flags=projection.runtime_flags.values,
            project_trusted=self.project_trusted,
        )

    def dispatch_shortcut(
        self,
        key: str,
        *,
        coding_session: ExtensionCodingSessionControl,
        ui_custom_driver: CustomComponentDriver | None,
    ) -> ExtensionCommandDispatch | None:
        projection, ui_driver, generation_id = self._projection()
        return dispatch_extension_shortcut(
            key,
            projection.commands.shortcuts,
            cwd=self.cwd,
            has_ui=self.has_ui,
            coding_session=coding_session,
            notify_sink=self.notify_sink,
            ui_custom_driver=ui_custom_driver,
            ui_driver=ui_driver,
            model_runtime=self.model_runtime_factory(generation_id, True),
            flags=projection.runtime_flags.values,
            project_trusted=self.project_trusted,
        )

    def dispatch_input(self, text: str) -> str:
        projection, ui_driver, generation_id = self._projection()
        return dispatch_input_hooks(
            projection.hooks.input,
            text,
            cwd=self.cwd,
            has_ui=self.has_ui,
            notify_sink=self.notify_sink,
            ui_driver=ui_driver,
            model_runtime=self.model_runtime_factory(generation_id, True),
            project_trusted=self.project_trusted,
        )

    def dispatch_before_agent_start(
        self,
        system_prompt: str,
    ) -> BeforeAgentStartResult:
        projection, ui_driver, generation_id = self._projection()
        return dispatch_before_agent_start_hooks(
            projection.hooks.before_agent_start,
            cwd=self.cwd,
            has_ui=self.has_ui,
            system_prompt=system_prompt,
            notify_sink=self.notify_sink,
            ui_driver=ui_driver,
            model_runtime=self.model_runtime_factory(generation_id, True),
            flags=projection.runtime_flags.values,
            project_trusted=self.project_trusted,
        )

    def prepare_provider_request(
        self,
        policy_input: AgentProviderRequestPolicyInput,
    ) -> AgentProviderRequestSnapshot:
        projection, ui_driver, generation_id = self._projection()
        return prepare_provider_request(
            policy_input,
            projection.hooks.before_provider_request,
            NativeProviderRequestHookContext(
                cwd=self.cwd,
                has_ui=self.has_ui,
                notify_sink=self.notify_sink,
                ui_driver=ui_driver,
                model_runtime=self.model_runtime_factory(generation_id, False),
                flags=projection.runtime_flags.values,
                project_trusted=self.project_trusted,
            ),
        )

    def provider_header_callback(
        self, session_tree: NativeSessionTree
    ) -> Callable[[MutableMapping[str, str | None]], None] | None:
        projection, ui_driver, _generation_id = self._projection()
        hooks = projection.hooks.before_provider_headers
        if not hooks:
            return None
        return ProviderHeaderRequestSnapshot(
            hooks=hooks,
            flags=projection.runtime_flags.values,
            cwd=self.cwd,
            has_ui=self.has_ui,
            notify_sink=self.notify_sink,
            ui_driver=ui_driver,
            session_tree=session_tree,
            project_trusted=self.project_trusted,
        )

    def transform_tool_result(
        self,
        *,
        tool_name: str,
        content: ProductContent,
        is_error: bool,
    ) -> ProductContent:
        projection, ui_driver, generation_id = self._projection()
        hooks = projection.hooks.tool_result
        if not hooks:
            return content
        return ProductContent(
            dispatch_tool_result_hooks(
                hooks,
                tool_name=tool_name,
                content=content.value,
                is_error=is_error,
                cwd=self.cwd,
                has_ui=self.has_ui,
                notify_sink=self.notify_sink,
                ui_driver=ui_driver,
                model_runtime=self.model_runtime_factory(generation_id, False),
                flags=projection.runtime_flags.values,
                project_trusted=self.project_trusted,
            )
        )

    def session_allows(
        self,
        family: SessionHookFamily,
        *,
        operation: str,
        target: str | None = None,
        trigger: str | None = None,
    ) -> SessionDecision:
        projection, ui_driver, generation_id = self._projection()
        hooks = {
            "switch": projection.hooks.session_before_switch,
            "fork": projection.hooks.session_before_fork,
            "compact": projection.hooks.session_before_compact,
            "tree": projection.hooks.session_before_tree,
        }[family]
        return dispatch_session_before_hooks(
            hooks,
            operation=operation,
            cwd=self.cwd,
            has_ui=self.has_ui,
            target=target,
            trigger=trigger,
            notify_sink=self.notify_sink,
            ui_driver=ui_driver,
            model_runtime=self.model_runtime_factory(generation_id, True),
            flags=projection.runtime_flags.values,
            project_trusted=self.project_trusted,
        )

    def user_bash_inputs(
        self,
    ) -> tuple[
        tuple[HookHandler, ...],
        Mapping[str, object],
        ExtensionUiDriver | None,
        ExtensionModelRuntimeControl,
    ]:
        projection, ui_driver, generation_id = self._projection()
        return (
            projection.hooks.user_bash,
            projection.runtime_flags.values,
            ui_driver,
            self.model_runtime_factory(generation_id, True),
        )
