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
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from pipy_harness.native.extension_loader import (
    _import_entry_module,
    _run_awaitable,
)
from pipy_harness.native.extension_ui import (
    _CUSTOM_RENDER_MAX_CHARS,
    _CollectingUi,
    coerce_tool_render_lines,
    lines_component,  # noqa: F401 - re-exported via pipy_harness.extensions
)
from pipy_harness.native.extensions import ExtensionDescriptor
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    BeforeAgentStartResult,  # noqa: F401 - re-exported via pipy_harness.extensions
    BeforeProviderHeadersEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    BeforeProviderRequestEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    ChromeComponent,  # noqa: F401 - re-exported via pipy_harness.extensions
    CompletionFn,  # noqa: F401 - re-exported via pipy_harness.extensions
    CustomComponent,  # noqa: F401 - re-exported via pipy_harness.extensions
    CustomComponentDriver,
    CustomComponentFactory,  # noqa: F401 - re-exported via pipy_harness.extensions
    EntryRenderContext,
    ExtensionCodingSessionControl,
    ExtensionFlag,
    ExtensionModelRuntimeControl,
    ExtensionOAuthConfig,
    ExtensionProvider,
    ExtensionTool,
    ExtensionUi,
    ExtensionUiDriver,
    FooterData,  # noqa: F401 - re-exported via pipy_harness.extensions
    InputEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    InputTransform,  # noqa: F401 - re-exported via pipy_harness.extensions
    LifecycleEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    MessageRenderComponent,  # noqa: F401 - re-exported via pipy_harness.extensions
    MessageRenderContext,
    ProviderContext,  # noqa: F401 - re-exported via pipy_harness.extensions
    ProviderRequestTransform,  # noqa: F401 - re-exported via pipy_harness.extensions
    QueuedCustomMessage,
    QueuedUserMessage,
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
    REASON_RESERVED_TOOL,
    RESERVED_SHORTCUT_KEYS,
    RegisteredFlag,
    RegisteredProvider,
    RegisteredTool,
    RenderedCustomEntry,
    SessionBeforeEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    SessionDecision,  # noqa: F401 - re-exported via pipy_harness.extensions
    ThemeColor,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolBlock,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolCallEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolRenderComponent,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolRenderContext,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolRenderTheme,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolResult,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolResultEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    ToolResultTransform,  # noqa: F401 - re-exported via pipy_harness.extensions
    UserBashDecision,  # noqa: F401 - re-exported via pipy_harness.extensions
    UserBashDispatch,  # noqa: F401 - re-exported via pipy_harness.extensions
    UserBashEvent,  # noqa: F401 - re-exported via pipy_harness.extensions
    WidgetPlacement,  # noqa: F401 - re-exported via pipy_harness.extensions
    _ActivationError,
    _is_valid_command_name,
    _safe_diagnostic,
    is_valid_custom_entry_type,
    normalize_shortcut_key,
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
    hooks: Mapping[str, tuple[HookHandler, ...]] = field(default_factory=dict)
    tools: tuple[RegisteredTool, ...] = ()
    providers: tuple[RegisteredProvider, ...] = ()
    unregistered_providers: tuple[str, ...] = ()
    shortcuts: tuple[RegisteredShortcut, ...] = ()
    flags: tuple[RegisteredFlag, ...] = ()
    message_renderers: tuple[RegisteredMessageRenderer, ...] = ()
    entry_renderers: tuple[RegisteredEntryRenderer, ...] = ()
    custom_messages: tuple[QueuedCustomMessage, ...] = ()
    _activation_key: str | None = field(default=None, repr=False, compare=False)
    _activation_api: "_ActivationApi | None" = field(
        default=None, repr=False, compare=False
    )


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


@dataclass(frozen=True, slots=True)
class _SessionExtensionGeneration:
    """Canonical live extension state for one session generation.

    The activated runtime owns every registered contribution and the outbox
    lists extensions retain after activation. Parsed flag values belong beside
    that runtime because every command, hook, provider, tool, renderer, and UI
    dispatch must observe flags from the same live generation.
    """

    runtime: _ExtensionRuntime
    flag_values: dict[str, object]


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


@dataclass(slots=True)
class _TakenContributions:
    """Names committed by earlier extensions in the current activation pass."""

    commands: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)
    shortcuts: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    message_renderers: set[str] = field(default_factory=set)
    entry_renderers: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _StagedContributions:
    """All atomic registrations collected from one successful activation."""

    commands: tuple[RegisteredCommand, ...]
    tools: tuple[RegisteredTool, ...]
    providers: tuple[RegisteredProvider, ...]
    shortcuts: tuple[RegisteredShortcut, ...]
    flags: tuple[RegisteredFlag, ...]
    message_renderers: tuple[RegisteredMessageRenderer, ...]
    entry_renderers: tuple[RegisteredEntryRenderer, ...]
    custom_messages: tuple[QueuedCustomMessage, ...]


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


def _copy_session_data(value: object) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
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


def _normalize_provider_name(raw_name: object) -> str:
    if not isinstance(raw_name, str):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    name = raw_name.strip()
    if not name or "/" in name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return name


def _normalize_provider_models(models: object) -> tuple[str, ...]:
    if not isinstance(models, tuple) or not models:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    model_ids: list[str] = []
    for model in models:
        if not isinstance(model, str):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_id = model.strip()
        if not model_id:
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids.append(model_id)
    return tuple(model_ids)


def _normalize_default_model(
    default_model: object,
    model_ids: tuple[str, ...],
) -> str | None:
    if isinstance(default_model, str):
        default_model = default_model.strip()
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
    oauth_name = oauth.name.strip() if isinstance(oauth.name, str) else ""
    if not oauth_name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    if (
        not callable(oauth.login)
        or not callable(oauth.refresh_token)
        or not callable(oauth.get_api_key)
        or (oauth.modify_models is not None and not callable(oauth.modify_models))
    ):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return ExtensionOAuthConfig(
        name=oauth_name,
        login=oauth.login,
        refresh_token=oauth.refresh_token,
        get_api_key=oauth.get_api_key,
        modify_models=oauth.modify_models,
    )


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
    ) -> None:
        self._extension_name = extension_name
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
        self._activated = False

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Enqueue a deterministic user turn (drained by the session loop)."""

        message = QueuedUserMessage(content=str(content), options=dict(options or {}))
        if self._activated:
            self._outbox.append(message)
        else:
            self._staged_messages.append(message)

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Stage a custom session message until activation succeeds."""

        queued = coerce_custom_message(message, options)
        if self._activated:
            self._custom_outbox.append(queued)
        else:
            self._staged_custom_messages.append(queued)

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        self.send_message(message, options)

    def commit_activation(self) -> None:
        """Flush staged `send_user_message` calls after successful activation."""

        self._activated = True
        self._outbox.extend(self._staged_messages)
        self._staged_messages = []

    def staged_custom_messages(self) -> tuple[QueuedCustomMessage, ...]:
        return tuple(self._staged_custom_messages)

    def register_tool(self, tool: ExtensionTool) -> None:
        try:
            self._validate_and_stage_tool(tool)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage_tool(self, tool: ExtensionTool) -> None:
        if not isinstance(tool, ExtensionTool):
            raise _ActivationError(REASON_INVALID_TOOL)
        name = tool.name
        if not isinstance(name, str) or not name:
            raise _ActivationError(REASON_INVALID_TOOL)
        if name in self._reserved_tools:
            raise _ActivationError(REASON_RESERVED_TOOL)
        if name in self._taken_tools or name in self._staged_tools:
            raise _ActivationError(REASON_DUPLICATE_TOOL)
        if not callable(tool.handler):
            raise _ActivationError(REASON_INVALID_TOOL)
        if not isinstance(tool.input_schema, Mapping):
            raise _ActivationError(REASON_INVALID_TOOL)
        try:
            # Construct a ToolDefinition to validate the name + schema in
            # pipy's supported subset (same validation built-in tools get).
            ToolDefinition(
                name=name,
                description=str(tool.description),
                input_schema=dict(tool.input_schema),
            )
        except (ValueError, TypeError) as exc:
            raise _ActivationError(REASON_INVALID_TOOL, _safe_diagnostic(exc)) from None
        self._staged_tools[name] = RegisteredTool(
            tool=tool, extension=self._extension_name
        )

    def staged_tools(self) -> tuple[RegisteredTool, ...]:
        return tuple(self._staged_tools.values())

    def register_provider(self, provider: ExtensionProvider) -> None:
        try:
            self._validate_and_stage_provider(provider)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage_provider(self, provider: ExtensionProvider) -> None:
        if not isinstance(provider, ExtensionProvider):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        name = _normalize_provider_name(provider.name)
        if not callable(provider.factory):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids = _normalize_provider_models(provider.models)
        default_model = _normalize_default_model(provider.default_model, model_ids)
        oauth = _normalize_provider_oauth(provider.oauth)
        # Providers MAY override a built-in of the same name (Pi behavior;
        # unregister restores it), so there is no reserved-name check; only
        # a duplicate registration across extensions is rejected.
        if name in self._staged_providers or name in self._taken_providers:
            raise _ActivationError(REASON_DUPLICATE_PROVIDER)
        normalized = ExtensionProvider(
            name=name,
            default_model=default_model,
            models=model_ids,
            factory=provider.factory,
            oauth=oauth,
        )
        self._staged_providers[name] = RegisteredProvider(
            provider=normalized, extension=self._extension_name
        )

    def unregister_provider(self, name: str) -> None:
        if isinstance(name, str) and name and name not in self._staged_unregistered:
            self._staged_unregistered.append(name)

    def staged_providers(self) -> tuple[RegisteredProvider, ...]:
        return tuple(self._staged_providers.values())

    def staged_unregistered(self) -> tuple[str, ...]:
        return tuple(self._staged_unregistered)

    def register_flag(self, flag: ExtensionFlag) -> None:
        try:
            self._validate_and_stage_flag(flag)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage_flag(self, flag: ExtensionFlag) -> None:
        if not isinstance(flag, ExtensionFlag):
            raise _ActivationError(REASON_INVALID_FLAG)
        raw_name = flag.name
        if not isinstance(raw_name, str):
            raise _ActivationError(REASON_INVALID_FLAG)
        name = raw_name.strip()
        if not _is_valid_command_name(name):
            raise _ActivationError(REASON_INVALID_FLAG)
        if name in self._taken_flags or name in self._staged_flags:
            raise _ActivationError(REASON_DUPLICATE_FLAG)
        flag_type = flag.flag_type
        if flag_type not in ("boolean", "string"):
            raise _ActivationError(REASON_INVALID_FLAG)
        default = flag.default
        if (
            flag_type == "boolean"
            and default is not None
            and not isinstance(default, bool)
        ):
            raise _ActivationError(REASON_INVALID_FLAG)
        if (
            flag_type == "string"
            and default is not None
            and not isinstance(default, str)
        ):
            raise _ActivationError(REASON_INVALID_FLAG)
        self._staged_flags[name] = RegisteredFlag(
            flag=ExtensionFlag(
                name=name,
                flag_type=flag_type,
                description=flag.description,
                default=default,
            ),
            extension=self._extension_name,
            values=self._flag_values,
        )
        if default is not None:
            self._flag_values[name] = default

    def get_flag(self, name: str) -> object | None:
        return self._flag_values.get(str(name))

    def staged_flags(self) -> tuple[RegisteredFlag, ...]:
        return tuple(self._staged_flags.values())

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        try:
            self._validate_and_stage_message_renderer(custom_type, renderer)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        if not isinstance(custom_type, str):
            raise _ActivationError(REASON_INVALID_MESSAGE_RENDERER)
        name = custom_type.strip()
        if not is_valid_custom_entry_type(name):
            raise _ActivationError(REASON_INVALID_MESSAGE_RENDERER)
        if not callable(renderer):
            raise _ActivationError(REASON_INVALID_MESSAGE_RENDERER)
        if (
            name in self._taken_message_renderers
            or name in self._staged_message_renderers
        ):
            raise _ActivationError(REASON_DUPLICATE_MESSAGE_RENDERER)
        self._staged_message_renderers[name] = RegisteredMessageRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def staged_message_renderers(self) -> tuple[RegisteredMessageRenderer, ...]:
        return tuple(self._staged_message_renderers.values())

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        try:
            self._validate_and_stage_entry_renderer(custom_type, renderer)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        if not isinstance(custom_type, str):
            raise _ActivationError(REASON_INVALID_ENTRY_RENDERER)
        name = custom_type.strip()
        if not is_valid_custom_entry_type(name) or not callable(renderer):
            raise _ActivationError(REASON_INVALID_ENTRY_RENDERER)
        if name in self._taken_entry_renderers or name in self._staged_entry_renderers:
            raise _ActivationError(REASON_DUPLICATE_ENTRY_RENDERER)
        self._staged_entry_renderers[name] = RegisteredEntryRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def staged_entry_renderers(self) -> tuple[RegisteredEntryRenderer, ...]:
        return tuple(self._staged_entry_renderers.values())

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None:
        try:
            self._validate_and_stage(name, description, handler)
        except _ActivationError as err:
            # Record the first failure so the extension is disabled even
            # if it swallows this exception; then re-raise so a
            # well-behaved extension aborts immediately.
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def _validate_and_stage(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None:
        if not isinstance(name, str) or not _is_valid_command_name(name):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        if name in self._reserved:
            raise _ActivationError(REASON_RESERVED_COMMAND)
        if name in self._taken or name in self._staged:
            raise _ActivationError(REASON_DUPLICATE_COMMAND)
        if not callable(handler):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        self._staged[name] = RegisteredCommand(
            name=name,
            description=str(description),
            handler=handler,
            extension=self._extension_name,
        )

    def register_shortcut(self, key: str, handler: CommandHandler) -> None:
        try:
            if not isinstance(key, str) or not key.strip():
                raise _ActivationError(REASON_INVALID_SHORTCUT)
            if not callable(handler):
                raise _ActivationError(REASON_INVALID_SHORTCUT)
            normalized = normalize_shortcut_key(key)
            # A single-character key (a bare printable like "a"/"." or a raw
            # control char) would shadow ordinary typing in the editor, since
            # the shortcut check runs before text insertion. Only multi-char
            # named keys (e.g. "ctrl-g") may be bound.
            if len(normalized) <= 1:
                raise _ActivationError(REASON_INVALID_SHORTCUT)
            # A modifier-only key with an empty base (e.g. "ctrl-" from "Ctrl+")
            # can never be emitted by the decoder; refuse it rather than
            # register an unreachable binding.
            if normalized.endswith("-"):
                raise _ActivationError(REASON_INVALID_SHORTCUT)
            if normalized in RESERVED_SHORTCUT_KEYS:
                raise _ActivationError(REASON_RESERVED_SHORTCUT)
            if (
                normalized in self._taken_shortcuts
                or normalized in self._staged_shortcuts
            ):
                raise _ActivationError(REASON_DUPLICATE_SHORTCUT)
            self._staged_shortcuts[normalized] = RegisteredShortcut(
                key=normalized,
                handler=handler,
                extension=self._extension_name,
            )
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    def staged_shortcuts(self) -> tuple[RegisteredShortcut, ...]:
        return tuple(self._staged_shortcuts.values())

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

        if handler is None:

            def _decorator(func: HookHandler) -> HookHandler:
                self._register_hook(event, func)
                return func

            return _decorator
        self._register_hook(event, handler)
        return handler

    def _register_hook(self, event: str, handler: HookHandler) -> None:
        try:
            if not isinstance(event, str) or not event:
                raise _ActivationError(REASON_INVALID_HOOK)
            if not callable(handler):
                raise _ActivationError(REASON_INVALID_HOOK)
            self._hooks.setdefault(event, []).append(handler)
        except _ActivationError as err:
            if self._failure is None:
                self._failure = (err.reason, err.diagnostic)
            raise

    @property
    def failure(self) -> tuple[str, str | None] | None:
        return self._failure

    def staged_commands(self) -> tuple[RegisteredCommand, ...]:
        return tuple(self._staged.values())

    def staged_hooks(self) -> dict[str, tuple[HookHandler, ...]]:
        return {event: tuple(handlers) for event, handlers in self._hooks.items()}


def activate_extensions(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
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
    )
    return list(batch.activated)


def activate_extension_batch(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
    preloaded: ExtensionActivationBatch | None = None,
    pending: bool = False,
) -> ExtensionActivationBatch:
    """Activate once, or finalize a pending pre-trust batch in final order."""

    if preloaded is not None and not preloaded.pending:
        raise ValueError("preloaded extension batch is already finalized")
    if preloaded is not None and pending:
        raise ValueError("a final merge cannot remain pending")

    reserved = frozenset(reserved_command_names)
    reserved_tools = frozenset(reserved_tool_names)
    taken = _TakenContributions()
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

    for descriptor in descriptors:
        if descriptor.status != "loadable":
            # Discovery already disabled this; never import it.
            results.append(_passthrough_disabled(descriptor))
            continue
        key = _descriptor_activation_key(descriptor)
        existing = preloaded_by_key.get(key)
        if existing is not None:
            reused = _finalize_preloaded_extension(
                existing,
                descriptor=descriptor,
                reserved=reserved,
                reserved_tools=reserved_tools,
                taken=taken,
            )
            results.append(reused)
            continue
        # Pending pre-trust activation stages each extension independently.
        # Cross-extension collisions are provisional because the final reserved
        # set can disable an earlier extension and free its names for a later
        # one. Resolve those collisions only once, in final descriptor order.
        activation_taken = _TakenContributions() if pending else taken
        results.append(
            _activate_one(
                descriptor,
                reserved=reserved,
                reserved_tools=reserved_tools,
                taken=activation_taken,
                outbox=outbox,
                custom_outbox=custom_outbox,
                commit_activation=not pending,
            )
        )
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
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
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
        return json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
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
            owner.values[parsed.name] = parsed.value
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


def _staged_contribution_names(staged: _StagedContributions) -> _ContributionNames:
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
    taken: set[str],
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
    taken: set[str],
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
    taken: _TakenContributions,
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


def _commit_contribution_names(
    names: _ContributionNames,
    taken: _TakenContributions,
) -> None:
    """Commit every category only after the complete collision check passes."""

    taken.commands.update(names.commands)
    taken.tools.update(names.tools)
    taken.providers.update(names.providers)
    taken.shortcuts.update(names.shortcuts)
    taken.flags.update(names.flags)
    taken.message_renderers.update(names.message_renderers)
    taken.entry_renderers.update(names.entry_renderers)


def _finalize_preloaded_extension(
    existing: ActivatedExtension,
    *,
    descriptor: ExtensionDescriptor,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributions,
) -> ActivatedExtension:
    """Validate and commit one pending preload without running it again."""

    if existing.status != "activated":
        return existing
    api = existing._activation_api
    if api is None:
        return _disabled(descriptor, REASON_ACTIVATION_ERROR, "invalid preload state")
    names = _activated_contribution_names(existing)
    collision = _preloaded_collision_reason(
        names,
        reserved=reserved,
        reserved_tools=reserved_tools,
        taken=taken,
    )
    if collision is not None:
        return _disabled(descriptor, collision, None)
    _commit_contribution_names(names, taken)
    api.commit_activation()
    return replace(existing, _activation_api=None)


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
) -> ActivatedExtension | None:
    try:
        result = activate(api)
        if inspect.isawaitable(result):
            _run_awaitable(result)
    except _ActivationError as err:
        return _disabled(descriptor, err.reason, err.diagnostic)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _disabled(descriptor, REASON_ACTIVATION_ERROR, _safe_diagnostic(err))

    # A failed registration disables the extension even if its own code
    # swallowed the error: no partial registration set is ever committed.
    if api.failure is not None:
        failure_reason, failure_diagnostic = api.failure
        return _disabled(descriptor, failure_reason, failure_diagnostic)
    return None


def _collect_staged_contributions(api: _ActivationApi) -> _StagedContributions:
    return _StagedContributions(
        commands=api.staged_commands(),
        tools=api.staged_tools(),
        providers=api.staged_providers(),
        shortcuts=api.staged_shortcuts(),
        flags=api.staged_flags(),
        message_renderers=api.staged_message_renderers(),
        entry_renderers=api.staged_entry_renderers(),
        custom_messages=api.staged_custom_messages(),
    )


def _activate_one(
    descriptor: ExtensionDescriptor,
    *,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributions,
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
    commit_activation: bool = True,
) -> ActivatedExtension:
    resolution = _resolve_activation_entry(descriptor)
    if isinstance(resolution, _FailedActivationEntry):
        return resolution.disabled

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
    disabled = _execute_activation_entry(descriptor, resolution.activate, api)
    if disabled is not None:
        return disabled

    staged = _collect_staged_contributions(api)
    _commit_contribution_names(_staged_contribution_names(staged), taken)
    if commit_activation:
        api.commit_activation()
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="activated",
        reason=None,
        commands=staged.commands,
        diagnostic=None,
        hooks=api.staged_hooks(),
        tools=staged.tools,
        providers=staged.providers,
        unregistered_providers=api.staged_unregistered(),
        shortcuts=staged.shortcuts,
        flags=staged.flags,
        message_renderers=staged.message_renderers,
        entry_renderers=staged.entry_renderers,
        custom_messages=staged.custom_messages,
        _activation_key=_descriptor_activation_key(descriptor),
        _activation_api=None if commit_activation else api,
    )


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
