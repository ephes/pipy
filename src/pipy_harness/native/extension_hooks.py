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

It depends only on the `_drive_awaitable` coroutine driver from
`extension_loader`, the hook value objects from `extension_types`, and the
`_CommandContext` / `_CollectingUi` builders plus the `EVENT_*` event-name
constants from `extension_runtime`. The dependency is one-way and
cycle-free: `extension_runtime` never imports back from this module.

The `before_agent_start` and `tool_result` injections are each bounded by
`_BEFORE_AGENT_START_MAX_CHARS` / `_TOOL_RESULT_MAX_CHARS` so a buggy or
malicious extension cannot create unbounded provider input.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Literal, cast

from pipy_harness.native.extension_loader import _drive_awaitable
from pipy_harness.native.extension_runtime import (
    EVENT_PROJECT_TRUST,
    EVENT_TOOL_CALL,
    ActivatedExtension,
    ExtensionUiDriver,
    HookHandler,
    _CollectingUi,
    _CommandContext,
    _ConversationView,
)
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent,
    BeforeAgentStartResult,
    BeforeProviderHeadersEvent,
    BeforeProviderRequestEvent,
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
    _safe_diagnostic,
)

if False:  # pragma: no cover - imported for type checkers only
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
            _ConversationView(getattr(request, "messages", ())),
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
        flags=flags,
        session_tree=session_tree,
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
