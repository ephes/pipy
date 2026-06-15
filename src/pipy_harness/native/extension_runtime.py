"""Extension activation sandbox boundary (slice 2).

This module imports an explicit, already-inventoried *loadable*
extension module (from `pipy_harness.native.extensions`), calls its
`activate(api)` entry point, and supports command registration only.

It is the first slice that actually executes extension code, so it is
deliberately fail-closed per extension: an import error, a missing or
non-callable `activate`, an exception during activation, or an
invalid / duplicate / reserved command name disables that one extension
with a safe reason code — it never crashes the session and never lets a
bad extension take down the others. Disabled discovery descriptors are
never imported.

This slice does NOT wire commands into the live REPL dispatch (that is
slice 3); it produces the activation result + registered command table
and proves the failure modes. Command output, handlers, and source code
never enter the default archive; project the result through
`safe_activation_metadata`.

Public API:

- `PipyExtensionAPI` — the activation-time API protocol (also re-exported
  from `pipy_harness.extensions`).
- `RegisteredCommand` / `ActivatedExtension` value objects.
- `activate_extensions(descriptors, *, reserved_command_names=())`.
- `safe_activation_metadata(activated)`.
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

CommandHandler = Callable[..., object]

# Activation reason codes (safe, enumerable labels).
REASON_IMPORT_ERROR: str = "import_error"
REASON_NO_ACTIVATE: str = "no_activate"
REASON_ACTIVATION_ERROR: str = "activation_error"
REASON_INVALID_COMMAND_NAME: str = "invalid_command_name"
REASON_RESERVED_COMMAND: str = "reserved_command"
REASON_DUPLICATE_COMMAND: str = "duplicate_command"
REASON_INVALID_HOOK: str = "invalid_hook"

# Event names (the dispatched subset grows per slice).
EVENT_TOOL_CALL: str = "tool_call"

HookHandler = Callable[..., object]


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

ActivationStatus = Literal["activated", "disabled"]


class PipyExtensionAPI(Protocol):
    """The activation-time API handed to an extension's `activate`.

    Slice 2 supports command registration only. Later slices add tool /
    hook / provider / UI registration to this surface.
    """

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None: ...

    def on(
        self,
        event: str,
        handler: HookHandler | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    """One command an extension registered during activation."""

    name: str
    description: str
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


@runtime_checkable
class ExtensionUi(Protocol):
    """Minimal mode-aware UI handed to a command handler (slice 3).

    Slice 3 exposes only `notify`; the richer UI surface (dialogs,
    status, widgets) lands in a later slice. In non-interactive mode the
    methods still behave deterministically (record/queue, never block).
    """

    has_ui: bool

    def notify(self, message: str, kind: str = "info") -> None: ...


@runtime_checkable
class CommandContext(Protocol):
    """Context passed to an extension command handler.

    Slice 3 keeps this small: the workspace root, whether interactive UI
    is available, and the `ui` capability. It grows (session view, model
    info, cancellation, system-prompt access) in later slices.
    """

    cwd: str
    has_ui: bool
    ui: ExtensionUi


class _CollectingUi:
    """A mode-aware `ExtensionUi` that records notifications.

    The dispatcher returns the collected messages so the caller (the
    REPL) emits them as live UI output; nothing is archived. This keeps
    dispatch pure and testable and gives deterministic non-interactive
    behavior (notifications are recorded, never blocking).
    """

    def __init__(self, has_ui: bool) -> None:
        self.has_ui = has_ui
        self.messages: list[tuple[str, str]] = []

    def notify(self, message: str, kind: str = "info") -> None:
        safe_kind = kind if kind in ("info", "warning", "error") else "info"
        self.messages.append((safe_kind, str(message)))


class _CommandContext:
    """Concrete `CommandContext` for one command invocation."""

    def __init__(self, cwd: str, ui: _CollectingUi) -> None:
        self.cwd = cwd
        self.has_ui = ui.has_ui
        self.ui = ui


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


def dispatch_extension_command(
    command_text: str,
    command_map: dict[str, RegisteredCommand],
    *,
    cwd: str,
    has_ui: bool,
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

    ui = _CollectingUi(has_ui)
    ctx = _CommandContext(cwd, ui)
    try:
        command.handler(ctx, args)
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
    ) -> None:
        self._extension_name = extension_name
        self._reserved = reserved
        self._taken = taken
        self._staged: dict[str, RegisteredCommand] = {}
        self._hooks: dict[str, list[HookHandler]] = {}
        self._failure: tuple[str, str | None] | None = None

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
) -> list[ActivatedExtension]:
    """Activate the loadable descriptors, in order.

    Disabled discovery descriptors are passed through unchanged (never
    imported). Each loadable descriptor is imported and activated in
    isolation; any failure disables only that extension. Command names
    are deduplicated across all extensions in this pass (first
    registration wins; a later collision disables the later extension).
    """

    reserved = frozenset(reserved_command_names)
    taken: set[str] = set()
    results: list[ActivatedExtension] = []

    for descriptor in descriptors:
        if descriptor.status != "loadable":
            # Discovery already disabled this; never import it.
            results.append(_passthrough_disabled(descriptor))
            continue
        results.append(_activate_one(descriptor, reserved=reserved, taken=taken))
    return results


def _activate_one(
    descriptor: ExtensionDescriptor,
    *,
    reserved: frozenset[str],
    taken: set[str],
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
        descriptor.name, reserved=reserved, taken=frozenset(taken)
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
    # Commit the names only now that activation fully succeeded.
    for command in commands:
        taken.add(command.name)
    return ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="activated",
        reason=None,
        commands=commands,
        diagnostic=None,
        hooks=api.staged_hooks(),
    )


def extension_tool_call_hooks(
    activated: Sequence[ActivatedExtension],
) -> tuple[HookHandler, ...]:
    """Collect `tool_call` hooks from activated extensions, in order."""

    hooks: list[HookHandler] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        hooks.extend(extension.hooks.get(EVENT_TOOL_CALL, ()))
    return tuple(hooks)


def dispatch_tool_call_hooks(
    hooks: Sequence[HookHandler],
    *,
    tool_name: str,
    tool_input: Mapping[str, object],
    cwd: str,
    has_ui: bool,
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
    ctx = _CommandContext(cwd, _CollectingUi(has_ui))
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
