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
`extensions.contracts`.

It also owns the internal canonical-agent lifecycle adapter. The product
composition root supplies one already-composed immediate `AgentEventSink`; the
adapter delivers to it first, maps canonical run/turn boundaries to extension
lifecycle events, carries the extension dispatch context, and keeps
`agent_settled` extension-only.

It depends on canonical agent contracts, extension discovery and reserved-name
policy, the `_drive_awaitable` coroutine driver from `extension_loader`, hook
value objects from `extension_types`, command/shortcut collectors from
`extensions.dispatch`, context/UI adapters from their native owners, and the
activation contracts and collectors from their leaf owners, and lifecycle
operations plus `EVENT_*` constants from `extensions.activation`. The dependency
remains one-way and cycle-free: activation never imports back from this module.

The `before_agent_start` and `tool_result` injections are each bounded by
`_BEFORE_AGENT_START_MAX_CHARS` / `TOOL_RESULT_MAX_CHARS` so a buggy or
malicious extension cannot create unbounded provider input.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from pipy_harness.native.session_generation import SessionGenerationSnapshot

from pipy_harness.native.agent import (
    AgentEvent,
    AgentEventSink,
    AgentRunCompleted,
    AgentRunStarted,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
    ToolResultBlock,
    tool_result_images,
    tool_result_text,
)
from pipy_harness.native.extension_loader import _drive_awaitable
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
    ExtensionUiDriver,
    InputEvent,
    InputHandled,
    InputTransform,
    LifecycleEvent,
    McpServersChangeEvent,
    ProjectTrustContext,
    ProjectTrustDispatchResult,
    ProjectTrustEvent,
    ProjectTrustHandlerError,
    ProviderRequestTransform,
    QueuedCustomMessage,
    QueuedUserMessage,
    SessionBeforeEvent,
    SessionDecision,
    SystemPromptOptions,
    ToolBlock,
    ToolCallEvent,
    ToolResultEvent,
    ToolResultTransform,
    UserBashDecision,
    UserBashDispatch,
    UserBashEvent,
    _safe_diagnostic,
)
from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.extensions.activation import (
    EVENT_AGENT_END,
    EVENT_AGENT_SETTLED,
    EVENT_AGENT_START,
    EVENT_BEFORE_AGENT_START,
    EVENT_BEFORE_PROVIDER_HEADERS,
    EVENT_BEFORE_PROVIDER_REQUEST,
    EVENT_INPUT,
    EVENT_MCP_SERVERS_CHANGE,
    EVENT_PROJECT_TRUST,
    EVENT_SESSION_BEFORE_COMPACT,
    EVENT_SESSION_BEFORE_FORK,
    EVENT_SESSION_BEFORE_SWITCH,
    EVENT_SESSION_BEFORE_TREE,
    EVENT_SESSION_START,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USER_BASH,
    LIFECYCLE_EVENTS,
    _dispose_activation_results,
    _report_activation_cleanup,
    activate_extensions,
)
from pipy_harness.native.extensions.collectors import (
    extension_entry_renderers,
    extension_flags,
    extension_message_renderers,
    extension_providers,
    extension_tool_renderers,
    extension_tools,
    extension_unregistered_providers,
)
from pipy_harness.native.extensions.command_context import _CommandContext
from pipy_harness.native.extensions.contracts import (
    ActivatedExtension,
    ExtensionActivationBatch,
    HookHandler,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.dispatch import (
    extension_command_map,
    extension_shortcuts,
)
from pipy_harness.native.extensions.message_routing import GenerationMessageRouting
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.mcp.registry import McpServerRegistry, RegisteredMcpServer
from pipy_harness.native.session_generation import (
    FrozenStagedDeliveryBatch,
    OrderedDeliveryGate,
    OrderedDeliveryToken,
)
from pipy_harness.native.tools.base import json_safe_details, json_safe_value

if False:  # pragma: no cover - imported for type checkers only
    from pipy_harness.native.package_resources import PackageRoot
    from pipy_harness.native.resources import WorkspaceResources
    from pipy_harness.native.session_tree import NativeSessionTree

# Bound a transformed tool-result observation before it reaches the model.
TOOL_RESULT_MAX_CHARS: int = 60 * 1024
# Cap the total `before_agent_start` system-prompt injection so a buggy or
# malicious extension cannot create unbounded provider input.
_BEFORE_AGENT_START_MAX_CHARS: int = 16 * 1024
# Bound each `before_provider_request` transformed field so an extension
# cannot create unbounded provider input.
_PROVIDER_REQUEST_FIELD_MAX_CHARS: int = 128 * 1024


def deliver_staged_custom(
    sink: Callable[..., object], message: QueuedCustomMessage
) -> None:
    sink(
        message.custom_type,
        message.content,
        message.display,
        message.options,
        message.details,
    )


def deliver_accepted_staged_batch(  # noqa: C901 - explicit independent cleanup arms
    batch: FrozenStagedDeliveryBatch,
    *,
    gate: OrderedDeliveryGate,
    token: OrderedDeliveryToken,
    user_sink: Callable[[QueuedUserMessage], None],
    custom_sink: Callable[[QueuedCustomMessage], None],
    release_route: Callable[[], object] | None = None,
) -> None:
    """Deliver one frozen staged batch before releasing queued live sends.

    R3b defines this sequencer; startup and accepted reload now call it. Users retain
    their order and all run before customs in their order (the established R1
    flush shape). Every sink runs unlocked. Ordinary failures are all preserved;
    interrupts stop delivery, abort queued work, and propagate.
    """

    failures: list[Exception] = []

    def deliver(callback: Callable[[Any], None], value: object) -> None:
        try:
            callback(value)
        except Exception as error:  # noqa: BLE001 - collected; every subscriber still delivered
            failures.append(error)

    route_release_attempted = False
    try:
        gate.validate(token)
        for user_message in batch.user_messages:
            deliver(user_sink, user_message)
        for custom_message in batch.custom_messages:
            deliver(custom_sink, custom_message)
        if release_route is not None:
            route_release_attempted = True
            try:
                release_route()
            except Exception as error:  # noqa: BLE001 - collected; the gate is still released
                failures.append(error)
        gate.release(token)
        try:
            gate.drain(token)
        except Exception as error:  # noqa: BLE001 - collected; drain failure is reported
            failures.append(error)
    except BaseException:
        if release_route is not None and not route_release_attempted:
            try:
                release_route()
            except BaseException:  # noqa: BLE001 - rollback in rollback; original re-raised
                pass
        raise
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise ExceptionGroup("accepted staged delivery failures", failures)


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
            message_routing = GenerationMessageRouting(outbox, custom_outbox)
            mcp_servers = McpServerRegistry()
            activated = activate_extensions(
                descriptors,
                reserved_command_names=reserved,
                reserved_tool_names=extension_reserved_tool_names(reserved_tool_names),
                message_outbox=outbox,
                custom_message_outbox=custom_outbox,
                message_routing=message_routing,
                mcp_servers=mcp_servers,
                diagnostic=diagnostic,
            )
        else:
            activated = list(activation_batch.activated)
            outbox = activation_batch.message_outbox
            custom_outbox = activation_batch.custom_message_outbox
            message_routing = activation_batch.message_routing
            mcp_servers = activation_batch.mcp_servers
            if activation_batch.pending:
                raise ValueError("initial extension activation batch must be finalized")
        return _compose_extension_bundle(
            activated, outbox, custom_outbox, message_routing, mcp_servers
        )
    except BaseException:
        # This boundary owns every supplied or newly activated candidate host
        # until the runtime ownership value is constructed.
        _report_activation_cleanup(
            _dispose_activation_results(activated),
            diagnostic,
        )
        raise


def _compose_extension_bundle(
    activated: Sequence[ActivatedExtension],
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
    message_routing: GenerationMessageRouting,
    mcp_servers: McpServerRegistry | None = None,
) -> _ExtensionRuntime:
    """Compose one candidate runtime without publishing its host ownership.

    ``mcp_servers`` is the registry the activation hosts register MCP
    servers in; without it the runtime has an empty one.
    """

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
        tool_renderers=extension_tool_renderers(activated),
        custom_messages=custom_messages,
        activation_hosts=tuple(
            extension._activation_host
            for extension in activated
            if extension.status == "activated"
            if extension._activation_host is not None
        ),
        message_routing=message_routing,
        mcp_servers=mcp_servers if mcp_servers is not None else McpServerRegistry(),
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
) -> str | None:
    """Run `input` hooks over a submitted prompt; return the final text.

    Hooks run in registration order, each receiving an `InputEvent` with
    the current text. A hook returning an `InputTransform` replaces the
    text for subsequent hooks; one returning `InputHandled` consumed the
    prompt: the result is `None`, later hooks do not run and nothing is
    sent (Pi `emitInput`). Any other return value observes only. A hook
    that raises is fail-safe: the current text is kept unchanged so a
    buggy hook never breaks submission. `KeyboardInterrupt` /
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
        if isinstance(result, InputHandled):
            return None
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
    sections: dict[str, str] | None = None,
    render_system_prompt: Callable[[], str] | None = None,
) -> BeforeAgentStartResult:
    """Run `before_agent_start` hooks; aggregate their context injections.

    Each hook receives a `BeforeAgentStartEvent` (the current system
    prompt) and may return a `BeforeAgentStartResult` whose
    `append_system_prompt` is concatenated (in order). A hook that raises
    is fail-safe (ignored). `KeyboardInterrupt` / `SystemExit` propagate.

    ``sections`` is the run's own prompt sections; every hook gets the same
    mapping as ``event.system_prompt_options.sections`` and changes it in
    place, so the caller reads the outcome from it (what a hook changed
    before it raised stays). ``render_system_prompt`` renders the prompt for
    the mapping as it is now; ``event.system_prompt`` calls it on every read
    (Pi's getter), so a hook sees the sections it and earlier hooks changed.
    Without it every hook is shown ``system_prompt``. Either way the text
    earlier hooks appended follows the prompt.
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
        options = SystemPromptOptions(sections={} if sections is None else sections)
        for hook in hooks:
            # Later hooks see earlier hooks' appended context (ordered
            # composition), matching `BeforeAgentStartEvent.system_prompt`.
            prompt: str | Callable[[], str] = "\n".join([system_prompt, *appended])
            if render_system_prompt is not None:
                prompt = functools.partial(
                    _prompt_with_appended, render_system_prompt, tuple(appended)
                )
            try:
                result = hook(
                    BeforeAgentStartEvent(prompt, system_prompt_options=options),
                    ctx,
                )
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
    if not appended:
        return BeforeAgentStartResult(append_system_prompt=None)
    combined = "\n".join(appended)
    if len(combined) > _BEFORE_AGENT_START_MAX_CHARS:
        combined = (
            combined[:_BEFORE_AGENT_START_MAX_CHARS]
            + "\n[pipy: before_agent_start injection truncated]"
        )
    return BeforeAgentStartResult(append_system_prompt=combined)


def _prompt_with_appended(render: Callable[[], str], appended: tuple[str, ...]) -> str:
    return "\n".join([render(), *appended])


def bounded_result_blocks(
    blocks: object, cap: int, marker: str
) -> tuple[ToolResultBlock, ...] | None:
    """A handler's ``blocks`` with their text bounded, or ``None`` when they
    are not a sequence of text and image blocks.

    The bound is ``cap`` or the ``tool_result`` hooks' bound, whichever is
    smaller: text the hooks would cut loses the images with it. Text within
    the bound is kept as it is. Otherwise images are kept whole and in
    order, the text block that reaches the bound is cut and marked, and
    later text blocks are left out. ``marker`` ends the cut text.
    """

    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        return None
    checked: list[ToolResultBlock] = []
    for block in blocks:
        if type(block) is ImageContent:
            checked.append(block)
        elif type(block) is TextContent and block.signature is None:
            checked.append(block)
        else:
            return None
    limit = min(cap, TOOL_RESULT_MAX_CHARS)
    if len(tool_result_text(checked)) <= limit:
        return tuple(checked)
    # Room for the marker and its newline is kept, so the text stays within
    # the bound.
    budget = limit - len(marker) - 1
    kept: list[ToolResultBlock] = []
    used = 0  # length of the kept text blocks joined by newlines
    texts = 0
    cut = False
    for block in checked:
        if not isinstance(block, TextContent):
            kept.append(block)
            continue
        if cut:
            continue
        separator = 1 if texts else 0
        if used + separator + len(block.text) > budget:
            room = max(budget - used - separator, 0)
            block = TextContent(block.text[:room] + marker)
            cut = True
        kept.append(block)
        used += separator + len(block.text)
        texts += 1
    return tuple(kept)


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
    structured_content: object = NO_STRUCTURED_CONTENT,
    tool_call_id: str = "",
    parent_tool_call_id: str | None = None,
    tool_input: Mapping[str, object] | None = None,
    details: Mapping[str, object] | None = None,
    blocks: tuple[ToolResultBlock, ...] = (),
) -> ToolResultTransform | None:
    """Run `tool_result` hooks over a finalized tool result (Pi `emitToolResult`).

    Each hook receives a `ToolResultEvent` with the result as the earlier
    hooks left it and may return a `ToolResultTransform` to change it for
    later hooks, the model and programmatic callers. Hooks run in
    registration order. A transform's `blocks` (Pi's content array) or
    `content` replace the whole content, images included; replaced content
    drops the structured content unless the transform carries one.
    `structured_content`, `is_error` and `details` replace their field when
    given. A hook that raises, or returns a transform with a field of the
    wrong kind, is fail-safe: the result stays as it was. Replaced text is
    bounded to `TOOL_RESULT_MAX_CHARS`; images are kept whole.
    `KeyboardInterrupt` / `SystemExit` propagate.

    Returns the result as the hooks left it, or `None` when no hook changed
    anything.
    """

    if not hooks:
        return None
    state = _ToolResultState(content, blocks, structured_content, is_error, details)
    arguments = dict(tool_input or {})
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
                    tool_name=tool_name,
                    content=state.content,
                    is_error=state.is_error,
                    structured_content=state.structured_content,
                    tool_call_id=tool_call_id,
                    parent_tool_call_id=parent_tool_call_id,
                    input=arguments,
                    details=state.details,
                    blocks=state.blocks,
                ),
                ctx,
            )
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - fail-safe: keep the result
            continue
        if isinstance(result, ToolResultTransform):
            state.apply(result)
    if not state.modified:
        return None
    return ToolResultTransform(
        content=state.content,
        structured_content=state.structured_content,
        blocks=state.blocks,
        is_error=state.is_error,
        details=state.details,
    )


_TOOL_RESULT_TRUNCATED = "\n[pipy: tool_result transform truncated]"


class _ToolResultState:
    """A result as the `tool_result` hooks have left it so far."""

    __slots__ = (
        "blocks",
        "content",
        "details",
        "is_error",
        "modified",
        "structured_content",
    )

    def __init__(
        self,
        content: str,
        blocks: tuple[ToolResultBlock, ...],
        structured_content: object,
        is_error: bool,
        details: Mapping[str, object] | None,
    ) -> None:
        self.content = content
        self.blocks = blocks
        self.structured_content = structured_content
        self.is_error = is_error
        self.details = details
        self.modified = False

    def apply(self, transform: ToolResultTransform) -> None:
        """Take what ``transform`` changes; nothing when a field it gives has
        the wrong kind or cannot be copied as JSON (Pi ``emitToolResult``)."""

        try:
            replaced, structured, details = self._prepared(transform)
        except (RecursionError, ValueError, TypeError):
            # A field of the wrong kind, or a value too deep (or otherwise
            # not JSON) to copy: the transform changes nothing.
            return
        if replaced is not None:
            self.content, self.blocks = replaced
            # Pi: structured content that is not replaced along with the
            # content may no longer match it.
            self.structured_content = NO_STRUCTURED_CONTENT
        if transform.structured_content is not NO_STRUCTURED_CONTENT:
            self.structured_content = structured
        if transform.is_error is not None:
            self.is_error = transform.is_error
        if details is not None:
            self.details = details
        self.modified = True

    def _prepared(
        self, transform: ToolResultTransform
    ) -> tuple[
        tuple[str, tuple[ToolResultBlock, ...]] | None,
        object,
        dict[str, Any] | None,
    ]:
        """The checked replacement content, structured content and details.
        Raises ``TypeError`` for a field of the wrong kind."""

        replaced = _replaced_content(transform)
        if transform.is_error is not None and type(transform.is_error) is not bool:
            raise TypeError("is_error must be a bool")
        details: dict[str, Any] | None = None
        if transform.details is not None:
            details = json_safe_details(transform.details)
            if details is None:
                raise TypeError("details must be a JSON object")
        given = transform.structured_content
        # The event's own value handed back is kept as it is; any other is a
        # detached JSON copy, and a value that is not JSON is dropped.
        structured = (
            given
            if given is NO_STRUCTURED_CONTENT or given is self.structured_content
            else json_safe_value(given)
        )
        return replaced, structured, details


def _replaced_content(
    transform: ToolResultTransform,
) -> tuple[str, tuple[ToolResultBlock, ...]] | None:
    """The text and blocks a transform puts in place of the content, bounded;
    ``None`` when it leaves the content. Raises ``TypeError`` when its
    ``blocks`` or ``content`` are of the wrong kind."""

    if transform.blocks is not None:
        bounded = bounded_result_blocks(
            transform.blocks, TOOL_RESULT_MAX_CHARS, _TOOL_RESULT_TRUNCATED
        )
        if bounded is None:
            raise TypeError("blocks must be text and image blocks")
        # Blocks without an image say no more than their text.
        return tool_result_text(bounded), (
            bounded if tool_result_images(bounded) else ()
        )
    if transform.content is None:
        return None
    if not isinstance(transform.content, str):
        raise TypeError("content must be a string")
    text = transform.content
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + _TOOL_RESULT_TRUNCATED
    return text, ()


def dispatch_lifecycle_hooks(
    hooks: Sequence[HookHandler],
    event: LifecycleEvent | McpServersChangeEvent,
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
        generation_snapshot: Callable[[], "SessionGenerationSnapshot"],
        cwd: str,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        project_trusted: bool = False,
    ) -> None:
        self._immediate_sink = immediate_sink
        self._generation_snapshot = generation_snapshot
        self._lifecycle_cwd = cwd
        self._lifecycle_has_ui = has_ui
        self._lifecycle_notify_sink = notify_sink
        self._lifecycle_ui_driver = ui_driver
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

    def fire_candidate_session_start(
        self,
        hooks: Mapping[str, tuple[HookHandler, ...]],
        flags: Mapping[str, object],
        *,
        ui_driver: ExtensionUiDriver | None,
    ) -> None:
        self._dispatch_lifecycle(
            hooks.get(EVENT_SESSION_START),
            LifecycleEvent(name=EVENT_SESSION_START, reason="reload"),
            ui_driver=ui_driver,
            flags=flags,
        )

    def mcp_servers_change_handled(self) -> bool:
        """Whether a loaded extension handles ``mcp_servers_change``, which
        means it connects the MCP servers extensions register."""

        projection = self._generation_snapshot().generation.projection
        return projection is not None and bool(
            projection.hooks.lifecycle.get(EVENT_MCP_SERVERS_CHANGE)
        )

    def fire_mcp_servers_change(self, servers: Sequence[RegisteredMcpServer]) -> None:
        """Tell the loaded extensions that the registered MCP servers
        changed; ``servers`` is every registration after the change."""

        self.fire_lifecycle(
            EVENT_MCP_SERVERS_CHANGE,
            event=McpServersChangeEvent(servers=tuple(servers)),
        )

    def fire_lifecycle(
        self,
        name: str,
        *,
        reason: str | None = None,
        event: LifecycleEvent | McpServersChangeEvent | None = None,
    ) -> None:
        snapshot = self._generation_snapshot()
        projection = snapshot.generation.projection
        if projection is None:
            raise RuntimeError("published extension generation has no projection")
        ui_driver = self._lifecycle_ui_driver
        chrome = projection.chrome
        bind = getattr(ui_driver, "generation_driver", None)
        if chrome is not None and callable(bind):
            ui_driver = bind(chrome.sink)
        self._dispatch_lifecycle(
            projection.hooks.lifecycle.get(name),
            LifecycleEvent(name=name, reason=reason) if event is None else event,
            ui_driver=ui_driver,
            flags=projection.runtime_flags.values,
        )

    def _dispatch_lifecycle(
        self,
        hooks: Sequence[HookHandler] | None,
        event: LifecycleEvent | McpServersChangeEvent,
        *,
        ui_driver: ExtensionUiDriver | None,
        flags: Mapping[str, object],
    ) -> None:
        if not hooks:
            return
        dispatch_lifecycle_hooks(
            hooks,
            event,
            cwd=self._lifecycle_cwd,
            has_ui=self._lifecycle_has_ui,
            notify_sink=self._lifecycle_notify_sink,
            ui_driver=ui_driver,
            flags=flags,
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
    tool_input: dict[str, object],
    tool_call_id: str,
    parent_tool_call_id: str | None = None,
    cwd: str,
    has_ui: bool,
    notify_sink: Callable[[str, str], None] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ToolBlock | None:
    """Run `tool_call` hooks for one tool call; return the first block.

    Each hook receives the same `ToolCallEvent` (tool name, validated input,
    call id and parent call id) and a mode-aware context. `tool_input` is
    handed over, not copied: what the hooks change in it is what the caller
    runs (Pi `emitToolCall`). The first hook to return a `ToolBlock` blocks
    the call; hooks returning anything else allow it. A hook that raises
    fails closed (blocks with a safe reason), since a policy gate that
    errors must not silently allow the action. `KeyboardInterrupt` /
    `SystemExit` propagate (user abort is never swallowed).
    """

    event = ToolCallEvent(
        tool_name=tool_name,
        input=tool_input,
        tool_call_id=tool_call_id,
        parent_tool_call_id=parent_tool_call_id,
    )
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
