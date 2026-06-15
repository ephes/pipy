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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pipy_harness.native.extensions import ExtensionDescriptor

CommandHandler = Callable[..., object]

# Activation reason codes (safe, enumerable labels).
REASON_IMPORT_ERROR: str = "import_error"
REASON_NO_ACTIVATE: str = "no_activate"
REASON_ACTIVATION_ERROR: str = "activation_error"
REASON_INVALID_COMMAND_NAME: str = "invalid_command_name"
REASON_RESERVED_COMMAND: str = "reserved_command"
REASON_DUPLICATE_COMMAND: str = "duplicate_command"

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

    @property
    def failure(self) -> tuple[str, str | None] | None:
        return self._failure

    def staged_commands(self) -> tuple[RegisteredCommand, ...]:
        return tuple(self._staged.values())


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
    )


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
    """Drive an async `activate` coroutine to completion.

    Works whether or not the caller is already inside a running event
    loop: with no loop, `asyncio.run` is used directly; with a running
    loop (we cannot block it from the same thread), the coroutine is
    driven in a dedicated worker thread with its own fresh loop. Any
    exception (including an `_ActivationError` raised inside the
    coroutine) is re-raised in the calling thread, preserving its type so
    the caller maps it to the right reason code.
    """

    import asyncio

    if not _event_loop_is_running():
        asyncio.run(_as_coroutine(awaitable))
        return

    import threading

    box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            asyncio.run(_as_coroutine(awaitable))
        except BaseException as err:  # noqa: BLE001 - re-raised below
            box["err"] = err

    thread = threading.Thread(target=_runner, name="pipy-ext-activate")
    thread.start()
    thread.join()
    if "err" in box:
        raise box["err"]


def _event_loop_is_running() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


async def _as_coroutine(awaitable: object) -> None:
    await awaitable  # type: ignore[misc]


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
