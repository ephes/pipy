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
- The collectors (`extension_command_map`, `extension_shortcuts`,
  `extension_tools`, `extension_event_hooks`, ...) and dispatchers
  (`dispatch_extension_command`, `dispatch_extension_shortcut`, the hook
  dispatchers), plus `safe_activation_metadata(activated)`.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pipy_harness.native.extensions import ExtensionDescriptor
from pipy_harness.native.tools.base import ToolDefinition

CommandHandler = Callable[..., object]

# Activation reason codes (safe, enumerable labels).
REASON_IMPORT_ERROR: str = "import_error"
REASON_NO_ACTIVATE: str = "no_activate"
REASON_ACTIVATION_ERROR: str = "activation_error"
REASON_INVALID_COMMAND_NAME: str = "invalid_command_name"
REASON_RESERVED_COMMAND: str = "reserved_command"
REASON_DUPLICATE_COMMAND: str = "duplicate_command"
REASON_INVALID_HOOK: str = "invalid_hook"
REASON_INVALID_TOOL: str = "invalid_tool"
REASON_RESERVED_TOOL: str = "reserved_tool"
REASON_DUPLICATE_TOOL: str = "duplicate_tool"
REASON_INVALID_PROVIDER: str = "invalid_provider"
REASON_DUPLICATE_PROVIDER: str = "duplicate_provider"
REASON_INVALID_SHORTCUT: str = "invalid_shortcut"
REASON_RESERVED_SHORTCUT: str = "reserved_shortcut"
REASON_DUPLICATE_SHORTCUT: str = "duplicate_shortcut"

# Built-in hotkey / editor keys an extension shortcut may never claim, so a
# binding can never shadow core input editing or the app hotkeys. Compared
# against the normalized key string (see `normalize_shortcut_key`).
RESERVED_SHORTCUT_KEYS: frozenset[str] = frozenset(
    {
        "enter",
        "tab",
        "shift-tab",
        "backspace",
        "esc",
        "ctrl-c",
        "ctrl-d",
        "ctrl-u",
        "ctrl-y",
        "ctrl-z",
        "ctrl-o",
        "ctrl-p",
        "ctrl-t",
        "ctrl-v",
        "shift-ctrl-p",
        "alt-enter",
        "alt-up",
        "home",
        "end",
        "up",
        "down",
        "left",
        "right",
        "paste",
        # Defensive: common editor keys that are not decoded to a named form
        # today but must never be claimable if the decoder grows to emit them.
        "delete",
        "insert",
        "pageup",
        "pagedown",
    }
)

# Canonical modifier order for a normalized shortcut key, matching pipy's
# decoded forms (e.g. "shift-ctrl-p", "shift-tab"). Modifiers are re-emitted in
# this order so "Ctrl+Shift+P" and "Shift+Ctrl+P" canonicalize identically and a
# reserved hotkey cannot be bypassed by reordering its modifiers.
_SHORTCUT_MODIFIERS: tuple[str, ...] = ("shift", "ctrl", "alt", "meta")


def normalize_shortcut_key(key: str) -> str:
    """Normalize a Pi-style shortcut key to pipy's internal key string.

    Accepts ``"Ctrl+."`` / ``"ctrl+x"`` (Pi's ``+``-joined form), lowercases and
    ``+``→``-`` it to pipy's decoded form (``"ctrl-."`` / ``"ctrl-x"``), and
    re-emits leading modifiers in a canonical order (``shift`` before ``ctrl``
    before ``alt``), so modifier reordering can neither bypass a reserved key
    nor create duplicate bindings. A single character is returned as-is.
    """

    raw = key.strip().lower().replace("+", "-")
    if not raw:
        return raw
    parts = raw.split("-")
    modifiers: list[str] = []
    index = 0
    # Leading tokens that are modifiers (but never the final base token) are
    # collected; the remainder is the base key (which may itself contain "-").
    while index < len(parts) - 1 and parts[index] in _SHORTCUT_MODIFIERS:
        modifiers.append(parts[index])
        index += 1
    base = "-".join(parts[index:])
    ordered = [mod for mod in _SHORTCUT_MODIFIERS if mod in modifiers]
    return "-".join([*ordered, base]) if ordered else base

# Bound an extension tool's provider-visible output.
_TOOL_OUTPUT_MAX_CHARS: int = 32 * 1024

# Event names (the dispatched subset grows per slice).
EVENT_TOOL_CALL: str = "tool_call"
EVENT_SESSION_START: str = "session_start"
EVENT_SESSION_SHUTDOWN: str = "session_shutdown"
EVENT_AGENT_START: str = "agent_start"
EVENT_AGENT_END: str = "agent_end"
EVENT_TURN_START: str = "turn_start"
EVENT_TURN_END: str = "turn_end"
EVENT_INPUT: str = "input"
EVENT_BEFORE_AGENT_START: str = "before_agent_start"
EVENT_TOOL_RESULT: str = "tool_result"

# Bound a transformed tool-result observation before it reaches the model.
_TOOL_RESULT_MAX_CHARS: int = 60 * 1024

LIFECYCLE_EVENTS: tuple[str, ...] = (
    EVENT_SESSION_START,
    EVENT_SESSION_SHUTDOWN,
    EVENT_AGENT_START,
    EVENT_AGENT_END,
    EVENT_TURN_START,
    EVENT_TURN_END,
)

HookHandler = Callable[..., object]


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """An observe-only lifecycle event passed to `@api.on(<event>)` hooks.

    `name` is the event (for example `session_start`). `reason` is the
    session-start reason (`"startup"`, `"reload"`, ...) where applicable,
    and `None` otherwise. The event carries only safe metadata.
    """

    name: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InputEvent:
    """A submitted prompt presented to an `input` hook before a turn."""

    text: str


@dataclass(frozen=True, slots=True)
class InputTransform:
    """Returned by an `input` hook to replace the submitted prompt text."""

    text: str


@dataclass(frozen=True, slots=True)
class BeforeAgentStartEvent:
    """Presented to a `before_agent_start` hook before an agent run."""

    system_prompt: str


@dataclass(frozen=True, slots=True)
class BeforeAgentStartResult:
    """Returned by a `before_agent_start` hook to inject bounded context.

    `append_system_prompt` is appended (bounded) to the turn's system
    prompt. Later slices may add more fields (custom messages, model
    options); they default off so existing extensions keep working.
    """

    append_system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class QueuedUserMessage:
    """A message an extension enqueued via `api.send_user_message`."""

    content: str
    options: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolResultEvent:
    """The finalized, bounded result of a tool, shown to `tool_result` hooks.

    `tool_name` is the tool that ran (built-in or extension); `content`
    is the current provider-visible result text; `is_error` marks an
    error observation.
    """

    tool_name: str
    content: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolResultTransform:
    """Returned by a `tool_result` hook to replace the observation content."""

    content: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Returned by an extension tool handler.

    `content` is the provider-visible result text (bounded before it
    reaches the model). `details` is structured local state/metadata for
    rendering or later hooks; it is not sent to the provider and not
    archived by default. (Pi-shaped `content`/`details`; the richer
    block-content + `terminate` shape arrives in a later slice.)
    """

    content: str
    details: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ExtensionTool:
    """A model-visible tool an extension registers via `api.register_tool`.

    `input_schema` is a JSON-schema dict in pipy's supported subset
    (validated at registration). `handler(ctx, input)` receives a
    mode-aware context and the validated input mapping and returns a
    `ToolResult`.
    """

    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: Callable[..., object]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """An extension tool accepted during activation, with its owner."""

    tool: ExtensionTool
    extension: str


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """Context passed to an extension provider `factory`.

    Carries only safe selection metadata: the provider name and its
    default model. A provider extension must read its own environment /
    a future auth capability — it never receives the shared auth store.
    """

    provider_name: str
    default_model: str | None


@dataclass(frozen=True, slots=True)
class ExtensionProvider:
    """A model provider an extension registers via `api.register_provider`.

    `name` is the provider name (selectable through the catalog / `/model`);
    `default_model` and `models` describe the provider's model ids;
    `factory(ProviderContext)` builds a `ProviderPort`. A provider may
    override a built-in of the same name; `unregister_provider(name)`
    removes it and restores the built-in.
    """

    name: str
    default_model: str | None
    models: tuple[str, ...]
    factory: Callable[..., object]


@dataclass(frozen=True, slots=True)
class RegisteredProvider:
    """An extension provider accepted during activation, with its owner."""

    provider: ExtensionProvider
    extension: str


def build_extension_provider_port(registered: RegisteredProvider) -> object | None:
    """Build a `ProviderPort` from a registered extension provider.

    Calls the provider's `factory(ProviderContext)`. A factory that raises
    (or returns nothing) yields `None` rather than crashing the caller, so
    a bad provider is bounded. `KeyboardInterrupt` / `SystemExit`
    propagate.
    """

    provider = registered.provider
    context = ProviderContext(
        provider_name=provider.name, default_model=provider.default_model
    )
    try:
        port = provider.factory(context)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - bound a bad provider factory
        return None
    return port


def make_extension_context(
    cwd: str,
    has_ui: bool,
    notify_sink: "Callable[[str, str], None] | None" = None,
    *,
    messages: "Sequence[object]" = (),
    complete_fn: "CompletionFn | None" = None,
) -> CommandContext:
    """Build a mode-aware context for a tool/command/hook invocation.

    When `notify_sink` is given, `ctx.ui.notify` routes to it (live UI
    output) in addition to recording; otherwise notifications are only
    recorded (deterministic non-interactive behavior). `messages` is a
    snapshot of the live conversation used to back `ctx.conversation`;
    `complete_fn`, when given, backs `ctx.complete`.
    """

    return _CommandContext(
        cwd,
        _CollectingUi(has_ui, notify_sink),
        _ConversationView(messages),
        complete_fn,
    )


@dataclass(frozen=True, slots=True)
class ToolBlock:
    """Returned by a `tool_call` hook to block a tool call with a reason."""

    reason: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """The live model-selected tool call presented to a `tool_call` hook.

    `tool_name` is the tool the model chose; `input` is its parsed
    arguments. Trusted local hooks may inspect these to gate execution;
    this live access does not change archive policy (raw tool inputs are
    not archived by default).
    """

    tool_name: str
    input: Mapping[str, object]

_COMMAND_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_COMMAND_BODY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_DIAGNOSTIC_MAX_LENGTH: int = 200
# Cap the total `before_agent_start` system-prompt injection so a buggy or
# malicious extension cannot create unbounded provider input.
_BEFORE_AGENT_START_MAX_CHARS: int = 16 * 1024

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

    def register_tool(self, tool: "ExtensionTool") -> None: ...

    def register_provider(self, provider: "ExtensionProvider") -> None: ...

    def unregister_provider(self, name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    """One command an extension registered during activation."""

    name: str
    description: str
    handler: CommandHandler
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


@runtime_checkable
class CustomComponent(Protocol):
    """A trusted extension component driven by `ctx.ui.custom`.

    `render(width)` returns the full-screen overlay lines (the component owns
    its own styling/layout). `handle_input(key)` consumes one decoded key
    string (e.g. ``"enter"``, ``"up"``, ``"tab"``, ``"esc"``, or a printable
    character); the component finishes by calling the `done` callback it was
    built with.
    """

    def render(self, width: int) -> list[str]: ...

    def handle_input(self, key: str) -> None: ...


# A factory that builds a CustomComponent given a `done(result)` callback.
CustomComponentFactory = Callable[[Callable[..., None]], CustomComponent]
# The live driver that takes over the terminal to run a custom component.
CustomComponentDriver = Callable[[CustomComponentFactory], object]


@runtime_checkable
class ExtensionUi(Protocol):
    """Mode-aware UI handed to a command handler.

    Exposes `notify` (transient messages) and `custom` (take over the terminal
    with a custom interactive component). In non-interactive mode the methods
    behave deterministically: notifications are recorded and `custom` is a
    no-op returning ``None`` (never blocks).
    """

    has_ui: bool

    def notify(self, message: str, kind: str = "info") -> None: ...

    def custom(self, factory: CustomComponentFactory) -> object: ...


class ExtensionCapabilityError(RuntimeError):
    """A capability a handler asked for is not available in this context.

    Raised by e.g. `ctx.complete(...)` when no completion backend is wired
    (a deterministic / non-interactive dispatch), so a handler degrades
    predictably instead of crashing on a missing attribute.
    """


# A bounded one-shot completion backend: (system_prompt, user_text) -> text.
CompletionFn = Callable[[str, str], str]


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

    def complete(self, system_prompt: str, user_text: str) -> str:
        """Run one bounded provider completion and return its text.

        Raises `ExtensionCapabilityError` when no completion backend is wired
        (a non-interactive / deterministic dispatch).
        """
        ...


class _ConversationView:
    """Concrete `ConversationView` over a snapshot of the message history.

    The handler receives a snapshot taken at dispatch time; it never mutates
    the live conversation. Messages without an assistant turn yield `None`.
    """

    def __init__(self, messages: "Sequence[object]" = ()) -> None:
        self._messages = tuple(messages)

    def last_assistant_message(self) -> "AssistantMessageView | None":
        # Import here to avoid a heavy import at module load.
        from pipy_harness.native.tools.messages import AssistantMessage

        for message in reversed(self._messages):
            if isinstance(message, AssistantMessage):
                text = message.content or ""
                complete = bool(text.strip()) and not message.tool_calls
                return AssistantMessageView(text=text, complete=complete)
        return None


class _CollectingUi:
    """A mode-aware `ExtensionUi` that records notifications.

    The dispatcher returns the collected messages so the caller (the
    REPL) emits them as live UI output; nothing is archived. This keeps
    dispatch pure and testable and gives deterministic non-interactive
    behavior (notifications are recorded, never blocking).
    """

    def __init__(
        self,
        has_ui: bool,
        notify_sink: "Callable[[str, str], None] | None" = None,
        custom_driver: "CustomComponentDriver | None" = None,
    ) -> None:
        self.has_ui = has_ui
        self.messages: list[tuple[str, str]] = []
        self._notify_sink = notify_sink
        self._custom_driver = custom_driver

    def custom(self, factory: "CustomComponentFactory") -> object:
        """Run a custom interactive component, or no-op deterministically.

        Returns the component's result when a live driver is wired and a UI is
        available; otherwise returns ``None`` without constructing or driving
        the component (deterministic non-interactive behavior).
        """
        if self._custom_driver is None or not self.has_ui:
            return None
        return self._custom_driver(factory)

    def notify(self, message: str, kind: str = "info") -> None:
        safe_kind = kind if kind in ("info", "warning", "error") else "info"
        text = str(message)
        self.messages.append((safe_kind, text))
        if self._notify_sink is not None:
            try:
                self._notify_sink(safe_kind, text)
            except Exception:  # noqa: BLE001 - a UI sink must not break the handler
                pass


class _CommandContext:
    """Concrete `CommandContext` for one command invocation."""

    def __init__(
        self,
        cwd: str,
        ui: _CollectingUi,
        conversation: "ConversationView | None" = None,
        complete_fn: "CompletionFn | None" = None,
    ) -> None:
        self.cwd = cwd
        self.has_ui = ui.has_ui
        self.ui: ExtensionUi = ui
        self.conversation: ConversationView = conversation or _ConversationView()
        self._complete_fn = complete_fn

    def complete(self, system_prompt: str, user_text: str) -> str:
        if self._complete_fn is None:
            raise ExtensionCapabilityError(
                "completion is not available in this context"
            )
        return self._complete_fn(str(system_prompt), str(user_text))


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
    messages: "Sequence[object]" = (),
    complete_fn: "CompletionFn | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
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
        messages=messages,
        complete_fn=complete_fn,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
    )


def dispatch_extension_shortcut(
    key: str,
    shortcut_map: dict[str, RegisteredShortcut],
    *,
    cwd: str,
    has_ui: bool,
    messages: "Sequence[object]" = (),
    complete_fn: "CompletionFn | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
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
        messages=messages,
        complete_fn=complete_fn,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
    )


def _run_extension_handler(
    name: str,
    handler: CommandHandler,
    args: str,
    *,
    cwd: str,
    has_ui: bool,
    messages: "Sequence[object]",
    complete_fn: "CompletionFn | None",
    notify_sink: "Callable[[str, str], None] | None",
    ui_custom_driver: "CustomComponentDriver | None",
) -> ExtensionCommandDispatch:
    """Run a command/shortcut handler with a mode-aware context; bound errors."""

    ui = _CollectingUi(has_ui, notify_sink, ui_custom_driver)
    ctx = _CommandContext(cwd, ui, _ConversationView(messages), complete_fn)
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


class _ActivationError(Exception):
    """Raised internally to disable one extension with a reason code."""

    def __init__(self, reason: str, diagnostic: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


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
        reserved_tools: frozenset[str] = frozenset(),
        taken_tools: frozenset[str] = frozenset(),
        taken_providers: frozenset[str] = frozenset(),
        taken_shortcuts: frozenset[str] = frozenset(),
    ) -> None:
        self._extension_name = extension_name
        self._reserved = reserved
        self._taken = taken
        self._reserved_tools = reserved_tools
        self._taken_tools = taken_tools
        self._taken_providers = taken_providers
        self._taken_shortcuts = taken_shortcuts
        self._outbox = outbox
        self._staged: dict[str, RegisteredCommand] = {}
        self._staged_shortcuts: dict[str, RegisteredShortcut] = {}
        self._staged_tools: dict[str, RegisteredTool] = {}
        self._staged_providers: dict[str, RegisteredProvider] = {}
        self._staged_unregistered: list[str] = []
        self._hooks: dict[str, list[HookHandler]] = {}
        self._failure: tuple[str, str | None] | None = None
        # Messages are staged during activation and only committed to the
        # shared outbox once activation succeeds, so a disabled extension
        # never leaves a queued prompt behind. After activation commits,
        # runtime calls (from command handlers / hooks) append directly.
        self._staged_messages: list[QueuedUserMessage] = []
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

    def commit_activation(self) -> None:
        """Flush staged `send_user_message` calls after successful activation."""

        self._activated = True
        self._outbox.extend(self._staged_messages)
        self._staged_messages = []

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
        name = provider.name
        if not isinstance(name, str) or not name:
            raise _ActivationError(REASON_INVALID_PROVIDER)
        if not callable(provider.factory):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        if not isinstance(provider.models, tuple):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        # Providers MAY override a built-in of the same name (Pi behavior;
        # unregister restores it), so there is no reserved-name check; only
        # a duplicate registration across extensions is rejected.
        if name in self._staged_providers or name in self._taken_providers:
            raise _ActivationError(REASON_DUPLICATE_PROVIDER)
        self._staged_providers[name] = RegisteredProvider(
            provider=provider, extension=self._extension_name
        )

    def unregister_provider(self, name: str) -> None:
        if isinstance(name, str) and name and name not in self._staged_unregistered:
            self._staged_unregistered.append(name)

    def staged_providers(self) -> tuple[RegisteredProvider, ...]:
        return tuple(self._staged_providers.values())

    def staged_unregistered(self) -> tuple[str, ...]:
        return tuple(self._staged_unregistered)

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
            if normalized in self._taken_shortcuts or normalized in self._staged_shortcuts:
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

    reserved = frozenset(reserved_command_names)
    reserved_tools = frozenset(reserved_tool_names)
    taken: set[str] = set()
    taken_tools: set[str] = set()
    taken_providers: set[str] = set()
    taken_shortcuts: set[str] = set()
    outbox = message_outbox if message_outbox is not None else []
    results: list[ActivatedExtension] = []

    for descriptor in descriptors:
        if descriptor.status != "loadable":
            # Discovery already disabled this; never import it.
            results.append(_passthrough_disabled(descriptor))
            continue
        results.append(
            _activate_one(
                descriptor,
                reserved=reserved,
                taken=taken,
                reserved_tools=reserved_tools,
                taken_tools=taken_tools,
                taken_providers=taken_providers,
                taken_shortcuts=taken_shortcuts,
                outbox=outbox,
            )
        )
    return results


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


def drain_user_messages(
    outbox: list[QueuedUserMessage],
) -> list[QueuedUserMessage]:
    """Return and clear the queued `send_user_message` messages, in order."""

    drained = list(outbox)
    outbox.clear()
    return drained


def _activate_one(
    descriptor: ExtensionDescriptor,
    *,
    reserved: frozenset[str],
    taken: set[str],
    reserved_tools: frozenset[str],
    taken_tools: set[str],
    taken_providers: set[str],
    taken_shortcuts: set[str],
    outbox: list[QueuedUserMessage],
) -> ActivatedExtension:
    try:
        module = _import_entry_module(descriptor)
    except _ActivationError as err:
        return _disabled(descriptor, err.reason, err.diagnostic)

    # Resolving the entry function is inside the fail-closed boundary:
    # a module-level `__getattr__` could execute code and raise.
    try:
        activate = getattr(module, descriptor.entry_function, None)
        is_callable = callable(activate)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _disabled(
            descriptor, REASON_ACTIVATION_ERROR, _safe_diagnostic(err)
        )
    if activate is None or not is_callable:
        return _disabled(descriptor, REASON_NO_ACTIVATE, None)

    api = _ActivationApi(
        descriptor.name,
        reserved=reserved,
        taken=frozenset(taken),
        reserved_tools=reserved_tools,
        taken_tools=frozenset(taken_tools),
        taken_providers=frozenset(taken_providers),
        taken_shortcuts=frozenset(taken_shortcuts),
        outbox=outbox,
    )
    try:
        result = activate(api)
        if inspect.isawaitable(result):
            _run_awaitable(result)
    except _ActivationError as err:
        return _disabled(descriptor, err.reason, err.diagnostic)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _disabled(
            descriptor, REASON_ACTIVATION_ERROR, _safe_diagnostic(err)
        )

    # A failed registration disables the extension even if its own code
    # swallowed the error: no partial command set is ever committed.
    if api.failure is not None:
        failure_reason, failure_diagnostic = api.failure
        return _disabled(descriptor, failure_reason, failure_diagnostic)

    commands = api.staged_commands()
    tools = api.staged_tools()
    providers = api.staged_providers()
    shortcuts = api.staged_shortcuts()
    # Commit the command/tool/provider/shortcut names + staged
    # send_user_message prompts only now that activation fully succeeded.
    for command in commands:
        taken.add(command.name)
    for registered in tools:
        taken_tools.add(registered.tool.name)
    for registered_provider in providers:
        taken_providers.add(registered_provider.provider.name)
    for shortcut in shortcuts:
        taken_shortcuts.add(shortcut.key)
    api.commit_activation()
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="activated",
        reason=None,
        commands=commands,
        diagnostic=None,
        hooks=api.staged_hooks(),
        tools=tools,
        providers=providers,
        unregistered_providers=api.staged_unregistered(),
        shortcuts=shortcuts,
    )


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
    ctx = _CommandContext(cwd, _CollectingUi(has_ui, notify_sink))
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
    system_prompt: str = "",
) -> BeforeAgentStartResult:
    """Run `before_agent_start` hooks; aggregate their context injections.

    Each hook receives a `BeforeAgentStartEvent` (the current system
    prompt) and may return a `BeforeAgentStartResult` whose
    `append_system_prompt` is concatenated (in order). A hook that raises
    is fail-safe (ignored). `KeyboardInterrupt` / `SystemExit` propagate.
    """

    appended: list[str] = []
    if hooks:
        ctx = _CommandContext(cwd, _CollectingUi(has_ui, notify_sink))
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
        ctx = _CommandContext(cwd, _CollectingUi(has_ui, notify_sink))
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
    ctx = _CommandContext(cwd, _CollectingUi(has_ui, notify_sink))
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
    ctx = _CommandContext(cwd, _CollectingUi(has_ui, notify_sink))
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


def _import_entry_module(descriptor: ExtensionDescriptor) -> object:
    """Import the entry module from its on-disk path with normal semantics.

    Modules are loaded under a unique, namespaced name and registered in
    `sys.modules` (so `sys.modules[__name__]` works during import). For a
    *directory* extension the module is loaded as a submodule of a
    package rooted at the extension's own directory, so it can use
    relative imports (`from .helper import ...`); that package's search
    path is the extension dir only, never the shared store, so one
    extension can never import another. A *single-file* extension is a
    standalone top-level module (no package, no relative imports) because
    its directory is the shared store.

    Any error during import is converted to a fail-closed `import_error`,
    and partially-created `sys.modules` entries are removed.
    """

    entry_path_s = descriptor.entry_path
    if not entry_path_s:
        raise _ActivationError(REASON_IMPORT_ERROR, "no entry path")
    entry_path = Path(entry_path_s)
    digest = hashlib.sha256(entry_path_s.encode("utf-8")).hexdigest()[:12]
    base_name = f"pipy_ext_{_safe_module_segment(descriptor.name)}_{digest}"
    try:
        if descriptor.kind == "directory":
            module = _load_package_submodule(
                base_name, entry_path, descriptor.entry_module
            )
        else:
            module = _load_standalone_module(base_name, entry_path)
    except _ActivationError:
        _purge_modules(base_name)
        raise
    except (KeyboardInterrupt, SystemExit):
        _purge_modules(base_name)
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad import
        _purge_modules(base_name)
        raise _ActivationError(REASON_IMPORT_ERROR, _safe_diagnostic(err)) from None
    return module


def _load_standalone_module(module_name: str, entry_path: Path) -> object:
    spec = importlib.util.spec_from_file_location(module_name, str(entry_path))
    if spec is None or spec.loader is None:
        raise _ActivationError(REASON_IMPORT_ERROR, "no module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_package_submodule(
    package_name: str,
    entry_path: Path,
    entry_module: str,
) -> object:
    entry_dir = str(entry_path.parent)
    # A package rooted at the extension's OWN directory: relative imports
    # resolve here, isolated from the shared store and other extensions.
    # Only the package carries `__path__`; the entry is a regular module
    # whose parent is this package, so `from .helper import ...` resolves
    # to `<package>.helper` (not nested under the entry module).
    pkg_spec = importlib.machinery.ModuleSpec(package_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [entry_dir]
    package = importlib.util.module_from_spec(pkg_spec)
    sys.modules[package_name] = package

    module_name = f"{package_name}.{entry_module}"
    spec = importlib.util.spec_from_file_location(module_name, str(entry_path))
    if spec is None or spec.loader is None:
        raise _ActivationError(REASON_IMPORT_ERROR, "no module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _purge_modules(base_name: str) -> None:
    """Remove every `sys.modules` entry under this extension's namespace.

    On import failure, any submodules the extension already imported
    (for example `<base>.helper`) must also be removed, not just the
    package and entry module, so a failed activation leaves no stale
    extension modules behind. The base name is unique (it carries the
    entry-path hash), so the prefix match touches only this extension.
    """

    prefix = base_name + "."
    for key in [k for k in sys.modules if k == base_name or k.startswith(prefix)]:
        sys.modules.pop(key, None)


def _safe_module_segment(name: str) -> str:
    """Map an extension name to a safe Python module-name segment."""

    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)


def _run_awaitable(awaitable: object) -> None:
    """Drive an async `activate` coroutine to completion (return ignored)."""

    _drive_awaitable(awaitable)


def _drive_awaitable(awaitable: object) -> object:
    """Drive an awaitable to completion and return its result.

    Works whether or not the caller is already inside a running event
    loop: with no loop, `asyncio.run` is used directly; with a running
    loop (we cannot block it from the same thread), the awaitable is
    driven in a dedicated worker thread with its own fresh loop. Any
    exception (including an `_ActivationError` raised inside the
    coroutine) is re-raised in the calling thread, preserving its type.
    """

    import asyncio

    if not _event_loop_is_running():
        return asyncio.run(_as_coroutine(awaitable))

    import threading

    box: dict[str, object] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(_as_coroutine(awaitable))
        except BaseException as err:  # noqa: BLE001 - re-raised below
            box["err"] = err

    thread = threading.Thread(target=_runner, name="pipy-ext-activate")
    thread.start()
    thread.join()
    if "err" in box:
        raise box["err"]  # type: ignore[misc]
    return box.get("value")


def _event_loop_is_running() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


async def _as_coroutine(awaitable: object) -> object:
    return await awaitable  # type: ignore[misc]


def _passthrough_disabled(descriptor: ExtensionDescriptor) -> ActivatedExtension:
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="disabled",
        reason=descriptor.reason,
        commands=(),
        diagnostic=None,
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
        }
        for item in activated
    ]


def _is_valid_command_name(name: str) -> bool:
    """Lowercase ASCII identifier with optional `-` (Pi command rule)."""

    if not name:
        return False
    if name[0] not in _COMMAND_START_CHARS:
        return False
    return all(ch in _COMMAND_BODY_CHARS for ch in name)


def _safe_diagnostic(err: BaseException) -> str:
    """Return a safe diagnostic label from an exception.

    Only the exception *type name* is kept (for example `RuntimeError`,
    `ModuleNotFoundError`). The raw exception message is deliberately
    dropped: it can carry absolute paths, prompts, or secrets from the
    extension, which must never enter a diagnostic. The type name is
    enough to distinguish failure modes without leaking content.
    """

    kind = type(err).__name__
    if len(kind) > _DIAGNOSTIC_MAX_LENGTH:
        return kind[:_DIAGNOSTIC_MAX_LENGTH]
    return kind
