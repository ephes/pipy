"""Turn hook-dispatch and gate families for activated extensions.

This module owns the per-turn hook collectors and dispatchers that run
activated extension hooks over one submitted prompt, one system-prompt
injection point, one tool call, one tool result, and each lifecycle
event. It preserves the serial, fail-soft / fail-closed semantics of each
family verbatim: `input`, `before_agent_start`, and `tool_result` hooks
are fail-safe (a raising or non-conforming hook is ignored and the current
value is kept), `tool_call` hooks fail closed (a raising gate blocks the
call), and lifecycle hooks observe only (a raising observer is bounded and
ignored). `KeyboardInterrupt` / `SystemExit` always propagate.

It also owns the serial gate dispatchers that decide whether a stateful
operation may proceed: `dispatch_project_trust_hooks` runs pre-trust
handlers until the first valid `yes`/`no` (a raising handler is fail-soft
and recorded), while `dispatch_user_bash_hooks` and
`dispatch_session_before_hooks` fail closed — a raising local-shell or
session-operation gate blocks the command or operation.

Finally it owns the provider-request hook dispatchers that run just before
a provider call: `dispatch_before_provider_request_hooks` reads the request
attributes structurally, runs `before_provider_request` hooks fail-safe
(a raising or non-conforming hook keeps the current fields), and returns
the final `ProviderRequestTransform` with each transformed field bounded by
`_PROVIDER_REQUEST_FIELD_MAX_CHARS`; `dispatch_before_provider_headers_hooks`
runs mutation-only header hooks serially and fail-soft over one shared
mutable mapping.

It also owns the private session-run activation/projection builder. That
builder discovers and activates workspace extensions, applies reserved-name
policy, and aggregates their commands, hooks, tools, providers, renderers, and
outboxes into the `_ExtensionRuntime` value bundle owned by
`extension_runtime`.

It also owns the internal canonical-agent lifecycle adapter. The product
composition root supplies one already-composed immediate `AgentEventSink`; the
adapter delivers to it first, maps canonical run/turn boundaries to extension
lifecycle events, carries the extension dispatch context, and keeps
`agent_settled` extension-only.

It depends on canonical agent contracts, extension discovery and reserved-name
policy, the `_drive_awaitable` coroutine driver from `extension_loader`, the
hook value objects from `extension_types`, and the activation collectors,
`_ExtensionRuntime`, `_CommandContext`, `_CollectingUi`, and `EVENT_*` constants
from `extension_runtime`. The dependency remains one-way and cycle-free:
`extension_runtime` never imports back from this module.

The `before_agent_start` and `tool_result` injections are each bounded by
`_BEFORE_AGENT_START_MAX_CHARS` / `_TOOL_RESULT_MAX_CHARS` so a buggy or
malicious extension cannot create unbounded provider input.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Literal, cast

from pipy_harness.native.agent import (
    AgentEvent,
    AgentEventSink,
    AgentRunCompleted,
    AgentRunStarted,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.extension_loader import _drive_awaitable
from pipy_harness.native.extension_runtime import (
    EVENT_AGENT_END,
    EVENT_AGENT_SETTLED,
    EVENT_AGENT_START,
    EVENT_BEFORE_AGENT_START,
    EVENT_BEFORE_PROVIDER_HEADERS,
    EVENT_BEFORE_PROVIDER_REQUEST,
    EVENT_INPUT,
    EVENT_PROJECT_TRUST,
    EVENT_SESSION_BEFORE_COMPACT,
    EVENT_SESSION_BEFORE_FORK,
    EVENT_SESSION_BEFORE_SWITCH,
    EVENT_SESSION_BEFORE_TREE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USER_BASH,
    LIFECYCLE_EVENTS,
    ActivatedExtension,
    ExtensionActivationBatch,
    HookHandler,
    _CommandContext,
    _ExtensionRuntime,
    _dispose_activation_results,
    _report_activation_cleanup,
    activate_extensions,
    extension_command_map,
    extension_entry_renderers,
    extension_flags,
    extension_message_renderers,
    extension_providers,
    extension_shortcuts,
    extension_tools,
    extension_unregistered_providers,
)
from pipy_harness.native.extension_provider_catalog import (
    extension_reserved_command_names,
    extension_reserved_tool_names,
)
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent,
    BeforeAgentStartResult,
    BeforeProviderHeadersEvent,
    BeforeProviderRequestEvent,
    ExtensionCodingSessionControl,
    ExtensionMode,
    ExtensionModelRuntimeControl,
    InputEvent,
    InputTransform,
    LifecycleEvent,
    ProjectTrustContext,
    ProjectTrustDispatchResult,
    ProjectTrustEvent,
    ProjectTrustHandlerError,
    ProviderRequestTransform,
    SessionBeforeEvent,
    SessionDecision,
    ToolBlock,
    ToolCallEvent,
    ToolResultEvent,
    ToolResultTransform,
    UserBashDecision,
    UserBashDispatch,
    UserBashEvent,
    ExtensionUiDriver,
    QueuedCustomMessage,
    QueuedUserMessage,
    _safe_diagnostic,
)
from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.extensions import discover_extensions

if False:  # pragma: no cover - imported for type checkers only
    from pipy_harness.native.package_resources import PackageRoot
    from pipy_harness.native.resources import WorkspaceResources
    from pipy_harness.native.session_tree import NativeSessionTree

# Bound a transformed tool-result observation before it reaches the model.
_TOOL_RESULT_MAX_CHARS: int = 60 * 1024
# Cap the total `before_agent_start` system-prompt injection so a buggy or
# malicious extension cannot create unbounded provider input.
_BEFORE_AGENT_START_MAX_CHARS: int = 16 * 1024
# Bound each `before_provider_request` transformed field so an extension
# cannot create unbounded provider input.
_PROVIDER_REQUEST_FIELD_MAX_CHARS: int = 128 * 1024


def extension_event_hooks(
    activated: Sequence[ActivatedExtension],
    event_name: str,
) -> tuple[HookHandler, ...]:
    """Collect hooks for `event_name` from activated extensions, in order."""

    hooks: list[HookHandler] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        hooks.extend(extension.hooks.get(event_name, ()))
    return tuple(hooks)


def extension_tool_call_hooks(
    activated: Sequence[ActivatedExtension],
) -> tuple[HookHandler, ...]:
    """Collect `tool_call` hooks from activated extensions, in order."""

    return extension_event_hooks(activated, EVENT_TOOL_CALL)


def _activate_workspace_extensions(
    cwd: Path,
    resources: "WorkspaceResources",
    reserved_tool_names: tuple[str, ...] = (),
    *,
    package_roots: "Sequence[PackageRoot]" = (),
    extension_patterns: Sequence[str] = (),
    explicit_extension_paths: Sequence[Path] = (),
    include_default_extensions: bool = True,
    include_workspace_defaults: bool = False,
    activation_batch: ExtensionActivationBatch | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> _ExtensionRuntime:
    """Discover + activate extensions and project their contributions.

    Reserved names are the executable built-in/custom command set, so an
    extension command can never shadow a built-in or a custom command.
    The result bundles the command map (for dispatch), the menu
    ``/<name>`` labels + descriptions, the ordered ``tool_call`` hooks,
    the per-event lifecycle hooks, the ``input`` and ``before_agent_start``
    hooks, and the shared ``send_user_message`` outbox. Activation runs
    extension code; any failing extension is disabled by
    ``activate_extensions`` without affecting the session. Workspace extension
    discovery is fail-closed unless the caller supplies a resolved trusted
    project state.
    """

    activated: list[ActivatedExtension] = []
    try:
        if activation_batch is None:
            reserved = extension_reserved_command_names(
                resources.custom_command_slash_names()
            )
            descriptors = discover_extensions(
                cwd,
                package_roots=tuple(package_roots),
                explicit_paths=explicit_extension_paths,
                include_defaults=include_default_extensions,
                include_workspace_defaults=include_workspace_defaults,
            )
            if extension_patterns:
                from pipy_harness.native.resource_enablement import is_resource_enabled

                descriptors = [
                    descriptor
                    for descriptor in descriptors
                    if descriptor.source_kind == "cli"
                    or is_resource_enabled(descriptor.name, list(extension_patterns))
                ]
            outbox: list[QueuedUserMessage] = []
            custom_outbox: list[QueuedCustomMessage] = []
            activated = activate_extensions(
                descriptors,
                reserved_command_names=reserved,
                reserved_tool_names=extension_reserved_tool_names(reserved_tool_names),
                message_outbox=outbox,
                custom_message_outbox=custom_outbox,
                diagnostic=diagnostic,
            )
        else:
            activated = list(activation_batch.activated)
            outbox = activation_batch.message_outbox
            custom_outbox = activation_batch.custom_message_outbox
            if activation_batch.pending:
                raise ValueError("initial extension activation batch must be finalized")
        return _compose_extension_runtime(activated, outbox, custom_outbox)
    except BaseException:
        # This boundary owns every supplied or newly activated candidate host
        # until the runtime ownership value is constructed.
        _report_activation_cleanup(
            _dispose_activation_results(activated),
            diagnostic,
        )
        raise


def _compose_extension_runtime(
    activated: Sequence[ActivatedExtension],
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
) -> _ExtensionRuntime:
    """Compose one candidate runtime without publishing its host ownership."""

    command_map = extension_command_map(activated)
    menu_names = tuple(f"/{name}" for name in command_map)
    descriptions = {
        f"/{command.name}": command.description for command in command_map.values()
    }
    custom_messages = tuple(
        message
        for extension in activated
        if extension.status == "activated"
        for message in extension.custom_messages
    )
    tool_call_hooks = extension_tool_call_hooks(activated)
    lifecycle_hooks = {
        event: extension_event_hooks(activated, event) for event in LIFECYCLE_EVENTS
    }
    input_hooks = extension_event_hooks(activated, EVENT_INPUT)
    before_agent_start_hooks = extension_event_hooks(
        activated, EVENT_BEFORE_AGENT_START
    )
    tool_result_hooks = extension_event_hooks(activated, EVENT_TOOL_RESULT)
    user_bash_hooks = extension_event_hooks(activated, EVENT_USER_BASH)
    before_provider_headers_hooks = extension_event_hooks(
        activated, EVENT_BEFORE_PROVIDER_HEADERS
    )
    before_provider_request_hooks = extension_event_hooks(
        activated, EVENT_BEFORE_PROVIDER_REQUEST
    )
    session_before_switch_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_SWITCH
    )
    session_before_fork_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_FORK
    )
    session_before_compact_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_COMPACT
    )
    session_before_tree_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_TREE
    )
    return _ExtensionRuntime(
        commands=command_map,
        menu_names=menu_names,
        descriptions=descriptions,
        tool_call_hooks=tool_call_hooks,
        lifecycle_hooks=lifecycle_hooks,
        input_hooks=input_hooks,
        before_agent_start_hooks=before_agent_start_hooks,
        tool_result_hooks=tool_result_hooks,
        user_bash_hooks=user_bash_hooks,
        before_provider_headers_hooks=before_provider_headers_hooks,
        before_provider_request_hooks=before_provider_request_hooks,
        session_before_switch_hooks=session_before_switch_hooks,
        session_before_fork_hooks=session_before_fork_hooks,
        session_before_compact_hooks=session_before_compact_hooks,
        session_before_tree_hooks=session_before_tree_hooks,
        outbox=outbox,
        custom_outbox=custom_outbox,
        tools=extension_tools(activated),
        shortcuts=extension_shortcuts(activated),
        flags=extension_flags(activated),
        providers=extension_providers(activated),
        unregistered_providers=extension_unregistered_providers(activated),
        message_renderers=extension_message_renderers(activated),
        entry_renderers=extension_entry_renderers(activated),
        custom_messages=custom_messages,
        activation_hosts=tuple(
            extension._activation_host
            for extension in activated
            if extension.status == "activated"
            if extension._activation_host is not None
        ),
    )


def dispatch_input_hooks(
    hooks: Sequence[HookHandler],
    text: str,
    *,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> str:
    """Run `input` hooks over a submitted prompt; return the final text.

    Hooks run in registration order, each receiving an `InputEvent` with
    the current text. A hook returning an `InputTransform` replaces the
    text for subsequent hooks; any other return value observes only. A
    hook that raises is fail-safe: the current text is kept unchanged so
    a buggy hook never breaks submission. `KeyboardInterrupt` /
    `SystemExit` propagate.
    """

    current = text
    if not hooks:
        return current
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    for hook in hooks:
        try:
            result = hook(InputEvent(text=current), ctx)
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail-safe: keep current text
            continue
        if isinstance(result, InputTransform) and isinstance(result.text, str):
            # Ignore a non-string transform (fail-safe): never propagate a
            # non-string into @file resolution / the provider request.
            current = result.text
    return current


def dispatch_before_agent_start_hooks(
    hooks: Sequence[HookHandler],
    *,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    system_prompt: str = "",
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> BeforeAgentStartResult:
    """Run `before_agent_start` hooks; aggregate their context injections.

    Each hook receives a `BeforeAgentStartEvent` (the current system
    prompt) and may return a `BeforeAgentStartResult` whose
    `append_system_prompt` is concatenated (in order). A hook that raises
    is fail-safe (ignored). `KeyboardInterrupt` / `SystemExit` propagate.
    """

    appended: list[str] = []
    if hooks:
        ctx = _CommandContext(
            cwd,
            _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
            model_runtime=model_runtime,
            flags=flags,
            project_trusted=project_trusted,
        )
        current_prompt = system_prompt
        for hook in hooks:
            try:
                result = hook(BeforeAgentStartEvent(system_prompt=current_prompt), ctx)
                if inspect.isawaitable(result):
                    result = _drive_awaitable(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - fail-safe: ignore a bad hook
                continue
            if (
                isinstance(result, BeforeAgentStartResult)
                and isinstance(result.append_system_prompt, str)
                and result.append_system_prompt
            ):
                appended.append(result.append_system_prompt)
                # Later hooks see earlier hooks' appended context (ordered
                # composition), matching `BeforeAgentStartEvent.system_prompt`.
                current_prompt = current_prompt + "\n" + result.append_system_prompt
    if not appended:
        return BeforeAgentStartResult(append_system_prompt=None)
    combined = "\n".join(appended)
    if len(combined) > _BEFORE_AGENT_START_MAX_CHARS:
        combined = (
            combined[:_BEFORE_AGENT_START_MAX_CHARS]
            + "\n[pipy: before_agent_start injection truncated]"
        )
    return BeforeAgentStartResult(append_system_prompt=combined)


def dispatch_tool_result_hooks(
    hooks: Sequence[HookHandler],
    *,
    tool_name: str,
    content: str,
    is_error: bool,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> str:
    """Run `tool_result` hooks over a finalized tool result; return content.

    Each hook receives a `ToolResultEvent` with the current content and
    may return a `ToolResultTransform` to replace it for later hooks /
    the model. Hooks run in registration order. A hook that raises or
    returns a non-string transform is fail-safe (the current content is
    kept). The final content is bounded before returning to the model.
    `KeyboardInterrupt` / `SystemExit` propagate.
    """

    current = content
    if hooks:
        ctx = _CommandContext(
            cwd,
            _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
            model_runtime=model_runtime,
            flags=flags,
            project_trusted=project_trusted,
        )
        for hook in hooks:
            try:
                result = hook(
                    ToolResultEvent(
                        tool_name=tool_name, content=current, is_error=is_error
                    ),
                    ctx,
                )
                if inspect.isawaitable(result):
                    result = _drive_awaitable(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - fail-safe: keep current content
                continue
            if isinstance(result, ToolResultTransform) and isinstance(
                result.content, str
            ):
                current = result.content
    if len(current) > _TOOL_RESULT_MAX_CHARS:
        current = (
            current[:_TOOL_RESULT_MAX_CHARS]
            + "\n[pipy: tool_result transform truncated]"
        )
    return current


def dispatch_lifecycle_hooks(
    hooks: Sequence[HookHandler],
    event: LifecycleEvent,
    *,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> None:
    """Run observe-only lifecycle hooks for one event, in order.

    Each hook receives the `LifecycleEvent` and a mode-aware context. The
    return value is ignored (these hooks observe; they do not alter the
    turn in this slice). A hook that raises is bounded and ignored so one
    crashing observer never breaks the session or the other observers.
    `KeyboardInterrupt` / `SystemExit` propagate (user abort is never
    swallowed).
    """

    if not hooks:
        return
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    for hook in hooks:
        try:
            result = hook(event, ctx)
            if inspect.isawaitable(result):
                _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - an observer must not break the session
            continue


class _ExtensionLifecycleAgentEventAdapter:
    """Deliver canonical events immediately, then notify extension lifecycle hooks.

    Product projection composition remains outside this adapter: the supplied
    sink is the root-owned renderer/automation/product/archive/caller composite.
    Lifecycle observers run only after that sink accepts an event, so a failed
    immediate projection prevents the corresponding extension callback.
    """

    def __init__(
        self,
        immediate_sink: AgentEventSink,
        *,
        lifecycle_hooks: dict[str, tuple[HookHandler, ...]],
        cwd: str,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> None:
        self._immediate_sink = immediate_sink
        self._lifecycle_hooks = lifecycle_hooks
        self._lifecycle_cwd = cwd
        self._lifecycle_has_ui = has_ui
        self._lifecycle_notify_sink = notify_sink
        self._lifecycle_ui_driver = ui_driver
        self._lifecycle_flags = dict(flags or {})
        self._lifecycle_project_trusted = bool(project_trusted)

    def emit(self, event: AgentEvent) -> None:
        """Synchronously deliver one event before its lifecycle callback."""

        self._immediate_sink.emit(event)
        if isinstance(event, AgentRunStarted):
            self.fire_lifecycle(EVENT_AGENT_START)
        elif isinstance(event, AgentRunCompleted):
            self.fire_lifecycle(EVENT_AGENT_END)
        elif isinstance(event, TurnStarted):
            self.fire_lifecycle(EVENT_TURN_START)
        elif isinstance(event, TurnCompleted):
            self.fire_lifecycle(EVENT_TURN_END)

    def set_lifecycle_hooks(
        self, lifecycle_hooks: dict[str, tuple[HookHandler, ...]]
    ) -> None:
        self._lifecycle_hooks = lifecycle_hooks

    def set_flags(self, flags: Mapping[str, object]) -> None:
        self._lifecycle_flags = dict(flags)

    def fire_lifecycle(
        self,
        name: str,
        *,
        reason: str | None = None,
        ui_driver_override: ExtensionUiDriver | None = None,
    ) -> None:
        hooks = self._lifecycle_hooks.get(name)
        if not hooks:
            return
        dispatch_lifecycle_hooks(
            hooks,
            LifecycleEvent(name=name, reason=reason),
            cwd=self._lifecycle_cwd,
            has_ui=self._lifecycle_has_ui,
            notify_sink=self._lifecycle_notify_sink,
            ui_driver=(
                ui_driver_override
                if ui_driver_override is not None
                else self._lifecycle_ui_driver
            ),
            flags=self._lifecycle_flags,
            project_trusted=self._lifecycle_project_trusted,
        )

    def agent_settled(self) -> None:
        # Extension-only: JSON and RPC own their protocol `agent_settled`
        # synthesis at mode-specific idle boundaries. Sending this through the
        # shared canonical event stream would duplicate those public events.
        self.fire_lifecycle(EVENT_AGENT_SETTLED)


def dispatch_tool_call_hooks(
    hooks: Sequence[HookHandler],
    *,
    tool_name: str,
    tool_input: Mapping[str, object],
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ToolBlock | None:
    """Run `tool_call` hooks for one tool call; return the first block.

    Each hook receives a `ToolCallEvent` (live tool name + parsed input)
    and a mode-aware context. The first hook to return a `ToolBlock`
    blocks the call; hooks returning anything else allow it. A hook that
    raises fails closed (blocks with a safe reason), since a policy gate
    that errors must not silently allow the action. `KeyboardInterrupt` /
    `SystemExit` propagate (user abort is never swallowed).
    """

    event = ToolCallEvent(tool_name=tool_name, input=tool_input)
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    for hook in hooks:
        try:
            result = hook(event, ctx)
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail closed on a bad gate
            return ToolBlock(reason="extension tool_call hook error")
        if isinstance(result, ToolBlock):
            return result
    return None


def dispatch_project_trust_hooks(
    activated: Sequence[ActivatedExtension],
    *,
    cwd: str,
    mode: ExtensionMode,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: ExtensionUiDriver | None = None,
) -> ProjectTrustDispatchResult:
    """Run pre-trust handlers serially until the first valid yes/no result."""

    ui = _CollectingUi(has_ui, notify_sink=notify_sink, ui_driver=ui_driver)
    event = ProjectTrustEvent(cwd=cwd)
    ctx = ProjectTrustContext(cwd=cwd, mode=mode, has_ui=has_ui, ui=ui)
    errors: list[ProjectTrustHandlerError] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        for handler in extension.hooks.get(EVENT_PROJECT_TRUST, ()):
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    result = _drive_awaitable(result)
                if not isinstance(result, Mapping):
                    raise ValueError("project_trust handler must return a mapping")
                trusted = result.get("trusted")
                if trusted == "undecided":
                    continue
                if trusted not in ("yes", "no"):
                    raise ValueError(
                        "project_trust trusted must be yes, no, or undecided"
                    )
                return ProjectTrustDispatchResult(
                    trusted=cast(Literal["yes", "no"], trusted),
                    remember=result.get("remember") is True,
                    errors=tuple(errors),
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as err:  # noqa: BLE001 - fail-soft extension hook
                errors.append(
                    ProjectTrustHandlerError(
                        extension=extension.path_label,
                        error=_safe_diagnostic(err),
                    )
                )
    return ProjectTrustDispatchResult(errors=tuple(errors))


def dispatch_user_bash_hooks(
    hooks: Sequence[HookHandler],
    *,
    command: str,
    exclude_from_context: bool,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> UserBashDispatch:
    """Run `user_bash` hooks for one local shell shortcut.

    Hooks run in registration order. A `UserBashDecision` may block,
    replace the command, flip context recording, or provide a synthetic
    result that skips shell execution. A crashing hook fails closed and
    blocks the shell command. `KeyboardInterrupt` / `SystemExit` propagate.
    """

    current_command = command
    current_exclude = bool(exclude_from_context)
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    for hook in hooks:
        event = UserBashEvent(
            command=current_command,
            exclude_from_context=current_exclude,
            cwd=cwd,
        )
        try:
            result = hook(event, ctx)
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail closed on a shell gate
            return UserBashDispatch(
                allowed=False,
                command=current_command,
                exclude_from_context=current_exclude,
                reason="extension user_bash hook error",
            )
        if not isinstance(result, UserBashDecision):
            continue
        if not result.allow:
            return UserBashDispatch(
                allowed=False,
                command=current_command,
                exclude_from_context=current_exclude,
                reason=result.reason or "blocked by extension",
            )
        if isinstance(result.command, str) and result.command.strip():
            current_command = result.command.strip()
        if isinstance(result.exclude_from_context, bool):
            current_exclude = result.exclude_from_context
        if isinstance(result.result, str):
            return UserBashDispatch(
                allowed=True,
                command=current_command,
                exclude_from_context=current_exclude,
                result=result.result,
                exit_code=int(result.exit_code)
                if isinstance(result.exit_code, int)
                else 0,
            )
    return UserBashDispatch(
        allowed=True,
        command=current_command,
        exclude_from_context=current_exclude,
    )


def dispatch_session_before_hooks(
    hooks: Sequence[HookHandler],
    *,
    operation: str,
    cwd: str,
    has_ui: bool,
    target: str | None = None,
    trigger: str | None = None,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> SessionDecision:
    """Run session-operation gates and return the first blocking decision.

    A crashing hook fails closed, because session switching/forking/tree
    navigation/compaction are stateful operations. Observe-only or
    `SessionDecision(allow=True)` returns allow the operation.
    """

    if not hooks:
        return SessionDecision()
    event = SessionBeforeEvent(operation=operation, target=target, trigger=trigger)
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    for hook in hooks:
        try:
            result = hook(event, ctx)
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail closed on a session gate
            return SessionDecision(
                allow=False, reason=f"extension {operation} hook error"
            )
        if isinstance(result, SessionDecision) and not result.allow:
            return SessionDecision(
                allow=False, reason=result.reason or "blocked by extension"
            )
    return SessionDecision()


def dispatch_before_provider_request_hooks(
    hooks: Sequence[HookHandler],
    request: object,
    *,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ProviderRequestTransform:
    """Run `before_provider_request` hooks and return the final transform.

    The dispatcher deliberately avoids importing `ProviderRequest` here to
    keep the public extension runtime lightweight. It reads the expected
    request attributes structurally. Crashing hooks are fail-safe: the
    current request fields are preserved.
    """

    current_system = str(getattr(request, "system_prompt", ""))
    current_user = str(getattr(request, "user_prompt", ""))
    tools = tuple(
        str(getattr(tool, "name", ""))
        for tool in getattr(request, "available_tools", ())
        if str(getattr(tool, "name", ""))
    )
    current_tools: tuple[str, ...] | None = None
    if hooks:
        ctx = _CommandContext(
            cwd,
            _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
            ExtensionCodingSessionControl(
                messages=tuple(getattr(request, "messages", ()))
            ),
            model_runtime=model_runtime,
            flags=flags,
            project_trusted=project_trusted,
        )
        for hook in hooks:
            event = BeforeProviderRequestEvent(
                system_prompt=current_system,
                user_prompt=current_user,
                provider_name=str(getattr(request, "provider_name", "")),
                model_id=str(getattr(request, "model_id", "")),
                available_tools=tools if current_tools is None else current_tools,
                messages=tuple(getattr(request, "messages", ())),
            )
            try:
                result = hook(event, ctx)
                if inspect.isawaitable(result):
                    result = _drive_awaitable(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - fail-safe: preserve request
                continue
            if not isinstance(result, ProviderRequestTransform):
                continue
            if isinstance(result.system_prompt, str):
                current_system = _bounded_provider_field(result.system_prompt)
            if isinstance(result.user_prompt, str):
                current_user = _bounded_provider_field(result.user_prompt)
            if result.available_tools is not None:
                current_tools = tuple(
                    str(name)
                    for name in result.available_tools
                    if isinstance(name, str) and name
                )
    return ProviderRequestTransform(
        system_prompt=current_system,
        user_prompt=current_user,
        available_tools=current_tools,
    )


def dispatch_before_provider_headers_hooks(
    hooks: Sequence[HookHandler],
    headers: MutableMapping[str, str | None],
    *,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    flags: Mapping[str, object] | None = None,
    session_tree: "NativeSessionTree | None" = None,
    project_trusted: bool = False,
) -> None:
    """Run mutation-only provider-header hooks serially and fail soft.

    Every hook receives the same mutable mapping, so later handlers observe
    prior mutations. Awaitables are driven to completion and return values are
    deliberately ignored. A bad handler does not prevent later handlers or the
    provider request from continuing.
    """

    if not hooks:
        return
    ctx = _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        ExtensionCodingSessionControl(session_tree=session_tree),
        flags=flags,
        project_trusted=project_trusted,
    )
    event = BeforeProviderHeadersEvent(headers=headers)
    for hook in hooks:
        try:
            result = hook(event, ctx)
            if inspect.isawaitable(result):
                _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - Pi-compatible fail-soft observer
            continue


def _bounded_provider_field(value: str) -> str:
    if len(value) <= _PROVIDER_REQUEST_FIELD_MAX_CHARS:
        return value
    return (
        value[:_PROVIDER_REQUEST_FIELD_MAX_CHARS]
        + "\n[pipy: before_provider_request field truncated]"
    )
