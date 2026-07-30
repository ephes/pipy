"""Extension activation sandbox boundary + runtime dispatch surface.

This module imports an explicit, already-inventoried *loadable* extension
module (from `pipy_harness.native.extensions`), calls its `activate(api)` entry
point, and exposes the registered contributions to the live session. The
activation API supports command and keyboard-shortcut registration, event
hooks, tool and provider registration/unregistration, and `send_user_message`.

It is fail-closed per extension: an import error, a missing or non-callable
`activate`, an exception during activation, or an invalid / duplicate /
reserved command / tool / provider / shortcut registration disables that one
extension with a safe reason code — it never crashes the session and never lets
a bad extension take down the others. Disabled discovery descriptors are never
imported, and a partial registration set is never committed.

Command/shortcut handlers run with a mode-aware `CommandContext` (workspace
root, `ui` with `notify` + `custom` interactive overlays, a read-only
`conversation` view, and a bounded `complete`). Command output, handlers, and
source code never enter the default archive; project activation results through
`safe_activation_metadata`.

Public API (also re-exported from `pipy_harness.extensions`):

- `PipyExtensionAPI` — the activation-time API protocol.
- `RegisteredCommand` / `RegisteredShortcut` / `RegisteredTool` /
  `RegisteredProvider` / `ActivatedExtension` value objects.
- `activate_extensions(descriptors, *, reserved_command_names=(),
  reserved_tool_names=(), message_outbox=None)`.
- The command/shortcut/tool/provider collectors (`extension_command_map`,
  `extension_shortcuts`, `extension_tools`, ...) and dispatchers
  (`dispatch_extension_command`, `dispatch_extension_shortcut`), plus
  `safe_activation_metadata(activated)`. The per-turn hook collectors and
  dispatchers (`extension_event_hooks`, `dispatch_input_hooks`, the
  lifecycle/tool-call/tool-result families) live in
  `pipy_harness.native.extension_hooks`.
"""

from __future__ import annotations

import inspect
import json
import threading
from collections.abc import (
    Callable,
    Container,
    Iterable,
    Mapping,
    MutableMapping,
    Sequence,
)
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import (
    Literal,
    NoReturn,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)

from pipy_harness.native.extension_loader import (
    _import_entry_module,
    _run_awaitable,
)
from pipy_harness.native.extension_ui import (
    _CUSTOM_RENDER_MAX_CHARS,
    _CollectingUi as _CollectingUi,
    coerce_tool_render_lines as coerce_tool_render_lines,
    lines_component as lines_component,
)
from pipy_harness.native.extensions import ExtensionDescriptor
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent as BeforeAgentStartEvent,
    BeforeAgentStartResult as BeforeAgentStartResult,
    BeforeProviderHeadersEvent as BeforeProviderHeadersEvent,
    BeforeProviderRequestEvent as BeforeProviderRequestEvent,
    ChromeComponent as ChromeComponent,
    CompletionFn as CompletionFn,
    CustomComponent as CustomComponent,
    CustomComponentDriver,
    CustomComponentFactory as CustomComponentFactory,
    EntryRenderContext as EntryRenderContext,
    ExtensionCodingSessionControl as ExtensionCodingSessionControl,
    ExtensionFlag as ExtensionFlag,
    ExtensionModelRuntimeControl as ExtensionModelRuntimeControl,
    ExtensionOAuthConfig as ExtensionOAuthConfig,
    ExtensionProvider as ExtensionProvider,
    ExtensionTool as ExtensionTool,
    ExtensionUi as ExtensionUi,
    ExtensionUiDriver as ExtensionUiDriver,
    FooterData as FooterData,
    InputEvent as InputEvent,
    InputTransform as InputTransform,
    LifecycleEvent as LifecycleEvent,
    MessageRenderComponent as MessageRenderComponent,
    MessageRenderContext as MessageRenderContext,
    ProviderContext as ProviderContext,
    ProviderRequestTransform as ProviderRequestTransform,
    QueuedCustomMessage as QueuedCustomMessage,
    QueuedUserMessage as QueuedUserMessage,
    REASON_ACTIVATION_ERROR,
    REASON_DUPLICATE_COMMAND,
    REASON_DUPLICATE_ENTRY_RENDERER,
    REASON_DUPLICATE_FLAG,
    REASON_DUPLICATE_MESSAGE_RENDERER,
    REASON_DUPLICATE_PROVIDER,
    REASON_DUPLICATE_SHORTCUT,
    REASON_DUPLICATE_TOOL,
    REASON_INVALID_COMMAND_NAME,
    REASON_INVALID_ENTRY_RENDERER,
    REASON_INVALID_FLAG,
    REASON_INVALID_HOOK,
    REASON_INVALID_MESSAGE_RENDERER,
    REASON_INVALID_PROVIDER,
    REASON_INVALID_SHORTCUT,
    REASON_INVALID_TOOL,
    REASON_NO_ACTIVATE,
    REASON_RESERVED_COMMAND,
    REASON_RESERVED_SHORTCUT,
    REASON_RESERVED_TOOL as REASON_RESERVED_TOOL,
    RESERVED_SHORTCUT_KEYS as RESERVED_SHORTCUT_KEYS,
    RegisteredFlag as RegisteredFlag,
    RegisteredProvider as RegisteredProvider,
    RegisteredTool as RegisteredTool,
    RenderedCustomEntry as RenderedCustomEntry,
    SessionBeforeEvent as SessionBeforeEvent,
    SessionDecision as SessionDecision,
    ThemeColor as ThemeColor,
    ToolBlock as ToolBlock,
    ToolCallEvent as ToolCallEvent,
    ToolRenderComponent as ToolRenderComponent,
    ToolRenderContext as ToolRenderContext,
    ToolRenderTheme as ToolRenderTheme,
    ToolResult as ToolResult,
    ToolResultEvent as ToolResultEvent,
    ToolResultTransform as ToolResultTransform,
    UserBashDecision as UserBashDecision,
    UserBashDispatch as UserBashDispatch,
    UserBashEvent as UserBashEvent,
    WidgetPlacement as WidgetPlacement,
    _ActivationError,
    _is_valid_command_name,
    _safe_diagnostic,
    is_valid_custom_entry_type as is_valid_custom_entry_type,
    normalize_shortcut_key as normalize_shortcut_key,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

if False:  # pragma: no cover - imported for type checkers only
    from pipy_harness.native.session_tree import (
        CustomEntry as _CustomEntry,
        CustomMessageEntry as _CustomMessageEntry,
        NativeSessionTree,
        SessionEntry,
        SessionHeader,
        SessionTreeNode,
    )

CommandHandler = Callable[..., object]
ToolRenderDetails: TypeAlias = Mapping[str, object] | None
ToolRenderDetailsSink: TypeAlias = MutableMapping[str, object | None]


class ToolRenderDetailsWriter(Protocol):
    """Write-only side of the render-details handoff."""

    def __setitem__(
        self, correlation_id: str, details: ToolRenderDetails, /
    ) -> None: ...


# Bound an extension tool's provider-visible output.
_TOOL_OUTPUT_MAX_CHARS: int = 32 * 1024

# Event names (the dispatched subset grows per slice).
EVENT_TOOL_CALL: str = "tool_call"
EVENT_PROJECT_TRUST: str = "project_trust"
EVENT_SESSION_START: str = "session_start"
EVENT_SESSION_SHUTDOWN: str = "session_shutdown"
EVENT_AGENT_START: str = "agent_start"
EVENT_AGENT_END: str = "agent_end"
EVENT_AGENT_SETTLED: str = "agent_settled"
EVENT_TURN_START: str = "turn_start"
EVENT_TURN_END: str = "turn_end"
EVENT_INPUT: str = "input"
EVENT_BEFORE_AGENT_START: str = "before_agent_start"
EVENT_TOOL_RESULT: str = "tool_result"
EVENT_USER_BASH: str = "user_bash"
EVENT_BEFORE_PROVIDER_REQUEST: str = "before_provider_request"
EVENT_BEFORE_PROVIDER_HEADERS: str = "before_provider_headers"
EVENT_SESSION_BEFORE_SWITCH: str = "session_before_switch"
EVENT_SESSION_BEFORE_FORK: str = "session_before_fork"
EVENT_SESSION_BEFORE_COMPACT: str = "session_before_compact"
EVENT_SESSION_BEFORE_TREE: str = "session_before_tree"

# Bound custom extension-rendered session entry text and data. Product native
# sessions intentionally store full user-visible content, but extension payloads
# should still be JSON-safe and capped so a bad renderer cannot grow the TUI or
# session file without bound.
_CUSTOM_ENTRY_DATA_MAX_CHARS: int = 64 * 1024

LIFECYCLE_EVENTS: tuple[str, ...] = (
    EVENT_SESSION_START,
    EVENT_SESSION_SHUTDOWN,
    EVENT_AGENT_START,
    EVENT_AGENT_END,
    EVENT_AGENT_SETTLED,
    EVENT_TURN_START,
    EVENT_TURN_END,
)

HookHandler = Callable[..., object]


def make_extension_context(
    cwd: str,
    has_ui: bool,
    notify_sink: "Callable[[str, str], None] | None" = None,
    *,
    coding_session: "ExtensionCodingSessionControl | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    project_trusted: bool = False,
) -> CommandContext:
    """Build a mode-aware context for a tool/command/hook invocation.

    When `notify_sink` is given, `ctx.ui.notify` routes to it (live UI
    output) in addition to recording; otherwise notifications are only
    recorded (deterministic non-interactive behavior). `coding_session`, when
    given, backs the coding-session-facing surface: its `messages` snapshot
    backs `ctx.conversation`, its `session_tree` backs `ctx.session_manager`,
    and its capability callables back `ctx.complete` / `ctx.append_entry` /
    session-name / label / custom-message.
    """

    return _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink, ui_driver=ui_driver),
        coding_session,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )


class _ExtensionToolPort:
    """Adapt an extension `RegisteredTool` to the native `ToolPort`.

    The loop validates arguments against `definition.input_schema` before
    `invoke`, so the handler receives already-validated input. A handler
    exception becomes a bounded tool error (never a session crash), and
    the provider-visible output is bounded. `KeyboardInterrupt` /
    `SystemExit` propagate.

    Trust model (see the extension-api spec "Local trust boundary"):
    extension tool handlers are trusted local Python that runs in-process
    with the user's own OS permissions — the same trust level as the
    extension's `activate()` function. There is no in-process sandbox, so
    "read-only / pure" is the *documented convention* for this slice, not
    a runtime guarantee; capability *enforcement* (shell / network / write
    permission gates derived from the manifest `[permissions]` table) is a
    later, explicitly-scoped permission-policy slice. What pipy does
    enforce here is the provider boundary: schema-validated input, bounded
    output, and bounded errors.
    """

    def __init__(
        self,
        registered: RegisteredTool,
        *,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        set_active_tools_fn: Callable[[Sequence[str]], bool] | None = None,
        flags: Mapping[str, object] | None = None,
        render_details_sink: ToolRenderDetailsWriter | None = None,
        project_trusted: bool = False,
    ) -> None:
        self._registered = registered
        self._has_ui = has_ui
        self._notify_sink = notify_sink
        self._set_active_tools_fn = set_active_tools_fn
        self._flags = dict(flags or {})
        self._render_details_sink = render_details_sink
        self._project_trusted = bool(project_trusted)
        tool = registered.tool
        self._definition = ToolDefinition(
            name=tool.name,
            description=str(tool.description),
            input_schema=dict(tool.input_schema),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        ctx = make_extension_context(
            str(context.workspace_root),
            self._has_ui,
            self._notify_sink,
            model_runtime=ExtensionModelRuntimeControl(
                set_active_tools_fn=self._set_active_tools_fn
            ),
            flags=self._flags,
            project_trusted=self._project_trusted,
        )
        try:
            result = self._registered.tool.handler(ctx, dict(request.arguments))
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - bound a bad tool
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=f"extension tool error: {type(err).__name__}",
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        if isinstance(result, ToolResult) and isinstance(result.content, str):
            content = result.content
        elif isinstance(result, ToolResult):
            content = str(result.content)
        else:
            content = str(result)
        cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        if len(content) > cap:
            content = content[: cap - 64] + "\n[pipy: extension tool output truncated]"
        if (
            self._render_details_sink is not None
            and self._registered.tool.render_result is not None
            and request.provider_correlation_id is not None
        ):
            details = result.details if isinstance(result, ToolResult) else None
            self._render_details_sink[request.provider_correlation_id] = (
                dict(details) if isinstance(details, Mapping) else None
            )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=content,
            is_error=False,
            provider_correlation_id=request.provider_correlation_id,
        )


ActivationStatus = Literal["activated", "disabled"]


class PipyExtensionAPI(Protocol):
    """The activation-time API handed to an extension's `activate`.

    Supports command and keyboard-shortcut registration (`register_command`,
    `register_shortcut`), event hooks (`on`), tool and provider registration
    (`register_tool`, `register_provider` / `unregister_provider`), and
    `send_user_message`. Each registration is validated eagerly and committed
    only if `activate` completes without error.
    """

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None: ...

    def register_shortcut(self, key: str, handler: CommandHandler) -> None: ...

    def on(
        self,
        event: str,
        handler: HookHandler | None = None,
    ) -> object: ...

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def register_tool(self, tool: "ExtensionTool") -> None: ...

    def register_provider(self, provider: "ExtensionProvider") -> None: ...

    def unregister_provider(self, name: str) -> None: ...

    def register_flag(self, flag: "ExtensionFlag") -> None: ...

    def get_flag(self, name: str) -> object | None: ...

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    """One command an extension registered during activation."""

    name: str
    description: str
    handler: CommandHandler
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredMessageRenderer:
    """A renderer for extension custom messages of one type."""

    custom_type: str
    renderer: Callable[..., object]
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredEntryRenderer:
    """A TUI renderer for durable extension custom entries of one type."""

    custom_type: str
    renderer: Callable[..., object]
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredShortcut:
    """One keyboard shortcut an extension bound during activation.

    `key` is the normalized pipy key string (e.g. ``"ctrl-."``). `handler` has
    the same shape as a command handler (`handler(ctx, args)`); a shortcut
    always dispatches it with an empty argument string.
    """

    key: str
    handler: CommandHandler
    extension: str


_EMPTY_HOOKS: Mapping[str, tuple[HookHandler, ...]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ActivatedExtension:
    """The outcome of attempting to activate one extension.

    `status` is `"activated"` when `activate(api)` completed and all its
    command registrations were accepted, or `"disabled"` with a safe
    `reason` code otherwise. `commands` is empty for any disabled
    extension (a partial registration is never committed). `diagnostic`
    is a safe, bounded label; it never contains source code, secrets, or
    full tracebacks.
    """

    name: str
    version: str
    path_label: str
    status: ActivationStatus
    reason: str | None
    commands: tuple[RegisteredCommand, ...]
    diagnostic: str | None
    hooks: Mapping[str, tuple[HookHandler, ...]] = field(
        default_factory=lambda: _EMPTY_HOOKS
    )
    tools: tuple[RegisteredTool, ...] = ()
    providers: tuple[RegisteredProvider, ...] = ()
    unregistered_providers: tuple[str, ...] = ()
    shortcuts: tuple[RegisteredShortcut, ...] = ()
    flags: tuple[RegisteredFlag, ...] = ()
    message_renderers: tuple[RegisteredMessageRenderer, ...] = ()
    entry_renderers: tuple[RegisteredEntryRenderer, ...] = ()
    custom_messages: tuple[QueuedCustomMessage, ...] = ()
    _activation_key: str | None = field(default=None, repr=False, compare=False)
    # Pending activation is a one-shot ownership token. Finalized candidates
    # retain the host separately so the old pending/uncommitted sentinel never
    # acquires a second meaning.
    _pending_activation: "_PendingActivation | None" = field(
        default=None, repr=False, compare=False
    )
    _activation_host: "_ActivationApi | None" = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Freeze the hook map for activated, disabled, and passthrough results."""

        object.__setattr__(self, "hooks", MappingProxyType(dict(self.hooks)))


@dataclass(slots=True)
class ExtensionActivationBatch:
    """One reusable extension activation pass and its shared live outboxes."""

    activated: tuple[ActivatedExtension, ...]
    message_outbox: list[QueuedUserMessage]
    custom_message_outbox: list[QueuedCustomMessage]
    pending: bool = False


@dataclass(frozen=True, slots=True)
class _ExtensionRuntime:
    """The activated-extension contributions wired into one session run."""

    commands: dict[str, RegisteredCommand]
    menu_names: tuple[str, ...]
    descriptions: dict[str, str]
    tool_call_hooks: tuple[HookHandler, ...]
    lifecycle_hooks: dict[str, tuple[HookHandler, ...]]
    input_hooks: tuple[HookHandler, ...]
    before_agent_start_hooks: tuple[HookHandler, ...]
    tool_result_hooks: tuple[HookHandler, ...]
    user_bash_hooks: tuple[HookHandler, ...]
    before_provider_headers_hooks: tuple[HookHandler, ...]
    before_provider_request_hooks: tuple[HookHandler, ...]
    session_before_switch_hooks: tuple[HookHandler, ...]
    session_before_fork_hooks: tuple[HookHandler, ...]
    session_before_compact_hooks: tuple[HookHandler, ...]
    session_before_tree_hooks: tuple[HookHandler, ...]
    outbox: list[QueuedUserMessage]
    custom_outbox: list[QueuedCustomMessage]
    tools: tuple[RegisteredTool, ...]
    shortcuts: dict[str, RegisteredShortcut]
    flags: tuple[RegisteredFlag, ...]
    providers: tuple[RegisteredProvider, ...]
    unregistered_providers: tuple[str, ...]
    message_renderers: dict[str, RegisteredMessageRenderer]
    entry_renderers: dict[str, RegisteredEntryRenderer]
    custom_messages: tuple[QueuedCustomMessage, ...]
    activation_hosts: tuple["_ActivationApi", ...]


@dataclass(frozen=True, slots=True)
class _ContributionNames:
    """Accepted contribution names, grouped in collision-check order."""

    commands: tuple[str, ...]
    tools: tuple[str, ...]
    providers: tuple[str, ...]
    shortcuts: tuple[str, ...]
    flags: tuple[str, ...]
    message_renderers: tuple[str, ...]
    entry_renderers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TakenContributionState:
    """One immutable reservation snapshot for an activation pass."""

    commands: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    shortcuts: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()
    message_renderers: frozenset[str] = frozenset()
    entry_renderers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _FrozenActivation:
    """One atomic, immutable view of a sealed activation host."""

    commands: tuple[RegisteredCommand, ...]
    tools: tuple[RegisteredTool, ...]
    providers: tuple[RegisteredProvider, ...]
    unregistered_providers: tuple[str, ...]
    shortcuts: tuple[RegisteredShortcut, ...]
    flags: tuple[RegisteredFlag, ...]
    message_renderers: tuple[RegisteredMessageRenderer, ...]
    entry_renderers: tuple[RegisteredEntryRenderer, ...]
    hooks: Mapping[str, tuple[HookHandler, ...]]
    user_messages: tuple[QueuedUserMessage, ...]
    custom_messages: tuple[QueuedCustomMessage, ...]
    failure: tuple[str, str | None] | None


@dataclass(frozen=True, slots=True)
class _NormalizedFlagRegistration:
    """A normalized flag definition and its optional initial value."""

    registered: RegisteredFlag
    default: bool | str | None


_RegistrationFamily: TypeAlias = Literal[
    "command",
    "shortcut",
    "tool",
    "provider",
    "flag",
    "message_renderer",
    "entry_renderer",
]
_RegistrationValue: TypeAlias = (
    RegisteredCommand
    | RegisteredShortcut
    | RegisteredTool
    | RegisteredProvider
    | _NormalizedFlagRegistration
    | RegisteredMessageRenderer
    | RegisteredEntryRenderer
)
_RegistrationOrdering: TypeAlias = Literal[
    "availability_before_value",
    "value_before_availability",
]
_RegistrationResult = TypeVar("_RegistrationResult")

_REGISTRATION_INVALID_REASONS: Mapping[_RegistrationFamily, str] = MappingProxyType(
    {
        "command": REASON_INVALID_COMMAND_NAME,
        "shortcut": REASON_INVALID_SHORTCUT,
        "tool": REASON_INVALID_TOOL,
        "provider": REASON_INVALID_PROVIDER,
        "flag": REASON_INVALID_FLAG,
        "message_renderer": REASON_INVALID_MESSAGE_RENDERER,
        "entry_renderer": REASON_INVALID_ENTRY_RENDERER,
    }
)
_REGISTRATION_ORDERING: Mapping[_RegistrationFamily, _RegistrationOrdering] = (
    MappingProxyType(
        {
            "command": "availability_before_value",
            "shortcut": "availability_before_value",
            "tool": "availability_before_value",
            "provider": "value_before_availability",
            "flag": "availability_before_value",
            "message_renderer": "value_before_availability",
            "entry_renderer": "value_before_availability",
        }
    )
)


@dataclass(frozen=True, slots=True)
class _ProviderCatalogFinalization:
    """Bounded outcome from terminally finalizing accepted catalog hosts."""

    finalized: int = 0
    refused_disposed: int = 0
    refused_published: int = 0
    refused_already_terminal: int = 0
    inaccessible: int = 0

    @property
    def anomaly_diagnostic(self) -> str | None:
        if not (
            self.refused_disposed
            or self.refused_published
            or self.refused_already_terminal
            or self.inaccessible
        ):
            return None
        return (
            "pipy: extension provider catalog finalization anomalies: "
            f"{self.refused_disposed} refused host(s) disposed, "
            f"{self.refused_published} published host(s) skipped live, "
            f"{self.refused_already_terminal} refused host(s) already terminal, "
            f"and {self.inaccessible} inaccessible/failing host guard(s)."
        )


class _PendingActivation:
    """Minimum one-shot holder used before a pre-trust host is composed."""

    def __init__(self, host: "_ActivationApi") -> None:
        self._host: _ActivationApi | None = host

    def claim(self) -> "_ActivationApi | None":
        """Transfer this session-thread-owned pending host exactly once."""

        host = self._host
        self._host = None
        return host

    def dispose(self) -> "_ActivationCleanup":
        """Dispose an unclaimed host, retaining it only when cleanup cannot enter."""

        host = self._host
        if host is None:
            return _ActivationCleanup()
        cleanup = _dispose_activation_hosts((host,))
        if cleanup.failed == 0:
            self._host = None
        return cleanup


_ACTIVATION_LIFECYCLE_TOKEN = object()
_ACTIVATION_PUBLICATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ActivationCleanup:
    """Structured result from bounded per-host candidate cleanup."""

    disposed: int = 0
    skipped_published: int = 0
    failed: int = 0

    def merge(self, other: "_ActivationCleanup") -> "_ActivationCleanup":
        return _ActivationCleanup(
            disposed=self.disposed + other.disposed,
            skipped_published=self.skipped_published + other.skipped_published,
            failed=self.failed + other.failed,
        )

    @property
    def anomaly_diagnostic(self) -> str | None:
        if not self.skipped_published and not self.failed:
            return None
        return (
            "pipy: extension candidate cleanup skipped "
            f"{self.skipped_published} published and {self.failed} inaccessible "
            "activation host(s)."
        )


def _report_activation_cleanup(
    cleanup: _ActivationCleanup,
    diagnostic: Callable[[str], None] | None,
) -> None:
    """Route cleanup anomalies through an existing diagnostic sink when present."""

    message = cleanup.anomaly_diagnostic
    if diagnostic is not None and message is not None:
        diagnostic(message)


class ExtensionCapabilityError(RuntimeError):
    """A capability a handler asked for is not available in this context.

    Raised by e.g. `ctx.complete(...)` when no completion backend is wired
    (a deterministic / non-interactive dispatch), so a handler degrades
    predictably instead of crashing on a missing attribute.
    """


@dataclass(frozen=True, slots=True)
class AssistantMessageView:
    """Read-only view of an assistant message handed to a command handler.

    `text` is the assistant message content. `complete` is True when it was a
    finished text answer — i.e. it carries text and left no tool calls pending
    (the pipy analog of Pi's `stopReason === "stop"`). A handler that wants the
    last *complete* answer (e.g. to extract questions from it) checks `complete`
    before using `text`.
    """

    text: str
    complete: bool


@runtime_checkable
class ConversationView(Protocol):
    """Read-only view of the live conversation handed to a command handler."""

    def last_assistant_message(self) -> "AssistantMessageView | None": ...


@dataclass(frozen=True, slots=True)
class SessionHeaderView:
    """Read-only extension view of the active native session header."""

    id: str | None = None
    timestamp: str | None = None
    cwd: str | None = None
    version: int | None = None
    parent_session: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "cwd": self.cwd,
            "version": self.version,
            "parentSession": self.parent_session,
        }


@dataclass(frozen=True, slots=True)
class SessionEntryView:
    """Immutable, JSON-like extension view of one native session entry."""

    id: str
    parent_id: str | None
    timestamp: str
    type: str
    data: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "id": self.id,
            "parentId": self.parent_id,
            "timestamp": self.timestamp,
            "type": self.type,
        }
        body.update(dict(self.data))
        return body


@dataclass(frozen=True, slots=True)
class SessionTreeNodeView:
    """Read-only extension view of a session tree node."""

    entry: SessionEntryView
    children: tuple["SessionTreeNodeView", ...] = ()
    label: str | None = None
    label_timestamp: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "entry": self.entry.to_dict(),
            "children": [child.to_dict() for child in self.children],
            "label": self.label,
            "labelTimestamp": self.label_timestamp,
        }


@runtime_checkable
class SessionManagerView(Protocol):
    """Read-only Pi-shaped session-manager view for extension contexts."""

    def get_cwd(self) -> str | None: ...
    def get_session_dir(self) -> str | None: ...
    def get_session_id(self) -> str | None: ...
    def get_session_file(self) -> str | None: ...
    def get_leaf_id(self) -> str | None: ...
    def get_leaf_entry(self) -> SessionEntryView | None: ...
    def get_entry(self, entry_id: str) -> SessionEntryView | None: ...
    def get_label(self, entry_id: str) -> str | None: ...
    def get_branch(
        self, from_id: str | None = None
    ) -> tuple[SessionEntryView, ...]: ...
    def get_header(self) -> SessionHeaderView: ...
    def get_entries(self) -> tuple[SessionEntryView, ...]: ...
    def get_tree(self) -> tuple[SessionTreeNodeView, ...]: ...
    def get_session_name(self) -> str | None: ...


@runtime_checkable
class CommandContext(Protocol):
    """Context passed to an extension command handler.

    Carries the workspace root, whether interactive UI is available, the `ui`
    capability, and a read-only `conversation` view (the last assistant
    message). It grows (model info, cancellation, system-prompt access) in
    later slices.
    """

    cwd: str
    has_ui: bool
    ui: ExtensionUi
    conversation: ConversationView
    session_manager: SessionManagerView
    sessionManager: SessionManagerView
    flags: Mapping[str, object]

    def is_project_trusted(self) -> bool: ...
    def isProjectTrusted(self) -> bool: ...

    def complete(self, system_prompt: str, user_text: str) -> str:
        """Run one bounded provider completion and return its text.

        Raises `ExtensionCapabilityError` when no completion backend is wired
        (a non-interactive / deterministic dispatch).
        """
        ...

    def set_active_tools(self, tool_names: Sequence[str]) -> bool:
        """Restrict the active model-visible tools for later provider turns."""
        ...

    def set_model(self, reference: str) -> bool:
        """Switch the active model/provider selection by reference."""
        ...

    def set_thinking_level(self, level: str) -> bool:
        """Set the active thinking level for later provider turns."""
        ...

    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        """Append a custom entry to the active product session tree."""
        ...

    def set_session_name(self, name: str | None) -> object: ...
    def setSessionName(self, name: str | None) -> object: ...
    def get_session_name(self) -> str | None: ...
    def getSessionName(self) -> str | None: ...
    def set_label(self, entry_id: str, label: str | None) -> object: ...
    def setLabel(self, entry_id: str, label: str | None) -> object: ...
    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object: ...
    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object: ...


class _ConversationView:
    """Concrete `ConversationView` over a snapshot of the message history.

    The handler receives a snapshot taken at dispatch time; it never mutates
    the live conversation. Messages without an assistant turn yield `None`.
    """

    def __init__(self, messages: "Sequence[object]" = ()) -> None:
        self._messages = tuple(messages)

    def last_assistant_message(self) -> "AssistantMessageView | None":
        # Import here to avoid a heavy import at module load.
        from pipy_harness.native.agent import AgentAssistantMessage

        for message in reversed(self._messages):
            if isinstance(message, AgentAssistantMessage):
                text = message.content.value
                complete = bool(text.strip()) and not message.tool_calls
                return AssistantMessageView(text=text, complete=complete)
        return None


def _validated_json_value(value: object) -> object:
    """Validate and detach one value produced by the stdlib JSON decoder."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        detached_items: list[object] = []
        for item in value:
            detached_items.append(_validated_json_value(item))
        return detached_items
    if isinstance(value, dict):
        detached_mapping: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object key is not a string")
            detached_mapping[key] = _validated_json_value(item)
        return detached_mapping
    raise ValueError("JSON decoder produced an unsupported value")


def _json_round_trip(value: object) -> tuple[str, object]:
    """Encode, decode, and executably narrow a JSON-compatible value."""

    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return encoded, _validated_json_value(json.loads(encoded))


def _copy_session_data(value: object) -> object:
    try:
        _encoded, decoded = _json_round_trip(value)
        return decoded
    except (TypeError, ValueError):
        return str(value)


def _session_header_view(header: "SessionHeader") -> SessionHeaderView:
    return SessionHeaderView(
        id=header.id,
        timestamp=header.timestamp,
        cwd=header.cwd,
        version=header.version,
        parent_session=header.parent_session,
    )


def _session_entry_view(entry: "SessionEntry") -> SessionEntryView:
    from pipy_harness.native.session_tree import _entry_to_json

    body = _entry_to_json(entry)
    data = {
        key: _copy_session_data(value)
        for key, value in body.items()
        if key not in {"id", "parentId", "timestamp", "type"}
    }
    parent_id = body.get("parentId")
    return SessionEntryView(
        id=str(body["id"]),
        parent_id=parent_id if isinstance(parent_id, str) else None,
        timestamp=str(body.get("timestamp", "")),
        type=str(body.get("type", "")),
        data=MappingProxyType(data),
    )


def _session_tree_node_view(node: "SessionTreeNode") -> SessionTreeNodeView:
    return SessionTreeNodeView(
        entry=_session_entry_view(node.entry),
        children=tuple(_session_tree_node_view(child) for child in node.children),
        label=node.label,
        label_timestamp=node.label_timestamp,
    )


class _ReadOnlySessionManagerView:
    """Read-only adapter from `NativeSessionTree` to extension context."""

    def __init__(self, tree: "NativeSessionTree | None" = None) -> None:
        self._tree = tree

    def get_cwd(self) -> str | None:
        return self._tree.header.cwd if self._tree is not None else None

    def get_session_dir(self) -> str | None:
        if self._tree is None or self._tree.path is None:
            return None
        return str(self._tree.path.parent)

    def get_session_id(self) -> str | None:
        return self._tree.session_id if self._tree is not None else None

    def get_session_file(self) -> str | None:
        if self._tree is None or self._tree.path is None:
            return None
        return str(self._tree.path)

    def get_leaf_id(self) -> str | None:
        return self._tree.get_leaf_id() if self._tree is not None else None

    def get_leaf_entry(self) -> SessionEntryView | None:
        if self._tree is None:
            return None
        entry = self._tree.get_leaf_entry()
        return _session_entry_view(entry) if entry is not None else None

    def get_entry(self, entry_id: str) -> SessionEntryView | None:
        if self._tree is None:
            return None
        entry = self._tree.get_entry(str(entry_id))
        return _session_entry_view(entry) if entry is not None else None

    def get_label(self, entry_id: str) -> str | None:
        return self._tree.get_label(str(entry_id)) if self._tree is not None else None

    def get_branch(self, from_id: str | None = None) -> tuple[SessionEntryView, ...]:
        if self._tree is None:
            return ()
        return tuple(
            _session_entry_view(entry) for entry in self._tree.get_branch(from_id)
        )

    def get_header(self) -> SessionHeaderView:
        if self._tree is None:
            return SessionHeaderView()
        return _session_header_view(self._tree.get_header())

    def get_entries(self) -> tuple[SessionEntryView, ...]:
        if self._tree is None:
            return ()
        return tuple(_session_entry_view(entry) for entry in self._tree.get_entries())

    def get_tree(self) -> tuple[SessionTreeNodeView, ...]:
        if self._tree is None:
            return ()
        return tuple(_session_tree_node_view(node) for node in self._tree.get_tree())

    def get_session_name(self) -> str | None:
        return self._tree.name if self._tree is not None else None


class _CommandContext:
    """Concrete `CommandContext` for one command invocation."""

    def __init__(
        self,
        cwd: str,
        ui: _CollectingUi,
        coding_session: "ExtensionCodingSessionControl | None" = None,
        *,
        model_runtime: "ExtensionModelRuntimeControl | None" = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> None:
        self.cwd = cwd
        self.has_ui = ui.has_ui
        self.ui: ExtensionUi = ui
        session = coding_session or ExtensionCodingSessionControl()
        self.conversation: ConversationView = _ConversationView(session.messages)
        self.session_manager: SessionManagerView = _ReadOnlySessionManagerView(
            session.session_tree
        )
        self.sessionManager: SessionManagerView = self.session_manager
        self.flags: Mapping[str, object] = dict(flags or {})
        self._project_trusted = bool(project_trusted)
        self._coding_session = session
        self._model_runtime = model_runtime or ExtensionModelRuntimeControl()

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def isProjectTrusted(self) -> bool:  # noqa: N802 - Pi-shaped alias
        return self.is_project_trusted()

    def complete(self, system_prompt: str, user_text: str) -> str:
        if self._coding_session.complete_fn is None:
            raise ExtensionCapabilityError(
                "completion is not available in this context"
            )
        return self._coding_session.complete_fn(str(system_prompt), str(user_text))

    def set_active_tools(self, tool_names: Sequence[str]) -> bool:
        if self._model_runtime.set_active_tools_fn is None:
            raise ExtensionCapabilityError(
                "active-tool control is not available in this context"
            )
        return self._model_runtime.set_active_tools_fn(
            tuple(str(name) for name in tool_names)
        )

    def set_model(self, reference: str) -> bool:
        if self._model_runtime.set_model_fn is None:
            raise ExtensionCapabilityError(
                "model control is not available in this context"
            )
        return self._model_runtime.set_model_fn(str(reference))

    def set_thinking_level(self, level: str) -> bool:
        if self._model_runtime.set_thinking_level_fn is None:
            raise ExtensionCapabilityError(
                "thinking-level control is not available in this context"
            )
        return self._model_runtime.set_thinking_level_fn(str(level))

    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        if self._coding_session.append_entry_fn is None:
            raise ExtensionCapabilityError(
                "custom session entries are not available in this context"
            )
        name = str(custom_type).strip()
        if not is_valid_custom_entry_type(name):
            raise ValueError("invalid custom entry type")
        return self._coding_session.append_entry_fn(name, data)

    def set_session_name(self, name: str | None) -> object:
        if self._coding_session.set_session_name_fn is None:
            raise ExtensionCapabilityError(
                "session-name mutation is not available in this context"
            )
        return self._coding_session.set_session_name_fn(
            None if name is None else str(name)
        )

    def setSessionName(self, name: str | None) -> object:
        return self.set_session_name(name)

    def get_session_name(self) -> str | None:
        if self._coding_session.get_session_name_fn is None:
            return None
        return self._coding_session.get_session_name_fn()

    def getSessionName(self) -> str | None:
        return self.get_session_name()

    def set_label(self, entry_id: str, label: str | None) -> object:
        if self._coding_session.set_label_fn is None:
            raise ExtensionCapabilityError(
                "session-label mutation is not available in this context"
            )
        return self._coding_session.set_label_fn(
            str(entry_id), None if label is None else str(label)
        )

    def setLabel(self, entry_id: str, label: str | None) -> object:
        return self.set_label(entry_id, label)

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object:
        if self._coding_session.send_message_fn is None:
            raise ExtensionCapabilityError(
                "custom messages are not available in this context"
            )
        queued = coerce_custom_message(message, options)
        return self._coding_session.send_message_fn(
            queued.custom_type,
            queued.content,
            queued.display,
            queued.options,
            queued.details,
        )

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object:
        return self.send_message(message, options)


@dataclass(frozen=True, slots=True)
class ExtensionCommandDispatch:
    """Outcome of dispatching one extension `/command`.

    `ran` is True when the handler completed; `error` carries a safe,
    bounded label (exception type name only) when it raised. `messages`
    are the `(kind, text)` notifications the handler emitted, for the
    caller to render as live UI output. No provider turn is implied.
    """

    name: str
    ran: bool
    error: str | None
    messages: tuple[tuple[str, str], ...]


def extension_command_map(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredCommand]:
    """Build a `name -> RegisteredCommand` map from activated extensions.

    Only `activated` extensions contribute; a name registered by an
    earlier extension wins (duplicates were already disabled during
    activation, so this is deterministic).
    """

    command_map: dict[str, RegisteredCommand] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for command in extension.commands:
            command_map.setdefault(command.name, command)
    return command_map


def extension_shortcuts(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredShortcut]:
    """Build a `key -> RegisteredShortcut` map from activated extensions.

    Only `activated` extensions contribute; a key bound by an earlier
    extension wins (duplicate keys were already disabled during activation,
    so this is deterministic).
    """

    shortcut_map: dict[str, RegisteredShortcut] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for shortcut in extension.shortcuts:
            shortcut_map.setdefault(shortcut.key, shortcut)
    return shortcut_map


def dispatch_extension_command(
    command_text: str,
    command_map: dict[str, RegisteredCommand],
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ExtensionCommandDispatch | None:
    """Dispatch `command_text` to an extension command, or return None.

    Returns None when `command_text` is not a `/<name>` form or names no
    registered extension command, so the caller falls through to its
    normal handling (built-ins run earlier, so extensions can never
    shadow them). When it matches, the handler runs locally with a
    mode-aware context and the raw argument string; it triggers no
    provider turn. A handler exception is bounded into a safe `error`.
    """

    if not command_text.startswith("/"):
        return None
    body = command_text[1:]
    # Split only on the first space: the command name, then the raw
    # argument string verbatim (intentional leading/trailing whitespace
    # is preserved, per the handler contract).
    name, _, args = body.partition(" ")
    command = command_map.get(name)
    if command is None:
        return None

    return _run_extension_handler(
        name,
        command.handler,
        args,
        cwd=cwd,
        has_ui=has_ui,
        coding_session=coding_session,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
        ui_driver=ui_driver,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )


def dispatch_extension_shortcut(
    key: str,
    shortcut_map: dict[str, RegisteredShortcut],
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ExtensionCommandDispatch | None:
    """Dispatch a registered extension shortcut `key`, or return None.

    Returns None when `key` (already normalized by the caller) names no
    registered shortcut. When it matches, the bound handler runs locally with
    the same mode-aware context as a command and an empty argument string; it
    triggers no provider turn and a handler exception is bounded into a safe
    `error`.
    """

    shortcut = shortcut_map.get(normalize_shortcut_key(key))
    if shortcut is None:
        return None
    return _run_extension_handler(
        shortcut.key,
        shortcut.handler,
        "",
        cwd=cwd,
        has_ui=has_ui,
        coding_session=coding_session,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
        ui_driver=ui_driver,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )


def _run_extension_handler(
    name: str,
    handler: CommandHandler,
    args: str,
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None",
    notify_sink: "Callable[[str, str], None] | None",
    ui_custom_driver: "CustomComponentDriver | None",
    ui_driver: "ExtensionUiDriver | None",
    model_runtime: "ExtensionModelRuntimeControl | None",
    flags: Mapping[str, object] | None,
    project_trusted: bool,
) -> ExtensionCommandDispatch:
    """Run a command/shortcut handler with a mode-aware context; bound errors."""

    ui = _CollectingUi(has_ui, notify_sink, ui_custom_driver, ui_driver)
    ctx = _CommandContext(
        cwd,
        ui,
        coding_session,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    try:
        handler(ctx, args)
    except (KeyboardInterrupt, SystemExit):
        # A genuine user abort / interpreter exit is control flow, not an
        # extension failure: never swallow it into a bounded error.
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad handler
        return ExtensionCommandDispatch(
            name=name,
            ran=False,
            error=_safe_diagnostic(err),
            messages=tuple(ui.messages),
        )
    return ExtensionCommandDispatch(
        name=name,
        ran=True,
        error=None,
        messages=tuple(ui.messages),
    )


def _coerce_activation_string(value: object, reason: str) -> str:
    """Accept prior str subclasses but detach an exact plain str before staging."""

    if not isinstance(value, str):
        raise _ActivationError(reason)
    # ``str(value)`` dispatches an overridden ``__str__`` on subclasses (and
    # the default ``Enum.__str__`` for ``class Event(str, Enum)``). The base
    # descriptor copies the underlying Unicode value without invoking either.
    return str.__str__(value)


def _normalize_provider_name(raw_name: object) -> str:
    name = _coerce_activation_string(raw_name, REASON_INVALID_PROVIDER).strip()
    if not name or "/" in name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return name


def _normalize_provider_models(models: object) -> tuple[str, ...]:
    if not isinstance(models, tuple) or not models:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    model_ids: list[str] = []
    for model in models:
        model_id = _coerce_activation_string(model, REASON_INVALID_PROVIDER).strip()
        if not model_id:
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids.append(model_id)
    return tuple(model_ids)


def _normalize_default_model(
    default_model: object,
    model_ids: tuple[str, ...],
) -> str | None:
    if isinstance(default_model, str):
        default_model = _coerce_activation_string(
            default_model, REASON_INVALID_PROVIDER
        ).strip()
    if default_model is not None and (
        not isinstance(default_model, str)
        or not default_model
        or default_model not in model_ids
    ):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return default_model


def _normalize_provider_oauth(oauth: object) -> ExtensionOAuthConfig | None:
    if oauth is None:
        return None
    if not isinstance(oauth, ExtensionOAuthConfig):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    raw_oauth_name = oauth.name
    oauth_name = (
        _coerce_activation_string(raw_oauth_name, REASON_INVALID_PROVIDER).strip()
        if isinstance(raw_oauth_name, str)
        else ""
    )
    if not oauth_name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    login = oauth.login
    refresh_token = oauth.refresh_token
    get_api_key = oauth.get_api_key
    modify_models = oauth.modify_models
    if (
        not callable(login)
        or not callable(refresh_token)
        or not callable(get_api_key)
        or (modify_models is not None and not callable(modify_models))
    ):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return replace(oauth, name=oauth_name)


class _ActivationApi:
    """Concrete `PipyExtensionAPI` for one extension's activation.

    Command registrations are validated eagerly and staged on this api;
    they are only committed to the global registry once `activate`
    completes without error, so a later failure never leaves a partial
    registration behind.
    """

    def __init__(
        self,
        extension_name: str,
        *,
        reserved: frozenset[str],
        taken: frozenset[str],
        outbox: list[QueuedUserMessage],
        custom_outbox: list[QueuedCustomMessage],
        reserved_tools: frozenset[str] = frozenset(),
        taken_tools: frozenset[str] = frozenset(),
        taken_providers: frozenset[str] = frozenset(),
        taken_shortcuts: frozenset[str] = frozenset(),
        taken_flags: frozenset[str] = frozenset(),
        taken_message_renderers: frozenset[str] = frozenset(),
        taken_entry_renderers: frozenset[str] = frozenset(),
        guard: AbstractContextManager[object] | None = None,
    ) -> None:
        self._extension_name = extension_name
        self._guard: AbstractContextManager[object] = (
            guard if guard is not None else threading.RLock()
        )
        self._state: Literal[
            "open",
            "sealed",
            "committed",
            "published",
            "catalog_finalized",
            "disposed",
        ] = "open"
        self._publication_token: object | None = None
        self._reserved = reserved
        self._taken = taken
        self._reserved_tools = reserved_tools
        self._taken_tools = taken_tools
        self._taken_providers = taken_providers
        self._taken_shortcuts = taken_shortcuts
        self._taken_flags = taken_flags
        self._taken_message_renderers = taken_message_renderers
        self._taken_entry_renderers = taken_entry_renderers
        self._outbox = outbox
        self._custom_outbox = custom_outbox
        self._staged: dict[str, RegisteredCommand] = {}
        self._staged_shortcuts: dict[str, RegisteredShortcut] = {}
        self._staged_tools: dict[str, RegisteredTool] = {}
        self._staged_providers: dict[str, RegisteredProvider] = {}
        self._staged_unregistered: list[str] = []
        self._staged_flags: dict[str, RegisteredFlag] = {}
        self._flag_values: dict[str, object] = {}
        self._staged_message_renderers: dict[str, RegisteredMessageRenderer] = {}
        self._staged_entry_renderers: dict[str, RegisteredEntryRenderer] = {}
        self._hooks: dict[str, list[HookHandler]] = {}
        self._failure: tuple[str, str | None] | None = None
        # Messages are staged during activation and only committed to the
        # shared outbox once activation succeeds, so a disabled extension
        # never leaves a queued prompt behind. After activation commits,
        # runtime calls (from command handlers / hooks) append directly.
        self._staged_messages: list[QueuedUserMessage] = []
        self._staged_custom_messages: list[QueuedCustomMessage] = []
        self._frozen_activation: _FrozenActivation | None = None
        self._activated = False

    _STATE_TRANSITIONS: Mapping[str, frozenset[str]] = MappingProxyType(
        {
            "open": frozenset(("sealed", "disposed")),
            "sealed": frozenset(("committed", "disposed")),
            "committed": frozenset(("published", "catalog_finalized", "disposed")),
            "published": frozenset(),
            "catalog_finalized": frozenset(),
            "disposed": frozenset(),
        }
    )

    def _require_open_registration(self) -> None:
        if self._state != "open":
            raise ExtensionCapabilityError(
                "extension contribution registration is closed"
            )

    def _allows_transition_locked(
        self,
        target: str,
        *,
        publication_token: object | None = None,
    ) -> bool:
        if (
            target == "published"
            and publication_token is not _ACTIVATION_PUBLICATION_TOKEN
        ):
            return False
        return target in self._STATE_TRANSITIONS.get(self._state, frozenset())

    def _transition_locked(
        self,
        target: str,
        *,
        publication_token: object | None = None,
    ) -> bool:
        """Apply one host-authored lifecycle edge while ``_guard`` is held."""

        if not self._allows_transition_locked(
            target,
            publication_token=publication_token,
        ):
            return False
        self._state = cast(
            Literal[
                "open",
                "sealed",
                "committed",
                "published",
                "catalog_finalized",
                "disposed",
            ],
            target,
        )
        if target == "published":
            self._publication_token = publication_token
        return True

    def _is_published_locked(self) -> bool:
        return (
            self._state == "published"
            and self._publication_token is _ACTIVATION_PUBLICATION_TOKEN
        )

    def _record_failure(self, err: _ActivationError) -> None:
        if self._failure is None:
            self._failure = (err.reason, err.diagnostic)

    def _check_registration_open(self) -> None:
        """Check lifecycle state without running extension-controlled code."""

        with self._guard:
            self._require_open_registration()

    def _raise_registration_failure(self, err: _ActivationError) -> NoReturn:
        """Record a validation failure only if registration is still open."""

        with self._guard:
            self._require_open_registration()
            self._record_failure(err)
        raise err

    def _validate_registration_unlocked(
        self,
        validate: Callable[[], _RegistrationResult],
        *,
        invalid_reason: str,
    ) -> _RegistrationResult:
        """Bound extension-controlled validation without holding the host guard."""

        try:
            return validate()
        except _ActivationError as err:
            self._raise_registration_failure(err)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - hostile extension value
            self._raise_registration_failure(
                _ActivationError(invalid_reason, _safe_diagnostic(err))
            )

    def _stage_registration(
        self,
        family: _RegistrationFamily,
        normalize_name: Callable[[], str],
        normalize_value: Callable[[str], _RegistrationValue],
    ) -> None:
        """Validate unlocked in the family's historical order and commit atomically."""

        self._check_registration_open()
        invalid_reason = _REGISTRATION_INVALID_REASONS[family]
        name = self._validate_registration_unlocked(
            normalize_name,
            invalid_reason=invalid_reason,
        )
        ordering = _REGISTRATION_ORDERING[family]
        if ordering == "availability_before_value":
            with self._guard:
                self._require_open_registration()
                self._check_registration_available_locked(family, name)
        value = self._validate_registration_unlocked(
            lambda: normalize_value(name),
            invalid_reason=invalid_reason,
        )
        with self._guard:
            self._require_open_registration()
            self._check_registration_available_locked(family, name)
            self._commit_registration_locked(family, name, value)

    def _check_registration_available_locked(
        self,
        family: _RegistrationFamily,
        name: str,
    ) -> None:
        registries: Mapping[
            _RegistrationFamily,
            tuple[Container[str], Container[str], str],
        ] = {
            "command": (self._taken, self._staged, REASON_DUPLICATE_COMMAND),
            "shortcut": (
                self._taken_shortcuts,
                self._staged_shortcuts,
                REASON_DUPLICATE_SHORTCUT,
            ),
            "tool": (self._taken_tools, self._staged_tools, REASON_DUPLICATE_TOOL),
            "provider": (
                self._taken_providers,
                self._staged_providers,
                REASON_DUPLICATE_PROVIDER,
            ),
            "flag": (self._taken_flags, self._staged_flags, REASON_DUPLICATE_FLAG),
            "message_renderer": (
                self._taken_message_renderers,
                self._staged_message_renderers,
                REASON_DUPLICATE_MESSAGE_RENDERER,
            ),
            "entry_renderer": (
                self._taken_entry_renderers,
                self._staged_entry_renderers,
                REASON_DUPLICATE_ENTRY_RENDERER,
            ),
        }
        reserved_reason: str | None = None
        if family == "command" and name in self._reserved:
            reserved_reason = REASON_RESERVED_COMMAND
        elif family == "shortcut" and name in RESERVED_SHORTCUT_KEYS:
            reserved_reason = REASON_RESERVED_SHORTCUT
        elif family == "tool" and name in self._reserved_tools:
            reserved_reason = REASON_RESERVED_TOOL
        taken, staged, duplicate_reason = registries[family]
        failure_reason = reserved_reason
        if failure_reason is None and (name in taken or name in staged):
            failure_reason = duplicate_reason
        if failure_reason is not None:
            failure = _ActivationError(failure_reason)
            self._record_failure(failure)
            raise failure

    def _commit_registration_locked(
        self,
        family: _RegistrationFamily,
        name: str,
        value: _RegistrationValue,
    ) -> None:
        if family == "command" and isinstance(value, RegisteredCommand):
            self._staged[name] = value
        elif family == "shortcut" and isinstance(value, RegisteredShortcut):
            self._staged_shortcuts[name] = value
        elif family == "tool" and isinstance(value, RegisteredTool):
            self._staged_tools[name] = value
        elif family == "provider" and isinstance(value, RegisteredProvider):
            self._staged_providers[name] = value
        elif family == "flag" and isinstance(value, _NormalizedFlagRegistration):
            self._staged_flags[name] = value.registered
            if value.default is not None:
                self._flag_values[name] = value.default
        elif family == "message_renderer" and isinstance(
            value, RegisteredMessageRenderer
        ):
            self._staged_message_renderers[name] = value
        elif family == "entry_renderer" and isinstance(value, RegisteredEntryRenderer):
            self._staged_entry_renderers[name] = value
        else:
            raise AssertionError(f"invalid normalized {family} registration")

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Enqueue a deterministic user turn (drained by the session loop)."""

        message = QueuedUserMessage(content=str(content), options=dict(options or {}))
        target: list[QueuedUserMessage] | None = None
        with self._guard:
            if self._state == "open":
                self._staged_messages.append(message)
            elif self._state in ("committed", "published") and self._activated:
                target = self._outbox
            else:
                # A sealed, still-pending send keeps its historical silent
                # ``None`` shape but cannot alter the frozen activation.
                return
        # R4a will put the target append under the session mutex. Keeping it
        # outside the candidate guard now establishes the required no-nesting
        # handoff without changing the current list-backed queue semantics.
        if target is not None:
            target.append(message)

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Stage a custom session message until activation succeeds."""

        queued = coerce_custom_message(message, options)
        target: list[QueuedCustomMessage] | None = None
        with self._guard:
            if self._state == "open":
                self._staged_custom_messages.append(queued)
            elif self._state in ("committed", "published") and self._activated:
                target = self._custom_outbox
            else:
                return
        if target is not None:
            target.append(queued)

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        self.send_message(message, options)

    def _commit_activation(
        self,
        *,
        _lifecycle_token: object | None = None,
    ) -> tuple[QueuedCustomMessage, ...]:
        """Host-internal commit of a sealed activation exactly once."""

        if _lifecycle_token is not _ACTIVATION_LIFECYCLE_TOKEN:
            raise ExtensionCapabilityError("extension activation is unavailable")
        with self._guard:
            snapshot = self._frozen_activation
            if snapshot is None or not self._transition_locked("committed"):
                raise ExtensionCapabilityError("extension activation is unavailable")
            self._activated = True
        # The one seal-time snapshot is authoritative. Sends racing after seal
        # are silent no-ops, so finalization flushes exactly these messages once.
        # R4a still owns serialization of this flush with accepted/live runtime
        # appends after activation commit.
        self._outbox.extend(snapshot.user_messages)
        return snapshot.custom_messages

    def register_tool(self, tool: ExtensionTool) -> None:
        self._stage_registration(
            "tool",
            lambda: self._normalize_tool_name(tool),
            lambda name: self._normalize_tool(tool, name=name),
        )

    @staticmethod
    def _normalize_tool_name(tool: ExtensionTool) -> str:
        if not isinstance(tool, ExtensionTool):
            raise _ActivationError(REASON_INVALID_TOOL)
        name = _coerce_activation_string(tool.name, REASON_INVALID_TOOL)
        if not name:
            raise _ActivationError(REASON_INVALID_TOOL)
        return name

    def _normalize_tool(self, tool: ExtensionTool, *, name: str) -> RegisteredTool:
        """Validate extension-controlled non-name values without the host guard."""

        description = tool.description
        input_schema = tool.input_schema
        handler = tool.handler
        if not callable(handler) or not isinstance(input_schema, Mapping):
            raise _ActivationError(REASON_INVALID_TOOL)
        try:
            normalized_description = str(description)
            normalized_schema = dict(input_schema)
            ToolDefinition(
                name=name,
                description=normalized_description,
                input_schema=normalized_schema,
            )
        except (ValueError, TypeError) as exc:
            raise _ActivationError(REASON_INVALID_TOOL, _safe_diagnostic(exc)) from None
        normalized = replace(
            tool,
            name=name,
            description=normalized_description,
            input_schema=normalized_schema,
        )
        return RegisteredTool(tool=normalized, extension=self._extension_name)

    def register_provider(self, provider: ExtensionProvider) -> None:
        self._stage_registration(
            "provider",
            lambda: self._normalize_provider_registration_name(provider),
            lambda name: self._normalize_provider(provider, name=name),
        )

    @staticmethod
    def _normalize_provider_registration_name(provider: ExtensionProvider) -> str:
        if not isinstance(provider, ExtensionProvider):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        return _normalize_provider_name(provider.name)

    def _normalize_provider(
        self,
        provider: ExtensionProvider,
        *,
        name: str,
    ) -> RegisteredProvider:
        """Validate extension-controlled provider values without the guard."""

        factory = provider.factory
        raw_models = provider.models
        raw_default = provider.default_model
        raw_oauth = provider.oauth
        if not callable(factory):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids = _normalize_provider_models(raw_models)
        default_model = _normalize_default_model(raw_default, model_ids)
        oauth = _normalize_provider_oauth(raw_oauth)
        normalized = replace(
            provider,
            name=name,
            default_model=default_model,
            models=model_ids,
            oauth=oauth,
        )
        return RegisteredProvider(
            provider=normalized,
            extension=self._extension_name,
        )

    def unregister_provider(self, name: str) -> None:
        self._check_registration_open()
        try:
            normalized = _normalize_provider_name(name)
        except _ActivationError as err:
            self._raise_registration_failure(err)
        with self._guard:
            self._require_open_registration()
            if normalized not in self._staged_unregistered:
                self._staged_unregistered.append(normalized)

    def register_flag(self, flag: ExtensionFlag) -> None:
        self._stage_registration(
            "flag",
            lambda: self._normalize_flag_name(flag),
            lambda name: self._normalize_flag(flag, name=name),
        )

    @staticmethod
    def _normalize_flag_name(flag: ExtensionFlag) -> str:
        if not isinstance(flag, ExtensionFlag):
            raise _ActivationError(REASON_INVALID_FLAG)
        name = _coerce_activation_string(flag.name, REASON_INVALID_FLAG).strip()
        if not _is_valid_command_name(name):
            raise _ActivationError(REASON_INVALID_FLAG)
        return name

    def _normalize_flag(
        self,
        flag: ExtensionFlag,
        *,
        name: str,
    ) -> _NormalizedFlagRegistration:
        raw_flag_type = flag.flag_type
        description = flag.description
        default = flag.default
        flag_type = _coerce_activation_string(raw_flag_type, REASON_INVALID_FLAG)
        if flag_type not in ("boolean", "string"):
            raise _ActivationError(REASON_INVALID_FLAG)
        flag_type = cast(Literal["boolean", "string"], flag_type)
        if flag_type == "boolean" and default is not None and type(default) is not bool:
            raise _ActivationError(REASON_INVALID_FLAG)
        if flag_type == "string" and default is not None:
            default = _coerce_activation_string(default, REASON_INVALID_FLAG)
        normalized_description = (
            description
            if description is None or type(description) is str
            else str(description)
        )
        definition = replace(
            flag,
            name=name,
            flag_type=flag_type,
            description=normalized_description,
            default=default,
        )
        return _NormalizedFlagRegistration(
            registered=RegisteredFlag(
                flag=definition,
                extension=self._extension_name,
                _get_value=self._get_flag_value,
                _set_value=self._set_flag_value,
            ),
            default=default,
        )

    def get_flag(self, name: str) -> object | None:
        try:
            normalized = _coerce_activation_string(name, REASON_INVALID_FLAG)
        except _ActivationError:
            return None
        return self._get_flag_value(normalized)

    def _get_flag_value(self, name: str) -> object | None:
        with self._guard:
            return self._flag_values.get(name)

    def _set_flag_value(self, name: str, value: object) -> None:
        with self._guard:
            if self._state in ("catalog_finalized", "disposed"):
                return
            self._flag_values[name] = value

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        self._stage_registration(
            "message_renderer",
            lambda: self._normalize_renderer_name(
                custom_type, reason=REASON_INVALID_MESSAGE_RENDERER
            ),
            lambda name: self._normalize_message_renderer(name, renderer),
        )

    @staticmethod
    def _normalize_renderer_name(custom_type: str, *, reason: str) -> str:
        name = _coerce_activation_string(custom_type, reason).strip()
        if not is_valid_custom_entry_type(name):
            raise _ActivationError(reason)
        return name

    def _normalize_message_renderer(
        self,
        name: str,
        renderer: Callable[..., object],
    ) -> RegisteredMessageRenderer:
        if not callable(renderer):
            raise _ActivationError(REASON_INVALID_MESSAGE_RENDERER)
        return RegisteredMessageRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        self._stage_registration(
            "entry_renderer",
            lambda: self._normalize_renderer_name(
                custom_type, reason=REASON_INVALID_ENTRY_RENDERER
            ),
            lambda name: self._normalize_entry_renderer(name, renderer),
        )

    def _normalize_entry_renderer(
        self,
        name: str,
        renderer: Callable[..., object],
    ) -> RegisteredEntryRenderer:
        if not callable(renderer):
            raise _ActivationError(REASON_INVALID_ENTRY_RENDERER)
        return RegisteredEntryRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None:
        self._stage_registration(
            "command",
            lambda: self._normalize_command_name(name),
            lambda normalized: self._normalize_command(
                normalized, description, handler
            ),
        )

    @staticmethod
    def _normalize_command_name(name: str) -> str:
        normalized = _coerce_activation_string(name, REASON_INVALID_COMMAND_NAME)
        if not _is_valid_command_name(normalized):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        return normalized

    def _normalize_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> RegisteredCommand:
        if not callable(handler):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        return RegisteredCommand(
            name=name,
            description=str(description),
            handler=handler,
            extension=self._extension_name,
        )

    def register_shortcut(self, key: str, handler: CommandHandler) -> None:
        self._stage_registration(
            "shortcut",
            lambda: self._normalize_shortcut_name(key, handler),
            lambda normalized: self._normalize_shortcut(normalized, handler),
        )

    @staticmethod
    def _normalize_shortcut_name(key: str, handler: CommandHandler) -> str:
        plain_key = _coerce_activation_string(key, REASON_INVALID_SHORTCUT)
        if not plain_key.strip() or not callable(handler):
            raise _ActivationError(REASON_INVALID_SHORTCUT)
        normalized = normalize_shortcut_key(plain_key)
        if len(normalized) <= 1 or normalized.endswith("-"):
            raise _ActivationError(REASON_INVALID_SHORTCUT)
        return normalized

    def _normalize_shortcut(
        self,
        normalized: str,
        handler: CommandHandler,
    ) -> RegisteredShortcut:
        return RegisteredShortcut(
            key=normalized,
            handler=handler,
            extension=self._extension_name,
        )

    def on(
        self,
        event: str,
        handler: HookHandler | None = None,
    ) -> object:
        """Register an event hook. Supports decorator and direct forms.

        `api.on("tool_call", handler)` registers directly;
        `@api.on("tool_call")` returns a decorator. Any non-empty event
        name is accepted (only dispatched events fire); an invalid event
        or non-callable handler records a failure and re-raises, so the
        extension is disabled even if it swallows the error.
        """

        self._check_registration_open()
        try:
            normalized_event = self._normalize_hook_event(event)
        except _ActivationError as err:
            self._raise_registration_failure(err)
        if handler is None:
            # Refuse if seal won while the event was being normalized.
            self._check_registration_open()

            def _decorator(func: HookHandler) -> HookHandler:
                self._register_hook(normalized_event, func)
                return func

            return _decorator
        self._register_hook(normalized_event, handler)
        return handler

    @staticmethod
    def _normalize_hook_event(event: str) -> str:
        normalized = _coerce_activation_string(event, REASON_INVALID_HOOK)
        if not normalized:
            raise _ActivationError(REASON_INVALID_HOOK)
        return normalized

    def _register_hook(self, event: str, handler: HookHandler) -> None:
        self._check_registration_open()
        if not callable(handler):
            self._raise_registration_failure(_ActivationError(REASON_INVALID_HOOK))
        with self._guard:
            self._require_open_registration()
            self._hooks.setdefault(event, []).append(handler)

    def _seal_and_freeze(
        self,
        *,
        _lifecycle_token: object | None = None,
    ) -> _FrozenActivation:
        """Host-internally seal registration and return one complete snapshot."""

        if _lifecycle_token is not _ACTIVATION_LIFECYCLE_TOKEN:
            raise ExtensionCapabilityError("extension activation is unavailable")
        with self._guard:
            if not self._transition_locked("sealed"):
                raise ExtensionCapabilityError("extension activation is unavailable")
            self._activated = False
            user_messages = tuple(self._staged_messages)
            custom_messages = tuple(self._staged_custom_messages)
            snapshot = _FrozenActivation(
                commands=tuple(self._staged.values()),
                tools=tuple(self._staged_tools.values()),
                providers=tuple(self._staged_providers.values()),
                unregistered_providers=tuple(self._staged_unregistered),
                shortcuts=tuple(self._staged_shortcuts.values()),
                flags=tuple(self._staged_flags.values()),
                message_renderers=tuple(self._staged_message_renderers.values()),
                entry_renderers=tuple(self._staged_entry_renderers.values()),
                hooks=MappingProxyType(
                    {event: tuple(handlers) for event, handlers in self._hooks.items()}
                ),
                user_messages=user_messages,
                custom_messages=custom_messages,
                failure=self._failure,
            )
            self._frozen_activation = snapshot
            # Detach mutable staging queues at the seal boundary. Commit reads
            # only ``_frozen_activation``; no later send can enter this value.
            self._staged_messages = []
            self._staged_custom_messages = []
        return snapshot

    def _clear_terminal_storage_locked(self, *, retain_flag_values: bool) -> None:
        """Drop all staging/outbox reachability while ``_guard`` is held."""

        self._activated = False
        self._reserved = frozenset()
        self._taken = frozenset()
        self._reserved_tools = frozenset()
        self._taken_tools = frozenset()
        self._taken_providers = frozenset()
        self._taken_shortcuts = frozenset()
        self._taken_flags = frozenset()
        self._taken_message_renderers = frozenset()
        self._taken_entry_renderers = frozenset()
        # Replace rather than call extension-corruptible container methods.
        self._staged = {}
        self._staged_shortcuts = {}
        self._hooks = {}
        self._staged_tools = {}
        self._staged_providers = {}
        self._staged_unregistered = []
        self._staged_flags = {}
        if not retain_flag_values:
            self._flag_values = {}
        self._staged_message_renderers = {}
        self._staged_entry_renderers = {}
        self._staged_messages = []
        self._staged_custom_messages = []
        self._frozen_activation = None
        self._failure = None
        self._publication_token = None
        self._outbox = []
        self._custom_outbox = []

    def _finalize_provider_catalog_locked(self) -> bool:
        """Enter the accepted-catalog terminal state while retaining flag reads."""

        if not self._transition_locked("catalog_finalized"):
            return False
        self._clear_terminal_storage_locked(retain_flag_values=True)
        return True

    def _dispose_locked(self) -> bool:
        """Fail closed while ``_guard`` is held, including corrupted states."""

        if self._is_published_locked() or self._state == "catalog_finalized":
            return False
        changed = self._state != "disposed"
        if changed and not self._transition_locked("disposed"):
            # Trusted Python can corrupt private attributes. Such a value is
            # not a lifecycle state; terminal disposal is the bounded recovery.
            self._state = "disposed"
        self._clear_terminal_storage_locked(retain_flag_values=False)
        return changed

    def _dispose(self) -> bool:
        """Host-internally clear candidate state without disposing a live host."""

        with self._guard:
            return self._dispose_locked()


def _publish_activation_hosts_atomically(
    hosts: tuple[_ActivationApi, ...],
) -> bool:
    """Validate all host-authored transitions, then publish the complete set."""

    with ExitStack() as guards:
        for host in hosts:
            guards.enter_context(host._guard)
        if any(
            not _ActivationApi._allows_transition_locked(
                host,
                "published",
                publication_token=_ACTIVATION_PUBLICATION_TOKEN,
            )
            for host in hosts
        ):
            return False
        for host in hosts:
            if not _ActivationApi._transition_locked(
                host,
                "published",
                publication_token=_ACTIVATION_PUBLICATION_TOKEN,
            ):
                raise AssertionError("validated activation host did not publish")
    return True


def _dispose_activation_hosts(
    hosts: tuple[_ActivationApi, ...],
) -> _ActivationCleanup:
    """Dispose every unpublished host and return bounded anomaly details."""

    disposed = 0
    skipped_published = 0
    failed = 0
    for host in hosts:
        try:
            with host._guard:
                if _ActivationApi._is_published_locked(host):
                    skipped_published += 1
                    continue
                if _ActivationApi._dispose_locked(host):
                    disposed += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - continue bounded sibling cleanup
            failed += 1
    return _ActivationCleanup(
        disposed=disposed,
        skipped_published=skipped_published,
        failed=failed,
    )


def _dispose_activation_host_with_diagnostic(
    api: _ActivationApi,
    diagnostic: Callable[[str], None] | None,
) -> None:
    cleanup = _dispose_activation_hosts((api,))
    _report_activation_cleanup(cleanup, diagnostic)


def activate_extensions(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> list[ActivatedExtension]:
    """Activate the loadable descriptors, in order.

    Disabled discovery descriptors are passed through unchanged (never
    imported). Each loadable descriptor is imported and activated in
    isolation; any failure disables only that extension. Command names
    are deduplicated across all extensions in this pass (first
    registration wins; a later collision disables the later extension).

    `message_outbox` is the shared list that `api.send_user_message`
    appends to; the session drains it with `drain_user_messages`. When
    omitted, a private outbox is used (messages are simply unread).
    """

    batch = activate_extension_batch(
        descriptors,
        reserved_command_names=reserved_command_names,
        reserved_tool_names=reserved_tool_names,
        message_outbox=message_outbox,
        custom_message_outbox=custom_message_outbox,
        diagnostic=diagnostic,
    )
    return list(batch.activated)


def _dispose_activation_results(
    activated: Iterable[ActivatedExtension],
) -> _ActivationCleanup:
    """Dispose every host still owned by uncomposed activation results."""

    cleanup = _ActivationCleanup()
    for extension in activated:
        pending_activation = extension._pending_activation
        if pending_activation is not None:
            cleanup = cleanup.merge(pending_activation.dispose())
        activation_host = extension._activation_host
        if activation_host is not None:
            cleanup = cleanup.merge(_dispose_activation_hosts((activation_host,)))
    return cleanup


def _finalize_provider_catalog_results(
    activated: Iterable[ActivatedExtension],
) -> _ProviderCatalogFinalization:
    """Terminally retain only guarded flag reads needed by accepted factories."""

    finalized = 0
    refused_disposed = 0
    refused_published = 0
    refused_already_terminal = 0
    inaccessible = 0
    for extension in activated:
        host = extension._activation_host
        if host is None:
            continue
        try:
            with host._guard:
                if host._finalize_provider_catalog_locked():
                    finalized += 1
                elif host._state == "published":
                    # A published host may be live even if its private marker was
                    # corrupted. Catalog harvesting never takes that risk.
                    refused_published += 1
                elif host._dispose_locked():
                    refused_disposed += 1
                else:
                    # Refusal can be an idempotent revisit of an existing
                    # catalog-finalized/disposed terminal host.
                    refused_already_terminal += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - continue bounded sibling finalization
            inaccessible += 1
    return _ProviderCatalogFinalization(
        finalized=finalized,
        refused_disposed=refused_disposed,
        refused_published=refused_published,
        refused_already_terminal=refused_already_terminal,
        inaccessible=inaccessible,
    )


def _report_provider_catalog_finalization(
    result: _ProviderCatalogFinalization,
    diagnostic: Callable[[str], None],
) -> None:
    message = result.anomaly_diagnostic
    if message is not None:
        diagnostic(message)


def activate_extension_batch(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
    preloaded: ExtensionActivationBatch | None = None,
    pending: bool = False,
    diagnostic: Callable[[str], None] | None = None,
) -> ExtensionActivationBatch:
    """Activate once, or finalize a pending pre-trust batch in final order."""

    if preloaded is not None and not preloaded.pending:
        raise ValueError("preloaded extension batch is already finalized")
    if preloaded is not None and pending:
        raise ValueError("a final merge cannot remain pending")

    reserved = frozenset(reserved_command_names)
    reserved_tools = frozenset(reserved_tool_names)
    taken = _TakenContributionState()
    outbox = (
        preloaded.message_outbox
        if preloaded is not None
        else (message_outbox if message_outbox is not None else [])
    )
    custom_outbox = (
        preloaded.custom_message_outbox
        if preloaded is not None
        else (custom_message_outbox if custom_message_outbox is not None else [])
    )
    results: list[ActivatedExtension] = []
    preloaded_by_key = (
        {
            item._activation_key: item
            for item in preloaded.activated
            if item._activation_key is not None
        }
        if preloaded is not None
        else {}
    )

    try:
        for descriptor in descriptors:
            if descriptor.status != "loadable":
                # Discovery already disabled this; never import it.
                results.append(_passthrough_disabled(descriptor))
                continue
            key = _descriptor_activation_key(descriptor)
            existing = preloaded_by_key.get(key)
            if existing is not None:
                reused, taken = _finalize_preloaded_extension(
                    existing,
                    descriptor=descriptor,
                    reserved=reserved,
                    reserved_tools=reserved_tools,
                    taken=taken,
                    diagnostic=diagnostic,
                )
                results.append(reused)
                continue
            # Pending pre-trust activation stages each extension independently.
            # Cross-extension collisions are provisional because the final
            # reserved set can disable an earlier extension and free its names
            # for a later one. Resolve those collisions only once, in final
            # descriptor order.
            activation_taken = _TakenContributionState() if pending else taken
            activated, successor = _activate_one(
                descriptor,
                reserved=reserved,
                reserved_tools=reserved_tools,
                taken=activation_taken,
                outbox=outbox,
                custom_outbox=custom_outbox,
                commit_activation=not pending,
                diagnostic=diagnostic,
            )
            results.append(activated)
            if not pending:
                taken = successor
    except BaseException:
        cleanup = _dispose_activation_results(results)
        if preloaded is not None:
            cleanup = cleanup.merge(_dispose_activation_results(preloaded.activated))
        _report_activation_cleanup(cleanup, diagnostic)
        raise
    if preloaded is not None:
        # A pending descriptor omitted from the final trusted set is abandoned.
        # Claimed tokens are already empty, so this cannot dispose a transferee.
        cleanup = _dispose_activation_results(preloaded.activated)
        _report_activation_cleanup(cleanup, diagnostic)
    return ExtensionActivationBatch(
        activated=tuple(results),
        message_outbox=outbox,
        custom_message_outbox=custom_outbox,
        pending=pending,
    )


def extension_providers(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredProvider, ...]:
    """Collect registered providers from activated extensions, in order."""

    providers: list[RegisteredProvider] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        providers.extend(extension.providers)
    return tuple(providers)


def extension_oauth_providers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredProvider]:
    """OAuth-capable extension providers keyed by derived provider id.

    Pi derives a dynamic OAuth provider's id from the registered provider name
    (`{...oauth, id: providerName}`). Pipy projects only already-accepted
    provider registrations here and never invokes OAuth callbacks while building
    the map.
    """

    return {
        registered.provider.name.lower(): registered
        for registered in extension_providers(activated)
        if registered.provider.oauth is not None
    }


def extension_unregistered_providers(
    activated: Sequence[ActivatedExtension],
) -> tuple[str, ...]:
    """Collect provider names extensions asked to unregister, in order."""

    names: list[str] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        for name in extension.unregistered_providers:
            if name not in names:
                names.append(name)
    return tuple(names)


def extension_tools(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredTool, ...]:
    """Collect registered tools from activated extensions, in order."""

    tools: list[RegisteredTool] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        tools.extend(extension.tools)
    return tuple(tools)


def extension_flags(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredFlag, ...]:
    """Collect registered CLI flags from activated extensions, in order."""

    flags: list[RegisteredFlag] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        flags.extend(extension.flags)
    return tuple(flags)


def extension_message_renderers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredMessageRenderer]:
    """Collect custom-message renderers from activated extensions."""

    renderers: dict[str, RegisteredMessageRenderer] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for renderer in extension.message_renderers:
            renderers.setdefault(renderer.custom_type, renderer)
    return renderers


def extension_entry_renderers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredEntryRenderer]:
    """Collect durable-entry TUI renderers from activated extensions."""

    renderers: dict[str, RegisteredEntryRenderer] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for renderer in extension.entry_renderers:
            renderers.setdefault(renderer.custom_type, renderer)
    return renderers


def coerce_custom_message(
    message: Mapping[str, object],
    options: Mapping[str, object] | None = None,
) -> QueuedCustomMessage:
    """Validate and bound a Pi-shaped custom message payload."""

    if not isinstance(message, Mapping):
        raise ValueError("custom message must be a mapping")
    custom_type = str(message.get("customType", message.get("custom_type", ""))).strip()
    if not is_valid_custom_entry_type(custom_type):
        raise ValueError("invalid custom message type")
    content = str(message.get("content", ""))
    if len(content) > _CUSTOM_ENTRY_DATA_MAX_CHARS:
        content = (
            content[: _CUSTOM_ENTRY_DATA_MAX_CHARS - 128]
            + "\n[pipy: custom message truncated]"
        )
    return QueuedCustomMessage(
        custom_type=custom_type,
        content=content,
        display=bool(message.get("display", True)),
        details=safe_custom_entry_data(message.get("details")),
        options=dict(options or {}),
    )


def safe_custom_entry_data(data: object | None) -> object | None:
    """Return JSON-safe, bounded custom-entry data for the product session."""

    if data is None:
        return None
    try:
        encoded, decoded = _json_round_trip(data)
    except (TypeError, ValueError):
        encoded = str(data)
        decoded = encoded
    if len(encoded) <= _CUSTOM_ENTRY_DATA_MAX_CHARS:
        return decoded
    return {
        "truncated": True,
        "text": encoded[: _CUSTOM_ENTRY_DATA_MAX_CHARS - 128],
    }


def _custom_message_renderer_payload(entry: _CustomMessageEntry) -> dict[str, object]:
    """Return the Pi-shaped payload passed to CustomMessageEntry renderers."""

    return {
        "customType": entry.custom_type,
        "content": entry.content,
        "display": entry.display,
        "details": safe_custom_entry_data(entry.details),
    }


def _custom_entry_renderer_payload(entry: _CustomEntry) -> dict[str, object]:
    """Return the Pi-shaped full stored entry passed to entry renderers."""

    return {
        "type": "custom",
        "id": entry.id,
        "parentId": entry.parent_id,
        "timestamp": entry.timestamp,
        "customType": entry.custom_type,
        "data": safe_custom_entry_data(entry.data),
    }


_CustomEntryRedrawRow: TypeAlias = (
    tuple[str, str, tuple[str, ...]]
    | tuple[
        str,
        str,
        tuple[str, ...],
        object | None,
        Mapping[str, RegisteredMessageRenderer] | Mapping[str, RegisteredEntryRenderer],
    ]
)


def _custom_entry_redraw_rows(
    branch: Iterable[object],
    render_custom_entry: Callable[[_CustomEntry], RenderedCustomEntry | None],
    render_custom_message_entry: Callable[[_CustomMessageEntry], RenderedCustomEntry]
    | None = None,
    *,
    render_metadata: Mapping[str, RegisteredMessageRenderer] | None = None,
    entry_render_metadata: Mapping[str, RegisteredEntryRenderer] | None = None,
) -> list[_CustomEntryRedrawRow]:
    """Build TUI redraw rows for active-branch extension custom entries."""

    from pipy_harness.native.session_tree import (
        CustomEntry as _CustomEntry,
    )
    from pipy_harness.native.session_tree import (
        CustomMessageEntry as _CustomMessageEntry,
    )

    rows: list[_CustomEntryRedrawRow] = []
    for entry in branch:
        if isinstance(entry, _CustomEntry):
            data = _custom_entry_renderer_payload(entry)
            rendered = render_custom_entry(entry)
            if rendered is None:
                continue
            row: _CustomEntryRedrawRow = (
                "entry",
                entry.custom_type,
                tuple(rendered.lines),
            )
            if entry_render_metadata is not None:
                row = (*row, data, entry_render_metadata)
            rows.append(row)
        elif isinstance(entry, _CustomMessageEntry) and entry.display:
            if render_custom_message_entry is not None:
                data = _custom_message_renderer_payload(entry)
                rendered = render_custom_message_entry(entry)
                row = (
                    "styled" if rendered.styled else "plain",
                    entry.custom_type,
                    tuple(rendered.lines),
                )
                if render_metadata is not None:
                    row = (*row, data, render_metadata)
                rows.append(row)
            else:
                rows.append(
                    (
                        "plain",
                        entry.custom_type,
                        tuple(entry.content.splitlines() or [""]),
                    )
                )
    return rows


def _renderer_wants_context(renderer: Callable[..., object]) -> bool:
    """True if ``renderer`` requires a second positional MessageRenderContext.

    Counts only REQUIRED positional params (those without a default): a 2-arg
    ``renderer(data, ctx)`` is context-aware, while the slice-16 capture-default
    idiom ``renderer(data, prefix=captured)`` stays 1-arg/plain so its default is
    never clobbered by the context. ``*args`` is treated as context-aware.
    Defaults to False (1-arg slice-16 form) when the signature is unavailable,
    so back-compat is the safe fallback."""

    try:
        sig = inspect.signature(renderer)
    except (TypeError, ValueError):
        return False
    positional = 0
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            if param.default is inspect.Parameter.empty:
                positional += 1
        elif param.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
    return positional >= 2


def _plain_message_render(value: object | None) -> RenderedCustomEntry:
    if value is None:
        return RenderedCustomEntry((), False)
    return RenderedCustomEntry((_bounded_render_text(value),), False)


def _invoke_message_renderer(
    registered: RegisteredMessageRenderer,
    detached: object | None,
    *,
    custom_type: str,
    wants_context: bool,
    width: int,
    expanded: bool,
    theme: object | None,
) -> object:
    if not wants_context:
        return registered.renderer(detached)
    context = MessageRenderContext(
        custom_type=custom_type,
        data=detached,
        expanded=expanded,
        width=width,
        theme=theme,
    )
    return registered.renderer(detached, context)


def _coerce_message_component(
    rendered: object,
    *,
    width: int,
    fallback: object | None,
) -> RenderedCustomEntry | None:
    """Render a context-aware component, or leave plain output to the caller."""

    render = getattr(rendered, "render", None)
    if not callable(render) or isinstance(rendered, (str, bytes, bytearray)):
        return None
    produced = render(width)
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return _plain_message_render(fallback)
    return RenderedCustomEntry(tuple(coerced), True)


def render_extension_message(
    renderers: Mapping[str, RegisteredMessageRenderer],
    custom_type: str,
    data: object | None,
    *,
    width: int = 80,
    expanded: bool = False,
    theme: object | None = None,
) -> RenderedCustomEntry:
    """Render a custom entry through its extension renderer, fail-soft.

    A renderer that accepts a second parameter receives a MessageRenderContext
    and may return a component (committed SGR-preserving, ``styled=True``).
    Text/lines returns and any failure fall back to plain rendering
    (``styled=False``)."""

    registered = renderers.get(custom_type)
    if registered is None:
        return _plain_message_render(data)
    detached = _copy_custom_entry_data(data)
    wants_context = _renderer_wants_context(registered.renderer)
    try:
        rendered = _invoke_message_renderer(
            registered,
            detached,
            custom_type=custom_type,
            wants_context=wants_context,
            width=width,
            expanded=expanded,
            theme=theme,
        )
        # A 1-arg renderer keeps exact plain-text behavior even when its return
        # object happens to expose a render() attribute.
        if wants_context:
            component = _coerce_message_component(
                rendered,
                width=width,
                fallback=detached,
            )
            if component is not None:
                return component
        return RenderedCustomEntry(_coerce_rendered_lines(rendered), False)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound bad renderer behavior
        return RenderedCustomEntry((f"render error: {_safe_diagnostic(err)}",), False)


def _close_unsupported_awaitable(value: object) -> bool:
    if not inspect.isawaitable(value):
        return False
    close = getattr(value, "close", None)
    if callable(close):
        close()
    return True


def _coerce_entry_component(
    rendered: object,
    *,
    width: int,
) -> RenderedCustomEntry | None:
    if _close_unsupported_awaitable(rendered):
        return None
    if rendered is None or isinstance(rendered, (str, bytes, bytearray)):
        return None
    render = getattr(rendered, "render", None)
    if not callable(render):
        return None
    produced = render(width)
    if _close_unsupported_awaitable(produced):
        return None
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    return RenderedCustomEntry(tuple(coerced), True)


def render_extension_entry(
    renderers: Mapping[str, RegisteredEntryRenderer],
    entry: Mapping[str, object],
    *,
    width: int = 80,
    expanded: bool = False,
    theme: object | None = None,
) -> RenderedCustomEntry | None:
    """Render one stored custom entry for the product TUI, fail-soft.

    Pi's entry renderer is a component-only, interactive surface. Missing
    renderers, ``None`` returns, unsupported outputs, awaitables, and failures
    all omit the live row while leaving the durable session entry untouched.
    """

    custom_type = str(entry.get("customType", ""))
    registered = renderers.get(custom_type)
    if registered is None:
        return None
    detached = _copy_custom_entry_data(dict(entry))
    if not isinstance(detached, dict):
        return None
    try:
        rendered = registered.renderer(
            detached,
            EntryRenderContext(expanded=expanded, width=width, theme=theme),
        )
        return _coerce_entry_component(rendered, width=width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - omit a bad live renderer safely
        return None


def _copy_custom_entry_data(data: object | None) -> object | None:
    if data is None:
        return None
    try:
        _encoded, decoded = _json_round_trip(data)
        return decoded
    except (TypeError, ValueError):
        return safe_custom_entry_data(data)


def _coerce_rendered_lines(rendered: object) -> tuple[str, ...]:
    if inspect.isawaitable(rendered):
        close = getattr(rendered, "close", None)
        if callable(close):
            close()
        return ("render error: unsupported awaitable",)
    if rendered is None:
        return ()
    if isinstance(rendered, str):
        lines = rendered.splitlines() or [""]
    elif isinstance(rendered, Sequence) and not isinstance(
        rendered, (bytes, bytearray)
    ):
        lines = [str(item) for item in rendered]
    else:
        lines = [_bounded_render_text(rendered)]
    text = "\n".join(lines)
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        text = (
            text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: custom render truncated]"
        )
    return tuple(text.splitlines() or [""])


def _bounded_render_text(value: object) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        return (
            text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: custom render truncated]"
        )
    return text


@dataclass(frozen=True, slots=True)
class _ParsedExtensionFlagToken:
    name: str
    value: object
    next_index: int


def _parse_boolean_flag_value(name: str, separator: str, inline: str) -> bool | str:
    if not separator:
        return True
    lowered = inline.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return f"invalid boolean value for --{name}"


def _parse_string_flag_value(
    name: str,
    separator: str,
    inline: str,
    tokens: Sequence[str],
    index: int,
) -> tuple[str, int] | str:
    if separator:
        return inline, index + 1
    next_index = index + 1
    if next_index >= len(tokens) or tokens[next_index].startswith("--"):
        return f"missing value for --{name}"
    return tokens[next_index], index + 2


def _parse_extension_flag_token(
    definitions: Mapping[str, ExtensionFlag],
    tokens: Sequence[str],
    index: int,
) -> _ParsedExtensionFlagToken | str:
    """Classify one token and parse its value without mutating flag owners."""

    token = tokens[index]
    if not token.startswith("--") or token == "--":
        return f"unexpected extension flag token: {token!r}"
    name, separator, inline = token[2:].partition("=")
    flag = definitions.get(name)
    if flag is None:
        return f"unknown extension flag: --{name}"
    if flag.flag_type == "boolean":
        value = _parse_boolean_flag_value(name, separator, inline)
        if isinstance(value, str):
            return value
        return _ParsedExtensionFlagToken(name, value, index + 1)
    parsed = _parse_string_flag_value(name, separator, inline, tokens, index)
    if isinstance(parsed, str):
        return parsed
    value, next_index = parsed
    return _ParsedExtensionFlagToken(name, value, next_index)


def parse_extension_flag_tokens(
    registered_flags: Sequence[RegisteredFlag],
    tokens: Sequence[str],
) -> tuple[dict[str, object], str | None]:
    """Parse unknown CLI tokens against activated extension flags."""

    definitions = {
        registered.flag.name: registered.flag for registered in registered_flags
    }
    owners = {registered.flag.name: registered for registered in registered_flags}
    values: dict[str, object] = {
        flag.name: flag.default
        for flag in definitions.values()
        if flag.default is not None
    }
    index = 0
    while index < len(tokens):
        parsed = _parse_extension_flag_token(definitions, tokens, index)
        if isinstance(parsed, str):
            return {}, parsed
        values[parsed.name] = parsed.value
        owner = owners.get(parsed.name)
        if owner is not None:
            owner._apply_value(parsed.value)
        index = parsed.next_index
    return values, None


def drain_user_messages(
    outbox: list[QueuedUserMessage],
) -> list[QueuedUserMessage]:
    """Return and clear the queued `send_user_message` messages, in order."""

    drained = list(outbox)
    outbox.clear()
    return drained


def drain_custom_messages(
    outbox: list[QueuedCustomMessage],
) -> list[QueuedCustomMessage]:
    """Return and clear queued `send_message` custom messages, in order."""

    drained = list(outbox)
    outbox.clear()
    return drained


def _descriptor_activation_key(descriptor: ExtensionDescriptor) -> str:
    if descriptor.entry_path:
        try:
            return str(Path(descriptor.entry_path).expanduser().resolve())
        except OSError:
            return str(Path(descriptor.entry_path).expanduser().absolute())
    return f"{descriptor.source_kind}:{descriptor.path_label}"


def _activated_contribution_names(existing: ActivatedExtension) -> _ContributionNames:
    return _ContributionNames(
        commands=tuple(command.name for command in existing.commands),
        tools=tuple(registered.tool.name for registered in existing.tools),
        providers=tuple(registered.provider.name for registered in existing.providers),
        shortcuts=tuple(shortcut.key for shortcut in existing.shortcuts),
        flags=tuple(registered.flag.name for registered in existing.flags),
        message_renderers=tuple(
            renderer.custom_type for renderer in existing.message_renderers
        ),
        entry_renderers=tuple(
            renderer.custom_type for renderer in existing.entry_renderers
        ),
    )


def _staged_contribution_names(staged: _FrozenActivation) -> _ContributionNames:
    return _ContributionNames(
        commands=tuple(command.name for command in staged.commands),
        tools=tuple(registered.tool.name for registered in staged.tools),
        providers=tuple(registered.provider.name for registered in staged.providers),
        shortcuts=tuple(shortcut.key for shortcut in staged.shortcuts),
        flags=tuple(registered.flag.name for registered in staged.flags),
        message_renderers=tuple(
            renderer.custom_type for renderer in staged.message_renderers
        ),
        entry_renderers=tuple(
            renderer.custom_type for renderer in staged.entry_renderers
        ),
    )


def _reserved_or_taken_collision(
    names: Iterable[str],
    *,
    reserved: frozenset[str],
    taken: frozenset[str],
    reserved_reason: str,
    duplicate_reason: str,
) -> str | None:
    for name in names:
        if name in reserved:
            return reserved_reason
        if name in taken:
            return duplicate_reason
    return None


def _taken_collision(
    names: Iterable[str],
    *,
    taken: frozenset[str],
    duplicate_reason: str,
) -> str | None:
    for name in names:
        if name in taken:
            return duplicate_reason
    return None


def _preloaded_collision_reason(
    names: _ContributionNames,
    *,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
) -> str | None:
    reason = _reserved_or_taken_collision(
        names.commands,
        reserved=reserved,
        taken=taken.commands,
        reserved_reason=REASON_RESERVED_COMMAND,
        duplicate_reason=REASON_DUPLICATE_COMMAND,
    )
    if reason is not None:
        return reason
    reason = _reserved_or_taken_collision(
        names.tools,
        reserved=reserved_tools,
        taken=taken.tools,
        reserved_reason=REASON_RESERVED_TOOL,
        duplicate_reason=REASON_DUPLICATE_TOOL,
    )
    if reason is not None:
        return reason
    collision_categories = (
        (names.providers, taken.providers, REASON_DUPLICATE_PROVIDER),
        (names.shortcuts, taken.shortcuts, REASON_DUPLICATE_SHORTCUT),
        (names.flags, taken.flags, REASON_DUPLICATE_FLAG),
        (
            names.message_renderers,
            taken.message_renderers,
            REASON_DUPLICATE_MESSAGE_RENDERER,
        ),
        (
            names.entry_renderers,
            taken.entry_renderers,
            REASON_DUPLICATE_ENTRY_RENDERER,
        ),
    )
    for category_names, category_taken, duplicate_reason in collision_categories:
        reason = _taken_collision(
            category_names,
            taken=category_taken,
            duplicate_reason=duplicate_reason,
        )
        if reason is not None:
            return reason
    return None


def _normalize_contribution_name_category(
    category: tuple[str, ...],
) -> tuple[str, ...]:
    if any(type(name) is not str for name in category):
        raise TypeError("extension contribution name is not an exact string")
    copied = tuple(category)
    if len(frozenset(copied)) != len(copied):
        raise ValueError("duplicate extension contribution name")
    return copied


def _normalize_contribution_names(names: _ContributionNames) -> _ContributionNames:
    """Copy exact immutable strings while preserving named category binding."""

    return _ContributionNames(
        commands=_normalize_contribution_name_category(names.commands),
        tools=_normalize_contribution_name_category(names.tools),
        providers=_normalize_contribution_name_category(names.providers),
        shortcuts=_normalize_contribution_name_category(names.shortcuts),
        flags=_normalize_contribution_name_category(names.flags),
        message_renderers=_normalize_contribution_name_category(
            names.message_renderers
        ),
        entry_renderers=_normalize_contribution_name_category(names.entry_renderers),
    )


def _prepare_contribution_names_commit(
    names: _ContributionNames,
    taken: _TakenContributionState,
) -> _TakenContributionState:
    """Build the complete successor reservation state without mutating ``taken``."""

    return _TakenContributionState(
        commands=taken.commands.union(names.commands),
        tools=taken.tools.union(names.tools),
        providers=taken.providers.union(names.providers),
        shortcuts=taken.shortcuts.union(names.shortcuts),
        flags=taken.flags.union(names.flags),
        message_renderers=taken.message_renderers.union(names.message_renderers),
        entry_renderers=taken.entry_renderers.union(names.entry_renderers),
    )


def _finalize_preloaded_extension(
    existing: ActivatedExtension,
    *,
    descriptor: ExtensionDescriptor,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
    diagnostic: Callable[[str], None] | None,
) -> tuple[ActivatedExtension, _TakenContributionState]:
    """Validate and commit one pending preload without running it again."""

    if existing.status != "activated":
        return existing, taken
    pending_activation = existing._pending_activation
    if pending_activation is None:
        return (
            _disabled(descriptor, REASON_ACTIVATION_ERROR, "invalid preload state"),
            taken,
        )
    api = pending_activation.claim()
    if api is None:
        # Re-finalizing the same pending batch is a bounded disabled outcome.
        # The first final candidate owns the transferred host, so this path
        # deliberately has nothing to dispose.
        return (
            _disabled(descriptor, REASON_ACTIVATION_ERROR, "invalid preload state"),
            taken,
        )
    ownership_transferred = False
    try:
        names = _normalize_contribution_names(_activated_contribution_names(existing))
        collision = _preloaded_collision_reason(
            names,
            reserved=reserved,
            reserved_tools=reserved_tools,
            taken=taken,
        )
        if collision is not None:
            return _disabled(descriptor, collision, None), taken
        # Complete every potentially fallible hash/equality operation before
        # activation commit can flush the frozen messages.
        prepared_names = _prepare_contribution_names_commit(names, taken)
        # Build the immutable result before commit flushes staged messages, so
        # a malformed/corrupted finalization cannot leak candidate effects.
        finalized = replace(
            existing,
            _pending_activation=None,
            _activation_host=api,
        )
        _ActivationApi._commit_activation(
            api,
            _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
        )
        ownership_transferred = True
        return finalized, prepared_names
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a corrupted host
        return (
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            ),
            taken,
        )
    finally:
        if not ownership_transferred:
            _dispose_activation_host_with_diagnostic(api, diagnostic)


@dataclass(frozen=True, slots=True)
class _ResolvedActivationEntry:
    activate: Callable[..., object]


@dataclass(frozen=True, slots=True)
class _FailedActivationEntry:
    disabled: ActivatedExtension


_ActivationEntryResolution: TypeAlias = (
    _ResolvedActivationEntry | _FailedActivationEntry
)


def _resolve_activation_entry(
    descriptor: ExtensionDescriptor,
) -> _ActivationEntryResolution:
    try:
        module = _import_entry_module(descriptor)
    except _ActivationError as err:
        return _FailedActivationEntry(_disabled(descriptor, err.reason, err.diagnostic))

    # A module-level `__getattr__` can execute code, so resolution remains
    # inside the same fail-closed boundary as extension execution.
    try:
        activate: object = getattr(module, descriptor.entry_function, None)
        if activate is None or not callable(activate):
            return _FailedActivationEntry(
                _disabled(descriptor, REASON_NO_ACTIVATE, None)
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _FailedActivationEntry(
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            )
        )
    return _ResolvedActivationEntry(activate)


def _execute_activation_entry(
    descriptor: ExtensionDescriptor,
    activate: Callable[..., object],
    api: _ActivationApi,
    diagnostic: Callable[[str], None] | None,
) -> ActivatedExtension | None:
    try:
        result = activate(api)
        if inspect.isawaitable(result):
            _run_awaitable(
                result,
                abandon=lambda: _dispose_activation_host_with_diagnostic(
                    api, diagnostic
                ),
            )
    except _ActivationError as err:
        return _disabled(descriptor, err.reason, err.diagnostic)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _disabled(descriptor, REASON_ACTIVATION_ERROR, _safe_diagnostic(err))

    return None


def _activate_one(
    descriptor: ExtensionDescriptor,
    *,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
    diagnostic: Callable[[str], None] | None,
    commit_activation: bool = True,
) -> tuple[ActivatedExtension, _TakenContributionState]:
    resolution = _resolve_activation_entry(descriptor)
    if isinstance(resolution, _FailedActivationEntry):
        return resolution.disabled, taken

    api = _ActivationApi(
        descriptor.name,
        reserved=reserved,
        taken=frozenset(taken.commands),
        reserved_tools=reserved_tools,
        taken_tools=frozenset(taken.tools),
        taken_providers=frozenset(taken.providers),
        taken_shortcuts=frozenset(taken.shortcuts),
        taken_flags=frozenset(taken.flags),
        taken_message_renderers=frozenset(taken.message_renderers),
        taken_entry_renderers=frozenset(taken.entry_renderers),
        outbox=outbox,
        custom_outbox=custom_outbox,
    )
    ownership_transferred = False
    try:
        disabled = _execute_activation_entry(
            descriptor, resolution.activate, api, diagnostic
        )
        if disabled is not None:
            return disabled, taken

        staged = _ActivationApi._seal_and_freeze(
            api,
            _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
        )
        if staged.failure is not None:
            failure_reason, failure_diagnostic = staged.failure
            return _disabled(descriptor, failure_reason, failure_diagnostic), taken
        names = _normalize_contribution_names(_staged_contribution_names(staged))
        collision = _preloaded_collision_reason(
            names,
            reserved=reserved,
            reserved_tools=reserved_tools,
            taken=taken,
        )
        if collision is not None:
            return _disabled(descriptor, collision, None), taken
        prepared_names = _prepare_contribution_names_commit(names, taken)
        # Construct the immutable result before commit can flush staged user
        # messages. Any bad host/result shape is then disabled without effects.
        activated = ActivatedExtension(
            name=descriptor.name,
            version=descriptor.version,
            path_label=descriptor.path_label,
            status="activated",
            reason=None,
            commands=staged.commands,
            diagnostic=None,
            hooks=staged.hooks,
            tools=staged.tools,
            providers=staged.providers,
            unregistered_providers=staged.unregistered_providers,
            shortcuts=staged.shortcuts,
            flags=staged.flags,
            message_renderers=staged.message_renderers,
            entry_renderers=staged.entry_renderers,
            custom_messages=staged.custom_messages,
            _activation_key=_descriptor_activation_key(descriptor),
            _pending_activation=(
                None if commit_activation else _PendingActivation(api)
            ),
            _activation_host=(api if commit_activation else None),
        )
        if commit_activation:
            _ActivationApi._commit_activation(
                api,
                _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
            )
        ownership_transferred = True
        return activated, prepared_names
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a corrupted host
        return (
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            ),
            taken,
        )
    finally:
        if not ownership_transferred:
            _dispose_activation_host_with_diagnostic(api, diagnostic)


@dataclass(slots=True)
class _ExtensionCandidate:
    """Optional runtime holder before reload/startup ownership is transferred."""

    runtime: _ExtensionRuntime | None = None

    def adopt(
        self,
        runtime: _ExtensionRuntime,
        diagnostic: Callable[[str], None],
    ) -> None:
        """Take ownership immediately after the composition seam returns."""

        if self.runtime is None:
            self.runtime = runtime
            return
        _report_activation_cleanup(
            _dispose_activation_hosts(runtime.activation_hosts),
            diagnostic,
        )
        raise ExtensionCapabilityError("extension candidate already owns a runtime")

    def publish(self) -> bool:
        """Transfer the held runtime's hosts and clear the optional holder."""

        runtime = self.runtime
        if runtime is None or not _publish_activation_hosts_atomically(
            runtime.activation_hosts
        ):
            return False
        self.runtime = None
        return True

    def dispose(self) -> _ActivationCleanup:
        """Dispose unpublished hosts, retaining only an inaccessible runtime."""

        runtime = self.runtime
        if runtime is None:
            return _ActivationCleanup()
        cleanup = _dispose_activation_hosts(runtime.activation_hosts)
        if cleanup.failed == 0:
            self.runtime = None
        return cleanup


def _passthrough_disabled(descriptor: ExtensionDescriptor) -> ActivatedExtension:
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="disabled",
        reason=descriptor.reason,
        commands=(),
        diagnostic=None,
        _activation_key=_descriptor_activation_key(descriptor),
    )


def _disabled(
    descriptor: ExtensionDescriptor,
    reason: str,
    diagnostic: str | None,
) -> ActivatedExtension:
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="disabled",
        reason=reason,
        commands=(),
        diagnostic=diagnostic,
        _activation_key=_descriptor_activation_key(descriptor),
    )


def safe_activation_metadata(
    activated: Sequence[ActivatedExtension],
) -> list[dict[str, object]]:
    """Project activation results to archive-safe metadata.

    Only safe labels are emitted: name, version, path label, status,
    reason code, and the registered command names. Command handlers,
    descriptions, source code, and diagnostics are excluded.
    """

    return [
        {
            "name": item.name,
            "version": item.version,
            "path_label": item.path_label,
            "status": item.status,
            "reason": item.reason,
            "commands": [command.name for command in item.commands],
            "message_renderers": [
                renderer.custom_type for renderer in item.message_renderers
            ],
            "entry_renderers": [
                renderer.custom_type for renderer in item.entry_renderers
            ],
        }
        for item in activated
    ]
