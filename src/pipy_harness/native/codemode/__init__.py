"""Python codemode sandbox: CPython-on-WASI under wasmtime (CM1).

This package is standalone: it does not import the agent loop, tools or UI.
The host side never imports ``wasmtime``; only :mod:`.worker`, run as a
separate process, does. See ``docs/specs/2026-10-06-python-codemode-spike.md``.
"""

from pipy_harness.native.codemode.runtime import (
    RUNTIME_PIN,
    CodemodePaths,
    CodemodeRuntimeError,
    InstallResult,
    RuntimePin,
    default_paths,
    install_runtime,
    verify_runtime,
)
from pipy_harness.native.codemode.selftest import Availability, availability

__all__ = [
    "RUNTIME_PIN",
    "Availability",
    "CodemodePaths",
    "CodemodeRuntimeError",
    "InstallResult",
    "RuntimePin",
    "availability",
    "default_paths",
    "install_runtime",
    "verify_runtime",
]
