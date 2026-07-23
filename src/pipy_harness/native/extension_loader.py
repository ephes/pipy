"""Sandboxed on-disk import and awaitable-driving mechanics for extensions.

This module owns the low-level sandbox boundary that the extension runtime
uses to bring an already-inventoried *loadable* extension into the process: it
imports the entry module from its on-disk path under a unique, namespaced
`sys.modules` name (fail-closed to `import_error` on any failure, with
partially-created module entries purged), and it drives an extension's async
`activate`/handler coroutines to completion whether or not the caller is
already inside a running event loop.

Directory extensions are loaded as a submodule of a package rooted at the
extension's own directory, so relative imports (`from .helper import ...`)
resolve there and never reach the shared store or another extension.
Single-file extensions are standalone top-level modules with no package and no
relative imports.

It depends only on the stdlib and on the fail-closed vocabulary leaf
`native.extension_types` (`_ActivationError`, `REASON_IMPORT_ERROR`,
`_safe_diagnostic`), so it never imports back into `extension_runtime` and can
never participate in an import cycle with the runtime that consumes it.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.extension_types import (
    REASON_IMPORT_ERROR,
    _ActivationError,
    _safe_diagnostic,
)
from pipy_harness.native.extensions import ExtensionDescriptor


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
    pkg_spec = importlib.machinery.ModuleSpec(
        package_name, loader=None, is_package=True
    )
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


def _run_awaitable(awaitable: Awaitable[object]) -> None:
    """Drive an async `activate` coroutine to completion (return ignored)."""

    _drive_awaitable(awaitable)


def _drive_awaitable(awaitable: Awaitable[object]) -> object:
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

    outcome = _AwaitableOutcome()

    def _runner() -> None:
        try:
            outcome.value = asyncio.run(_as_coroutine(awaitable))
        except BaseException as err:  # noqa: BLE001 - re-raised below
            outcome.error = err

    thread = threading.Thread(target=_runner, name="pipy-ext-activate")
    thread.start()
    thread.join()
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


def _event_loop_is_running() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@dataclass(slots=True)
class _AwaitableOutcome:
    """Result transport from the private event-loop worker thread."""

    value: object = None
    error: BaseException | None = None


async def _as_coroutine(awaitable: Awaitable[object]) -> object:
    return await awaitable
