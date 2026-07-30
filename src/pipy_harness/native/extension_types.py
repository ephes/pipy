"""Fail-closed extension vocabulary and error primitives.

This is the stdlib-only leaf that both the extension runtime and the later
extension loader depend on. It owns the safe, enumerable activation reason
codes, the internal `_ActivationError` used to disable one extension with a
reason code, the `_safe_diagnostic` type-name projection, the Pi command-name
character rules (`_is_valid_command_name` / `is_valid_custom_entry_type`), the
reserved-shortcut layer (`RESERVED_SHORTCUT_KEYS`, `_SHORTCUT_MODIFIERS`,
`normalize_shortcut_key`), and the bounded-length constants they rely on.

It also owns the extension UI protocol contracts that command handlers and tool
renderers annotate against — `ExtensionUi`, `ExtensionUiDriver`,
`ToolRenderContext`, the `CustomComponent` Protocol plus its
`CustomComponentFactory`/`CustomComponentOptions`/`CustomComponentDriver`
aliases, and the `WidgetPlacement` literal — so `ProjectTrustContext.ui` and
`ExtensionTool.render_call`/`render_result` resolve to leaf-local types. The
headless UI implementation and render helpers live in `extension_ui`;
activation and session-render orchestration stay in `extension_runtime`, and
the concrete live driver stays in `tui`.

It has no project imports, so it can never participate in an import cycle with
the runtime or loader that import it. The stable `pipy_harness.extensions`
façade imports `normalize_shortcut_key` directly from this owner, while
`extension_runtime` preserves the characterized internal identity explicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - type-checker-only forward references
    # ``ChromePalette`` is the theme value object returned by ``ExtensionUi``'s
    # ``theme`` / ``get_theme`` and accepted by ``set_theme``. It is imported
    # here only to resolve those annotations: a type-checking-only edge with no
    # runtime import, so ``extension_types`` remains a runtime leaf with no
    # import cycle.
    from pipy_harness.native.session_tree import NativeSessionTree
    from pipy_harness.native.themes import ChromePalette

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
REASON_INVALID_FLAG: str = "invalid_flag"
REASON_DUPLICATE_FLAG: str = "duplicate_flag"
REASON_INVALID_MESSAGE_RENDERER: str = "invalid_message_renderer"
REASON_DUPLICATE_MESSAGE_RENDERER: str = "duplicate_message_renderer"
REASON_INVALID_ENTRY_RENDERER: str = "invalid_entry_renderer"
REASON_DUPLICATE_ENTRY_RENDERER: str = "duplicate_entry_renderer"

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
        # Pi reserves the default app.editor.external key. Dynamic reservation
        # for user-rebound app.editor.external is deferred with the rest of the
        # shortcut/keybindings integration; when a user binds the editor action
        # to a key that an extension also registers, the live editor branch wins,
        # while Ctrl-G remains reserved even if the user moves the editor action.
        "ctrl-g",
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


_COMMAND_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_COMMAND_BODY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_DIAGNOSTIC_MAX_LENGTH: int = 200
_CUSTOM_ENTRY_TYPE_MAX_CHARS: int = 200


def is_valid_custom_entry_type(name: str) -> bool:
    """Bounded lowercase ASCII custom-entry identifier."""

    return len(name) <= _CUSTOM_ENTRY_TYPE_MAX_CHARS and _is_valid_command_name(name)


def _is_valid_command_name(name: str) -> bool:
    """Lowercase ASCII identifier with optional `-` (Pi command rule)."""

    if not name:
        return False
    if name[0] not in _COMMAND_START_CHARS:
        return False
    return all(ch in _COMMAND_BODY_CHARS for ch in name)


class _ActivationError(Exception):
    """Raised internally to disable one extension with a reason code."""

    def __init__(self, reason: str, diagnostic: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


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


# The extension mode the current session runs in. Extensions read it (via
# ``ProjectTrustContext.mode`` and command/tool contexts) to gate interactive
# behavior; ``tui`` alone offers live UI.
ExtensionMode = Literal["tui", "print", "json", "rpc"]

# Model-runtime control callables an activated extension may invoke through a
# command/hook context to mutate the live session's model runtime: restrict the
# model-visible tool set, switch the active model, or set the reasoning level.
# Each returns ``True`` when the mutation was accepted.
ControlSetActiveToolsFn = Callable[[Sequence[str]], bool]
ControlSetModelFn = Callable[[str], bool]
ControlSetThinkingLevelFn = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ExtensionModelRuntimeControl:
    """Frozen bundle of the three model-runtime control callables.

    Groups the ``set_active_tools`` / ``set_model`` / ``set_thinking_level``
    capability functions the extension host threads into every command, hook,
    and tool context as a single port instead of an ad-hoc callable fan-out.
    Each field is ``None`` when that mutation is not offered in the current
    context (the context then raises ``ExtensionCapabilityError``).
    """

    set_active_tools_fn: ControlSetActiveToolsFn | None = None
    set_model_fn: ControlSetModelFn | None = None
    set_thinking_level_fn: ControlSetThinkingLevelFn | None = None


# Coding-session host collaborators an activated extension may invoke through a
# command/hook context: a bounded one-shot completion, custom session-entry
# append, session-name get/set, entry-label set, and a custom-message send. Each
# is ``None`` when that capability is not offered in the current context (the
# context then raises ``ExtensionCapabilityError``, except ``get_session_name``
# which returns ``None``).
CompletionFn = Callable[[str, str], str]
AppendEntryFn = Callable[[str, object | None], object]
SetSessionNameFn = Callable[[str | None], object]
GetSessionNameFn = Callable[[], str | None]
SetLabelFn = Callable[[str, str | None], object]
SendMessageFn = Callable[[str, str, bool, Mapping[str, object], object | None], object]


@dataclass(frozen=True, slots=True)
class ExtensionCodingSessionControl:
    """Frozen bundle of the coding-session host collaborators and snapshot.

    Groups the completion, custom-entry append, session-name get/set,
    entry-label set, and custom-message send capability functions the extension
    host threads into every command and shortcut context — plus the live
    ``session_tree`` the read-only session-manager view reads and the
    ``messages`` conversation snapshot the read-only conversation view reads — as
    a single port instead of an ad-hoc callable fan-out. Each callable field is
    ``None`` when that capability is not offered in the current context;
    ``session_tree`` is ``None`` and ``messages`` is empty when the context has
    no live coding session.
    """

    complete_fn: CompletionFn | None = None
    append_entry_fn: AppendEntryFn | None = None
    set_session_name_fn: SetSessionNameFn | None = None
    get_session_name_fn: GetSessionNameFn | None = None
    set_label_fn: SetLabelFn | None = None
    send_message_fn: SendMessageFn | None = None
    session_tree: "NativeSessionTree | None" = None
    messages: Sequence[object] = ()


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
CustomComponentOptions = Mapping[str, object]
CustomComponentDriver = Callable[
    [CustomComponentFactory, CustomComponentOptions | None], object
]


@dataclass(frozen=True, slots=True)
class ToolRenderContext:
    """Read-only context passed to an extension tool renderer.

    `state` is a single mutable mapping shared across render_call ->
    render_result for one tool execution. `details` is the extension's
    ToolResult.details (None at call phase). `theme` is a ToolRenderTheme."""

    tool_name: str
    args: Mapping[str, object]
    is_result: bool
    is_error: bool
    content: str | None
    details: Mapping[str, object] | None
    expanded: bool
    width: int
    theme: object  # ToolRenderTheme | None (None only in unit tests)
    state: MutableMapping[str, object]


WidgetPlacement = Literal["above_editor", "below_editor"]


@runtime_checkable
class ExtensionUiDriver(Protocol):
    """Live UI operations backed by the product TUI."""

    def select(self, title: str, options: Sequence[str]) -> str | None: ...

    def input(self, title: str, placeholder: str | None = None) -> str | None: ...

    def editor(self, title: str, prefill: str | None = None) -> str | None: ...

    def confirm(self, title: str, message: str) -> bool: ...

    def set_status(self, key: str, text: str | None) -> None: ...

    def set_working_message(self, message: str | None = None) -> None: ...

    def set_working_visible(self, visible: bool) -> None: ...

    def set_widget(self, key: str, content: object, placement: str) -> None: ...

    def set_header(self, factory: object | None) -> None: ...

    def set_footer(self, factory: object | None) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_working_indicator(
        self, frames: Sequence[str] | None, interval_ms: int | None
    ) -> None: ...

    def set_hidden_thinking_label(self, label: str | None = None) -> None: ...

    def get_editor_text(self) -> str: ...

    def set_editor_text(self, text: str) -> None: ...

    def paste_to_editor(self, text: str) -> None: ...

    def apply_theme(self, name: str) -> tuple[bool, str | None]: ...


@runtime_checkable
class ExtensionUi(Protocol):
    """Mode-aware UI handed to a command handler.

    Exposes transient notifications, simple dialogs, live status/working
    controls, and `custom` (take over the terminal with a custom interactive
    component). In non-interactive mode the methods behave deterministically:
    notifications are recorded, dialogs return cancel/default values, and
    `custom` is a no-op returning ``None`` (never blocks).
    """

    has_ui: bool

    def notify(self, message: str, kind: str = "info") -> None: ...

    def select(self, title: str, options: Sequence[str]) -> str | None: ...

    def input(self, title: str, placeholder: str | None = None) -> str | None: ...

    def editor(self, title: str, prefill: str | None = None) -> str | None: ...

    def confirm(self, title: str, message: str) -> bool: ...

    def set_status(self, key: str, text: str | None) -> None: ...

    def set_working_message(self, message: str | None = None) -> None: ...

    def set_working_visible(self, visible: bool) -> None: ...

    def custom(
        self,
        factory: CustomComponentFactory,
        options: CustomComponentOptions | None = None,
    ) -> object: ...

    def set_widget(
        self,
        key: str,
        content: object,
        *,
        placement: WidgetPlacement = "above_editor",
    ) -> None: ...

    def set_header(self, factory: object | None) -> None: ...

    def set_footer(self, factory: object | None) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_working_indicator(
        self,
        frames: Sequence[str] | None = None,
        *,
        interval_ms: int | None = None,
    ) -> None: ...

    def set_hidden_thinking_label(self, label: str | None = None) -> None: ...

    def setHiddenThinkingLabel(self, label: str | None = None) -> None: ...

    def get_editor_text(self) -> str: ...

    def getEditorText(self) -> str: ...

    def set_editor_text(self, text: str) -> None: ...

    def setEditorText(self, text: str) -> None: ...

    def paste_to_editor(self, text: str) -> None: ...

    def pasteToEditor(self, text: str) -> None: ...

    @property
    def theme(self) -> ChromePalette: ...

    def get_all_themes(self) -> list[dict[str, str | None]]: ...

    def get_theme(self, name: str) -> ChromePalette | None: ...

    def set_theme(self, theme: "str | ChromePalette") -> dict[str, object]: ...

    def add_autocomplete_provider(self, factory: object) -> None: ...

    def addAutocompleteProvider(self, factory: object) -> None: ...

    def on_terminal_input(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]: ...

    def onTerminalInput(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]: ...

    def set_editor_component(self, factory: object | None) -> None: ...

    def setEditorComponent(self, factory: object | None) -> None: ...

    def get_editor_component(self) -> object | None: ...

    def getEditorComponent(self) -> object | None: ...

    def get_tools_expanded(self) -> bool: ...

    def getToolsExpanded(self) -> bool: ...

    def set_tools_expanded(self, expanded: bool) -> None: ...

    def setToolsExpanded(self, expanded: bool) -> None: ...


ThemeColor = Literal["text", "accent", "success", "warning", "error", "dim"]


@runtime_checkable
class ToolRenderTheme(Protocol):
    """Bounded styling helper handed to extension tool renderers.

    Implementations map semantic names onto the active chrome palette and
    emit plain text when color is disabled (captured / NO_COLOR)."""

    def fg(self, color: ThemeColor, text: str) -> str: ...
    def bold(self, text: str) -> str: ...
    def dim(self, text: str) -> str: ...


@runtime_checkable
class ToolRenderComponent(Protocol):
    """A render-once tool-row component returned by render_call/render_result.

    `render(width)` returns the row's content lines (already theme-styled by
    the component). Aligned with `CustomComponent`; `invalidate`/`dispose`/
    `handle_input` are reserved for the later live-runtime slice and are not
    called here."""

    def render(self, width: int) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class MessageRenderContext:
    """Read-only context passed to a rich extension message renderer.

    A renderer that requires a second positional parameter receives this; a
    1-arg ``renderer(data)`` (including the capture-default idiom
    ``renderer(data, prefix=captured)``) keeps its slice-16 plain-text behavior.
    ``theme`` is a ToolRenderTheme (None only in unit tests / no-color captured
    runs)."""

    custom_type: str
    data: object | None
    expanded: bool
    width: int
    theme: object  # ToolRenderTheme | None


@dataclass(frozen=True, slots=True)
class EntryRenderContext:
    """Live product-TUI context passed to a durable entry renderer."""

    expanded: bool
    width: int
    theme: object  # ToolRenderTheme | None


# A message renderer's component shares the tool-renderer component contract
# (render(width) -> Sequence[str]); one contract across the rich-UI slices.
MessageRenderComponent = ToolRenderComponent


@dataclass(frozen=True, slots=True)
class RenderedCustomEntry:
    """Result of rendering one custom entry.

    ``styled`` True means ``lines`` carry theme SGR and must be committed
    SGR-preserving (the ``custom_message_custom`` TUI kind); False means the
    plain, sanitized back-compat path."""

    lines: tuple[str, ...]
    styled: bool


@runtime_checkable
class ChromeComponent(Protocol):
    """A width-reactive snapshot chrome component.

    Only ``render(width)`` is required (so ``lines_component`` output satisfies
    it structurally). ``invalidate()`` and ``dispose()`` are OPTIONAL and
    duck-typed — called if present: ``invalidate()`` before a re-render on
    resize, ``dispose()`` when the component is replaced/cleared/reloaded or on
    shutdown. Per-frame repaint and requestRender-driven animation are reserved
    for the later live slice and never invoked here."""

    def render(self, width: int) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class FooterData:
    """Read-only data handed to a footer factory.

    This mirrors Pi's ``ReadonlyFooterDataProvider`` method surface while keeping
    pipy's scalar fields snapshot-based. Live product-TUI snapshots may carry an
    optional branch-change registrar for Pi-shaped ``onBranchChange`` delivery;
    headless/offline snapshots keep a safe no-op disposer. ``extension_statuses``
    is copied into a read-only proxy so a caller-passed ``dict`` cannot be
    mutated through the snapshot.
    """

    git_branch: str | None
    extension_statuses: Mapping[str, str]
    available_provider_count: int = 0
    branch_change_registrar: (
        Callable[[Callable[[], object]], Callable[[], None]] | None
    ) = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extension_statuses",
            MappingProxyType(dict(self.extension_statuses)),
        )
        object.__setattr__(
            self,
            "available_provider_count",
            max(0, int(self.available_provider_count)),
        )

    def get_git_branch(self) -> str | None:
        return self.git_branch

    def getGitBranch(self) -> str | None:  # noqa: N802 - Pi-shaped API
        return self.get_git_branch()

    def get_extension_statuses(self) -> Mapping[str, str]:
        return self.extension_statuses

    def getExtensionStatuses(self) -> Mapping[str, str]:  # noqa: N802 - Pi-shaped API
        return self.get_extension_statuses()

    def get_available_provider_count(self) -> int:
        return self.available_provider_count

    def getAvailableProviderCount(self) -> int:  # noqa: N802 - Pi-shaped API
        return self.get_available_provider_count()

    def on_branch_change(self, callback: Callable[[], object]) -> Callable[[], None]:
        """Register for branch changes and return a safe disposer.

        Live TUI snapshots delegate to the UI registrar. Headless snapshots keep
        translated extensions source-shaped with a no-op disposer.
        """

        if self.branch_change_registrar is not None:
            return self.branch_change_registrar(callback)
        return lambda: None

    def onBranchChange(  # noqa: N802 - Pi-shaped API
        self, callback: Callable[[], object]
    ) -> Callable[[], None]:
        return self.on_branch_change(callback)


@dataclass(frozen=True, slots=True)
class ProjectTrustEvent:
    """Startup event offered only to pre-trust extension handlers."""

    cwd: str
    type: Literal["project_trust"] = "project_trust"


@dataclass(frozen=True, slots=True)
class ProjectTrustContext:
    """Bounded startup-only context for a project-trust decision."""

    cwd: str
    mode: ExtensionMode
    has_ui: bool
    ui: "ExtensionUi"

    @property
    def hasUI(self) -> bool:  # noqa: N802 - Pi-shaped alias
        return self.has_ui


@dataclass(frozen=True, slots=True)
class ProjectTrustHandlerError:
    extension: str
    error: str


@dataclass(frozen=True, slots=True)
class ProjectTrustDispatchResult:
    trusted: Literal["yes", "no"] | None = None
    remember: bool = False
    errors: tuple[ProjectTrustHandlerError, ...] = ()


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
class QueuedCustomMessage:
    """A custom message an extension enqueued via `send_message`."""

    custom_type: str
    content: str
    display: bool
    details: object | None
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
    `ToolResult`. `render_call` and `render_result` are optional callables
    that receive a `ToolRenderContext` and return a `ToolRenderComponent`
    (or object) controlling how the tool's call and result rows render.
    """

    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: Callable[..., object]
    render_call: Callable[["ToolRenderContext"], object] | None = None
    render_result: Callable[["ToolRenderContext"], object] | None = None


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """An extension tool accepted during activation, with its owner."""

    tool: ExtensionTool
    extension: str


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


@dataclass(frozen=True, slots=True)
class UserBashEvent:
    """A local ``!``/``!!`` shell shortcut before execution.

    `command` is the live shell command string, and `exclude_from_context`
    mirrors Pi's ``!!`` form. Trusted local hooks may inspect the command
    and either observe, transform, block, or provide a complete synthetic
    result; raw command text is never written to the default archive by this
    dispatcher.
    """

    command: str
    exclude_from_context: bool
    cwd: str


@dataclass(frozen=True, slots=True)
class UserBashDecision:
    """Return value for `user_bash` hooks.

    `allow=False` blocks execution with `reason`. `command` replaces the
    shell command for later hooks / execution. `exclude_from_context`
    overrides whether the final result is recorded into provider-visible
    context. `result` supplies a synthetic output and skips shell execution.
    """

    allow: bool = True
    reason: str | None = None
    command: str | None = None
    exclude_from_context: bool | None = None
    result: str | None = None
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class UserBashDispatch:
    """Final decision after all `user_bash` hooks ran."""

    allowed: bool
    command: str
    exclude_from_context: bool
    reason: str | None = None
    result: str | None = None
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class BeforeProviderRequestEvent:
    """The live in-memory provider request before `ProviderPort.complete`.

    The event carries bounded request fields and safe metadata. Extensions
    that need the full message objects can inspect `messages` live, but the
    default archive still stores no provider payloads.
    """

    system_prompt: str
    user_prompt: str
    provider_name: str
    model_id: str
    available_tools: tuple[str, ...]
    messages: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class BeforeProviderHeadersEvent:
    """Mutable request headers after adapter assembly and before transport."""

    headers: MutableMapping[str, str | None]
    type: Literal["before_provider_headers"] = "before_provider_headers"


@dataclass(frozen=True, slots=True)
class ProviderRequestTransform:
    """Return value for `before_provider_request` hooks.

    `system_prompt` and `user_prompt` replace those request fields for later
    hooks and the provider call. `available_tools` narrows the active tool set
    for this request.
    """

    system_prompt: str | None = None
    user_prompt: str | None = None
    available_tools: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SessionBeforeEvent:
    """Session operation gate event.

    `operation` is one of `switch`, `fork`, `compact`, or `tree`.
    `target` is a safe label when the operation has one; it may be None.
    """

    operation: str
    target: str | None = None
    trigger: str | None = None


@dataclass(frozen=True, slots=True)
class SessionDecision:
    """Return value for session-before hooks."""

    allow: bool = True
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionFlag:
    """A Pi-shaped CLI flag an extension registers at activation time."""

    name: str
    flag_type: Literal["boolean", "string"]
    description: str | None = None
    default: bool | str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredFlag:
    """One extension CLI flag accepted during activation, with guarded access."""

    flag: ExtensionFlag
    extension: str
    _get_value: Callable[[str], object | None] = field(repr=False, compare=False)
    _set_value: Callable[[str, object], None] = field(repr=False, compare=False)

    def get_value(self) -> object | None:
        return self._get_value(self.flag.name)

    def _apply_value(self, value: object) -> None:
        self._set_value(self.flag.name, value)


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """Context passed to an extension provider `factory`.

    Carries only safe selection metadata: the provider name, its default
    model, and the currently selected model when the factory is built for a
    concrete catalog selection. A provider extension must read its own
    environment / a future auth capability — it never receives the shared
    auth store.
    """

    provider_name: str
    default_model: str | None
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionOAuthConfig:
    """OAuth metadata for an extension-registered provider.

    Mirrors Pi's ``ProviderConfig.oauth`` shape through Python snake_case. The
    provider name is the future OAuth id source, matching Pi's derived
    ``{...oauth, id: providerName}``; extension authors do not supply an id.
    Callbacks are preserved for later auth/login integration and are not invoked
    during activation or provider-port construction.
    """

    name: str
    login: Callable[..., object]
    refresh_token: Callable[..., object]
    get_api_key: Callable[..., object]
    modify_models: Callable[..., object] | None = None


@dataclass(frozen=True, slots=True)
class ExtensionProvider:
    """A model provider an extension registers via `api.register_provider`.

    `name` is the provider name (selectable through the catalog / `/model`);
    `default_model` and `models` describe the provider's model ids;
    `factory(ProviderContext)` builds a `ProviderPort`. A provider may
    override a built-in of the same name; `unregister_provider(name)`
    removes it and restores the built-in. ``oauth`` preserves Pi-shaped OAuth
    metadata for a later `/login`/auth-storage integration slice.
    """

    name: str
    default_model: str | None
    models: tuple[str, ...]
    factory: Callable[..., object]
    oauth: ExtensionOAuthConfig | None = None


@dataclass(frozen=True, slots=True)
class RegisteredProvider:
    """An extension provider accepted during activation, with its owner."""

    provider: ExtensionProvider
    extension: str
